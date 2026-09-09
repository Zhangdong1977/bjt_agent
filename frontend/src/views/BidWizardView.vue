<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from "vue";
import { Modal, Tag } from "ant-design-vue";
import {
  analyzeTenderDocument,
  deleteTenderDocument as apiDeleteTenderDocument,
  archiveWizard,
  cancelWritingTask,
  confirmSpec,
  createWizard,
  deleteWizard,
  estimateIndexCost,
  generateQuestionnaire,
  generateSpec,
  getLatestWritingTask,
  getMaterialIndex,
  getWizard,
  getWizardAccess,
  getWritingSectionContent,
  listMaterials,
  listWizards,
  listWritingSections,
  markSectionWritten,
  materialUploadUrl,
  regenerateWritingSection,
  resetWrittenSections,
  listLatestSections,
  restoreWizard,
  adoptSidebarAnswer as apiAdoptAnswer,
  answerFollowup as apiAnswerFollowup,
  askSidebarQuestion as apiAskQuestion,
  generateFollowups as apiGenerateFollowups,
  type WizardFollowup,
  deleteMaterial as apiDeleteMaterial,
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
  type WizardListItem,
  type WizardMaterial,
  type WizardQuestion,
  type WizardSection,
  type WizardSpecNode,
  type WizardWritingTask,
  type WizardStage,
} from "@/api/bidWizard";
import { documentsApi, projectsApi } from "@/api/client";
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
const STAGE_TITLES: Record<string, string> = Object.fromEntries(
  STAGES.map((stage) => [stage.key, stage.title]),
);

const access = ref<{ enabled: boolean; mode: string } | null>(null);
const wizard = ref<Wizard | null>(null);
const loading = ref(true);
const pageError = ref("");
const busy = ref(""); // 当前阻塞交互的操作名（按钮态）

// =============================================================== 项目列表（§4.0 入口）

const view = ref<"list" | "wizard">("list");
const projectList = ref<{ active: WizardListItem[]; archived: WizardListItem[] } | null>(null);
const newListName = ref("");
const archivedExpanded = ref(false);
const renaming = ref<{ wizardId: string; projectId: string; name: string } | null>(null);

function isWriting(item: WizardListItem): boolean {
  const status = item.latest_writing_task?.status;
  return status === "pending" || status === "running";
}

function formatTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString("zh-CN", {
    hour12: false,
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

async function loadProjectList() {
  projectList.value = await listWizards();
}

/** 进入（或切换到）某个向导：全量重置本地状态后按目标状态恢复（含撰写 SSE 回填）。 */
async function enterWizard(wizardId: string) {
  resetLocalState();
  wizard.value = await getWizard(wizardId);
  view.value = "wizard";
  questionnaire.value = wizard.value.questionnaire?.questions || [];
  for (const question of questionnaire.value) ensureAnswerDraft(question);
  loadSpecFromWizard();
  await Promise.all([loadTenderDoc(), loadMaterials()]);
  startMaterialPoll();
  if (wizard.value.stage === "writing") await resumeWriting();
}

function onCreateProject() {
  if (busy.value) return;
  void withBusy("create", async () => {
    const created = await createWizard({
      project_name: newListName.value.trim() || undefined,
    });
    newListName.value = "";
    await enterWizard(created.id);
  });
}

function onOpenProject(item: WizardListItem) {
  if (busy.value) return;
  void withBusy("open", async () => {
    await enterWizard(item.wizard_id);
  });
}

/** 返回项目列表：不停云端撰写任务（列表以「撰写中」徽标展示，重进自动恢复 SSE）。 */
function onBackToList() {
  if (!wizard.value || busy.value) return;
  resetLocalState();
  wizard.value = null;
  view.value = "list";
  void withBusy("list", async () => {
    await loadProjectList();
  });
}

function onArchiveProject(item: WizardListItem) {
  Modal.confirm({
    title: "归档该项目？",
    content: "归档后从「进行中」收起，可随时恢复继续；数据保留在云端。",
    okText: "归档",
    cancelText: "取消",
    onOk: () =>
      withBusy("list", async () => {
        await archiveWizard(item.wizard_id);
        await loadProjectList();
      }),
  });
}

function onRestoreProject(item: WizardListItem) {
  void withBusy("list", async () => {
    await restoreWizard(item.wizard_id);
    await loadProjectList();
  });
}

function onDeleteProject(item: WizardListItem) {
  Modal.confirm({
    title: "永久删除该项目？",
    content: "素材与生成产物将永久删除、不可恢复；已消耗点数不退。请确认不再需要该项目。",
    okText: "永久删除",
    okType: "danger",
    cancelText: "取消",
    onOk: () =>
      withBusy("list", async () => {
        await deleteWizard(item.wizard_id);
        await loadProjectList();
      }),
  });
}

function startRename(item: WizardListItem) {
  renaming.value = { wizardId: item.wizard_id, projectId: item.project_id, name: item.project_name };
}

function saveRename() {
  if (!renaming.value) return;
  const { projectId, name } = renaming.value;
  const trimmed = name.trim();
  if (!trimmed) {
    pageError.value = "项目名称不能为空";
    return;
  }
  void withBusy("rename", async () => {
    await projectsApi.update(projectId, { name: trimmed.slice(0, 200) });
    renaming.value = null;
    await loadProjectList();
  });
}

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

const tenderDocs = ref<Document[]>([]);
const materials = ref<WizardMaterial[]>([]);
const tenderUploading = ref(false);
const materialUploading = ref(false);
let materialPollTimer: ReturnType<typeof setInterval> | null = null;
let tenderPollTimer: ReturnType<typeof setInterval> | null = null;

async function loadTenderDoc() {
  if (!wizard.value) return;
  try {
    const docs = await documentsApi.list(wizard.value.project_id);
    tenderDocs.value = docs.filter((item) => item.doc_type === "tender");
    if (tenderDocs.value.some((item) => item.status === "pending" || item.status === "parsing")) {
      startTenderPoll();
    }
  } catch {
    /* 入口静默，操作时报错 */
  }
}

function startTenderPoll() {
  stopTenderPoll();
  if (!wizard.value) return;
  const projectId = wizard.value.project_id;
  tenderPollTimer = setInterval(async () => {
    try {
      const docs = await documentsApi.list(projectId);
      tenderDocs.value = docs.filter((item) => item.doc_type === "tender");
      const active = tenderDocs.value.some(
        (item) => item.status === "pending" || item.status === "parsing",
      );
      if (!active) stopTenderPoll();
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
  const files = Array.from(input.files || []);
  input.value = "";
  if (!files.length || !wizard.value) return;
  void withBusy("tender", async () => {
    // 支持多份（正文 + 补遗/澄清等）：逐份上传；任一文件变化后端清空旧解读
    tenderUploading.value = true;
    try {
      for (const file of files) {
        await apiUploadTender(wizard.value!.id, file);
      }
      await Promise.all([refreshWizard(), loadTenderDoc()]);
    } finally {
      tenderUploading.value = false;
    }
  });
}

function onDeleteTenderDocument(documentId: string) {
  if (!wizard.value) return;
  void withBusy("tender", async () => {
    await apiDeleteTenderDocument(wizard.value!.id, documentId);
    await Promise.all([refreshWizard(), loadTenderDoc()]);
  });
}

function onUploadMaterials(event: Event) {
  const input = event.target as HTMLInputElement;
  const files = Array.from(input.files || []);
  input.value = "";
  if (!files.length || !wizard.value) return;
  void withBusy("material", async () => {
    // 上传前预估确认（决策 17a）：token 量级 + 约点数，实际按用量计费
    const estimate = await estimateIndexCost(
      files.reduce((sum, file) => sum + file.size, 0),
    ).catch(() => null);
    let costText = "按实际用量计费";
    if (estimate) {
      costText = `预计索引消耗约 ${(estimate.estimated_tokens / 1000).toFixed(1)} 千 token`;
      if (estimate.estimated_points != null) costText += `（约 ${estimate.estimated_points} 点）`;
    }
    const confirmed = await new Promise<boolean>((resolve) => {
      Modal.confirm({
        title: "确认上传素材？",
        content: `已选 ${files.length} 份素材，${costText}（上传后自动建立索引，费用计入账户）`,
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
        await apiUploadMaterial(wizard.value!.id, file, localCategory.value || null);
      }
      await Promise.all([refreshWizard(), loadMaterials()]);
      startMaterialPoll();
    } finally {
      materialUploading.value = false;
    }
  });
}

// ---- 从客户端选取（M2 决策 33/34：只接文件类 12 分类，classify_name 直传 category）----

const CLIENT_CATEGORIES: { id: string; name: string }[] = [
  { id: "MaterialProgramme", name: "素材-方案" },
  { id: "MaterialGallery", name: "素材-图库" },
  { id: "StandardSpecification", name: "素材-标准规范" },
  { id: "MaterialLetters", name: "素材-函件" },
  { id: "MaterialTemplate", name: "素材-模板" },
  { id: "HistoryDoc", name: "素材-历史模板" },
  { id: "BaseInfo", name: "企业-基本信息" },
  { id: "Organization", name: "企业-组织架构" },
  { id: "Certification", name: "企业-资质证照" },
  { id: "Performance", name: "企业-业绩履历" },
  { id: "Finance", name: "企业-财务报表" },
  { id: "OtherInfo", name: "企业-其他资料" },
];
const CATEGORY_OPTIONS = [{ id: "", name: "不设置分类" }, ...CLIENT_CATEGORIES, { id: "其他", name: "其他" }];

const materialSource = ref<"local" | "client">("local");
const localCategory = ref("");
const clientCategory = ref("");
interface ClientMaterialItem {
  id: string;
  classify_id: string | null;
  classify_name: string | null;
  original_filename: string | null;
  url: string | null;
}
const clientItems = ref<ClientMaterialItem[]>([]);
const clientLoading = ref(false);
const clientError = ref("");
const clientUnavailable = ref(false); // 客户端未启动/插件缺失 → 置灰降级，不阻断
const clientChecked = ref<Set<string>>(new Set());
const clientUploading = ref(false);
const clientProgress = ref({ done: 0, total: 0 });

async function loadClientMaterials() {
  if (!bridge.available.value) {
    clientUnavailable.value = true;
    clientError.value = "需要从 Word 任务面板打开本页才能读取客户端素材库";
    return;
  }
  clientLoading.value = true;
  clientError.value = "";
  clientUnavailable.value = false;
  try {
    const result = await bridge.listClientMaterials(clientCategory.value || undefined);
    if (!result.success) {
      clientUnavailable.value = /客户端/.test(result.error || "");
      throw new Error(result.error || "读取客户端素材库失败");
    }
    const data = (result.data as { items?: ClientMaterialItem[] } | undefined) || {};
    clientItems.value = data.items || [];
  } catch (error) {
    clientError.value = error instanceof Error ? error.message : "读取客户端素材库失败";
    clientItems.value = [];
  } finally {
    clientLoading.value = false;
  }
}

function onClientCategoryChange(id: string) {
  clientCategory.value = id;
  clientChecked.value = new Set();
  void loadClientMaterials();
}

function toggleClientItem(id: string) {
  if (clientChecked.value.has(id)) clientChecked.value.delete(id);
  else clientChecked.value.add(id);
}

function onUploadFromClient() {
  if (!wizard.value || busy.value) return;
  const picked = clientItems.value.filter(
    (item) => clientChecked.value.has(String(item.id)) && item.url,
  );
  if (!picked.length) return;
  const items = picked.map((item) => ({
    id: String(item.id),
    url: item.url!,
    filename: item.original_filename || (item.url || "").split(/[\\/]/).pop() || String(item.id),
  }));
  // 桥列表无文件大小，无法按字节预估（决策 17a 的预估弹窗退化为通用文案）
  Modal.confirm({
    title: "上传所选客户端素材？",
    content: `已选 ${items.length} 份素材，上传后自动建立索引（按实际用量计费，费用计入账户）`,
    okText: "上传并索引",
    cancelText: "取消",
    onOk: () =>
      withBusy("clientMaterial", async () => {
        clientUploading.value = true;
        clientProgress.value = { done: 0, total: items.length };
        try {
          const result = await bridge.uploadClientMaterials(
            items,
            materialUploadUrl(wizard.value!.id),
            wizardToken() || "",
            clientCategory.value
              ? CLIENT_CATEGORIES.find((item) => item.id === clientCategory.value)?.name || undefined
              : undefined,
            {
              timeoutMs: 300_000,
              onProgress: (done, total) => {
                clientProgress.value = { done, total };
              },
            },
          );
          const rows = (result.results as Array<{ filename?: string; success?: boolean; error?: string | null }>) || [];
          if (!rows.length) {
            throw new Error(result.error || "上传失败，请稍后重试");
          }
          const failed = rows.filter((row) => !row.success);
          if (failed.length) {
            pageError.value = `${failed.length}/${rows.length} 份素材上传失败：${failed
              .slice(0, 3)
              .map((row) => row.filename || "")
              .filter(Boolean)
              .join("、")}`;
          }
          clientChecked.value = new Set();
          await Promise.all([refreshWizard(), loadMaterials()]);
          startMaterialPoll();
        } finally {
          clientUploading.value = false;
        }
      }),
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

const tenderReady = computed(
  () =>
    tenderDocs.value.length > 0 &&
    tenderDocs.value.every((item) => item.status === "parsed"),
);
const indexSummary = computed(() => {
  const indexed = materials.value.filter((item) => item.index_status === "indexed").length;
  const indexing = materials.value.filter(
    (item) => item.index_status === "pending" || item.index_status === "indexing",
  ).length;
  const failed = materials.value.filter((item) => item.index_status === "failed").length;
  return { indexed, indexing, failed };
});

/** AI 招标解读（§4.2：解读后驱动用户补素材）。 */
interface SuggestedMaterial {
  name: string;
  reason?: string;
}
const analysis = computed<Record<string, unknown> | null>(
  () => (wizard.value?.analysis as Record<string, unknown>) || null,
);
/** 解读 basic 全部字段逐行 key:value（联调反馈：比挑几个字段平铺更易读）。 */
const analysisBasicEntries = computed<[string, string][]>(() => {
  const raw = analysis.value?.basic;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return [];
  return Object.entries(raw as Record<string, unknown>)
    .filter(([, value]) => value !== null && value !== undefined && String(value).trim() !== "")
    .map(([key, value]) => [String(key).trim(), String(value).trim()] as [string, string])
    .filter(([key]) => key !== "")
    .slice(0, 30);
});
function analysisStrings(field: string): string[] {
  const raw = analysis.value?.[field];
  return Array.isArray(raw) ? (raw as unknown[]).map(String).filter(Boolean).slice(0, 50) : [];
}
const analysisRequirements = computed(() => analysisStrings("tender_requirements"));
const analysisScoring = computed(() => analysisStrings("scoring_criteria"));
const analysisRejection = computed(() => analysisStrings("rejection_items"));
const suggestedMaterials = computed<SuggestedMaterial[]>(() => {
  const raw = analysis.value?.suggested_materials;
  if (!Array.isArray(raw)) return [];
  return (raw as SuggestedMaterial[]).filter((item) => item && typeof item.name === "string");
});

function onAnalyzeTender() {
  if (!wizard.value) return;
  if (!tenderReady.value) {
    pageError.value = "招标文件尚未解析完成，请稍候";
    return;
  }
  void withBusy("analysis", async () => {
    wizard.value = await analyzeTenderDocument(wizard.value!.id);
  });
}

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
    // 决策 32b：保存后自动触发缺口检测（非阻塞——检测期间可进入大纲；单轮，已有追问不重测）
    if (!followups.value.length) triggerFollowups();
  });
}

// ---- 多轮追问（决策 32：追问侧栏 + AI 主动反问）----

const followups = computed<WizardFollowup[]>(() => {
  const requirements = wizard.value?.requirements as { followups?: WizardFollowup[] } | null;
  return Array.isArray(requirements?.followups) ? requirements!.followups! : [];
});
const supplementalCount = computed(() => {
  const requirements = wizard.value?.requirements as { supplementals?: unknown[] } | null;
  return Array.isArray(requirements?.supplementals) ? requirements!.supplementals!.length : 0;
});
const followupsLoading = ref(false); // 独立于 busy：检测不阻塞进入大纲（Q19 非阻塞）
const followupDrafts = reactive<Record<string, string>>({});
const qaQuestion = ref("");
const qaHistory = ref<{ question: string; answer: string; adopted: boolean }[]>([]);

function triggerFollowups() {
  if (!wizard.value || followupsLoading.value) return;
  followupsLoading.value = true;
  apiGenerateFollowups(wizard.value.id)
    .then(() => refreshWizard())
    .catch((error) => {
      pageError.value = friendlyError(error, "需求缺口检测失败，可稍后在需求页重试");
    })
    .finally(() => {
      followupsLoading.value = false;
    });
}

function onFollowupAnswer(followup: WizardFollowup, action: "answered" | "skipped") {
  if (!wizard.value) return;
  const answer = (followupDrafts[followup.id] || "").trim();
  if (action === "answered" && !answer) {
    pageError.value = "请输入回答，或选择跳过";
    return;
  }
  void withBusy("followup", async () => {
    wizard.value = await apiAnswerFollowup(wizard.value!.id, followup.id, action, answer || null);
  });
}

function onAskQuestion() {
  if (!wizard.value || !qaQuestion.value.trim()) return;
  void withBusy("qaAsk", async () => {
    const question = qaQuestion.value.trim();
    const result = await apiAskQuestion(wizard.value!.id, question);
    qaHistory.value.unshift({ question, answer: result.answer, adopted: false });
    qaQuestion.value = "";
  });
}

function onAdoptQaAnswer(item: { question: string; answer: string; adopted: boolean }) {
  if (!wizard.value || item.adopted) return;
  void withBusy("qaAdopt", async () => {
    wizard.value = await apiAdoptAnswer(wizard.value!.id, item.question, item.answer);
    item.adopted = true;
  });
}

// =============================================================== 阶段 3：编写大纲

interface EditableSpecNode extends WizardSpecNode {
  _uid: number; // 前端折叠/详情展开状态键，保存时不提交（payload 逐字段映射）
}
let specUidSeed = 0;

const specNodes = ref<EditableSpecNode[]>([]);
const specDirty = ref(false);
const reviseInstruction = ref("");
const collapsedUids = ref<Set<number>>(new Set());
const openDetailUid = ref<number | null>(null);

const canConfirmSpec = computed(() =>
  Boolean(specNodes.value.length && !wizard.value?.spec_stale),
);

const specTotalWords = computed(() =>
  specNodes.value.reduce((sum, node) => sum + (node.text_count || 0), 0),
);

interface SpecTreeItem {
  uid: number;
  index: number;
  displayId: string;
  node: EditableSpecNode;
  children: SpecTreeItem[];
}

interface SpecRow {
  uid: number;
  index: number;
  displayId: string;
  node: EditableSpecNode;
  hasChildren: boolean;
  guides: Array<"line" | "blank" | "elbow-mid" | "elbow-last">;
}

/** 扁平 spec → 树；displayId 按位置实时编号，层级跳变按父级+1 平滑（与后端 _smooth_levels 同口径）。 */
const specTree = computed<SpecTreeItem[]>(() => {
  const items: SpecTreeItem[] = [];
  const stack: SpecTreeItem[] = [];
  const counters: number[] = [];
  let prevLevel = 0;
  specNodes.value.forEach((node, index) => {
    const raw = Math.max(1, Math.floor(node.level || 1));
    const level = raw > prevLevel + 1 ? prevLevel + 1 : raw;
    prevLevel = level;
    counters.length = level;
    counters[level - 1] = (counters[level - 1] || 0) + 1;
    while (stack.length >= level) stack.pop();
    const item: SpecTreeItem = { uid: node._uid, index, displayId: counters.join("."), node, children: [] };
    if (stack.length) stack[stack.length - 1].children.push(item);
    else items.push(item);
    stack.push(item);
  });
  return items;
});

/** 可见树行（折叠的子树整支隐藏）；guides 描述每行左侧的层级导线。 */
const specRows = computed<SpecRow[]>(() => {
  const out: SpecRow[] = [];
  const walk = (list: SpecTreeItem[], prefix: SpecRow["guides"]) => {
    list.forEach((item, i) => {
      const isLast = i === list.length - 1;
      const elbow = isLast ? "elbow-last" : "elbow-mid";
      out.push({
        uid: item.uid,
        index: item.index,
        displayId: item.displayId,
        node: item.node,
        hasChildren: item.children.length > 0,
        // 根级不画肘形连接线（无父可连），子级在祖先导线后接自己的 ├─/└─
        guides: prefix.length ? [...prefix, elbow] : [],
      });
      if (item.children.length && !collapsedUids.value.has(item.uid)) {
        walk(item.children, [...prefix, isLast ? "blank" : "line"]);
      }
    });
  };
  walk(specTree.value, []);
  return out;
});

function loadSpecFromWizard() {
  specNodes.value = (wizard.value?.spec || []).map((node) => ({
    ...node,
    charts: node.charts ? node.charts.map((chart) => ({ ...chart })) : null,
    _uid: (specUidSeed += 1),
  }));
  collapsedUids.value = new Set();
  openDetailUid.value = null;
  specDirty.value = false;
}

function markSpecDirty() {
  specDirty.value = true;
}

function toggleCollapse(uid: number) {
  const next = new Set(collapsedUids.value);
  if (!next.delete(uid)) next.add(uid);
  collapsedUids.value = next;
}

function setAllCollapsed(collapsed: boolean) {
  collapsedUids.value = collapsed
    ? new Set(specTree.value.filter((item) => item.children.length).map((item) => item.uid))
    : new Set();
}

function toggleDetail(uid: number) {
  openDetailUid.value = openDetailUid.value === uid ? null : uid;
}

function addNode(afterIndex: number, level: number) {
  const node: EditableSpecNode = {
    node_id: "",
    title: "新章节",
    level: Math.max(1, level),
    summary: "",
    article_count: 2,
    text_count: 400,
    charts: null,
    _uid: (specUidSeed += 1),
  };
  specNodes.value.splice(afterIndex + 1, 0, node);
  openDetailUid.value = node._uid;
  markSpecDirty();
}

/** 删除整个子树（父章连子章一起删，避免旧实现残留深层级子章挂错父级）。 */
function removeNode(index: number) {
  const end = subtreeEndOf(index);
  const count = end - index + 1;
  const doRemove = () => {
    specNodes.value.splice(index, count);
    markSpecDirty();
  };
  if (count <= 1) {
    doRemove();
    return;
  }
  Modal.confirm({
    title: "删除章节及其子章节？",
    content: `「${specNodes.value[index].title}」下还有 ${count - 1} 个子章节，将一并删除。`,
    okText: "删除",
    okType: "danger",
    cancelText: "取消",
    onOk: () => doRemove(),
  });
}

/** ↑/↓：与上/下一个同级章节整体交换位置（整棵子树随行）。 */
function moveNode(index: number, delta: number) {
  const parent = parentIndexOf(index);
  const end = subtreeEndOf(index);
  const size = end - index + 1;
  if (delta < 0) {
    let sibling = -1;
    for (let i = index - 1; i >= 0; i -= 1) {
      const p = parentIndexOf(i);
      if (p === parent) {
        sibling = i;
        break;
      }
      if (parent >= 0 && p < parent) break;
    }
    if (sibling < 0) return;
    const block = specNodes.value.splice(index, size);
    specNodes.value.splice(sibling, 0, ...block);
  } else {
    let sibling = -1;
    for (let i = end + 1; i < specNodes.value.length; i += 1) {
      if (parentIndexOf(i) === parent) {
        sibling = i;
        break;
      }
    }
    if (sibling < 0) return;
    const siblingEnd = subtreeEndOf(sibling);
    const block = specNodes.value.splice(index, size);
    specNodes.value.splice(siblingEnd - size + 1, 0, ...block);
  }
  markSpecDirty();
}

// ---- 拖拽排序（§4.2：同级章节拖拽；跨级移动不在 M1）----
let dragIndex = -1;
const dragOverIndex = ref(-1);
const dragOverPos = ref<"before" | "after">("before");

/** 节点的父节点下标（最近的更浅层级节点；顶级返回 -1）。 */
function parentIndexOf(index: number): number {
  const level = specNodes.value[index].level;
  if (level <= 1) return -1;
  for (let i = index - 1; i >= 0; i -= 1) {
    if (specNodes.value[i].level < level) return i;
  }
  return -1;
}

/** 节点子树的最后一个下标（含自身）。 */
function subtreeEndOf(index: number): number {
  const level = specNodes.value[index].level;
  let end = index;
  for (let i = index + 1; i < specNodes.value.length && specNodes.value[i].level > level; i += 1) {
    end = i;
  }
  return end;
}

function onSpecDragStart(index: number, event: DragEvent) {
  dragIndex = index;
  if (event.dataTransfer) {
    event.dataTransfer.setData("text/plain", String(index)); // Firefox 需 setData 才触发拖拽
    event.dataTransfer.effectAllowed = "move";
  }
}

function onSpecDragOver(targetIndex: number, event: DragEvent) {
  if (dragIndex < 0 || dragIndex === targetIndex) return;
  if (parentIndexOf(dragIndex) !== parentIndexOf(targetIndex)) return; // 仅同级
  event.preventDefault();
  if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
  const rect = (event.currentTarget as HTMLElement).getBoundingClientRect();
  dragOverPos.value = event.clientY < rect.top + rect.height / 2 ? "before" : "after";
  dragOverIndex.value = targetIndex;
}

function onSpecDragLeave() {
  dragOverIndex.value = -1;
}

function onSpecDrop(targetIndex: number, event: DragEvent) {
  const from = dragIndex;
  dragIndex = -1;
  dragOverIndex.value = -1;
  event.preventDefault();
  if (from < 0 || from === targetIndex) return;
  if (parentIndexOf(from) !== parentIndexOf(targetIndex)) return;
  const rect = (event.currentTarget as HTMLElement).getBoundingClientRect();
  const before = event.clientY < rect.top + rect.height / 2;
  // 「拖到下半区」= 放到目标节点子树之后（仍与其同级）
  let insertAt = before ? targetIndex : subtreeEndOf(targetIndex) + 1;
  const [moved] = specNodes.value.splice(from, 1);
  if (from < insertAt) insertAt -= 1;
  specNodes.value.splice(insertAt, 0, moved);
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
const anchorLost = ref(false); // 插入锚点失效（书签被删/文档结构变化）→ 显示「重新定位插入点」
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

/** 撰写页章节勾选：空集合=默认全选（§4.2），物化后才允许提交（onStartWriting 消费）。 */
function specNodeIds(): string[] {
  return (wizard.value?.spec || []).map((node) => node.node_id);
}

function toggleNode(nodeId: string) {
  // 空集合时视觉上是全选：首次点击视为取消勾选该章，先物化全部再删
  if (!selectedNodes.value.size) selectedNodes.value = new Set(specNodeIds());
  if (!selectedNodes.value.delete(nodeId)) selectedNodes.value.add(nodeId);
}

function onStartWriting() {
  if (!wizard.value) return;
  const nodeIds = selectedNodes.value.size ? Array.from(selectedNodes.value) : specNodeIds();
  if (!nodeIds.length) {
    pageError.value = "请至少勾选一个要撰写的章节";
    return;
  }
  if (!bridge.available.value) {
    pageError.value = "撰写需要 Word 插件环境，请从 Word 的任务面板打开本页";
    return;
  }
  void withBusy("writing", async () => {
    // §5.6 初始锚点：点「开始撰写」时在 Word 光标处打锚点书签，逐章插入都锚定其后
    const anchored = await bridge.createBookmark(ANCHOR_BOOKMARK);
    if (!anchored.success) {
      throw new Error(anchored.error || "创建插入锚点失败，请在 Word 中点击插入起始位置后重试");
    }
    anchorLost.value = false;
    const created = await createWritingTask(wizard.value!.id, nodeIds);
    trackTask(created);
  });
}

/** 锚点失效的软降级（§8）：在 Word 光标处重建锚点书签，然后补写待写入队列。 */
async function repositionAnchor() {
  if (busy.value) return;
  writeError.value = "";
  try {
    const result = await bridge.createBookmark(ANCHOR_BOOKMARK);
    if (!result.success) throw new Error(result.error || "重建插入锚点失败，请重试");
    anchorLost.value = false;
    pushLog("ok", "已重新定位插入点，继续写入待写章节");
    const queue = [...awaitingWrite.value];
    for (const nodeId of queue) {
      await writeSection(nodeId);
    }
  } catch (error) {
    writeError.value = error instanceof Error ? error.message : "重建插入锚点失败，请重试";
  }
}

function trackTask(next: WizardWritingTask) {
  stopTaskStream();
  stopTaskPoll();
  taskId.value = next.id;
  task.value = next;
  sections.value = [];
  awaitingWrite.value = [];
  writeError.value = "";
  anchorLost.value = false;
  writingNode.value = "";
  eventLog.value = [];
  pushLog("info", next.continue_of ? "单章重生成任务已提交" : "撰写任务已提交");
  void primeTaskSectionsThenListen(next.id);
  startTaskPoll();
}

/** 先回填章节终态再开流：SSE 无 Last-Event-ID 会从 0 全量回放，已 written 章节必须先已知，
 * 幂等防护才能跳过（否则重开页面会把已写章节重复插入 Word）；顺带把已生成未写入章节
 * 入队补写（§4.3 断点续作：撰写中重开页面继续写入"已生成未写入"章节）。 */
async function primeTaskSectionsThenListen(id: string) {
  try {
    for (const row of await listWritingSections(id)) upsertSection(row);
    if (writeMode.value === "auto") {
      for (const item of sections.value.filter((section) => section.status === "generated")) {
        enqueueWrite(item.node_id);
      }
      pumpWriteQueue();
    }
  } catch {
    /* 回填失败：交由 5s 轮询与 SSE 回放补齐 */
  }
  void listenTask(id);
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
  stopTaskStream();
  const controller = new AbortController();
  streamController = controller;
  let lastEventId = "";
  // §4.3 断线重连：记录服务端 id: 行，重连带 Last-Event-ID 增量回放；
  // 任务终态即停；5s 轮询兜底仍在跑
  while (!controller.signal.aborted && taskRunning.value) {
    try {
      const headers: Record<string, string> = { Accept: "text/event-stream" };
      const token = wizardToken();
      if (token) headers.Authorization = `Bearer ${token}`;
      if (lastEventId) headers["Last-Event-ID"] = lastEventId;
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
          const lines = block.split("\n");
          const idLine = lines.find((item) => item.startsWith("id:"));
          if (idLine) lastEventId = idLine.slice(3).trim();
          const line = lines.find((item) => item.startsWith("data:"));
          if (!line) return;
          try {
            handleTaskEvent(JSON.parse(line.slice(5).trim()) as Record<string, unknown>);
          } catch {
            /* ignore replay noise */
          }
        });
      }
    } catch {
      /* 退避后重连；轮询兜底仍在跑 */
    }
    if (controller.signal.aborted || !taskRunning.value) break;
    await new Promise((resolve) => setTimeout(resolve, 2_000));
  }
  if (streamController === controller) streamController = null;
}

function handleTaskEvent(event: Record<string, unknown>) {
  const type = String(event.type || "");
  if (type === "status") {
    const status = String(event.status || "");
    if (status === "completed" || status === "failed" || status === "cancelled") void refreshTask();
  } else if (type === "phase") {
    pushLog("info", `开始逐章撰写（共 ${event.section_total || "?"} 章）`);
  } else if (type === "section_started") {
    const nodeId = String(event.node_id || "");
    // SSE 重放幂等：written 是终态，不被旧的 started 事件降级（否则紧随的 completed 防护会失效）
    if (sections.value.some((item) => item.node_id === nodeId && item.status === "written")) return;
    upsertSection({
      node_id: nodeId,
      title: String(event.title || event.node_id || ""),
      status: "generating",
    });
  } else if (type === "section_completed") {
    const nodeId = String(event.node_id || "");
    // SSE 重放（刷新/HMR 重挂载后从头回放）幂等保护：已写入是终态，不降级、不重复写入
    if (sections.value.some((item) => item.node_id === nodeId && item.status === "written")) return;
    upsertSection({
      node_id: nodeId,
      title: String(event.title || ""),
      status: "generated",
      word_count: Number(event.word_count || 0),
    });
    pushLog("ok", `完成章节 ${event.node_id} ${displayTitle(String(event.node_id), String(event.title || ""))}`);
    if (writeMode.value === "auto") {
      enqueueWrite(nodeId);
      pumpWriteQueue();
    } else awaitingWrite.value.push(nodeId);
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
      for (const node of pending) enqueueWrite(node);
      pumpWriteQueue();
    }
  } catch (error) {
    if (!quiet) pageError.value = friendlyError(error, "读取撰写任务失败");
  }
}

/** 写入请求入队（去重；确认模式待写队列与自动模式串行队列共用）。 */
function enqueueWrite(nodeId: string) {
  if (!awaitingWrite.value.includes(nodeId)) awaitingWrite.value.push(nodeId);
}

/** 自动模式串行推进待写队列：写入空闲时取队首开写；确认模式/写入中/无任务不动。 */
function pumpWriteQueue() {
  if (writeMode.value !== "auto" || writingNode.value || !taskId.value || !awaitingWrite.value.length) return;
  void writeSectionForRow(taskId.value, awaitingWrite.value[0]);
}

/** 把一章写入 Word（书签锚点推进 + 章首/章尾书签对 + 图表预处理 + written 回报）。 */
async function writeSection(nodeId: string) {
  return writeSectionForRow(taskId.value, nodeId);
}

/** 「重新写入 Word」批量动作可跨任务消费各章最新内容（决策 31），故接受行级 task_id。 */
async function writeSectionForRow(rowTaskId: string, nodeId: string) {
  if (!rowTaskId) return;
  if (writingNode.value) {
    // 正在写别的章：当前任务的同章请求排队等 pump（此前直接 return 会把章节静默丢掉，
    // 首章大插入耗时数分钟期间完成的章节全部漏写）；跨任务批量重写串行 await 不会走到这里。
    if (rowTaskId === taskId.value) enqueueWrite(nodeId);
    return;
  }
  writingNode.value = nodeId;
  writeError.value = "";
  let ok = false;
  try {
    const content = await getWritingSectionContent(rowTaskId, nodeId);
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
        anchorLost.value = true;
        writeError.value = "Word 插入锚点失效（文档结构变化或书签被删）；请重新定位插入点后继续写入";
      } else {
        writeError.value = result.error || "写入 Word 失败";
      }
      if (rowTaskId === taskId.value) enqueueWrite(nodeId); // 失败保留在队列（不自动重试，防死循环）
      return;
    }
    await markSectionWritten(rowTaskId, nodeId);
    upsertSection({ node_id: nodeId, status: "written" });
    pushLog("ok", `已写入 Word：${displayTitle(nodeId, content.title)}（Ctrl+Z 可撤销本章）`);
    const index = awaitingWrite.value.indexOf(nodeId);
    if (index >= 0) awaitingWrite.value.splice(index, 1);
    ok = true;
  } catch (error) {
    writeError.value = friendlyError(error, "写入 Word 失败");
    if (rowTaskId === taskId.value) enqueueWrite(nodeId); // 失败保留在队列（不自动重试，防死循环）
  } finally {
    writingNode.value = "";
    if (ok) pumpWriteQueue(); // 成功后串行写下一章；失败停在队首等下一事件/手动恢复
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
      anchorBookmark: ANCHOR_BOOKMARK, // 锚点落在被删范围内时由插件推进到新章末尾
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
const generatedCount = computed(() => sections.value.filter((item) => item.status === "generated").length);

/** 移除全部 AI 内容（决策 31）：插件书签范围整删（单一撤销单元）→ 服务端 written 回退。 */
function onRemoveAllSections() {
  if (!wizard.value || !taskId.value || taskRunning.value || busy.value) return;
  Modal.confirm({
    title: "移除全部 AI 撰写内容？",
    content: "将删除所有 AI 撰写章节及你在其中的手工修改（一次 Ctrl+Z 可整体恢复）。移除后可「重新写入 Word」，也可在新的 Word 文档中重写整本。",
    okText: "移除全部",
    okType: "danger",
    cancelText: "取消",
    onOk: () =>
      withBusy("writing", async () => {
        const result = await bridge.sectionsRemove("AIWIZ_", "移除 AI 撰写内容");
        if (!result.success) {
          throw new Error(result.error || "移除失败，请稍后重试");
        }
        const removed = Number(result.removed) || 0;
        const missing = Number(result.missing) || 0;
        if (removed === 0) {
          pageError.value =
            missing > 0
              ? "未移除任何章节（章节书签缺失），Word 内容可能已被手动清理"
              : "没有可移除的 AI 章节";
          return;
        }
        await resetWrittenSections(wizard.value!.id);
        await refreshTask();
        pushLog(
          "ok",
          `已移除 ${removed} 章 AI 内容${missing ? `（${missing} 章书签缺失未动）` : ""}；可「重新写入 Word」或在新文档中重写`,
        );
      }),
  });
}

/** 「重新写入 Word」批量动作（决策 31）：锚点重建到当前光标，按权威清单顺序重写待写入章节
 *（不调 LLM、不计费，复用既有逐章写入；亦覆盖"换一份新 Word 文档整本重写"场景）。 */
function onRewriteAllSections() {
  if (!wizard.value || taskRunning.value || busy.value || !bridge.available.value) return;
  void withBusy("writing", async () => {
    const rows = await listLatestSections(wizard.value!.id);
    const writable = rows.filter((row) => row.status === "generated");
    if (!writable.length) {
      pageError.value = "没有待写入的章节";
      return;
    }
    const anchored = await bridge.createBookmark(ANCHOR_BOOKMARK);
    if (!anchored.success) {
      throw new Error(anchored.error || "创建插入锚点失败，请先在 Word 中点击插入起始位置后重试");
    }
    anchorLost.value = false;
    pushLog("info", `开始重新写入 ${writable.length} 章（不调用 AI、不计费）`);
    for (const row of writable) {
      if (anchorLost.value) break; // 锚点失效时停下，走「重新定位插入点」恢复
      await writeSectionForRow(row.task_id, row.node_id);
    }
  });
}

// =============================================================== 装配与生命周期

async function bootstrap() {
  loading.value = true;
  try {
    access.value = await getWizardAccess();
    if (!access.value.enabled) return;
    // 决策 27：入口=项目列表页，不再自动恢复最近活动向导
    view.value = "list";
    await loadProjectList();
  } catch {
    /* 列表加载失败：留在列表态显示错误 */
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

/** 清空页面的全部本地向导状态（切向导前调用，防旧向导数据残留显示）。 */
function resetLocalState() {
  stopTenderPoll();
  stopMaterialPoll();
  stopTaskStream();
  stopTaskPoll();
  tenderDocs.value = [];
  materials.value = [];
  questionnaire.value = [];
  for (const key of Object.keys(answerDrafts)) delete answerDrafts[key];
  specNodes.value = [];
  specDirty.value = false;
  reviseInstruction.value = "";
  selectedNodes.value = new Set();
  taskId.value = "";
  task.value = null;
  sections.value = [];
  eventLog.value = [];
  awaitingWrite.value = [];
  writingNode.value = "";
  writeError.value = "";
  anchorLost.value = false;
  pageError.value = "";
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
      <button
        v-if="view === 'wizard' && wizard"
        type="button"
        class="ghost new-project-btn"
        :disabled="Boolean(busy)"
        title="返回项目列表（进行中的撰写任务继续在云端执行，重进自动恢复）"
        @click="onBackToList"
      >项目列表</button>
    </header>

    <div v-if="loading" class="wiz-loading">正在进入 AI编标…</div>

    <template v-else-if="access && !access.enabled">
      <section class="guide-card">
        <h2>AI编标 功能即将开放</h2>
        <p>四阶段智能编写向导（素材准备 → 需求确认 → 编写大纲 → 逐章撰写）正在内测中，敬请期待。</p>
      </section>
    </template>

    <!-- ==================================================== 项目列表（§4.0 入口） -->
    <template v-else-if="view === 'list'">
      <div v-if="pageError" class="wiz-error" role="alert">{{ pageError }}</div>
      <section class="stage-panel">
        <div class="card">
          <h3>新建编标项目</h3>
          <div class="new-project-row">
            <input
              v-model="newListName"
              type="text"
              maxlength="200"
              placeholder="项目名称（可选，默认「AI编标 + 日期」，上传招标文件后自动改用文件名）"
              :disabled="Boolean(busy)"
              @keydown.enter="onCreateProject"
            >
            <button type="button" class="primary" :disabled="Boolean(busy)" @click="onCreateProject">
              {{ busy === "create" ? "创建中…" : "新建项目" }}
            </button>
          </div>
          <p class="hint">AI 将引导你完成：上传招标文件与公司素材 → 确认编写需求 → 制定编写大纲 → 逐章撰写并写入 Word。</p>
        </div>

        <div class="card">
          <h3>进行中{{ projectList ? `（${projectList.active.length}）` : "" }}</h3>
          <p v-if="projectList && !projectList.active.length" class="hint">还没有进行中的编标项目，先新建一个吧。</p>
          <ul v-else class="project-list">
            <li v-for="item in projectList?.active || []" :key="item.wizard_id" class="project-row">
              <template v-if="renaming && renaming.wizardId === item.wizard_id">
                <input
                  v-model="renaming.name"
                  class="rename-input"
                  maxlength="200"
                  :disabled="busy === 'rename'"
                  @keydown.enter="saveRename"
                  @keydown.esc="renaming = null"
                >
                <button type="button" class="link-btn" :disabled="Boolean(busy)" @click="saveRename">保存</button>
                <button type="button" class="link-btn" :disabled="Boolean(busy)" @click="renaming = null">取消</button>
              </template>
              <template v-else>
                <div class="project-main" :title="`继续：${item.project_name}`" @click="onOpenProject(item)">
                  <span class="doc-name">{{ item.project_name }}</span>
                  <Tag color="default">{{ STAGE_TITLES[item.stage] || item.stage }}</Tag>
                  <Tag v-if="isWriting(item)" color="blue">撰写中</Tag>
                </div>
                <span class="project-meta">
                  <span v-if="item.tender_filename" class="project-tender" :title="item.tender_filename">{{ item.tender_filename }}</span>
                  <span class="project-time">{{ formatTime(item.updated_at) }}</span>
                </span>
                <span class="project-actions">
                  <button type="button" class="link-btn" :disabled="Boolean(busy)" @click="onOpenProject(item)">继续</button>
                  <button type="button" class="link-btn" :disabled="Boolean(busy)" @click="startRename(item)">重命名</button>
                  <button type="button" class="link-btn danger" :disabled="Boolean(busy) || isWriting(item)" @click="onArchiveProject(item)">归档</button>
                </span>
              </template>
            </li>
          </ul>
        </div>

        <div v-if="projectList && projectList.archived.length" class="card">
          <button type="button" class="link-btn archived-toggle" @click="archivedExpanded = !archivedExpanded">
            已归档（{{ projectList.archived.length }}）{{ archivedExpanded ? "▲ 收起" : "▼ 展开" }}
          </button>
          <ul v-if="archivedExpanded" class="project-list">
            <li v-for="item in projectList.archived" :key="item.wizard_id" class="project-row archived">
              <div class="project-main">
                <span class="doc-name">{{ item.project_name }}</span>
                <Tag color="default">{{ STAGE_TITLES[item.stage] || item.stage }}</Tag>
              </div>
              <span class="project-meta">
                <span v-if="item.tender_filename" class="project-tender" :title="item.tender_filename">{{ item.tender_filename }}</span>
                <span class="project-time">{{ formatTime(item.updated_at) }}</span>
              </span>
              <span class="project-actions">
                <button type="button" class="link-btn" :disabled="Boolean(busy)" @click="onRestoreProject(item)">恢复</button>
                <button type="button" class="link-btn danger" :disabled="Boolean(busy)" @click="onDeleteProject(item)">删除</button>
              </span>
            </li>
          </ul>
          <p class="hint">恢复＝回到归档前进度继续编标；删除＝素材与生成产物永久清除、不可恢复（已消耗点数不退）。</p>
        </div>
      </section>
    </template>

    <template v-else-if="wizard">
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
          <h3>① 招标文件（必传一份，可多份）</h3>
          <div v-for="doc in tenderDocs" :key="doc.id" class="doc-line">
            <span class="doc-name">{{ doc.original_filename }}</span>
            <Tag :color="doc.status === 'parsed' ? 'green' : doc.status === 'failed' ? 'red' : 'blue'">
              {{ doc.status === "parsed" ? "解析完成" : doc.status === "failed" ? "解析失败" : "解析中…" }}
            </Tag>
            <button
              type="button"
              class="link-btn"
              :disabled="tenderUploading || Boolean(busy)"
              @click="onDeleteTenderDocument(doc.id)"
            >删除</button>
          </div>
          <label class="upload-btn" :class="{ disabled: tenderUploading || Boolean(busy) }">
            {{
              tenderUploading
                ? "上传中…"
                : tenderDocs.length
                  ? "继续添加招标文件（补遗 / 澄清等，可多选）"
                  : "点击上传招标文件（PDF / Word / Excel，可多选）"
            }}
            <input type="file" multiple accept=".pdf,.docx,.doc,.xlsx" hidden :disabled="tenderUploading || Boolean(busy)" @change="onUploadTender">
          </label>
          <p class="hint">支持多份招标文件（正文、补遗书、澄清/修改文件等），格式 PDF / Word / Excel；AI 解读将合并全部文件内容。</p>
        </div>

        <div v-if="tenderReady" class="card">
          <h3>AI 招标解读</h3>
          <template v-if="analysis">
            <table v-if="analysisBasicEntries.length" class="kv-table">
              <tbody>
                <tr v-for="[key, value] in analysisBasicEntries" :key="key">
                  <th>{{ key }}</th>
                  <td>{{ value }}</td>
                </tr>
              </tbody>
            </table>
            <div v-if="analysisRequirements.length" class="analysis-group">
              <strong>关键要求</strong>
              <ul class="analysis-reqs">
                <li v-for="(item, index) in analysisRequirements" :key="index">{{ item }}</li>
              </ul>
            </div>
            <div v-if="analysisScoring.length" class="analysis-group">
              <strong>评分标准</strong>
              <ul class="analysis-reqs">
                <li v-for="(item, index) in analysisScoring" :key="index">{{ item }}</li>
              </ul>
            </div>
            <div v-if="analysisRejection.length" class="analysis-group">
              <strong>废标风险条款</strong>
              <ul class="analysis-reqs">
                <li v-for="(item, index) in analysisRejection" :key="index">{{ item }}</li>
              </ul>
            </div>
            <div v-if="suggestedMaterials.length" class="analysis-suggest">
              <strong>根据招标要素建议补充：</strong>
              <span
                v-for="(item, index) in suggestedMaterials"
                :key="index"
                class="suggest-chip"
                :title="item.reason || ''"
              >{{ item.name }}</span>
            </div>
            <p
              v-if="!analysisBasicEntries.length && !analysisRequirements.length && !analysisScoring.length && !analysisRejection.length && !suggestedMaterials.length"
              class="hint"
            >解读结果较简略，可直接进入下一步。</p>
            <button type="button" class="link-btn" :disabled="Boolean(busy)" @click="onAnalyzeTender">重新解读</button>
          </template>
          <template v-else>
            <p class="hint">解读招标要素（项目/预算/截止/关键要求），并给出建议补充的素材清单，帮你把素材备齐。</p>
            <button type="button" class="primary" :disabled="Boolean(busy)" @click="onAnalyzeTender">
              {{ busy === "analysis" ? "AI 正在解读（最长 5 分钟）…（招标文件较大时耗时较长，请勿关闭页面）" : "AI 解读招标文件" }}
            </button>
          </template>
        </div>

        <div class="card">
          <h3>② 公司素材（素材池）</h3>
          <div class="material-summary">
            <span>已索引 {{ indexSummary.indexed }}</span>
            <span v-if="indexSummary.indexing">索引中 {{ indexSummary.indexing }}</span>
            <span v-if="indexSummary.failed" class="danger">失败 {{ indexSummary.failed }}</span>
          </div>
          <div class="src-tabs">
            <button type="button" :class="{ active: materialSource === 'local' }" @click="materialSource = 'local'">本地上传</button>
            <button type="button" :class="{ active: materialSource === 'client' }" @click="materialSource = 'client'; loadClientMaterials()">从客户端选取</button>
          </div>

          <template v-if="materialSource === 'local'">
            <div class="local-cat-row">
              <span class="local-cat-label">素材分类</span>
              <select v-model="localCategory" :disabled="materialUploading || Boolean(busy)">
                <option v-for="option in CATEGORY_OPTIONS" :key="option.id" :value="option.id">{{ option.name }}</option>
              </select>
            </div>
            <label class="upload-btn" :class="{ disabled: materialUploading || Boolean(busy) }">
              {{ materialUploading ? "上传中…" : "点击上传素材（可多选）" }}
              <input type="file" multiple accept=".pdf,.docx,.doc,.xlsx,.txt,.md" hidden :disabled="materialUploading || Boolean(busy)" @change="onUploadMaterials">
            </label>
          </template>

          <template v-else>
            <div v-if="clientError" class="client-error">
              {{ clientError }}
              <button v-if="clientUnavailable" type="button" class="link-btn" :disabled="clientLoading || Boolean(busy)" @click="loadClientMaterials">重试</button>
            </div>
            <template v-if="!clientUnavailable">
              <div class="cat-chips">
                <button
                  type="button"
                  class="cat-chip"
                  :class="{ active: clientCategory === '' }"
                  :disabled="clientLoading || clientUploading"
                  @click="onClientCategoryChange('')"
                >全部</button>
                <button
                  v-for="category in CLIENT_CATEGORIES"
                  :key="category.id"
                  type="button"
                  class="cat-chip"
                  :class="{ active: clientCategory === category.id }"
                  :disabled="clientLoading || clientUploading"
                  @click="onClientCategoryChange(category.id)"
                >{{ category.name }}</button>
              </div>
              <p v-if="clientLoading" class="hint">正在读取客户端素材库…</p>
              <p v-else-if="!clientItems.length && !clientError" class="hint">该分类下暂无素材。可先在标捷通客户端的素材库里添加。</p>
              <ul v-else class="client-list">
                <li v-for="item in clientItems" :key="item.id" class="client-row" :class="{ checked: clientChecked.has(String(item.id)) }">
                  <label class="radio">
                    <input
                      type="checkbox"
                      :checked="clientChecked.has(String(item.id))"
                      :disabled="clientUploading"
                      @change="toggleClientItem(String(item.id))"
                    >
                    <span class="doc-name">{{ item.original_filename || item.url }}</span>
                  </label>
                  <Tag v-if="item.classify_name" color="default">{{ item.classify_name }}</Tag>
                </li>
              </ul>
              <div class="stage-actions">
                <button type="button" class="ghost" :disabled="clientLoading || clientUploading" @click="loadClientMaterials">刷新列表</button>
                <button
                  type="button"
                  class="primary"
                  :disabled="!clientChecked.size || clientUploading || Boolean(busy)"
                  @click="onUploadFromClient"
                >
                  {{ clientUploading ? `上传中 ${clientProgress.done}/${clientProgress.total}…` : `上传所选素材（${clientChecked.size} 份）` }}
                </button>
              </div>
            </template>
          </template>

          <ul class="material-list">
            <li v-for="item in materials" :key="item.id" class="material-row">
              <span class="doc-name">{{ item.original_filename }}</span>
              <Tag v-if="item.category" color="default">{{ item.category }}</Tag>
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

        <!-- AI 主动反问（决策 32b）：保存需求后自动检测，非阻塞、可跳过、单轮 -->
        <div v-if="followups.length" class="card">
          <h3>AI 追问（回答后并入编写需求，可跳过）</h3>
          <div v-for="followup in followups" :key="followup.id" class="q-card followup-card">
            <div class="q-head">
              <strong>{{ followup.question }}</strong>
              <Tag v-if="followup.status === 'answered'" color="green">已回答</Tag>
              <Tag v-else-if="followup.status === 'skipped'" color="default">已跳过</Tag>
            </div>
            <p v-if="followup.why" class="q-why">为什么问：{{ followup.why }}</p>
            <div v-if="!followup.status" class="q-actions">
              <textarea
                v-model="followupDrafts[followup.id]"
                rows="2"
                placeholder="输入你的回答"
                :disabled="Boolean(busy)"
              />
              <button type="button" class="link-btn" :disabled="Boolean(busy)" @click="onFollowupAnswer(followup, 'answered')">提交回答</button>
              <button type="button" class="link-btn" :disabled="Boolean(busy)" @click="onFollowupAnswer(followup, 'skipped')">跳过</button>
            </div>
          </div>
        </div>
        <div v-else-if="followupsLoading" class="card">
          <p class="hint">AI 正在检查编写需求的缺口（不阻塞，可直接进入下一步）…</p>
        </div>

        <div class="card">
          <template v-if="!questionnaire.length">
            <p class="hint">AI 将根据招标要素与素材索引生成结构化问卷（约 6-15 题），每题尽量附带素材依据的建议答案。</p>
            <button type="button" class="primary" :disabled="Boolean(busy) || !tenderReady" @click="onGenerateQuestionnaire">
              {{ busy === "questionnaire" ? "AI 正在生成问卷（最长 5 分钟）…（素材较多时耗时较长，请勿关闭页面）" : "生成问卷" }}
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
        <!-- 追问侧栏（决策 32a）：自由提问，采纳并入编写需求 -->
        <div class="card">
          <h3>向 AI 追问</h3>
          <p class="hint" style="margin:0 0 8px">基于招标要素与已索引素材回答你的提问；采纳的问答会并入编写需求（{{ supplementalCount }} 条补充说明），供大纲与撰写参考。每次提问按问答微任务计费。</p>
          <div class="qa-input-row">
            <input
              v-model="qaQuestion"
              type="text"
              maxlength="2000"
              placeholder="例如：我们的业绩案例该怎么在标书里突出？"
              :disabled="Boolean(busy) || !tenderReady"
              @keydown.enter="onAskQuestion"
            >
            <button type="button" class="primary" :disabled="Boolean(busy) || !tenderReady || !qaQuestion.trim()" @click="onAskQuestion">
              {{ busy === "qaAsk" ? "AI 回答中（最长 2 分钟）…" : "提问" }}
            </button>
          </div>
          <div v-for="(item, index) in qaHistory" :key="index" class="qa-item">
            <p class="qa-q">问：{{ item.question }}</p>
            <p class="qa-a">{{ item.answer }}</p>
            <button v-if="!item.adopted" type="button" class="link-btn" :disabled="Boolean(busy)" @click="onAdoptQaAnswer(item)">采纳并入需求</button>
            <Tag v-else color="green">已并入</Tag>
          </div>
        </div>

        <div v-if="questionnaire.length" class="stage-actions">
          <button type="button" class="ghost" :disabled="Boolean(busy)" @click="gotoStage(0)">上一步</button>
          <div class="action-group">
            <button type="button" class="ghost" :disabled="Boolean(busy)" @click="onSubmitRequirements">保存需求</button>
            <button
              type="button"
              class="primary"
              :disabled="Boolean(busy) || wizard.requirements === null"
              @click="gotoStage(2)"
            >进入编写大纲</button>
          </div>
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
              {{ busy === "spec" ? "AI 正在生成大纲（最长 5 分钟）…（素材较多时耗时较长，请勿关闭页面）" : "生成编写大纲" }}
            </button>
          </template>
          <template v-else>
            <div class="spec-toolbar">
              <button type="button" class="ghost" :disabled="Boolean(busy) || !specDirty" @click="onSaveSpec">保存修改</button>
              <button type="button" class="ghost" :disabled="Boolean(busy) || !wizard.spec_previous" @click="onRollbackSpec">回退 AI 修订</button>
              <button type="button" class="ghost" @click="setAllCollapsed(false)">全部展开</button>
              <button type="button" class="ghost" @click="setAllCollapsed(true)">全部收起</button>
              <span v-if="specDirty" class="dirty-tip">有未保存的修改</span>
              <span class="spec-stat">{{ specNodes.length }} 章 · 约 {{ specTotalWords.toLocaleString() }} 字</span>
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
            <p class="hint" style="margin:0 0 8px">同级章节可拖拽排序（目标行上半区=插到它前面，下半区=插到它含子树之后）；点「编辑」展开摘要与图表计划，▾/▸ 折叠子树。</p>
            <div class="spec-tree" role="tree">
              <div
                v-for="row in specRows"
                :key="row.uid"
                class="spec-node"
                :class="{ open: openDetailUid === row.uid }"
              >
                <div
                  class="spec-row"
                  :class="{
                    'drop-before': dragOverIndex === row.index && dragOverPos === 'before',
                    'drop-after': dragOverIndex === row.index && dragOverPos === 'after',
                  }"
                  draggable="true"
                  @dragstart="onSpecDragStart(row.index, $event)"
                  @dragover="onSpecDragOver(row.index, $event)"
                  @dragleave="onSpecDragLeave"
                  @drop="onSpecDrop(row.index, $event)"
                  @dragend="onSpecDragLeave"
                >
                  <span v-for="(guide, gi) in row.guides" :key="gi" class="guide" :class="guide"></span>
                  <button
                    type="button"
                    class="twist"
                    :class="{ leaf: !row.hasChildren }"
                    :disabled="!row.hasChildren"
                    :title="row.hasChildren ? (collapsedUids.has(row.uid) ? '展开子树' : '折叠子树') : ''"
                    @click="toggleCollapse(row.uid)"
                  >{{ collapsedUids.has(row.uid) ? "▸" : "▾" }}</button>
                  <span class="spec-num">{{ row.displayId }}</span>
                  <input
                    v-model="row.node.title"
                    class="spec-title"
                    @input="markSpecDirty"
                    @focus="openDetailUid = row.uid"
                  >
                  <span class="spec-row-meta">
                    {{ row.node.text_count || 0 }}字<template v-if="row.node.charts && row.node.charts.length"> · 图{{ row.node.charts.length }}</template>
                  </span>
                  <span class="row-actions">
                    <button type="button" class="link-btn" title="上移（含子树）" @click="moveNode(row.index, -1)">↑</button>
                    <button type="button" class="link-btn" title="下移（含子树）" @click="moveNode(row.index, 1)">↓</button>
                    <button type="button" class="link-btn" @click="addNode(subtreeEndOf(row.index), row.node.level + 1)">加子章</button>
                    <button type="button" class="link-btn" @click="addNode(subtreeEndOf(row.index), row.node.level)">加同级</button>
                    <button type="button" class="link-btn danger" @click="removeNode(row.index)">删</button>
                    <button type="button" class="link-btn" @click="toggleDetail(row.uid)">{{ openDetailUid === row.uid ? "收起" : "编辑" }}</button>
                  </span>
                </div>
                <div v-if="openDetailUid === row.uid" class="spec-detail">
                  <textarea
                    v-model="row.node.summary"
                    class="spec-summary"
                    rows="2"
                    placeholder="本章摘要（写作纲领：回应什么、引用哪些素材）"
                    @input="markSpecDirty"
                  />
                  <div class="spec-meta">
                    <label>段落数 <input v-model.number="row.node.article_count" type="number" min="1" max="8" @input="markSpecDirty"></label>
                    <label>每段字数 <input v-model.number="row.node.text_count" type="number" min="100" max="3000" step="50" @input="markSpecDirty"></label>
                  </div>
                  <div v-if="row.node.charts && row.node.charts.length" class="spec-charts">
                    <div v-for="(chart, chartIndex) in row.node.charts" :key="chartIndex" class="chart-row">
                      <select v-model="chart.type" @change="markSpecDirty">
                        <option value="table">表格</option>
                        <option value="mermaid">图示</option>
                      </select>
                      <input v-model="chart.title" placeholder="图表标题" @input="markSpecDirty">
                      <input v-model="chart.points" placeholder="要点" @input="markSpecDirty">
                      <button type="button" class="link-btn danger" @click="row.node.charts?.splice(chartIndex, 1); markSpecDirty()">删</button>
                    </div>
                  </div>
                  <button type="button" class="link-btn" @click="addChart(row.node)">＋图表计划</button>
                </div>
              </div>
            </div>
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
            <p class="hint">点「开始撰写」会在 Word 当前光标处建立插入锚点——请先在 Word 中点击要开始插入的位置。</p>
          </template>
          <template v-else>
            <div class="writing-head">
              <Tag :color="task.status === 'completed' ? 'green' : task.status === 'failed' ? 'red' : taskRunning ? 'blue' : 'default'">
                {{ { pending: "排队中", running: "撰写中", completed: "已完成", failed: "失败", cancelled: "已取消" }[task.status] || task.status }}
              </Tag>
              <span>已写入 {{ writtenCount }} / {{ sections.length }} 章</span>
              <button v-if="taskRunning" type="button" class="ghost" :disabled="Boolean(busy)" @click="onCancelTask">取消撰写</button>
            </div>
            <div v-if="!taskRunning && (generatedCount > 0 || writtenCount > 0)" class="writing-tools">
              <button
                v-if="generatedCount > 0"
                type="button"
                class="link-btn"
                :disabled="Boolean(busy)"
                @click="onRewriteAllSections"
              >重新写入 Word（{{ generatedCount }} 章待写入）</button>
              <button
                v-if="writtenCount > 0"
                type="button"
                class="link-btn danger"
                :disabled="Boolean(busy)"
                @click="onRemoveAllSections"
              >移除全部 AI 内容</button>
            </div>
            <div v-if="writeError" class="wiz-error">
              {{ writeError }}
              <button v-if="anchorLost" type="button" class="link-btn" :disabled="Boolean(busy)" @click="repositionAnchor">重新定位插入点</button>
            </div>
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
.new-project-btn{margin-left:auto;padding:6px 14px;border:1px solid #d9d9d9;border-radius:7px;background:#fff;color:#555;cursor:pointer;font-size:12px}
.new-project-btn:hover:not(:disabled){border-color:#d7041a;color:#d7041a}
.new-project-btn:disabled{opacity:.5;cursor:not-allowed}
.wiz-logo{width:96px;object-fit:contain}
.wiz-title h1{margin:0;font-size:18px}
.bridge-state{color:#888;font-size:12px}
.wiz-loading{padding:60px 16px;text-align:center;color:#888}
.wiz-error{margin:8px 16px;padding:9px 12px;border:1px solid #ffccc7;border-radius:7px;background:#fff1f0;color:#c23b3b;font-size:12px;line-height:1.6}
.guide-card{margin:40px auto;padding:28px;width:min(460px,calc(100vw - 28px));background:#fff;border:1px solid #e8e8e8;border-top:3px solid #d7041a;border-radius:12px;text-align:center}
.guide-card h2{margin:0 0 10px;font-size:17px}
.guide-card p{color:#777;line-height:1.7;margin:0 0 18px}
.new-project-row{display:flex;gap:8px}
.new-project-row input{flex:1;border:1px solid #e5e5e5;border-radius:6px;padding:7px 10px;font-size:13px}
.new-project-row input:focus{outline:none;border-color:#d7041a}
.project-list{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:8px}
.project-row{display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:10px 12px;border:1px solid #f0f0f0;border-radius:9px}
.project-row.archived{background:#fafafa}
.project-main{display:flex;align-items:center;gap:8px;flex:1;min-width:0;cursor:pointer}
.project-row.archived .project-main{cursor:default}
.project-meta{display:flex;align-items:center;gap:10px;color:#999;font-size:12px}
.project-tender{max-width:130px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.project-actions{display:flex;gap:2px;flex-shrink:0}
.rename-input{flex:1;border:1px solid #e5e5e5;border-radius:6px;padding:5px 8px;font-size:13px}
.rename-input:focus{outline:none;border-color:#d7041a}
.archived-toggle{font-size:13px;margin:2px 0 8px}
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
.src-tabs{display:flex;gap:6px;margin-bottom:10px;border-bottom:1px solid #f0f0f0}
.src-tabs button{padding:7px 14px;border:0;border-bottom:2px solid transparent;background:transparent;color:#888;cursor:pointer;font-size:13px}
.src-tabs button.active{color:#d7041a;border-bottom-color:#d7041a;font-weight:600}
.local-cat-row{display:flex;align-items:center;gap:8px;margin-bottom:8px;color:#777;font-size:12px}
.local-cat-row select{border:1px solid #e5e5e5;border-radius:6px;padding:5px 8px;font-size:12px;max-width:180px}
.cat-chips{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px}
.cat-chip{padding:3px 11px;border:1px solid #e8e8e8;border-radius:999px;background:#fff;color:#666;cursor:pointer;font-size:12px}
.cat-chip.active{border-color:#d7041a;color:#d7041a;background:#fff5f4}
.cat-chip:disabled{opacity:.5;cursor:not-allowed}
.client-error{padding:8px 12px;border:1px solid #ffccc7;border-radius:7px;background:#fff1f0;color:#c23b3b;font-size:12px;line-height:1.7}
.client-list{list-style:none;margin:0 0 10px;padding:0;max-height:260px;overflow:auto;display:flex;flex-direction:column;gap:5px}
.client-row{display:flex;align-items:center;gap:8px;padding:6px 8px;border:1px solid #f0f0f0;border-radius:7px}
.client-row.checked{border-color:#d7041a;background:#fff5f4}
.kv-table{width:100%;border-collapse:collapse;margin:4px 0 8px;font-size:12px}
.kv-table th{width:130px;text-align:left;font-weight:600;color:#555;white-space:nowrap;vertical-align:top;padding:6px 10px;background:#fafafa;border:1px solid #eee}
.kv-table td{color:#333;padding:6px 10px;border:1px solid #eee;line-height:1.7;word-break:break-all}
.analysis-group{margin:6px 0 8px;font-size:12px}
.analysis-group>strong{display:block;margin-bottom:2px;color:#555}
.analysis-reqs{margin:4px 0 8px;padding-left:18px;color:#666;font-size:12px;line-height:1.8}
.analysis-suggest{display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin:4px 0 8px;font-size:12px}
.suggest-chip{padding:2px 10px;border:1px solid #ffd6d0;border-radius:999px;background:#fff5f4;color:#b80015;cursor:default}
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
.followup-card{border-color:#ffe58f;background:#fffbe6}
.qa-input-row{display:flex;gap:8px}
.qa-input-row input{flex:1;border:1px solid #e5e5e5;border-radius:6px;padding:7px 10px;font-size:13px}
.qa-item{border-top:1px dashed #f0f0f0;margin-top:10px;padding-top:10px}
.qa-q{margin:0 0 4px;font-weight:600;font-size:12.5px}
.qa-a{margin:0 0 6px;white-space:pre-wrap;color:#555;line-height:1.7;font-size:12.5px}
.action-group{display:flex;gap:10px}
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
.spec-tree{margin:0;display:flex;flex-direction:column}
.spec-node{border-radius:8px}
.spec-node.open{border:1px solid #f0f0f0;background:#fafbfc}
.spec-row{display:flex;align-items:center;gap:4px;min-height:34px;padding:2px 8px 2px 2px;border-radius:7px}
.spec-row:hover{background:#f7f9fb}
.spec-node.open .spec-row{background:#fff}
.spec-row.drop-before{box-shadow:inset 0 2px 0 #d7041a}
.spec-row.drop-after{box-shadow:inset 0 -2px 0 #d7041a}
.guide{flex:none;width:18px;align-self:stretch;position:relative}
.guide.line::before{content:"";position:absolute;left:8px;top:0;bottom:0;border-left:1px solid #d9dee8}
.guide.blank::before{content:none}
.guide.elbow-mid::before{content:"";position:absolute;left:8px;top:0;bottom:0;border-left:1px solid #d9dee8}
.guide.elbow-last::before{content:"";position:absolute;left:8px;top:0;height:50%;border-left:1px solid #d9dee8}
.guide.elbow-mid::after,.guide.elbow-last::after{content:"";position:absolute;left:8px;top:50%;width:10px;border-top:1px solid #d9dee8}
.twist{flex:none;width:18px;height:18px;border:none;background:none;cursor:pointer;color:#888;font-size:11px;line-height:1;padding:0}
.twist.leaf{visibility:hidden}
.twist:not(.leaf):hover{color:#333}
.spec-num{flex:none;min-width:34px;color:#888;font-size:12px;font-variant-numeric:tabular-nums}
.spec-title{flex:1;min-width:140px;border:1px solid transparent;border-radius:6px;padding:5px 8px;font-size:13px;font-weight:600;background:transparent}
.spec-title:hover{border-color:#e5e5e5}
.spec-title:focus{border-color:#bbb;background:#fff;outline:none}
.spec-row-meta{flex:none;color:#999;font-size:11.5px;white-space:nowrap}
.row-actions{display:none;gap:2px;flex:none;align-items:center}
.spec-row:hover .row-actions,.spec-node.open .row-actions{display:flex}
.spec-detail{margin:0 8px 8px 46px;border-top:1px dashed #e8e8e8;padding:8px 2px 0}
.spec-stat{color:#999;font-size:12px;margin-left:auto;white-space:nowrap}
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
