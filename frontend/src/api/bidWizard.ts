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
  action?: "answered" | "adopted" | "skipped" | null;
  answer?: string | null;
  effective_answer?: string | null;
}

export interface Wizard {
  id: string;
  project_id: string;
  stage: WizardStage;
  status: string;
  analysis?: Record<string, unknown> | null;
  questionnaire?: { questions?: WizardQuestion[] } | null;
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

export async function saveRequirements(
  wizardId: string,
  answers: { question_id: string; action: "answered" | "adopted" | "skipped"; answer?: string | null }[],
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

export function wizardToken() {
  return getAccessToken();
}
