<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { Tag } from "ant-design-vue";
import {
  CheckOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  FileSearchOutlined,
  LoadingOutlined,
} from "@ant-design/icons-vue";
import {
  bidDraftStreamUrl,
  bidDraftToken,
  cancelBidDraftTask,
  createBidDraftTask,
  getBidDraftAssembled,
  getBidDraftSectionContent,
  getBidDraftTask,
  listBidDraftSections,
  regenerateBidDraftSection,
  type BidDraftSectionContent,
  type BidDraftSectionMeta,
  type BidDraftTask,
} from "@/api/bidDraft";
import { documentsApi, projectsApi } from "@/api/client";
import type { Document, Project } from "@/types";
import { useVstoBridge } from "@/composables/useVstoBridge";
import { prepareChartAssets, splitMermaidFences } from "@/utils/chartAssets";
import MermaidFigure from "@/components/MermaidFigure.vue";
import { useBillingStore } from "@/stores/billing";
import logoUrl from "@/assets/images/ui/common-logo-black.png";
import iconWallet from "@/assets/images/ui/common-icon-wallet.png";
import iconPoints from "@/assets/images/ui/common-icon-points.png";

const billingStore = useBillingStore();
const bridge = useVstoBridge();

const timelineSteps = [
  { title: "解析招标要素", detail: "提取招标需求、评分标准与废标项" },
  { title: "生成章节大纲", detail: "按评分办法设计投标文件结构" },
  { title: "逐节撰写标书", detail: "逐章生成正文，可单节重生成" },
  { title: "汇总并写入 Word", detail: "一键插入全文，Ctrl+Z 可撤销" },
];
const PHASE_INDEX: Record<string, number> = {
  tender_analysis: 1,
  outline: 2,
  generating: 3,
  assembling: 4,
  assembled: 4,
};

const step = ref<"setup" | "running" | "done">("setup");
const projects = ref<Project[]>([]);
const loadingProjects = ref(false);
const projectId = ref("");
const newProjectName = ref("");
const creatingProject = ref(false);

const tenderDoc = ref<Document | null>(null);
const uploading = ref(false);
const uploadPercent = ref(0);
const docErrorMessage = ref("");
let docPollTimer: ReturnType<typeof setInterval> | null = null;

const taskId = ref("");
const task = ref<BidDraftTask | null>(null);
const sections = ref<BidDraftSectionMeta[]>([]);
const progressStep = ref(0);
const progressMessage = ref("");
const submitting = ref(false);
const cancelling = ref(false);
const terminalStatus = ref("");
const taskErrorMessage = ref("");

const assembledContent = ref("");
const inserting = ref(false);
const inserted = ref(false);
const staleSnapshot = ref(false);
const insertMessage = ref("");
const insertError = ref("");
const renderingCharts = ref(false);
const chartFailures = ref(0);

const selectedSection = ref<BidDraftSectionContent | null>(null);
const regeneratingNode = ref("");

// 章节预览：按 ```mermaid 围栏拆分，图表内联渲染，其余保持原文本展示。
const sectionSegments = computed(() =>
  selectedSection.value?.content ? splitMermaidFences(selectedSection.value.content) : [],
);

// 详细时间线步骤流：SSE 事件逐条记录，内部/外部用户均完整显示
// （产品要求：标书生成不做 review 那类按 interior_user 的详情过滤）。
type DetailKind = "phase" | "section" | "info" | "error" | "analysis";
interface DetailStep {
  time: string;
  text: string;
  kind: DetailKind;
  nodeId?: string;
  progress?: string;
}
const detailSteps = ref<DetailStep[]>([]);

function pushStep(kind: DetailKind, text: string, extra?: { nodeId?: string; progress?: string }) {
  const time = new Date().toLocaleTimeString("zh-CN", { hour12: false });
  detailSteps.value.push({ time, text, kind, ...extra });
  if (detailSteps.value.length > 300) detailSteps.value.splice(0, detailSteps.value.length - 300);
}

/** 章节步骤卡上附带的实时进度，如 "12/45 完成"。 */
function sectionProgressText(): string {
  return `${sections.value.filter(s => s.status === "generated").length}/${sections.value.length} 完成`;
}

// 章节标题若以数字编号开头（"1.1 投标函"），node_id 已携带编号，渲染时剥掉避免重复。
function displayTitle(nodeId: string, title: string | null | undefined): string {
  const raw = String(title || "").trim();
  const stripped = raw.replace(/^\d+(?:\.\d+)*(?:[\s、:：\-—]+|\.\s+)/, "").trim();
  return stripped || raw || nodeId;
}

function nodeSortKey(nodeId: string): number[] {
  return String(nodeId || "")
    .split(".")
    .map((chunk) => {
      const value = Number.parseInt(chunk, 10);
      return Number.isFinite(value) ? value : Number.MAX_SAFE_INTEGER;
    });
}

// 本地按自然序排章节（"1" < "1.1" < "2" < "10"），不依赖接口返回顺序。
const sortedSections = computed(() =>
  [...sections.value].sort((a, b) => {
    const ka = nodeSortKey(a.node_id);
    const kb = nodeSortKey(b.node_id);
    for (let i = 0; i < Math.max(ka.length, kb.length); i += 1) {
      const diff = (ka[i] ?? 0) - (kb[i] ?? 0);
      if (diff) return diff;
    }
    return 0;
  }),
);

// 详细步骤时间线（同标书检查页 ReviewTimeline 的节点卡片样式）
const DETAIL_KIND_LABELS: Record<DetailKind, string> = {
  phase: "阶段",
  section: "章节",
  info: "信息",
  error: "错误",
  analysis: "解析",
};
const DETAIL_KIND_TAG_COLORS: Record<DetailKind, string> = {
  phase: "blue",
  section: "green",
  info: "default",
  error: "red",
  analysis: "orange",
};

function isLiveStep(index: number): boolean {
  return step.value === "running" && index === detailSteps.value.length - 1 && detailSteps.value[index]?.kind !== "error";
}

