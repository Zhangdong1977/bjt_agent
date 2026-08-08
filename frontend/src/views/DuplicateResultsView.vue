<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message, Modal } from 'ant-design-vue'

import { duplicateApi } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { useProjectStore } from '@/stores/project'
import type {
  DuplicateEvidenceCluster,
  DuplicateMatrixResponse,
  DuplicateResultsResponse,
  DuplicateTableComparison,
} from '@/types'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const projectStore = useProjectStore()
const projectId = computed(() => route.params.id as string)
const selectedTaskId = ref('')
const loading = ref(false)
const data = ref<DuplicateResultsResponse | null>(null)
const tableComparisons = ref<DuplicateTableComparison[]>([])
const tableWarnings = ref<string[]>([])
const matrix = ref<DuplicateMatrixResponse | null>(null)
const clusters = ref<DuplicateEvidenceCluster[]>([])

const leftDocument = computed(() =>
  projectStore.documents.find((document) => document.doc_type === 'duplicate_left')
)
const rightDocument = computed(() =>
  projectStore.documents.find((document) => document.doc_type === 'duplicate_right')
)
const batchDocuments = computed(() =>
  projectStore.documents
    .filter((document) => document.doc_type === 'duplicate_bid')
    .sort((a, b) => (a.duplicate_ordinal ?? 999) - (b.duplicate_ordinal ?? 999)),
)

// 重复点按「A 文档位置顺序」排列：先按 A 文档文件名（batch 多文档分桶），
// 再按 section 文本（中文本地化排序），最后按 start_line / end_line。
// 位置字段可能缺失，start_line 用 Infinity 兜底（与后端 grouper 同思路）。
const sortedFindings = computed(() => {
  const findings = data.value?.findings || []
  return [...findings].sort((a, b) => {
    const fileCmp = String(a.left_filename ?? '').localeCompare(String(b.left_filename ?? ''), 'zh-Hans-CN')
    if (fileCmp !== 0) return fileCmp
    const secCmp = String(a.left_location?.section ?? '').localeCompare(
      String(b.left_location?.section ?? ''),
      'zh-Hans-CN',
    )
    if (secCmp !== 0) return secCmp
    const aStart = Number(a.left_location?.start_line) || Infinity
    const bStart = Number(b.left_location?.start_line) || Infinity
    if (aStart !== bStart) return aStart - bStart
    const aEnd = Number(a.left_location?.end_line) || Infinity
    const bEnd = Number(b.left_location?.end_line) || Infinity
    return aEnd - bEnd
  })
})

const selectedFindingId = ref('')
const selectedFinding = computed(() =>
  sortedFindings.value.find((finding) => finding.id === selectedFindingId.value) || null,
)

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
    try {
      matrix.value = await duplicateApi.getMatrix(projectId.value, taskId)
      clusters.value = await duplicateApi.getClusters(projectId.value, taskId, true)
    } catch {
      matrix.value = null
      clusters.value = []
    }
    try {
      const tables = await duplicateApi.getTableComparisons(projectId.value, taskId)
      tableComparisons.value = tables.comparisons || []
      tableWarnings.value = tables.warnings || []
    } catch {
      tableComparisons.value = []
      tableWarnings.value = ['表格结构对照暂不可用']
    }
    // 默认选中按 A 文档位置排序后的第一条；若上次选中仍在新结果中则保留。
    const stillSelected = (data.value?.findings || []).some(
      (item) => item.id === selectedFindingId.value,
    )
    selectedFindingId.value = stillSelected ? selectedFindingId.value : (sortedFindings.value[0]?.id ?? '')
  } catch {
    data.value = null
    tableComparisons.value = []
    message.error('加载查重结果失败')
  } finally {
    loading.value = false
  }
}, { immediate: true })

function verdictLabel(verdict: string): string {
  const labels: Record<string, string> = {
    reasonable: '合理重复',
    suspicious: '疑似不合理重复',
    unknown: '证据不足 / 来源待确认',
  }
  return labels[verdict] || '证据不足 / 来源待确认'
}

function sourceBasisLabel(sourceBasis: string): string {
  const labels: Record<string, string> = {
    tender: '招标文件证据',
    public: '公开来源证据',
    bidder_authored: '投标文件原文证据',
    unknown: '来源待确认',
  }
  return labels[sourceBasis] || '来源待确认'
}

