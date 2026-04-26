<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useProjectStore } from '@/stores/project'
import { reviewApi } from '@/api/client'
import ReviewTimeline from '@/components/ReviewTimeline.vue'
import { ElMessage } from 'element-plus'

const props = defineProps<{
  projectId: string
}>()

const emit = defineEmits<{
  (e: 'task-complete', taskId: string): void
}>()

const projectStore = useProjectStore()
const router = useRouter()

const timelineRef = ref<InstanceType<typeof ReviewTimeline> | null>(null)
const historicalSteps = ref<any[]>([])
const isHistoricalMode = ref(false)
const selectedTaskId = ref<string>('')

const completedTasks = computed(() =>
  projectStore.reviewTasks.filter(t => t.status === 'completed')
)

const tenderDoc = computed(() => projectStore.documents.find(d => d.doc_type === 'tender'))
const bidDoc = computed(() => projectStore.documents.find(d => d.doc_type === 'bid'))

const canStartReview = computed(() => {
  return tenderDoc.value?.status === 'parsed' && bidDoc.value?.status === 'parsed'
})

onMounted(async () => {
  await projectStore.fetchReviewTasks()

  // Default select latest completed task if available
  if (completedTasks.value.length > 0) {
    const latestTask = completedTasks.value[0]
    selectedTaskId.value = latestTask.id
    await loadHistoricalSteps()
  }
})

watch(selectedTaskId, async (newTaskId) => {
  if (newTaskId) {
    await loadHistoricalSteps()
  }
})

async function loadHistoricalSteps() {
  if (!selectedTaskId.value || !props.projectId) return

  try {
    const steps = await reviewApi.getSteps(props.projectId, selectedTaskId.value)
    historicalSteps.value = steps.map(s => ({
      step_number: s.step_number,
      step_type: s.step_type,
      content: s.content,
      timestamp: s.created_at ? new Date(s.created_at) : new Date(),
      tool_args: s.tool_args,
      tool_result: s.tool_result,
    }))
    isHistoricalMode.value = true
  } catch (error: any) {
    // 404 means the task doesn't exist yet (e.g., newly created task)
    // Silently ignore and clear the steps
    if (error?.response?.status === 404) {
      console.log('[loadHistoricalSteps] Task steps not found (may be new task), clearing steps')
      historicalSteps.value = []
      return
    }
    console.error('Failed to load historical steps:', error)
    ElMessage.error('加载历史时间线失败')
  }
}

async function startReview() {
  try {
    // Clear historical mode
    isHistoricalMode.value = false
    selectedTaskId.value = ''
    historicalSteps.value = []

    await projectStore.startReview()
    ElMessage.success('审查已启动，正在跳转...')

    // Navigate to review execution page
    if (projectStore.currentProject?.id) {
      router.push({
        name: 'review-execution',
        params: { id: projectStore.currentProject.id }
      })
    }
  } catch (error) {
    console.error('Failed to start review:', error)
    ElMessage.error('启动审查失败')
  }
}

function handleTaskComplete() {
  emit('task-complete', projectStore.currentTask?.id || '')
}

function formatDate(dateStr: string | null): string {
  if (!dateStr) return 'N/A'
  return new Date(dateStr).toLocaleString('zh-CN')
}

function getStatusClass(status: string) {
  switch (status) {
    case 'parsed':
    case 'completed':
      return 'status-success'
    case 'parsing':
    case 'running':
      return 'status-running'
    case 'failed':
      return 'status-error'
    default:
      return 'status-pending'
  }
}
</script>

<template>
  <div class="timeline-area">
    <!-- Task Selector -->
    <div v-if="completedTasks.length > 0" class="task-selector">
      <label>选择任务查看时间线:</label>
      <select v-model="selectedTaskId">
        <option v-for="task in completedTasks" :key="task.id" :value="task.id">
          {{ formatDate(task.completed_at) }} - {{ task.status }}
        </option>
      </select>
    </div>

    <!-- Review Controls -->
    <div class="review-controls">
      <button
        class="primary-btn"
        :disabled="!canStartReview || projectStore.reviewLoading"
        @click="startReview"
      >
        {{ projectStore.reviewLoading ? '启动中...' : '开始审查' }}
      </button>

      <div v-if="projectStore.currentTask" class="task-status">
        <span :class="['status', getStatusClass(projectStore.currentTask.status)]">
          {{ projectStore.currentTask.status }}
        </span>
        <p v-if="projectStore.currentTask.error_message" class="error-msg">
          {{ projectStore.currentTask.error_message }}
        </p>
      </div>
    </div>

    <!-- Timeline -->
    <ReviewTimeline
      v-if="selectedTaskId || projectStore.currentTask"
      ref="timelineRef"
      :task-id="selectedTaskId || projectStore.currentTask?.id || ''"
      :initial-steps="historicalSteps"
      :historical-mode="isHistoricalMode"
      @complete="handleTaskComplete"
    />
  </div>
</template>

<style scoped>
.timeline-area {
  margin-top: 1.5rem;
  padding-top: 1rem;
  border-top: 1px solid var(--line);
}

.task-selector {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin-bottom: 1rem;
  padding: 0.75rem;
  background: var(--bg2);
  border-radius: 8px;
}

.task-selector label {
  font-weight: 500;
  color: var(--text);
}

.task-selector select {
  padding: 0.5rem;
  border: 1px solid var(--line);
  border-radius: 4px;
  min-width: 200px;
}

.review-controls {
  display: flex;
  align-items: center;
  gap: 1rem;
  margin-bottom: 1rem;
}

.primary-btn {
  padding: 0.75rem 1.5rem;
  background: var(--blue);
  color: var(--white);
  border: none;
  border-radius: 6px;
  cursor: pointer;
  font-weight: 500;
  transition: background-color 0.2s ease, transform 0.1s ease;
}

.primary-btn:hover {
  background: var(--blue-dim);
}

.primary-btn:active {
  transform: scale(0.98);
}

.primary-btn:disabled {
  background: var(--dim);
  cursor: not-allowed;
}

.task-status {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.status {
  display: inline-flex;
  align-items: center;
  padding: 0.2rem 0.6rem;
  border-radius: 9999px;
  font-size: 0.75rem;
  font-weight: 600;
}

.status-pending {
  background: var(--bg4);
  color: var(--muted);
}

.status-running {
  background: var(--amber-bg);
  color: var(--amber);
}

.status-success {
  background: var(--green-bg);
  color: var(--green);
}

.status-error {
  background: var(--red-bg);
  color: var(--red);
}

.error-msg {
  color: var(--red);
  font-size: 0.85rem;
  margin: 0;
}
</style>
