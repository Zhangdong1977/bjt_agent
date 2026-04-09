<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed, watch } from 'vue'
import type { SSEEvent } from '@/types'
import {
  CheckOutlined,
  ClockCircleOutlined,
  LoadingOutlined,
  CloseCircleOutlined,
} from '@ant-design/icons-vue'
import { Tag } from 'ant-design-vue'
import TodoListCard from './TodoListCard.vue'
import SubAgentCard from './SubAgentCard.vue'

const API_BASE = import.meta.env.VITE_API_BASE || '/api'

const props = defineProps<{
  taskId: string
  initialSteps?: TimelineStep[]  // For displaying historical steps
  historicalMode?: boolean        // Whether showing historical data
}>()

const isHistorical = computed(() => props.historicalMode)

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
  status?: 'pending' | 'running' | 'completed' | 'error'
  duration?: number
  tool_args?: {
    tool_calls?: ToolCall[]
  }
  tool_result?: {
    tool_results?: ToolResult[]
  }
  data?: Record<string, any>
}

interface TodoItemState {
  id: string
  rule_doc_name: string
  check_items: CheckItemState[]
  status: 'pending' | 'running' | 'completed' | 'failed'
  result?: {
    findings: any[]
  }
  error_message?: string
}

interface CheckItemState {
  id: string
  title: string
  status: 'pending' | 'running' | 'completed' | 'failed'
}

const steps = ref<TimelineStep[]>([])
const phase = ref<'master' | 'todo' | 'sub_agents' | 'merging' | 'completed'>('master')
const todos = ref<Map<string, TodoItemState>>(new Map())
const isMerging = ref(false)
const mergeProgress = ref('')

// Convert Map to array for TodoListCard and SubAgentCard
const todoList = computed(() => Array.from(todos.value.values()))

// Track agent index by todo id for SubAgentCard
const agentIndexMap = computed(() => {
  const map = new Map<string, number>()
  let idx = 1
  for (const todo of todos.value.values()) {
    map.set(todo.id, idx++)
  }
  return map
})
let eventSource: EventSource | null = null

// 工具名称映射
const toolNameMap: Record<string, string> = {
  search_tender_doc: '搜索文档',
  rag_search: '搜索知识库',
  comparator: '内容比对',
}

onMounted(() => {
  // Always set initial steps if available, regardless of historicalMode
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
  } else {
    steps.value = []  // 清理步骤，避免从历史模式切回时残留
  }
}, { deep: true })

// Watch for taskId changes to reconnect SSE
watch(() => props.taskId, (newTaskId, oldTaskId) => {
  if (!props.historicalMode && newTaskId && newTaskId !== oldTaskId) {
    steps.value = []  // 清理旧步骤
    disconnect()      // 断开旧连接
    connect(newTaskId) // 连接新 SSE
  }
})

// Watch for historicalMode changes
watch(() => props.historicalMode, (isHistorical) => {
  if (isHistorical) {
    // Entering historical mode: load historical steps from props
    if (props.initialSteps?.length) {
      steps.value = props.initialSteps
    }
    disconnect()  // 断开 SSE 连接
  } else if (props.taskId) {
    steps.value = []  // 清理步骤
    connect(props.taskId)  // 连接 SSE
  }
})

