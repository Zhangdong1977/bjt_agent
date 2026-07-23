<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message, Modal } from 'ant-design-vue'

import { duplicateApi } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { useProjectStore } from '@/stores/project'
import type { DuplicateResultsResponse } from '@/types'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const projectStore = useProjectStore()
const projectId = computed(() => route.params.id as string)
const selectedTaskId = ref('')
const loading = ref(false)
const data = ref<DuplicateResultsResponse | null>(null)

const leftDocument = computed(() =>
  projectStore.documents.find((document) => document.doc_type === 'duplicate_left')
)
const rightDocument = computed(() =>
  projectStore.documents.find((document) => document.doc_type === 'duplicate_right')
)

const ruleGroups = computed(() => {
  const findings = data.value?.findings || []
  return (data.value?.todos || []).map((todo) => ({
    todo,
    findings: findings.filter((finding) => finding.todo_id === todo.id),
  }))
})

onMounted(async () => {
  await projectStore.selectProject(projectId.value)
  await projectStore.fetchDuplicateTasks()
  const requested = route.query.taskId as string | undefined
  const selected = requested && projectStore.reviewTasks.some((task) => task.id === requested)
    ? requested
    : projectStore.reviewTasks[0]?.id
  if (selected) selectedTaskId.value = selected
})

watch(selectedTaskId, async (taskId) => {
  if (!taskId) return
  if (route.query.taskId !== taskId) {
    await router.replace({ query: { ...route.query, taskId } })
  }
  loading.value = true
  try {
    data.value = await duplicateApi.getResults(projectId.value, taskId)
  } catch {
    data.value = null
    message.error('加载查重结果失败')
  } finally {
    loading.value = false
  }
}, { immediate: true })

function verdictLabel(verdict: string): string {
  return verdict === 'reasonable' ? '合理重复' : '疑似不合理重复'
}

function matchTypeLabel(type: string): string {
  const labels: Record<string, string> = {
    exact: '完全一致',
    near_exact: '近似复制',
    semantic: '语义相似',
    structural: '结构/数字一致',
    ocr_error: '相同 OCR 异常',
    logic_anomaly: '归属逻辑异常',
  }
  return labels[type] || type
}

function locationText(location: Record<string, any>): string {
  const parts = [location?.section]
  if (location?.page) parts.push(`第 ${location.page} 页`)
  if (location?.start_line) {
    parts.push(`第 ${location.start_line}${location.end_line && location.end_line !== location.start_line ? `-${location.end_line}` : ''} 行`)
  }
  return parts.filter(Boolean).join(' · ') || '位置未知'
}

function formatDate(value: string | null): string {
  return value ? new Date(value).toLocaleString('zh-CN') : '暂无'
}

function viewTimeline() {
  if (!selectedTaskId.value) return
  router.push({
    name: 'duplicate-execution',
    params: { id: projectId.value },
    query: { taskId: selectedTaskId.value },
  })
}

function recheck() {
  Modal.confirm({
    title: '确认重新查重',
    content: '将创建新的查重任务，当前任务和结果会保留。',
    okText: '开始查重',
    cancelText: '取消',
    onOk: async () => {
      try {
        await duplicateApi.start(projectId.value)
        await router.push({ name: 'duplicate-execution', params: { id: projectId.value } })
      } catch (error: any) {
        const detail = error?.response?.data?.detail
        message.error(typeof detail === 'object' ? detail?.message : detail || '重新查重失败')
      }
    },
  })
}
</script>

