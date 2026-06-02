<script setup lang="ts">
import type { FeedbackResponse } from '@/types'

defineProps<{
  feedbacks: FeedbackResponse[]
}>()

const typeLabels: Record<string, string> = {
  confirm: '同意',
  contradict: '反对',
  refine: '修正',
}

const statusLabels: Record<string, string> = {
  pending: '待审核',
  accepted: '已接受',
  rejected: '已拒绝',
  superseded: '已覆盖',
}

function formatDate(dateStr: string): string {
  return new Date(dateStr).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}
</script>

<template>
  <div class="feedback-history">
    <div class="history-title">反馈记录</div>
    <div v-if="feedbacks.length === 0" class="history-empty">暂无反馈</div>
    <div
      v-for="fb in feedbacks"
      :key="fb.id"
      :class="['history-item', fb.feedback_type]"
    >
      <div class="history-header">
        <span :class="['type-badge', fb.feedback_type]">
          {{ typeLabels[fb.feedback_type] || fb.feedback_type }}
        </span>
        <span :class="['status-text', fb.status]">
          {{ statusLabels[fb.status] || fb.status }}
        </span>
        <span class="history-time">{{ formatDate(fb.created_at) }}</span>
      </div>
      <div v-if="fb.comment" class="history-comment">{{ fb.comment }}</div>
    </div>
  </div>
</template>

<style scoped>
.feedback-history {
  padding: 8px 0;
}

.history-title {
  font-size: 11px;
  font-weight: 500;
  color: var(--muted);
  margin-bottom: 6px;
}

.history-empty {
  font-size: 11px;
  color: var(--muted);
  font-style: italic;
}

.history-item {
  padding: 6px 8px;
  border-left: 3px solid var(--line);
  margin-bottom: 4px;
  background: var(--bg2);
  border-radius: 0 var(--r) var(--r) 0;
}

.history-item.confirm {
  border-left-color: var(--green);
}

.history-item.contradict {
  border-left-color: var(--red);
}

.history-item.refine {
  border-left-color: var(--amber);
}

.history-header {
  display: flex;
  align-items: center;
  gap: 6px;
}

.type-badge {
  font-size: 10px;
  font-weight: 500;
  padding: 1px 6px;
  border-radius: 3px;
}

.type-badge.confirm {
  background: var(--green-bg);
  color: var(--green);
}

.type-badge.contradict {
  background: var(--red-bg);
  color: var(--red);
}

.type-badge.refine {
  background: var(--amber-bg);
  color: var(--amber);
}

.status-text {
  font-size: 10px;
  color: var(--muted);
}

.status-text.accepted {
  color: var(--green);
}

.status-text.pending {
  color: var(--amber);
}

.status-text.rejected {
  color: var(--red);
}

.history-time {
  font-size: 10px;
  color: var(--muted);
  margin-left: auto;
}

.history-comment {
  font-size: 11px;
  color: var(--sub);
  margin-top: 4px;
  line-height: 1.4;
}
</style>
