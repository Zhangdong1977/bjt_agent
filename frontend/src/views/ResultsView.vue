<script setup lang="ts">
import { onMounted, computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Modal } from 'ant-design-vue'
import { useProjectStore } from '@/stores/project'
import { reviewApi } from '@/api/client'
import ReviewResultsArea from '@/components/ReviewResultsArea.vue'
import type { ReviewResponse } from '@/types'
// 按钮图（自带胶囊/图标，文字叠加在右侧）：查看时间线 / 重新审查
import btnTimeline from '@/assets/images/ui/result-btn-timeline.png'
import btnRecheck from '@/assets/images/ui/result-btn-recheck.png'

const route = useRoute()
const router = useRouter()
const projectStore = useProjectStore()

const projectId = computed(() => route.params.id as string)
const fromExperience = computed(() => route.query.from === 'experience')
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
  if (!dateStr) return '暂无'
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
  router.push({ name: fromExperience.value ? 'experience-dashboard' : 'history' })
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
      <!-- 任务选择器 -->
      <div v-if="projectStore.reviewTasks.length > 0" class="task-bar">
        <label class="task-label">审查记录:</label>
        <select v-model="selectedTaskId" class="task-select">
          <option v-for="task in projectStore.reviewTasks" :key="task.id" :value="task.id">
            {{ getStatusLabel(task.status) }} - {{ formatDate(task.created_at) }}
          </option>
        </select>
        <button
          class="bg-btn"
          :style="{ backgroundImage: `url(${btnTimeline})` }"
          :disabled="!selectedTaskId"
          @click="goToTaskExecution"
        >
          <span class="bg-btn-text text-white">查看时间线</span>
        </button>
        <button
          v-if="!fromExperience"
          class="bg-btn"
          :style="{ backgroundImage: `url(${btnRecheck})` }"
          @click="startNewReview"
        >
          <span class="bg-btn-text text-red">重新审查</span>
        </button>
      </div>

      <section v-if="taskResults" class="section">
        <h2 class="section-title">审查结果</h2>
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
  padding: 0;
}

.task-bar {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.75rem 1rem;
  background: var(--bg1);
  border: 1px solid var(--line);
  border-radius: var(--r-lg);
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

/* 图片按钮：背景 PNG（自带胶囊+左侧图标），文字绝对定位落在胶囊右侧内部 */
.bg-btn {
  position: relative;
  /* 放大按钮以容纳「查看时间线」5 字：等比缩放背景图（148×60）到高度 52px → 宽 128px */
  height: 52px;
  width: 128px;
  padding: 0;
  border: none;
  background-color: transparent;
  background-size: 100% 100%;
  background-position: center;
  background-repeat: no-repeat;
  cursor: pointer;
  transition: filter 0.2s ease, transform 0.1s ease;
}

.bg-btn:hover:not(:disabled) {
  filter: brightness(1.06);
}

.bg-btn:active:not(:disabled) {
  transform: scale(0.98);
}

.bg-btn:disabled {
  cursor: not-allowed;
  opacity: 0.5;
  filter: grayscale(0.4);
}

/* 文字层：按 PNG 像素测量精确定位。
   背景 PNG 148×60，胶囊实测 bbox x[9,138] y[5,46]：
   - 胶囊垂直中心在 y=25.5/60≈42.5%，故 top 用 42.5%（不是 50%）让文字落在胶囊中线；
   - 时间线图左侧图标结束于 x=42/148≈28%，文本区 x[45,138] 几何中心在
     (45+138)/2/148≈61.8%；改用 left:30%/right:7% 得中心 61.5%，与图像吻合且整体左移。 */
.bg-btn-text {
  position: absolute;
  top: 42.5%;
  left: 30%;
  right: 7%;
  transform: translateY(-50%);
  text-align: center;
  font-family: inherit;
  font-size: 12px;
  font-weight: 600;
  line-height: 1;
  letter-spacing: 1px;
  white-space: nowrap;
}

.text-white { color: #fff; }
.text-red { color: #D7041A; }

.section {
  background: var(--bg1);
  padding: 1.5rem;
  border-radius: var(--r-lg);
  border: 1px solid var(--line);
}

.section-title {
  position: relative;
  color: #222;
  font-size: 20px;
  font-weight: 700;
  margin-bottom: 1.25rem;
  padding-bottom: 12px;
  letter-spacing: 0.5px;
}

/* 标题下方朱红强调短下划线条 */
.section-title::after {
  content: '';
  position: absolute;
  left: 0;
  bottom: 0;
  width: 40px;
  height: 3px;
  border-radius: 2px;
  background: #D7041A;
}

.no-results {
  text-align: center;
  padding: 3rem;
  color: var(--sub);
  background: var(--bg1);
  border-radius: var(--r-lg);
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
