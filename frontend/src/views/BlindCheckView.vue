<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import {
  blindCheckStreamUrl,
  blindCheckToken,
  cancelBlindCheckTask,
  closeVstoToolSession,
  createBlindCheckTask,
  createVstoToolSession,
  getBlindCheckResults,
  heartbeatVstoToolSession,
  submitVstoToolResult,
  type BlindCheckFinding,
} from "@/api/blindCheck";
import { useBillingStore } from "@/stores/billing";
import logoUrl from "@/assets/images/ui/common-logo-black.png";
import iconWallet from "@/assets/images/ui/common-icon-wallet.png";
import iconPoints from "@/assets/images/ui/common-icon-points.png";

const BRIDGE_REQUEST = "bjt.vsto.tool.request";
const BRIDGE_RESULT = "bjt.vsto.tool.result";
const BRIDGE_CONTEXT = "bjt.vsto.context";
const BRIDGE_BIND_RESULT = "bjt.vsto.session.bind.result";
const BRIDGE_LOCATE_RESULT = "bjt.vsto.locate.result";
const billingStore = useBillingStore();
const timelineSteps = [
  { title: "建立全文快照", detail: "锁定当前 Word 文档版本" },
  { title: "读取文档证据", detail: "通过只读工具检查全文" },
  { title: "智能合规研判", detail: "逐项对照暗标要求" },
  { title: "汇总检查结果", detail: "生成风险与证据定位" },
];

const requirementText = ref("");
const toolSessionId = ref("");
const taskId = ref("");
const documentName = ref("");
const documentKey = ref("");
const documentRevision = ref("");
const snapshotId = ref("");
const wholeDocumentConfirmed = ref(false);
const bridgeState = ref<"disconnected" | "ready" | "busy">("disconnected");
const submitting = ref(false);
const cancelling = ref(false);
const finished = ref(false);
const errorMessage = ref("");
const progressMessage = ref("等待提交检查…");
const progressStep = ref(0);
const findings = ref<BlindCheckFinding[]>([]);
const summary = ref<Record<string, unknown>>({ overall: "unknown", critical: 0, major: 0, minor: 0, unknown: 0 });
let streamController: AbortController | null = null;
let heartbeatTimer: ReturnType<typeof setInterval> | null = null;
let bridgeListener: ((event: MessageEvent) => void) | null = null;
let openingSession: Promise<void> | null = null;
const forwardedToolCalls = new Set<string>();
const clientInstanceId = typeof crypto !== "undefined" && "randomUUID" in crypto
  ? crypto.randomUUID()
  : `word-${Date.now()}-${Math.random().toString(16).slice(2)}`;

const running = computed(() => Boolean(taskId.value) && !finished.value);
const canSubmit = computed(() => Boolean(
  requirementText.value.trim()
  && toolSessionId.value
  && documentKey.value
  && snapshotId.value
  && bridgeState.value === "ready"
  && wholeDocumentConfirmed.value
  && !submitting.value
  && !running.value,
));
const overallText = computed(() => ({ pass: "未发现明确违规", fail: "发现暗标风险", unknown: "存在待确认项目" } as Record<string, string>)[String(summary.value.overall)] || "检查完成");
const bridgeStateText = computed(() => bridgeState.value === "ready" ? "已连接 Word 文档" : bridgeState.value === "busy" ? "正在读取 Word 文档" : "等待 Word 插件连接");
const coverageIncompleteTools = computed(() => {
  const value = summary.value.coverage_incomplete_tools;
  return Array.isArray(value) ? value.map((item) => String(item)) : [];
});
const coverageText = computed(() => coverageIncompleteTools.value.length
  ? `以下检查尚未完成全文覆盖：${coverageIncompleteTools.value.join("、")}`
  : (summary.value.coverage_complete === true ? "确定性检查已完成声明范围覆盖" : "检查覆盖度信息待确认"));

function timelineState(index: number): "done" | "active" | "waiting" {
  const step = index + 1;
  if (progressStep.value > step || (finished.value && progressStep.value >= step)) return "done";
  if (!finished.value && progressStep.value === step) return "active";
  return "waiting";
}

function formatMetric(value: number) {
  return new Intl.NumberFormat("zh-CN").format(Math.round(value || 0));
}

