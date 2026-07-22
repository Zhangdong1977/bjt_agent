<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message, Modal } from 'ant-design-vue'
import { duplicateCheckApi } from '@/api/client'
import type { DuplicatePairResult, DuplicateResults, ReviewTaskListItem, TodoItem } from '@/types'

const route = useRoute()
const router = useRouter()
const projectId = computed(() => route.params.id as string)
const tasks = ref<ReviewTaskListItem[]>([])
const selectedTaskId = ref('')
const results = ref<DuplicateResults | null>(null)
const pairs = ref<TodoItem[]>([])
const selectedPair = ref<DuplicatePairResult | null>(null)
const loading = ref(false)

const selectedTask = computed(() => tasks.value.find(t => t.id === selectedTaskId.value))

function conclusionLabel(value: string) {
  return {
    suspicious_duplicate: '发现可疑重复',
    no_suspicious_duplicate: '未发现可疑重复',
    manual_review_required: '建议人工复核',
  }[value] || value
}

function conclusionColor(value: string) {
  return value === 'suspicious_duplicate' ? 'red' : value === 'no_suspicious_duplicate' ? 'green' : 'orange'
}

function taskStatusLabel(value: string) {
  return {
    pending: '等待调度', running: '执行中', completed: '已完成',
    completed_with_warnings: '部分完成', failed: '失败', cancelled: '已取消',
  }[value] || value
}

watch(selectedTaskId, async id => {
  if (!id) return
  loading.value = true
  try {
    const [data, pairTodos] = await Promise.all([
      duplicateCheckApi.getResults(projectId.value, id),
      duplicateCheckApi.getPairs(projectId.value, id),
    ])
    results.value = data
    pairs.value = pairTodos
    selectedPair.value = data.pairs[0] || null
    router.replace({ query: { ...route.query, taskId: id } })
  } finally {
    loading.value = false
  }
}, { immediate: false })

async function retryFailed() {
  if (!selectedTaskId.value) return
  try {
    const task = await duplicateCheckApi.retryFailed(projectId.value, selectedTaskId.value)
    router.push({ name: 'duplicate-execution', params: { id: projectId.value }, query: { taskId: task.id } })
  } catch (error: any) {
    message.error(error?.response?.data?.detail || '重试失败')
  }
}

function startAgain() {
  Modal.confirm({
    title: '重新查重',
    content: '将使用当前项目中的标书创建一条新的查重任务，历史结果不会被覆盖。',
    async onOk() {
      const task = await duplicateCheckApi.start(projectId.value)
      router.push({ name: 'duplicate-execution', params: { id: projectId.value }, query: { taskId: task.id } })
    },
  })
}

onMounted(async () => {
  tasks.value = await duplicateCheckApi.getTasks(projectId.value)
  selectedTaskId.value = (route.query.taskId as string) || tasks.value[0]?.id || ''
})
</script>

