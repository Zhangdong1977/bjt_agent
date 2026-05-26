<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  documentId: string
  stage: string
  processed: number
  total: number
  etaSeconds: number
}>()

const percent = computed(() => {
  if (props.total <= 0) return 0
  return Math.min(Math.round((props.processed / props.total) * 100), 100)
})

const stageLabel = computed(() => {
  if (props.stage === 'parsing_pdf') {
    return props.processed === 0 ? '正在初始化解析器' : '正在解析文档'
  }
  if (props.stage === 'extracting_text') return '正在提取文本'
  if (props.stage === 'processing_images') return '正在处理图片'
  if (props.stage === 'saving') return '正在保存结果'
  return '正在解析文档'
})
</script>

<template>
  <div class="doc-parse-progress">
    <div class="pulse-indicator">
      <div class="pulse-ring"></div>
      <div class="pulse-core"></div>
    </div>

    <div class="progress-body">
      <div class="progress-header">
        <span class="stage-label">{{ stageLabel }}</span>
        <span class="percent-value" v-if="stage !== 'saving'">{{ percent }}%</span>
      </div>
      <div class="stage-bar-track" v-if="stage !== 'saving'">
        <div class="stage-bar-fill" :style="{ width: percent + '%' }"></div>
      </div>
      <div class="progress-footer" v-else>
        <span class="eta-label">请稍候...</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.doc-parse-progress {
  display: flex;
  align-items: flex-start;
  gap: 1rem;
  padding: 0.75rem 1rem;
  background: var(--bg2);
  border-radius: 12px;
  border: 1px solid var(--bg4);
}

.pulse-indicator {
  position: relative;
  width: 36px;
  height: 36px;
  flex-shrink: 0;
  margin-top: 2px;
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
  inset: 8px;
  border-radius: 50%;
  background: var(--blue);
  animation: pulse-core 1.5s ease-in-out infinite;
}

@keyframes pulse-ring {
  0% { transform: scale(0.8); opacity: 0.3; }
  50% { transform: scale(1.1); opacity: 0.15; }
  100% { transform: scale(1.3); opacity: 0; }
}

@keyframes pulse-core {
  0%, 100% { transform: scale(1); }
  50% { transform: scale(0.85); }
}

.progress-body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
}

.progress-header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
}

.stage-label {
  font-size: 0.8rem;
  color: var(--muted);
}

.percent-value {
  font-size: 0.95rem;
  font-weight: 700;
  color: var(--text);
  font-variant-numeric: tabular-nums;
}

.stage-bar-track {
  height: 5px;
  background: var(--bg3, #e5e7eb);
  border-radius: 2.5px;
  overflow: hidden;
}

.stage-bar-fill {
  height: 100%;
  background: var(--blue);
  border-radius: 2.5px;
  transition: width 0.3s ease-out;
}

.progress-footer {
  margin-top: 0.125rem;
}

.eta-label {
  font-size: 0.7rem;
  color: var(--muted);
}
</style>
