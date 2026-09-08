<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from "vue";
import { Modal, Tag } from "ant-design-vue";
import {
  cancelWritingTask,
  confirmSpec,
  createWizard,
  estimateIndexCost,
  generateQuestionnaire,
  generateSpec,
  getActiveWizard,
  getLatestWritingTask,
  getMaterialIndex,
  getWizard,
  getWizardAccess,
  getWritingSectionContent,
  listMaterials,
  listWritingSections,
  markSectionWritten,
  regenerateWritingSection,
  deleteMaterial as apiDeleteMaterial,
  deleteTender as apiDeleteTender,
  createWritingTask,
  rollbackSpec as apiRollbackSpec,
  reindexMaterial as apiReindexMaterial,
  reviseSpec as apiReviseSpec,
  saveRequirements as apiSaveRequirements,
  saveSpec as apiSaveSpec,
  updateWizardStage,
  uploadMaterial as apiUploadMaterial,
  uploadTender as apiUploadTender,
  wizardStreamUrl,
  wizardToken,
  type Wizard,
  type WizardMaterial,
  type WizardQuestion,
  type WizardSection,
  type WizardSpecNode,
  type WizardWritingTask,
  type WizardStage,
} from "@/api/bidWizard";
import { documentsApi } from "@/api/client";
import type { Document } from "@/types";
import { useVstoBridge } from "@/composables/useVstoBridge";
import { prepareChartAssets, splitMermaidFences } from "@/utils/chartAssets";
import logoUrl from "@/assets/images/ui/common-logo-black.png";

const bridge = useVstoBridge();

/** 向导锚点书签（常量，与单章书签名一起由页面生成、插件只接收）。 */
const ANCHOR_BOOKMARK = "AI_WIZARD_ANCHOR";
function sectionBookmarks(nodeId: string): { start: string; end: string } {
  const safe = nodeId.replace(/[^0-9A-Za-z]/g, "_").slice(0, 24);
  return { start: `AIWIZ_${safe}_S`, end: `AIWIZ_${safe}_E` };
}

const STAGES: { key: WizardStage; title: string; hint: string }[] = [
  { key: "material", title: "素材准备", hint: "上传招标文件与公司素材，AI 自动分段索引" },
  { key: "requirement", title: "需求确认", hint: "AI 提问挖掘编写需求，可采纳素材建议答案" },
  { key: "outline", title: "编写大纲", hint: "目录＋每章摘要＋图表规划，可手工或 AI 修改" },
  { key: "writing", title: "逐章撰写", hint: "逐章生成并写入 Word，每章可单独撤销/重生成" },
];
const stageIndexOf: Record<string, number> = { material: 0, requirement: 1, outline: 2, writing: 3 };

const access = ref<{ enabled: boolean; mode: string } | null>(null);
const wizard = ref<Wizard | null>(null);
const loading = ref(true);
const pageError = ref("");
const busy = ref(""); // 当前阻塞交互的操作名（按钮态）

function friendlyError(error: unknown, fallback: string): string {
  const err = error as { status?: number; detail?: unknown; response?: { status?: number; data?: { detail?: unknown } }; message?: string };
  const status = err?.status ?? err?.response?.status;
  const detail = err?.detail ?? err?.response?.data?.detail;
  if (status === 402) {
    return (detail && typeof detail === "object" && "message" in detail)
      ? String((detail as { message?: string }).message)
      : "余额不足，请先充值后再使用 AI编标";
  }
  if (status === 409 && detail && typeof detail === "object" && "code" in detail
    && (detail as { code?: string }).code === "ACTIVE_BILLING_TASK_EXISTS") {
    return "您有正在进行的 AI 任务（检查/生成/润色/素材索引），请等待完成或取消后再试";
  }
  if (typeof detail === "string" && detail) return detail;
  if (detail && typeof detail === "object" && "message" in detail) {
    return String((detail as { message?: string }).message || fallback);
  }
  return error instanceof Error ? error.message : fallback;
}

async function withBusy(name: string, action: () => Promise<void>) {
  if (busy.value) return;
  busy.value = name;
  pageError.value = "";
  try {
    await action();
  } catch (error) {
    pageError.value = friendlyError(error, "操作失败，请稍后重试");
  } finally {
    busy.value = "";
  }
}

async function refreshWizard() {
  if (!wizard.value) return;
  wizard.value = await getWizard(wizard.value.id);
}

const stageIndex = computed(() => stageIndexOf[wizard.value?.stage || "material"] ?? 0);
const bridgeStateText = computed(() =>
  bridge.contextReady.value
    ? "已连接 Word 文档"
    : bridge.available.value
      ? "正在连接 Word 文档"
      : "未检测到 Word 插件桥（仍可完成前三阶段）",
);

async function gotoStage(index: number) {
  if (!wizard.value || busy.value) return;
  if (index === stageIndex.value) return;
  if (index > stageIndex.value && index >= 3 && !wizard.value.spec_confirmed_at) return;
  await withBusy("stage", async () => {
    wizard.value = await updateWizardStage(wizard.value!.id, STAGES[index].key);
  });
}

// =============================================================== 阶段 1：素材准备

const tenderDoc = ref<Document | null>(null);
const materials = ref<WizardMaterial[]>([]);
const tenderUploading = ref(false);
const materialUploading = ref(false);
let materialPollTimer: ReturnType<typeof setInterval> | null = null;
let tenderPollTimer: ReturnType<typeof setInterval> | null = null;

async function loadTenderDoc() {
  if (!wizard.value) return;
  try {
    const docs = await documentsApi.list(wizard.value.project_id);
    const tender = docs.find((item) => item.doc_type === "tender") || null;
    tenderDoc.value = tender;
    if (tender && (tender.status === "pending" || tender.status === "parsing")) {
      startTenderPoll();
    }
  } catch {
    /* 入口静默，操作时报错 */
  }
}

function startTenderPoll() {
  stopTenderPoll();
  if (!wizard.value || !tenderDoc.value) return;
  const projectId = wizard.value.project_id;
  const documentId = tenderDoc.value.id;
  tenderPollTimer = setInterval(async () => {
    try {
      const updated = await documentsApi.get(projectId, documentId);
      tenderDoc.value = updated;
      if (updated.status === "parsed" || updated.status === "failed") stopTenderPoll();
    } catch {
      /* keep polling */
    }
  }, 2_000);
}

function stopTenderPoll() {
  if (tenderPollTimer) {
    clearInterval(tenderPollTimer);
    tenderPollTimer = null;
  }
}