function stepIcon(item: DetailStep, index: number) {
  if (item.kind === "error") return CloseCircleOutlined;
  if (item.kind === "analysis") return FileSearchOutlined;
  if (isLiveStep(index)) return LoadingOutlined;
  if (item.kind === "info") return ClockCircleOutlined;
  return CheckOutlined;
}

const detailScrollRef = ref<HTMLElement | null>(null);
watch(
  () => detailSteps.value.length,
  async () => {
    await nextTick();
    const el = detailScrollRef.value;
    if (el) el.scrollTop = el.scrollHeight;
  },
);

/** 解析结果作为时间线内的一个节点展示（与检查页体验一致），不另开独立卡片。 */
function pushAnalysisStep() {
  if (detailSteps.value.some((item) => item.kind === "analysis")) return;
  pushStep("analysis", "招标要素解析完成");
}

// 生成中：任务对象带上 analysis_result（解析阶段完成、大纲阶段开始时落库）即补一个解析节点。
watch(
  () => task.value?.analysis_result,
  (value) => {
    if (value && step.value === "running") pushAnalysisStep();
  },
);

function phaseText(phase: string): string {
  return ({
    tender_analysis: "阶段：解析招标要素（基本信息 / 招标需求 / 评分标准 / 废标项）",
    outline: "阶段：生成章节大纲",
    generating: "阶段：逐节撰写标书",
    assembling: "阶段：汇总生成结果",
    assembled: "汇总完成",
  } as Record<string, string>)[phase] || `阶段：${phase}`;
}

/** SSE 中途接入/断线时，从章节终态补一条步骤记录（刷新页面也能看到明细）。 */
function backfillStepsFromSections() {
  if (detailSteps.value.length || !sections.value.length) return;
  if (analysis.value) pushAnalysisStep();
  let generatedIndex = 0;
  for (const node of sortedSections.value) {
    if (node.status === "generated") {
      generatedIndex += 1;
      pushStep(
        "section",
        `完成章节 ${node.node_id} ${displayTitle(node.node_id, node.title)}${node.word_count ? `（${formatMetric(node.word_count)} 字）` : ""}`,
        { nodeId: node.node_id, progress: `${generatedIndex}/${sections.value.length} 完成` },
      );
    } else if (node.status === "failed") {
      pushStep("error", `章节失败 ${node.node_id} ${displayTitle(node.node_id, node.title)}${node.error_message ? `：${node.error_message}` : ""}`, { nodeId: node.node_id });
    }
  }
}

let streamController: AbortController | null = null;
let taskPollTimer: ReturnType<typeof setInterval> | null = null;

const canStart = computed(() =>
  Boolean(projectId.value && tenderDoc.value?.status === "parsed" && !submitting.value && step.value === "setup"),
);
const bridgeStateText = computed(() =>
  bridge.contextReady.value ? "已连接 Word 文档" : bridge.available.value ? "正在连接 Word 文档" : "未检测到 Word 插件（生成后可复制全文）",
);
const analysis = computed(() => {
  const value = task.value?.analysis_result;
  return value && typeof value === "object" ? value as Record<string, unknown> : null;
});
const summary = computed(() => {
  const value = task.value?.summary;
  return value && typeof value === "object" ? value as Record<string, number> : null;
});
const isRegenTask = computed(() => Boolean(task.value?.continue_of));

function formatMetric(value: number | null | undefined) {
  return new Intl.NumberFormat("zh-CN").format(Math.round(Number(value || 0)));
}

