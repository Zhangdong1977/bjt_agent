import apiClient, { getAccessToken } from "@/api/client";

const API_BASE = (import.meta.env.VITE_API_BASE || "/api").replace(/\/$/, "");

export interface BidDraftOutlineNode {
  node_id: string;
  title: string;
  level: number;
  requirement?: string | null;
  article_count?: number;
  text_count?: number;
}

export interface BidDraftTask {
  id: string;
  project_id: string;
  tender_document_id: string | null;
  status: string;
  phase: string | null;
  analysis_result: Record<string, unknown> | null;
  outline: BidDraftOutlineNode[] | null;
  generation_options: Record<string, unknown> | null;
  summary: Record<string, unknown> | null;
  continue_of: string | null;
  error_message: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
}

export interface BidDraftSectionMeta {
  node_id: string;
  title: string;
  status: string;
  word_count: number | null;
  attempts: number;
  error_message: string | null;
}

export interface BidDraftSectionContent {
  node_id: string;
  title: string;
  status: string;
  content: string | null;
  word_count: number | null;
}

export interface BidDraftAssembled {
  task_id: string;
  status: string;
  content: string | null;
  word_count: number | null;
  section_total: number;
  section_generated: number;
  section_failed: number;
}

export async function createBidDraftTask(payload: {
  project_id: string;
  tender_document_id: string;
  outline?: BidDraftOutlineNode[];
  analysis?: Record<string, unknown> | null;
  options?: Record<string, unknown> | null;
}) {
  const response = await apiClient.post<BidDraftTask>("/bid-draft/tasks", payload);
  return response.data;
}

export async function getBidDraftTask(taskId: string) {
  const response = await apiClient.get<BidDraftTask>(
    `/bid-draft/tasks/${encodeURIComponent(taskId)}`,
  );
  return response.data;
}

export async function cancelBidDraftTask(taskId: string) {
  const response = await apiClient.post<BidDraftTask>(
    `/bid-draft/tasks/${encodeURIComponent(taskId)}/cancel`,
  );
  return response.data;
}

export async function listBidDraftSections(taskId: string) {
  const response = await apiClient.get<BidDraftSectionMeta[]>(
    `/bid-draft/tasks/${encodeURIComponent(taskId)}/sections`,
  );
  return response.data;
}

export async function getBidDraftSectionContent(taskId: string, nodeId: string) {
  const response = await apiClient.get<BidDraftSectionContent>(
    `/bid-draft/tasks/${encodeURIComponent(taskId)}/sections/${encodeURIComponent(nodeId)}`,
  );
  return response.data;
}

export async function getBidDraftAssembled(taskId: string) {
  const response = await apiClient.get<BidDraftAssembled>(
    `/bid-draft/tasks/${encodeURIComponent(taskId)}/assembled`,
  );
  return response.data;
}

export async function regenerateBidDraftSection(taskId: string, nodeId: string) {
  const response = await apiClient.post<BidDraftTask>(
    `/bid-draft/tasks/${encodeURIComponent(taskId)}/sections/${encodeURIComponent(nodeId)}/regenerate`,
  );
  return response.data;
}

export function bidDraftStreamUrl(taskId: string) {
  return `${API_BASE}/bid-draft/tasks/${encodeURIComponent(taskId)}/stream`;
}

export function bidDraftToken() {
  return getAccessToken();
}
