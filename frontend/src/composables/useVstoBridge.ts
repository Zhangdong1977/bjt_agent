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
    { resolve: (value: VstoBridgeResult) => void; reject: (reason: Error) => void; timer: number }
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
      pending.set(requestId, { resolve, reject, timer });
      post({ ...message, request_id: requestId });
    });
  }

  /** 读取当前 Word 选中文本（插件侧截断至 20000 字）。 */
  function requestSelection() {
    return request({ type: "bjt.vsto.selection.request" }, 15_000);
  }

  /** 在锚点/光标处插入 Markdown；返回 code="snapshot_stale" 时可重试。
   * 全量标书写入按标题逐段进 Word，实测约 4s/标题、60 节 ≈ 6-7 分钟，
   * timeoutMs 必须按内容规模给足（默认 60s 只适合小片段）。
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

  return { available, contextReady, documentContext, requestSelection, insertMarkdown, replaceSelection, postBridge: post };
}