function postBridge(message: Record<string, unknown>) {
  const webview = (window as Window & { chrome?: { webview?: { postMessage: (value: unknown) => void } } }).chrome?.webview;
  webview?.postMessage(message);
}

function handleBridgeMessage(event: MessageEvent) {
  let message: unknown = event.data;
  if (typeof message === "string") {
    try { message = JSON.parse(message); } catch { return; }
  }
  if (!message || typeof message !== "object") return;
  const payload = message as Record<string, unknown>;
  if (payload.type === BRIDGE_CONTEXT) {
    if (payload.success === false || payload.error) {
      bridgeState.value = "disconnected";
      errorMessage.value = String(payload.error || "Word 文档上下文读取失败，请关闭页面后重试");
      return;
    }
    const context = (payload.data || {}) as Record<string, unknown>;
    const nextDocumentName = String(context.document_name || "");
    const nextDocumentKey = String(context.document_key || "");
    const nextDocumentRevision = String(context.document_revision || "");
    const nextSnapshotId = String(context.snapshot_id || "");
    if (!nextDocumentKey || !nextDocumentRevision || !nextSnapshotId) {
      bridgeState.value = "disconnected";
      errorMessage.value = "Word 插件未返回完整文档快照，请关闭页面后重试";
      return;
    }
    const documentChanged = Boolean(documentKey.value) && (
      documentKey.value !== nextDocumentKey
      || documentRevision.value !== nextDocumentRevision
      || snapshotId.value !== nextSnapshotId
    );
    documentName.value = nextDocumentName;
    documentKey.value = nextDocumentKey;
    documentRevision.value = nextDocumentRevision;
    snapshotId.value = nextSnapshotId;
    if (documentChanged) wholeDocumentConfirmed.value = false;
    if (documentChanged && running.value) {
      bridgeState.value = "disconnected";
      errorMessage.value = "检查过程中 Word 文档已变化，本次结果将作废，请重新检查";
      return;
    }
    if (documentChanged && toolSessionId.value) {
      const previousSessionId = toolSessionId.value;
      toolSessionId.value = "";
      if (heartbeatTimer) {
        clearInterval(heartbeatTimer);
        heartbeatTimer = null;
      }
      void closeVstoToolSession(previousSessionId).catch(() => undefined);
      forwardedToolCalls.clear();
    }
    bridgeState.value = "busy";
    void openSession();
  } else if (payload.type === BRIDGE_BIND_RESULT) {
    if (String(payload.tool_session_id || "") !== toolSessionId.value) return;
    if (payload.success === true && String(payload.snapshot_id || "") === snapshotId.value) {
      bridgeState.value = "ready";
      errorMessage.value = "";
    } else {
      bridgeState.value = "disconnected";
      errorMessage.value = String(payload.error || "Word 工具会话绑定失败，请重新打开页面");
    }
  } else if (payload.type === BRIDGE_RESULT) {
    bridgeState.value = "ready";
    void forwardToolResult(payload);
  } else if (payload.type === BRIDGE_LOCATE_RESULT) {
    if (payload.success === true) {
      progressMessage.value = "已在 Word 中定位到证据";
    } else {
      errorMessage.value = String(payload.error || "无法在 Word 中定位该证据");
    }
  }
}

function stopTaskStream() {
  if (streamController) {
    streamController.abort();
    streamController = null;
  }
}

async function openSession() {
  if (toolSessionId.value || openingSession) return openingSession || undefined;
  if (!documentKey.value || !documentRevision.value || !snapshotId.value) return;
  openingSession = (async () => {
    try {
      const session = await createVstoToolSession({
        client_instance_id: clientInstanceId,
        document_name: documentName.value || null,
        document_key: documentKey.value,
        document_revision: documentRevision.value,
        snapshot_id: snapshotId.value,
      });
      toolSessionId.value = session.id;
      if (heartbeatTimer) clearInterval(heartbeatTimer);
      heartbeatTimer = setInterval(() => {
        if (toolSessionId.value) void heartbeatVstoToolSession(toolSessionId.value).catch(() => undefined);
      }, 60_000);
      postBridge({
        type: "bjt.vsto.session.bind",
        tool_session_id: session.id,
        snapshot_id: snapshotId.value,
      });
    } catch (error) {
      bridgeState.value = "disconnected";
      errorMessage.value = errorText(error, "无法建立文档工具会话，请确认已登录");
    }
  })().finally(() => { openingSession = null; });
  return openingSession;
}

