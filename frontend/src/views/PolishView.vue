<script setup lang="ts">
import { computed, onUnmounted, ref } from "vue";
import { cancelPolishTask, createPolishTask, getPolishTask, type PolishMode } from "@/api/polish";
import { useVstoBridge } from "@/composables/useVstoBridge";
import { useBillingStore } from "@/stores/billing";
import logoUrl from "@/assets/images/ui/common-logo-black.png";
import iconWallet from "@/assets/images/ui/common-icon-wallet.png";
import iconPoints from "@/assets/images/ui/common-icon-points.png";

const billingStore = useBillingStore();
const bridge = useVstoBridge();

const MODES: { value: PolishMode; label: string; hint: string }[] = [
  { value: "polish", label: "润色", hint: "优化表达与术语，不改变事实" },
  { value: "expand", label: "扩写", hint: "补充论述与细节，保持原意" },
  { value: "abbreviate", label: "缩写", hint: "精炼压缩，保留关键信息" },
];

const inputText = ref("");
const originalSelection = ref("");
const selectionFromBridge = ref(false);
const mode = ref<PolishMode>("polish");
const requirements = ref("");
const targetLength = ref<number | null>(null);
const taskId = ref("");
const submitting = ref(false);
const cancelling = ref(false);
const finished = ref(false);
const statusMessage = ref("");
const resultText = ref("");
const errorMessage = ref("");
const readingSelection = ref(false);
const applying = ref<"idle" | "replace" | "insert">("idle");
const appliedMessage = ref("");
const staleSnapshot = ref(false);
let pollTimer: ReturnType<typeof setInterval> | null = null;

const running = computed(() => Boolean(taskId.value) && !finished.value);
const bridgeStateText = computed(() =>
  bridge.contextReady.value
    ? "已连接 Word 文档"
    : bridge.available.value
      ? "正在连接 Word 文档"
      : "未检测到 Word 插件（可粘贴文本使用）",
);
const canSubmit = computed(() =>
  Boolean(inputText.value.trim()) && !submitting.value && !running.value,
);
const charCount = computed(() => inputText.value.length);

function friendlyError(error: unknown, fallback: string): string {
  if (error && typeof error === "object" && "response" in error) {
    const response = (error as {
      response?: { status?: number; data?: { detail?: unknown } };
    }).response;
    const detail = response?.data?.detail;
    if (response?.status === 402) {
      return typeof detail === "object" && detail && "message" in detail
        ? String((detail as { message?: string }).message)
        : "余额不足，请先充值后再使用 AI 润色";
    }
    if (response?.status === 409) {
      if (typeof detail === "object" && detail && "code" in detail
        && (detail as { code?: string }).code === "ACTIVE_BILLING_TASK_EXISTS") {
        return "您有正在进行的 AI 任务（检查/生成/润色），请等待完成或取消后再试";
      }
    }
    if (detail) {
      if (typeof detail === "string") return detail;
      if (typeof detail === "object" && "message" in detail) {
        return String((detail as { message?: string }).message || fallback);
      }
    }
  }
  return error instanceof Error ? error.message : fallback;
}

function formatMetric(value: number) {
  return new Intl.NumberFormat("zh-CN").format(Math.round(value || 0));
}

