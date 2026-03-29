<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed } from 'vue'
import type { SSEEvent } from '@/types'
import {
  CheckOutlined,
  ClockCircleOutlined,
  LoadingOutlined,
  CloseCircleOutlined,
} from '@ant-design/icons-vue'
import { Tag, Collapse, CollapsePanel } from 'ant-design-vue'

const props = defineProps<{
  taskId: string
  initialSteps?: TimelineStep[]  // For displaying historical steps
  historicalMode?: boolean        // Whether showing historical data
}>()

const isHistorical = computed(() => props.historicalMode)

interface TimelineStep {
  step_number: number
  step_type: string
  tool_name?: string
  content: string
  timestamp: Date
  status?: 'pending' | 'running' | 'completed' | 'error'
  duration?: number      // 耗时（秒）
  tool_params?: {       // 工具调用参数
    prompt: string
  }
  tool_result?: string   // 工具调用结果
}

const steps = ref<TimelineStep[]>([])
let eventSource: EventSource | null = null

onMounted(() => {
  if (props.initialSteps?.length) {
    steps.value = props.initialSteps
  }
  if (!props.historicalMode) {
    connect(props.taskId)
  }
})

function handleSSEEvent(event: SSEEvent) {
  if (event.type === 'step' && event.step_number !== undefined) {
    // Deduplicate by step_number to prevent duplicate entries on SSE reconnect
    const exists = steps.value.some(s => s.step_number === event.step_number)
    if (!exists) {
      steps.value.push({
        step_number: event.step_number,
        step_type: event.step_type || 'unknown',
        tool_name: event.tool_name,
        content: event.content || '',
        timestamp: new Date(),
      })
    }
  } else if (event.type === 'status' && event.status === 'running') {
    steps.value = []
  }
}

function connect(taskId: string) {
  disconnect()
  eventSource = new EventSource(`/api/events/tasks/${taskId}/stream`)

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
    return `工具调用: ${toolName || 'unknown'}`
  }
  if (stepType === 'observation') {
    return '观察'
  }
  return '思考过程'
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

          <div :class="['timeline-content-card', `card-${step.step_type}`]">
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

            <!-- 可折叠详细信息（仅工具调用有此项） -->
            <Collapse v-if="step.step_type === 'tool_call' && step.tool_params" class="tool-collapse" ghost>
              <CollapsePanel key="1" header="显示详细信息">
                <div class="tool-params">
                  <strong>提示词:</strong>
                  <div class="prompt-box">{{ step.tool_params.prompt }}</div>
                </div>
                <div v-if="step.tool_result" class="tool-result">
                  <strong>结果:</strong>
                  <div>{{ step.tool_result }}</div>
                </div>
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
