<script setup lang="ts">
import { computed, ref, watch } from 'vue'

const props = defineProps<{
  documentId: string
  stage: string
  processed: number
  total: number
  etaSeconds: number
}>()

// Track history of processed values to show "increasing" effect
const history = ref<number[]>([])

// Keep last 5 distinct values for display
watch(() => props.processed, (newVal, oldVal) => {
  if (newVal !== oldVal && newVal > 0) {
    history.value.push(newVal)
    if (history.value.length > 5) {
      history.value.shift()
    }
  }
})

const latestValue = computed(() => props.processed)
const maxHistoryVal = computed(() => Math.max(...history.value, 1))

// Get stage display text
const stageLabel = computed(() => {
  if (props.stage === 'extracting_text') return '正在提取文本'
  if (props.stage === 'processing_images') return '正在处理图片'
  if (props.stage === 'saving') return '正在保存结果'
  return '正在解析文档'
})

// Whether to show processed count (saving stage doesn't have meaningful element count)
const showProcessedCount = computed(() => props.stage !== 'saving')
</script>

<template>
  <div class="doc-parse-progress">
    <!-- Pulsing indicator -->
    <div class="pulse-indicator">
      <div class="pulse-ring"></div>
      <div class="pulse-core"></div>
    </div>

    <!-- Main stats -->
    <div class="stats-container">
      <div class="stats-label">{{ stageLabel }}</div>
      <div class="stats-value" v-if="showProcessedCount">
        <span class="count-number">{{ latestValue.toLocaleString('zh-CN') }}</span>
        <span class="count-unit">个元素已处理</span>
      </div>
      <div class="stats-value" v-else>
        <span class="count-number">...</span>
        <span class="count-unit">请稍候</span>
      </div>
    </div>

    <!-- Recent activity trail -->
    <div class="activity-trail" v-if="history.length > 1">
      <div class="trail-label">处理进度</div>
      <div class="trail-bars">
        <div
          v-for="(val, idx) in history"
          :key="idx"
          class="trail-bar"
          :style="{ height: Math.max(8, (val / maxHistoryVal) * 32) + 'px' }"
        ></div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.doc-parse-progress {
  display: flex;
  align-items: center;
  gap: 1.5rem;
  padding: 1rem;
  background: var(--bg2);
  border-radius: 12px;
  border: 1px solid var(--bg4);
}

/* Pulsing indicator */
.pulse-indicator {
  position: relative;
  width: 48px;
  height: 48px;
  flex-shrink: 0;
}

.pulse-ring {
  position: absolute;
  inset: 0;
  border-radius: 50%;
  background: var(--blue);
  opacity: 0.2;
  animation: pulse-ring 1.5s ease-out infinite;
}

.pulse-core {
  position: absolute;
  inset: 12px;
  border-radius: 50%;
  background: var(--blue);
  animation: pulse-core 1.5s ease-in-out infinite;
}

@keyframes pulse-ring {
  0% {
    transform: scale(0.8);
    opacity: 0.3;
  }
  50% {
    transform: scale(1.1);
    opacity: 0.15;
  }
  100% {
    transform: scale(1.3);
    opacity: 0;
  }
}

@keyframes pulse-core {
  0%, 100% {
    transform: scale(1);
  }
  50% {
    transform: scale(0.85);
  }
}

/* Stats display */
.stats-container {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  min-width: 180px;
}

.stats-label {
  font-size: 0.8rem;
  color: var(--muted);
}

.stats-value {
  display: flex;
  align-items: baseline;
  gap: 0.5rem;
}

.count-number {
  font-size: 2rem;
  font-weight: 700;
  color: var(--text);
  font-variant-numeric: tabular-nums;
  line-height: 1;
  transition: transform 0.2s ease-out, color 0.2s;
}

.count-unit {
  font-size: 0.85rem;
  color: var(--sub);
}

/* Activity trail */
.activity-trail {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  padding-left: 1rem;
  border-left: 1px solid var(--bg4);
  min-width: 100px;
}

.trail-label {
  font-size: 0.7rem;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.trail-bars {
  display: flex;
  align-items: flex-end;
  gap: 3px;
  height: 32px;
}

.trail-bar {
  width: 6px;
  background: var(--blue);
  border-radius: 2px;
  opacity: 0.6;
  transition: height 0.3s ease-out;
}

</style>
