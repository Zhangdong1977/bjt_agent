<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed, watch } from 'vue'
import type { SSEEvent } from '@/types'
import {
  CheckOutlined,
  ClockCircleOutlined,
  LoadingOutlined,
  CloseCircleOutlined,
} from '@ant-design/icons-vue'
import { Tag, Collapse, CollapsePanel } from 'ant-design-vue'

const API_BASE = import.meta.env.VITE_API_BASE || '/api'

const props = defineProps<{
  taskId: string
  initialSteps?: TimelineStep[]  // For displaying historical steps
  historicalMode?: boolean        // Whether showing historical data
}>()

const isHistorical = computed(() => props.historicalMode)

interface ToolResult {
  status: 'success' | 'error'
  content?: string
  error?: string
  count?: number
}

interface TimelineStep {
  step_number: number
  step_type: string
  tool_name?: string
  content: string
  timestamp: Date
  status?: 'pending' | 'running' | 'completed' | 'error'
  duration?: number
  tool_args?: Record<string, any>
  tool_result?: ToolResult & { _merged?: boolean }
}

const steps = ref<TimelineStep[]>([])
let eventSource: EventSource | null = null

// 工具名称映射
const toolNameMap: Record<string, string> = {
  search_tender_doc: '搜索文档',
  rag_search: '搜索知识库',
  comparator: '内容比对',
}

// 参数键映射 (每个工具独立)
const toolParamKeyMap: Record<string, Record<string, string>> = {
  search_tender_doc: {
    doc_type: '文档类型',
    query: '查询内容',
    chunk: '章节',
    full_content: '完整内容',
  },
  rag_search: {
    query: '查询内容',
    limit: '返回数量',
  },
  comparator: {
    requirement: '招标要求',
    bid_content: '投标内容',
  },
}

// 参数值映射 (每个工具独立)
const toolParamValueMap: Record<string, Record<string, Record<string, string>>> = {
  search_tender_doc: {
    doc_type: {
      tender: '招标文档',
      bid: '投标文档',
    },
  },
}

// 工具结果格式化
const toolResultFormatter: Record<string, (result: ToolResult) => string> = {
  search_tender_doc: (result) => {
    if (result.status === 'success') {
      return `找到 ${result.count || 0} 条相关内容 - ${result.content?.slice(0, 100)}...`
    }
    return `搜索失败: ${result.error}`
  },
  rag_search: (result) => {
    if (result.status === 'success') {
      return `知识库返回 ${result.count || 0} 条结果`
    }
    return `查询失败: ${result.error}`
  },
  comparator: (result) => {
    if (result.status === 'success') {
      return `比对完成: ${result.content?.slice(0, 100)}...`
    }
    return `比对失败: ${result.error}`
  },
}

onMounted(() => {
  if (props.initialSteps?.length) {
    steps.value = props.initialSteps
  }
  if (!props.historicalMode) {
    connect(props.taskId)
  }
})

// Watch for initialSteps changes (e.g., when loading historical timeline multiple times)
watch(() => props.initialSteps, (newSteps) => {
  if (newSteps?.length) {
    steps.value = newSteps
  }
}, { deep: true })

function handleSSEEvent(event: SSEEvent) {
  if (event.type === 'step' && event.step_number !== undefined) {
    if (event.step_type === 'tool_call') {
      // tool_call: 创建新节点
      steps.value.push({
        step_number: event.step_number,
        step_type: 'tool_call',
        tool_name: event.tool_name,
        content: event.content || '',
        timestamp: new Date(),
        tool_args: event.tool_args,
        tool_result: undefined,  // 预置，后续补充
      })
    } else if (event.step_type === 'tool_result') {
      // tool_result: 查找对应的 tool_call 节点并合并
      const pairedStep = steps.value.find(s =>
        s.step_number === event.step_number! - 1 &&
        s.tool_name === event.tool_name &&
        s.step_type === 'tool_call'
      )
      if (pairedStep) {
        pairedStep.tool_result = event.tool_result
        pairedStep.tool_result!._merged = true  // 标记已合并
      }
    } else {
      // 其他类型(step_type === 'thought' 或 'observation')直接添加
      const exists = steps.value.some(s => s.step_number === event.step_number)
      if (!exists) {
        steps.value.push({
          step_number: event.step_number,
          step_type: event.step_type || 'unknown',
          tool_name: event.tool_name,
          content: event.content || '',
          timestamp: new Date(),
          tool_args: event.tool_args,
          tool_result: event.tool_result,
        })
      }
    }
  } else if (event.type === 'status' && event.status === 'running') {
    steps.value = []
  }
}

