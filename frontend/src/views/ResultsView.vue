<script setup lang="ts">
import { onMounted, computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Modal } from 'ant-design-vue'
import { useProjectStore } from '@/stores/project'
import { reviewApi } from '@/api/client'
import ReviewResultsArea from '@/components/ReviewResultsArea.vue'
import type { ReviewResponse } from '@/types'

const route = useRoute()
const router = useRouter()
const projectStore = useProjectStore()

const projectId = computed(() => route.params.id as string)
const selectedTaskId = ref<string>('')
const todos = ref<any[]>([])
const taskResults = ref<ReviewResponse | null>(null)

onMounted(async () => {
  await projectStore.selectProject(projectId.value)
  await projectStore.fetchReviewTasks()

  // 默认选中最新任务并加载数据
  if (projectStore.reviewTasks.length > 0) {
    const latestTask = projectStore.reviewTasks[0]
    selectedTaskId.value = latestTask.id
  }
})

// 当 selectedTaskId 变化时加载结果和 todos
watch(selectedTaskId, async (newTaskId) => {
  if (!newTaskId) return
  try {
    const [findings, taskTodos] = await Promise.all([
      reviewApi.getResultsByTask(projectId.value, newTaskId).catch(() => []),
      reviewApi.getTodosByTask(projectId.value, newTaskId).catch(() => []),
    ])
    todos.value = taskTodos
    const nonCompliant = findings.filter((f: any) => !f.is_compliant)
    taskResults.value = {
      summary: {
        category_count: taskTodos.length,
        check_item_count: taskTodos.reduce((sum: number, t: any) => sum + (t.check_items?.length || 0), 0),
        risk_item_count: new Set(nonCompliant.map((f: any) => f.check_item_name).filter(Boolean)).size,
      },
      findings,
    }
  } catch (err) {
    console.error('加载审查结果失败:', err)
    taskResults.value = null
    todos.value = []
  }
}, { immediate: true })

function getStatusLabel(status: string): string {
  const labels: Record<string, string> = {
    pending: '待处理',
    running: '进行中',
    completed: '已完成',
    failed: '失败'
  }
  return labels[status] || status
}

function formatDate(dateStr: string | null): string {
  if (!dateStr) return 'N/A'
  return new Date(dateStr).toLocaleString('zh-CN')
}

function goToTaskExecution() {
  if (!selectedTaskId.value) return
  router.push({
    name: 'review-execution',
    params: { id: projectId.value },
    query: { taskId: selectedTaskId.value }
  })
}

function goBack() {
  router.push({ name: 'history' })
}

async function startNewReview() {
  if (!projectId.value) return
  Modal.confirm({
    title: '确认重新审查',
    content: '重新审查将发起新的审查任务，当前审查结果不会被删除。确定要继续吗？',
    okText: '确定',
    cancelText: '取消',
    onOk: async () => {
      try {
        await reviewApi.start(projectId.value)
        router.push({
          name: 'review-execution',
          params: { id: projectId.value }
        })
      } catch (error) {
        console.error('启动审查失败:', error)
      }
    }
  })
}
</script>

<template>
  <div class="results-view">
    <main class="content">
      <a-breadcrumb class="breadcrumb">
        <a-breadcrumb-item><a @click="goBack">历史标书</a></a-breadcrumb-item>
        <a-breadcrumb-item>审查结果</a-breadcrumb-item>
      </a-breadcrumb>

      <!-- 任务选择器 -->
      <div v-if="projectStore.reviewTasks.length > 0" class="task-bar">
        <label class="task-label">审查记录:</label>
        <select v-model="selectedTaskId" class="task-select">
          <option v-for="task in projectStore.reviewTasks" :key="task.id" :value="task.id">
            {{ getStatusLabel(task.status) }} - {{ formatDate(task.created_at) }}
          </option>
        </select>
        <button class="view-task-btn" :disabled="!selectedTaskId" @click="goToTaskExecution">
          查看时间线
        </button>
        <button class="view-task-btn restart-btn" @click="startNewReview">
          重新审查
        </button>
      </div>

      <section v-if="taskResults" class="section">
        <h2>审查结果</h2>
        <ReviewResultsArea
          :review-results="taskResults"
          :todos="todos"
          :project-id="projectId"
          :task-id="selectedTaskId"
        />
      </section>

      <div v-else class="no-results">
        <p>暂无审查结果。</p>
        <a-button type="primary" @click="goBack">返回历史列表</a-button>
      </div>
    </main>
  </div>
</template>

<style scoped>
.content {
  max-width: 1200px;
  margin: 20px auto;
  padding: 0 20px;
}

.breadcrumb {
  margin-bottom: 20px;
}

.task-bar {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.75rem 1rem;
  background: var(--bg1);
  border: 1px solid var(--line);
  border-radius: var(--r2);
  margin-bottom: 1rem;
}

.task-label {
  font-weight: 500;
  color: var(--text);
  white-space: nowrap;
}

.task-select {
  padding: 0.5rem;
  border: 1px solid var(--line);
  border-radius: var(--r);
  min-width: 200px;
  background: var(--bg2);
  color: var(--text);
  font-size: 0.9rem;
}

.view-task-btn {
  padding: 0.5rem 1rem;
  background: var(--blue);
  color: var(--white);
  border: none;
  border-radius: var(--r);
  cursor: pointer;
  font-size: 0.85rem;
  font-weight: 500;
  transition: filter 0.2s;
}

.view-task-btn:hover:not(:disabled) {
  filter: brightness(1.1);
}

.view-task-btn:disabled {
  background: var(--muted);
  cursor: not-allowed;
  opacity: 0.6;
}

.restart-btn {
  background: var(--green);
}

.section {
  background: var(--bg1);
  padding: 1.5rem;
  border-radius: var(--r2);
  border: 1px solid var(--line);
}

.section h2 {
  color: var(--text);
  margin-bottom: 1rem;
  padding-bottom: 0.5rem;
  border-bottom: 2px solid var(--blue);
}

.no-results {
  text-align: center;
  padding: 3rem;
  color: var(--sub);
  background: var(--bg1);
  border-radius: var(--r2);
}

.no-results p {
  margin: 0 0 1rem;
}

@media (max-width: 767px) {
  .content {
    padding: 0 1rem;
  }

  .section {
    padding: 1rem;
  }

  .task-bar {
    flex-wrap: wrap;
  }
}
</style>