function friendlyError(error: unknown, fallback: string): string {
  if (error && typeof error === "object" && "response" in error) {
    const response = (error as {
      response?: { status?: number; data?: { detail?: unknown } };
    }).response;
    const detail = response?.data?.detail;
    if (response?.status === 402) {
      return typeof detail === "object" && detail && "message" in detail
        ? String((detail as { message?: string }).message)
        : "余额不足，请先充值后再使用 AI 标书生成";
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

// ------------------------------------------------------------- 项目与招标文件

async function loadProjects() {
  loadingProjects.value = true;
  try {
    const all = await projectsApi.list();
    projects.value = all.filter((item) => item.project_type === "bid_draft");
  } catch (error) {
    docErrorMessage.value = friendlyError(error, "项目列表加载失败");
  } finally {
    loadingProjects.value = false;
  }
}

async function createProject() {
  const name = newProjectName.value.trim();
  if (!name) return;
  creatingProject.value = true;
  try {
    const project = await projectsApi.create({ name, project_type: "bid_draft" });
    projects.value = [project, ...projects.value];
    projectId.value = project.id;
    newProjectName.value = "";
    await loadTenderDoc();
  } catch (error) {
    docErrorMessage.value = friendlyError(error, "新建项目失败");
  } finally {
    creatingProject.value = false;
  }
}

async function onProjectChange() {
  stopDocPoll();
  tenderDoc.value = null;
  docErrorMessage.value = "";
  if (projectId.value) await loadTenderDoc();
}

async function loadTenderDoc() {
  if (!projectId.value) return;
  try {
    const docs = await documentsApi.list(projectId.value);
    const tender = docs.find((item) => item.doc_type === "tender") || null;
    tenderDoc.value = tender;
    if (tender && (tender.status === "pending" || tender.status === "parsing")) {
      startDocPoll();
    }
  } catch (error) {
    docErrorMessage.value = friendlyError(error, "读取项目文件失败");
  }
}

function stopDocPoll() {
  if (docPollTimer) {
    clearInterval(docPollTimer);
    docPollTimer = null;
  }
}

function startDocPoll() {
  stopDocPoll();
  if (!projectId.value || !tenderDoc.value) return;
  const documentId = tenderDoc.value.id;
  docPollTimer = setInterval(async () => {
    try {
      const updated = await documentsApi.get(projectId.value, documentId);
      tenderDoc.value = updated;
      if (updated.status === "parsed" || updated.status === "failed") stopDocPoll();
    } catch {
      /* keep polling; transient errors are tolerable */
    }
  }, 2_000);
}

async function uploadTender(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = "";
  if (!file || !projectId.value) return;
  uploading.value = true;
  uploadPercent.value = 0;
  docErrorMessage.value = "";
  try {
    const doc = await documentsApi.upload(projectId.value, "tender", file, (progress) => {
      uploadPercent.value = progress.percent;
    });
    tenderDoc.value = doc;
    startDocPoll();
  } catch (error) {
    docErrorMessage.value = friendlyError(error, "招标文件上传失败");
  } finally {
    uploading.value = false;
  }
}

async function removeTender() {
  if (!projectId.value || !tenderDoc.value) return;
  const documentId = tenderDoc.value.id;
  try {
    await documentsApi.delete(projectId.value, documentId);
    tenderDoc.value = null;
  } catch (error) {
    docErrorMessage.value = friendlyError(error, "删除招标文件失败");
  }
}

// ------------------------------------------------------------------- 生成任务

function upsertSection(meta: Partial<BidDraftSectionMeta> & { node_id: string }) {
  const index = sections.value.findIndex((item) => item.node_id === meta.node_id);
  if (index >= 0) {
    sections.value[index] = { ...sections.value[index], ...meta } as BidDraftSectionMeta;
  } else {
    sections.value.push({
      node_id: meta.node_id,
      title: String(meta.title || meta.node_id),
      status: String(meta.status || "pending"),
      word_count: meta.word_count ?? null,
      attempts: meta.attempts ?? 0,
      error_message: meta.error_message ?? null,
    });
  }
}

function timelineState(index: number): "done" | "active" | "waiting" {
  const target = index + 1;
  if (progressStep.value > target || (step.value === "done" && progressStep.value >= target)) return "done";
  if (step.value !== "done" && progressStep.value === target) return "active";
  return "waiting";
}

async function startGeneration() {
  if (!canStart.value || !tenderDoc.value) return;
  submitting.value = true;
  taskErrorMessage.value = "";
  try {
    const created = await createBidDraftTask({
      project_id: projectId.value,
      tender_document_id: tenderDoc.value.id,
    });
    trackTask(created);
  } catch (error) {
    taskErrorMessage.value = friendlyError(error, "创建标书生成任务失败");
  } finally {
    submitting.value = false;
  }
}

function trackTask(next: BidDraftTask) {
  stopTaskStream();
  stopTaskPoll();
  taskId.value = next.id;
  task.value = next;
  sections.value = [];
  progressStep.value = 1;
  progressMessage.value = "任务已提交，等待智能体开始…";
  terminalStatus.value = "";
  taskErrorMessage.value = "";
  assembledContent.value = "";
  inserted.value = false;
  staleSnapshot.value = false;
  insertMessage.value = "";
  insertError.value = "";
  renderingCharts.value = false;
  chartFailures.value = 0;
  step.value = "running";
  detailSteps.value = [];
  pushStep("info", isRegenTask.value ? "单节重生成任务已提交" : "标书生成任务已提交");
  void listenTask(next.id);
  startTaskPoll(next.id);
}

function stopTaskStream() {
  if (streamController) {
    streamController.abort();
    streamController = null;
  }
}

function stopTaskPoll() {
  if (taskPollTimer) {
    clearInterval(taskPollTimer);
    taskPollTimer = null;
  }
}

async function listenTask(id: string) {
  const headers: HeadersInit = { Accept: "text/event-stream" };
  const token = bidDraftToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  stopTaskStream();
  const controller = new AbortController();
  streamController = controller;
  try {
    const response = await fetch(bidDraftStreamUrl(id), { headers, signal: controller.signal });
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
        try {
          handleTaskEvent(JSON.parse(line.slice(5).trim()) as Record<string, unknown>);
        } catch {
          /* ignore replay noise */
        }
      });
    }
  } catch (error) {
    if (!(error instanceof DOMException && error.name === "AbortError") && step.value === "running") {
      // 轮询兜底仍在跑，这里只提示弱化
      progressMessage.value = "进度连接中断，正在通过轮询获取状态…";
    }
  } finally {
    if (streamController === controller) streamController = null;
  }
}

function handleTaskEvent(event: Record<string, unknown>) {
  const type = String(event.type || "");
  if (type === "status") {
    const status = String(event.status || "");
    if (status === "running") progressMessage.value = "智能体正在处理…";
    if (status === "completed" || status === "failed" || status === "cancelled") void finishTask();
  } else if (type === "phase") {
    const phase = String(event.phase || "");
    if (PHASE_INDEX[phase]) progressStep.value = Math.max(progressStep.value, PHASE_INDEX[phase]);
    pushStep("phase", phaseText(phase));
    const messages: Record<string, string> = {
      tender_analysis: "正在解析招标文件要素…",
      outline: "正在生成章节大纲…",
      generating: `正在逐节撰写标书（共 ${event.section_total || sections.value.length || "?"} 节）…`,
      assembling: "正在汇总生成结果…",
      assembled: "汇总完成",
    };
    if (messages[phase]) progressMessage.value = messages[phase];
  } else if (type === "section_started") {
    progressStep.value = Math.max(progressStep.value, 3);
    const nodeId = String(event.node_id || "");
    const title = displayTitle(nodeId, String(event.title || event.node_id || ""));
    upsertSection({
      node_id: nodeId,
      title: String(event.title || nodeId),
      status: "generating",
    });
    pushStep("section", `开始撰写章节 ${nodeId} ${title}`, { nodeId, progress: sectionProgressText() });
    progressMessage.value = `正在撰写：${title}`;
  } else if (type === "section_completed") {
    const nodeId = String(event.node_id || "");
    upsertSection({
      node_id: nodeId,
      title: String(event.title || ""),
      status: "generated",
      word_count: Number(event.word_count || 0),
    });
    pushStep(
      "section",
      `完成章节 ${nodeId} ${displayTitle(nodeId, String(event.title || ""))}（${formatMetric(Number(event.word_count || 0))} 字）`,
      { nodeId, progress: sectionProgressText() },
    );
  } else if (type === "section_failed") {
    const nodeId = String(event.node_id || "");
    pushStep("error", `章节失败 ${nodeId}：${String(event.error || "生成失败")}`, { nodeId });
    upsertSection({
      node_id: String(event.node_id || ""),
      status: "failed",
      error_message: String(event.error || "生成失败"),
    });
  } else if (type === "result") {
    progressStep.value = 4;
    pushStep("info", "标书生成完成");
    void finishTask();
  } else if (type === "error") {
    taskErrorMessage.value = String(event.message || "任务失败");
    pushStep("error", String(event.message || "任务失败"));
    void finishTask();
  }
}

