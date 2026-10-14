import { getAccessToken } from "@/api/client";

const API_BASE = (import.meta.env.VITE_API_BASE || "/api").replace(/\/$/, "");

/** 招标文件解析（tender-parse）API（doc/招标文件解析需求.md §5）。 */

export type TenderParseStatus = "pending" | "running" | "completed" | "failed";

export interface TenderParseRow {
  field: string;
  value: string;
  source: string;
}

/** 六 Tab 行模型；键 = basic/qualification/scoring/technical/commercial/others */
export type TenderParseResult = Record<string, TenderParseRow[]>;

export interface TenderParseDocumentItem {
  document_id: string;
  original_filename: string;
  file_size?: number | null;
  page_count?: number | null;
  word_count?: number | null;
  doc_status?: string | null;
}

export interface TenderParseRecord {
  id: string;
  project_name: string;
  tender_unit?: string | null;
  status: TenderParseStatus;
  stage: string;
  failed_stage?: string | null;
  error_message?: string | null;
  duration_seconds?: number | null;
  created_at: string;
  updated_at: string;
  result?: TenderParseResult | null;
  manual_edit?: TenderParseResult | null;
  edited_at?: string | null;
  documents: TenderParseDocumentItem[];
}

export interface TenderParseListItem {
  id: string;
  project_name: string;
  tender_unit?: string | null;
  status: TenderParseStatus;
  stage: string;
  failed_stage?: string | null;
  error_message?: string | null;
  duration_seconds?: number | null;
  created_at: string;
  updated_at: string;
  file_name?: string | null;
  file_size?: number | null;
}

export interface TenderParseListResponse {
  items: TenderParseListItem[];
  total: number;
  page: number;
  page_size: number;
}

/** 详情页六页签定义（顺序与后端 TAB_ORDER 一致） */
export const TENDER_PARSE_TABS: { key: string; label: string }[] = [
  { key: "basic", label: "基本信息" },
  { key: "qualification", label: "资格条件" },
  { key: "scoring", label: "评标办法" },
  { key: "technical", label: "技术需求（参数）" },
  { key: "commercial", label: "商务要求" },
  { key: "others", label: "其他内容" },
];

async function request<T>(
  method: string,
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

export async function listTenderParseRecords(params: {
  projectName?: string;
  tenderUnit?: string;
  status?: string;
  page?: number;
  pageSize?: number;
}) {
  const query = new URLSearchParams();
  if (params.projectName?.trim()) query.set("project_name", params.projectName.trim());
  if (params.tenderUnit?.trim()) query.set("tender_unit", params.tenderUnit.trim());
  if (params.status) query.set("status", params.status);
  query.set("page", String(params.page ?? 1));
  query.set("page_size", String(params.pageSize ?? 10));
  return request<TenderParseListResponse>(
    "get",
    `/tender-parse/records?${query.toString()}`,
  );
}

export async function uploadTenderParseRecord(file: File) {
  const token = getAccessToken();
  const form = new FormData();
  form.append("file", file);
  const response = await fetch(`${API_BASE}/tender-parse/records`, {
    method: "post",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: form,
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
            : `上传失败（${response.status}）`),
    ) as Error & { status?: number; detail?: unknown };
    error.status = response.status;
    error.detail = detail;
    throw error;
  }
  const payload = (await response.json()) as { record: TenderParseRecord };
  return payload.record;
}

export async function getTenderParseRecord(recordId: string) {
  return request<TenderParseRecord>(
    "get",
    `/tender-parse/records/${encodeURIComponent(recordId)}`,
  );
}

export async function saveTenderParseEdit(recordId: string, result: TenderParseResult) {
  const payload = { result } as unknown as Record<string, unknown>;
  const record = await request<TenderParseRecord>(
    "put",
    `/tender-parse/records/${encodeURIComponent(recordId)}`,
    payload,
  );
  return record;
}

export async function deleteTenderParseRecord(recordId: string) {
  return request<{ deleted: boolean; id: string }>(
    "delete",
    `/tender-parse/records/${encodeURIComponent(recordId)}`,
  );
}

export async function retryTenderParseRecord(recordId: string) {
  return request<TenderParseRecord>(
    "post",
    `/tender-parse/records/${encodeURIComponent(recordId)}/retry`,
  );
}

/** 带鉴权下载原始文件（接口校验 Authorization 头，不能用裸链接） */
export async function downloadTenderParseFile(recordId: string, filename: string) {
  const token = getAccessToken();
  const response = await fetch(
    `${API_BASE}/tender-parse/records/${encodeURIComponent(recordId)}/file`,
    { headers: token ? { Authorization: `Bearer ${token}` } : {} },
  );
  if (!response.ok) {
    let detail: unknown = null;
    try {
      detail = (await response.json())?.detail;
    } catch {
      /* ignore body parse errors */
    }
    throw new Error(typeof detail === "string" ? detail : "文件下载失败");
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename || "招标文件";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

/** 秒数 → 「1分23秒」展示（需求 §2.1 耗时列） */
export function formatDuration(seconds?: number | null): string {
  if (seconds == null || seconds < 0) return "—";
  if (seconds < 60) return `${seconds}秒`;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return rest ? `${minutes}分${rest}秒` : `${minutes}分`;
}