async function startCheck() {
  if (!canSubmit.value) return;
  if (!toolSessionId.value) await openSession();
  if (!toolSessionId.value) return;
  submitting.value = true;
  finished.value = false;
  errorMessage.value = "";
  stopTaskStream();
  forwardedToolCalls.clear();
  findings.value = [];
  summary.value = { overall: "unknown", critical: 0, major: 0, minor: 0, unknown: 0 };
  progressMessage.value = "正在创建检查任务…";
  try {
    const task = await createBlindCheckTask({
      tool_session_id: toolSessionId.value,
      requirement_text: requirementText.value,
      document_name: documentName.value || null,
      document_key: documentKey.value || null,
      document_revision: documentRevision.value || null,
      scope: { mode: "whole_document", confirmed: wholeDocumentConfirmed.value },
    });
    taskId.value = task.id;
    progressStep.value = 1;
    progressMessage.value = "已提交，等待智能体读取文档…";
    void listenTask(task.id);
  } catch (error) {
    errorMessage.value = errorText(error, "提交暗标检查失败");
  } finally {
    submitting.value = false;
  }
}

async function listenTask(id: string) {
  const headers: HeadersInit = { Accept: "text/event-stream" };
  const token = blindCheckToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  stopTaskStream();
  const controller = new AbortController();
  streamController = controller;
  try {
    const response = await fetch(blindCheckStreamUrl(id), { headers, signal: controller.signal });
    if (!response.ok || !response.body) throw new Error(`SSE 连接失败（${response.status}）`);
    const reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";
    while (true) {
      const result = await reader.read();
      if (result.done) break;
      buffer += decoder.decode(result.value, { stream: true });
      const blocks = buffer.split("\n\n");
      buffer = blocks.pop() || "";
      blocks.forEach((block) => {
        const line = block.split("\n").find((item) => item.startsWith("data:"));
        if (!line) return;
        try { handleTaskEvent(JSON.parse(line.slice(5).trim()) as Record<string, unknown>); } catch { /* ignore replay noise */ }
      });
    }
  } catch (error) {
    if (!(error instanceof DOMException && error.name === "AbortError") && !finished.value) errorMessage.value = errorText(error, "检查进度连接中断");
  } finally {
    if (streamController === controller) streamController = null;
  }
}

function handleTaskEvent(event: Record<string, unknown>) {
  const type = String(event.type || "");
  if (type === "status") {
    const status = String(event.status || "");
    if (status === "running") { progressStep.value = Math.max(progressStep.value, 2); progressMessage.value = "智能体正在读取和分析文档…"; }
    if (status === "completed") { progressStep.value = 4; progressMessage.value = "检查完成"; finished.value = true; stopTaskStream(); void loadResults(); }
    if (status === "failed" || status === "cancelled") { finished.value = true; stopTaskStream(); errorMessage.value = String(event.message || "检查未完成"); }
  } else if (type === "vsto_tool_request") {
    const callId = String(event.call_id || "");
    if (!callId || String(event.tool_session_id || "") !== toolSessionId.value) {
      errorMessage.value = "收到不属于当前 Word 工具会话的调用请求，已拒绝执行";
      return;
    }
    if (forwardedToolCalls.has(callId)) return;
    forwardedToolCalls.add(callId);
    progressStep.value = Math.max(progressStep.value, 2);
    progressMessage.value = `正在调用 Word 工具：${String(event.tool || "")}`;
    bridgeState.value = "busy";
    postBridge({ type: BRIDGE_REQUEST, request_id: callId, call_id: callId, tool_session_id: event.tool_session_id, tool: event.tool, arguments: event.arguments || {} });
  } else if (["vsto_tool_result", "llm_output", "tool_call_start", "tool_call_end"].includes(type)) {
    progressStep.value = Math.max(progressStep.value, 3);
    progressMessage.value = "全文证据读取完成，正在进行智能合规研判…";
  } else if (type === "result") {
    progressStep.value = 4;
    finished.value = true;
    stopTaskStream();
    if (event.summary && typeof event.summary === "object") summary.value = event.summary as Record<string, unknown>;
    void loadResults();
  } else if (type === "error") {
    finished.value = true;
    stopTaskStream();
    errorMessage.value = String(event.message || "检查失败");
  }
}