function startTaskPoll(id: string) {
  stopTaskPoll();
  taskPollTimer = setInterval(async () => {
    if (step.value !== "running") {
      stopTaskPoll();
      return;
    }
    try {
      const latest = await getBidDraftTask(id);
      task.value = latest;
      if (latest.outline && Array.isArray(latest.outline) && latest.outline.length) {
        progressStep.value = Math.max(progressStep.value, 3);
      }
      const metas = await listBidDraftSections(id);
      metas.forEach((meta) => upsertSection(meta));
      if (latest.status === "running" && latest.phase && PHASE_INDEX[latest.phase]) {
        progressStep.value = Math.max(progressStep.value, PHASE_INDEX[latest.phase]);
      }
      if (["completed", "failed", "cancelled"].includes(latest.status)) {
        await finishTask();
      }
    } catch {
      /* transient polling errors are tolerable */
    }
  }, 3_000);
}

async function finishTask() {
  if (step.value === "done") return;
  stopTaskStream();
  stopTaskPoll();
  step.value = "done";
  progressStep.value = 4;
  try {
    const latest = await getBidDraftTask(taskId.value);
    task.value = latest;
    terminalStatus.value = latest.status;
    if (latest.status === "failed") {
      taskErrorMessage.value = latest.error_message || "标书生成失败，请重试";
    } else if (latest.status === "cancelled") {
      taskErrorMessage.value = latest.error_message || "任务已取消";
    } else if (latest.status === "completed") {
      progressMessage.value = "标书生成完成";
    }
    sections.value = await listBidDraftSections(taskId.value);
    // SSE 未覆盖（页面重开/断线）时，从章节终态回填详细步骤，保证历史任务也有明细可看
    backfillStepsFromSections();
  } catch (error) {
    taskErrorMessage.value = friendlyError(error, "读取任务结果失败");
  }
  void billingStore.fetchWallet().catch(() => undefined);
  if (task.value?.status === "completed") void autoInsert();
}

async function autoInsert() {
  if (inserted.value || inserting.value) return;
  try {
    const assembled = await getBidDraftAssembled(taskId.value);
    if (!assembled.content) {
      insertError.value = "没有成功生成的章节，可对失败章节单独重生成";
      return;
    }
    assembledContent.value = assembled.content;
    await insertToWord(false);
  } catch (error) {
    insertError.value = friendlyError(error, "读取生成结果失败");
  }
}

async function insertToWord(atCurrentCursor: boolean) {
  if (!assembledContent.value) {
    const assembled = await getBidDraftAssembled(taskId.value);
    assembledContent.value = assembled.content || "";
  }
  if (!assembledContent.value) {
    insertError.value = "没有可写入的内容";
    return;
  }
  inserting.value = true;
  insertError.value = "";
  insertMessage.value = "";
  chartFailures.value = 0;
  try {
    let content = assembledContent.value;
    let images: Record<string, string> | undefined;
    if (content.includes("```mermaid")) {
      // mermaid 图在 WebView2 内渲染为 PNG 随桥消息发给插件（服务端无渲染环境）
      renderingCharts.value = true;
      try {
        const payload = await prepareChartAssets(content);
        content = payload.content;
        images = payload.images;
        chartFailures.value = payload.failedCount;
      } finally {
        renderingCharts.value = false;
      }
    }
    const result = await bridge.insertMarkdown(content, {
      label: isRegenTask.value ? "AI 标书生成（单节）" : "AI 标书生成",
      snapshotId: atCurrentCursor ? null : bridge.documentContext.value?.snapshot_id || null,
      anchor: "cursor",
      // 全量标书逐标题写入 Word 实测约 4s/标题、60 节 ≈ 6-7 分钟（2026-08-25
      // 真机日志：20:12:55→20:19:17），60s 默认超时会在写完前误报"插件未响应"。
      timeoutMs: 15 * 60_000,
      images,
    });
    if (result.success === true) {
      inserted.value = true;
      staleSnapshot.value = false;
      insertMessage.value = "已写入 Word 当前光标处，可按 Ctrl+Z 一次撤销本次生成内容";
      if (chartFailures.value > 0) {
        insertMessage.value += `；${chartFailures.value} 张图表渲染失败，已按代码文本插入`;
      }
    } else if (String(result.code || "") === "snapshot_stale") {
      staleSnapshot.value = true;
      insertError.value = "Word 文档在生成期间已修改，请点击「重新定位插入点并写入」";
    } else {
      insertError.value = String(result.error || "写入 Word 失败");
    }
  } catch (error) {
    insertError.value = friendlyError(error, "写入 Word 失败，请从 Word 插件中打开本页");
  } finally {
    inserting.value = false;
  }
}

async function copyAll() {
  if (!assembledContent.value) {
    const assembled = await getBidDraftAssembled(taskId.value);
    assembledContent.value = assembled.content || "";
  }
  if (!assembledContent.value) return;
  try {
    await navigator.clipboard.writeText(assembledContent.value);
    insertMessage.value = "全文已复制到剪贴板";
  } catch {
    insertError.value = "复制失败，请打开章节内容手动复制";
  }
}

async function openSection(node: BidDraftSectionMeta) {
  try {
    selectedSection.value = await getBidDraftSectionContent(taskId.value, node.node_id);
  } catch (error) {
    taskErrorMessage.value = friendlyError(error, "读取章节内容失败");
  }
}

