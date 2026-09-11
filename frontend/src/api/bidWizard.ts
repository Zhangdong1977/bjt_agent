import { getAccessToken } from "@/api/client";

const API_BASE = (import.meta.env.VITE_API_BASE || "/api").replace(/\/$/, "");

/** AI编标（bid-wizard）四阶段向导 API（doc/workspace/20-bid-wizard.md §5.2）。 */

export type WizardStage = "material" | "requirement" | "outline" | "writing";

export interface WizardSpecChart {
  type: "table" | "mermaid";
  title: string;
  points?: string | null;
}

export interface WizardSpecNode {
  node_id: string;
  title: string;
  level: number;
  summary?: string | null;
  article_count?: number;
  text_count?: number;
  charts?: WizardSpecChart[] | null;
}

export interface WizardQuestion {
  id: string;
  topic: string;
  question: string;
  why?: string;
  suggested_answer?: string;
  source?: string;
  inferred?: boolean;
  action?: "answered" | "adopted" | "skipped" | "supplemented" | null;
  answer?: string | null;
  effective_answer?: string | null;
  round?: number | null; // ≥2 为追问轮（首轮无此字段视为 1）
}

export interface Wizard {
  id: string;
  project_id: string;
  stage: WizardStage;
  status: string;
  analysis?: Record<string, unknown> | null;
  questionnaire?: {
    questions?: WizardQuestion[];
    followup?: { auto_rounds?: number; status?: "active" | "done" } | null;
  } | null;
  requirements?: { questions?: WizardQuestion[] } | null;
  spec?: WizardSpecNode[] | null;
  spec_previous?: WizardSpecNode[] | null;
  spec_confirmed_at?: string | null;
  requirements_stale: boolean;
  spec_stale: boolean;
  error_message?: string | null;
  created_at: string;
  updated_at: string;
}

export interface WizardMaterial {
  id: string;
  document_id: string;
  category?: string | null;
  index_status: "pending" | "indexing" | "indexed" | "failed";
  indexed_at?: string | null;
  index_error?: string | null;
  chunk_count?: number | null;
  original_filename?: string | null;
  doc_status?: string | null;
  word_count?: number | null;
  page_count?: number | null;
  created_at: string;
}

export interface WizardWritingTask {
  id: string;
  wizard_id: string;
  project_id: string;
  status: string;
  selected_nodes?: string[] | null;
  summary?: Record<string, unknown> | null;
  continue_of?: string | null;
  error_message?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  created_at: string;
}

export interface WizardSection {
  node_id: string;
  title: string;
  summary?: string | null;
  chart_plan?: WizardSpecChart[] | null;
  status: "pending" | "generating" | "generated" | "written" | "failed";
  word_count?: number | null;
  written_at?: string | null;
  attempts: number;
  error_message?: string | null;
}

export interface WizardSectionContent {
  node_id: string;
  title: string;
  status: string;
  content: string | null;
  word_count: number | null;
}