function handleSSEEvent(event: SSEEvent) {
  // Master agent events
  if (event.type === 'master_started') {
    // 主代理开始解析
    steps.value = []
    phase.value = 'master'
  }

  if (event.type === 'master_scan_completed') {
    // 扫描完成，添加到时间轴
    steps.value.push({
      step_number: 1,
      step_type: 'master',
      content: `扫描规则库完成，发现 ${(event as any).total_docs} 个规则文档`,
      timestamp: new Date(),
      data: { rule_docs: (event as any).rule_docs }
    })
  }

  if (event.type === 'todo_created') {
    // 新建 Todo 项
    addTodoItem(event as any)
    phase.value = 'todo'
  }

  if (event.type === 'todo_list_completed') {
    // Todo 列表完成
    phase.value = 'sub_agents'
  }

  if (event.type === 'sub_agent_started') {
    // 子代理开始
    updateTodoStatus((event as any).todo_id, 'running')
  }

  if (event.type === 'sub_agent_progress') {
    // 子代理进度
    updateTodoProgress((event as any).todo_id, (event as any).progress, (event as any).current_check)
  }

  if (event.type === 'sub_agent_completed') {
    // 子代理完成
    updateTodoStatus((event as any).todo_id, 'completed', (event as any).findings_count)
  }

  if (event.type === 'sub_agent_failed') {
    // 子代理失败
    updateTodoStatus((event as any).todo_id, 'failed', 0, (event as any).error)
  }

  if (event.type === 'merging_started') {
    phase.value = 'merging'
  }

  if (event.type === 'merging_completed') {
    phase.value = 'completed'
  }

  if (event.type === 'step' && event.step_number !== undefined) {
    if (event.step_type === 'tool_result') {
      // tool_result: 查找对应的 step 并合并 tool_results
      const pairedStep = steps.value.find(s =>
        s.step_number === event.step_number! - 1 &&
        s.step_type !== 'tool_result'
      )
      if (pairedStep) {
        // Backend sends {tool_results: [...]} structure - use any to handle type mismatch
        const toolResultData = event.tool_result as any
        pairedStep.tool_result = { tool_results: toolResultData?.tool_results || [toolResultData] }
      }
    } else {
      // observation/thought/tool_call: 直接添加或更新现有 step
      // Backend sends tool_calls as direct array, not wrapped in tool_args
      // Convert: tool_calls -> tool_args.tool_calls
      const toolCalls = (event as any).tool_calls as Array<{ name: string; arguments: Record<string, any> }> | undefined
      const toolResults = (event as any).tool_results as Array<{ name: string; result: any }> | undefined

      // 跳过空的 observation/thought（content 为空且没有工具调用）
      if (!event.content && !toolCalls?.length) {
        return
      }
      // 去重检查：按 step_number 检查
      const existingIndex = steps.value.findIndex(s =>
        s.step_number === event.step_number
      )
      // Backend sends tool_calls/tool_results as flat arrays
      // Convert to TimelineStep format: {tool_calls: [...]} and {tool_results: [...]}
      const stepData: TimelineStep = {
        step_number: event.step_number,
        step_type: event.step_type || 'unknown',
        content: event.content || '',
        timestamp: new Date(),
        tool_args: toolCalls ? { tool_calls: toolCalls } : undefined,
        tool_result: toolResults ? { tool_results: toolResults } : undefined,
      }
      if (existingIndex >= 0) {
        // 更新现有 step
        steps.value[existingIndex] = { ...steps.value[existingIndex], ...stepData }
      } else {
        steps.value.push(stepData)
      }
    }
  } else if (event.type === 'status' && event.status === 'running') {
    steps.value = []
  } else if (event.type === 'merging') {
    // 收到 merging 事件，显示合并动画
    isMerging.value = true
    mergeProgress.value = event.message || '正在合并历史结果...'
  } else if (event.type === 'merged') {
    // 收到 merged 事件，隐藏合并动画
    isMerging.value = false
    mergeProgress.value = ''
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

// Helper methods for todo management
function addTodoItem(event: SSEEvent) {
  const todoItem: TodoItemState = {
    id: event.todo_id || '',
    rule_doc_name: event.rule_doc_name || '',
    check_items: (event.check_items || []).map((item: any) => ({
      id: item.id || '',
      title: item.title || '',
      status: 'pending' as const
    })),
    status: 'pending'
  }
  todos.value.set(todoItem.id, todoItem)
}

function updateTodoStatus(todoId: string, status: TodoItemState['status'], _findingsCount?: number, error?: string) {
  const todo = todos.value.get(todoId)
  if (todo) {
    todo.status = status
    if (status === 'completed') {
      todo.result = { findings: [] }
    }
    if (error) {
      todo.error_message = error
    }
    todos.value.set(todoId, { ...todo })
  }
}

function updateTodoProgress(todoId: string, _progress: number, currentCheck: string) {
  const todo = todos.value.get(todoId)
  if (todo) {
    // Update check items status
    todo.check_items.forEach(item => {
      if (item.title === currentCheck) {
        item.status = 'running'
      }
    })
    todos.value.set(todoId, { ...todo })
  }
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

function getStepLabel(stepType: string): string {
  if (stepType === 'observation') {
    return '观察'
  }
  if (stepType === 'thought') {
    return '思考过程'
  }
  return stepType
}

function getFriendlyToolName(toolName?: string): string {
  if (!toolName) return '未知工具'
  return toolNameMap[toolName] || toolName
}

// 人类友好的工具参数标签映射
const argLabelMap: Record<string, string> = {
  doc_type: '文档类型',
  query: '查询',
  requirement: '需求',
  bid_content: '投标内容',
  severity: '严重程度',
  full_content: '完整内容',
  chunk: '分块',
  limit: '数量限制',
}

// 文档类型值映射
const docTypeMap: Record<string, string> = {
  tender: '招标书',
  bid: '投标书',
}

function formatToolArg(key: string, value: any): string {
  // 映射标签
  const label = argLabelMap[key] || key
  // 映射值
  let displayValue = value
  if (key === 'doc_type' && docTypeMap[value]) {
    displayValue = docTypeMap[value]
  } else if (typeof value === 'boolean') {
    displayValue = value ? '是' : '否'
  } else if (typeof value === 'object') {
    displayValue = JSON.stringify(value).slice(0, 50)
    if (JSON.stringify(value).length > 50) displayValue += '...'
  } else if (typeof value === 'string' && value.length > 100) {
    displayValue = value.slice(0, 100) + '...'
  }
  return `${label}: ${displayValue}`
}

function formatToolResult(toolResult: ToolResult): string {
  if (!toolResult) return ''
  // tool_result has {name, result: {status, content, error}} format
  if (toolResult.name && toolResult.result) {
    const result = toolResult.result as any
    // Use human-friendly content if available
    if (result.status === 'success' && result.content) {
      const content = result.content.length > 200
        ? result.content.slice(0, 200) + '...'
        : result.content
      return `${getFriendlyToolName(toolResult.name)}: ${content}`
    }
    if (result.status === 'error') {
      return `${getFriendlyToolName(toolResult.name)}: 失败 - ${result.error || 'unknown'}`
    }
    // Fallback for other object formats
    const resultContent = typeof result === 'object'
      ? JSON.stringify(result).slice(0, 100)
      : String(result)
    return `${getFriendlyToolName(toolResult.name)}: ${resultContent}...`
  }
  // Legacy format
  const result = toolResult as any
  if (result.status === 'success') {
    return `完成: ${result.content?.slice(0, 200) || ''}...`
  }
  return `失败: ${result.error || 'unknown'}`
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
          v-for="(step, index) in steps"
          :key="index"
          :color="step.status === 'running' ? 'blue' : getStepColor(step.step_type)"
          :pending="step.status === 'running'"
        >
          <template #dot>
            <component :is="getStepIcon(step)" :class="{ 'spin-icon': step.status === 'running' }" />
          </template>

          <!-- 统一的步骤卡片 -->
          <div :class="['timeline-content-card', `card-${step.step_type}`]">
            <!-- 卡片头部 -->
            <div class="card-header">
              <Tag :color="getTagColor(step.step_type)">第 {{ step.step_number }} 步</Tag>
              <span class="step-label">
                {{ getStepEmoji(step.step_type) }} {{ getStepLabel(step.step_type) }}
              </span>
              <span v-if="step.status === 'running'" class="status-running">
                <Tag color="processing">RUNNING</Tag>
              </span>
              <span v-if="step.duration" class="duration">{{ step.duration }}s</span>
              <span class="timestamp">{{ formatTime(step.timestamp) }}</span>
            </div>

            <!-- 步骤内容 -->
            <p v-if="step.content" class="step-text">{{ step.content }}</p>

            <!-- 内嵌的工具调用列表 -->
            <div v-if="step.tool_args?.tool_calls?.length" class="tool-calls-section">
              <div
                v-for="(toolCall, tcIndex) in step.tool_args.tool_calls"
                :key="tcIndex"
                class="tool-call-item"
              >
                <div class="tool-call-header">
                  <Tag color="purple" size="small">🔧 {{ getFriendlyToolName(toolCall.name) }}</Tag>
                </div>
                <div class="tool-call-args">
                  <span v-for="(value, key) in toolCall.arguments" :key="key" class="param-tag">
                    {{ formatToolArg(key, value) }}
                  </span>
                </div>
                <!-- 配对的工具返回结果 -->
                <div v-if="step.tool_result?.tool_results?.[tcIndex]" class="tool-call-result">
                  <span class="row-label">结果:</span>
                  <span class="row-content result">
                    {{ formatToolResult(step.tool_result.tool_results[tcIndex]) }}
                  </span>
                </div>
              </div>
            </div>
          </div>
        </a-timeline-item>

        <a-timeline-item v-if="steps.length === 0 && !isMerging" pending>
          <template #dot><ClockCircleOutlined /></template>
          <div class="timeline-empty">
            <span>等待智能体启动...</span>
          </div>
        </a-timeline-item>

        <!-- Todo list phase: show TodoListCard for each pending todo -->
        <a-timeline-item v-if="phase === 'todo' && todoList.length > 0">
          <template #dot><LoadingOutlined class="spin-icon" /></template>
          <div class="phase-card phase-todo">
            <div class="phase-header">
              <Tag color="processing">待执行</Tag>
              <span class="phase-title">准备分配检查任务</span>
              <span class="phase-count">{{ todoList.length }} 个规则文档</span>
            </div>
            <div class="phase-body">
              <TodoListCard
                v-for="todo in todoList"
                :key="todo.id"
                :todo="todo as any"
              />
            </div>
          </div>
        </a-timeline-item>

        <!-- Sub agents phase: show SubAgentCard for each todo -->
        <a-timeline-item v-if="phase === 'sub_agents' && todoList.length > 0">
          <template #dot><LoadingOutlined class="spin-icon" /></template>
          <div class="phase-card phase-agents">
            <div class="phase-header">
              <Tag color="purple">执行中</Tag>
              <span class="phase-title">子智能体检查中</span>
            </div>
            <div class="phase-body">
              <SubAgentCard
                v-for="todo in todoList"
                :key="todo.id"
                :todo="todo as any"
                :agent-index="agentIndexMap.get(todo.id) || 1"
              />
            </div>
          </div>
        </a-timeline-item>

        <!-- 合并阶段动画 -->
        <a-timeline-item v-if="isMerging" pending>
          <template #dot><LoadingOutlined class="spin-icon" /></template>
          <div class="merge-progress-card">
            <div class="merge-progress-header">
              <Tag color="processing">合并中</Tag>
              <span class="merge-progress-text">{{ mergeProgress }}</span>
            </div>
            <div class="merge-progress-hint">
              <span>正在处理历史审查结果，这可能需要一些时间...</span>
            </div>
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

/* Timeline container - full height, no scroll */
.timeline-scroll-container {
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

/* Tool calls section - embedded within each step */
.tool-calls-section {
  margin-top: 0.75rem;
  padding: 0.5rem;
  background: rgba(0, 0, 0, 0.02);
  border-radius: 4px;
}

.tool-call-item {
  margin-bottom: 0.5rem;
  padding: 0.5rem;
  background: white;
  border-radius: 4px;
  border: 1px solid #f0f0f0;
}

.tool-call-item:last-child {
  margin-bottom: 0;
}

.tool-call-header {
  margin-bottom: 0.3rem;
}

.tool-call-args {
  font-size: 0.85rem;
  color: #666;
  margin-bottom: 0.3rem;
}

.tool-call-result {
  display: flex;
  align-items: flex-start;
  gap: 0.5rem;
  font-size: 0.85rem;
  line-height: 1.4;
  padding-top: 0.3rem;
  border-top: 1px dashed #f0f0f0;
}

.row-label {
  color: #888;
  min-width: 36px;
  flex-shrink: 0;
}

.row-content {
  color: #555;
}

.row-content.result {
  color: #666;
}

.param-tag {
  display: inline;
  margin-right: 0.75rem;
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

/* Merge progress card */
.merge-progress-card {
  background: linear-gradient(135deg, rgb(255, 251, 235) 0%, rgb(255, 252, 245) 100%);
  border-left: 4px solid rgb(255, 189, 46);
  border-radius: 6px;
  padding: 1rem 1.25rem;
  margin-bottom: 0.75rem;
}

.merge-progress-header {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin-bottom: 0.5rem;
}

.merge-progress-text {
  font-weight: 600;
  color: #d46b08;
}

.merge-progress-hint {
  font-size: 0.85rem;
  color: #8c8c8c;
}

/* Phase cards */
.phase-card {
  border-radius: 6px;
  padding: 0.75rem 1rem;
  margin-bottom: 0.75rem;
}

.phase-todo {
  background: linear-gradient(135deg, rgb(255, 251, 235) 0%, rgb(255, 252, 245) 100%);
  border-left: 4px solid rgb(255, 189, 46);
}

.phase-agents {
  background: linear-gradient(135deg, rgb(249, 240, 255) 0%, rgb(253, 250, 255) 100%);
  border-left: 4px solid rgb(211, 173, 247);
}

.phase-header {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin-bottom: 0.75rem;
}

.phase-title {
  font-weight: 600;
  color: #333;
}

.phase-count {
  color: #8c8c8c;
  font-size: 0.85rem;
  margin-left: auto;
}

.phase-body {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
</style>
