<script setup lang="ts">
import type { ReviewSummary } from '@/types'

defineProps<{
  summary: ReviewSummary
}>()
</script>

<template>
  <div class="summary">
    <div class="summary-item">
      <span class="summary-value">{{ summary.total_requirements }}</span>
      <span class="summary-label">总计</span>
    </div>
    <div class="summary-item success">
      <span class="summary-value">{{ summary.compliant }}</span>
      <span class="summary-label">合规</span>
    </div>
    <div class="summary-item error">
      <span class="summary-value">{{ summary.non_compliant }}</span>
      <span class="summary-label">不合规</span>
    </div>
    <div class="summary-item critical">
      <span class="summary-value">{{ summary.critical }}</span>
      <span class="summary-label">严重</span>
    </div>
    <div class="summary-item major">
      <span class="summary-value">{{ summary.major }}</span>
      <span class="summary-label">主要</span>
    </div>
    <div class="summary-item minor">
      <span class="summary-value">{{ summary.minor }}</span>
      <span class="summary-label">次要</span>
    </div>
  </div>
</template>

<style scoped>
.summary {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 1rem;
  margin-bottom: 2rem;
}

.summary-item {
  text-align: center;
  padding: 1rem;
  background: #f5f5f5;
  border-radius: 8px;
}

.summary-value {
  display: block;
  font-size: 2rem;
  font-weight: bold;
  color: #333;
}

.summary-label {
  color: #666;
  font-size: 0.9rem;
}

.summary-item.success .summary-value {
  color: #68d391;
}

.summary-item.error .summary-value {
  color: #e53e3e;
}

.summary-item.critical .summary-value {
  color: #c53030;
}

.summary-item.major .summary-value {
  color: #dd6b20;
}

.summary-item.minor .summary-value {
  color: #d69e2e;
}

@media (max-width: 768px) {
  .summary {
    grid-template-columns: repeat(3, 1fr);
  }
}
</style>
