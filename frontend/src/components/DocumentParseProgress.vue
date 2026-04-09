<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  documentId: string
  stage: string
  processed: number
  total: number
  etaSeconds: number
}>()

// Stage label mapping (Chinese)
const stageLabels: Record<string, string> = {
  extracting_text: '正在提取文档内容',
  processing_images: '正在调用 AI 理解图片',
  saving: '正在保存解析结果',
}

// Stage number mapping (1-indexed for display)
const stageOrder: Record<string, number> = {
  extracting_text: 1,
  processing_images: 2,
  saving: 3,
}

const stageNumber = computed(() => stageOrder[props.stage] || 1)

const stageLabel = computed(() => stageLabels[props.stage] || '正在处理')

// Format ETA as human-readable string
function formatEta(seconds: number): string {
  if (seconds <= 0) return '计算中...'
  if (seconds < 60) return `约 ${seconds} 秒`
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `约 ${minutes} 分钟`
  const hours = Math.floor(minutes / 60)
  const remainingMinutes = minutes % 60
  return `约 ${hours} 小时 ${remainingMinutes} 分钟`
}

const etaLabel = computed(() => formatEta(props.etaSeconds))

// SVG circle progress calculations
const RADIUS = 36
const CIRCUMFERENCE = 2 * Math.PI * RADIUS
const percent = computed(() => {
  if (props.total === 0) return 0
  return Math.min(props.processed / props.total, 1)
})
const dashOffset = computed(() => CIRCUMFERENCE * (1 - percent.value))
const progressText = computed(() => `${stageNumber.value}/3`)
</script>

<template>
  <div class="doc-parse-progress">
    <div class="progress-ring-container">
      <svg class="progress-ring" width="96" height="96" viewBox="0 0 96 96">
        <!-- Background track -->
        <circle
          class="ring-track"
          cx="48"
          cy="48"
          :r="RADIUS"
          fill="none"
          stroke="#e5e7eb"
          stroke-width="8"
        />
        <!-- Progress fill -->
        <circle
          class="ring-fill"
          cx="48"
          cy="48"
          :r="RADIUS"
          fill="none"
          stroke="#6366f1"
          stroke-width="8"
          stroke-linecap="round"
          :stroke-dasharray="CIRCUMFERENCE"
          :stroke-dashoffset="dashOffset"
          transform="rotate(-90 48 48)"
        />
      </svg>
      <div class="ring-center">
        <span class="progress-text">{{ progressText }}</span>
      </div>
    </div>
    <div class="progress-info">
      <span class="stage-label">{{ stageLabel }}</span>
      <span class="eta-label">预计剩余: {{ etaLabel }}</span>
    </div>
  </div>
</template>

<style scoped>
.doc-parse-progress {
  display: flex;
  align-items: center;
  gap: 1rem;
  padding: 0.5rem 0;
}

.progress-ring-container {
  position: relative;
  flex-shrink: 0;
  width: 96px;
  height: 96px;
}

.progress-ring {
  display: block;
}

.ring-fill {
  transition: stroke-dashoffset 0.3s ease;
}

.ring-center {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  text-align: center;
}

.progress-text {
  font-size: 0.9rem;
  font-weight: 700;
  color: #1e1b4b;
  font-variant-numeric: tabular-nums;
}

.progress-info {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.stage-label {
  font-size: 0.85rem;
  font-weight: 500;
  color: #374151;
}

.eta-label {
  font-size: 0.75rem;
  color: #6b7280;
}
</style>