async function request<T>(
  method: "get" | "post" | "put" | "delete",
  path: string,
  body?: unknown,
): Promise<T> {
  const token = getAccessToken();
  const response = await fetch(`${API_BASE}${path}`, {
    method,
    headers: {
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) {
    let detail: unknown = null;
    try {
      detail = (await response.json())?.detail;
    } catch {
      /* ignore body parse errors */
    }
    const error = new Error(
      typeof detail === "string"
        ? detail
        : (detail && typeof detail === "object" && "message" in detail
            ? String((detail as { message?: string }).message)
            : `请求失败（${response.status}）`),
    ) as Error & { status?: number; detail?: unknown };
    error.status = response.status;
    error.detail = detail;
    throw error;
  }
  return (await response.json()) as T;
}

export async function getWizardAccess() {
  return request<{ enabled: boolean; mode: string }>("get", "/bid-wizard/access");
}

export async function estimateIndexCost(bytes: number) {
  return request<{ chars: number; estimated_tokens: number; estimated_points: number | null }>(
    "get",
    `/bid-wizard/estimate?bytes=${encodeURIComponent(bytes)}`,
  );
}

export async function createWizard(payload: { project_id?: string; project_name?: string } = {}) {
  return request<Wizard>("post", "/bid-wizard/wizards", payload);
}

export async function getActiveWizard() {
  return request<Wizard>("get", "/bid-wizard/wizards/active");
}

export async function getWizard(wizardId: string) {
  return request<Wizard>("get", `/bid-wizard/wizards/${encodeURIComponent(wizardId)}`);
}

export async function updateWizardStage(wizardId: string, stage: WizardStage) {
  return request<Wizard>("post", `/bid-wizard/wizards/${encodeURIComponent(wizardId)}/stage`, {
    stage,
  });
}

/** 归档当前向导（数据保留，不再被 /wizards/active 恢复）——项目列表页「归档」动作。 */
export async function archiveWizard(wizardId: string) {
  return request<Wizard>("post", `/bid-wizard/wizards/${encodeURIComponent(wizardId)}/archive`);
}

/** 恢复归档：status 回 active、回归档前 stage 断点（决策 28，可逆动作）。 */
export async function restoreWizard(wizardId: string) {
  return request<Wizard>("post", `/bid-wizard/wizards/${encodeURIComponent(wizardId)}/restore`);
}

/** 删除项目（决策 29）：仅已归档可删；软删 project + 异步清理 workspace；计费流水不动。 */
export async function deleteWizard(wizardId: string) {
  return request<Wizard>("delete", `/bid-wizard/wizards/${encodeURIComponent(wizardId)}`);
}

export interface WizardListItem {
  wizard_id: string;
  project_id: string;
  project_name: string;
  stage: string;
  status: string;
  tender_filename: string | null;
  latest_writing_task: { id: string; status: string } | null;
  updated_at: string;
  created_at: string;
}

/** 项目列表页聚合（§4.0）：进行中 + 已归档两组，updated_at 倒序。 */
export async function listWizards() {
  return request<{ active: WizardListItem[]; archived: WizardListItem[] }>(
    "get",
    "/bid-wizard/wizards",
  );
}

/** multipart 上传（招标文件/素材共用；走 XHR 以便带上进度回调）。 */
function uploadFile(
  path: string,
  file: File,
  extraQuery: Record<string, string> = {},
  onProgress?: (percent: number) => void,
): Promise<WizardMaterial | Wizard> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const formData = new FormData();
    formData.append("file", file);
    const query = new URLSearchParams(extraQuery).toString();
    xhr.open("POST", `${API_BASE}${path}${query ? `?${query}` : ""}`);
    const token = getAccessToken();
    if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && onProgress) {
        onProgress(Math.round((event.loaded / event.total) * 100));
      }
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText));
        } catch (error) {
          reject(error);
        }
      } else {
        let detail = "";
        try {
          const parsed = JSON.parse(xhr.responseText)?.detail;
          detail = typeof parsed === "string" ? parsed : parsed?.message || `上传失败（${xhr.status}）`;
        } catch {
          detail = `上传失败（${xhr.status}）`;
        }
        const error = new Error(detail) as Error & { status?: number };
        error.status = xhr.status;
        reject(error);
      }
    };
    xhr.onerror = () => reject(new Error("网络错误，上传失败"));
    xhr.send(formData);
  });
}

export function uploadTender(wizardId: string, file: File, onProgress?: (p: number) => void) {
  return uploadFile(`/bid-wizard/wizards/${encodeURIComponent(wizardId)}/tender`, file, {}, onProgress);
}

export function uploadMaterial(
  wizardId: string,
  file: File,
  category: string | null,
  onProgress?: (p: number) => void,
) {
  return uploadFile(
    `/bid-wizard/wizards/${encodeURIComponent(wizardId)}/materials`,
    file,
    category ? { category } : {},
    onProgress,
  );
}

export async function deleteTender(wizardId: string) {
  return request<Wizard>("delete", `/bid-wizard/wizards/${encodeURIComponent(wizardId)}/tender`);
}

