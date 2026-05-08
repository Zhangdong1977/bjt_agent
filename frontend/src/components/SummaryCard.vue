<script setup lang="ts">
import type { ReviewSummary } from '@/types'

defineProps<{
  summary: ReviewSummary
}>()
</script>

<template>
  <div class="stats-bar">
    <div class="stat-box">
      <div class="stat-val sv-blue">{{ summary.category_count }}</div>
      <div class="stat-lbl">检查大类</div>
    </div>
    <div class="stat-box">
      <div class="stat-val sv-purple">{{ summary.check_item_count }}</div>
      <div class="stat-lbl">检查项总数</div>
    </div>
    <div class="stat-box">
      <div class="stat-val sv-red">{{ summary.risk_item_count }}</div>
      <div class="stat-lbl">风险项总数</div>
    </div>
  </div>
</template>

<style scoped>
.stats-bar {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: 6px;
  margin-bottom: 1rem;
}

.stat-box {
  background: var(--bg2);
  border: 1px solid var(--line);
  border-radius: var(--r);
  padding: 10px 12px;
}

.stat-val {
  font-size: 22px;
  font-weight: 600;
  line-height: 1;
  letter-spacing: -0.02em;
}

.sv-blue { color: var(--blue); }
.sv-purple { color: var(--blue); }
.sv-red { color: var(--red); }

.stat-lbl {
  font-size: 10px;
  color: var(--muted);
  margin-top: 4px;
}

@media (max-width: 768px) {
  .stats-bar {
    grid-template-columns: 1fr;
  }
}
</style>
