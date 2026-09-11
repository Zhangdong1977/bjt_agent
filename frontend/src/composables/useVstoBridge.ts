import { onMounted, onUnmounted, ref } from "vue";

/**
 * VSTO WebView2 JSON bridge for the /vsto/* task-pane pages.
 *
 * 复用暗标检查已验证的 `bjt.vsto.*` 协议族，并为标书生成/扩写润色提供
 * 写通道（selection 读取、Markdown 插入、选区替换）。消息均为「页面发、
 * 插件回 result」的 request/response 形态，凭 request_id 关联。
 */

export interface VstoDocumentContext {
  document_name: string;
  document_key: string;
  document_revision: string;
  snapshot_id: string;
}

export interface VstoBridgeResult {
  success: boolean;
  code?: string | null;
  error?: string | null;
  snapshot_id?: string | null;
  [key: string]: unknown;
}

type VstoWindow = Window & {
  chrome?: {
    webview?: {
      postMessage: (value: unknown) => void;
      addEventListener: (type: string, listener: (event: MessageEvent) => void) => void;
      removeEventListener: (type: string, listener: (event: MessageEvent) => void) => void;
    };
  };
};

const RESULT_TYPES = new Set([
  "bjt.vsto.selection.result",
  "bjt.vsto.insert.result",
  "bjt.vsto.selection.replace.result",
  "bjt.vsto.section.replace.result",
  "bjt.vsto.bookmark.create.result",
  "bjt.vsto.material.list.result",
  "bjt.vsto.material.upload.result",
  "bjt.vsto.sections.remove.result",
]);

