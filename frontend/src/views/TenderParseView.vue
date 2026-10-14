<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { Modal } from "ant-design-vue";
import {
  TENDER_PARSE_TABS,
  deleteTenderParseRecord,
  downloadTenderParseFile,
  formatDuration,
  getTenderParseRecord,
  listTenderParseRecords,
  retryTenderParseRecord,
  saveTenderParseEdit,
  uploadTenderParseRecord,
  type TenderParseListItem,
  type TenderParseRecord,
  type TenderParseResult,
} from "@/api/tenderParse";
import { apiErrorText } from "@/utils/apiError";
import { useBillingStore } from "@/stores/billing";
import logoUrl from "@/assets/images/ui/common-logo-black.png";
import iconWallet from "@/assets/images/ui/metric-icon-wallet.png";
import iconPoints from "@/assets/images/ui/metric-icon-points.png";

/**
 * 招标文件解析（/vsto/tender-parse，插件 TaskPane 500px 窄面板）。
 * 列表（查询/新增/轮询）与详情（六 Tab 三列表格 + 编辑保存）同组件内切换；
 * 视觉与交互骨架照 BidWizardView / PolishView（品牌线 + 卡片 + 状态灯）。
 */

const billingStore = useBillingStore();

// ------------------------------------------------------------------ 列表态
const items = ref<TenderParseListItem[]>([]);
const total = ref(0);
const page = ref(1);
const pageSize = 10;
const listLoading = ref(false);
const listError = ref("");

const qProjectName = ref("");
const qTenderUnit = ref("");
const qStatus = ref("");

const uploading = ref(false);
const fileInput = ref<HTMLInputElement | null>(null);
let listPollTimer: ReturnType<typeof setInterval> | null = null;

// ------------------------------------------------------------------ 详情态
const current = ref<TenderParseRecord | null>(null);
const detailError = ref("");
const activeTab = ref("basic");
const editing = ref(false);
const editDraft = ref<TenderParseResult>({});
const saving = ref(false);
const downloading = ref(false);
let detailPollTimer: ReturnType<typeof setInterval> | null = null;

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize)));

function formatMetric(value: number) {
  return new Intl.NumberFormat("zh-CN").format(Math.round(value || 0));
}