/** 时间线上的章节步骤卡可点击查看该节全文。 */
function openSectionByNodeId(nodeId: string) {
  const node = sections.value.find(item => item.node_id === nodeId);
  if (node) void openSection(node);
}

function regenerateSelectedSection() {
  const nodeId = selectedSection.value?.node_id;
  if (!nodeId) return;
  const node = sections.value.find(item => item.node_id === nodeId);
  if (!node) return;
  selectedSection.value = null;
  void regenerateSection(node);
}

async function regenerateSection(node: BidDraftSectionMeta) {
  if (regeneratingNode.value) return;
  regeneratingNode.value = node.node_id;
  taskErrorMessage.value = "";
  try {
    const next = await regenerateBidDraftSection(taskId.value, node.node_id);
    trackTask(next);
  } catch (error) {
    taskErrorMessage.value = friendlyError(error, "创建单节重生成任务失败");
  } finally {
    regeneratingNode.value = "";
  }
}

async function cancelTask() {
  if (!taskId.value || step.value !== "running") return;
  cancelling.value = true;
  try {
    await cancelBidDraftTask(taskId.value);
    await finishTask();
  } catch (error) {
    taskErrorMessage.value = friendlyError(error, "取消任务失败");
  } finally {
    cancelling.value = false;
  }
}

function restart() {
  step.value = "setup";
  taskId.value = "";
  task.value = null;
  sections.value = [];
  progressStep.value = 0;
  progressMessage.value = "";
  terminalStatus.value = "";
  taskErrorMessage.value = "";
  assembledContent.value = "";
  inserted.value = false;
  staleSnapshot.value = false;
  insertMessage.value = "";
  insertError.value = "";
  renderingCharts.value = false;
  chartFailures.value = 0;
}

onMounted(() => {
  void billingStore.fetchWallet().catch(() => undefined);
  void loadProjects();
});

onUnmounted(() => {
  stopTaskStream();
  stopTaskPoll();
  stopDocPoll();
});
</script>