async function loadMaterials() {
  if (!wizard.value) return;
  materials.value = await listMaterials(wizard.value.id);
}

function startMaterialPoll() {
  stopMaterialPoll();
  if (!wizard.value) return;
  materialPollTimer = setInterval(async () => {
    try {
      await loadMaterials();
      const active = materials.value.some(
        (item) => item.index_status === "pending" || item.index_status === "indexing",
      );
      const parsing = materials.value.some(
        (item) => item.doc_status === "pending" || item.doc_status === "parsing",
      );
      if (!active && !parsing) stopMaterialPoll();
    } catch {
      /* keep polling */
    }
  }, 3_000);
}

function stopMaterialPoll() {
  if (materialPollTimer) {
    clearInterval(materialPollTimer);
    materialPollTimer = null;
  }
}

function onUploadTender(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = "";
  if (!file || !wizard.value) return;
  void withBusy("tender", async () => {
    if (tenderDoc.value) {
      await apiDeleteTender(wizard.value!.id);
      tenderDoc.value = null;
      stopTenderPoll();
    }
    tenderUploading.value = true;
    try {
      await apiUploadTender(wizard.value!.id, file);
      await Promise.all([refreshWizard(), loadTenderDoc()]);
    } finally {
      tenderUploading.value = false;
    }
  });
}

function onUploadMaterials(event: Event) {
  const input = event.target as HTMLInputElement;
  const files = Array.from(input.files || []);
  input.value = "";
  if (!files.length || !wizard.value) return;
  void withBusy("material", async () => {
    // 上传前预估确认（决策 17a）：粗估量级、实际按用量计费
    const estimate = await estimateIndexCost(
      files.reduce((sum, file) => sum + file.size, 0),
    ).catch(() => null);
    const tokensText = estimate
      ? `预计索引消耗约 ${(estimate.estimated_tokens / 1000).toFixed(1)} 千 token`
      : "按实际用量计费";
    const confirmed = await new Promise<boolean>((resolve) => {
      Modal.confirm({
        title: "确认上传素材？",
        content: `已选 ${files.length} 份素材，${tokensText}（上传后自动建立索引，费用计入账户）`,
        okText: "上传并索引",
        cancelText: "取消",
        onOk: () => resolve(true),
        onCancel: () => resolve(false),
      });
    });
    if (!confirmed) return;
    materialUploading.value = true;
    try {
      for (const file of files) {
        await apiUploadMaterial(wizard.value!.id, file, null);
      }
      await Promise.all([refreshWizard(), loadMaterials()]);
      startMaterialPoll();
    } finally {
      materialUploading.value = false;
    }
  });
}

function removeMaterial(item: WizardMaterial) {
  if (!wizard.value) return;
  Modal.confirm({
    title: "删除素材？",
    content: `${item.original_filename || "该素材"} 将从素材池移除，相关需求与大纲会标记为过期。`,
    okText: "删除",
    okType: "danger",
    cancelText: "取消",
    onOk: () =>
      withBusy("material", async () => {
        await apiDeleteMaterial(wizard.value!.id, item.document_id);
        await Promise.all([refreshWizard(), loadMaterials()]);
      }),
  });
}

function reindexFailed(item: WizardMaterial) {
  if (!wizard.value) return;
  void withBusy("material", async () => {
    await apiReindexMaterial(wizard.value!.id, item.document_id);
    await loadMaterials();
    startMaterialPoll();
  });
}

const materialIndexModal = reactive({
  open: false,
  title: "",
  content: "",
  status: "",
});

function viewMaterialIndex(item: WizardMaterial) {
  if (!wizard.value) return;
  void withBusy("material", async () => {
    const result = await getMaterialIndex(wizard.value!.id, item.document_id);
    materialIndexModal.title = item.original_filename || "素材索引";
    materialIndexModal.status = result.index_status;
    materialIndexModal.content = result.index_content || "（索引尚未生成）";
    materialIndexModal.open = true;
  });
}

const tenderReady = computed(() => tenderDoc.value?.status === "parsed");
const indexSummary = computed(() => {
  const indexed = materials.value.filter((item) => item.index_status === "indexed").length;
  const indexing = materials.value.filter(
    (item) => item.index_status === "pending" || item.index_status === "indexing",
  ).length;
  const failed = materials.value.filter((item) => item.index_status === "failed").length;
  return { indexed, indexing, failed };
});

// =============================================================== 阶段 2：需求确认

const questionnaire = ref<WizardQuestion[]>([]);
type AnswerState = { action: "answered" | "adopted" | "skipped"; answer: string };
const answerDrafts = reactive<Record<string, AnswerState>>({});

const groupedQuestions = computed(() => {
  const groups: { topic: string; items: WizardQuestion[] }[] = [];
  for (const question of questionnaire.value) {
    const group = groups.find((item) => item.topic === question.topic);
    if (group) group.items.push(question);
    else groups.push({ topic: question.topic, items: [question] });
  }
  return groups;
});

function ensureAnswerDraft(question: WizardQuestion) {
  if (!answerDrafts[question.id]) {
    answerDrafts[question.id] = {
      action: question.suggested_answer ? "adopted" : "skipped",
      answer: "",
    };
  }
  return answerDrafts[question.id];
}

function onGenerateQuestionnaire() {
  if (!wizard.value) return;
  if (!tenderReady.value) {
    pageError.value = "招标文件尚未解析完成，请稍候";
    return;
  }
  void withBusy("questionnaire", async () => {
    wizard.value = await generateQuestionnaire(wizard.value!.id);
    questionnaire.value = wizard.value.questionnaire?.questions || [];
    for (const question of questionnaire.value) ensureAnswerDraft(question);
  });
}

function onSubmitRequirements() {
  if (!wizard.value) return;
  void withBusy("requirements", async () => {
    const answers = questionnaire.value.map((question) => {
      const draft = ensureAnswerDraft(question);
      return {
        question_id: question.id,
        action: draft.action,
        answer: draft.action === "answered" ? draft.answer : null,
      };
    });
    wizard.value = await apiSaveRequirements(wizard.value!.id, answers);
    wizard.value = await updateWizardStage(wizard.value!.id, "outline");
  });
}

// =============================================================== 阶段 3：编写大纲

const specNodes = ref<WizardSpecNode[]>([]);
const specDirty = ref(false);
const reviseInstruction = ref("");

const canConfirmSpec = computed(() =>
  Boolean(specNodes.value.length && !wizard.value?.spec_stale),
);

