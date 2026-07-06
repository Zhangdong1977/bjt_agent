import { defineStore } from "pinia";
import { ref, computed } from "vue";
import { announcementApi } from "@/api/client";
import type { Announcement } from "@/types";

/**
 * 系统公告 store。
 *
 * 职责：
 * - 维护顶栏未读角标 ``unreadCount``；
 * - 维护弹窗队列 ``unreadQueue``（进入应用时拉取未读公告逐条弹出）；
 * - 维护收件箱列表 ``inboxItems``（顶栏铃铛打开的抽屉）。
 *
 * 所有变更均以不可变方式更新（返回新数组/新 Set），遵循项目不可变约定。
 * 角标/弹窗失败时静默处理，不阻塞主流程。
 */
export const useAnnouncementStore = defineStore("announcement", () => {
  const unreadCount = ref(0);

  // 收件箱
  const inboxItems = ref<Announcement[]>([]);
  const inboxTotal = ref(0);
  const inboxLoaded = ref(false);
  const inboxLoading = ref(false);

  // 弹窗队列
  const unreadQueue = ref<Announcement[]>([]);
  const popupIndex = ref(0);
  const initialized = ref(false);

  // 本会话内「仅关闭、未标记已读」的公告 id，避免反复弹窗骚扰
  const sessionDismissed = ref<Set<string>>(new Set());

  const currentPopup = computed<Announcement | null>(
    () => unreadQueue.value[popupIndex.value] ?? null,
  );
  const popupVisible = computed(() => currentPopup.value !== null);
  const popupProgress = computed(() => ({
    index: Math.min(popupIndex.value + 1, unreadQueue.value.length),
    total: unreadQueue.value.length,
  }));

  /** 进入应用时调用：拉未读角标 + 弹窗队列。仅执行一次。 */
  async function initialize() {
    if (initialized.value) return;
    initialized.value = true; // 防止并发重复拉取
    await loadPopupQueue();
  }

  /** 拉取未读角标。 */
  async function refreshUnreadCount() {
    try {
      const res = await announcementApi.getUnreadCount();
      unreadCount.value = res.unread_count;
    } catch {
      /* 静默：角标失败不阻塞主流程 */
    }
  }

  /** 拉取未读公告作为弹窗队列（过滤掉本会话已关闭的）。 */
  async function loadPopupQueue() {
    try {
      const res = await announcementApi.list({ unread_only: true, limit: 50 });
      unreadCount.value = res.unread_count;
      unreadQueue.value = res.items.filter(
        (a) => !sessionDismissed.value.has(a.id),
      );
      popupIndex.value = 0;
    } catch {
      /* 静默 */
    }
  }

  /** 拉取收件箱列表（顶栏铃铛打开时）。 */
  async function fetchInbox(force = false) {
    if (inboxLoaded.value && !force) return;
    inboxLoading.value = true;
    try {
      const res = await announcementApi.list({ limit: 100, offset: 0 });
      inboxItems.value = res.items;
      inboxTotal.value = res.total;
      unreadCount.value = res.unread_count;
      inboxLoaded.value = true;
    } finally {
      inboxLoading.value = false;
    }
  }

  /** 把当前弹窗公告标记为已读，并从队列移除、推进到下一条。 */
  async function markCurrentRead() {
    const cur = currentPopup.value;
    if (!cur) return;
    try {
      await announcementApi.markRead(cur.id);
      unreadCount.value = Math.max(0, unreadCount.value - 1);
    } catch {
      /* 即便后端失败也推进队列，避免卡住用户 */
    }
    _removeFromQueue(cur.id);
    _markInboxRead(cur.id);
  }

  /** 跳到下一条未读（不标记已读）。 */
  function nextPopup() {
    if (popupIndex.value < unreadQueue.value.length - 1) {
      popupIndex.value += 1;
    }
  }

  /** 仅关闭当前弹窗（不标记已读，本会话不再弹出）。 */
  function dismissPopup() {
    const cur = currentPopup.value;
    if (!cur) return;
    sessionDismissed.value = new Set([...sessionDismissed.value, cur.id]);
    _removeFromQueue(cur.id);
    // unreadCount 不变（仍未读）
  }

  /** 一键全部已读。 */
  async function markAllRead() {
    await announcementApi.markAllRead();
    unreadCount.value = 0;
    unreadQueue.value = [];
    popupIndex.value = 0;
    inboxItems.value = inboxItems.value.map((a) => ({ ...a, is_read: true }));
  }

  /** 在收件箱里把指定公告标记为已读。 */
  async function markReadInInbox(id: string) {
    try {
      await announcementApi.markRead(id);
      unreadCount.value = Math.max(0, unreadCount.value - 1);
      _markInboxRead(id);
      unreadQueue.value = unreadQueue.value.filter((a) => a.id !== id);
    } catch {
      /* 静默 */
    }
  }

  function reset() {
    unreadCount.value = 0;
    inboxItems.value = [];
    inboxTotal.value = 0;
    inboxLoaded.value = false;
    inboxLoading.value = false;
    unreadQueue.value = [];
    popupIndex.value = 0;
    initialized.value = false;
    sessionDismissed.value = new Set();
  }

  // ---- 内部不可变工具 ----

  function _removeFromQueue(id: string) {
    unreadQueue.value = unreadQueue.value.filter((a) => a.id !== id);
    if (popupIndex.value >= unreadQueue.value.length) {
      popupIndex.value = Math.max(0, unreadQueue.value.length - 1);
    }
  }

  function _markInboxRead(id: string) {
    inboxItems.value = inboxItems.value.map((a) =>
      a.id === id ? { ...a, is_read: true, read_at: new Date().toISOString() } : a,
    );
  }

  return {
    // state
    unreadCount,
    inboxItems,
    inboxTotal,
    inboxLoaded,
    inboxLoading,
    unreadQueue,
    popupIndex,
    initialized,
    // getters
    currentPopup,
    popupVisible,
    popupProgress,
    // actions
    initialize,
    refreshUnreadCount,
    loadPopupQueue,
    fetchInbox,
    markCurrentRead,
    nextPopup,
    dismissPopup,
    markAllRead,
    markReadInInbox,
    reset,
  };
});