/** 删除一份招标文件（多文件口径：正文 + 补遗/澄清可并存，逐份删除）。 */
export async function deleteTenderDocument(wizardId: string, documentId: string) {
  return request<Wizard>(
    "delete",
    `/bid-wizard/wizards/${encodeURIComponent(wizardId)}/tender/${encodeURIComponent(documentId)}`,
  );
}

/** AI 解读招标文件（同步微任务）：招标要素 + suggested_materials 建议补素材。 */
export async function analyzeTenderDocument(wizardId: string) {
  return request<Wizard>(
    "post",
    `/bid-wizard/wizards/${encodeURIComponent(wizardId)}/analysis`,
  );
}

export async function listMaterials(wizardId: string) {
  return request<WizardMaterial[]>(
    "get",
    `/bid-wizard/wizards/${encodeURIComponent(wizardId)}/materials`,
  );
}

export async function deleteMaterial(wizardId: string, documentId: string) {
  return request<Wizard>(
    "delete",
    `/bid-wizard/wizards/${encodeURIComponent(wizardId)}/materials/${encodeURIComponent(documentId)}`,
  );
}

export async function getMaterialIndex(wizardId: string, documentId: string) {
  return request<{
    material_id: string;
    index_status: string;
    chunk_count: number | null;
    index_content: string | null;
  }>(
    "get",
    `/bid-wizard/wizards/${encodeURIComponent(wizardId)}/materials/${encodeURIComponent(documentId)}/index`,
  );
}

export async function reindexMaterial(wizardId: string, documentId: string) {
  return request<WizardMaterial>(
    "post",
    `/bid-wizard/wizards/${encodeURIComponent(wizardId)}/materials/${encodeURIComponent(documentId)}/reindex`,
  );
}

export async function generateQuestionnaire(wizardId: string) {
  return request<Wizard>(
    "post",
    `/bid-wizard/wizards/${encodeURIComponent(wizardId)}/questionnaire`,
  );
}

/** 再次检查（反馈⑭）：保留现有问答，基于最新素材索引追加一轮 grill-me 追问。 */
export async function generateQuestionnaireRound(
  wizardId: string,
  trigger: "manual" | "auto" = "manual",
) {
  return request<Wizard>(
    "post",
    `/bid-wizard/wizards/${encodeURIComponent(wizardId)}/questionnaire/round?trigger=${trigger}`,
  );
}

export async function saveRequirements(
  wizardId: string,
  answers: { question_id: string; action: "answered" | "adopted" | "skipped" | "supplemented"; answer?: string | null }[],
) {
  return request<Wizard>(
    "put",
    `/bid-wizard/wizards/${encodeURIComponent(wizardId)}/requirements`,
    { answers },
  );
}

export async function generateSpec(wizardId: string) {
  return request<Wizard>("post", `/bid-wizard/wizards/${encodeURIComponent(wizardId)}/spec`);
}

export async function saveSpec(wizardId: string, spec: WizardSpecNode[]) {
  return request<Wizard>("put", `/bid-wizard/wizards/${encodeURIComponent(wizardId)}/spec`, {
    spec,
  });
}

export async function reviseSpec(wizardId: string, instruction: string) {
  return request<Wizard>(
    "post",
    `/bid-wizard/wizards/${encodeURIComponent(wizardId)}/spec/revise`,
    { instruction },
  );
}

export async function rollbackSpec(wizardId: string) {
  return request<Wizard>("post", `/bid-wizard/wizards/${encodeURIComponent(wizardId)}/spec/rollback`);
}

export async function confirmSpec(wizardId: string) {
  return request<Wizard>(
    "post",
    `/bid-wizard/wizards/${encodeURIComponent(wizardId)}/spec/confirm`,
  );
}

export async function createWritingTask(wizardId: string, nodeIds: string[]) {
  return request<WizardWritingTask>(
    "post",
    `/bid-wizard/wizards/${encodeURIComponent(wizardId)}/writing-tasks`,
    { node_ids: nodeIds },
  );
}

