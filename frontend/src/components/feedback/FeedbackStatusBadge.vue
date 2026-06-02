<script setup lang="ts">
import type { FeedbackResponse } from '@/types'

defineProps<{
  feedback: FeedbackResponse | null | undefined
}>()
</script>

<template>
  <span v-if="feedback" :class="['feedback-status-badge', feedback.feedback_type]">
    <template v-if="feedback.feedback_type === 'confirm'">已同意</template>
    <template v-else-if="feedback.feedback_type === 'contradict'">已反对</template>
    <template v-else-if="feedback.feedback_type === 'refine'">已修正</template>
  </span>
</template>

<style scoped>
.feedback-status-badge {
  display: inline-block;
  padding: 1px 6px;
  border-radius: 3px;
  font-size: 10px;
  font-weight: 500;
  white-space: nowrap;
}

.feedback-status-badge.confirm {
  background: var(--green-bg);
  color: var(--green);
  border: 1px solid var(--green-dim);
}

.feedback-status-badge.contradict {
  background: var(--red-bg);
  color: var(--red);
  border: 1px solid var(--red-dim);
}

.feedback-status-badge.refine {
  background: var(--amber-bg);
  color: var(--amber);
  border: 1px solid var(--amber-dim);
}
</style>