function connect(taskId: string) {
  disconnect()
  eventSource = new EventSource(`${API_BASE}/events/tasks/${taskId}/stream`)

  eventSource.onmessage = (e) => {
    try {
      const data: SSEEvent = JSON.parse(e.data)
      handleSSEEvent(data)
    } catch (err) {
      console.error('Failed to parse SSE event:', err)
    }
  }

  eventSource.onerror = () => {
    disconnect()
  }
}

function disconnect() {
  if (eventSource) {
    eventSource.close()
    eventSource = null
  }
}

function reset() {
  steps.value = []
}

function getStepColor(stepType: string): string {
  const colorMap: Record<string, string> = {
    tool_call: '#fa8c16',    // 橙色
    observation: '#52c41a',  // 绿色
    thought: '#1890ff',       // 蓝色
  }
  return colorMap[stepType] || '#d9d9d9'
}

function getTagColor(stepType: string): string {
  const colorMap: Record<string, string> = {
    tool_call: 'purple',
    observation: 'green',
    thought: 'blue',
  }
  return colorMap[stepType] || 'default'
}

function getStepEmoji(stepType: string): string {
  const emojiMap: Record<string, string> = {
    tool_call: '🔧',
    observation: '👁',
    thought: '💭',
  }
  return emojiMap[stepType] || '📝'
}

function getStepLabel(stepType: string, toolName?: string): string {
  if (stepType === 'tool_call') {
    return `工具调用: ${getFriendlyToolName(toolName)}`
  }
  if (stepType === 'tool_result') {
    return `工具返回: ${getFriendlyToolName(toolName)}`
  }
  if (stepType === 'observation') {
    return '观察'
  }
  return '思考过程'
}

function getFriendlyToolName(toolName?: string): string {
  if (!toolName) return '未知工具'
  return toolNameMap[toolName] || toolName
}

function getFriendlyArgs(toolName?: string, args?: Record<string, any>): Array<{key: string, value: string}> {
  if (!toolName || !args) return []
  const keyMap = toolParamKeyMap[toolName] || {}
  const valueMap = toolParamValueMap[toolName] || {}
  return Object.entries(args).map(([k, v]) => ({
    key: keyMap[k] || k,
    value: (valueMap[k] as Record<string, string>)?.[String(v)] || String(v),
  }))
}

function getFriendlyResult(toolName?: string, result?: ToolResult): string {
  if (!toolName || !result) return ''
  const formatter = toolResultFormatter[toolName]
  if (formatter) {
    return formatter(result)
  }
  return result.status === 'success' ? `完成: ${result.content?.slice(0, 50)}...` : `失败: ${result.error}`
}

function formatTime(date: Date): string {
  return date.toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
}

function getStepIcon(step: TimelineStep) {
  if (step.status === 'completed') return CheckOutlined
  if (step.status === 'running') return LoadingOutlined
  if (step.status === 'error') return CloseCircleOutlined
  return ClockCircleOutlined
}

defineExpose({ connect, disconnect, reset })

onUnmounted(() => {
  disconnect()
})
</script>