function evidenceStrength(value: unknown): string {
  const number = Number(value)
  return Number.isFinite(number) ? `${Math.round(number * 100)}%` : '待评估'
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
  if (location?.page_number || location?.page) parts.push(`第 ${location.page_number || location.page} 页`)
  if (location?.start_line) {
    parts.push(`第 ${location.start_line}${location.end_line && location.end_line !== location.start_line ? `-${location.end_line}` : ''} 行`)
  }
  return parts.filter(Boolean).join(' · ') || '位置未知'
}

function formatDate(value: string | null): string {
  return value ? new Date(value).toLocaleString('zh-CN') : '暂无'
}

function matrixStrength(pair: { max_evidence_strength: number | null; suspicious_count: number; finding_count: number }): string {
  const strength = Number(pair.max_evidence_strength || 0)
  if (pair.suspicious_count > 0 || strength >= 0.75) return 'matrix-high'
  if (strength >= 0.4 || pair.finding_count > 0) return 'matrix-medium'
  return 'matrix-low'
}

function clusterNames(cluster: DuplicateEvidenceCluster): string {
  const names = cluster.occurrences
    ?.map((item) => item.display_name || item.filename || item.document_id)
    .filter((value, index, values) => values.indexOf(value) === index)
  return (names || cluster.document_ids).join('、')
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
      <div class="result-title">
        <h1>{{ '标书查重结果' }}</h1>
        <p v-if="matrix && matrix.members.length > 2">
          批量文档：{{ batchDocuments.map((document) => document.duplicate_display_name || document.original_filename).join('、') }}
        </p>
        <p v-else>A 方：{{ leftDocument?.original_filename || '—' }}　　B 方：{{ rightDocument?.original_filename || '—' }}</p>
      </div>
      <div class="header-actions">
        <div class="task-control">
          <label for="duplicate-task-select">查重任务</label>
          <select id="duplicate-task-select" v-model="selectedTaskId">
            <option v-for="task in projectStore.reviewTasks" :key="task.id" :value="task.id">
              {{ formatDate(task.created_at) }} · {{ task.status === 'completed' ? '已完成' : task.status }}
            </option>
          </select>
        </div>
        <button v-if="authStore.isInteriorUser" @click="viewTimeline">查看执行时间线</button>
        <button class="primary" @click="recheck">重新查重</button>
        <button @click="router.push({ name: 'history' })">返回历史标书</button>
      </div>
    </section>

    <a-spin :spinning="loading">
      <template v-if="data">
        <a-alert
          v-if="data.summary.completed_rule_count < data.summary.rule_count"
          type="warning"
          show-icon
          message="部分查重规则执行失败，当前结果可能不完整，请结合各规则状态复核。"
        />
        <a-alert
          v-if="data.summary.coverage_status && data.summary.coverage_status !== 'complete'"
          type="warning"
          show-icon
          :message="`文档解析覆盖度为${data.summary.coverage_status === 'insufficient' ? '不足' : '部分'}，未发现重复不能视为绝对结论。`"
          :description="(data.summary.coverage_warnings || []).join('；') || '请打开解析诊断查看未覆盖对象。'"
        />
        <section class="summary-grid">
          <div><strong>{{ data.summary.rule_count }}</strong><span>规则子代理</span></div>
          <div><strong>{{ data.summary.completed_rule_count }}</strong><span>已完成</span></div>
          <div class="reasonable"><strong>{{ data.summary.reasonable_count }}</strong><span>合理重复</span></div>
          <div class="suspicious"><strong>{{ data.summary.suspicious_count }}</strong><span>疑似不合理重复</span></div>
          <div class="unknown"><strong>{{ data.summary.unknown_count || 0 }}</strong><span>证据不足</span></div>
        </section>

        <section v-if="matrix && matrix.members.length > 2" class="rule-section matrix-section">
          <header>
            <div>
              <h2>文档对证据矩阵</h2>
              <span class="status completed">仅表示证据强度 / 需复核程度</span>
            </div>
            <span>{{ matrix.members.length }} 份文档 · {{ matrix.pairs.length }} 个文档对</span>
          </header>
          <div class="matrix-table-wrap">
            <table class="matrix-table">
              <thead>
                <tr>
                  <th>文档</th>
                  <th v-for="member in matrix.members" :key="member.document_id">
                    {{ member.display_name || member.filename || member.party_key }}
                  </th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in matrix.members" :key="row.document_id">
                  <th>{{ row.display_name || row.filename || row.party_key }}</th>
                  <td v-for="column in matrix.members" :key="column.document_id">
                    <template v-if="row.document_id === column.document_id">—</template>
                    <template v-else>
                      <div
                        v-for="pair in matrix.pairs.filter((item) =>
                          (item.left_document_id === row.document_id && item.right_document_id === column.document_id) ||
                          (item.left_document_id === column.document_id && item.right_document_id === row.document_id),
                        )"
                        :key="pair.id"
                        :class="['matrix-cell', matrixStrength(pair)]"
                        :title="`finding ${pair.finding_count} · evidence ${Math.round(Number(pair.max_evidence_strength || 0) * 100)}%`"
                      >
                        {{ pair.finding_count }} / {{ Math.round(Number(pair.max_evidence_strength || 0) * 100) }}%
                      </div>
                    </template>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <section v-if="clusters.length" class="rule-section cluster-section">
          <header>
            <div>
              <h2>跨文档证据簇</h2>
              <span class="status completed">保留全部 occurrences</span>
            </div>
            <span>{{ clusters.length }} 个证据簇</span>
          </header>
          <article v-for="cluster in clusters.slice(0, 50)" :key="cluster.id" class="cluster-card">
            <div class="cluster-head">
              <strong>{{ cluster.content_type }} · {{ cluster.occurrence_count }} 处</strong>
              <span>证据强度 {{ evidenceStrength(cluster.evidence_strength) }}</span>
            </div>
            <p class="cluster-docs">文档：{{ clusterNames(cluster) }}</p>
            <blockquote>{{ cluster.representative_excerpt }}</blockquote>
            <div v-if="cluster.occurrences?.length" class="cluster-occurrences">
              <div v-for="occurrence in cluster.occurrences" :key="occurrence.id">
                <span>{{ occurrence.display_name || occurrence.filename || occurrence.document_id }}</span>
                <small>{{ occurrence.location?.section || '位置未知' }}</small>
                <p>{{ occurrence.excerpt }}</p>
              </div>
            </div>
          </article>
        </section>

        <section v-if="tableComparisons.length || tableWarnings.length" class="rule-section table-section">
          <header>
            <div>
              <h2>结构化表格对照</h2>
              <span class="status completed">独立通道</span>
            </div>
            <span>{{ tableComparisons.length }} 组行列候选</span>
          </header>
          <a-alert
            v-if="tableWarnings.length"
            type="warning"
            show-icon
            :message="tableWarnings.join('；')"
          />
          <div v-for="comparison in tableComparisons.slice(0, 20)" :key="comparison.table_candidate_id" class="table-comparison">
            <div class="table-score-row">
              <strong>证据强度 {{ Math.round(comparison.score * 100) }}%</strong>
              <span>表头 {{ Math.round(comparison.header_similarity * 100) }}%</span>
              <span>行对齐 {{ Math.round(comparison.row_alignment_score * 100) }}%</span>
              <span>数字签名 {{ Math.round(comparison.numeric_signature_score * 100) }}%</span>
              <span>罕见单元格 {{ Math.round(comparison.rare_cell_overlap * 100) }}%</span>
              <span>结构 {{ Math.round(comparison.table_structure_score * 100) }}%</span>
            </div>
            <div class="evidence-grid">
              <div>
                <h3>A 方 · {{ (comparison.left.section_path || []).join(' / ') || '表格' }}</h3>
                <small>表 {{ comparison.left.table_id }} · 行 {{ Number(comparison.left.row_index) + 1 }}</small>
                <div class="cell-row">
                  <span v-for="(cell, index) in comparison.left.cells" :key="index">{{ cell }}</span>
                </div>
              </div>
              <div>
                <h3>B 方 · {{ (comparison.right.section_path || []).join(' / ') || '表格' }}</h3>
                <small>表 {{ comparison.right.table_id }} · 行 {{ Number(comparison.right.row_index) + 1 }}</small>
                <div class="cell-row">
                  <span v-for="(cell, index) in comparison.right.cells" :key="index">{{ cell }}</span>
                </div>
              </div>
            </div>
            <p v-if="comparison.shared_rare_cells.length" class="rare-cells">
              共同罕见值：{{ comparison.shared_rare_cells.join('、') }}
            </p>
          </div>
        </section>

        <section class="rule-section duplicate-list-section">
          <header>
            <div>
              <h2>重复点（按 A 文档位置排序）</h2>
              <span class="status completed">点击列表查看 A/B 重复点说明</span>
            </div>
            <span>{{ sortedFindings.length }} 条</span>
          </header>

          <div v-if="sortedFindings.length === 0" class="empty-rule">
            未发现达到报告门槛的重复点
          </div>

          <div v-else class="duplicate-list-layout">
            <ul class="finding-list">
              <li
                v-for="(finding, index) in sortedFindings"
                :key="finding.id"
                :class="{ active: finding.id === selectedFindingId }"
                @click="selectedFindingId = finding.id"
              >
                <span class="idx">{{ index + 1 }}</span>
                <span :class="['verdict', finding.verdict]">{{ verdictLabel(finding.verdict) }}</span>
                <strong :title="finding.check_item_name">{{ finding.check_item_name }}</strong>
                <span class="loc">{{ locationText(finding.left_location) }}</span>
                <span class="score">相似度 {{ Math.round(finding.similarity_score * 100) }}%</span>
                <small v-if="finding.left_filename">{{ finding.left_filename }}</small>
              </li>
            </ul>

            <div class="finding-detail">
              <article v-if="selectedFinding" :class="['finding-card', selectedFinding.verdict]">
                <div class="finding-head">
                  <span :class="['verdict', selectedFinding.verdict]">{{ verdictLabel(selectedFinding.verdict) }}</span>
                  <strong>{{ selectedFinding.check_item_name }}</strong>
                  <span class="match-type">{{ matchTypeLabel(selectedFinding.match_type) }}</span>
                  <span class="score">相似度 {{ Math.round(selectedFinding.similarity_score * 100) }}%</span>
                  <span class="source-basis">{{ sourceBasisLabel(selectedFinding.source_basis) }}</span>
                  <span class="evidence-strength">证据强度 {{ evidenceStrength(selectedFinding.evidence?.evidence_strength) }}</span>
                  <span v-if="Number(selectedFinding.evidence?.collapsed_count) > 1" class="aggregate-count">
                    已合并 {{ selectedFinding.evidence?.collapsed_count }} 处
                  </span>
                </div>

                <div class="evidence-grid">
                  <div>
                    <h3>A 方证据</h3>
                    <small>{{ selectedFinding.left_filename }} · {{ locationText(selectedFinding.left_location) }}</small>
                    <img
                      v-if="selectedFinding.left_location?.thumbnail_url"
                      class="evidence-image"
                      :src="selectedFinding.left_location.thumbnail_url"
                      alt="A 方图片证据"
                    />
                    <blockquote>{{ selectedFinding.left_excerpt }}</blockquote>
                  </div>
                  <div>
                    <h3>B 方证据</h3>
                    <small>{{ selectedFinding.right_filename }} · {{ locationText(selectedFinding.right_location) }}</small>
                    <img
                      v-if="selectedFinding.right_location?.thumbnail_url"
                      class="evidence-image"
                      :src="selectedFinding.right_location.thumbnail_url"
                      alt="B 方图片证据"
                    />
                    <blockquote>{{ selectedFinding.right_excerpt }}</blockquote>
                  </div>
                </div>

                <div class="explanation">
                  <b>判断理由：</b>{{ selectedFinding.explanation }}
                  <p v-if="selectedFinding.suggestion"><b>处理建议：</b>{{ selectedFinding.suggestion }}</p>
                </div>
                <div v-if="selectedFinding.evidence?.image_comparison" class="image-metrics">
                  图片通道 {{ evidenceStrength(selectedFinding.evidence.image_comparison.image_score) }} ·
                  A hash {{ String(selectedFinding.evidence.image_comparison.left_image_sha256 || '—').slice(0, 12) }} ·
                  B hash {{ String(selectedFinding.evidence.image_comparison.right_image_sha256 || '—').slice(0, 12) }}
                </div>
                <div v-if="selectedFinding.evidence?.source_reference" class="source-reference">
                  <b>可追溯来源：</b>
                  {{ selectedFinding.evidence.source_reference.source_filename }} ·
                  {{ selectedFinding.evidence.source_reference.source_version }} ·
                  hash {{ String(selectedFinding.evidence.source_reference.source_snapshot_hash).slice(0, 16) }}
                  <blockquote>{{ selectedFinding.evidence.source_reference.source_excerpt }}</blockquote>
                </div>
              </article>
              <div v-else class="empty-rule">点击左侧列表项查看重复点详情</div>
            </div>
          </div>
        </section>
      </template>
      <div v-else-if="!loading" class="empty-page">暂无可展示的查重结果</div>
    </a-spin>
  </div>