export async function getLatestWritingTask(wizardId?: string) {
  const query = wizardId ? `?wizard_id=${encodeURIComponent(wizardId)}` : "";
  return request<WizardWritingTask>("get", `/bid-wizard/writing-tasks/latest${query}`);
}

export async function getWritingTask(taskId: string) {
  return request<WizardWritingTask>("get", `/bid-wizard/writing-tasks/${encodeURIComponent(taskId)}`);
}

export async function listWritingSections(taskId: string) {
  return request<WizardSection[]>(
    "get",
    `/bid-wizard/writing-tasks/${encodeURIComponent(taskId)}/sections`,
  );
}

export async function getWritingSectionContent(taskId: string, nodeId: string) {
  return request<WizardSectionContent>(
    "get",
    `/bid-wizard/writing-tasks/${encodeURIComponent(taskId)}/sections/${encodeURIComponent(nodeId)}`,
  );
}

export async function markSectionWritten(taskId: string, nodeId: string) {
  return request<{ node_id: string; status: string; written_at: string }>(
    "post",
    `/bid-wizard/writing-tasks/${encodeURIComponent(taskId)}/sections/${encodeURIComponent(nodeId)}/written`,
  );
}

export async function regenerateWritingSection(taskId: string, nodeId: string) {
  return request<WizardWritingTask>(
    "post",
    `/bid-wizard/writing-tasks/${encodeURIComponent(taskId)}/sections/${encodeURIComponent(nodeId)}/regenerate`,
  );
}

export async function cancelWritingTask(taskId: string) {
  return request<WizardWritingTask>(
    "post",
    `/bid-wizard/writing-tasks/${encodeURIComponent(taskId)}/cancel`,
  );
}

export function wizardStreamUrl(taskId: string) {
  return `${API_BASE}/bid-wizard/writing-tasks/${encodeURIComponent(taskId)}/stream`;
}

/** 客户端素材上传端点完整地址（插件桥转发用，§5.6 material.upload）。
 * 由插件 C# HttpClient 直连 POST，必须是绝对地址：生产构建 API_BASE 为相对 `/api`，
 * 按页面 origin 补全；端点与页面本地上传同一条 POST /wizards/{id}/materials。 */
export function materialUploadUrl(wizardId: string) {
  const path = `${API_BASE}/bid-wizard/wizards/${encodeURIComponent(wizardId)}/materials`;
  return new URL(path, window.location.origin).toString();
}

/** 移除全部 AI 内容后的服务端状态回退（决策 31）：written → generated。 */
export async function resetWrittenSections(wizardId: string) {
  return request<{ reset_count: number }>(
    "post",
    `/bid-wizard/wizards/${encodeURIComponent(wizardId)}/sections/reset-written`,
  );
}

/** 跨任务取每个 node 的最新章节行（「重新写入 Word」批量动作的权威清单）。 */
export async function listLatestSections(wizardId: string) {
  return request<
    Array<{ task_id: string; node_id: string; title: string; status: string; word_count: number | null }>
  >("get", `/bid-wizard/wizards/${encodeURIComponent(wizardId)}/sections/latest`);
}

// ---- 多轮追问（决策 32：追问侧栏；主动追问统一到问卷轮机制，反馈⑱）----

/** 追问侧栏提问（每轮一次 bid_wizard_qa 计费）。 */
export async function askSidebarQuestion(wizardId: string, question: string) {
  return request<{ answer: string }>(
    "post",
    `/bid-wizard/wizards/${encodeURIComponent(wizardId)}/qa/ask`,
    { question },
  );
}

/** 采纳追问问答对并入编写需求 supplementals（不计费）。 */
export async function adoptSidebarAnswer(wizardId: string, question: string, answer: string) {
  return request<Wizard>(
    "post",
    `/bid-wizard/wizards/${encodeURIComponent(wizardId)}/qa/adopt`,
    { question, answer },
  );
}

export function wizardToken() {
  return getAccessToken();
}
