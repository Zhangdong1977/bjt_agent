<script setup lang="ts">
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

const props = defineProps<Props>()

// 工具名称映射
const toolNameMap: Record<string, string> = {
  search_tender_doc: '搜索文档',
  rag_search: '搜索知识库',
  comparator: '内容比对',
}

function formatTime(date: Date): string {
  return new Date(date).toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false
  })
}

function getStepColor(stepType: string): string {
  const colorMap: Record<string, string> = {
    master: 'var(--purple)',
    tool_call: 'var(--amber)',
    observation: 'var(--green)',
    thought: 'var(--blue)',
  }
  return colorMap[stepType] || 'var(--dim)'
}

function getCardClass(stepType: string): string {
  return `card-${stepType}`
}

function formatToolArg(key: string, value: any): string {
  if (key === 'doc_type') {
    return value === 'tender' ? '文档类型: 招标书' : '文档类型: 投标书'
  }
  if (typeof value === 'string' && value.length > 100) {
    return `${key}: ${value.slice(0, 100)}...`
  }
  return `${key}: ${value}`
}

function formatToolResult(result: any): string {
  if (result?.status === 'success' && result?.content) {
    return result.content.slice(0, 200) + (result.content.length > 200 ? '...' : '')
  }
  if (result?.status === 'error') {
    return `失败: ${result.error || 'unknown'}`
  }
  return JSON.stringify(result).slice(0, 100)
}
</script>

<template>
  <div class="timeline-item">
    <div class="timeline-dot" :style="{ background: getStepColor(stepType) }"></div>
    <div :class="['timeline-card', getCardClass(stepType)]">
      <div class="card-header">
        <span class="step-number">#{{ stepNumber }}</span>
        <span class="step-label">{{ stepType === 'master' ? '主代理' : stepType === 'observation' ? '观察' : stepType === 'tool_call' ? '工具调用' : '思考' }}</span>
        <span v-if="status === 'running'" class="status-running">RUNNING</span>
        <span class="timestamp">{{ formatTime(timestamp) }}</span>
      </div>
      <p v-if="content" class="step-text">{{ content }}</p>
      <!-- 工具调用详情 -->
      <div v-if="toolCalls?.length" class="tool-calls-section">
        <div v-for="(tc, idx) in toolCalls" :key="idx" class="tool-call-item">
          <div class="tool-call-header">
            <span class="tool-name">{{ toolNameMap[tc.name] || tc.name }}</span>
          </div>
          <div class="tool-call-args">
            <span v-for="(value, key) in tc.arguments" :key="key" class="param-tag">
              {{ formatToolArg(key, value) }}
            </span>
          </div>
          <div v-if="toolResults?.[idx]" class="tool-call-result">
            <span class="result-text">{{ formatToolResult(toolResults[idx].result) }}</span>
          </div>
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

.card-master { background: var(--purple-bg); border-color: var(--purple); }
.card-observation { background: var(--green-bg); border-color: var(--green); }
.card-tool_call { background: var(--amber-bg); border-color: var(--amber); }
.card-thought { background: var(--blue-bg); border-color: var(--blue); }

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

.step-text {
  font-size: 12px;
  color: var(--text);
  line-height: 1.6;
  margin: 0;
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
  color: var(--purple);
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
  color: var(--green);
}
</style>