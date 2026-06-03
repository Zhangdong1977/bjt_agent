<script setup lang="ts">
import { ref } from 'vue'
import type { FeedbackResponse } from '@/types'
import { feedbackApi } from '@/api/client'

const props = defineProps<{
  feedback: FeedbackResponse
  projectId: string
}>()

const emit = defineEmits<{
  (e: 'reviewed', feedback: FeedbackResponse): void
}>()

const loading = ref(false)
const reason = ref('')

const contradictReasonLabels: Record<string, string> = {
  should_comply: '该项实际合规',
  severity_too_high: '严重程度过高',
  severity_too_low: '严重程度过低',
  item_not_applicable: '检查项不适用',
}

const severityLabels: Record<string, string> = {
  critical: '严重',
  major: '重要',
  minor: '一般',
}

async function handleReview(action: 'accept' | 'reject') {
  if (loading.value) return
  loading.value = true
  try {
    const result = await feedbackApi.reviewFeedback(
      props.projectId,
      props.feedback.id,
      action,
      reason.value.trim() || undefined,
    )
    reason.value = ''
    emit('reviewed', result)
  } catch {
    // Error handled silently
  } finally {
    loading.value = false
  }
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
  <div :class="['feedback-review-card', feedback.feedback_type]">
    <div class="card-header">
      <span :class="['type-badge', feedback.feedback_type]">
        <template v-if="feedback.feedback_type === 'confirm'">同意</template>
        <template v-else-if="feedback.feedback_type === 'contradict'">反对</template>
        <template v-else-if="feedback.feedback_type === 'refine'">修正</template>
      </span>
      <span class="card-time">{{ formatDate(feedback.created_at) }}</span>
      <span :class="['status-badge', feedback.status]">
        <template v-if="feedback.status === 'pending'">待审核</template>
        <template v-else-if="feedback.status === 'accepted'">已接受</template>
        <template v-else-if="feedback.status === 'rejected'">已拒绝</template>
      </span>
    </div>

    <div class="card-body">
      <div class="info-row">
        <span class="info-label">规则文档</span>
        <span class="info-value">{{ feedback.rule_doc_name?.replace('.md', '') || '-' }}</span>
      </div>

      <template v-if="feedback.feedback_type === 'contradict'">
        <div class="info-row">
          <span class="info-label">反对原因</span>
          <span class="info-value highlight">{{ contradictReasonLabels[feedback.contradict_reason ?? ''] || feedback.contradict_reason || '-' }}</span>
        </div>
      </template>

      <template v-if="feedback.feedback_type === 'refine'">
        <div v-if="feedback.corrected_severity" class="info-row">
          <span class="info-label">修正严重度</span>
          <span class="info-value highlight">{{ severityLabels[feedback.corrected_severity] || feedback.corrected_severity }}</span>
        </div>
        <div v-if="feedback.corrected_suggestion" class="info-row">
          <span class="info-label">修正建议</span>
          <span class="info-value">{{ feedback.corrected_suggestion }}</span>
        </div>
        <div v-if="feedback.corrected_is_compliant !== null" class="info-row">
          <span class="info-label">修正合规判定</span>
          <span class="info-value highlight">{{ feedback.corrected_is_compliant ? '合规' : '不合规' }}</span>
        </div>
      </template>

      <div v-if="feedback.comment" class="info-row">
        <span class="info-label">用户备注</span>
        <span class="info-value">{{ feedback.comment }}</span>
      </div>
    </div>

    <div v-if="feedback.status === 'pending'" class="card-actions">
      <input
        v-model="reason"
        class="reason-input"
        placeholder="审核意见（可选）"
      />
      <button
        class="accept-btn"
        :disabled="loading"
        @click="handleReview('accept')"
      >
        {{ loading ? '处理中...' : '✓ 接受' }}
      </button>
      <button
        class="reject-btn"
        :disabled="loading"
        @click="handleReview('reject')"
      >
        {{ loading ? '处理中...' : '✗ 拒绝' }}
      </button>
    </div>

    <div v-if="feedback.status !== 'pending' && feedback.reviewed_at" class="card-reviewed">
      已于 {{ formatDate(feedback.reviewed_at) }} 审核
    </div>
  </div>
</template>

<style scoped>
.feedback-review-card {
  background: var(--bg1);
  border: 1px solid var(--line);
  border-radius: var(--r);
  padding: 12px 14px;
  border-left: 3px solid var(--line);
}

.feedback-review-card.confirm {
  border-left-color: var(--green);
}

.feedback-review-card.contradict {
  border-left-color: var(--red);
}

.feedback-review-card.refine {
  border-left-color: var(--amber);
}

.card-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.type-badge {
  font-size: 10px;
  font-weight: 500;
  padding: 1px 8px;
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

.card-time {
  font-size: 10px;
  color: var(--muted);
}

.status-badge {
  font-size: 10px;
  font-weight: 500;
  margin-left: auto;
  padding: 1px 6px;
  border-radius: 3px;
}

.status-badge.pending {
  background: var(--amber-bg);
  color: var(--amber);
}

.status-badge.accepted {
  background: var(--green-bg);
  color: var(--green);
}

.status-badge.rejected {
  background: var(--red-bg);
  color: var(--red);
}

.card-body {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-bottom: 10px;
}

.info-row {
  display: flex;
  gap: 8px;
  font-size: 12px;
  line-height: 1.5;
}

.info-label {
  color: var(--muted);
  font-weight: 500;
  min-width: 72px;
  flex-shrink: 0;
}

.info-value {
  color: var(--text);
  word-break: break-word;
}

.info-value.highlight {
  color: var(--blue);
  font-weight: 500;
}

.card-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  padding-top: 8px;
  border-top: 1px dashed var(--line);
}

.reason-input {
  flex: 1;
  padding: 4px 8px;
  border: 1px solid var(--line);
  border-radius: var(--r);
  background: var(--bg2);
  color: var(--text);
  font-size: 11px;
  min-width: 0;
}

.reason-input:focus {
  outline: none;
  border-color: var(--blue);
}

.accept-btn {
  padding: 4px 14px;
  background: var(--green);
  color: var(--white);
  border: none;
  border-radius: var(--r);
  font-size: 11px;
  font-weight: 500;
  cursor: pointer;
  white-space: nowrap;
}

.accept-btn:hover:not(:disabled) {
  filter: brightness(1.1);
}

.accept-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.reject-btn {
  padding: 4px 14px;
  background: transparent;
  color: var(--red);
  border: 1px solid var(--red-dim);
  border-radius: var(--r);
  font-size: 11px;
  font-weight: 500;
  cursor: pointer;
  white-space: nowrap;
}

.reject-btn:hover:not(:disabled) {
  background: var(--red-bg);
}

.reject-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.card-reviewed {
  font-size: 10px;
  color: var(--muted);
  padding-top: 6px;
  border-top: 1px dashed var(--line);
  font-style: italic;
}
</style>