<template>
  <main class="bid-draft-view">
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
      <div class="section-kicker">WORD · 招标解析 → 标书生成</div>
      <h1>AI 标书生成</h1>
      <p>上传招标文件，智能体解析要素、生成章节大纲并逐节撰写投标文件；完成后自动写入 Word，可按 Ctrl+Z 一次撤销。</p>
      <div class="document-state" :class="{ ok: bridge.contextReady.value }">
        <span class="dot" />
        <span>{{ bridgeStateText }}</span>
        <span v-if="bridge.documentContext.value?.document_name" class="document-name" :title="bridge.documentContext.value.document_name">
          {{ bridge.documentContext.value.document_name }}
        </span>
      </div>
    </section>

    <template v-if="step === 'setup'">
      <section class="card">
        <div class="card-heading">
          <div><span class="heading-index">01</span><h2>选择项目</h2></div>
          <span v-if="loadingProjects" class="timeline-status">加载中…</span>
        </div>
        <select v-model="projectId" class="line-input" aria-label="选择项目" @change="onProjectChange">
          <option value="" disabled>请选择项目（用于保存招标文件与生成结果）</option>
          <option v-for="item in projects" :key="item.id" :value="item.id">{{ item.name }}</option>
        </select>
        <div class="new-project">
          <input
            v-model="newProjectName"
            class="line-input"
            maxlength="100"
            placeholder="或输入新项目名称直接创建"
            @keyup.enter="createProject"
          >
          <button class="ghost-btn" :disabled="!newProjectName.trim() || creatingProject" @click="createProject">
            {{ creatingProject ? "创建中…" : "新建项目" }}
          </button>
        </div>
      </section>

      <section class="card">
        <div class="card-heading">
          <div><span class="heading-index">02</span><h2>上传招标文件</h2></div>
          <span class="timeline-status">PDF / Word</span>
        </div>
        <div v-if="tenderDoc" class="doc-box">
          <div class="doc-row">
            <strong class="doc-name" :title="tenderDoc.original_filename">{{ tenderDoc.original_filename }}</strong>
            <span class="doc-status" :class="`doc-${tenderDoc.status}`">{{ tenderDoc.status === "parsed" ? "解析完成" : tenderDoc.status === "failed" ? "解析失败" : "解析中…" }}</span>
          </div>
          <p v-if="tenderDoc.status === 'parsed'" class="doc-meta">
            <template v-if="tenderDoc.page_count">{{ formatMetric(tenderDoc.page_count) }} 页 · </template>{{ formatMetric(tenderDoc.word_count) }} 字
          </p>
          <div class="doc-actions">
            <button class="ghost-btn danger" @click="removeTender">删除重传</button>
          </div>
        </div>
        <label v-else class="upload-area" :class="{ busy: uploading }">
          <input type="file" accept=".pdf,.docx,.doc" :disabled="!projectId || uploading" @change="uploadTender">
          <span v-if="!projectId">请先选择项目</span>
          <span v-else-if="uploading">上传中 {{ uploadPercent }}%</span>
          <span v-else>点击选择招标文件（.pdf / .docx / .doc）</span>
        </label>
        <div v-if="docErrorMessage" class="error">{{ docErrorMessage }}</div>
        <div v-if="taskErrorMessage" class="error">{{ taskErrorMessage }}</div>
        <div class="actions">
          <button class="primary" :disabled="!canStart" @click="startGeneration">
            {{ submitting ? "正在创建任务…" : "开始生成标书" }}
          </button>
        </div>
        <p class="hint">生成按 AI 用量计费；任务结束（含失败/取消）后结算。</p>
      </section>
    </template>

    <template v-else>
      <section class="card">
        <div class="card-heading compact">
          <div><span class="heading-index">03</span><h2>生成进度</h2></div>
          <span class="timeline-status">{{ isRegenTask ? "单节重生成" : step === "done" ? "已结束" : "执行中" }}</span>
        </div>
        <div class="phase-stepper" aria-label="生成阶段">
          <template v-for="(item, index) in timelineSteps" :key="item.title">
            <div class="phase-node" :class="`is-${timelineState(index)}`">
              <span class="phase-dot"><span v-if="timelineState(index) === 'done'">✓</span><span v-else>{{ index + 1 }}</span></span>
              <span class="phase-label">{{ item.title }}</span>
              <span class="phase-state">{{ timelineState(index) === "done" ? "完成" : timelineState(index) === "active" ? "进行中" : "等待" }}</span>
            </div>
            <span v-if="index < timelineSteps.length - 1" class="phase-bar" :class="{ passed: progressStep >= index + 2 }" />
          </template>
        </div>
        <div class="progress-message">
          <span v-if="step === 'running'" class="pulse-dot" />{{ progressMessage || "等待开始…" }}
        </div>
        <template v-if="detailSteps.length || step === 'running'">
          <p class="detail-caption">智能体执行明细</p>
          <div class="detail-scroll" aria-label="详细执行步骤" ref="detailScrollRef">
          <a-timeline>
            <a-timeline-item v-for="(item, index) in detailSteps" :key="index">
              <template #dot>
                <component
                  :is="stepIcon(item, index)"
                  :class="['step-icon', `icon-${item.kind}`, { 'icon-live spin-icon': isLiveStep(index) }]"
                />
              </template>
              <div
                class="step-card"
                :class="[`card-${item.kind}`, { 'is-clickable': item.kind === 'section' && item.nodeId }]"
                @click="item.kind === 'section' && item.nodeId && openSectionByNodeId(item.nodeId)"
              >
                <div class="step-card-header">
                  <Tag class="step-tag" :color="DETAIL_KIND_TAG_COLORS[item.kind]">{{ DETAIL_KIND_LABELS[item.kind] }}</Tag>
                  <span class="step-ts">{{ item.time }}</span>
                </div>
                <div class="step-card-text">{{ item.text }}<span v-if="item.progress" class="step-progress">{{ item.progress }}</span></div>
                <div v-if="item.kind === 'analysis' && analysis" class="analysis-detail">
                  <details>
                    <summary>招标要素解析结果（基本信息 / 招标需求 / 评分标准 / 废标项）</summary>
                    <div class="analysis-block"><pre>{{ JSON.stringify(analysis, null, 2) }}</pre></div>
                  </details>
                </div>
              </div>
            </a-timeline-item>
            <a-timeline-item v-if="!detailSteps.length">
              <template #dot>
                <ClockCircleOutlined class="step-icon icon-info" />
              </template>
              <div class="step-card card-info">
                <div class="step-card-text">等待智能体启动…</div>
              </div>
            </a-timeline-item>
          </a-timeline>
          </div>
        </template>
        <div v-if="taskErrorMessage" class="error">{{ taskErrorMessage }}</div>
        <div v-if="step === 'running'" class="actions">
          <button class="secondary" :disabled="cancelling" @click="cancelTask">{{ cancelling ? "取消中…" : "取消任务" }}</button>
        </div>
        <div v-if="step === 'done' && terminalStatus !== 'completed'" class="actions">
          <button class="primary" @click="restart">重新生成</button>
        </div>
      </section>

      <section v-if="step === 'done' && terminalStatus === 'completed'" class="card">
        <div class="card-heading compact">
          <div><span class="heading-index">04</span><h2>写入 Word</h2></div>
          <span v-if="summary" class="timeline-status">
            {{ summary.section_generated ?? sections.filter(s => s.status === "generated").length }} 节 · {{ formatMetric(summary.word_count) }} 字
          </span>
        </div>
        <div v-if="insertMessage" class="applied-tip">{{ insertMessage }}</div>
        <div v-if="insertError" class="error">{{ insertError }}</div>
        <p v-if="inserting" class="hint inserting-hint">
          <template v-if="renderingCharts">正在渲染图表（组织架构/流程/进度等图示）…</template>
          <template v-else>正在逐标题写入 Word，大文档约需数分钟，请勿在写入期间点击/编辑 Word 窗口（会导致"应用程序正在使用中"而中断）…</template>
        </p>
        <div class="actions">
          <button class="primary" :disabled="inserting || inserted" @click="insertToWord(staleSnapshot)">
            <template v-if="inserting">写入中…</template>
            <template v-else-if="inserted">已写入</template>
            <template v-else-if="staleSnapshot">重新定位插入点并写入</template>
            <template v-else>写入 Word</template>
          </button>
          <button class="secondary" :disabled="inserting" @click="copyAll">复制全文</button>
        </div>
        <p class="hint">写入为一次整体插入，按一次 Ctrl+Z 即可全部撤销。</p>
      </section>
    </template>

    <div v-if="selectedSection" class="modal-mask" @click.self="selectedSection = null">
      <div class="modal-card">
        <div class="modal-head">
          <strong>{{ selectedSection.node_id }} {{ displayTitle(selectedSection.node_id, selectedSection.title) }}</strong>
          <div class="modal-actions">
            <button
              v-if="step === 'done' && terminalStatus === 'completed'"
              class="mini-btn"
              :disabled="regeneratingNode === selectedSection.node_id"
              @click="regenerateSelectedSection"
            >重生成</button>
            <button class="ghost-btn" @click="selectedSection = null">关闭</button>
          </div>
        </div>
        <div class="modal-body">
          <template v-for="(segment, index) in sectionSegments" :key="index">
            <pre v-if="segment.kind === 'text' && segment.content.trim()" class="modal-pre">{{ segment.content }}</pre>
            <MermaidFigure v-else-if="segment.kind === 'mermaid'" :code="segment.content" />
          </template>
          <p v-if="!sectionSegments.length" class="modal-empty">（暂无内容）</p>
        </div>
      </div>
    </div>
  </main>
</template>

