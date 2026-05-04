<script setup lang="ts">
import { formatToolCallDescription, getToolDisplayName } from '@/utils/toolDisplay'
import { renderMarkdown } from '@/utils/markdown'

interface ToolCall {
  name: string
  arguments: Record<string, any>
}

interface ToolResult {
  name: string
  result: any
}

interface Props {
  stepNumber: number
  stepType: 'master' | 'observation' | 'tool_call' | 'thought' | 'tool_result'
  content: string
  timestamp: Date
  toolCalls?: ToolCall[]
  toolResults?: ToolResult[]
  status?: 'pending' | 'running' | 'completed' | 'error'
  duration?: number
}

defineProps<Props>()

function formatTime(date: Date): string {
  return date.toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false
  })
}

function getStepColor(stepType: string): string {
  const colorMap: Record<string, string> = {
    master: 'var(--blue)',
    tool_call: 'var(--amber)',
    observation: 'var(--green)',
    thought: 'var(--blue)',
    tool_result: 'var(--green)',
  }
  return colorMap[stepType] || 'var(--dim)'
}

function getCardClass(stepType: string): string {
  return `card-${stepType}`
}

function getToolResultText(result: any): string {
  if (result === undefined || result === null) return ''
  if (result.is_compliant !== undefined) {
    const emoji = result.is_compliant ? '✅' : '❌'
    const severity = result.severity || ''
    const explanation = result.explanation || ''
    return `${emoji} ${severity ? `(${severity}) ` : ''}${explanation}`
  }
  if (result?.status === 'success' && result?.content) {
    return result.content
  }
  if (result?.status === 'error') {
    return `失败: ${result.error || 'unknown'}`
  }
  const str = JSON.stringify(result)
  return str || String(result)
}
</script>

<template>
  <div class="timeline-item">
    <div class="timeline-dot" :style="{ background: getStepColor(stepType) }"></div>
    <div :class="['timeline-card', getCardClass(stepType)]">
      <div class="card-header">
        <span class="step-number">#{{ stepNumber }}</span>
        <span class="step-label">{{ stepType === 'master' ? '主代理' : stepType === 'observation' ? '观察' : stepType === 'tool_call' ? '工具调用' : stepType === 'tool_result' ? '工具结果' : '思考' }}</span>
        <span v-if="status === 'running'" class="status-running">RUNNING</span>
        <span v-if="duration !== undefined" class="duration">{{ duration }}ms</span>
        <span class="timestamp">{{ formatTime(timestamp) }}</span>
      </div>
      <div v-if="content" class="step-text markdown-content" v-html="renderMarkdown(content)"></div>
      <!-- 工具调用详情 -->
      <div v-if="toolCalls?.length" class="tool-calls-section">
        <div v-for="(tc, idx) in toolCalls" :key="idx" class="tool-call-item">
          <div class="tool-call-header">
            <span class="tool-name">{{ getToolDisplayName(tc.name) }}</span>
          </div>
          <div class="tool-call-args">
            <span class="param-tag">
              {{ formatToolCallDescription(tc.name, tc.arguments) }}
            </span>
          </div>
          <div v-if="toolResults?.[idx]" class="tool-call-result markdown-content" v-html="renderMarkdown(getToolResultText(toolResults[idx].result))"></div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.timeline-item {
  display: flex;
  gap: 12px;
  margin-bottom: 12px;
  position: relative;
}

.timeline-item::before {
  content: '';
  position: absolute;
  left: 5px;
  top: 20px;
  bottom: -12px;
  width: 2px;
  background: var(--line);
}

.timeline-dot {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  flex-shrink: 0;
  margin-top: 4px;
  z-index: 1;
}

.timeline-card {
  flex: 1;
  padding: 10px 14px;
  border-radius: var(--r2);
  border-left: 3px solid;
}

.card-master { background: var(--blue-bg); border-color: var(--blue); }
.card-observation { background: var(--green-bg); border-color: var(--green); }
.card-tool_call { background: var(--amber-bg); border-color: var(--amber); }
.card-thought { background: var(--blue-bg); border-color: var(--blue); }
.card-tool_result { background: var(--green-bg); border-color: var(--green); }

.card-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.step-number {
  font-size: 11px;
  font-weight: 600;
  color: var(--dim);
}

.step-label {
  font-size: 12px;
  font-weight: 500;
  color: var(--text);
}

.status-running {
  font-size: 10px;
  color: var(--blue);
  background: var(--blue-bg);
  padding: 2px 6px;
  border-radius: 3px;
}

.timestamp {
  margin-left: auto;
  font-size: 10px;
  color: var(--muted);
}

.duration {
  font-size: 10px;
  color: var(--muted);
}

.step-text {
  font-size: 12px;
  color: var(--text);
  line-height: 1.6;
  margin: 0;
}

.markdown-content :deep(h1),
.markdown-content :deep(h2),
.markdown-content :deep(h3) {
  margin-top: 0.8em;
  margin-bottom: 0.4em;
  font-weight: 600;
}

.markdown-content :deep(h1) { font-size: 1.4em; }
.markdown-content :deep(h2) { font-size: 1.2em; }
.markdown-content :deep(h3) { font-size: 1.1em; }

.markdown-content :deep(p) {
  margin-bottom: 0.5em;
}

.markdown-content :deep(ul),
.markdown-content :deep(ol) {
  margin-bottom: 0.5em;
  padding-left: 1.5em;
}

.markdown-content :deep(li) {
  margin-bottom: 0.3em;
}

.markdown-content :deep(code) {
  background-color: var(--bg2);
  padding: 0.1em 0.3em;
  border-radius: 3px;
  font-size: 0.9em;
}

.markdown-content :deep(pre) {
  background-color: var(--bg2);
  padding: 0.8em;
  border-radius: var(--r);
  overflow-x: auto;
  margin-bottom: 0.5em;
}

.markdown-content :deep(pre code) {
  background: none;
  padding: 0;
}

.markdown-content :deep(table) {
  border-collapse: collapse;
  width: 100%;
  margin-bottom: 0.5em;
}

.markdown-content :deep(th),
.markdown-content :deep(td) {
  border: 1px solid var(--line);
  padding: 4px 8px;
}

.markdown-content :deep(th) {
  background-color: var(--bg2);
  font-weight: 600;
}

.markdown-content :deep(strong) {
  font-weight: 600;
}

.markdown-content :deep(blockquote) {
  border-left: 3px solid var(--blue);
  padding-left: 0.8em;
  margin: 0.5em 0;
  color: var(--muted);
}

.tool-calls-section {
  margin-top: 10px;
  padding: 8px;
  background: var(--bg3);
  border-radius: var(--r);
}

.tool-call-item {
  background: var(--bg1);
  border: 1px solid var(--line);
  border-radius: var(--r);
  padding: 8px 10px;
  margin-bottom: 6px;
}

.tool-call-item:last-child {
  margin-bottom: 0;
}

.tool-name {
  font-size: 11px;
  font-weight: 600;
  color: var(--blue);
}

.tool-call-args {
  margin-top: 4px;
  font-size: 11px;
  color: var(--muted);
}

.param-tag {
  display: inline-block;
  margin-right: 10px;
}

.tool-call-result {
  margin-top: 6px;
  padding-top: 6px;
  border-top: 1px dashed var(--line);
  font-size: 11px;
  color: var(--text);
}
</style>