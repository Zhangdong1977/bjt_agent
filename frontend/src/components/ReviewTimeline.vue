<script setup lang="ts">
import { ref, onUnmounted } from 'vue'
import type { SSEEvent } from '@/types'
import { Timeline } from 'ant-design-vue'
import {
  CheckOutlined,
  ClockCircleOutlined,
  LoadingOutlined,
  CloseCircleOutlined,
  ToolOutlined,
  EyeOutlined,
  BulbOutlined,
} from '@ant-design/icons-vue'

defineProps<{
  taskId: string
}>()

// 注册 Ant Design Timeline 组件
const TimelineItem = Timeline.Item

interface TimelineStep {
  step_number: number
  step_type: string
  tool_name?: string
  content: string
  timestamp: Date
  status?: 'pending' | 'running' | 'completed' | 'error'
}

const steps = ref<TimelineStep[]>([])
let eventSource: EventSource | null = null

function handleSSEEvent(event: SSEEvent) {
  if (event.type === 'step' && event.step_number !== undefined) {
    steps.value.push({
      step_number: event.step_number,
      step_type: event.step_type || 'unknown',
      tool_name: event.tool_name,
      content: event.content || '',
      timestamp: new Date(),
    })
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
    <h3>智能体进度</h3>
    <a-timeline mode="left" class="review-timeline">
      <a-timeline-item
        v-for="(step, index) in steps"
        :key="index"
        :color="getStepColor(step.step_type)"
        :pending="step.status === 'running'"
      >
        <template #dot>
          <CheckOutlined v-if="step.status === 'completed'" />
          <ClockCircleOutlined v-else-if="step.status === 'pending'" />
          <LoadingOutlined v-else-if="step.status === 'running'" class="spin-icon" />
          <CloseCircleOutlined v-else-if="step.status === 'error'" />
        </template>

        <div :class="['timeline-content-card', `card-${step.step_type}`]">
          <div class="card-header">
            <span :class="['step-icon', `icon-${step.step_type}`]">
              <ToolOutlined v-if="step.step_type === 'tool_call'" />
              <EyeOutlined v-else-if="step.step_type === 'observation'" />
              <BulbOutlined v-else />
            </span>
            <span class="step-type">
              {{ step.step_type === 'tool_call' ? `${step.tool_name || '工具'}` : step.step_type === 'observation' ? '观察' : '思考' }}
            </span>
          </div>
          <p class="step-text">{{ step.content }}</p>
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

/* Timeline container scrollable */
:deep(.review-timeline) {
  max-height: 400px;
  overflow-y: auto;
  padding-left: 0.5rem;
}

/* Content cards */
.timeline-content-card {
  flex: 1;
  padding: 0.75rem 1rem;
  border-radius: 6px;
  background: #fff;
  border-left: 4px solid;
  margin-bottom: 0.75rem;
  transition: transform 0.2s ease, box-shadow 0.2s ease;
}

.timeline-content-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
}

.card-tool_call {
  border-left-color: #fa8c16;
}

.card-observation {
  border-left-color: #52c41a;
}

.card-thought {
  border-left-color: #1890ff;
}

.card-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.5rem;
}

.step-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
}

.icon-tool_call {
  color: #fa8c16;
}

.icon-observation {
  color: #52c41a;
}

.icon-thought {
  color: #1890ff;
}

.step-type {
  font-size: 0.85rem;
  font-weight: 600;
  color: #555;
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