<template>
  <div class="results-page">
    <a-card :bordered="false" class="toolbar">
      <div class="toolbar-content">
        <a-space>
          <span>查重记录：</span>
          <a-select v-model:value="selectedTaskId" style="width: 300px">
            <a-select-option v-for="task in tasks" :key="task.id" :value="task.id">
              {{ new Date(task.created_at).toLocaleString() }} · {{ taskStatusLabel(task.status) }}
            </a-select-option>
          </a-select>
        </a-space>
        <a-space>
          <a-button v-if="selectedTask?.status === 'completed_with_warnings'" danger @click="retryFailed">仅重试失败项</a-button>
          <a-button type="primary" @click="startAgain">重新查重</a-button>
        </a-space>
      </div>
    </a-card>

    <a-spin :spinning="loading">
      <template v-if="results">
        <div class="stats">
          <a-card><strong>{{ results.summary.document_count }}</strong><span>标书数量</span></a-card>
          <a-card><strong>{{ results.summary.pair_count }}</strong><span>文档对数</span></a-card>
          <a-card><strong class="danger">{{ results.summary.suspicious_pair_count }}</strong><span>可疑文档对</span></a-card>
          <a-card><strong class="danger">{{ results.summary.suspicious_item_count }}</strong><span>可疑重复项</span></a-card>
        </div>

        <a-alert
          v-if="selectedTask?.status === 'completed_with_warnings'"
          type="warning" show-icon :message="selectedTask.error_message || '部分文档对执行失败，当前结果可能不完整'"
          class="warning"
        />

        <a-card :bordered="false" class="result-card">
          <div class="split">
            <aside class="pair-list">
              <h3>文档对结果</h3>
              <button
                v-for="pair in results.pairs" :key="pair.id"
                :class="['pair-button', { active: selectedPair?.id === pair.id }]"
                @click="selectedPair = pair"
              >
                <span>{{ pair.document_a_name }}<br />↔ {{ pair.document_b_name }}</span>
                <a-tag :color="conclusionColor(pair.conclusion)">{{ conclusionLabel(pair.conclusion) }}</a-tag>
              </button>
              <div v-for="pair in pairs.filter(p => p.status === 'failed')" :key="pair.id" class="failed-pair">
                <strong>{{ pair.display_name }}</strong>
                <span>执行失败：{{ pair.error_message }}</span>
              </div>
            </aside>

            <main v-if="selectedPair" class="detail">
              <div class="detail-head">
                <div>
                  <h2>{{ selectedPair.document_a_name }} ↔ {{ selectedPair.document_b_name }}</h2>
                  <p>{{ selectedPair.execution_mode === 'structured' ? '章节结构分析' : '片段召回核验' }} · 规则 {{ selectedPair.rule_version || '-' }}</p>
                </div>
                <a-tag :color="conclusionColor(selectedPair.conclusion)">{{ conclusionLabel(selectedPair.conclusion) }}</a-tag>
              </div>
              <a-alert :type="selectedPair.conclusion === 'suspicious_duplicate' ? 'error' : 'success'" :message="selectedPair.summary || conclusionLabel(selectedPair.conclusion)" show-icon />

              <a-empty v-if="!selectedPair.matches.length" description="未发现符合规则的可疑重复内容" />
              <section v-for="(match, index) in selectedPair.matches" :key="index" class="match">
                <h3>{{ index + 1 }}. {{ match.title }}</h3>
                <p class="analysis">{{ match.analysis }}</p>
                <div class="evidence-grid">
                  <article>
                    <h4>{{ selectedPair.document_a_name }}</h4>
                    <small>{{ match.document_a_evidence.section_title || '未识别章节' }}<template v-if="match.document_a_evidence.page_start"> · 第 {{ match.document_a_evidence.page_start }} 页</template></small>
                    <blockquote>{{ match.document_a_evidence.excerpt }}</blockquote>
                  </article>
                  <article>
                    <h4>{{ selectedPair.document_b_name }}</h4>
                    <small>{{ match.document_b_evidence.section_title || '未识别章节' }}<template v-if="match.document_b_evidence.page_start"> · 第 {{ match.document_b_evidence.page_start }} 页</template></small>
                    <blockquote>{{ match.document_b_evidence.excerpt }}</blockquote>
                  </article>
                </div>
              </section>
            </main>
            <a-empty v-else description="请选择文档对查看结果" class="detail" />
          </div>
        </a-card>
        <a-alert class="disclaimer" type="warning" show-icon message="查重结果由算法和大模型生成，仅供人工复核，不作为认定围标、串标或违法行为的最终依据。" />
      </template>
      <a-empty v-else-if="!loading" description="暂无查重结果" />
    </a-spin>
  </div>
</template>

<style scoped>
.results-page { max-width: 1320px; margin: 0 auto; }
.toolbar, .result-card { border-radius: 12px; box-shadow: var(--shadow-md); margin-bottom: 18px; }
.toolbar-content, .detail-head { display: flex; justify-content: space-between; align-items: center; gap: 20px; }
.stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin-bottom: 18px; }
.stats :deep(.ant-card-body) { display: grid; gap: 4px; text-align: center; }
.stats strong { font-size: 30px; color: #1f4c99; }
.stats .danger { color: #d7041a; }
.stats span { color: #777; }
.warning { margin-bottom: 18px; }
.split { display: grid; grid-template-columns: 300px 1fr; min-height: 520px; }
.pair-list { padding-right: 18px; border-right: 1px solid #eee; }
.pair-button { width: 100%; display: grid; gap: 8px; text-align: left; padding: 12px; margin-bottom: 8px; border: 1px solid #eee; border-radius: 8px; background: #fff; cursor: pointer; }
.pair-button.active { border-color: #d7041a; background: #fff5f6; }
.failed-pair { display: grid; gap: 5px; padding: 12px; margin-top: 8px; color: #d7041a; background: #fff1f0; border-radius: 8px; }
.detail { padding-left: 24px; min-width: 0; }
.detail-head { margin-bottom: 16px; }
.detail h2 { margin: 0 0 6px; font-size: 20px; }
.detail-head p { margin: 0; color: #777; }
.match { margin-top: 20px; padding: 18px; border: 1px solid #eee; border-radius: 10px; }
.match h3 { margin-top: 0; }
.analysis { color: #555; }
.evidence-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.evidence-grid article { padding: 14px; background: #f7f8fa; border-radius: 8px; }
.evidence-grid h4 { margin: 0 0 4px; }
.evidence-grid small { color: #888; }
blockquote { margin: 12px 0 0; padding: 10px 12px; white-space: pre-wrap; border-left: 3px solid #d7041a; background: #fff; }
.disclaimer { margin: 20px 0; }
@media (max-width: 900px) { .stats { grid-template-columns: 1fr 1fr; } .split { grid-template-columns: 1fr; } .pair-list { border-right: 0; padding-right: 0; } .detail { padding: 20px 0 0; } .evidence-grid { grid-template-columns: 1fr; } }
</style>