function loadSpecFromWizard() {
  specNodes.value = (wizard.value?.spec || []).map((node) => ({
    ...node,
    charts: node.charts ? node.charts.map((chart) => ({ ...chart })) : null,
  }));
  specDirty.value = false;
}

function markSpecDirty() {
  specDirty.value = true;
}

function addNode(afterIndex: number, level: number) {
  const node: WizardSpecNode = {
    node_id: "",
    title: "新章节",
    level: Math.max(1, level),
    summary: "",
    article_count: 2,
    text_count: 400,
    charts: null,
  };
  specNodes.value.splice(afterIndex + 1, 0, node);
  markSpecDirty();
}

function removeNode(index: number) {
  specNodes.value.splice(index, 1);
  markSpecDirty();
}

function moveNode(index: number, delta: number) {
  const target = index + delta;
  if (target < 0 || target >= specNodes.value.length) return;
  const [node] = specNodes.value.splice(index, 1);
  specNodes.value.splice(target, 0, node);
  markSpecDirty();
}

function addChart(node: WizardSpecNode) {
  if (!node.charts) node.charts = [];
  node.charts.push({ type: "table", title: "新图表", points: "" });
  markSpecDirty();
}

function onGenerateSpec() {
  if (!wizard.value) return;
  if (wizard.value.requirements === null) {
    pageError.value = "请先完成需求确认（保存问卷答案）";
    return;
  }
  void withBusy("spec", async () => {
    wizard.value = await generateSpec(wizard.value!.id);
    loadSpecFromWizard();
  });
}

function onReviseSpec() {
  if (!wizard.value || !reviseInstruction.value.trim()) return;
  void withBusy("spec", async () => {
    wizard.value = await apiReviseSpec(wizard.value!.id, reviseInstruction.value.trim());
    loadSpecFromWizard();
    reviseInstruction.value = "";
  });
}

function onRollbackSpec() {
  if (!wizard.value) return;
  void withBusy("spec", async () => {
    wizard.value = await apiRollbackSpec(wizard.value!.id);
    loadSpecFromWizard();
  });
}

function onSaveSpec() {
  if (!wizard.value || !specNodes.value.length) return;
  void withBusy("spec", async () => {
    const payload = specNodes.value.map((node) => ({
      title: node.title,
      level: node.level,
      summary: node.summary || null,
      article_count: node.article_count || 2,
      text_count: node.text_count || 400,
      charts: node.charts,
    })) as WizardSpecNode[];
    wizard.value = await apiSaveSpec(wizard.value!.id, payload);
    loadSpecFromWizard();
  });
}

function onConfirmSpec() {
  if (!wizard.value) return;
  void withBusy("spec", async () => {
    if (specDirty.value) await onSaveSpecInner();
    wizard.value = await confirmSpec(wizard.value!.id);
    wizard.value = await updateWizardStage(wizard.value!.id, "writing");
  });
}

async function onSaveSpecInner() {
  const payload = specNodes.value.map((node) => ({
    title: node.title,
    level: node.level,
    summary: node.summary || null,
    article_count: node.article_count || 2,
    text_count: node.text_count || 400,
    charts: node.charts,
  })) as WizardSpecNode[];
  wizard.value = await apiSaveSpec(wizard.value!.id, payload);
  loadSpecFromWizard();
}

// =============================================================== 阶段 4：逐章撰写

const writeMode = ref<"auto" | "confirm">("auto");
const selectedNodes = ref<Set<string>>(new Set());
const taskId = ref("");
const task = ref<WizardWritingTask | null>(null);
const sections = ref<WizardSection[]>([]);
const eventLog = ref<{ time: string; text: string; kind: "info" | "ok" | "error" }[]>([]);
const awaitingWrite = ref<string[]>([]); // 逐章确认模式待写入队列（node_id 顺序）
const writingNode = ref("");
const writeError = ref("");
const taskRunning = computed(() =>
  Boolean(task.value && !["completed", "failed", "cancelled"].includes(task.value.status)),
);
let streamController: AbortController | null = null;
let taskPollTimer: ReturnType<typeof setInterval> | null = null;
const logScrollRef = ref<HTMLElement | null>(null);

watch(
  () => eventLog.value.length,
  async () => {
    await nextTick();
    const el = logScrollRef.value;
    if (el) el.scrollTop = el.scrollHeight;
  },
);

function pushLog(kind: "info" | "ok" | "error", text: string) {
  eventLog.value.push({ time: new Date().toLocaleTimeString("zh-CN", { hour12: false }), text, kind });
  if (eventLog.value.length > 200) eventLog.value.splice(0, eventLog.value.length - 200);
}

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

function upsertSection(meta: Partial<WizardSection> & { node_id: string }) {
  const index = sections.value.findIndex((item) => item.node_id === meta.node_id);
  if (index >= 0) sections.value[index] = { ...sections.value[index], ...meta } as WizardSection;
  else {
    sections.value.push({
      title: meta.node_id,
      status: "pending",
      attempts: 0,
      ...meta,
    } as WizardSection);
  }
}

function toggleNode(nodeId: string) {
  if (selectedNodes.value.has(nodeId)) selectedNodes.value.delete(nodeId);
  else selectedNodes.value.add(nodeId);
}

function onStartWriting() {
  if (!wizard.value || !selectedNodes.value.size) return;
  void withBusy("writing", async () => {
    const created = await createWritingTask(wizard.value!.id, Array.from(selectedNodes.value));
    trackTask(created);
  });
}

function trackTask(next: WizardWritingTask) {
  stopTaskStream();
  stopTaskPoll();
  taskId.value = next.id;
  task.value = next;
  sections.value = [];
  awaitingWrite.value = [];
  writeError.value = "";
  writingNode.value = "";
  eventLog.value = [];
  pushLog("info", next.continue_of ? "单章重生成任务已提交" : "撰写任务已提交");
  void listenTask(next.id);
  startTaskPoll();
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
  const token = wizardToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  stopTaskStream();
  const controller = new AbortController();
  streamController = controller;
  try {
    const response = await fetch(wizardStreamUrl(id), { headers, signal: controller.signal });
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
  } catch {
    /* 轮询兜底仍在跑 */
  } finally {
    if (streamController === controller) streamController = null;
  }
}