function newRequestId(prefix: string) {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function useVstoBridge() {
  const available = ref(false);
  const contextReady = ref(false);
  const documentContext = ref<VstoDocumentContext | null>(null);
  const pending = new Map<
    string,
    {
      resolve: (value: VstoBridgeResult) => void;
      reject: (reason: Error) => void;
      timer: number;
      timeoutMs: number;
      onProgress?: (done: number, total: number) => void;
    }
  >();
  let listener: ((event: MessageEvent) => void) | null = null;

  function webview() {
    return (window as VstoWindow).chrome?.webview || null;
  }

  function post(message: Record<string, unknown>) {
    webview()?.postMessage(message);
  }

  function handleMessage(event: MessageEvent) {
    let value: unknown = event.data;
    if (typeof value === "string") {
      try {
        value = JSON.parse(value);
      } catch {
        return;
      }
    }
    if (!value || typeof value !== "object") return;
    const payload = value as Record<string, unknown>;
    const type = String(payload.type || "");

    if (type === "bjt.vsto.context") {
      if (payload.success === false || payload.error) {
        contextReady.value = false;
        return;
      }
      const data = (payload.data || {}) as Record<string, unknown>;
      const next: VstoDocumentContext = {
        document_name: String(data.document_name || ""),
        document_key: String(data.document_key || ""),
        document_revision: String(data.document_revision || ""),
        snapshot_id: String(data.snapshot_id || ""),
      };
      if (next.document_key && next.document_revision && next.snapshot_id) {
        documentContext.value = next;
        contextReady.value = true;
      }
      return;
    }

    if (type === "bjt.vsto.insert.progress" || type === "bjt.vsto.material.upload.progress") {
      const requestId = String(payload.request_id || "");
      const entry = pending.get(requestId);
      if (entry) {
        // 插件仍在逐块写入（全量标书实测 30 分钟量级）：续期超时计时器，
        // 只在"距上次进度 timeoutMs 仍无消息"时才判定插件无响应。
        window.clearTimeout(entry.timer);
        entry.timer = window.setTimeout(() => {
          pending.delete(requestId);
          entry.reject(new Error("Word 插件未响应，请确认插件版本已更新后重试"));
        }, entry.timeoutMs);
        entry.onProgress?.(Number(payload.done) || 0, Number(payload.total) || 0);
      }
      return;
    }

    if (RESULT_TYPES.has(type)) {
      const requestId = String(payload.request_id || "");
      const entry = pending.get(requestId);
      if (!entry) return;
      pending.delete(requestId);
      window.clearTimeout(entry.timer);
      const result: VstoBridgeResult = {
        success: payload.success === true,
        code: payload.code ? String(payload.code) : null,
        error: payload.error ? String(payload.error) : null,
        snapshot_id: payload.snapshot_id ? String(payload.snapshot_id) : null,
      };
      for (const [key, valueOfKey] of Object.entries(payload)) {
        if (!(key in result)) result[key] = valueOfKey;
      }
      entry.resolve(result);
    }
  }

  function request(
    message: Record<string, unknown>,
    timeoutMs = 20_000,
    onProgress?: (done: number, total: number) => void,
  ): Promise<VstoBridgeResult> {
    return new Promise((resolve, reject) => {
      if (!webview()) {
        reject(new Error("Word 插件桥不可用，请从 Word 任务面板打开本页"));
        return;
      }
      const requestId = String(message.request_id || "") || newRequestId("bridge");
      const timer = window.setTimeout(() => {
        pending.delete(requestId);
        reject(new Error("Word 插件未响应，请确认插件版本已更新后重试"));
      }, timeoutMs);
      pending.set(requestId, { resolve, reject, timer, timeoutMs, onProgress });
      post({ ...message, request_id: requestId });
    });
  }

  /** 读取当前 Word 选中文本（插件侧截断至 20000 字）。 */
  function requestSelection() {
    return request({ type: "bjt.vsto.selection.request" }, 15_000);
  }

  /** 在锚点/光标处插入 Markdown；返回 code="snapshot_stale" 时可重试。
   * 全量标书写入按标题逐段进 Word，实测可达 30 分钟（含逐张插图表），timeoutMs
   * 必须按内容规模给足；新插件每写完一块会回 progress 消息续期超时并触发
   * onProgress，静态超时只在插件彻底停摆（无任何进度）时兜底。
   * images：图表附件（"bjt-chart://N" → PNG dataURL），Markdown 中以
   * `![题注](bjt-chart://N)` 独立图片行引用；仅新版插件识别，旧插件按普通文本降级。 */
  function insertMarkdown(
    content: string,
    options: {
      label?: string;
      snapshotId?: string | null;
      anchor?: "cursor" | "end";
      timeoutMs?: number;
      images?: Record<string, string>;
      onProgress?: (done: number, total: number) => void;
    } = {},
  ) {
    return request(
      {
        type: "bjt.vsto.insert",
        content,
        label: options.label || "AI 写入",
        snapshot_id: options.snapshotId ?? null,
        anchor: options.anchor || "cursor",
        ...(options.images && Object.keys(options.images).length ? { images: options.images } : {}),
      },
      options.timeoutMs ?? 60_000,
      options.onProgress,
    );
  }

  /** 校验当前选区与 originalText 一致后替换为 Markdown（一个撤销单元）。 */
  function replaceSelection(originalText: string, content: string, label?: string) {
    return request(
      {
        type: "bjt.vsto.selection.replace",
        original_text: originalText,
        content,
        label: label || "AI 替换",
      },
      60_000,
    );
  }

  /** AI编标：在书签锚点后插入本章 Markdown，插件写完把锚点书签推进到本章末尾，
   * 并按 sectionBookmarks 打章首/章尾书签对（ADR-0001 逐章写入）。
   * 旧插件不认识 anchor_bookmark，按未知字段忽略后走光标插入——发版同步后不存在。 */
  function insertSection(
    content: string,
    options: {
      anchorBookmark: string;
      sectionStartBookmark: string;
      sectionEndBookmark: string;
      label?: string;
      images?: Record<string, string>;
      timeoutMs?: number;
      onProgress?: (done: number, total: number) => void;
    },
  ) {
    return request(
      {
        type: "bjt.vsto.insert",
        content,
        label: options.label || "AI 撰写",
        snapshot_id: null,
        anchor: "bookmark",
        anchor_bookmark: options.anchorBookmark,
        section_bookmarks: {
          start: options.sectionStartBookmark,
          end: options.sectionEndBookmark,
        },
        ...(options.images && Object.keys(options.images).length
          ? { images: options.images }
          : {}),
      },
      options.timeoutMs ?? 10 * 60_000,
      options.onProgress,
    );
  }

  /** AI编标：在当前 Word 光标处创建/重建插入锚点书签（§5.6 初始锚点；
   * 书签被删后的「重新定位插入点」软降级复用同一消息）。 */
  function createBookmark(name: string, options: { timeoutMs?: number } = {}) {
    return request(
      { type: "bjt.vsto.bookmark.create", name },
      options.timeoutMs ?? 15_000,
    );
  }

  /** AI编标：单章重生成的落 Word 路径——按书签对删旧章插新章，同一撤销单元；
   * 书签被用户删除时回 code="bookmark_missing"，页面引导重新定位。
   * anchorBookmark：锚点书签落在被删范围内时由插件推进到新章末尾（可选）。 */
  function sectionReplace(
    startBookmark: string,
    endBookmark: string,
    content: string,
    options: {
      sectionStartBookmark?: string;
      sectionEndBookmark?: string;
      anchorBookmark?: string;
      label?: string;
      images?: Record<string, string>;
      timeoutMs?: number;
    } = {},
  ) {
    return request(
      {
        type: "bjt.vsto.section.replace",
        start_bookmark: startBookmark,
        end_bookmark: endBookmark,
        content,
        label: options.label || "AI 重写本章",
        ...(options.anchorBookmark ? { anchor_bookmark: options.anchorBookmark } : {}),
        ...(options.sectionStartBookmark && options.sectionEndBookmark
          ? {
              section_bookmarks: {
                start: options.sectionStartBookmark,
                end: options.sectionEndBookmark,
              },
            }
          : {}),
        ...(options.images && Object.keys(options.images).length
          ? { images: options.images }
          : {}),
      },
      options.timeoutMs ?? 10 * 60_000,
    );
  }

  /** AI编标 M2：查客户端素材库元数据（分类筛选；插件经 localhost bridgeList 只回元数据）。
   * 返回 data.items；客户端未启动时插件回 success=false + error。 */
  function listClientMaterials(classifyId?: string, options: { timeoutMs?: number } = {}) {
    return request(
      { type: "bjt.vsto.material.list", classify_id: classifyId || null },
      options.timeoutMs ?? 15_000,
    );
  }

  /** AI编标 M2：把勾选的客户端素材逐份读流上传云端素材池（插件带短时 JWT 转发，
   * 逐份回 progress，单份失败不阻断整批；结果在 result.results 里逐份列出）。 */
  function uploadClientMaterials(
    items: Array<{ id: string; url: string; filename: string }>,
    uploadUrl: string,
    authToken: string,
    category?: string,
    options: { timeoutMs?: number; onProgress?: (done: number, total: number) => void } = {},
  ) {
    return request(
      {
        type: "bjt.vsto.material.upload",
        items,
        upload_url: uploadUrl,
        auth_token: authToken,
        category: category || null,
      },
      options.timeoutMs ?? 180_000,
      options.onProgress,
    );
  }

  /** AI编标 M2：移除全部 AI 内容（决策 31）——插件按书签前缀整删全部章节、清残留书签，
   * 单一撤销单元（一次 Ctrl+Z 整体恢复）；书签缺失章节跳过并在结果里计 removed/missing。 */
  function sectionsRemove(sectionPrefix: string, label?: string) {
    return request(
      {
        type: "bjt.vsto.sections.remove",
        section_prefix: sectionPrefix,
        label: label || "移除 AI 撰写内容",
      },
      60_000,
    );
  }

  onMounted(() => {
    const bridge = webview();
    if (!bridge) return;
    available.value = true;
    listener = handleMessage;
    bridge.addEventListener("message", handleMessage);
    post({ type: "bjt.vsto.ready" });
  });

  onUnmounted(() => {
    for (const [, entry] of pending) {
      window.clearTimeout(entry.timer);
      entry.reject(new Error("页面已关闭，操作取消"));
    }
    pending.clear();
    const bridge = webview();
    if (bridge && listener) bridge.removeEventListener("message", listener);
  });

  return {
    available,
    contextReady,
    documentContext,
    requestSelection,
    insertMarkdown,
    replaceSelection,
    insertSection,
    sectionReplace,
    createBookmark,
    listClientMaterials,
    uploadClientMaterials,
    sectionsRemove,
    postBridge: post,
  };
}
