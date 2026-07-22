<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { duplicateCheckApi } from '@/api/client'
import type { ReviewTask, TodoItem } from '@/types'

const route = useRoute()
const router = useRouter()
const projectId = computed(() => route.params.id as string)
const task = ref<ReviewTask | null>(null)
const pairs = ref<TodoItem[]>([])
const loading = ref(true)
let pollTimer: number | null = null
let heartbeatTimer: number | null = null

const terminal = computed(() => task.value && ['completed', 'completed_with_warnings', 'failed', 'cancelled'].includes(task.value.status))
const completedCount = computed(() => pairs.value.filter(p => p.status === 'completed').length)
const failedCount = computed(() => pairs.value.filter(p => p.status === 'failed').length)
const progress = computed(() => pairs.value.length ? Math.round((completedCount.value + failedCount.value) / pairs.value.length * 100) : 0)

async function refresh() {
  if (!task.value) return
  try {
    const [updated, updatedPairs] = await Promise.all([
      duplicateCheckApi.getTask(projectId.value, task.value.id),
      duplicateCheckApi.getPairs(projectId.value, task.value.id),
    ])
    task.value = updated
    pairs.value = updatedPairs
    if (terminal.value) stopRealtime()
  } catch (error) {
    console.warn('刷新查重任务失败', error)
  } finally {
    loading.value = false
  }
}

function stopRealtime() {
  if (pollTimer) window.clearInterval(pollTimer)
  if (heartbeatTimer) window.clearInterval(heartbeatTimer)
  pollTimer = heartbeatTimer = null
}

async function cancelTask() {
  if (!task.value) return
  task.value = await duplicateCheckApi.cancel(projectId.value, task.value.id)
  message.info('已请求取消查重任务')
  await refresh()
}

function viewResults() {
  if (!task.value) return
  router.push({ name: 'duplicate-results', params: { id: projectId.value }, query: { taskId: task.value.id } })
}

function statusLabel(status: string) {
  return {
    pending: '等待调度', running: '执行中', completed: '已完成',
    completed_with_warnings: '部分完成', failed: '失败', cancelled: '已取消',
  }[status] || status
}

onMounted(async () => {
  const taskId = route.query.taskId as string | undefined
  if (taskId) task.value = await duplicateCheckApi.getTask(projectId.value, taskId)
  else task.value = (await duplicateCheckApi.getTasks(projectId.value))[0] || null
  if (!task.value) {
    loading.value = false
    return
  }
  await refresh()
  if (!terminal.value) {
    pollTimer = window.setInterval(refresh, 2500)
    heartbeatTimer = window.setInterval(() => task.value && duplicateCheckApi.heartbeat(projectId.value, task.value.id).catch(() => {}), 10000)
  }
})

onUnmounted(stopRealtime)
</script>

<template>
  <div class="execution-page">
    <a-spin :spinning="loading">
      <a-card v-if="task" :bordered="false" class="overview">
        <div class="overview-head">
          <div>
            <h2>标书查重执行中</h2>
            <p>主代理已将标书生成文档对，每个子代理独立完成一对查重。</p>
          </div>
          <a-tag :color="terminal ? (task.status.includes('completed') ? 'green' : 'red') : 'processing'">{{ statusLabel(task.status) }}</a-tag>
        </div>
        <a-progress :percent="progress" status="active" />
        <div class="metrics">
          <span>文档对 {{ pairs.length }}</span><span>已完成 {{ completedCount }}</span><span>失败 {{ failedCount }}</span>
        </div>
        <a-alert v-if="task.status === 'completed_with_warnings'" type="warning" show-icon :message="task.error_message || '部分文档对执行失败'" />
        <a-alert v-else-if="task.status === 'failed'" type="error" show-icon :message="task.error_message || '查重任务失败'" />
      </a-card>

      <div v-if="pairs.length" class="pair-grid">
        <a-card v-for="(pair, index) in pairs" :key="pair.id" class="pair-card" :bordered="false">
          <div class="pair-head">
            <span class="agent-index">A{{ index + 1 }}</span>
            <strong>{{ pair.display_name }}</strong>
            <a-tag :color="pair.status === 'completed' ? 'green' : pair.status === 'failed' ? 'red' : pair.status === 'running' ? 'processing' : 'default'">
              {{ statusLabel(pair.status) }}
            </a-tag>
          </div>
          <p>模式：{{ pair.execution_mode === 'structured' ? '章节结构分析' : '片段召回核验' }}</p>
          <a-alert v-if="pair.error_message" type="error" :message="pair.error_message" />
          <div v-if="pair.result" class="pair-result">
            {{ (pair.result as any).summary }}
          </div>
        </a-card>
      </div>
      <a-empty v-else-if="!loading" description="主代理正在生成文档对" />

      <div v-if="task" class="actions">
        <a-button v-if="!terminal" danger @click="cancelTask">取消查重</a-button>
        <a-button v-else-if="task.status === 'completed' || task.status === 'completed_with_warnings'" type="primary" @click="viewResults">查看查重结果</a-button>
      </div>
    </a-spin>
  </div>
</template>

<style scoped>
.execution-page { max-width: 1180px; margin: 0 auto; }
.overview { border-radius: 12px; box-shadow: var(--shadow-md); margin-bottom: 20px; }
.overview-head, .pair-head { display: flex; align-items: center; gap: 12px; }
.overview-head { justify-content: space-between; margin-bottom: 18px; }
.overview h2 { margin: 0 0 6px; }
.overview p, .pair-card p { color: #777; margin: 0; }
.metrics { display: flex; gap: 30px; margin: 12px 0; color: #666; }
.pair-grid { display: grid; gap: 14px; grid-template-columns: repeat(auto-fit, minmax(360px, 1fr)); }
.pair-card { border-radius: 10px; box-shadow: var(--shadow-sm); }
.pair-head strong { flex: 1; overflow-wrap: anywhere; }
.agent-index { display: inline-grid; place-items: center; width: 34px; height: 34px; border-radius: 50%; background: #fff1f2; color: #d7041a; font-weight: 700; }
.pair-result { margin-top: 12px; padding: 10px; border-radius: 6px; background: #f7f8fa; }
.actions { text-align: center; margin: 28px 0; }
</style>