</template>

<style scoped>
.duplicate-results-view { display: flex; flex-direction: column; gap: 18px; }
.result-header, .rule-section { background: #fff; border: 1px solid #e6e8ee; border-radius: 9px; padding: 20px; }
.result-header { display: flex; align-items: center; gap: 24px; }
.result-title { min-width: 0; flex: 1; }
.result-header h1 { margin: 0 0 6px; font-size: 22px; }
.result-header p { margin: 0; color: #777; }
.header-actions { display: flex; align-items: center; gap: 10px; margin-left: auto; white-space: nowrap; }
.task-control { display: flex; align-items: center; gap: 10px; }
button { border: 1px solid #d4d7df; background: #fff; border-radius: 6px; padding: 8px 14px; cursor: pointer; }
button.primary { border-color: #d7041a; background: #d7041a; color: #fff; }
.task-control label { font-weight: 600; }
.task-control select { min-width: 260px; padding: 8px; border: 1px solid #d4d7df; border-radius: 5px; }
.summary-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 14px; }
.summary-grid > div { background: #fff; border: 1px solid #e6e8ee; border-radius: 9px; padding: 18px; display: flex; flex-direction: column; }
.summary-grid strong { font-size: 28px; }
.summary-grid span { color: #777; }
.summary-grid .reasonable strong { color: #18864b; }
.summary-grid .suspicious strong { color: #d7041a; }
.summary-grid .unknown strong { color: #b77900; }
.matrix-table-wrap { overflow-x: auto; }
.matrix-table { width: 100%; border-collapse: collapse; min-width: 720px; }
.matrix-table th, .matrix-table td { border: 1px solid #e1e4eb; padding: 8px; text-align: center; }
.matrix-table th { background: #f7f8fa; text-align: left; }
.matrix-cell { border-radius: 4px; padding: 6px 4px; font-size: 12px; }
.matrix-high { color: #9d1c1c; background: #ffe4e4; }
.matrix-medium { color: #805c00; background: #fff3cd; }
.matrix-low { color: #4f6474; background: #eef3f7; }
.cluster-card { border: 1px solid #e2e5eb; border-radius: 7px; padding: 13px; margin-top: 10px; }
.cluster-head { display: flex; justify-content: space-between; gap: 12px; }
.cluster-docs { margin: 7px 0; color: #666; }
.cluster-occurrences { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 8px; margin-top: 10px; }
.cluster-occurrences > div { background: #fafbfc; border-radius: 5px; padding: 8px; }
.cluster-occurrences small { display: block; color: #888; margin-top: 3px; }
.cluster-occurrences p { margin: 5px 0 0; white-space: pre-wrap; }
.rule-section > header { display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #eee; padding-bottom: 12px; margin-bottom: 14px; }
.rule-section h2 { display: inline; margin: 0 10px 0 0; font-size: 18px; }
.status { font-size: 12px; border-radius: 10px; padding: 2px 8px; background: #eee; }
.status.completed { color: #18864b; background: #eaf7f0; }
.status.failed { color: #c62828; background: #fff0f0; }
/* 左列表 + 右详情布局：列表随页面整体滚动，无独立滚动条 */
.duplicate-list-layout { display: grid; grid-template-columns: minmax(280px, 380px) 1fr; gap: 16px; align-items: start; }
.finding-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
.finding-list li { cursor: pointer; padding: 10px 12px; border: 1px solid #e2e5eb; border-radius: 6px; display: flex; flex-wrap: wrap; align-items: center; gap: 6px 10px; background: #fff; }
.finding-list li:hover { border-color: #c0c4cc; background: #fafbfc; }
.finding-list li.active { border-color: #d7041a; background: #fff5f6; }
.finding-list .idx { flex: 0 0 auto; min-width: 22px; height: 22px; line-height: 22px; text-align: center; border-radius: 50%; background: #eef0f4; color: #555; font-size: 12px; }
.finding-list li.active .idx { background: #d7041a; color: #fff; }
.finding-list .verdict { flex: 0 0 auto; }
.finding-list strong { flex: 1 1 160px; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 14px; }
.finding-list .loc { flex: 1 1 100%; color: #888; font-size: 12px; }
.finding-list .score { flex: 0 0 auto; font-weight: 600; color: #d7041a; font-size: 12px; }
.finding-list small { flex: 1 1 100%; color: #999; font-size: 11px; }
.finding-detail { min-width: 0; }
.finding-card { border: 1px solid #e2e5eb; border-left-width: 4px; border-radius: 7px; padding: 16px; margin-top: 0; }
.finding-card.reasonable { border-left-color: #18864b; }
.finding-card.suspicious { border-left-color: #d7041a; }
.finding-card.unknown { border-left-color: #d99b13; }
.finding-head { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; }
.finding-head strong { min-width: 220px; flex: 1; }
.verdict { border-radius: 4px; padding: 3px 8px; font-size: 12px; }
.verdict.reasonable { color: #18864b; background: #eaf7f0; }
.verdict.suspicious { color: #d7041a; background: #fff0f0; }
.verdict.unknown { color: #8b6400; background: #fffbe6; }
.match-type { color: #666; font-size: 12px; }
.score { font-weight: 600; color: #d7041a; }
.source-basis, .evidence-strength, .aggregate-count { color: #777; font-size: 12px; }
.aggregate-count { color: #6c4fa3; }
.evidence-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-top: 14px; }
.evidence-grid > div { background: #fafbfc; border-radius: 6px; padding: 13px; }
.evidence-grid h3 { margin: 0 0 4px; font-size: 14px; }
.evidence-grid small { color: #888; }
.evidence-image { display: block; max-width: 100%; max-height: 280px; margin-top: 10px; border: 1px solid #e0e3e9; border-radius: 4px; object-fit: contain; background: #fff; }
blockquote { margin: 10px 0 0; padding-left: 12px; border-left: 2px solid #ccd1db; white-space: pre-wrap; color: #444; }
.explanation { background: #f7f8fa; margin-top: 12px; padding: 12px; line-height: 1.6; }
.explanation p { margin: 6px 0 0; }
.source-reference { margin-top: 12px; padding: 12px; border: 1px solid #dfe7f2; background: #f7fbff; border-radius: 6px; }
.image-metrics { margin-top: 10px; color: #666; font-size: 12px; }
.table-comparison { padding: 14px 0; border-top: 1px solid #eef0f4; }
.table-comparison:first-of-type { border-top: 0; }
.table-score-row { display: flex; flex-wrap: wrap; gap: 8px 16px; color: #666; font-size: 12px; }
.table-score-row strong { color: #d7041a; font-size: 14px; }
.cell-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(100px, 1fr)); margin-top: 10px; border: 1px solid #e0e3e9; }
.cell-row span { padding: 7px; border-right: 1px solid #e0e3e9; word-break: break-all; }
.rare-cells { margin: 8px 0 0; color: #8b6400; }
.empty-rule, .empty-page { color: #999; text-align: center; padding: 28px; }
@media (max-width: 1200px) {
  .result-header { align-items: stretch; flex-direction: column; }
  .header-actions { margin-left: 0; }
}
@media (max-width: 900px) {
  .header-actions { flex-wrap: wrap; white-space: normal; }
  .task-control { width: 100%; }
  .task-control select { min-width: 0; flex: 1; }
  .summary-grid { grid-template-columns: 1fr 1fr; }
  .duplicate-list-layout { grid-template-columns: 1fr; }
  .evidence-grid { grid-template-columns: 1fr; }
}
</style>