async function forwardToolResult(message: Record<string, unknown>) {
  const callId = String(message.call_id || "");
  if (!callId || !toolSessionId.value) return;
  if (String(message.tool_session_id || "") !== toolSessionId.value) {
    errorMessage.value = "收到不属于当前 Word 工具会话的结果，已拒绝回传";
    return;
  }
  try {
    await submitVstoToolResult({
      tool_session_id: String(message.tool_session_id || toolSessionId.value),
      call_id: callId,
      success: message.success === true,
      data: (message.data && typeof message.data === "object" ? message.data : {}) as Record<string, unknown>,
      content: String(message.content || ""),
      error: message.error ? String(message.error) : null,
      snapshot_id: message.snapshot_id ? String(message.snapshot_id) : null,
    });
    progressStep.value = Math.max(progressStep.value, 3);
    progressMessage.value = "已读取一批 Word 证据，正在对照暗标要求…";
  } catch (error) {
    forwardedToolCalls.delete(callId);
    errorMessage.value = errorText(error, "Word 工具结果回传失败");
  }
}

async function loadResults() {
  if (!taskId.value) return;
  try {
    const result = await getBlindCheckResults(taskId.value);
    findings.value = result.findings;
    if (result.summary) summary.value = result.summary;
    void billingStore.fetchWallet().catch(() => undefined);
  } catch (error) {
    errorMessage.value = errorText(error, "读取检查结果失败");
  }
}

async function cancelCheck() {
  if (!taskId.value) return;
  cancelling.value = true;
  try { await cancelBlindCheckTask(taskId.value); finished.value = true; stopTaskStream(); progressMessage.value = "检查已取消"; } catch (error) { errorMessage.value = errorText(error, "取消检查失败"); } finally { cancelling.value = false; }
}

function locateFinding(item: BlindCheckFinding) {
  const locationQuery = item.location && typeof item.location.query === "string" ? item.location.query : item.evidence_text;
  if (locationQuery) postBridge({ type: "bjt.vsto.locate", tool_session_id: toolSessionId.value, snapshot_id: snapshotId.value, query: locationQuery.slice(0, 500), page_number: item.page_number, paragraph_index: item.paragraph_index });
}

function categoryText(value: string) { return ({ format: "格式", company_identity: "公司身份", person_identity: "人员身份", metadata: "文件属性", other: "其他" } as Record<string, string>)[value] || value || "其他"; }
function severityText(value: string) { return ({ critical: "严重", major: "主要", minor: "一般", info: "提示" } as Record<string, string>)[value] || value || "提示"; }
function verdictText(value: string) { return ({ violation: "疑似违规", compliant: "已符合", unknown: "待确认" } as Record<string, string>)[value] || "待确认"; }
function locationText(item: BlindCheckFinding) { return item.page_number ? `第 ${item.page_number} 页${item.paragraph_index ? ` · 第 ${item.paragraph_index} 段` : ""}（点击定位）` : "点击定位到证据"; }
function errorText(error: unknown, fallback: string) {
  if (error && typeof error === "object" && "response" in error) {
    const response = (error as { response?: { data?: { detail?: string; message?: string } } }).response;
    if (response?.data?.detail || response?.data?.message) return response.data.detail || response.data.message || fallback;
  }
  return error instanceof Error ? error.message : fallback;
}

onMounted(() => {
  void billingStore.fetchWallet().catch(() => undefined);
  const webview = (window as Window & { chrome?: { webview?: { addEventListener: (type: string, listener: (event: MessageEvent) => void) => void } } }).chrome?.webview;
  if (webview) {
    bridgeListener = handleBridgeMessage;
    webview.addEventListener("message", handleBridgeMessage);
    postBridge({ type: "bjt.vsto.ready" });
  } else {
    errorMessage.value = "暗标检查必须从 Word 插件中打开，浏览器页面无法读取当前文档";
  }
});