<style scoped>
.bid-draft-view{--brand:#d7041a;--brand-deep:#b80015;box-sizing:border-box;width:100%;max-width:760px;min-height:100vh;margin:0 auto;padding:0 12px 36px;background:#f5f5f5;color:#333;overflow-x:hidden;font-family:"Microsoft YaHei","PingFang SC",Arial,sans-serif}.brand-line{height:3px;margin:0 -12px;background:linear-gradient(90deg,var(--brand),var(--brand-deep))}.panel-header{display:flex;align-items:center;justify-content:space-between;gap:12px;min-height:60px;padding:10px 4px}.panel-logo{width:108px;height:auto;object-fit:contain}.account-strip{display:flex;align-items:center;gap:7px;min-width:0}.metric-pill{display:inline-flex;align-items:center;justify-content:flex-end;box-sizing:border-box;height:29px;min-width:88px;padding:0 9px 0 34px;background-position:center;background-repeat:no-repeat;background-size:100% 100%;white-space:nowrap;color:#333;font-size:11px;font-weight:600}.card{box-sizing:border-box;width:100%;margin-bottom:12px;padding:17px;background:#fff;border:1px solid #e8e8e8;border-radius:12px;box-shadow:0 3px 14px rgba(40,28,30,.045)}.hero-card{position:relative;overflow:hidden;border-top:3px solid var(--brand)}.hero-card::after{content:"";position:absolute;right:-34px;top:-52px;width:116px;height:116px;border-radius:50%;background:linear-gradient(135deg,rgba(215,4,26,.12),rgba(215,4,26,0));pointer-events:none}.section-kicker{margin-bottom:5px;color:var(--brand);font-size:10px;font-weight:700;letter-spacing:.12em}.hero-card h1{position:relative;margin:0;color:#171717;font-size:22px;line-height:1.35}.hero-card>p{position:relative;margin:8px 0 13px;color:#777;font-size:12px;line-height:1.65}.document-state{display:flex;min-width:0;align-items:center;gap:7px;padding:8px 10px;border-radius:7px;background:#fafafa;color:#999;font-size:11px}.document-state.ok{background:#f6ffed;color:#3d9b18}.dot{width:7px;height:7px;flex:0 0 7px;border-radius:50%;background:currentColor}.document-name{min-width:0;margin-left:auto;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#555}.card-heading{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:12px}.card-heading.compact{margin-bottom:12px}.card-heading>div{display:flex;align-items:center;gap:8px;min-width:0}.heading-index{display:inline-flex;align-items:center;justify-content:center;width:26px;height:22px;border-radius:5px;background:#fee7e8;color:var(--brand);font-size:10px;font-weight:700}.card-heading h2{margin:0;color:#222;font-size:15px}.timeline-status{padding:3px 7px;border-radius:999px;background:#fff4f4;color:var(--brand);font-size:10px}.line-input{box-sizing:border-box;width:100%;padding:11px 12px;border:1px solid #dedede;border-radius:8px;background:#fff;color:#333;font:inherit;font-size:12px;outline:none;transition:border-color .2s}.line-input:focus{border-color:var(--brand);box-shadow:0 0 0 3px rgba(215,4,26,.08)}.new-project{display:flex;gap:8px;margin-top:10px}.new-project .line-input{margin:0;flex:1}.ghost-btn{padding:8px 13px;border:1px solid #ddd;border-radius:7px;background:#fff;color:#555;font:inherit;font-size:11px;cursor:pointer;white-space:nowrap}.ghost-btn:hover:not(:disabled){border-color:var(--brand);color:var(--brand)}.ghost-btn:disabled{opacity:.5;cursor:not-allowed}.ghost-btn.danger{color:#c53030}.upload-area{display:flex;align-items:center;justify-content:center;min-height:96px;border:1.5px dashed #d9d9d9;border-radius:10px;background:#fafafa;color:#999;font-size:12px;cursor:pointer;transition:border-color .2s}.upload-area:hover:not(.busy){border-color:var(--brand);color:var(--brand)}.upload-area.busy{cursor:progress}.upload-area input{display:none}.doc-box{padding:11px;border:1px solid #eee;border-radius:9px;background:#fafafa}.doc-row{display:flex;align-items:center;justify-content:space-between;gap:9px}.doc-name{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#333;font-size:12px}.doc-status{padding:3px 7px;border-radius:999px;font-size:10px;background:#f5f5f5;color:#777}.doc-parsed{background:#f6ffed;color:#3d9b18}.doc-failed{background:#fff1f0;color:#c53030}.doc-meta{margin:7px 0 0;color:#888;font-size:10px}.doc-actions{display:flex;justify-content:flex-end;margin-top:8px}.actions{display:flex;gap:9px;margin-top:14px}.actions button{display:inline-flex;align-items:center;justify-content:center;min-height:38px;border-radius:7px;padding:8px 16px;font:inherit;font-size:12px;font-weight:600;cursor:pointer;transition:filter .2s,opacity .2s}.primary{flex:1;border:0;background:linear-gradient(90deg,var(--brand),var(--brand-deep));color:#fff;box-shadow:0 4px 12px rgba(215,4,26,.2)}.secondary{border:1px solid #ddd;background:#fff;color:#666}.actions button:disabled{opacity:.45;cursor:not-allowed;box-shadow:none}.hint{margin:9px 0 0;color:#999;font-size:10px;line-height:1.5}.phase-stepper{display:flex;align-items:flex-start;gap:2px;padding:2px 0 6px}.phase-node{display:flex;flex-direction:column;align-items:center;gap:3px;min-width:54px;max-width:78px}.phase-dot{display:flex;align-items:center;justify-content:center;width:22px;height:22px;border:1px solid #ddd;border-radius:50%;background:#fafafa;color:#aaa;font-size:9px;font-weight:700}.phase-label{color:#999;font-size:9px;line-height:1.25;text-align:center}.phase-state{color:#c2c2c2;font-size:8px}.phase-bar{flex:1 1 auto;min-width:6px;height:2px;margin-top:10px;border-radius:1px;background:#ececec}.phase-bar.passed{background:#cfe9bf}.phase-node.is-active .phase-dot{border-color:var(--brand);background:#fee7e8;color:var(--brand);animation:pulse 1.4s ease-in-out infinite}.phase-node.is-active .phase-label{color:var(--brand);font-weight:600}.phase-node.is-active .phase-state{color:var(--brand)}.phase-node.is-done .phase-dot{border-color:#b7df9f;background:#f6ffed;color:#3d9b18}.phase-node.is-done .phase-label{color:#3d9b18}.phase-node.is-done .phase-state{color:#8fd66a}.progress-message{display:flex;align-items:flex-start;gap:7px;margin-top:4px;padding:9px 10px;border-radius:7px;background:#f7f7f7;color:#666;font-size:11px;line-height:1.5}.pulse-dot{width:6px;height:6px;flex:0 0 6px;margin-top:5px;border-radius:50%;background:var(--brand);animation:pulse 1.4s ease-in-out infinite}
.detail-caption{margin:10px 2px 0;color:#999;font-size:9px;font-weight:600;letter-spacing:.05em}
.detail-scroll{margin-top:5px;max-height:280px;overflow-y:auto;border:1px solid #f0f0f0;border-radius:8px;background:#fafafa;padding:10px 10px 4px}
.detail-scroll :deep(.ant-timeline){margin:0;padding-left:2px}
.detail-scroll :deep(.ant-timeline-item){padding-bottom:10px}
.detail-scroll :deep(.ant-timeline-item:last-child){padding-bottom:0}
.detail-scroll :deep(.ant-timeline-item-head){background:transparent;font-size:12px}
.detail-scroll :deep(.ant-timeline-item-head-custom){padding:0;line-height:1}
.step-icon{font-size:12px}
.icon-phase{color:#1677ff}.icon-section{color:#52c41a}.icon-info{color:#8c8c8c}.icon-error{color:#ff4d4f}.icon-analysis{color:#fa8c16}
.icon-live{color:#1677ff}
.spin-icon{display:inline-block;animation:detail-spin 1s linear infinite}
@keyframes detail-spin{to{transform:rotate(360deg)}}
.step-card{padding:6px 9px;border-radius:6px;background:#fff;border:1px solid #f0f0f0;font-size:11px;line-height:1.55}
.card-phase{border-left:3px solid #1677ff;background:#f0f7ff}
.card-section{border-left:3px solid #52c41a;background:#f6ffed}
.card-info{border-left:3px solid #bbb}
.card-analysis{border-left:3px solid #fa8c16;background:#fff7e6}
.card-error{border-left:3px solid #ff4d4f;background:#fff1f0}
.card-error .step-card-text{color:#c53030}
.step-card-header{display:flex;align-items:center;gap:6px;margin-bottom:2px}
.step-tag{margin-right:0;flex:0 0 auto;line-height:16px;padding:0 5px;font-size:9px}
.step-ts{color:#aaa;font-family:Consolas,monospace;font-size:9px}
.step-card-text{color:#444;overflow-wrap:anywhere}
.step-progress{margin-left:8px;color:#8c8c8c;font-size:9px;white-space:nowrap}
.step-card.is-clickable{cursor:pointer}
.step-card.is-clickable:hover{border-color:#b7eb8f}
.modal-actions{display:flex;align-items:center;gap:8px;flex:0 0 auto}
.analysis-detail{margin-top:4px}
.analysis-detail summary{cursor:pointer;color:#d46b08;font-size:10px;font-weight:600;user-select:none}
.analysis-detail .analysis-block pre{max-height:220px}
.analysis-block pre{margin:9px 0 0;padding:10px;border-radius:7px;background:#f7f7f7;color:#666;font-size:10px;line-height:1.6;white-space:pre-wrap;word-break:break-word;max-height:32vh;overflow-y:auto}
.mini-btn{padding:4px 8px;border:1px solid #ddd;border-radius:6px;background:#fff;color:#666;font:inherit;font-size:9px;cursor:pointer}.mini-btn:hover:not(:disabled){border-color:var(--brand);color:var(--brand)}.mini-btn:disabled{opacity:.5;cursor:not-allowed}
.applied-tip{margin-bottom:10px;padding:9px 10px;border-radius:7px;background:#f6ffed;color:#3d9b18;font-size:11px}
.error{margin-top:11px;padding:9px 10px;border:1px solid #ffccc7;border-radius:7px;background:#fff1f0;color:#c53030;font-size:11px;line-height:1.55;overflow-wrap:anywhere}
.inserting-hint{color:#c58608}
.modal-mask{position:fixed;inset:0;z-index:50;display:flex;align-items:center;justify-content:center;padding:18px;background:rgba(0,0,0,.38)}.modal-card{display:flex;flex-direction:column;width:min(680px,100%);max-height:80vh;background:#fff;border-radius:12px;overflow:hidden}.modal-head{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:12px 14px;border-bottom:1px solid #f0f0f0}.modal-head strong{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#333;font-size:13px}.modal-body{flex:1;overflow-y:auto;padding:14px}.modal-pre{margin:0 0 4px;color:#444;font-size:11px;line-height:1.75;white-space:pre-wrap;word-break:break-word}.modal-empty{margin:0;color:#999;font-size:11px}
@keyframes pulse{0%,100%{box-shadow:0 0 0 3px rgba(215,4,26,.07)}50%{box-shadow:0 0 0 6px rgba(215,4,26,.03)}}
@media (max-width:560px){.bid-draft-view{padding:0 8px 28px}.brand-line{margin:0 -8px}.panel-header{min-height:56px}.panel-logo{width:96px}.metric-pill{height:27px;min-width:80px;padding-left:30px;font-size:10px}.card{padding:14px 12px;margin-bottom:9px;border-radius:9px}.hero-card h1{font-size:19px}.document-state{align-items:flex-start;flex-wrap:wrap}.document-name{flex-basis:100%;margin-left:14px}.new-project{flex-direction:column}.actions{flex-direction:column}.actions button{width:100%}}
@media (max-width:390px){.panel-header{align-items:flex-start;flex-direction:column;padding:10px 3px}.account-strip{width:100%}.metric-pill{flex:1}}
</style>
