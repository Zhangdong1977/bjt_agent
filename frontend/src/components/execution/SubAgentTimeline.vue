<script setup lang="ts">
interface ToolCall {
  name: string
  arguments: Record<string, any>
}

interface ToolResult {
  name: string
  result: any
}

interface TimelineStep {
  step_number: number
  step_type: string
  content: string
  timestamp: Date
  tool_args?: {
    tool_calls?: ToolCall[]
  }
  tool_result?: {
    tool_results?: ToolResult[]
  }
}

defineProps<{
  steps: TimelineStep[]
}>()

const toolNameMap: Record<string, string> = {
  search_tender_doc: '搜索文档',
  rag_search: '搜索知识库',
  comparator: '内容比对',
}

function getStepColor(stepType: string): string {
  const colorMap: Record<string, string> = {
    tool_call: 'var(--amber)',
    observation: 'var(--green)',
    thought: 'var(--blue)',
  }
  return colorMap[stepType] || 'var(--dim)'
}

function getStepLabel(stepType: string): string {
  if (stepType === 'observation') return '观察'
  if (stepType === 'thought') return '思考过程'
  return stepType
}

function formatToolResult(result: ToolResult): string {
  if (!result) return ''
  if (result.result && typeof result.result === 'object') {
    const r = result.result as any
    if (r.status === 'success' && r.content) {
      return r.content.slice(0, 100) + (r.content.length > 100 ? '...' : '')
    }
    if (r.status === 'error') return `失败: ${r.error || 'unknown'}`
  }
  return JSON.stringify(result).slice(0, 100)
}

function formatTime(date: Date): string {
  return new Date(date).toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
}
</script>

<template>
  <div class="sub-agent-timeline">
    <div class="timeline-header">
      <span class="timeline-title">执行时间线</span>
      <span class="step-count">{{ steps.length }} 步骤</span>
    </div>
    <div class="timeline-content">
      <div
        v-for="step in steps"
        :key="step.step_number"
        :class="['timeline-step', `step-${step.step_type}`]"
      >
        <div class="step-header">
          <span class="step-type" :style="{ color: getStepColor(step.step_type) }">
            {{ getStepLabel(step.step_type) }}
          </span>
          <span class="step-time">{{ formatTime(step.timestamp) }}</span>
        </div>
        <div v-if="step.content" class="step-content">
          {{ step.content }}
        </div>
        <div v-if="step.tool_args?.tool_calls?.length" class="tool-calls">
          <div
            v-for="(toolCall, idx) in step.tool_args.tool_calls"
            :key="idx"
            class="tool-call-item"
          >
            <span class="tool-name">{{ toolNameMap[toolCall.name] || toolCall.name }}</span>
            <span class="tool-arrow">→</span>
            <span class="tool-result">
              {{ step.tool_result?.tool_results?.[idx] ? formatToolResult(step.tool_result.tool_results[idx]) : '等待结果' }}
            </span>
          </div>
        </div>
      </div>
      <div v-if="steps.length === 0" class="empty-state">
        暂无执行记录
      </div>
    </div>
  </div>
</template>

<style scoped>
.sub-agent-timeline {
  margin-top: 12px;
  padding: 12px;
  background: var(--bg3);
  border: 1px solid var(--line);
  border-radius: var(--r);
}

.timeline-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--line2);
}

.timeline-title {
  font-size: 12px;
  font-weight: 500;
  color: var(--text);
}

.step-count {
  font-size: 10px;
  color: var(--muted);
}

.timeline-content {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 300px;
  overflow-y: auto;
}

.timeline-step {
  padding: 8px 10px;
  background: var(--bg2);
  border-radius: var(--r);
  border-left: 3px solid;
}

.step-tool_call {
  border-left-color: var(--amber);
}

.step-observation {
  border-left-color: var(--green);
}

.step-thought {
  border-left-color: var(--blue);
}

.step-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 4px;
}

.step-type {
  font-size: 10px;
  font-weight: 600;
  text-transform: uppercase;
}

.step-time {
  font-size: 10px;
  color: var(--muted);
}

.step-content {
  font-size: 11px;
  color: var(--sub);
  line-height: 1.5;
  margin-bottom: 6px;
}

.tool-calls {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.tool-call-item {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 10px;
}

.tool-name {
  color: var(--blue);
  font-weight: 500;
}

.tool-arrow {
  color: var(--dim);
}

.tool-result {
  color: var(--green);
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.empty-state {
  text-align: center;
  padding: 20px;
  color: var(--muted);
  font-size: 12px;
}
</style>