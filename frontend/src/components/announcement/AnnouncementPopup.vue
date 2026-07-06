<script setup lang="ts">
import { computed } from "vue";
import { useAnnouncementStore } from "@/stores/announcement";
import type { AnnouncementSeverity } from "@/types";

/** 进入应用后自动弹出的未读公告 modal，逐条展示，「我知道了」即标记已读。 */
const store = useAnnouncementStore();

const open = computed(() => store.popupVisible);
const current = computed(() => store.currentPopup);
const progress = computed(() => store.popupProgress);
const hasMore = computed(() => progress.value.total > 1);

const severityLabel: Record<AnnouncementSeverity, string> = {
  info: "公告",
  important: "重要",
  urgent: "紧急",
};

const severityColor: Record<AnnouncementSeverity, string> = {
  info: "#1677ff",
  important: "#fa8c16",
  urgent: "#d7041a",
};

function acknowledge() {
  void store.markCurrentRead();
}

function next() {
  store.nextPopup();
}

/** 点击 X / 遮罩关闭：仅本会话关闭，不标记已读。 */
function dismiss() {
  store.dismissPopup();
}

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleString("zh-CN", { hour12: false });
  } catch {
    return iso;
  }
}
</script>

<template>
  <a-modal
    :open="open"
    :mask-closable="false"
    :closable="true"
    :footer="null"
    :destroy-on-close="false"
    :width="520"
    wrap-class-name="announcement-popup-wrap"
    @cancel="dismiss"
  >
    <template v-if="current">
      <div class="popup-accent" :style="{ background: severityColor[current.severity] }"></div>

      <div class="popup-head">
        <span
          class="popup-tag"
          :style="{
            color: severityColor[current.severity],
            borderColor: severityColor[current.severity],
          }"
        >
          {{ severityLabel[current.severity] }}
        </span>
        <span v-if="hasMore" class="popup-progress">
          {{ progress.index }} / {{ progress.total }}
        </span>
      </div>

      <h3 class="popup-title">{{ current.title }}</h3>
      <div class="popup-meta">{{ formatTime(current.published_at) }}</div>
      <div class="popup-content">{{ current.content }}</div>

      <div class="popup-footer">
        <button v-if="hasMore" class="popup-btn popup-btn--ghost" @click="next">
          下一条
        </button>
        <button
          class="popup-btn popup-btn--primary"
          :style="{ background: severityColor[current.severity] }"
          @click="acknowledge"
        >
          我知道了
        </button>
      </div>
    </template>
  </a-modal>
</template>

<style scoped>
.popup-accent {
  height: 4px;
  border-radius: 8px 8px 0 0;
  margin: -20px -24px 16px;
}

.popup-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.popup-tag {
  display: inline-block;
  padding: 1px 10px;
  border: 1px solid currentColor;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 600;
  line-height: 20px;
}

.popup-progress {
  font-size: 12px;
  color: #999;
}

.popup-title {
  margin: 0 0 4px;
  font-size: 18px;
  font-weight: 700;
  color: #222;
  line-height: 1.4;
}

.popup-meta {
  font-size: 12px;
  color: #999;
  margin-bottom: 14px;
}

.popup-content {
  font-size: 14px;
  line-height: 1.75;
  color: #444;
  white-space: pre-wrap;
  word-break: break-word;
  background: #fafafa;
  border-radius: 8px;
  padding: 14px 16px;
  max-height: 46vh;
  overflow-y: auto;
}

.popup-footer {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  margin-top: 20px;
}

.popup-btn {
  height: 36px;
  padding: 0 22px;
  border-radius: 6px;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  border: 0;
  transition: filter 0.15s ease, transform 0.1s ease;
}

.popup-btn--primary {
  color: #fff;
}

.popup-btn--primary:hover {
  filter: brightness(1.06);
}

.popup-btn--ghost {
  background: #fff;
  border: 1px solid #d9d9d9;
  color: #555;
}

.popup-btn--ghost:hover {
  border-color: #d7041a;
  color: #d7041a;
}

.popup-btn:active {
  transform: scale(0.98);
}
</style>