function formatDate(value?: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

/** 状态徽标：需求 §4.3 状态机（排队/文档解析中/要素提取中/已解析/解析失败） */
function statusInfo(item: { status: string; stage: string }) {
  if (item.status === "completed") return { text: "已解析", cls: "st-done" };
  if (item.status === "failed") return { text: "解析失败", cls: "st-fail" };
  if (item.status === "pending") return { text: "排队中", cls: "st-busy" };
  if (item.stage === "parsing") return { text: "文档解析中", cls: "st-busy" };
  if (item.stage === "extracting") return { text: "要素提取中", cls: "st-busy" };
  return { text: "解析中", cls: "st-busy" };
}

function friendlyError(error: unknown, fallback: string): string {
  // 优先取后端 detail（含 402/409 闸门对象的 message），再退回本地 Error 文案
  return apiErrorText(error) || (error instanceof Error ? error.message : fallback);
}

// ------------------------------------------------------------------ 列表逻辑

async function loadList() {
  listLoading.value = true;
  listError.value = "";
  try {
    const response = await listTenderParseRecords({
      projectName: qProjectName.value,
      tenderUnit: qTenderUnit.value,
      status: qStatus.value,
      page: page.value,
      pageSize,
    });
    items.value = response.items;
    total.value = response.total;
  } catch (error) {
    listError.value = friendlyError(error, "解析记录加载失败，请稍后重试");
  } finally {
    listLoading.value = false;
  }
  syncListPoll();
}

function syncListPoll() {
  const active = items.value.some(
    (item) => item.status === "pending" || item.status === "running",
  );
  if (active && !listPollTimer) {
    listPollTimer = setInterval(async () => {
      try {
        const response = await listTenderParseRecords({
          projectName: qProjectName.value,
          tenderUnit: qTenderUnit.value,
          status: qStatus.value,
          page: page.value,
          pageSize,
        });
        items.value = response.items;
        total.value = response.total;
        if (!response.items.some((item) => item.status === "pending" || item.status === "running")) {
          stopListPoll();
        }
      } catch {
        /* keep polling */
      }
    }, 2_000);
  } else if (!active) {
    stopListPoll();
  }
}

function stopListPoll() {
  if (listPollTimer) {
    clearInterval(listPollTimer);
    listPollTimer = null;
  }
}

function applyQuery() {
  page.value = 1;
  void loadList();
}

function resetQuery() {
  qProjectName.value = "";
  qTenderUnit.value = "";
  qStatus.value = "";
  page.value = 1;
  void loadList();
}

function turnPage(delta: number) {
  const next = page.value + delta;
  if (next < 1 || next > totalPages.value) return;
  page.value = next;
  void loadList();
}

function startUpload() {
  fileInput.value?.click();
}

async function onFileChange(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = "";
  if (!file) return;
  uploading.value = true;
  listError.value = "";
  try {
    await uploadTenderParseRecord(file);
    page.value = 1;
    await loadList();
  } catch (error) {
    listError.value = friendlyError(error, "上传失败，请稍后重试");
  } finally {
    uploading.value = false;
  }
}

function confirmRemove(item: TenderParseListItem) {
  Modal.confirm({
    title: "删除该解析记录？",
    content: `将删除「${item.project_name}」的解析记录与原始文件，删除后不可恢复。`,
    okText: "删除",
    okType: "danger",
    cancelText: "取消",
    onOk: async () => {
      try {
        await deleteTenderParseRecord(item.id);
        if (total.value % pageSize === 1 && page.value > 1) page.value -= 1;
        await loadList();
      } catch (error) {
        listError.value = friendlyError(error, "删除失败，请稍后重试");
      }
    },
  });
}

function confirmRetry(item: TenderParseListItem) {
  Modal.confirm({
    title: item.status === "failed" ? "重新解析？" : "重新解析并覆盖当前结果？",
    content:
      item.status === "failed"
        ? `将从失败环节继续解析「${item.project_name}」。`
        : `将重新提取「${item.project_name}」的招标要素，当前结果与人工修正会被覆盖。`,
    okText: "重新解析",
    cancelText: "取消",
    onOk: async () => {
      try {
        await retryTenderParseRecord(item.id);
        await loadList();
      } catch (error) {
        listError.value = friendlyError(error, "发起重试失败，请稍后重试");
      }
    },
  });
}

// ------------------------------------------------------------------ 详情逻辑

async function openDetail(item: TenderParseListItem) {
  detailError.value = "";
  editing.value = false;
  activeTab.value = "basic";
  try {
    current.value = await getTenderParseRecord(item.id);
    syncDetailPoll();
  } catch (error) {
    detailError.value = friendlyError(error, "解析记录加载失败，请稍后重试");
  }
}

function backToList() {
  stopDetailPoll();
  current.value = null;
  detailError.value = "";
  void loadList();
}

function syncDetailPoll() {
  const record = current.value;
  const active = record?.status === "pending" || record?.status === "running";
  if (record && active && !detailPollTimer) {
    const recordId = record.id;
    detailPollTimer = setInterval(async () => {
      try {
        current.value = await getTenderParseRecord(recordId);
        if (current.value && current.value.status !== "pending" && current.value.status !== "running") {
          stopDetailPoll();
        }
      } catch {
        /* keep polling */
      }
    }, 2_000);
  } else if (!active) {
    stopDetailPoll();
  }
}

function stopDetailPoll() {
  if (detailPollTimer) {
    clearInterval(detailPollTimer);
    detailPollTimer = null;
  }
}

/** 展示口径：有人工修正以修正版为准（需求 §4.4） */
const displayedResult = computed<TenderParseResult | null>(
  () => current.value?.manual_edit ?? current.value?.result ?? null,
);

const currentRows = computed(() => displayedResult.value?.[activeTab.value] ?? []);

const primaryFile = computed(
  () => current.value?.documents?.[0] ?? null,
);

function beginEdit() {
  if (!displayedResult.value) return;
  const draft: TenderParseResult = {};
  for (const tab of TENDER_PARSE_TABS) {
    draft[tab.key] = (displayedResult.value[tab.key] ?? []).map((row) => ({ ...row }));
  }
  editDraft.value = draft;
  editing.value = true;
}

function cancelEdit() {
  editing.value = false;
  editDraft.value = {};
}

async function saveEdit() {
  if (!current.value) return;
  saving.value = true;
  detailError.value = "";
  try {
    current.value = await saveTenderParseEdit(current.value.id, editDraft.value);
    editing.value = false;
  } catch (error) {
    detailError.value = friendlyError(error, "保存失败，请稍后重试");
  } finally {
    saving.value = false;
  }
}

async function downloadFile() {
  if (!current.value) return;
  downloading.value = true;
  try {
    await downloadTenderParseFile(
      current.value.id,
      primaryFile.value?.original_filename || current.value.project_name,
    );
  } catch (error) {
    detailError.value = friendlyError(error, "文件下载失败，请稍后重试");
  } finally {
    downloading.value = false;
  }
}

function confirmRetryCurrent() {
  if (!current.value) return;
  const record = current.value;
  Modal.confirm({
    title: record.status === "failed" ? "重新解析？" : "重新解析并覆盖当前结果？",
    content:
      record.status === "failed"
        ? `将从失败环节继续解析「${record.project_name}」。`
        : `将重新提取「${record.project_name}」的招标要素，当前结果与人工修正会被覆盖。`,
    okText: "重新解析",
    cancelText: "取消",
    onOk: async () => {
      try {
        current.value = await retryTenderParseRecord(record.id);
        editing.value = false;
        syncDetailPoll();
      } catch (error) {
        detailError.value = friendlyError(error, "发起重试失败，请稍后重试");
      }
    },
  });
}

onMounted(() => {
  void billingStore.fetchWallet().catch(() => undefined);
  void loadList();
});

onUnmounted(() => {
  stopListPoll();
  stopDetailPoll();
});
</script>

<template>
  <main class="tender-parse-view">
    <div class="brand-line" />
    <header class="panel-header">
      <img :src="logoUrl" alt="标书审查智能体" class="panel-logo">
      <div class="account-strip" :aria-label="billingStore.privateCloud ? '企业剩余次数' : '账户余额'">
        <template v-if="billingStore.privateCloud">
          <span
            class="metric-pill"
            :style="{ '--pill-icon': `url(${iconWallet})` }"
            title="AI 剩余次数（企业共享次数池，检查/查重/暗标/生成/润色等每个任务消耗 1 次）"
          >
            <span>AI 剩余 {{ billingStore.loading && !billingStore.wallet ? "--" : formatMetric(billingStore.rechargeBalance) }} 次</span>
          </span>
          <span
            class="metric-pill"
            :style="{ '--pill-icon': `url(${iconPoints})` }"
            title="OCR 剩余次数（企业共享次数池，每次图片文字识别消耗 1 次）"
          >
            <span>OCR 剩余 {{ billingStore.loading && !billingStore.wallet ? "--" : formatMetric(billingStore.giftBalance) }} 次</span>
          </span>
        </template>
        <template v-else>
          <span class="metric-pill" :style="{ '--pill-icon': `url(${iconWallet})` }">
            <span>{{ billingStore.loading && !billingStore.wallet ? "--" : formatMetric(billingStore.balanceWen) }}点</span>
          </span>
          <span class="metric-pill" :style="{ '--pill-icon': `url(${iconPoints})` }">
            <span>{{ billingStore.loading && !billingStore.wallet ? "--" : formatMetric(billingStore.points) }}积分</span>
          </span>
        </template>
      </div>
    </header>

    <!-- ======================= 列表 ======================= -->
    <template v-if="!current">
      <section class="card hero-card">
        <div class="section-kicker">WORD · 招标文件解析</div>
        <h1>招标文件解析</h1>
        <p>上传招标文件，自动完成文档解析与 AI 要素提取，按基本信息 / 资格条件 / 评标办法 / 技术需求 / 商务要求 / 其他内容六个页签结构化呈现，支持人工修正。</p>
        <input
          ref="fileInput"
          type="file"
          accept=".pdf,.docx,.doc,.xlsx"
          hidden
          :disabled="uploading"
          @change="onFileChange"
        >
        <div class="actions">
          <button class="primary" type="button" :disabled="uploading" @click="startUpload">
            <span v-if="uploading" class="button-spinner" aria-hidden="true" />
            {{ uploading ? "正在上传…" : "新增解析" }}
          </button>
        </div>
        <div v-if="listError" class="error" role="alert">{{ listError }}</div>
      </section>

      <section class="card">
        <div class="query-grid">
          <label class="query-field">
            <span>项目名称</span>
            <input
              v-model="qProjectName"
              type="text"
              maxlength="200"
              placeholder="按项目名称搜索"
              @keyup.enter="applyQuery"
            >
          </label>
          <label class="query-field">
            <span>招标方名称</span>
            <input
              v-model="qTenderUnit"
              type="text"
              maxlength="200"
              placeholder="按招标方搜索"
              @keyup.enter="applyQuery"
            >
          </label>
          <label class="query-field">
            <span>状态</span>
            <select v-model="qStatus">
              <option value="">全部</option>
              <option value="running">解析中</option>
              <option value="completed">已解析</option>
              <option value="failed">解析失败</option>
            </select>
          </label>
        </div>
        <div class="query-actions">
          <button class="secondary" type="button" @click="applyQuery">查询</button>
          <button class="ghost-btn" type="button" @click="resetQuery">重置</button>
        </div>
      </section>

      <section class="card">
        <div class="card-heading">
          <div>
            <span class="heading-index">01</span>
            <h2>解析记录</h2>
          </div>
          <span class="record-count">共 {{ total }} 条</span>
        </div>

        <div v-if="listLoading && items.length === 0" class="empty">正在加载解析记录…</div>
        <div v-else-if="items.length === 0" class="empty">
          暂无解析记录，点击「新增解析」上传招标文件开始。
        </div>

        <article
          v-for="(item, index) in items"
          :key="item.id"
          class="record-card"
          :data-index="index"
        >
          <div class="record-head">
            <strong class="record-title" :title="item.project_name">{{ item.project_name }}</strong>
            <span class="status-badge" :class="statusInfo(item).cls">{{ statusInfo(item).text }}</span>
          </div>
          <div class="record-row">
            <span class="row-label">招标方</span>
            <span class="row-value" :title="item.tender_unit || ''">{{ item.tender_unit || "—（解析完成后回填）" }}</span>
          </div>
          <div class="record-row">
            <span class="row-label">招标文件</span>
            <span class="row-value file-name" :title="item.file_name || ''">{{ item.file_name || "—" }}</span>
          </div>
          <div class="record-row">
            <span class="row-label">耗时</span>
            <span class="row-value">{{ formatDuration(item.duration_seconds) }}</span>
            <span class="row-label">创建时间</span>
            <span class="row-value">{{ formatDate(item.created_at) }}</span>
          </div>
          <div v-if="item.status === 'failed' && item.error_message" class="record-error" :title="item.error_message">
            {{ item.error_message }}
          </div>
          <div class="record-actions">
            <button class="link-btn" type="button" @click="openDetail(item)">查看</button>
            <button
              v-if="item.status === 'failed' || item.status === 'completed'"
              class="link-btn"
              type="button"
              @click="confirmRetry(item)"
            >重新解析</button>
            <button class="link-btn danger" type="button" @click="confirmRemove(item)">删除</button>
          </div>
        </article>

        <div v-if="total > pageSize" class="pager">
          <button class="secondary" type="button" :disabled="page <= 1" @click="turnPage(-1)">上一页</button>
          <span class="pager-text">{{ page }} / {{ totalPages }} 页</span>
          <button class="secondary" type="button" :disabled="page >= totalPages" @click="turnPage(1)">下一页</button>
        </div>
      </section>
    </template>

    <!-- ======================= 详情 ======================= -->
    <template v-else>
      <section class="card hero-card">
        <button class="ghost-btn back-btn" type="button" @click="backToList">← 返回列表</button>
        <div class="section-kicker">解析结果</div>
        <h1 class="detail-title" :title="current.project_name">{{ current.project_name }} 解析结果</h1>
        <div class="detail-meta">
          <span class="status-badge" :class="statusInfo(current).cls">{{ statusInfo(current).text }}</span>
          <span v-if="current.tender_unit" class="meta-item">招标方：{{ current.tender_unit }}</span>
          <span class="meta-item">耗时：{{ formatDuration(current.duration_seconds) }}</span>
          <span class="meta-item">创建：{{ formatDate(current.created_at) }}</span>
        </div>
        <div class="detail-file">
          <button
            class="ghost-btn"
            type="button"
            :disabled="downloading || !primaryFile"
            @click="downloadFile"
          >{{ downloading ? "正在下载…" : `下载原文件：${primaryFile?.original_filename || "—"}` }}</button>
        </div>
        <div v-if="detailError" class="error" role="alert">{{ detailError }}</div>

        <div v-if="current.status === 'pending' || current.status === 'running'" class="progress-message">
          <span class="pulse-dot" aria-hidden="true" />
          <span>{{
            current.status === "pending"
              ? "已排队，等待解析…"
              : current.stage === "parsing"
                ? "正在进行文档解析（提取文本与表格）…"
                : "正在进行 AI 要素提取，通常需要几十秒…"
          }}</span>
        </div>
        <template v-else-if="current.status === 'failed'">
          <div class="error" role="alert">
            解析失败（{{ current.failed_stage === "extracting" ? "要素提取" : "文档解析" }}环节）：{{ current.error_message || "未知错误" }}
          </div>
          <div class="actions">
            <button class="primary" type="button" @click="confirmRetryCurrent">重新解析</button>
          </div>
        </template>
      </section>

      <section v-if="current.status === 'completed' && displayedResult" class="card">
        <div class="tab-bar" role="tablist">
          <button
            v-for="tab in TENDER_PARSE_TABS"
            :key="tab.key"
            class="tab-pill"
            :class="{ active: activeTab === tab.key }"
            type="button"
            role="tab"
            :aria-selected="activeTab === tab.key"
            @click="activeTab = tab.key"
          >{{ tab.label }}</button>
        </div>

        <div class="result-toolbar">
          <span class="edited-hint">
            <template v-if="current.edited_at">已人工修正 · 保存于 {{ formatDate(current.edited_at) }}</template>
            <template v-else>AI 提取结果</template>
          </span>
          <span v-if="editing" class="edit-actions">
            <button class="secondary" type="button" :disabled="saving" @click="cancelEdit">取消</button>
            <button class="primary slim" type="button" :disabled="saving" @click="saveEdit">
              <span v-if="saving" class="button-spinner" aria-hidden="true" />
              {{ saving ? "正在保存…" : "保存" }}
            </button>
          </span>
          <button v-else class="secondary" type="button" @click="beginEdit">编辑</button>
        </div>

        <div class="table-wrap">
          <table class="result-table">
            <thead>
              <tr>
                <th class="col-field">字段名</th>
                <th class="col-value">内容</th>
                <th class="col-source">原文位置</th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="currentRows.length === 0">
                <td colspan="3" class="empty">本页签暂无内容</td>
              </tr>
              <tr v-for="(row, rowIndex) in currentRows" :key="`${activeTab}-${rowIndex}`">
                <td class="col-field">{{ row.field || "—" }}</td>
                <td class="col-value">
                  <template v-if="editing">
                    <textarea
                      v-model="editDraft[activeTab]![rowIndex]!.value"
                      rows="2"
                      maxlength="4000"
                    />
                  </template>
                  <template v-else>{{ row.value || "—" }}</template>
                </td>
                <td class="col-source">{{ row.source || "—" }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p class="edit-tip">
          「编辑」可修正各字段的「内容」（字段名与原文位置为只读）；保存后详情展示以修正版为准，重新解析会覆盖修正。
        </p>
      </section>
    </template>
  </main>
</template>

<style scoped>
.tender-parse-view{--brand:#d7041a;--brand-deep:#b80015;box-sizing:border-box;width:100%;max-width:760px;min-height:100vh;margin:0 auto;padding:0 12px 36px;background:#f5f5f5;color:#333;overflow-x:hidden;font-family:"Microsoft YaHei","PingFang SC",Arial,sans-serif;font-size:13px}
.brand-line{height:3px;margin:0 -12px;background:linear-gradient(90deg,var(--brand),var(--brand-deep))}
.panel-header{display:flex;align-items:center;justify-content:space-between;gap:12px;min-height:60px;padding:10px 4px}
.panel-logo{width:108px;height:auto;object-fit:contain}
.account-strip{display:flex;align-items:center;gap:7px;min-width:0}
.metric-pill{display:inline-flex;align-items:center;box-sizing:border-box;height:29px;min-width:88px;padding:0 11px 0 9px;border-radius:999px;background:linear-gradient(180deg,#fafafa 0%,#fbe3be 9%,#fbe1ba 50%,#fbe3be 91%,#fafafa 100%);white-space:nowrap;color:#333;font-size:11px;font-weight:600}.metric-pill::before{content:"";width:17px;height:20px;flex:0 0 17px;margin-right:5px;background:var(--pill-icon) center/contain no-repeat}
.card{box-sizing:border-box;width:100%;margin-bottom:12px;padding:17px;background:#fff;border:1px solid #e8e8e8;border-radius:12px;box-shadow:0 3px 14px rgba(40,28,30,.045)}
.hero-card{position:relative;overflow:hidden;border-top:3px solid var(--brand)}
.hero-card::after{content:"";position:absolute;right:-34px;top:-52px;width:116px;height:116px;border-radius:50%;background:linear-gradient(135deg,rgba(215,4,26,.12),rgba(215,4,26,0));pointer-events:none}
.section-kicker{margin-bottom:5px;color:var(--brand);font-size:10px;font-weight:700;letter-spacing:.12em}
.hero-card h1{position:relative;z-index:1;margin:0;color:#171717;font-size:22px;line-height:1.35}
.hero-card>p{position:relative;z-index:1;margin:8px 0 13px;color:#777;font-size:12px;line-height:1.65}
.back-btn{margin-bottom:10px}
.detail-title{overflow-wrap:anywhere}
.detail-meta{position:relative;z-index:1;display:flex;flex-wrap:wrap;align-items:center;gap:9px;margin:4px 0 10px;color:#666;font-size:11px}
.detail-file{position:relative;z-index:1}
.meta-item{color:#888}
.actions{display:flex;gap:9px;margin-top:14px}
.actions button{display:inline-flex;align-items:center;justify-content:center;gap:7px;min-height:38px;border-radius:7px;padding:8px 16px;font:inherit;font-size:12px;font-weight:600;cursor:pointer;transition:filter .2s,transform .1s,opacity .2s}
.primary{flex:1;border:0;background:linear-gradient(90deg,var(--brand),var(--brand-deep));color:#fff;box-shadow:0 4px 12px rgba(215,4,26,.2)}
.primary:hover:not(:disabled){filter:brightness(1.05)}
.primary:active:not(:disabled){transform:scale(.99)}
.primary.slim{flex:0 0 auto}
.secondary{border:1px solid #ddd;background:#fff;color:#666}
.primary:disabled,.secondary:disabled{opacity:.45;cursor:not-allowed;box-shadow:none}
.ghost-btn{padding:6px 12px;border:1px solid #ddd;border-radius:7px;background:#fff;color:#555;font:inherit;font-size:11px;cursor:pointer;transition:border-color .2s,color .2s}
.ghost-btn:hover:not(:disabled){border-color:var(--brand);color:var(--brand)}
.ghost-btn:disabled{opacity:.5;cursor:not-allowed}
.button-spinner{width:13px;height:13px;border:2px solid rgba(255,255,255,.45);border-top-color:#fff;border-radius:50%;animation:spin .75s linear infinite}
.error{margin-top:11px;padding:9px 10px;border:1px solid #ffccc7;border-radius:7px;background:#fff1f0;color:#c53030;font-size:11px;line-height:1.55;overflow-wrap:anywhere}
.progress-message{display:flex;align-items:flex-start;gap:7px;margin-top:11px;padding:9px 10px;border-radius:7px;background:#f7f7f7;color:#666;font-size:11px;line-height:1.5}
.pulse-dot{width:6px;height:6px;flex:0 0 6px;margin-top:5px;border-radius:50%;background:var(--brand);animation:pulse 1.4s ease-in-out infinite}
.query-grid{display:grid;grid-template-columns:1fr;gap:11px}
.query-field{display:flex;flex-direction:column;gap:5px;font-size:11px;color:#666}
.query-field input,.query-field select{box-sizing:border-box;width:100%;min-height:36px;padding:7px 10px;border:1px solid #dedede;border-radius:7px;background:#fff;color:#333;font:inherit;font-size:12px;outline:none;transition:border-color .2s,box-shadow .2s}
.query-field input:focus,.query-field select:focus{border-color:var(--brand);box-shadow:0 0 0 3px rgba(215,4,26,.08)}
.query-actions{display:flex;gap:9px;margin-top:12px}
.query-actions .secondary{min-height:34px;padding:6px 18px;border-radius:7px;font-size:12px;cursor:pointer}
.card-heading{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:12px}
.card-heading>div{display:flex;align-items:center;gap:8px;min-width:0}
.heading-index{display:inline-flex;align-items:center;justify-content:center;width:26px;height:22px;border-radius:5px;background:#fee7e8;color:var(--brand);font-size:10px;font-weight:700}
.card-heading h2{margin:0;color:#222;font-size:15px}
.record-count{flex:0 0 auto;color:#999;font-size:11px}
.empty{padding:22px;text-align:center;color:#999;font-size:11px}
.record-card{margin-bottom:11px;padding:12px;border:1px solid #eee;border-radius:9px;background:#fafafa;transition:border-color .2s,box-shadow .2s}
.record-card:hover{border-color:#f1c9cd;box-shadow:0 3px 10px rgba(215,4,26,.06)}
.record-head{display:flex;align-items:flex-start;justify-content:space-between;gap:9px;margin-bottom:8px}
.record-title{min-width:0;color:#222;font-size:13px;line-height:1.45;overflow-wrap:anywhere}
.status-badge{flex:0 0 auto;padding:3px 8px;border-radius:999px;font-size:10px;font-weight:600;white-space:nowrap}
.st-busy{background:#fffbe6;color:#c58608}
.st-done{background:#f6ffed;color:#3d9b18}
.st-fail{background:#fff1f0;color:#d7041a}
.record-row{display:flex;align-items:baseline;gap:6px;margin-top:4px;font-size:11px;line-height:1.5}
.row-label{flex:0 0 auto;color:#999}
.row-value{min-width:0;color:#555;overflow-wrap:anywhere}
.record-row .row-label:last-of-type{margin-left:10px}
.file-name{color:var(--brand)}
.record-error{margin-top:7px;padding:7px 8px;border-radius:6px;background:#fff1f0;color:#c53030;font-size:10px;line-height:1.5;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.record-actions{display:flex;gap:4px;margin-top:9px}
.link-btn{padding:5px 11px;border:0;border-radius:6px;background:transparent;color:var(--brand);font:inherit;font-size:11px;font-weight:600;cursor:pointer;transition:background .2s}
.link-btn:hover{background:#fee7e8}
.link-btn.danger{color:#d46b08}
.link-btn.danger:hover{background:#fff7e6}
.pager{display:flex;align-items:center;justify-content:center;gap:11px;margin-top:14px}
.pager .secondary{min-height:30px;padding:4px 12px;font-size:11px;cursor:pointer}
.pager-text{color:#999;font-size:11px}
.tab-bar{display:flex;gap:6px;overflow-x:auto;padding-bottom:9px;margin-bottom:11px}
.tab-pill{flex:0 0 auto;padding:7px 12px;border:1px solid #e5e5e5;border-radius:999px;background:#fff;color:#666;font:inherit;font-size:11px;white-space:nowrap;cursor:pointer;transition:all .2s}
.tab-pill.active{border-color:var(--brand);background:#fee7e8;color:var(--brand);font-weight:600}
.result-toolbar{display:flex;align-items:center;justify-content:space-between;gap:9px;margin-bottom:11px}
.result-toolbar .secondary{min-height:32px;padding:5px 14px;font-size:11px;cursor:pointer}
.edited-hint{color:#999;font-size:10px}
.edit-actions{display:flex;gap:7px}
.edit-actions .secondary{cursor:pointer}
.table-wrap{overflow-x:auto}
.result-table{width:100%;border-collapse:collapse;font-size:11px;line-height:1.55}
.result-table th{padding:8px 9px;background:#fafafa;border-bottom:1px solid #ececec;color:#888;font-weight:600;text-align:left;white-space:nowrap}
.result-table td{padding:9px;border-bottom:1px solid #f2f2f2;vertical-align:top;color:#444}
.col-field{width:26%;color:#333}
.col-source{width:34%;color:#999}
.col-value textarea{box-sizing:border-box;width:100%;min-height:52px;padding:7px 9px;border:1px solid #dedede;border-radius:6px;background:#fff;color:#333;resize:vertical;font:inherit;font-size:11px;line-height:1.55;outline:none;transition:border-color .2s,box-shadow .2s}
.col-value textarea:focus{border-color:var(--brand);box-shadow:0 0 0 3px rgba(215,4,26,.08)}
.edit-tip{margin:11px 0 0;color:#aaa;font-size:10px;line-height:1.6}
@keyframes spin{to{transform:rotate(360deg)}}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.35}}
@media (max-width:560px){.tender-parse-view{padding:0 8px 28px}.brand-line{margin:0 -8px}.panel-header{min-height:56px}.panel-logo{width:96px}.metric-pill{height:27px;min-width:80px;font-size:10px}.metric-pill::before{width:15px;height:18px;flex-basis:15px}.card{padding:14px 12px;margin-bottom:9px;border-radius:9px}.hero-card h1{font-size:19px}.actions{flex-direction:column}.actions button{width:100%}}
@media (max-width:390px){.panel-header{align-items:flex-start;flex-direction:column;padding:10px 3px}.account-strip{width:100%}.metric-pill{flex:1}}
</style>
