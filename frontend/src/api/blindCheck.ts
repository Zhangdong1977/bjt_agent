import apiClient, { getAccessToken } from "@/api/client";

const API_BASE = (import.meta.env.VITE_API_BASE || "/api").replace(/\/$/, "");

export interface VstoToolSession {
  id: string;
  status: string;
  document_name: string | null;
  document_revision: string | null;
  snapshot_id: string | null;
  expires_at: string;
}

export interface BlindCheckScope {
  mode: "whole_document";
  confirmed: boolean;
}

export interface BlindCheckTask {
  id: string;
  tool_session_id: string;
  requirement_text: string;
  document_name: string | null;
  document_revision: string | null;
  snapshot_id: string | null;
  scope: BlindCheckScope | null;
  status: string;
  celery_task_id: string | null;
  summary: Record<string, unknown> | null;
  error_message: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
}

export interface BlindCheckFinding {
  id: string;
  task_id: string;
  category: string;
  severity: string;
  verdict: string;
  title: string;
  description: string;
  evidence_text: string | null;
  page_number: number | null;
  paragraph_index: number | null;
  location: Record<string, unknown> | null;
  rule_reference: string | null;
  confidence: number | null;
}

export interface BlindCheckResults {
  task_id: string;
  status: string;
  summary: Record<string, unknown> | null;
  findings: BlindCheckFinding[];
}

export interface ToolResultPayload {
  tool_session_id: string;
  call_id: string;
  success: boolean;
  data?: Record<string, unknown>;
  content?: string;
  error?: string | null;
  snapshot_id?: string | null;
}

export async function createVstoToolSession(payload: Record<string, unknown> = {}) {
  const response = await apiClient.post<VstoToolSession>("/vsto-tools/sessions", payload);
  return response.data;
}

export async function heartbeatVstoToolSession(sessionId: string) {
  const response = await apiClient.post<VstoToolSession>(`/vsto-tools/sessions/${encodeURIComponent(sessionId)}/heartbeat`);
  return response.data;
}

export async function closeVstoToolSession(sessionId: string) {
  await apiClient.post(`/vsto-tools/sessions/${encodeURIComponent(sessionId)}/close`);
}

export async function createBlindCheckTask(payload: {
  tool_session_id: string;
  requirement_text: string;
  document_name?: string | null;
  document_key?: string | null;
  document_revision?: string | null;
  scope?: BlindCheckScope;
}) {
  const response = await apiClient.post<BlindCheckTask>("/blind-check/tasks", payload);
  return response.data;
}

export async function getBlindCheckResults(taskId: string) {
  const response = await apiClient.get<BlindCheckResults>(`/blind-check/tasks/${encodeURIComponent(taskId)}/results`);
  return response.data;
}

export async function cancelBlindCheckTask(taskId: string) {
  const response = await apiClient.post<BlindCheckTask>(`/blind-check/tasks/${encodeURIComponent(taskId)}/cancel`);
  return response.data;
}

export async function submitVstoToolResult(payload: ToolResultPayload) {
  await apiClient.post("/vsto-tools/results", payload);
}

export function blindCheckStreamUrl(taskId: string) {
  return `${API_BASE}/blind-check/tasks/${encodeURIComponent(taskId)}/stream`;
}

export function blindCheckToken() {
  return getAccessToken();
}