function handleTaskEvent(event: Record<string, unknown>) {
  const type = String(event.type || "");
  if (type === "status") {
    const status = String(event.status || "");
    if (status === "completed" || status === "failed" || status === "cancelled") void refreshTask();
  } else if (type === "phase") {
    pushLog("info", `开始逐章撰写（共 ${event.section_total || "?"} 章）`);
  } else if (type === "section_started") {
    upsertSection({
      node_id: String(event.node_id || ""),
      title: String(event.title || event.node_id || ""),
      status: "generating",
    });
  } else if (type === "section_completed") {
    upsertSection({
      node_id: String(event.node_id || ""),
      title: String(event.title || ""),
      status: "generated",
      word_count: Number(event.word_count || 0),
    });
    pushLog("ok", `完成章节 ${event.node_id} ${displayTitle(String(event.node_id), String(event.title || ""))}`);
    if (writeMode.value === "auto") void writeSection(String(event.node_id || ""));
    else awaitingWrite.value.push(String(event.node_id || ""));
  } else if (type === "section_failed") {
    upsertSection({
      node_id: String(event.node_id || ""),
      status: "failed",
      error_message: String(event.error || "生成失败"),
    });
    pushLog("error", `章节失败 ${event.node_id}：${String(event.error || "生成失败")}`);
  } else if (type === "error") {
    pushLog("error", String(event.message || "任务失败"));
  }
}

function startTaskPoll() {
  stopTaskPoll();
  taskPollTimer = setInterval(() => {
    void refreshTask(true);
  }, 5_000);
}

async function refreshTask(quiet = false) {
  if (!taskId.value) return;
  try {
    const [current, sectionRows] = await Promise.all([
      import("@/api/bidWizard").then((m) => m.getWritingTask(taskId.value)),
      listWritingSections(taskId.value),
    ]);
    task.value = current;
    for (const row of sectionRows) upsertSection(row);
    if (!taskRunning.value) {
      stopTaskPoll();
      stopTaskStream();
      if (current.status === "completed") pushLog("ok", "撰写任务完成");
      if (current.status === "failed") pushLog("error", current.error_message || "任务失败");
      if (current.status === "cancelled") pushLog("info", "任务已取消");
      // 恢复场景：已生成未写入的章节进入待写入队列
      const pending = sectionRows.filter((row) => row.status === "generated").map((row) => row.node_id);
      if (pending.length && !awaitingWrite.value.length) awaitingWrite.value.push(...pending);
    }
  } catch (error) {
    if (!quiet) pageError.value = friendlyError(error, "读取撰写任务失败");
  }
}

/** 把一章写入 Word（书签锚点推进 + 章首/章尾书签对 + 图表预处理 + written 回报）。 */
async function writeSection(nodeId: string) {
  if (!taskId.value || writingNode.value) return;
  writingNode.value = nodeId;
  writeError.value = "";
  try {
    const content = await getWritingSectionContent(taskId.value, nodeId);
    if (!content.content) throw new Error("章节内容为空");
    const prepared = await prepareChartAssets(content.content);
    const marks = sectionBookmarks(nodeId);
    const result = await bridge.insertSection(prepared.content, {
      anchorBookmark: ANCHOR_BOOKMARK,
      sectionStartBookmark: marks.start,
      sectionEndBookmark: marks.end,
      label: `AI 撰写：${displayTitle(nodeId, content.title)}`,
      images: prepared.images,
    });
    if (!result.success) {
      if (result.code === "snapshot_stale" || result.code === "bookmark_missing") {
        writeError.value = "Word 插入锚点失效（文档结构变化或书签被删），已生成的章节可稍后在列表中重新写入";
      } else {
        writeError.value = result.error || "写入 Word 失败";
      }
      awaitingWrite.value.push(nodeId);
      return;
    }
    await markSectionWritten(taskId.value, nodeId);
    upsertSection({ node_id: nodeId, status: "written" });
    pushLog("ok", `已写入 Word：${displayTitle(nodeId, content.title)}（Ctrl+Z 可撤销本章）`);
  } catch (error) {
    writeError.value = friendlyError(error, "写入 Word 失败");
    awaitingWrite.value.push(nodeId);
  } finally {
    writingNode.value = "";
    const index = awaitingWrite.value.indexOf(nodeId);
    if (index >= 0) awaitingWrite.value.splice(index, 1);
  }
}

function onCancelTask() {
  if (!taskId.value) return;
  void withBusy("writing", async () => {
    await cancelWritingTask(taskId.value);
    await refreshTask();
  });
}

const previewModal = reactive({
  open: false,
  nodeId: "",
  title: "",
  content: "",
});

const previewSegments = computed(() =>
  previewModal.content ? splitMermaidFences(previewModal.content) : [],
);

async function previewSection(nodeId: string) {
  if (!taskId.value) return;
  const content = await getWritingSectionContent(taskId.value, nodeId);
  previewModal.nodeId = nodeId;
  previewModal.title = displayTitle(nodeId, content.title);
  previewModal.content = content.content || "";
  previewModal.open = true;
}

function onRegenerateSection(nodeId: string) {
  if (!taskId.value || taskRunning.value) return;
  Modal.confirm({
    title: "重新生成本章？",
    content: "将以当前大纲重新生成该章（新计一次撰写费用），完成后需手动替换 Word 中的旧章。",
    okText: "重新生成",
    cancelText: "取消",
    onOk: () =>
      withBusy("writing", async () => {
        const created = await regenerateWritingSection(taskId.value, nodeId);
        previewModal.open = false;
        trackTask(created);
      }),
  });
}

/** 重生成完成后替换 Word 中的旧章（书签对删旧插新，同一撤销单元）。 */
async function replaceSectionInWord(nodeId: string) {
  if (!taskId.value || writingNode.value) return;
  writingNode.value = nodeId;
  writeError.value = "";
  try {
    const content = await getWritingSectionContent(taskId.value, nodeId);
    if (!content.content) throw new Error("章节内容为空");
    const prepared = await prepareChartAssets(content.content);
    const marks = sectionBookmarks(nodeId);
    const result = await bridge.sectionReplace(marks.start, marks.end, prepared.content, {
      sectionStartBookmark: marks.start,
      sectionEndBookmark: marks.end,
      label: `AI 重写：${displayTitle(nodeId, content.title)}`,
      images: prepared.images,
    });
    if (!result.success) {
      writeError.value =
        result.code === "bookmark_missing"
          ? "Word 中的本章书签不存在（旧版写入或书签被删），请定位到本章后手动插入新内容"
          : result.error || "替换 Word 章节失败";
      return;
    }
    await markSectionWritten(taskId.value, nodeId);
    upsertSection({ node_id: nodeId, status: "written" });
    pushLog("ok", `已替换 Word 中的本章：${displayTitle(nodeId, content.title)}`);
  } catch (error) {
    writeError.value = friendlyError(error, "替换 Word 章节失败");
  } finally {
    writingNode.value = "";
  }
}

