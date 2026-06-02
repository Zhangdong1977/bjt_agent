<script setup lang="ts">
import { ref } from 'vue'
import { feedbackApi } from '@/api/client'

const props = defineProps<{
  projectId: string
  taskId: string
  ruleDocName: string
  findingCount: number
}>()

const emit = defineEmits<{
  (e: 'batch-confirmed', count: number): void
}>()

const loading = ref(false)
const confirmed = ref(false)

async function handleBatchConfirm() {
  if (loading.value || confirmed.value) return
  loading.value = true
  try {
    const result = await feedbackApi.batchConfirm(
      props.projectId,
      props.taskId,
      props.ruleDocName,
    )
    confirmed.value = true
    emit('batch-confirmed', result.created_count)
  } catch {
    // Error handled silently; user can retry
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <button
    v-if="findingCount > 0"
    :class="['batch-confirm-btn', { done: confirmed }]"
    :disabled="loading || confirmed"
    @click="handleBatchConfirm"
  >
    <template v-if="confirmed">
      ✓ 已全部确认
    </template>
    <template v-else-if="loading">
      处理中...
    </template>
    <template v-else>
      全部确认 ({{ findingCount }})
    </template>
  </button>
</template>

<style scoped>
.batch-confirm-btn {
  padding: 3px 12px;
  border: 1px solid var(--green-dim);
  border-radius: var(--r);
  background: var(--green-bg);
  color: var(--green);
  font-size: 11px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s;
  white-space: nowrap;
}

.batch-confirm-btn:hover:not(:disabled) {
  filter: brightness(0.95);
}

.batch-confirm-btn:disabled {
  cursor: not-allowed;
  opacity: 0.7;
}

.batch-confirm-btn.done {
  background: var(--bg2);
  color: var(--green);
  border-color: var(--green-dim);
}
</style>
