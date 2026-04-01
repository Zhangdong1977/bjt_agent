<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
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
      timestamp: new Date(s.created_at || Date.now()),
      tool_args: s.tool_args,
      tool_result: s.tool_result,
    }))
    isHistoricalMode.value = true
  } catch (error) {
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
    ElMessage.info('审查已启动，正在连接事件流...')

    // Connect to SSE via ReviewTimeline component
    if (projectStore.currentTask?.id) {
      timelineRef.value?.connect(projectStore.currentTask.id)
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
  border-top: 1px solid #eee;
}

.task-selector {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin-bottom: 1rem;
  padding: 0.75rem;
  background: #f5f5f5;
  border-radius: 8px;
}

.task-selector label {
  font-weight: 500;
  color: #333;
}

.task-selector select {
  padding: 0.5rem;
  border: 1px solid #ddd;
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
  background: #6366f1;
  color: white;
  border: none;
  border-radius: 6px;
  cursor: pointer;
  font-weight: 500;
  transition: background-color 0.2s ease, transform 0.1s ease;
}

.primary-btn:hover {
  background: #4f46e5;
}

.primary-btn:active {
  transform: scale(0.98);
}

.primary-btn:disabled {
  background: #d1d5db;
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
  background: #f3f4f6;
  color: #6b7280;
}

.status-running {
  background: #fef9c3;
  color: #854d0e;
}

.status-success {
  background: #dcfce7;
  color: #166534;
}

.status-error {
  background: #fee2e2;
  color: #991b1b;
}

.error-msg {
  color: #dc2626;
  font-size: 0.85rem;
  margin: 0;
}
</style>
