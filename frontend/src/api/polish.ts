import apiClient from "@/api/client";

export type PolishMode = "expand" | "polish" | "abbreviate";

export interface PolishTask {
  id: string;
  mode: PolishMode;
  status: string;
  result_text: string | null;
  error_message: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
}

export async function createPolishTask(payload: {
  mode: PolishMode;
  text: string;
  requirements?: string | null;
  target_length?: number | null;
}) {
  const response = await apiClient.post<PolishTask>("/polish/tasks", payload);
  return response.data;
}

export async function getPolishTask(taskId: string) {
  const response = await apiClient.get<PolishTask>(
    `/polish/tasks/${encodeURIComponent(taskId)}`,
  );
  return response.data;
}

export async function cancelPolishTask(taskId: string) {
  const response = await apiClient.post<PolishTask>(
    `/polish/tasks/${encodeURIComponent(taskId)}/cancel`,
  );
  return response.data;
}
