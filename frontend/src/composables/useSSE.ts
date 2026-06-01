/**
 * useSSE - 统一的 SSE (Server-Sent Events) 连接管理 composable
 *
 * 特性：
 * - 指数退避自动重连（1s → 30s + jitter）
 * - 浏览器原生 Last-Event-ID 支持（后端已支持）
 * - 认证 token 注入
 * - 连接生命周期管理（组件卸载自动断开）
 * - 可选的 requestAnimationFrame 批量处理
 *
 * 使用方式：
 *   const { connect, disconnect, isConnected } = useSSE({
 *     onEvent: (event) => { ... },
 *     onComplete: () => { ... },
 *   })
 *   connect(taskId)
 */

import { ref, onUnmounted } from "vue";
import { getAccessToken } from "@/api/client";

const API_BASE = import.meta.env.VITE_API_BASE || "/api";

/** SSE 重连配置 */
const BASE_RECONNECT_DELAY = 1000; // 1 秒基础延迟
const MAX_RECONNECT_DELAY = 30000; // 30 秒上限
const MAX_RECONNECT_ATTEMPTS = 20; // 最多重连次数
const JITTER_MAX = 500; // 最大随机抖动 (ms)

export interface SSEOptions {
  /** 收到 SSE 事件时的回调 */
  onEvent: (event: any) => void;
  /** 连接成功建立时的回调 */
  onOpen?: () => void;
  /** 连接断开时的回调（不包括主动 disconnect） */
  onError?: (error: Event) => void;
  /** 任务完成/失败时调用，返回 true 表示不应重连 */
  shouldStop?: () => boolean;
  /** 是否启用 requestAnimationFrame 批量处理（默认 false） */
  enableBatching?: boolean;
  /** SSE 端点类型：任务或文档解析（默认 tasks） */
  endpointType?: "tasks" | "documents";
}

export function useSSE(options: SSEOptions) {
  const {
    onEvent,
    onOpen,
    onError,
    shouldStop,
    enableBatching = false,
    endpointType = "tasks",
  } = options;

  const isConnected = ref(false);
  const reconnectAttempts = ref(0);

  let eventSource: EventSource | null = null;
  let currentId: string | null = null;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  // RAF batching state
  let pendingEvents: any[] = [];
  let rafId: number | null = null;

  /**
   * 计算重连延迟（指数退避 + 随机抖动）
   */
  function getReconnectDelay(): number {
    const delay = Math.min(
      BASE_RECONNECT_DELAY * Math.pow(2, reconnectAttempts.value),
      MAX_RECONNECT_DELAY,
    );
    return delay + Math.random() * JITTER_MAX;
  }

  /**
   * 处理批量事件（RAF 节流）
   */
  function flushPendingEvents() {
    const events = pendingEvents;
    pendingEvents = [];
    rafId = null;
    events.forEach((evt) => onEvent(evt));
  }

  /**
   * 构建 SSE URL（含认证 token）
   */
  function buildUrl(id: string): string {
    const token = getAccessToken();
    const base = `${API_BASE}/events/${endpointType}/${id}/stream`;
    return token ? `${base}?token=${encodeURIComponent(token)}` : base;
  }

  /**
   * 断开当前连接，清理所有状态
   */
  function disconnect() {
    // 清理重连定时器
    if (reconnectTimer !== null) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }

    // 清理 RAF
    if (rafId !== null) {
      cancelAnimationFrame(rafId);
      rafId = null;
    }
    pendingEvents = [];

    // 关闭 EventSource
    if (eventSource) {
      eventSource.close();
      eventSource = null;
    }

    isConnected.value = false;
    currentId = null;
  }

  /**
   * 建立 SSE 连接
   *
   * 注意：浏览器原生 EventSource 会在服务器发送 `id:` 字段时
   * 自动在重连时附加 `Last-Event-ID` 请求头。后端已支持此头部
   * 用于断线后的事件回放。因此我们不在 onerror 中调用 close()，
   * 让浏览器自动重连（除非 shouldStop() 返回 true）。
   */
  function connect(id: string) {
    // 先断开已有连接
    disconnect();

    currentId = id;
    reconnectAttempts.value = 0;

    const url = buildUrl(id);
    eventSource = new EventSource(url);

    eventSource.onopen = () => {
      isConnected.value = true;
      reconnectAttempts.value = 0; // 重置重连计数
      onOpen?.();
    };

    eventSource.onmessage = (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data);

        if (enableBatching) {
          pendingEvents.push(data);
          if (!rafId) {
            rafId = requestAnimationFrame(flushPendingEvents);
          }
        } else {
          onEvent(data);
        }
      } catch (err) {
        console.error("[useSSE] Failed to parse event:", err);
      }
    };

    eventSource.onerror = (e: Event) => {
      onError?.(e);

      // 检查是否应该停止重连（任务已完成/失败）
      if (shouldStop?.()) {
        disconnect();
        return;
      }

      // 检查重连次数
      if (reconnectAttempts.value >= MAX_RECONNECT_ATTEMPTS) {
        console.error(
          `[useSSE] Max reconnect attempts (${MAX_RECONNECT_ATTEMPTS}) reached for ${id}`,
        );
        disconnect();
        return;
      }

      // 指数退避重连
      // 注意：EventSource 有浏览器内置的自动重连机制，
      // 但它的重连间隔不可控。我们主动 close + 延迟重建，
      // 以便实现自定义的指数退避策略。
      reconnectAttempts.value++;
      const delay = getReconnectDelay();

      // 先关闭当前 EventSource（阻止浏览器默认自动重连）
      if (eventSource) {
        eventSource.close();
        eventSource = null;
      }
      isConnected.value = false;

      console.log(
        `[useSSE] Reconnecting in ${Math.round(delay)}ms (attempt ${reconnectAttempts.value}/${MAX_RECONNECT_ATTEMPTS}) for ${id}`,
      );

      reconnectTimer = setTimeout(() => {
        reconnectTimer = null;
        if (currentId === id) {
          connect(id);
        }
      }, delay);
    };
  }

  // 组件卸载时自动断开
  onUnmounted(() => {
    disconnect();
  });

  return {
    connect,
    disconnect,
    isConnected,
    reconnectAttempts,
  };
}