<template>
  <div class="duplicate-results-view">
    <section class="result-header">
      <div>
        <h1>{{ projectStore.currentProject?.name || '标书查重结果' }}</h1>
        <p>A 方：{{ leftDocument?.original_filename || '—' }}　　B 方：{{ rightDocument?.original_filename || '—' }}</p>
      </div>
      <div class="header-actions">
        <button v-if="authStore.isInteriorUser" @click="viewTimeline">查看执行时间线</button>
        <button class="primary" @click="recheck">重新查重</button>
      </div>
    </section>

    <section class="task-bar">
      <label>查重任务</label>
      <select v-model="selectedTaskId">
        <option v-for="task in projectStore.reviewTasks" :key="task.id" :value="task.id">
          {{ formatDate(task.created_at) }} · {{ task.status === 'completed' ? '已完成' : task.status }}
        </option>
      </select>
      <button @click="router.push({ name: 'history' })">返回历史标书</button>
    </section>

    <a-spin :spinning="loading">
      <template v-if="data">
        <a-alert
          v-if="data.summary.completed_rule_count < data.summary.rule_count"
          type="warning"
          show-icon
          message="部分查重规则执行失败，当前结果可能不完整，请结合各规则状态复核。"
        />
        <section class="summary-grid">
          <div><strong>{{ data.summary.rule_count }}</strong><span>规则子代理</span></div>
          <div><strong>{{ data.summary.completed_rule_count }}</strong><span>已完成</span></div>
          <div class="reasonable"><strong>{{ data.summary.reasonable_count }}</strong><span>合理重复</span></div>
          <div class="suspicious"><strong>{{ data.summary.suspicious_count }}</strong><span>疑似不合理重复</span></div>
        </section>

        <section v-for="group in ruleGroups" :key="group.todo.id" class="rule-section">
          <header>
            <div>
              <h2>{{ group.todo.rule_doc_name.replace('.md', '') }}</h2>
              <span :class="['status', group.todo.status]">{{ group.todo.status === 'completed' ? '已完成' : group.todo.status }}</span>
            </div>
            <span>{{ group.findings.length }} 条结果</span>
          </header>

          <div v-if="group.findings.length === 0" class="empty-rule">
            {{ group.todo.status === 'failed' ? (group.todo.error_message || '该规则执行失败') : '未发现重复项' }}
          </div>

          <article
            v-for="finding in group.findings"
            :key="finding.id"
            :class="['finding-card', finding.verdict]"
          >
            <div class="finding-head">
              <span :class="['verdict', finding.verdict]">{{ verdictLabel(finding.verdict) }}</span>
              <strong>{{ finding.check_item_name }}</strong>
              <span class="match-type">{{ matchTypeLabel(finding.match_type) }}</span>
              <span class="score">相似度 {{ Math.round(finding.similarity_score * 100) }}%</span>
            </div>

            <div class="evidence-grid">
              <div>
                <h3>A 方证据</h3>
                <small>{{ finding.left_filename }} · {{ locationText(finding.left_location) }}</small>
                <blockquote>{{ finding.left_excerpt }}</blockquote>
              </div>
              <div>
                <h3>B 方证据</h3>
                <small>{{ finding.right_filename }} · {{ locationText(finding.right_location) }}</small>
                <blockquote>{{ finding.right_excerpt }}</blockquote>
              </div>
            </div>

            <div class="explanation">
              <b>判断理由：</b>{{ finding.explanation }}
              <p v-if="finding.suggestion"><b>处理建议：</b>{{ finding.suggestion }}</p>
            </div>
          </article>
        </section>
      </template>
      <div v-else-if="!loading" class="empty-page">暂无可展示的查重结果</div>
    </a-spin>
  </div>
</template>

<style scoped>
.duplicate-results-view { display: flex; flex-direction: column; gap: 18px; }
.result-header, .task-bar, .rule-section { background: #fff; border: 1px solid #e6e8ee; border-radius: 9px; padding: 20px; }
.result-header { display: flex; justify-content: space-between; align-items: center; }
.result-header h1 { margin: 0 0 6px; font-size: 22px; }
.result-header p { margin: 0; color: #777; }
.header-actions, .task-bar { display: flex; align-items: center; gap: 10px; }
button { border: 1px solid #d4d7df; background: #fff; border-radius: 6px; padding: 8px 14px; cursor: pointer; }
button.primary { border-color: #d7041a; background: #d7041a; color: #fff; }
.task-bar label { font-weight: 600; }
.task-bar select { min-width: 300px; padding: 8px; border: 1px solid #d4d7df; border-radius: 5px; }
.task-bar button { margin-left: auto; }
.summary-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; }
.summary-grid > div { background: #fff; border: 1px solid #e6e8ee; border-radius: 9px; padding: 18px; display: flex; flex-direction: column; }
.summary-grid strong { font-size: 28px; }
.summary-grid span { color: #777; }
.summary-grid .reasonable strong { color: #18864b; }
.summary-grid .suspicious strong { color: #d7041a; }
.rule-section > header { display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #eee; padding-bottom: 12px; margin-bottom: 14px; }
.rule-section h2 { display: inline; margin: 0 10px 0 0; font-size: 18px; }
.status { font-size: 12px; border-radius: 10px; padding: 2px 8px; background: #eee; }
.status.completed { color: #18864b; background: #eaf7f0; }
.status.failed { color: #c62828; background: #fff0f0; }
.finding-card { border: 1px solid #e2e5eb; border-left-width: 4px; border-radius: 7px; padding: 16px; margin-top: 12px; }
.finding-card.reasonable { border-left-color: #18864b; }
.finding-card.suspicious { border-left-color: #d7041a; }
.finding-head { display: flex; align-items: center; gap: 10px; }
.finding-head strong { flex: 1; }
.verdict { border-radius: 4px; padding: 3px 8px; font-size: 12px; }
.verdict.reasonable { color: #18864b; background: #eaf7f0; }
.verdict.suspicious { color: #d7041a; background: #fff0f0; }
.match-type { color: #666; font-size: 12px; }
.score { font-weight: 600; color: #d7041a; }
.evidence-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-top: 14px; }
.evidence-grid > div { background: #fafbfc; border-radius: 6px; padding: 13px; }
.evidence-grid h3 { margin: 0 0 4px; font-size: 14px; }
.evidence-grid small { color: #888; }
blockquote { margin: 10px 0 0; padding-left: 12px; border-left: 2px solid #ccd1db; white-space: pre-wrap; color: #444; }
.explanation { background: #f7f8fa; margin-top: 12px; padding: 12px; line-height: 1.6; }
.explanation p { margin: 6px 0 0; }
.empty-rule, .empty-page { color: #999; text-align: center; padding: 28px; }
@media (max-width: 900px) { .summary-grid { grid-template-columns: 1fr 1fr; } .evidence-grid { grid-template-columns: 1fr; } }
</style>