onUnmounted(() => {
  stopTaskStream();
  if (heartbeatTimer) clearInterval(heartbeatTimer);
  const webview = (window as Window & { chrome?: { webview?: { removeEventListener: (type: string, listener: (event: MessageEvent) => void) => void } } }).chrome?.webview;
  if (webview && bridgeListener) webview.removeEventListener("message", bridgeListener);
  if (toolSessionId.value) void closeVstoToolSession(toolSessionId.value).catch(() => undefined);
});
</script>

<template>
  <main class="blind-check-view">
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

    <section class="blind-card hero-card">
      <div class="section-kicker">WORD · 全文只读检查</div>
      <h1>暗标合规检查</h1>
      <p>检查全文格式、企业与人员身份线索。所有 Word 工具均为只读，不会修改原文。</p>
      <div class="document-state" :class="`state-${bridgeState}`">
        <span class="dot" />
        <span>{{ bridgeStateText }}</span>
        <span v-if="documentName" class="document-name" :title="documentName">{{ documentName }}</span>
      </div>
      <div v-if="errorMessage" class="error" role="alert">{{ errorMessage }}</div>
    </section>

    <section class="blind-card form-card">
      <div class="card-heading">
        <div>
          <span class="heading-index">01</span>
          <h2>填写暗标要求</h2>
        </div>
        <span class="required-tip">必填</span>
      </div>
      <textarea
        id="blind-requirement"
        v-model="requirementText"
        maxlength="50000"
        rows="8"
        aria-label="暗标要求"
        placeholder="粘贴招标文件中关于匿名评审、字体字号、页眉页脚、装订及身份信息的完整要求"
      />
      <div class="scope-box">
        <div class="scope-icon" aria-hidden="true">
          <svg viewBox="0 0 24 24"><path d="M7 3h7l4 4v14H7z"/><path d="M14 3v5h5M10 12h5M10 16h5"/></svg>
        </div>
        <div class="scope-content">
          <div class="scope-title-row">
            <strong>检查整个当前 Word 文档</strong>
            <span>全文范围</span>
          </div>
          <p>提交后将按当前文档快照检查全部正文及可读取的页眉、页脚、对象和文件属性。</p>
          <label class="scope-confirm">
            <input v-model="wholeDocumentConfirmed" type="checkbox">
            <span>我确认对整个当前文档进行暗标检查</span>
          </label>
        </div>
      </div>
      <div class="actions">
        <button class="primary" :disabled="!canSubmit" @click="startCheck">
          <span v-if="submitting" class="button-spinner" aria-hidden="true" />
          {{ submitting ? "正在提交…" : "开始全文暗标检查" }}
        </button>
        <button v-if="running" class="secondary" :disabled="cancelling" @click="cancelCheck">
          {{ cancelling ? "取消中…" : "取消检查" }}
        </button>
      </div>
    </section>

    <section v-if="taskId" class="blind-card timeline-card">
      <div class="card-heading compact">
        <div><span class="heading-index">02</span><h2>检查时间线</h2></div>
        <span class="timeline-status">{{ finished ? "已结束" : "执行中" }}</span>
      </div>
      <div class="review-timeline">
        <div v-for="(step, index) in timelineSteps" :key="step.title" class="timeline-item" :class="`is-${timelineState(index)}`">
          <div class="timeline-rail">
            <span class="timeline-dot">
              <span v-if="timelineState(index) === 'done'">✓</span>
              <span v-else>{{ index + 1 }}</span>
            </span>
          </div>
          <div class="timeline-content">
            <div class="timeline-title-row"><strong>{{ step.title }}</strong><span>{{ timelineState(index) === "done" ? "完成" : timelineState(index) === "active" ? "进行中" : "等待" }}</span></div>
            <p>{{ step.detail }}</p>
          </div>
        </div>
      </div>
      <div class="progress-message"><span class="pulse-dot" />{{ progressMessage }}</div>
    </section>

    <section v-if="taskId && (findings.length || finished)" class="blind-card results-card">
      <div class="result-head">
        <div><span class="heading-index">03</span><h2>检查结果</h2><strong :class="`verdict-${String(summary.overall)}`">{{ overallText }}</strong></div>
        <div class="counts"><span>严重 {{ summary.critical || 0 }}</span><span>主要 {{ summary.major || 0 }}</span><span>一般 {{ summary.minor || 0 }}</span><span>待确认 {{ summary.unknown || 0 }}</span></div>
      </div>
      <div v-if="summary.coverage_complete !== true" class="coverage-warning">{{ coverageText }}。覆盖不完整时，“未发现违规”不等于全文合规。</div>
      <div v-if="errorMessage" class="error">{{ errorMessage }}</div>
      <div class="findings-scroll">
        <div v-if="!findings.length && finished" class="empty">未返回结构化发现，请重新检查。</div>
        <article v-for="item in findings" :key="item.id" class="finding" @click="locateFinding(item)">
          <div class="finding-meta"><b :class="`severity-${item.severity}`">{{ severityText(item.severity) }}</b><span>{{ categoryText(item.category) }}</span><span>{{ verdictText(item.verdict) }}</span></div>
          <h3>{{ item.title }}</h3><p>{{ item.description }}</p>
          <blockquote v-if="item.evidence_text">证据：{{ item.evidence_text }}</blockquote>
          <small>{{ locationText(item) }}</small>
        </article>
      </div>
    </section>
  </main>
