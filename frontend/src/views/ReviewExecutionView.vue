<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import { useProjectStore } from '@/stores/project'
import ExecutionHeader from '@/components/execution/ExecutionHeader.vue'
import ExecutionStepper from '@/components/execution/ExecutionStepper.vue'
import LeftPane from '@/components/execution/LeftPane.vue'
import RightSidebar from '@/components/execution/RightSidebar.vue'
import type { TodoItem } from '@/types/review'

const API_BASE = import.meta.env.VITE_API_BASE || '/api'
const route = useRoute()
const projectStore = useProjectStore()

const projectId = computed(() => route.params.id as string)

// SSE 连接
let eventSource: EventSource | null = null

// 状态
const phase = ref<'master' | 'todo' | 'sub_agents' | 'merging' | 'completed'>('master')
const todos = ref<Map<string, TodoItem>>(new Map())
const masterOutput = ref<{
  totalDocs: number
  totalItems: number
  ruleDocs: Array<{ name: string; items: number }>
} | null>(null)

// 统计数据
const totalDocs = computed(() => masterOutput.value?.totalDocs || 0)
const totalItems = computed(() => masterOutput.value?.totalItems || 0)
const completedCount = computed(() => Array.from(todos.value.values()).filter(t => t.status === 'completed').length)
const runningCount = computed(() => Array.from(todos.value.values()).filter(t => t.status === 'running').length)
const pendingCount = computed(() => Array.from(todos.value.values()).filter(t => t.status === 'pending').length)

// 发现统计
const allFindings = computed(() => {
  const findings: any[] = []
  for (const todo of todos.value.values()) {
    if (todo.result?.findings) {
      findings.push(...todo.result.findings)
    }
  }
  return findings
})

const criticalCount = computed(() => allFindings.value.filter(f => f.severity === 'critical').length)
const majorCount = computed(() => allFindings.value.filter(f => f.severity === 'major').length)
const passedCount = computed(() => allFindings.value.filter(f => f.is_compliant).length)
const uncheckedCount = computed(() => totalItems.value - allFindings.value.length - (pendingCount.value * 5))

const progress = computed(() => {
  if (totalItems.value === 0) return 0
  return Math.round((completedCount.value / totalItems.value) * 100)
})

const currentStatus = computed(() => {
  if (projectStore.currentTask?.status) return projectStore.currentTask.status
  if (phase.value === 'completed') return 'completed'
  if (phase.value === 'master') return 'running'
  return phase.value as any
})

// SSE 事件处理
function handleSSEEvent(event: any) {
  switch (event.type) {
    case 'master_started':
      phase.value = 'master'
      break
    case 'master_scan_completed':
      masterOutput.value = {
        totalDocs: event.total_docs,
        totalItems: event.total_items,
        ruleDocs: event.rule_docs || []
      }
      break
    case 'todo_created':
      const todoItem: TodoItem = {
        id: event.todo_id || '',
        rule_doc_name: event.rule_doc_name || '',
        check_items: (event.check_items || []).map((item: any) => ({
          id: item.id || '',
          title: item.title || '',
          status: 'pending'
        })),
        status: 'pending'
      }
      todos.value.set(todoItem.id, todoItem)
      break
    case 'todo_list_completed':
      phase.value = 'sub_agents'
      break
    case 'sub_agent_started':
      const todo = todos.value.get(event.todo_id)
      if (todo) {
        todo.status = 'running'
        todos.value.set(todo.id, { ...todo })
      }
      break
    case 'sub_agent_completed':
      const completedTodo = todos.value.get(event.todo_id)
      if (completedTodo) {
        completedTodo.status = 'completed'
        completedTodo.result = { findings: event.findings || [] }
        todos.value.set(completedTodo.id, { ...completedTodo })
      }
      break
    case 'sub_agent_failed':
      const failedTodo = todos.value.get(event.todo_id)
      if (failedTodo) {
        failedTodo.status = 'failed'
        failedTodo.error_message = event.error
        todos.value.set(failedTodo.id, { ...failedTodo })
      }
      break
    case 'merging_started':
      phase.value = 'merging'
      break
    case 'merging_completed':
      phase.value = 'completed'
      break
  }
}

function connect() {
  if (!projectStore.currentTask?.id) return

  disconnect()
  eventSource = new EventSource(`${API_BASE}/events/tasks/${projectStore.currentTask.id}/stream`)

  eventSource.onmessage = (e) => {
    try {
      const data = JSON.parse(e.data)
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

onMounted(async () => {
  await projectStore.selectProject(projectId.value)
  await projectStore.fetchReviewTasks()

  if (projectStore.currentTask?.id) {
    connect()
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
          :todos="Array.from(todos.values())"
          :master-output="masterOutput"
        />

        <RightSidebar
          :total-docs="totalDocs"
          :total-items="totalItems"
          :completed-count="completedCount"
          :running-count="runningCount"
          :pending-count="pendingCount"
          :critical-count="criticalCount"
          :major-count="majorCount"
          :passed-count="passedCount"
          :unchecked-count="uncheckedCount"
          :progress="progress"
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