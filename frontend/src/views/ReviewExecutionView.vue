<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import { useProjectStore } from '@/stores/project'
import ExecutionHeader from '@/components/execution/ExecutionHeader.vue'
import ExecutionStepper from '@/components/execution/ExecutionStepper.vue'
import LeftPane from '@/components/execution/LeftPane.vue'
import RightSidebar from '@/components/execution/RightSidebar.vue'

const API_BASE = import.meta.env.VITE_API_BASE || '/api'
const route = useRoute()
const projectStore = useProjectStore()

const projectId = computed(() => route.params.id as string)

// SSE 连接
let eventSource: EventSource | null = null

// 状态
// phase: 根据实际事件流转 - pending -> running -> completed/failed
const phase = ref<'pending' | 'running' | 'completed' | 'failed'>('pending')
const steps = ref<any[]>([])
const errorMessage = ref<string | null>(null)
const findingsCount = ref<number>(0)

// 统计数据
const totalSteps = computed(() => steps.value.length)
const completedCount = computed(() =>
  steps.value.filter(s => s.step_type === 'tool_result').length
)

// SSE 事件处理 - 适配实际后端事件
function handleSSEEvent(event: any) {
  console.log('[ReviewExecutionView] SSE event received:', event.type, event)

  switch (event.type) {
    case 'status':
      // 状态更新事件
      if (event.status === 'running') {
        phase.value = 'running'
      } else if (event.status === 'completed') {
        phase.value = 'completed'
      } else if (event.status === 'failed') {
        phase.value = 'failed'
      }
      break

    case 'progress':
      // 进度消息事件
      console.log('[ReviewExecutionView] Progress:', event.message)
      break

    case 'step':
      // Agent 执行步骤事件 - 这是核心事件
      if (event.step_number !== undefined) {
        // 跳过空的 step
        if (!event.content && !event.tool_calls?.length) {
          break
        }

        // 检查是否已存在该 step（去重）
        const existingIndex = steps.value.findIndex(
          s => s.step_number === event.step_number && s.step_type !== 'tool_result'
        )

        if (existingIndex >= 0) {
          // 更新现有 step
          steps.value[existingIndex] = {
            ...steps.value[existingIndex],
            step_number: event.step_number,
            step_type: event.step_type || 'unknown',
            content: event.content || '',
            timestamp: new Date(),
            tool_calls: event.tool_calls || [],
            tool_results: event.tool_results || [],
          }
        } else {
          // 添加新 step
          steps.value.push({
            step_number: event.step_number,
            step_type: event.step_type || 'unknown',
            content: event.content || '',
            timestamp: new Date(),
            tool_calls: event.tool_calls || [],
            tool_results: event.tool_results || [],
          })
        }

        // 如果收到第一个 step 事件，说明 agent 已开始运行
        if (phase.value === 'pending') {
          phase.value = 'running'
        }
      }
      break

    case 'complete':
      // 审查完成事件
      phase.value = 'completed'
      findingsCount.value = event.findings_count || 0
      break

    case 'error':
      // 错误事件
      phase.value = 'failed'
      errorMessage.value = event.message || 'Unknown error'
      break

    case 'merging':
      // 合并历史结果（不影响 phase）
      console.log('[ReviewExecutionView] Merging historical results...')
      break

    case 'merged':
      // 合并完成
      console.log('[ReviewExecutionView] Merge complete')
      break
  }
}

function connect() {
  if (!projectStore.currentTask?.id) {
    console.log('[ReviewExecutionView] No currentTask.id, skipping SSE connection')
    return
  }

  disconnect()
  const url = `${API_BASE}/events/tasks/${projectStore.currentTask.id}/stream`
  console.log('[ReviewExecutionView] Connecting to SSE:', url)

  eventSource = new EventSource(url)

  eventSource.onopen = () => {
    console.log('[ReviewExecutionView] SSE connection opened')
  }

  eventSource.onmessage = (e) => {
    try {
      const data = JSON.parse(e.data)
      handleSSEEvent(data)
    } catch (err) {
      console.error('[ReviewExecutionView] Failed to parse SSE event:', err)
    }
  }

  eventSource.onerror = (err) => {
    console.error('[ReviewExecutionView] SSE error:', err)
    // 不要立即断开，尝试重连
  }
}

function disconnect() {
  if (eventSource) {
    eventSource.close()
    eventSource = null
  }
}

const currentStatus = computed(() => {
  if (errorMessage.value) return 'failed'
  if (phase.value === 'completed') return 'completed'
  if (phase.value === 'running') return 'running'
  return 'pending'
})

// 格式化 steps 用于 LeftPane
const timelineSteps = computed(() => steps.value.map(s => ({
  step_number: s.step_number,
  step_type: s.step_type,
  content: s.content,
  timestamp: s.timestamp,
  tool_args: s.tool_calls ? { tool_calls: s.tool_calls } : undefined,
  tool_result: s.tool_results ? { tool_results: s.tool_results } : undefined,
})))

onMounted(async () => {
  console.log('[ReviewExecutionView] onMounted, projectId:', projectId.value)

  // 加载项目信息
  await projectStore.selectProject(projectId.value)

  // 获取当前项目的审查任务
  await projectStore.fetchReviewTasks()

  // 查找最新的任务（可能是刚创建的或正在运行的）
  if (projectStore.reviewTasks.length > 0) {
    // 使用最新的任务
    const latestTask = projectStore.reviewTasks[0]
    console.log('[ReviewExecutionView] Latest task:', latestTask.id, latestTask.status)

    await projectStore.selectReviewTask(latestTask.id)

    // 如果任务已完成或失败，设置相应状态
    if (latestTask.status === 'completed') {
      phase.value = 'completed'
    } else if (latestTask.status === 'failed') {
      phase.value = 'failed'
      errorMessage.value = projectStore.currentTask?.error_message || '审查失败'
    }
  }

  // 建立 SSE 连接以接收实时更新
  if (projectStore.currentTask?.id) {
    connect()
  } else {
    console.log('[ReviewExecutionView] No currentTask, SSE will not connect')
  }
})

onUnmounted(() => {
  disconnect()
})
</script>

<template>
  <div class="review-execution-view">
    <ExecutionHeader
      :project-name="projectStore.currentProject?.name || '项目'"
      :status="currentStatus"
    />

    <div class="main-layout">
      <ExecutionStepper :phase="phase" />

      <div class="content-area">
        <LeftPane
          :phase="phase"
          :steps="timelineSteps"
          :error-message="errorMessage"
        />

        <RightSidebar
          :total-steps="totalSteps"
          :completed-count="completedCount"
          :findings-count="findingsCount"
          :phase="phase"
        />
      </div>
    </div>
  </div>
</template>

<style scoped>
.review-execution-view {
  min-height: 100vh;
  background: var(--bg);
}

.main-layout {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 56px);
  padding: 20px;
}

.content-area {
  display: grid;
  grid-template-columns: 1fr 320px;
  flex: 1;
  min-height: 0;
  border-radius: var(--r2);
  overflow: hidden;
  border: 1px solid var(--line);
}
</style>