</template>

<style scoped>
.blind-check-view{--brand:#d7041a;--brand-deep:#b80015;box-sizing:border-box;width:100%;max-width:760px;min-height:100vh;margin:0 auto;padding:0 12px 36px;background:#f5f5f5;color:#333;overflow-x:hidden;font-family:"Microsoft YaHei","PingFang SC",Arial,sans-serif}.brand-line{height:3px;margin:0 -12px;background:linear-gradient(90deg,var(--brand),var(--brand-deep))}.panel-header{display:flex;align-items:center;justify-content:space-between;gap:12px;min-height:60px;padding:10px 4px}.panel-logo{width:108px;height:auto;object-fit:contain}.account-strip{display:flex;align-items:center;gap:7px;min-width:0}.metric-pill{display:inline-flex;align-items:center;justify-content:flex-end;box-sizing:border-box;height:29px;min-width:88px;padding:0 9px 0 34px;background-position:center;background-repeat:no-repeat;background-size:100% 100%;white-space:nowrap;color:#333;font-size:11px;font-weight:600}.blind-card{box-sizing:border-box;width:100%;margin-bottom:12px;padding:17px;background:#fff;border:1px solid #e8e8e8;border-radius:12px;box-shadow:0 3px 14px rgba(40,28,30,.045)}.hero-card{position:relative;overflow:hidden;border-top:3px solid var(--brand)}.hero-card::after{content:"";position:absolute;right:-34px;top:-52px;width:116px;height:116px;border-radius:50%;background:linear-gradient(135deg,rgba(215,4,26,.12),rgba(215,4,26,0));pointer-events:none}.section-kicker{margin-bottom:5px;color:var(--brand);font-size:10px;font-weight:700;letter-spacing:.12em}.hero-card h1{position:relative;margin:0;color:#171717;font-size:22px;line-height:1.35}.hero-card>p{position:relative;margin:8px 0 13px;color:#777;font-size:12px;line-height:1.65}.document-state{display:flex;min-width:0;align-items:center;gap:7px;padding:8px 10px;border-radius:7px;background:#fafafa;color:#999;font-size:11px}.state-ready{background:#f6ffed;color:#3d9b18}.state-busy{background:#fffbe6;color:#c58608}.dot{width:7px;height:7px;flex:0 0 7px;border-radius:50%;background:currentColor}.document-name{min-width:0;margin-left:auto;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#555}.card-heading{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:12px}.card-heading>div,.result-head>div:first-child{display:flex;align-items:center;gap:8px;min-width:0}.card-heading.compact{margin-bottom:15px}.heading-index{display:inline-flex;align-items:center;justify-content:center;width:26px;height:22px;border-radius:5px;background:#fee7e8;color:var(--brand);font-size:10px;font-weight:700}.card-heading h2,.result-head h2{margin:0;color:#222;font-size:15px}.required-tip,.timeline-status{padding:3px 7px;border-radius:999px;background:#fee7e8;color:var(--brand);font-size:10px}.timeline-status{background:#fff4f4}textarea{box-sizing:border-box;width:100%;min-height:146px;padding:11px 12px;border:1px solid #dedede;border-radius:8px;background:#fff;color:#333;resize:vertical;font:inherit;font-size:12px;line-height:1.65;outline:none;transition:border-color .2s,box-shadow .2s}textarea::placeholder{color:#aaa}textarea:focus{border-color:var(--brand);box-shadow:0 0 0 3px rgba(215,4,26,.08)}.scope-box{display:flex;gap:11px;margin-top:13px;padding:12px;border:1px solid #f1dadd;border-radius:9px;background:#fff8f8}.scope-icon{display:flex;align-items:center;justify-content:center;width:34px;height:34px;flex:0 0 34px;border-radius:8px;background:#fee7e8}.scope-icon svg{width:19px;height:19px;fill:none;stroke:var(--brand);stroke-width:1.6;stroke-linecap:round;stroke-linejoin:round}.scope-content{min-width:0;flex:1}.scope-title-row{display:flex;align-items:center;justify-content:space-between;gap:8px}.scope-title-row strong{color:#333;font-size:12px}.scope-title-row>span{padding:2px 6px;border:1px solid #f4bfc4;border-radius:4px;color:var(--brand);font-size:9px;white-space:nowrap}.scope-content>p{margin:5px 0 9px;color:#888;font-size:10px;line-height:1.55}.scope-confirm{display:flex;align-items:flex-start;gap:7px;color:#555;font-size:11px;line-height:1.45;cursor:pointer}.scope-confirm input{width:14px;height:14px;flex:0 0 14px;margin:1px 0 0;accent-color:var(--brand)}.actions{display:flex;gap:9px;margin-top:14px}.actions button{display:inline-flex;align-items:center;justify-content:center;gap:7px;min-height:38px;border-radius:7px;padding:8px 16px;font:inherit;font-size:12px;font-weight:600;cursor:pointer;transition:filter .2s,transform .1s,opacity .2s}.primary{flex:1;border:0;background:linear-gradient(90deg,var(--brand),var(--brand-deep));color:#fff;box-shadow:0 4px 12px rgba(215,4,26,.2)}.primary:hover:not(:disabled){filter:brightness(1.05)}.primary:active:not(:disabled){transform:scale(.99)}.secondary{border:1px solid #ddd;background:#fff;color:#666}.primary:disabled,.secondary:disabled{opacity:.45;cursor:not-allowed;box-shadow:none}.button-spinner{width:13px;height:13px;border:2px solid rgba(255,255,255,.45);border-top-color:#fff;border-radius:50%;animation:spin .75s linear infinite}.review-timeline{position:relative}.timeline-item{display:flex;gap:10px;min-height:58px}.timeline-rail{position:relative;width:24px;flex:0 0 24px;display:flex;justify-content:center}.timeline-rail::after{content:"";position:absolute;left:11px;top:25px;bottom:0;width:2px;background:#ececec}.timeline-item:last-child .timeline-rail::after{display:none}.timeline-dot{position:relative;z-index:1;display:flex;align-items:center;justify-content:center;width:22px;height:22px;border:1px solid #ddd;border-radius:50%;background:#fafafa;color:#aaa;font-size:9px;font-weight:700}.timeline-content{min-width:0;flex:1;margin-bottom:9px;padding:8px 10px;border:1px solid #eee;border-left:3px solid #ddd;border-radius:7px;background:#fafafa}.timeline-title-row{display:flex;align-items:center;justify-content:space-between;gap:8px}.timeline-title-row strong{font-size:11px;color:#777}.timeline-title-row span{font-size:9px;color:#aaa}.timeline-content p{margin:3px 0 0;color:#aaa;font-size:10px}.is-active .timeline-dot{border-color:var(--brand);background:#fee7e8;color:var(--brand);box-shadow:0 0 0 4px rgba(215,4,26,.07);animation:pulse 1.4s ease-in-out infinite}.is-active .timeline-content{border-color:#f1c9cd;border-left-color:var(--brand);background:#fff8f8}.is-active .timeline-title-row strong,.is-active .timeline-title-row span{color:var(--brand)}.is-done .timeline-dot{border-color:#b7df9f;background:#f6ffed;color:#3d9b18}.is-done .timeline-rail::after{background:#cfe9bf}.is-done .timeline-content{border-left-color:#52c41a;background:#fbfff8}.is-done .timeline-title-row strong{color:#444}.is-done .timeline-title-row span{color:#52a72e}.progress-message{display:flex;align-items:flex-start;gap:7px;margin-top:4px;padding:9px 10px;border-radius:7px;background:#f7f7f7;color:#666;font-size:11px;line-height:1.5}.pulse-dot{width:6px;height:6px;flex:0 0 6px;margin-top:5px;border-radius:50%;background:var(--brand)}.result-head{display:flex;justify-content:space-between;gap:12px}.result-head>div:first-child{flex-wrap:wrap}.result-head strong{flex-basis:100%;padding-left:34px;font-size:12px}.verdict-fail{color:#d7041a}.verdict-pass{color:#389e0d}.verdict-unknown{color:#c58608}.counts{display:flex;gap:5px;flex-wrap:wrap;justify-content:flex-end}.counts span{padding:4px 6px;background:#f5f5f5;border-radius:4px;color:#777;font-size:9px}.coverage-warning{margin-top:12px;padding:9px 10px;border-radius:7px;background:#fffbe6;color:#8b6400;font-size:10px;line-height:1.55}.error{margin-top:11px;padding:9px 10px;border:1px solid #ffccc7;border-radius:7px;background:#fff1f0;color:#c53030;font-size:11px;line-height:1.55;overflow-wrap:anywhere}.empty{padding:22px;text-align:center;color:#999;font-size:11px}.finding{margin-top:11px;padding:11px;border:1px solid #e8e8e8;border-left:3px solid var(--brand);border-radius:8px;cursor:pointer;overflow-wrap:anywhere;transition:border-color .2s,box-shadow .2s}.finding:hover{border-color:#f1515d;box-shadow:0 3px 10px rgba(215,4,26,.08)}.finding-meta{display:flex;gap:7px;align-items:center;flex-wrap:wrap;color:#999;font-size:9px}.finding-meta b{padding:2px 5px;border-radius:4px;font-weight:600}.severity-critical{background:#fff1f0;color:#d7041a}.severity-major{background:#fffbe6;color:#b27600}.severity-minor,.severity-info{background:#f6ffed;color:#3d9b18}.finding h3{margin:7px 0 4px;color:#333;font-size:12px}.finding p{margin:0;color:#666;font-size:11px;line-height:1.55}.finding blockquote{margin:7px 0 0;padding:7px 8px;border-left:2px solid #ddd;background:#fafafa;color:#777;font-size:10px;line-height:1.5}.finding small{display:block;margin-top:7px;color:var(--brand);font-size:9px}@keyframes spin{to{transform:rotate(360deg)}}@keyframes pulse{0%,100%{box-shadow:0 0 0 3px rgba(215,4,26,.07)}50%{box-shadow:0 0 0 6px rgba(215,4,26,.03)}}
@media (max-width:560px){.blind-check-view{padding:0 8px 28px}.brand-line{margin:0 -8px}.panel-header{min-height:56px}.panel-logo{width:96px}.metric-pill{height:27px;min-width:80px;padding-left:30px;font-size:10px}.blind-card{padding:14px 12px;margin-bottom:9px;border-radius:9px}.hero-card h1{font-size:19px}.document-state{align-items:flex-start;flex-wrap:wrap}.document-name{flex-basis:100%;margin-left:14px}.scope-title-row{align-items:flex-start}.actions{flex-direction:column}.actions button{width:100%}.result-head{flex-direction:column}.counts{justify-content:flex-start}.finding{padding:10px}}
@media (max-width:390px){.panel-header{align-items:flex-start;flex-direction:column;padding:10px 3px}.account-strip{width:100%}.metric-pill{flex:1}.scope-icon{display:none}.result-head strong{padding-left:0}.timeline-content{padding:7px 8px}}
.findings-scroll{max-height:60vh;overflow-y:auto}
</style>
