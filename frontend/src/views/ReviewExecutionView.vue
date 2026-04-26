<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useProjectStore } from '@/stores/project'
import { getAccessToken, reviewApi } from '@/api/client'
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

// 合并阶段状态
const isMerging = ref(false)
const mergeProgress = ref('')
const subAgentSteps = ref<Map<string, any[]>>(new Map())
const errorMessage = ref<string | null>(null)
const findingsCount = ref<number>(0)
const mergedStats = ref<{ total: number; critical: number; major: number; minor: number; compliant: number } | null>(null)

// Todo items state (mirrors ReviewTimeline.vue)
interface CheckItemState {
  id: string
  title: string
  status: 'pending' | 'running' | 'completed' | 'failed'
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

const todos = ref<Map<string, TodoItemState>>(new Map())
const todoList = computed(() => Array.from(todos.value.values()))

// 追踪待处理的 tool_calls 和 tool_results，用于组合细粒度事件
// Map<todo_id, Map<step_number, { tool_calls: any[], tool_results: any[] }>>
const pendingToolCallsMap = ref<Map<string, Map<number, { tool_calls: any[], tool_results: any[] }>>>(new Map())

// maxStepsMap and taskStartTime for execution stats
const maxStepsMap = ref<Record<string, number>>({})
const taskStartTime = ref<number>(0)

// 统计数据
const totalSteps = computed(() => steps.value.length)
const completedCount = computed(() =>
  steps.value.filter(s => s.step_type === 'tool_result').length
)

// SSE 事件处理 - 适配实际后端事件
async function handleSSEEvent(event: any) {
  // 详细日志，方便调试 SSE 数据
  console.log('[ReviewExecutionView] SSE event received:', event)

  switch (event.type) {
    case 'status':
      // 状态更新事件
      console.log('[ReviewExecutionView] status event received, status:', event.status, 'current phase:', phase.value)
      if (event.status === 'running') {
        if (phase.value !== 'running') {
          phase.value = 'running'
          console.log('[ReviewExecutionView] phase set to running')
        }
      } else if (event.status === 'completed') {
        phase.value = 'completed'
      } else if (event.status === 'failed') {
        phase.value = 'failed'
      }
      break

    case 'master_started':
      // 已完成的任务跳过重置，防止历史时间线数据被 SSE 缓存事件清空
      if (phase.value === 'completed') break
      // 主代理开始解析，重置 steps 和 subAgentSteps
      steps.value = []
      subAgentSteps.value = new Map()
      break

    case 'master_scan_completed':
      // 扫描完成
      break

    case 'todo_created':
      // 新建 Todo 项
      addTodoItem(event)
      break

    case 'todo_list_completed':
      // Todo 列表完成
      break

    case 'sub_agent_started':
    case 'sub_agent_sub_agent_started':
      // 子代理开始
      updateTodoStatus(event.todo_id, 'running')
      // 收集 max_steps
      if (event.max_steps !== undefined) {
        maxStepsMap.value[event.todo_id] = event.max_steps
      }
      // 记录任务开始时间
      if (!taskStartTime.value) {
        taskStartTime.value = Date.now()
      }
      break

    case 'sub_agent_progress':
      // 子代理进度
      updateTodoProgress(event.todo_id, event.progress, event.current_check)
      break

    case 'sub_agent_completed':
      // 子代理完成
      updateTodoStatus(event.todo_id, 'completed', event.findings)
      break

    case 'sub_agent_failed':
      // 子代理失败
      updateTodoStatus(event.todo_id, 'failed', undefined, event.error)
      break

    case 'merging_started':
      break

    case 'merging_completed':
      phase.value = 'completed'
      break

    case 'progress':
      // 进度消息事件
      console.log('[ReviewExecutionView] Progress:', event.message)
      break

    case 'step':
      // Agent 执行步骤事件 - 这是核心事件
      if (event.step_number !== undefined) {
        // 不过滤任何 step 类型，即使是 observation 也要添加
        // 移除原来的 content/tool_calls 检查，确保所有 step 都被记录

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
          console.log('[ReviewExecutionView] Step updated:', event.step_number, event.step_type)
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
          console.log('[ReviewExecutionView] Step added, total steps:', steps.value.length)
        }

        // 如果收到第一个 step 事件，说明 agent 已开始运行
        if (phase.value === 'pending') {
          phase.value = 'running'
        }
      }
      break

    case 'sub_agent_step':
      // 子代理执行步骤事件 - 存储到独立的 subAgentSteps Map
      if (event.step_number !== undefined && event.todo_id) {
        const todoId = event.todo_id
        const existingSteps = subAgentSteps.value.get(todoId) || []

        // 检查是否已存在该 step（去重）
        const existingIndex = existingSteps.findIndex(
          s => s.step_number === event.step_number && s.step_type !== 'tool_result'
        )

        if (existingIndex >= 0) {
          // 更新现有 step
          existingSteps[existingIndex] = {
            ...existingSteps[existingIndex],
            step_number: event.step_number,
            step_type: event.step_type || 'unknown',
            content: event.content || '',
            timestamp: new Date(),
            tool_calls: event.tool_calls || [],
            tool_results: event.tool_results || [],
          }
        } else {
          // 添加新 step
          existingSteps.push({
            step_number: event.step_number,
            step_type: event.step_type || 'unknown',
            content: event.content || '',
            timestamp: new Date(),
            tool_calls: event.tool_calls || [],
            tool_results: event.tool_results || [],
          })
        }

        subAgentSteps.value.set(todoId, [...existingSteps])
        console.log('[ReviewExecutionView] Sub-agent step added for', todoId, 'total steps:', existingSteps.length)
      }
      break

    // 处理 Mini-Agent 细粒度事件 (sub_agent_* 前缀)
    case 'sub_agent_step_start':
      // 子代理 step 开始 - 初始化 pending tool_calls
      if (event.todo_id && event.step !== undefined) {
        const todoId = event.todo_id
        const stepNum = event.step
        if (!pendingToolCallsMap.value.has(todoId)) {
          pendingToolCallsMap.value.set(todoId, new Map())
        }
        const todoPending = pendingToolCallsMap.value.get(todoId)!
        todoPending.set(stepNum, { tool_calls: [], tool_results: [] })
        console.log('[ReviewExecutionView] sub_agent_step_start for', todoId, 'step', stepNum)
      }
      break

    case 'sub_agent_llm_output':
      // 子代理 LLM 输出 - 包含 content 和 tool_calls
      if (event.todo_id && event.step !== undefined) {
        const todoId = event.todo_id
        const stepNum = event.step
        const content = event.content || ''
        const toolCalls = event.tool_calls || []

        // 存储 tool_calls 到 pending map
        if (pendingToolCallsMap.value.has(todoId)) {
          const todoPending = pendingToolCallsMap.value.get(todoId)!
          if (todoPending.has(stepNum)) {
            todoPending.get(stepNum)!.tool_calls = toolCalls
          }
        }

        // 添加或更新 step
        const existingSteps = subAgentSteps.value.get(todoId) || []
        const existingIndex = existingSteps.findIndex(
          s => s.step_number === stepNum && s.step_type !== 'tool_result'
        )

        if (existingIndex >= 0) {
          existingSteps[existingIndex] = {
            ...existingSteps[existingIndex],
            content: existingSteps[existingIndex].content + content,
            timestamp: new Date(),
            tool_calls: toolCalls,
          }
        } else {
          existingSteps.push({
            step_number: stepNum,
            step_type: 'observation',
            content: content,
            timestamp: new Date(),
            tool_calls: toolCalls,
            tool_results: [],
          })
        }

        subAgentSteps.value.set(todoId, [...existingSteps])
        console.log('[ReviewExecutionView] sub_agent_llm_output for', todoId, 'step', stepNum, 'tool_calls:', toolCalls.length)
      }
      break

    case 'sub_agent_tool_call_start':
      // 子代理工具调用开始
      if (event.todo_id && event.step !== undefined) {
        const todoId = event.todo_id
        const stepNum = event.step
        console.log('[ReviewExecutionView] sub_agent_tool_call_start for', todoId, 'step', stepNum, 'tool:', event.tool)
      }
      break

    case 'sub_agent_tool_call_end':
      // 子代理工具调用结束 - 添加 tool_result
      if (event.todo_id && event.step !== undefined) {
        const todoId = event.todo_id
        const stepNum = event.step
        const tool = event.tool
        const success = event.success
        const result = event.result
        const error = event.error

        // 添加 tool_result 到 step
        const existingSteps = subAgentSteps.value.get(todoId) || []
        const existingIndex = existingSteps.findIndex(s => s.step_number === stepNum)

        if (existingIndex >= 0) {
          const step = existingSteps[existingIndex]
          const toolResults = [...(step.tool_results || [])]
          toolResults.push({
            name: tool,
            result: {
              status: success ? 'success' : 'error',
              content: result || null,
              error: error || null,
              count: null,
            }
          })
          existingSteps[existingIndex] = {
            ...step,
            tool_results: toolResults,
            timestamp: new Date(),
          }
          subAgentSteps.value.set(todoId, [...existingSteps])
        }
        console.log('[ReviewExecutionView] sub_agent_tool_call_end for', todoId, 'step', stepNum, 'tool:', tool, 'success:', success)
      }
      break

    case 'sub_agent_step_complete':
      // 子代理 step 完成 - 清理 pending data
      if (event.todo_id && event.step !== undefined) {
        const todoId = event.todo_id
        const stepNum = event.step
        // 清理 pending data
        if (pendingToolCallsMap.value.has(todoId)) {
          const todoPending = pendingToolCallsMap.value.get(todoId)!
          todoPending.delete(stepNum)
        }
        console.log('[ReviewExecutionView] sub_agent_step_complete for', todoId, 'step', stepNum)
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
      // 合并历史结果
      isMerging.value = true
      mergeProgress.value = event.message || '正在合并历史结果...'
      console.log('[ReviewExecutionView] Merging historical results...')
      break

    case 'merged':
      // 合并完成
      isMerging.value = false
      mergeProgress.value = ''
      // 获取合并后的统计数据
      console.log('[ReviewExecutionView] Merge complete', event)
      // findingsCount 显示不合规问题数（从 summary.non_compliant 获取）
      findingsCount.value = event.non_compliant_count ?? event.total_count ?? event.merged_count ?? findingsCount.value
      // 获取合并后的详细结果
      if (projectStore.currentTask?.id) {
        try {
          const response = await reviewApi.getResults(projectId.value)
          if (response.summary) {
            // total 应该是 non_compliant，不是 total_requirements
            mergedStats.value = {
              total: response.summary.non_compliant || 0,
              critical: response.summary.critical || 0,
              major: response.summary.major || 0,
              minor: response.summary.minor || 0,
              compliant: response.summary.compliant || 0
            }
          }
        } catch (err) {
          console.error('[ReviewExecutionView] Failed to fetch merged results:', err)
        }
      }
      break

    default:
      // 记录所有未处理的事件类型
      console.log('[ReviewExecutionView] Unhandled event type:', event.type, 'event:', event)
  }
}

// Helper methods for todo management
function addTodoItem(event: any) {
  // Create a single default check item for rule-based review
  const todoItem: TodoItemState = {
    id: event.todo_id || '',
    rule_doc_name: event.rule_doc_name || '',
    check_items: [{
      id: `${event.todo_id}-default`,
      title: '规则审查',
      status: 'pending'
    }],
    status: 'pending'
  }
  todos.value.set(todoItem.id, todoItem)
}

function updateTodoStatus(todoId: string, status: TodoItemState['status'], findings?: any[], error?: string) {
  const todo = todos.value.get(todoId)
  if (todo) {
    todo.status = status
    if (status === 'completed' && findings) {
      todo.result = { findings }
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

// Load historical todos from API for completed tasks
async function loadHistoricalTodos(projectId: string, taskId: string) {
  try {
    const historicalTodos = await reviewApi.getTodosByTask(projectId, taskId)
    console.log('[ReviewExecutionView] Loaded historical todos:', historicalTodos.length)

    // Clear existing todos and populate from API response
    todos.value.clear()

    for (const item of historicalTodos) {
      const checkItems: CheckItemState[] = (item.check_items || []).map((ci: any, idx: number) => ({
        id: `${item.id}-${idx}`,
        title: ci.title || `检查项 ${idx + 1}`,
        status: item.status === 'completed' ? 'completed' : item.status === 'running' ? 'running' : item.status === 'failed' ? 'failed' : 'pending'
      }))

      const todoItem: TodoItemState = {
        id: item.id,
        rule_doc_name: item.rule_doc_name || '',
        check_items: checkItems,
        status: item.status as TodoItemState['status'],
        result: item.result || undefined,
        error_message: item.error_message || undefined
      }

      todos.value.set(item.id, todoItem)
    }

    console.log('[ReviewExecutionView] Populated todos from API, count:', todos.value.size)
  } catch (err) {
    console.error('[ReviewExecutionView] Failed to load historical todos:', err)
  }
}

function connect() {
  if (!projectStore.currentTask?.id) {
    console.log('[ReviewExecutionView] No currentTask.id, skipping SSE connection')
    return
  }

  disconnect()
  const token = getAccessToken()
  const url = token
    ? `${API_BASE}/events/tasks/${projectStore.currentTask.id}/stream?token=${encodeURIComponent(token)}`
    : `${API_BASE}/events/tasks/${projectStore.currentTask.id}/stream`
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

// 格式化 subAgentSteps 用于 LeftPane
const subAgentStepsMap = computed(() => {
  const map: Record<string, any[]> = {}
  subAgentSteps.value.forEach((stepsArr, todoId) => {
    map[todoId] = stepsArr.map(s => ({
      step_number: s.step_number,
      step_type: s.step_type,
      content: s.content,
      timestamp: s.timestamp,
      tool_args: s.tool_calls ? { tool_calls: s.tool_calls } : undefined,
      // 转换 tool_results 格式：后端已发送嵌套结构 {name, result: {status, content, error}}
      // 只需要取出来放到 tool_results 数组中
      tool_result: s.tool_results ? {
        tool_results: s.tool_results.map((r: any) => ({
          name: r.name,
          result: {
            status: r.result?.status,
            content: r.result?.content,
            error: r.result?.error,
            count: r.result?.count,
          }
        }))
      } : undefined,
    }))
  })
  return map
})

// 调试：监控 phase 变化
watch(phase, (newPhase) => {
  console.log('[ReviewExecutionView] phase changed to:', newPhase)
})

// 调试：监控 timelineSteps 变化
watch(timelineSteps, (newSteps) => {
  console.log('[ReviewExecutionView] timelineSteps changed, count:', newSteps.length, 'types:', [...new Set(newSteps.map(s => s.step_type))])
}, { deep: true })

onMounted(async () => {
  console.log('[ReviewExecutionView] onMounted, projectId:', projectId.value)

  // 加载项目信息
  await projectStore.selectProject(projectId.value)

  // 获取当前项目的审查任务
  await projectStore.fetchReviewTasks()

  // 检查是否有指定的 taskId
  if (route.query.taskId) {
    // 有指定 taskId，使用指定任务
    console.log('[ReviewExecutionView] Using specified taskId:', route.query.taskId)
    await projectStore.selectReviewTask(route.query.taskId as string)
    const task = projectStore.currentTask
    if (task) {
      if (task.status === 'completed') {
        phase.value = 'completed'
      } else if (task.status === 'failed') {
        phase.value = 'failed'
        errorMessage.value = task.error_message || '审查失败'
      } else if (task.status === 'running') {
        phase.value = 'running'
      }
    }
  } else {
    // 无指定，使用最新任务（现有逻辑）
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
      } else if (latestTask.status === 'running') {
        phase.value = 'running'
      }
    }
  }

  // 建立 SSE 连接以接收实时更新
  if (projectStore.currentTask?.id) {
    connect()

    // 如果任务已完成，主动拉取合并结果（处理 SSE 事件丢失的情况）
    if (projectStore.currentTask.status === 'completed') {
      try {
        const response = await reviewApi.getResults(projectId.value)
        if (response.summary) {
          mergedStats.value = {
            total: response.summary.non_compliant || 0,
            critical: response.summary.critical || 0,
            major: response.summary.major || 0,
            minor: response.summary.minor || 0,
            compliant: response.summary.compliant || 0
          }
          findingsCount.value = response.summary.non_compliant || 0
        }
      } catch (err) {
        console.error('[ReviewExecutionView] Failed to fetch merged results on mount:', err)
      }

      // 加载历史 todo 项（子代理信息）以处理 SSE 事件丢失的情况
      await loadHistoricalTodos(projectId.value, projectStore.currentTask.id)

      // 加载历史 master agent 步骤（时间线数据）
      try {
        const historicalSteps = await reviewApi.getSteps(projectId.value, projectStore.currentTask.id)
        if (historicalSteps.length > 0) {
          steps.value = historicalSteps.map(s => ({
            step_number: s.step_number,
            step_type: s.step_type,
            content: s.content || '',
            timestamp: new Date(),
            tool_calls: (s.tool_args as any)?.tool_calls || [],
            tool_results: (s.tool_result as any)?.tool_results || [],
          }))
          console.log('[ReviewExecutionView] Loaded historical steps:', steps.value.length)
        }
      } catch (err) {
        console.error('[ReviewExecutionView] Failed to load historical steps:', err)
      }
    }
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
          :todos="todoList"
          :sub-agent-steps-map="subAgentStepsMap"
          :merged-stats="mergedStats"
          :is-merging="isMerging"
          :merge-progress="mergeProgress"
        />

        <RightSidebar
          :total-steps="totalSteps"
          :completed-count="completedCount"
          :findings-count="findingsCount"
          :phase="phase"
          :sub-agent-steps-map="subAgentStepsMap"
          :max-steps-map="maxStepsMap"
          :task-start-time="taskStartTime"
          :todos="todoList"
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