<template>
  <div class="review-timeline">
    <div class="timeline-header">
      <h3>{{ isHistorical ? '历史记录' : '智能体进度' }}</h3>
      <span v-if="isHistorical" class="historical-badge">历史</span>
    </div>
    <div class="timeline-scroll-container">
      <a-timeline mode="left" class="review-timeline">
        <a-timeline-item
          v-for="(step, index) in steps.filter(s => !(s.step_type === 'tool_result' && s.tool_result?._merged))"
          :key="index"
          :color="step.status === 'running' ? 'blue' : getStepColor(step.step_type)"
          :pending="step.status === 'running'"
        >
          <template #dot>
            <component :is="getStepIcon(step)" :class="{ 'spin-icon': step.status === 'running' }" />
          </template>

          <!-- 合并的工具调用节点 -->
          <div v-if="step.step_type === 'tool_call'" class="tool-node">
            <div class="tool-header">
              <Tag color="purple">第 {{ step.step_number }} 节</Tag>
              <span class="tool-name">{{ getFriendlyToolName(step.tool_name) }}</span>
              <Tag v-if="step.tool_result" :color="step.tool_result.status === 'success' ? 'green' : 'red'">
                {{ step.tool_result.status === 'success' ? '成功' : '失败' }}
              </Tag>
            </div>

            <!-- 调用参数 -->
            <div v-if="step.tool_args" class="tool-section call-section">
              <strong>调用参数:</strong>
              <div class="params-list">
                <div v-for="param in getFriendlyArgs(step.tool_name, step.tool_args)" :key="param.key">
                  <span class="param-key">{{ param.key }}:</span>
                  <span class="param-value">{{ param.value }}</span>
                </div>
              </div>
            </div>

            <!-- 返回结果 -->
            <div v-if="step.tool_result" class="tool-section result-section">
              <strong>返回结果:</strong>
              <div class="result-text">{{ getFriendlyResult(step.tool_name, step.tool_result) }}</div>
            </div>
          </div>

          <!-- 非工具调用的节点使用原有样式 -->
          <div v-else :class="['timeline-content-card', `card-${step.step_type}`]">
            <!-- 卡片头部 -->
            <div class="card-header">
              <Tag :color="getTagColor(step.step_type)">第 {{ step.step_number }} 节</Tag>
              <span class="step-label">
                {{ getStepEmoji(step.step_type) }} {{ getStepLabel(step.step_type, step.tool_name) }}
              </span>
              <span v-if="step.status === 'running'" class="status-running">
                <Tag color="processing">RUNNING</Tag>
              </span>
              <span v-if="step.duration" class="duration">{{ step.duration }}s</span>
              <span class="timestamp">{{ formatTime(step.timestamp) }}</span>
            </div>

            <!-- 步骤内容 -->
            <p class="step-text">{{ step.content }}</p>

            <!-- 可折叠详细信息（仅观察和思考有此项） -->
            <Collapse v-if="(step.step_type === 'observation' || step.step_type === 'thought') && (step.tool_args || step.tool_result)" class="tool-collapse" ghost>
              <CollapsePanel key="1" header="显示详细信息">
                <template v-if="step.tool_result">
                  <div class="tool-section">
                    <strong>返回结果:</strong>
                    <div class="result-text">{{ getFriendlyResult(step.tool_name, step.tool_result) }}</div>
                  </div>
                </template>
              </CollapsePanel>
            </Collapse>
          </div>
        </a-timeline-item>

        <a-timeline-item v-if="steps.length === 0" pending>
          <template #dot><ClockCircleOutlined /></template>
          <div class="timeline-empty">
            <span>等待智能体启动...</span>
          </div>
        </a-timeline-item>
      </a-timeline>
    </div>
  </div>
</template>

<style scoped>
.review-timeline {
  margin-top: 1.5rem;
  padding-top: 1rem;
  border-top: 1px solid #eee;
}

.review-timeline h3 {
  color: #555;
  font-size: 1rem;
  margin-bottom: 1rem;
}

.historical-badge {
  background: #8b5cf6;
  color: white;
  padding: 0.2rem 0.5rem;
  border-radius: 4px;
  font-size: 0.75rem;
  margin-left: 0.5rem;
}

.timeline-header {
  display: flex;
  align-items: center;
}

/* Timeline scrollable container */
.timeline-scroll-container {
  max-height: 400px;
  overflow-y: auto;
  padding-left: 0.5rem;
}

/* Ant Design Timeline overrides */
:deep(.ant-timeline) {
  padding-left: 4px;
}

:deep(.ant-timeline-item-content) {
  padding: 0 0 20px 20px !important;
}