const writtenCount = computed(() => sections.value.filter((item) => item.status === "written").length);

// =============================================================== 装配与生命周期

async function bootstrap() {
  loading.value = true;
  try {
    access.value = await getWizardAccess();
    if (!access.value.enabled) return;
    wizard.value = await getActiveWizard();
    if (wizard.value) {
      questionnaire.value = wizard.value.questionnaire?.questions || [];
      for (const question of questionnaire.value) ensureAnswerDraft(question);
      loadSpecFromWizard();
      await Promise.all([loadTenderDoc(), loadMaterials()]);
      startMaterialPoll();
      if (wizard.value.stage === "writing") await resumeWriting();
    }
  } catch {
    /* 无活动向导：留在欢迎态 */
  } finally {
    loading.value = false;
  }
}

async function resumeWriting() {
  if (!wizard.value) return;
  try {
    const latest = await getLatestWritingTask(wizard.value.id);
    trackTask(latest);
  } catch {
    /* 没有撰写任务：正常展示选择界面 */
  }
}

function onStartNewWizard() {
  void withBusy("create", async () => {
    wizard.value = await createWizard({});
  });
}

onMounted(bootstrap);
onUnmounted(() => {
  stopTenderPoll();
  stopMaterialPoll();
  stopTaskStream();
  stopTaskPoll();
});
</script>

