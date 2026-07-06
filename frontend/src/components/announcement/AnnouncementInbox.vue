<script setup lang="ts">
import { ref, watch } from "vue";
import { useAnnouncementStore } from "@/stores/announcement";
import type { Announcement, AnnouncementSeverity } from "@/types";

/** 顶栏铃铛打开的收件箱抽屉：列出全部公告，区分已读/未读，支持一键已读。 */
const props = defineProps<{ open: boolean }>();
const emit = defineEmits<{ (e: "update:open", v: boolean): void }>();

const store = useAnnouncementStore();
const expandedId = ref<string | null>(null);

watch(
  () => props.open,
  (v) => {
    if (v) {
      void store.fetchInbox();
      expandedId.value = null;
    }
  },
);

function close() {
  emit("update:open", false);
}

function toggle(item: Announcement) {
  if (expandedId.value === item.id) {
    expandedId.value = null;
    return;
  }
  expandedId.value = item.id;
  // 展开未读公告即标记已读
  if (!item.is_read) {
    void store.markReadInInbox(item.id);
  }
}

async function markAll() {
  await store.markAllRead();
}

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleString("zh-CN", { hour12: false });
  } catch {
    return iso;
  }
}

const severityText: Record<AnnouncementSeverity, string> = {
  info: "公告",
  important: "重要",
  urgent: "紧急",
};
const severityColor: Record<AnnouncementSeverity, string> = {
  info: "#1677ff",
  important: "#fa8c16",
  urgent: "#d7041a",
};
</script>

<template>
  <a-drawer
    :open="open"
    title="系统公告"
    placement="right"
    :width="460"
    @close="close"
    @update:open="(v: boolean) => emit('update:open', v)"
  >
    <template #extra>
      <a-button
        size="small"
        type="primary"
        ghost
        :disabled="store.unreadCount === 0"
        @click="markAll"
      >
        全部已读
      </a-button>
    </template>

    <div v-if="store.inboxLoading" class="inbox-state">加载中…</div>

    <div v-else-if="store.inboxItems.length === 0" class="inbox-state">
      暂无公告
    </div>

    <ul v-else class="inbox-list">
      <li
        v-for="item in store.inboxItems"
        :key="item.id"
        class="inbox-item"
        :class="{ 'inbox-item--unread': !item.is_read, 'inbox-item--open': expandedId === item.id }"
        @click="toggle(item)"
      >
        <div class="inbox-item__head">
          <span class="inbox-dot" :style="{ background: severityColor[item.severity] }"></span>
          <span
            class="inbox-tag"
            :style="{ color: severityColor[item.severity], borderColor: severityColor[item.severity] }"
          >
            {{ severityText[item.severity] }}
          </span>
          <span class="inbox-item__title">{{ item.title }}</span>
          <span v-if="!item.is_read" class="inbox-unread-flag">未读</span>
        </div>
        <div class="inbox-item__meta">{{ formatTime(item.published_at) }}</div>
        <div v-if="expandedId === item.id" class="inbox-item__content">
          {{ item.content }}
        </div>
      </li>
    </ul>
  </a-drawer>
</template>

<style scoped>
.inbox-state {
  text-align: center;
  color: #999;
  padding: 48px 0;
  font-size: 14px;
}

.inbox-list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.inbox-item {
  padding: 14px 12px;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.15s ease;
  border-bottom: 1px solid #f3f3f3;
}

.inbox-item:hover {
  background: #fafafa;
}

.inbox-item--open {
  background: #fafafa;
}

.inbox-item__head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.inbox-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex: 0 0 auto;
}

.inbox-tag {
  flex: 0 0 auto;
  display: inline-block;
  padding: 0 6px;
  border: 1px solid currentColor;
  border-radius: 4px;
  font-size: 11px;
  line-height: 18px;
  font-weight: 600;
}

.inbox-item__title {
  flex: 1 1 auto;
  font-size: 14px;
  color: #333;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.inbox-item--unread .inbox-item__title {
  font-weight: 600;
  color: #111;
}

.inbox-unread-flag {
  flex: 0 0 auto;
  font-size: 11px;
  color: #fff;
  background: #d7041a;
  border-radius: 10px;
  padding: 0 7px;
  line-height: 18px;
}

.inbox-item__meta {
  margin-top: 4px;
  margin-left: 16px;
  font-size: 12px;
  color: #999;
}

.inbox-item__content {
  margin-top: 10px;
  padding: 12px 14px;
  background: #fff;
  border: 1px solid #f0f0f0;
  border-radius: 6px;
  font-size: 13px;
  line-height: 1.75;
  color: #444;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