:deep(.ant-timeline-item-tail) {
  left: 6px !important;
}

:deep(.ant-timeline-item-head) {
  left: 0 !important;
}

/* Content cards */
.timeline-content-card {
  flex: 1;
  padding: 0.75rem 1rem;
  border-radius: 6px;
  margin-bottom: 0.75rem;
  transition: transform 0.2s ease, box-shadow 0.2s ease;
}

.timeline-content-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
}

/* 渐变背景卡片 */
.card-tool_call {
  background: linear-gradient(135deg, rgb(249, 240, 255) 0%, rgb(253, 250, 255) 100%);
  border-left: 4px solid rgb(211, 173, 247);
}

.card-observation {
  background: linear-gradient(135deg, rgb(246, 255, 250) 0%, rgb(250, 255, 252) 100%);
  border-left: 4px solid rgb(183, 235, 200);
}

.card-thought {
  background: linear-gradient(135deg, rgb(240, 248, 255) 0%, rgb(245, 250, 255) 100%);
  border-left: 4px solid rgb(187, 224, 255);
}

/* 头部样式 */
.card-header {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.5rem;
  margin-bottom: 0.5rem;
}

.step-label {
  font-weight: 600;
  color: #333;
}

.status-running {
  margin-left: auto;
}

.duration {
  color: rgb(153, 153, 153);
  font-size: 0.85rem;
}

.timestamp {
  color: rgb(153, 153, 153);
  font-size: 0.85rem;
}

/* 折叠面板样式 */
.tool-collapse {
  margin-top: 0.75rem;
  border-radius: 4px;
}

.tool-params {
  margin-bottom: 0.75rem;
}

.tool-params strong {
  display: block;
  margin-bottom: 0.25rem;
  font-size: 0.85rem;
  color: #666;
}

.prompt-box {
  padding: 8px;
  background: rgb(245, 245, 245);
  border-radius: 4px;
  font-size: 12px;
  color: rgb(24, 144, 255);
  white-space: pre-wrap;
}

.tool-result {
  margin-top: 0.5rem;
}

.tool-result strong {
  display: block;
  margin-bottom: 0.25rem;
  font-size: 0.85rem;
  color: #666;
}

.tool-section {
  margin-bottom: 0.75rem;
}

.tool-section strong {
  display: block;
  margin-bottom: 0.25rem;
  font-size: 0.85rem;
  color: #666;
}

.params-list {
  padding: 8px;
  background: rgb(245, 245, 245);
  border-radius: 4px;
}

.param-item {
  display: flex;
  gap: 0.5rem;
  margin-bottom: 0.25rem;
  font-size: 0.85rem;
}

.param-key {
  color: #1890ff;
  font-weight: 500;
}

.param-value {
  color: #333;
}

.result-text {
  padding: 8px;
  background: rgb(245, 245, 245);
  border-radius: 4px;
  font-size: 0.85rem;
  color: #52c41a;
  white-space: pre-wrap;
}

.step-text {
  margin: 0;
  font-size: 0.9rem;
  color: #333;
  white-space: pre-wrap;
  word-break: break-word;
  line-height: 1.5;
}

/* Empty state */
.timeline-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.75rem;
  color: #666;
  font-style: italic;
  padding: 2rem 1rem;
  text-align: center;
}

/* 合并的工具节点样式 */
.tool-node {
  background: linear-gradient(135deg, rgb(249, 240, 255) 0%, rgb(253, 250, 255) 100%);
  border-left: 4px solid rgb(211, 173, 247);
  border-radius: 6px;
  padding: 0.75rem 1rem;
}

.tool-header {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.5rem;
  margin-bottom: 0.5rem;
}

.tool-name {
  font-weight: 600;
  color: #333;
}

.call-section {
  margin-bottom: 0.5rem;
  padding-bottom: 0.5rem;
  border-bottom: 1px dashed #ddd;
}

.result-section {
  background: rgba(82, 196, 26, 0.1);
  border-radius: 4px;
  padding: 0.5rem;
}

/* Animations */
.spin-icon {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}
</style>