<template>
  <div class="wizard-page">
    <header class="wiz-header">
      <img :src="logoUrl" alt="标书审查智能体" class="wiz-logo">
      <div class="wiz-title">
        <h1>AI编标</h1>
        <span class="bridge-state">{{ bridgeStateText }}</span>
      </div>
    </header>

    <div v-if="loading" class="wiz-loading">正在进入 AI编标…</div>

    <template v-else-if="access && !access.enabled">
      <section class="guide-card">
        <h2>AI编标 功能即将开放</h2>
        <p>四阶段智能编写向导（素材准备 → 需求确认 → 编写大纲 → 逐章撰写）正在内测中，敬请期待。</p>
      </section>
    </template>

    <template v-else-if="!wizard">
      <section class="guide-card">
        <h2>开始一份新标书</h2>
        <p>AI 将引导你完成：上传招标文件与公司素材 → 确认编写需求 → 制定编写大纲 → 逐章撰写并写入 Word。</p>
        <button type="button" class="primary" :disabled="Boolean(busy)" @click="onStartNewWizard">开始向导</button>
      </section>
    </template>

    <template v-else>
      <div v-if="pageError" class="wiz-error" role="alert">{{ pageError }}</div>

      <nav class="stage-bar">
        <button
          v-for="(stage, index) in STAGES"
          :key="stage.key"
          type="button"
          class="stage-item"
          :class="{ active: index === stageIndex, done: index < stageIndex }"
          :disabled="index >= stageIndex + 1 && (index >= 3 ? !wizard.spec_confirmed_at : index > stageIndex)"
          :title="stage.hint"
          @click="gotoStage(index)"
        >
          <span class="stage-no">{{ index < stageIndex ? "✓" : index + 1 }}</span>
          <span>{{ stage.title }}</span>
        </button>
      </nav>

      <!-- ==================================================== 阶段 1：素材准备 -->
      <section v-if="stageIndex === 0" class="stage-panel">
        <div class="card">
          <h3>① 招标文件（必传一份）</h3>
          <div v-if="tenderDoc" class="doc-line">
            <span class="doc-name">{{ tenderDoc.original_filename }}</span>
            <Tag :color="tenderDoc.status === 'parsed' ? 'green' : tenderDoc.status === 'failed' ? 'red' : 'blue'">
              {{ tenderDoc.status === "parsed" ? "解析完成" : tenderDoc.status === "failed" ? "解析失败" : "解析中…" }}
            </Tag>
            <label class="link-btn">
              重新上传
              <input type="file" accept=".pdf,.docx,.doc,.xlsx" hidden :disabled="tenderUploading || Boolean(busy)" @change="onUploadTender">
            </label>
          </div>
          <label v-else class="upload-btn" :class="{ disabled: tenderUploading || Boolean(busy) }">
            {{ tenderUploading ? "上传中…" : "点击上传招标文件（PDF / Word / Excel）" }}
            <input type="file" accept=".pdf,.docx,.doc,.xlsx" hidden :disabled="tenderUploading || Boolean(busy)" @change="onUploadTender">
          </label>
          <p class="hint">建议上传：类似业绩合同、公司资质证书、拟投入人员证书、历史技术方案、售后服务案例等。AI 将自动分段索引，供问卷与撰写引用。</p>
        </div>

        <div class="card">
          <h3>② 公司素材（素材池）</h3>
          <div class="material-summary">
            <span>已索引 {{ indexSummary.indexed }}</span>
            <span v-if="indexSummary.indexing">索引中 {{ indexSummary.indexing }}</span>
            <span v-if="indexSummary.failed" class="danger">失败 {{ indexSummary.failed }}</span>
          </div>
          <label class="upload-btn" :class="{ disabled: materialUploading || Boolean(busy) }">
            {{ materialUploading ? "上传中…" : "点击上传素材（可多选）" }}
            <input type="file" multiple accept=".pdf,.docx,.doc,.xlsx,.txt,.md" hidden :disabled="materialUploading || Boolean(busy)" @change="onUploadMaterials">
          </label>
          <ul class="material-list">
            <li v-for="item in materials" :key="item.id" class="material-row">
              <span class="doc-name">{{ item.original_filename }}</span>
              <Tag :color="item.index_status === 'indexed' ? 'green' : item.index_status === 'failed' ? 'red' : 'blue'">
                {{ { pending: "待索引", indexing: "索引中", indexed: `已索引${item.chunk_count ? `(${item.chunk_count} 段)` : ""}`, failed: "索引失败" }[item.index_status] || item.index_status }}
              </Tag>
              <button v-if="item.index_status === 'indexed'" type="button" class="link-btn" @click="viewMaterialIndex(item)">查看索引</button>
              <button v-if="item.index_status === 'failed'" type="button" class="link-btn" @click="reindexFailed(item)">重试索引</button>
              <button type="button" class="link-btn danger" @click="removeMaterial(item)">删除</button>
            </li>
          </ul>
          <div v-if="wizard.requirements_stale" class="stale-tip">素材已变化：进入需求确认后请重新生成问卷。</div>
        </div>

        <div class="stage-actions">
          <button type="button" class="primary" :disabled="!tenderReady || Boolean(busy)" @click="gotoStage(1)">
            {{ tenderReady ? "下一步：需求确认" : "等待招标文件解析…" }}
          </button>
        </div>
      </section>

      <!-- ==================================================== 阶段 2：需求确认 -->
      <section v-else-if="stageIndex === 1" class="stage-panel">
        <div v-if="wizard.requirements_stale" class="stale-tip">
          素材或招标文件已变化，当前问卷基于旧素材。
          <button type="button" class="link-btn" :disabled="Boolean(busy)" @click="onGenerateQuestionnaire">重新生成问卷</button>
        </div>
        <div class="card">
          <template v-if="!questionnaire.length">
            <p class="hint">AI 将根据招标要素与素材索引生成结构化问卷（约 6-15 题），每题尽量附带素材依据的建议答案。</p>
            <button type="button" class="primary" :disabled="Boolean(busy) || !tenderReady" @click="onGenerateQuestionnaire">
              {{ busy === "questionnaire" ? "AI 正在生成问卷（最长 2 分钟）…" : "生成问卷" }}
            </button>
          </template>
          <template v-else>
            <div v-for="group in groupedQuestions" :key="group.topic" class="q-group">
              <h4>{{ group.topic }}</h4>
              <div v-for="question in group.items" :key="question.id" class="q-card">
                <div class="q-head">
                  <strong>{{ question.question }}</strong>
                  <Tag v-if="question.inferred" color="orange">推断，请确认</Tag>
                </div>
                <p v-if="question.why" class="q-why">为什么问：{{ question.why }}</p>
                <p v-if="question.suggested_answer" class="q-suggest">
                  建议答案：{{ question.suggested_answer }}
                  <span v-if="question.source" class="q-source">（来源：{{ question.source }}）</span>
                </p>
                <div class="q-actions">
                  <label class="radio">
                    <input
                      v-model="ensureAnswerDraft(question).action"
                      type="radio"
                      :name="question.id"
                      value="adopted"
                    >采纳建议
                  </label>
                  <label class="radio">
                    <input
                      v-model="ensureAnswerDraft(question).action"
                      type="radio"
                      :name="question.id"
                      value="answered"
                    >自定义
                  </label>
                  <label class="radio">
                    <input
                      v-model="ensureAnswerDraft(question).action"
                      type="radio"
                      :name="question.id"
                      value="skipped"
                    >跳过
                  </label>
                  <textarea
                    v-if="ensureAnswerDraft(question).action === 'answered'"
                    v-model="ensureAnswerDraft(question).answer"
                    rows="2"
                    placeholder="输入你的回答"
                  />
                </div>
              </div>
            </div>
          </template>
        </div>
        <div v-if="questionnaire.length" class="stage-actions">
          <button type="button" class="ghost" :disabled="Boolean(busy)" @click="gotoStage(0)">上一步</button>
          <button type="button" class="primary" :disabled="Boolean(busy)" @click="onSubmitRequirements">保存需求，进入编写大纲</button>
        </div>
        <div v-else class="stage-actions">
          <button type="button" class="ghost" :disabled="Boolean(busy)" @click="gotoStage(0)">上一步</button>
        </div>
      </section>

      <!-- ==================================================== 阶段 3：编写大纲 -->
      <section v-else-if="stageIndex === 2" class="stage-panel">
        <div v-if="wizard.spec_stale" class="stale-tip">
          需求/素材已变化，当前大纲基于旧输入。
          <button type="button" class="link-btn" :disabled="Boolean(busy)" @click="onGenerateSpec">重新生成大纲</button>
        </div>
        <div class="card">
          <template v-if="!specNodes.length">
            <p class="hint">AI 将根据编写需求、招标要素与素材索引生成大纲：目录结构＋每章摘要＋图表规划。</p>
            <button type="button" class="primary" :disabled="Boolean(busy)" @click="onGenerateSpec">
              {{ busy === "spec" ? "AI 正在生成大纲（最长 2 分钟）…" : "生成编写大纲" }}
            </button>
          </template>
          <template v-else>
            <div class="spec-toolbar">
              <button type="button" class="ghost" :disabled="Boolean(busy) || !specDirty" @click="onSaveSpec">保存修改</button>
              <button type="button" class="ghost" :disabled="Boolean(busy) || !wizard.spec_previous" @click="onRollbackSpec">回退 AI 修订</button>
              <span v-if="specDirty" class="dirty-tip">有未保存的修改</span>
            </div>
            <div class="spec-revise">
              <input
                v-model="reviseInstruction"
                type="text"
                placeholder="用一句话让 AI 修改大纲，如：把技术方案拆成三章"
                :disabled="Boolean(busy)"
                @keydown.enter="onReviseSpec"
              >
              <button type="button" class="primary" :disabled="Boolean(busy) || !reviseInstruction.trim()" @click="onReviseSpec">
                {{ busy === "spec" ? "AI 修改中…" : "AI 修改" }}
              </button>
            </div>
            <ul class="spec-tree">
              <li v-for="(node, index) in specNodes" :key="`${index}-${node.title}`" class="spec-node" :style="{ paddingLeft: `${(node.level - 1) * 16}px` }">
                <div class="spec-line">
                  <input v-model="node.title" class="spec-title" @input="markSpecDirty">
                  <button type="button" class="link-btn" @click="moveNode(index, -1)">↑</button>
                  <button type="button" class="link-btn" @click="moveNode(index, 1)">↓</button>
                  <button type="button" class="link-btn" @click="addNode(index, node.level + 1)">加子章</button>
                  <button type="button" class="link-btn" @click="addNode(index, node.level)">加同级</button>
                  <button type="button" class="link-btn danger" @click="removeNode(index)">删</button>
                </div>
                <textarea
                  v-model="node.summary"
                  class="spec-summary"
                  rows="2"
                  placeholder="本章摘要（写作纲领：回应什么、引用哪些素材）"
                  @input="markSpecDirty"
                />
                <div class="spec-meta">
                  <label>段落数 <input v-model.number="node.article_count" type="number" min="1" max="8" @input="markSpecDirty"></label>
                  <label>每段字数 <input v-model.number="node.text_count" type="number" min="100" max="3000" step="50" @input="markSpecDirty"></label>
                </div>
                <div v-if="node.charts && node.charts.length" class="spec-charts">
                  <div v-for="(chart, chartIndex) in node.charts" :key="chartIndex" class="chart-row">
                    <select v-model="chart.type" @change="markSpecDirty">
                      <option value="table">表格</option>
                      <option value="mermaid">图示</option>
                    </select>
                    <input v-model="chart.title" placeholder="图表标题" @input="markSpecDirty">
                    <input v-model="chart.points" placeholder="要点" @input="markSpecDirty">
                    <button type="button" class="link-btn danger" @click="node.charts?.splice(chartIndex, 1); markSpecDirty()">删</button>
                  </div>
                </div>
                <button type="button" class="link-btn" @click="addChart(node)">＋图表计划</button>
              </li>
            </ul>
          </template>
        </div>
        <div v-if="specNodes.length" class="stage-actions">
          <button type="button" class="ghost" :disabled="Boolean(busy)" @click="gotoStage(1)">上一步</button>
          <button type="button" class="primary" :disabled="Boolean(busy) || !canConfirmSpec" @click="onConfirmSpec">
            {{ wizard.spec_confirmed_at ? "已确认（再次进入撰写）" : "确认大纲，进入逐章撰写" }}
          </button>
        </div>
        <div v-else class="stage-actions">
          <button type="button" class="ghost" :disabled="Boolean(busy)" @click="gotoStage(1)">上一步</button>
        </div>
      </section>

      <!-- ==================================================== 阶段 4：逐章撰写 -->
      <section v-else class="stage-panel">
        <div class="card">
          <template v-if="!task">
            <h3>选择要撰写的章节（默认全选）</h3>
            <ul class="pick-list">
              <li v-for="node in wizard.spec || []" :key="node.node_id">
                <label class="radio">
                  <input
                    type="checkbox"
                    :checked="selectedNodes.has(node.node_id) || !selectedNodes.size"
                    @change="toggleNode(node.node_id)"
                  >
                  <span :style="{ paddingLeft: `${(node.level - 1) * 12}px` }">{{ node.node_id }} {{ node.title }}</span>
                </label>
              </li>
            </ul>
            <div class="mode-row">
              <label class="radio"><input v-model="writeMode" type="radio" value="auto">自动连写（每章生成完自动写入 Word）</label>
              <label class="radio"><input v-model="writeMode" type="radio" value="confirm">逐章确认（每章预览后手动写入）</label>
            </div>
            <button type="button" class="primary" :disabled="Boolean(busy)" @click="onStartWriting">开始撰写</button>
          </template>
          <template v-else>
            <div class="writing-head">
              <Tag :color="task.status === 'completed' ? 'green' : task.status === 'failed' ? 'red' : taskRunning ? 'blue' : 'default'">
                {{ { pending: "排队中", running: "撰写中", completed: "已完成", failed: "失败", cancelled: "已取消" }[task.status] || task.status }}
              </Tag>
              <span>已写入 {{ writtenCount }} / {{ sections.length }} 章</span>
              <button v-if="taskRunning" type="button" class="ghost" :disabled="Boolean(busy)" @click="onCancelTask">取消撰写</button>
            </div>
            <div v-if="writeError" class="wiz-error">{{ writeError }}</div>
            <div ref="logScrollRef" class="event-log">
              <div v-for="log in eventLog" :key="`${log.time}-${log.text}`" class="log-line" :class="log.kind">
                <span class="log-time">{{ log.time }}</span>{{ log.text }}
              </div>
            </div>
            <ul class="section-list">
              <li v-for="section in sortedSections" :key="section.node_id" class="section-row">
                <span class="doc-name">{{ section.node_id }} {{ displayTitle(section.node_id, section.title) }}</span>
                <Tag :color="{ pending: 'default', generating: 'blue', generated: 'orange', written: 'green', failed: 'red' }[section.status]">
                  {{ { pending: "待生成", generating: "生成中", generated: "待写入", written: "已写入", failed: "失败" }[section.status] || section.status }}
                </Tag>
                <button
                  v-if="section.status === 'generated' && !writingNode"
                  type="button"
                  class="link-btn"
                  @click="writeSection(section.node_id)"
                >{{ writingNode === section.node_id ? "写入中…" : "写入 Word" }}</button>
                <button type="button" class="link-btn" @click="previewSection(section.node_id)">预览</button>
                <button
                  v-if="!taskRunning && (section.status === 'written' || section.status === 'generated')"
                  type="button"
                  class="link-btn"
                  @click="onRegenerateSection(section.node_id)"
                >重生成</button>
                <button
                  v-if="!taskRunning && section.status === 'generated' && writtenCount > 0"
                  type="button"
                  class="link-btn"
                  @click="replaceSectionInWord(section.node_id)"
                >替换 Word 中本章</button>
              </li>
            </ul>
            <p class="hint">每章写入后可用一次 Ctrl+Z 撤销该章；重生成完成后用「替换 Word 中本章」更新旧章（会覆盖本章内的人工修改）。</p>
          </template>
        </div>
      </section>
    </template>

    <a-modal
      v-model:open="materialIndexModal.open"
      :title="materialIndexModal.title"
      :footer="null"
      width="640px"
    >
      <pre class="index-pre">{{ materialIndexModal.content }}</pre>
    </a-modal>

    <a-modal
      v-model:open="previewModal.open"
      :title="previewModal.title"
      :footer="null"
      width="720px"
    >
      <div class="preview-body">
        <template v-for="(segment, index) in previewSegments" :key="index">
          <pre v-if="segment.kind === 'text'" class="preview-text">{{ segment.content }}</pre>
          <blockquote v-else class="preview-mermaid">
            <pre>{{ segment.content }}</pre>
          </blockquote>
        </template>
      </div>
    </a-modal>
  </div>