async function fetchSelection() {
  readingSelection.value = true;
  errorMessage.value = "";
  try {
    const result = await bridge.requestSelection();
    if (result.success !== true) {
      errorMessage.value = String(result.error || "读取选区失败，请重新选择后重试");
      return;
    }
    const content = String(result.content || "");
    if (!content.trim()) {
      errorMessage.value = "请先在 Word 中选中要处理的文本，再点击「读取选区」";
      return;
    }
    originalSelection.value = content;
    inputText.value = content;
    selectionFromBridge.value = true;
  } catch (error) {
    errorMessage.value = friendlyError(error, "读取 Word 选区失败");
  } finally {
    readingSelection.value = false;
  }
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

async function submitTask() {
  if (!canSubmit.value) return;
  submitting.value = true;
  finished.value = false;
  errorMessage.value = "";
  resultText.value = "";
  appliedMessage.value = "";
  staleSnapshot.value = false;
  statusMessage.value = "正在创建润色任务…";
  try {
    const task = await createPolishTask({
      mode: mode.value,
      text: inputText.value.slice(0, 20_000),
      requirements: requirements.value.trim() || null,
      target_length: mode.value === "polish" ? null : targetLength.value || null,
    });
    taskId.value = task.id;
    statusMessage.value = "任务已提交，等待处理…";
    startPolling(task.id);
  } catch (error) {
    errorMessage.value = friendlyError(error, "提交润色任务失败");
    statusMessage.value = "";
  } finally {
    submitting.value = false;
  }
}

function startPolling(id: string) {
  stopPolling();
  pollTimer = setInterval(async () => {
    try {
      const task = await getPolishTask(id);
      if (task.status === "running") statusMessage.value = "智能体正在处理文本…";
      if (task.status === "completed") {
        stopPolling();
        finished.value = true;
        resultText.value = task.result_text || "";
        statusMessage.value = "处理完成，请预览结果";
        void billingStore.fetchWallet().catch(() => undefined);
      } else if (task.status === "failed" || task.status === "cancelled") {
        stopPolling();
        finished.value = true;
        errorMessage.value = task.error_message || (task.status === "cancelled" ? "任务已取消" : "任务失败，请重试");
        statusMessage.value = "";
      }
    } catch (error) {
      stopPolling();
      errorMessage.value = friendlyError(error, "查询任务状态失败");
    }
  }, 1_500);
}

async function cancelTask() {
  if (!taskId.value || finished.value) return;
  cancelling.value = true;
  try {
    await cancelPolishTask(taskId.value);
    stopPolling();
    finished.value = true;
    statusMessage.value = "任务已取消";
  } catch (error) {
    errorMessage.value = friendlyError(error, "取消任务失败");
  } finally {
    cancelling.value = false;
  }
}

function handleApplyResult(result: {
  success: boolean;
  code?: string | null;
  error?: string | null;
}, successText: string, fallback: string) {
  if (result.success === true) {
    appliedMessage.value = successText;
    staleSnapshot.value = false;
    errorMessage.value = "";
    return;
  }
  const code = String(result.code || "");
  if (code === "snapshot_stale") {
    staleSnapshot.value = true;
    errorMessage.value = "Word 文档已修改，请点击「重新定位插入点」后重试";
    return;
  }
  if (code === "selection_changed") {
    errorMessage.value = "Word 当前选区与处理前的文本不一致，请重新选择后重试";
    return;
  }
  errorMessage.value = String(result.error || fallback);
}

async function applyReplace() {
  if (!resultText.value || !originalSelection.value) return;
  applying.value = "replace";
  errorMessage.value = "";
  try {
    const result = await bridge.replaceSelection(originalSelection.value, resultText.value, "AI 扩写润色");
    handleApplyResult(result, "已替换 Word 选区，可按 Ctrl+Z 一次撤销本次修改", "替换选区失败");
  } catch (error) {
    errorMessage.value = friendlyError(error, "替换选区失败，请从 Word 插件中打开本页");
  } finally {
    applying.value = "idle";
  }
}

async function applyInsert(atCurrentCursor = false) {
  if (!resultText.value) return;
  applying.value = "insert";
  errorMessage.value = "";
  try {
    const result = await bridge.insertMarkdown(resultText.value, {
      label: "AI 扩写润色",
      snapshotId: atCurrentCursor ? null : bridge.documentContext.value?.snapshot_id || null,
    });
    handleApplyResult(result, "已插入到 Word 光标处，可按 Ctrl+Z 一次撤销本次修改", "插入 Word 失败");
  } catch (error) {
    errorMessage.value = friendlyError(error, "插入 Word 失败，请从 Word 插件中打开本页");
  } finally {
    applying.value = "idle";
  }
}

async function copyResult() {
  if (!resultText.value) return;
  try {
    await navigator.clipboard.writeText(resultText.value);
    appliedMessage.value = "结果已复制到剪贴板";
  } catch {
    errorMessage.value = "复制失败，请手动选择结果文本复制";
  }
}

onUnmounted(stopPolling);
</script>

<template>
  <main class="polish-view">
    <div class="brand-line" />
    <header class="panel-header">
      <img :src="logoUrl" alt="标书审查智能体" class="panel-logo">
      <div class="account-strip" aria-label="账户余额">
        <span class="metric-pill" :style="{ backgroundImage: `url(${iconWallet})` }">
          <span>{{ billingStore.loading && !billingStore.wallet ? "--" : formatMetric(billingStore.balanceWen) }}点</span>
        </span>
        <span class="metric-pill" :style="{ backgroundImage: `url(${iconPoints})` }">
          <span>{{ billingStore.loading && !billingStore.wallet ? "--" : formatMetric(billingStore.points) }}积分</span>
        </span>
      </div>
    </header>

    <section class="card hero-card">
      <div class="section-kicker">WORD · 选区处理</div>
      <h1>AI 扩写润色</h1>
      <p>读取当前 Word 选中文本，进行专业扩写、润色或缩写；结果可一键替换原文或插入光标处，均可按 Ctrl+Z 一次撤销。</p>
      <div class="document-state" :class="{ ok: bridge.contextReady.value }">
        <span class="dot" />
        <span>{{ bridgeStateText }}</span>
        <span v-if="bridge.documentContext.value?.document_name" class="document-name" :title="bridge.documentContext.value.document_name">
          {{ bridge.documentContext.value.document_name }}
        </span>
      </div>
      <div v-if="errorMessage" class="error" role="alert">{{ errorMessage }}</div>
    </section>

    <section class="card">
      <div class="card-heading">
        <div><span class="heading-index">01</span><h2>处理文本</h2></div>
        <button class="ghost-btn" :disabled="readingSelection || running" @click="fetchSelection">
          {{ readingSelection ? "读取中…" : "读取 Word 选区" }}
        </button>
      </div>
      <textarea
        v-model="inputText"
        maxlength="20000"
        rows="8"
        aria-label="待处理文本"
        placeholder="点击「读取 Word 选区」自动填入；也可直接粘贴要处理的投标文件片段"
      />
      <div class="meta-row">
        <span>{{ charCount }}/20000 字</span>
        <span v-if="selectionFromBridge">来自 Word 选区，可直接「替换选区」</span>
        <span v-else>手动粘贴的文本建议使用「插入到光标」</span>
      </div>
    </section>

    <section class="card">
      <div class="card-heading">
        <div><span class="heading-index">02</span><h2>处理方式</h2></div>
      </div>
      <div class="mode-pills">
        <button
          v-for="item in MODES"
          :key="item.value"
          type="button"
          class="mode-pill"
          :class="{ active: mode === item.value }"
          :disabled="running"
          @click="mode = item.value"
        >
          <strong>{{ item.label }}</strong>
          <small>{{ item.hint }}</small>
        </button>
      </div>
      <input
        v-model="requirements"
        class="line-input"
        maxlength="2000"
        aria-label="补充要求"
        placeholder="补充要求（可选）：如“更技术化”“突出实施保障”“面向评标专家”"
      >
      <div v-if="mode !== 'polish'" class="target-row">
        <label for="polish-target">目标篇幅（字）</label>
        <input
          id="polish-target"
          v-model.number="targetLength"
          class="line-input short"
          type="number"
          min="50"
          max="20000"
          placeholder="如 800"
        >
      </div>
      <div class="actions">
        <button class="primary" :disabled="!canSubmit" @click="submitTask">
          {{ submitting ? "正在提交…" : "开始处理" }}
        </button>
        <button v-if="running" class="secondary" :disabled="cancelling" @click="cancelTask">
          {{ cancelling ? "取消中…" : "取消任务" }}
        </button>
      </div>
    </section>

    <section v-if="running || statusMessage" class="card">
      <div class="card-heading compact">
        <div><span class="heading-index">03</span><h2>处理进度</h2></div>
        <span class="timeline-status">{{ running ? "执行中" : "已结束" }}</span>
      </div>
      <div class="progress-message">
        <span v-if="running" class="pulse-dot" />
        {{ statusMessage || "等待处理…" }}
      </div>
    </section>

    <section v-if="resultText" class="card">
      <div class="card-heading compact">
        <div><span class="heading-index">04</span><h2>结果预览</h2></div>
        <span class="timeline-status">Markdown</span>
      </div>
      <pre class="result-block">{{ resultText }}</pre>
      <details class="origin-block">
        <summary>查看原文</summary>
        <pre>{{ originalSelection || inputText }}</pre>
      </details>
      <div class="actions">
        <button
          class="primary"
          :disabled="!originalSelection || applying !== 'idle'"
          :title="originalSelection ? '' : '仅“读取 Word 选区”获得的文本支持替换'"
          @click="applyReplace"
        >
          {{ applying === "replace" ? "替换中…" : "替换选区" }}
        </button>
        <button class="secondary" :disabled="applying !== 'idle'" @click="applyInsert(staleSnapshot)">
          {{ applying === "insert" ? "插入中…" : staleSnapshot ? "重新定位插入点并写入" : "插入到光标" }}
        </button>
        <button class="secondary" @click="copyResult">复制结果</button>
      </div>
      <div v-if="appliedMessage" class="applied-tip">{{ appliedMessage }}</div>
    </section>
  </main>
</template>

<style scoped>
.polish-view{--brand:#d7041a;--brand-deep:#b80015;box-sizing:border-box;width:100%;max-width:760px;min-height:100vh;margin:0 auto;padding:0 12px 36px;background:#f5f5f5;color:#333;overflow-x:hidden;font-family:"Microsoft YaHei","PingFang SC",Arial,sans-serif}.brand-line{height:3px;margin:0 -12px;background:linear-gradient(90deg,var(--brand),var(--brand-deep))}.panel-header{display:flex;align-items:center;justify-content:space-between;gap:12px;min-height:60px;padding:10px 4px}.panel-logo{width:108px;height:auto;object-fit:contain}.account-strip{display:flex;align-items:center;gap:7px;min-width:0}.metric-pill{display:inline-flex;align-items:center;justify-content:flex-end;box-sizing:border-box;height:29px;min-width:88px;padding:0 9px 0 34px;background-position:center;background-repeat:no-repeat;background-size:100% 100%;white-space:nowrap;color:#333;font-size:11px;font-weight:600}.card{box-sizing:border-box;width:100%;margin-bottom:12px;padding:17px;background:#fff;border:1px solid #e8e8e8;border-radius:12px;box-shadow:0 3px 14px rgba(40,28,30,.045)}.hero-card{position:relative;overflow:hidden;border-top:3px solid var(--brand)}.hero-card::after{content:"";position:absolute;right:-34px;top:-52px;width:116px;height:116px;border-radius:50%;background:linear-gradient(135deg,rgba(215,4,26,.12),rgba(215,4,26,0));pointer-events:none}.section-kicker{margin-bottom:5px;color:var(--brand);font-size:10px;font-weight:700;letter-spacing:.12em}.hero-card h1{position:relative;margin:0;color:#171717;font-size:22px;line-height:1.35}.hero-card>p{position:relative;margin:8px 0 13px;color:#777;font-size:12px;line-height:1.65}.document-state{display:flex;min-width:0;align-items:center;gap:7px;padding:8px 10px;border-radius:7px;background:#fafafa;color:#999;font-size:11px}.document-state.ok{background:#f6ffed;color:#3d9b18}.dot{width:7px;height:7px;flex:0 0 7px;border-radius:50%;background:currentColor}.document-name{min-width:0;margin-left:auto;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#555}.card-heading{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:12px}.card-heading.compact{margin-bottom:12px}.card-heading>div{display:flex;align-items:center;gap:8px;min-width:0}.heading-index{display:inline-flex;align-items:center;justify-content:center;width:26px;height:22px;border-radius:5px;background:#fee7e8;color:var(--brand);font-size:10px;font-weight:700}.card-heading h2{margin:0;color:#222;font-size:15px}.timeline-status{padding:3px 7px;border-radius:999px;background:#fff4f4;color:var(--brand);font-size:10px}.ghost-btn{padding:7px 12px;border:1px solid #ddd;border-radius:7px;background:#fff;color:#555;font:inherit;font-size:11px;cursor:pointer}.ghost-btn:hover:not(:disabled){border-color:var(--brand);color:var(--brand)}.ghost-btn:disabled{opacity:.5;cursor:not-allowed}textarea,.line-input{box-sizing:border-box;width:100%;padding:11px 12px;border:1px solid #dedede;border-radius:8px;background:#fff;color:#333;font:inherit;font-size:12px;line-height:1.65;outline:none;transition:border-color .2s,box-shadow .2s}textarea{min-height:150px;resize:vertical}textarea:focus,.line-input:focus{border-color:var(--brand);box-shadow:0 0 0 3px rgba(215,4,26,.08)}.line-input{margin-top:10px}.line-input.short{width:150px;margin-top:0}.meta-row{display:flex;justify-content:space-between;gap:8px;margin-top:7px;color:#999;font-size:10px}.mode-pills{display:flex;gap:8px}.mode-pill{flex:1;display:flex;flex-direction:column;gap:3px;padding:10px 8px;border:1px solid #dedede;border-radius:9px;background:#fff;cursor:pointer;font:inherit;text-align:left;transition:border-color .2s,background .2s}.mode-pill strong{color:#333;font-size:12px}.mode-pill small{color:#999;font-size:9px;line-height:1.4}.mode-pill.active{border-color:var(--brand);background:#fff8f8}.mode-pill.active strong{color:var(--brand)}.mode-pill:disabled{opacity:.55;cursor:not-allowed}.target-row{display:flex;align-items:center;gap:9px;margin-top:11px;color:#555;font-size:11px}.actions{display:flex;gap:9px;margin-top:14px}.actions button{display:inline-flex;align-items:center;justify-content:center;min-height:38px;border-radius:7px;padding:8px 16px;font:inherit;font-size:12px;font-weight:600;cursor:pointer;transition:filter .2s,opacity .2s}.primary{flex:1;border:0;background:linear-gradient(90deg,var(--brand),var(--brand-deep));color:#fff;box-shadow:0 4px 12px rgba(215,4,26,.2)}.secondary{border:1px solid #ddd;background:#fff;color:#666}.actions button:disabled{opacity:.45;cursor:not-allowed;box-shadow:none}.progress-message{display:flex;align-items:flex-start;gap:7px;padding:9px 10px;border-radius:7px;background:#f7f7f7;color:#666;font-size:11px;line-height:1.5}.pulse-dot{width:6px;height:6px;flex:0 0 6px;margin-top:5px;border-radius:50%;background:var(--brand);animation:pulse 1.4s ease-in-out infinite}.result-block{margin:0;padding:12px;border:1px solid #eee;border-radius:8px;background:#fafafa;color:#333;font-size:11px;line-height:1.7;white-space:pre-wrap;word-break:break-word;max-height:46vh;overflow-y:auto}.origin-block{margin-top:9px}.origin-block summary{color:#999;font-size:10px;cursor:pointer}.origin-block pre{margin:8px 0 0;padding:10px;border-radius:7px;background:#f7f7f7;color:#888;font-size:10px;line-height:1.6;white-space:pre-wrap;word-break:break-word;max-height:30vh;overflow-y:auto}.applied-tip{margin-top:10px;padding:9px 10px;border-radius:7px;background:#f6ffed;color:#3d9b18;font-size:11px}.error{margin-top:11px;padding:9px 10px;border:1px solid #ffccc7;border-radius:7px;background:#fff1f0;color:#c53030;font-size:11px;line-height:1.55;overflow-wrap:anywhere}@keyframes pulse{0%,100%{box-shadow:0 0 0 3px rgba(215,4,26,.07)}50%{box-shadow:0 0 0 6px rgba(215,4,26,.03)}}
@media (max-width:560px){.polish-view{padding:0 8px 28px}.brand-line{margin:0 -8px}.panel-header{min-height:56px}.panel-logo{width:96px}.metric-pill{height:27px;min-width:80px;padding-left:30px;font-size:10px}.card{padding:14px 12px;margin-bottom:9px;border-radius:9px}.hero-card h1{font-size:19px}.document-state{align-items:flex-start;flex-wrap:wrap}.document-name{flex-basis:100%;margin-left:14px}.mode-pills{flex-direction:column}.actions{flex-direction:column}.actions button{width:100%}.target-row{flex-wrap:wrap}}
@media (max-width:390px){.panel-header{align-items:flex-start;flex-direction:column;padding:10px 3px}.account-strip{width:100%}.metric-pill{flex:1}}
</style>
