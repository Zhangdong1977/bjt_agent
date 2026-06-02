<script setup lang="ts">
import { ref } from 'vue'
import type { ReviewResult, FeedbackResponse, FeedbackCreateRequest } from '@/types'
import { feedbackApi } from '@/api/client'
import FindingFeedbackForm from './FindingFeedbackForm.vue'

const props = defineProps<{
  finding: ReviewResult
  projectId: string
  existingFeedback?: FeedbackResponse | null
}>()

const emit = defineEmits<{
  (e: 'feedback-submitted', feedback: FeedbackResponse): void
  (e: 'feedback-error', message: string): void
}>()

const activeForm = ref<'none' | 'confirm' | 'contradict' | 'refine'>('none')
const submitting = ref(false)
const submittedFeedback = ref<FeedbackResponse | null>(null)

// Use the latest submitted feedback or the existing one passed as prop
const currentFeedback = ref<FeedbackResponse | null>(
  props.existingFeedback ?? null,
)

function openForm(type: 'confirm' | 'contradict' | 'refine') {
  // If already submitted, don't allow re-submitting the same type
  if (currentFeedback.value && currentFeedback.value.feedback_type === type) {
    return
  }
  activeForm.value = type
}

function cancelForm() {
  activeForm.value = 'none'
}

async function handleSubmit(data: FeedbackCreateRequest) {
  submitting.value = true
  try {
    const feedback = await feedbackApi.submitFeedback(
      props.projectId,
      props.finding.id,
      data,
    )
    submittedFeedback.value = feedback
    currentFeedback.value = feedback
    activeForm.value = 'none'
    emit('feedback-submitted', feedback)
  } catch (err: any) {
    const msg = err?.response?.data?.detail || '提交反馈失败'
    emit('feedback-error', msg)
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="finding-feedback-bar">
    <!-- Already submitted state -->
    <div v-if="currentFeedback" class="feedback-done">
      <span :class="['done-badge', currentFeedback.feedback_type]">
        <template v-if="currentFeedback.feedback_type === 'confirm'">✓ 已同意</template>
        <template v-else-if="currentFeedback.feedback_type === 'contradict'">✗ 已反对</template>
        <template v-else-if="currentFeedback.feedback_type === 'refine'">✎ 已修正</template>
      </span>
      <span v-if="currentFeedback.status === 'pending'" class="pending-hint">待审核</span>
    </div>

    <!-- Action buttons (only when no feedback submitted yet) -->
    <div v-else class="feedback-actions">
      <span class="feedback-label">反馈:</span>
      <button
        :class="['fb-btn', { active: activeForm === 'confirm' }]"
        @click="openForm('confirm')"
        title="同意此发现"
      >👍 同意</button>
      <button
        :class="['fb-btn', { active: activeForm === 'contradict' }]"
        @click="openForm('contradict')"
        title="不同意此发现"
      >👎 不同意</button>
      <button
        :class="['fb-btn', { active: activeForm === 'refine' }]"
        @click="openForm('refine')"
        title="修正此发现的细节"
      >✎ 修正</button>
    </div>

    <!-- Inline form -->
    <FindingFeedbackForm
      v-if="activeForm !== 'none'"
      :feedback-type="activeForm"
      :submitting="submitting"
      @submit="handleSubmit"
      @cancel="cancelForm"
    />
  </div>
</template>

<style scoped>
.finding-feedback-bar {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px dashed var(--line);
}

.feedback-actions {
  display: flex;
  align-items: center;
  gap: 6px;
}

.feedback-label {
  font-size: 11px;
  color: var(--muted);
  font-weight: 500;
}

.fb-btn {
  padding: 3px 10px;
  border: 1px solid var(--line);
  border-radius: var(--r);
  background: var(--bg2);
  color: var(--text);
  font-size: 11px;
  cursor: pointer;
  transition: all 0.15s;
  white-space: nowrap;
}

.fb-btn:hover {
  border-color: var(--blue-dim);
  background: var(--blue-bg);
}

.fb-btn.active {
  border-color: var(--blue);
  background: var(--blue-bg);
  color: var(--blue);
}

.feedback-done {
  display: flex;
  align-items: center;
  gap: 8px;
}

.done-badge {
  display: inline-block;
  padding: 2px 10px;
  border-radius: var(--r);
  font-size: 11px;
  font-weight: 500;
}

.done-badge.confirm {
  background: var(--green-bg);
  color: var(--green);
  border: 1px solid var(--green-dim);
}

.done-badge.contradict {
  background: var(--red-bg);
  color: var(--red);
  border: 1px solid var(--red-dim);
}

.done-badge.refine {
  background: var(--amber-bg);
  color: var(--amber);
  border: 1px solid var(--amber-dim);
}

.pending-hint {
  font-size: 10px;
  color: var(--muted);
  font-style: italic;
}
</style>