</template>

<style scoped>
.wizard-page{display:flex;flex-direction:column;min-height:100vh;background:#f5f5f5;font-family:"Microsoft YaHei",sans-serif;font-size:13px;color:#222}
.wiz-header{display:flex;align-items:center;gap:14px;padding:12px 16px;background:#fff;border-bottom:1px solid #ebebeb}
.wiz-logo{width:96px;object-fit:contain}
.wiz-title h1{margin:0;font-size:18px}
.bridge-state{color:#888;font-size:12px}
.wiz-loading{padding:60px 16px;text-align:center;color:#888}
.wiz-error{margin:8px 16px;padding:9px 12px;border:1px solid #ffccc7;border-radius:7px;background:#fff1f0;color:#c23b3b;font-size:12px;line-height:1.6}
.guide-card{margin:40px auto;padding:28px;width:min(460px,calc(100vw - 28px));background:#fff;border:1px solid #e8e8e8;border-top:3px solid #d7041a;border-radius:12px;text-align:center}
.guide-card h2{margin:0 0 10px;font-size:17px}
.guide-card p{color:#777;line-height:1.7;margin:0 0 18px}
.stage-bar{display:flex;gap:6px;padding:10px 16px 0;background:#fff;border-bottom:1px solid #ebebeb;overflow-x:auto}
.stage-item{display:flex;align-items:center;gap:7px;padding:9px 13px;border:0;border-bottom:2px solid transparent;background:transparent;color:#888;cursor:pointer;font-size:13px;white-space:nowrap}
.stage-item.active{color:#d7041a;border-bottom-color:#d7041a;font-weight:600}
.stage-item.done{color:#52c41a}
.stage-item:disabled{opacity:.45;cursor:not-allowed}
.stage-no{display:inline-flex;align-items:center;justify-content:center;width:18px;height:18px;border:1px solid currentColor;border-radius:50%;font-size:11px}
.stage-panel{padding:12px 16px 24px;display:flex;flex-direction:column;gap:12px}
.card{background:#fff;border:1px solid #ebebeb;border-radius:10px;padding:14px}
.card h3{margin:0 0 10px;font-size:14px}
.card h4{margin:14px 0 8px;font-size:13px;color:#555}
.hint{color:#999;font-size:12px;line-height:1.7;margin:8px 0 0}
.doc-line{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.doc-name{font-weight:500;word-break:break-all}
.upload-btn{display:block;padding:18px;border:1.5px dashed #d9d9d9;border-radius:9px;text-align:center;color:#666;cursor:pointer;margin:4px 0}
.upload-btn:hover{border-color:#d7041a;color:#d7041a}
.upload-btn.disabled{opacity:.5;pointer-events:none}
.material-summary{display:flex;gap:14px;color:#777;margin-bottom:8px}
.material-list{list-style:none;margin:8px 0 0;padding:0;display:flex;flex-direction:column;gap:6px}
.material-row,.section-row{display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:6px 8px;border:1px solid #f0f0f0;border-radius:7px}
.link-btn{border:0;background:transparent;color:#1677ff;cursor:pointer;padding:0 2px;font-size:12px}
.link-btn.danger{color:#cf1322}
.danger{color:#cf1322}
.stale-tip{padding:8px 12px;border:1px solid #ffe58f;border-radius:7px;background:#fffbe6;color:#8c6d1f;font-size:12px}
.stage-actions{display:flex;justify-content:space-between;gap:10px}
button.primary{padding:9px 22px;border:0;border-radius:7px;background:linear-gradient(90deg,#d7041a,#b80015);color:#fff;cursor:pointer}
button.primary:disabled{opacity:.5;cursor:not-allowed}
button.ghost{padding:8px 16px;border:1px solid #d9d9d9;border-radius:7px;background:#fff;color:#555;cursor:pointer}
button.ghost:disabled{opacity:.5;cursor:not-allowed}
.q-card{border:1px solid #f0f0f0;border-radius:9px;padding:10px 12px;margin-bottom:10px}
.q-head{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.q-why{color:#999;margin:5px 0 0;font-size:12px}
.q-suggest{margin:5px 0 0;padding:7px 9px;background:#fafafa;border-radius:6px;font-size:12px;line-height:1.6}
.q-source{color:#999}
.q-actions{display:flex;align-items:center;gap:14px;margin-top:8px;flex-wrap:wrap}
.radio{display:inline-flex;align-items:center;gap:5px;cursor:pointer}
.q-actions textarea,.spec-summary,.spec-revise input{width:100%;border:1px solid #e5e5e5;border-radius:6px;padding:6px 8px;font-size:12px;font-family:inherit}
.q-actions textarea{flex:1 1 100%}
.spec-toolbar{display:flex;align-items:center;gap:10px;margin-bottom:8px}
.dirty-tip{color:#d46b08;font-size:12px}
.spec-revise{display:flex;gap:8px;margin-bottom:12px}
.spec-revise input{flex:1}
.spec-tree{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:10px}
.spec-node{border:1px solid #f0f0f0;border-radius:9px;padding:9px 11px}
.spec-line{display:flex;align-items:center;gap:6px;flex-wrap:wrap}
.spec-title{flex:1;min-width:140px;border:1px solid #e5e5e5;border-radius:6px;padding:5px 8px;font-size:13px;font-weight:600}
.spec-summary{margin:7px 0 5px;resize:vertical}
.spec-meta{display:flex;gap:16px;color:#777;font-size:12px;align-items:center}
.spec-meta input{width:70px;border:1px solid #e5e5e5;border-radius:5px;padding:3px 5px;margin-left:4px}
.spec-charts{display:flex;flex-direction:column;gap:5px;margin:7px 0}
.chart-row{display:flex;gap:6px;align-items:center}
.chart-row select,.chart-row input{border:1px solid #e5e5e5;border-radius:5px;padding:4px 6px;font-size:12px}
.chart-row input{flex:1}
.pick-list{list-style:none;margin:0 0 10px;padding:0;max-height:300px;overflow:auto;display:flex;flex-direction:column;gap:4px}
.mode-row{display:flex;gap:18px;margin:10px 0 14px}
.writing-head{display:flex;align-items:center;gap:12px;margin-bottom:8px}
.event-log{max-height:180px;overflow:auto;border:1px solid #f0f0f0;border-radius:8px;padding:8px 10px;margin-bottom:10px;background:#fafafa}
.log-line{font-size:12px;color:#555;line-height:1.8}
.log-line.ok{color:#389e0d}
.log-line.error{color:#cf1322}
.log-time{color:#bbb;margin-right:8px}
.section-list{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:6px}
.index-pre{white-space:pre-wrap;word-break:break-all;max-height:420px;overflow:auto;font-size:12px;background:#fafafa;padding:10px;border-radius:8px}
.preview-body{max-height:60vh;overflow:auto}
.preview-text{white-space:pre-wrap;font-family:inherit;font-size:12.5px;line-height:1.8;margin:0}
.preview-mermaid{border-left:3px solid #d9d9d9;margin:6px 0;padding:4px 10px;color:#777}
</style>
