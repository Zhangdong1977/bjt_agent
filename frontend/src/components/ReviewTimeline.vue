<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import type { SSEEvent } from '@/types'

defineProps<{
  taskId: string
}>()

interface TimelineStep {
  step_number: number
  step_type: string
  tool_name?: string
  content: string
  timestamp: Date
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

defineExpose({ connect, disconnect, reset })

onUnmounted(() => {
  disconnect()
})
</script>

<template>
  <div class="review-timeline">
    <h3>Agent Progress</h3>
    <div class="timeline-steps">
      <div
        v-for="(step, index) in steps"
        :key="index"
        :class="['timeline-step', `step-${step.step_type}`]"
      >
        <div class="step-indicator">
          <span class="step-number">{{ step.step_number }}</span>
        </div>
        <div class="step-content">
          <span class="step-type">
            {{ step.step_type === 'tool_call' ? `🔧 ${step.tool_name || 'tool'}` : '💭' }}
          </span>
          <p class="step-text">{{ step.content }}</p>
        </div>
      </div>
      <div v-if="steps.length === 0" class="timeline-empty">
        Waiting for agent to start...
      </div>
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

.timeline-steps {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  max-height: 300px;
  overflow-y: auto;
}

.timeline-step {
  display: flex;
  gap: 0.75rem;
  align-items: flex-start;
}

.step-indicator {
  flex-shrink: 0;
  width: 1.75rem;
  height: 1.75rem;
  border-radius: 50%;
  background: #667eea;
  color: white;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.75rem;
  font-weight: bold;
}

.step-content {
  flex: 1;
  background: #f5f5f5;
  padding: 0.5rem 0.75rem;
  border-radius: 4px;
}

.step-type {
  font-size: 0.85rem;
  font-weight: 500;
  color: #555;
}

.step-text {
  margin: 0.25rem 0 0 0;
  font-size: 0.9rem;
  color: #333;
  white-space: pre-wrap;
  word-break: break-word;
}

.step-tool_call .step-indicator {
  background: #f6ad55;
}

.step-observation .step-indicator {
  background: #68d391;
}

.timeline-empty {
  color: #666;
  font-style: italic;
  padding: 1rem;
  text-align: center;
}
</style>
