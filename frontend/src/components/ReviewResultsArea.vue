<script setup lang="ts">
import { computed, ref } from 'vue'
import type { ReviewResponse, ReviewResult } from '@/types'
import { renderMarkdown } from '@/utils/markdown'
import { reviewApi } from '@/api/client'
import BatchConfirmBar from '@/components/feedback/BatchConfirmBar.vue'
import FindingsTable from '@/components/execution/FindingsTable.vue'

const props = defineProps<{
  reviewResults: ReviewResponse | null | undefined
  todos: any[]
  projectId: string
  taskId: string
}>()

const summary = computed(() => props.reviewResults?.summary ?? {
  category_count: 0,
  check_item_count: 0,
  risk_item_count: 0,
})

const findings = computed<ReviewResult[]>(() => props.reviewResults?.findings ?? [])

const hasFindings = computed(() => findings.value.length > 0)

const selectedKey = ref<string | null>(null)
const reportContent = ref<string>('')
const reportLoading = ref(false)

interface SubAgentGroup {
  key: string
  label: string
  ruleDocName: string
  allFindings: ReviewResult[]
  nonCompliantCount: number
  compliantCount: number
  topSeverity: 'critical' | 'major' | 'minor' | 'none'
  isCompliant: boolean
}

const subAgentGroups = computed<SubAgentGroup[]>(() => {
  const findingsByAgent = new Map<string, ReviewResult[]>()
  for (const f of findings.value) {
    const key = f.rule_doc_name || f.requirement_key
    if (!findingsByAgent.has(key)) {
      findingsByAgent.set(key, [])
    }
    findingsByAgent.get(key)!.push(f)
  }

  const groups: SubAgentGroup[] = []

  for (const todo of props.todos) {
    if (todo.status !== 'completed') continue

    const agentFindings = findingsByAgent.get(todo.rule_doc_name) || []
    const nonCompliant = agentFindings.filter(f => !f.is_compliant)
    const isCompliant = nonCompliant.length === 0

    let topSeverity: 'critical' | 'major' | 'minor' | 'none' = 'none'
    for (const f of nonCompliant) {
      if (f.severity === 'critical') { topSeverity = 'critical'; break }
      if (f.severity === 'major') topSeverity = 'major'
      if (topSeverity === 'none') topSeverity = 'minor'
    }

    groups.push({
      key: todo.id,
      label: (todo.rule_doc_name || '').replace('.md', ''),
      ruleDocName: todo.rule_doc_name,
      allFindings: agentFindings,
      nonCompliantCount: nonCompliant.length,
      compliantCount: agentFindings.filter(f => f.is_compliant).length,
      topSeverity,
      isCompliant,
    })
  }

  // Also include findings not matched to any todo (safety net)
  const matchedKeys = new Set(props.todos.map((t: any) => t.rule_doc_name))
  for (const [key, agentFindings] of findingsByAgent) {
    if (matchedKeys.has(key)) continue
    const nonCompliant = agentFindings.filter(f => !f.is_compliant)
    const isCompliant = nonCompliant.length === 0

    let topSeverity: 'critical' | 'major' | 'minor' | 'none' = 'none'
    for (const f of nonCompliant) {
      if (f.severity === 'critical') { topSeverity = 'critical'; break }
      if (f.severity === 'major') topSeverity = 'major'
      if (topSeverity === 'none') topSeverity = 'minor'
    }

    groups.push({
      key,
      label: key.replace('.md', ''),
      ruleDocName: key,
      allFindings: agentFindings,
      nonCompliantCount: nonCompliant.length,
      compliantCount: agentFindings.filter(f => f.is_compliant).length,
      topSeverity,
      isCompliant,
    })
  }

  // Sort: risk items first (by severity), then compliant items
  const severityOrder = { critical: 0, major: 1, minor: 2, none: 3 }
  groups.sort((a, b) => {
    const sevDiff = severityOrder[a.topSeverity] - severityOrder[b.topSeverity]
    if (sevDiff !== 0) return sevDiff
    return b.nonCompliantCount - a.nonCompliantCount
  })

  return groups
})

const selectedGroup = computed<SubAgentGroup | null>(() => {
  if (!selectedKey.value) return null
  return subAgentGroups.value.find(g => g.key === selectedKey.value) ?? null
})

async function selectGroup(key: string) {
  selectedKey.value = key
  reportContent.value = ''
  reportLoading.value = true
  try {
    reportContent.value = await reviewApi.getTodoReport(
      props.projectId,
      props.taskId,
      key,
    )
  } catch {
    reportContent.value = '无法加载报告内容'
  } finally {
    reportLoading.value = false
  }
}

function getSeverityColorClass(severity: string): string {
  switch (severity) {
    case 'critical': return 'sev-critical'
    case 'major': return 'sev-major'
    case 'minor': return 'sev-minor'
    default: return ''
  }
}
</script>

<template>
  <div class="review-results-area">
    <!-- 统计区 -->
    <div v-if="hasFindings" class="stats-bar">
      <div class="stat-box">
        <div class="stat-val sv-blue">{{ summary.category_count }}</div>
        <div class="stat-lbl">检查大类</div>
      </div>
      <div class="stat-box">
        <div class="stat-val sv-purple">{{ summary.check_item_count }}</div>
        <div class="stat-lbl">检查项总数</div>
      </div>
      <div class="stat-box">
        <div class="stat-val sv-red">{{ summary.risk_item_count }}</div>
        <div class="stat-lbl">风险项总数</div>
      </div>
    </div>

    <!-- 左右分栏 -->
    <div v-if="hasFindings" class="split-panel">
      <!-- 左面板：检查项清单 -->
      <div class="left-panel">
        <div class="panel-header">
          <span>检查项清单</span>
          <span class="count-badge">{{ subAgentGroups.length }}</span>
        </div>
        <div class="group-list">
          <div
            v-for="group in subAgentGroups"
            :key="group.key"
            :class="['group-item', { selected: selectedKey === group.key }]"
            @click="selectGroup(group.key)"
          >
            <div class="group-item-left">
              <span :class="['severity-dot', getSeverityColorClass(group.topSeverity)]"></span>
              <span class="group-label">{{ group.label }}</span>
            </div>
            <div class="group-item-right">
              <span v-if="group.isCompliant" class="group-count pass">✓</span>
              <span v-else class="group-count risk">!</span>
            </div>
          </div>
        </div>
      </div>

      <!-- 右面板：报告内容 -->
      <div class="right-panel">
        <template v-if="selectedGroup">
          <div class="detail-header">
            <h3 class="detail-title">{{ selectedGroup.label }}</h3>
            <div class="detail-stats">
              <span :class="['status-tag', selectedGroup.isCompliant ? 'compliant' : 'risk']">
                {{ selectedGroup.isCompliant ? '全部合规' : `风险项 ${selectedGroup.nonCompliantCount}` }}
              </span>
              <BatchConfirmBar
                v-if="!selectedGroup.isCompliant"
                :project-id="projectId"
                :task-id="taskId"
                :rule-doc-name="selectedGroup.ruleDocName"
                :finding-count="selectedGroup.nonCompliantCount"
                @batch-confirmed="() => {}"
              />
            </div>
          </div>
          <div class="report-content">
            <div v-if="reportLoading" class="report-loading">加载中...</div>
            <div v-else class="markdown-body" v-html="renderMarkdown(reportContent)"></div>
            <!-- Findings detail table with feedback -->
            <FindingsTable
              v-if="selectedGroup.allFindings.length > 0"
              :findings="selectedGroup.allFindings"
              :project-id="projectId"
            />
          </div>
        </template>
        <div v-else class="detail-placeholder">
          ← 选择左侧检查项查看报告
        </div>
      </div>
    </div>

    <!-- 空状态 -->
    <div v-if="!hasFindings" class="no-results">
      <p>暂无审查结果</p>
    </div>
  </div>
</template>

<style scoped>
.review-results-area {
  padding: 1rem;
}

/* 统计区 */
.stats-bar {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: 6px;
  margin-bottom: 1rem;
}

.stat-box {
  background: var(--bg2);
  border: 1px solid var(--line);
  border-radius: var(--r);
  padding: 10px 12px;
}

.stat-val {
  font-size: 22px;
  font-weight: 600;
  line-height: 1;
  letter-spacing: -0.02em;
}

.sv-blue { color: var(--blue); }
.sv-purple { color: var(--blue); }
.sv-red { color: var(--red); }

.stat-lbl {
  font-size: 10px;
  color: var(--muted);
  margin-top: 4px;
}

/* 左右分栏 */
.split-panel {
  display: grid;
  grid-template-columns: 280px 1fr;
  gap: 1px;
  background: var(--line);
  border: 1px solid var(--line);
  border-radius: var(--r);
  overflow: hidden;
}

.left-panel,
.right-panel {
  background: var(--bg1);
}

/* 左面板 */
.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 14px;
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
  border-bottom: 1px solid var(--line);
}

.count-badge {
  background: var(--red-dim);
  color: var(--red);
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 10px;
  font-weight: 500;
}

.group-list {
  overflow-y: auto;
  max-height: 500px;
}

.group-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  cursor: pointer;
  border-bottom: 1px solid var(--line);
  transition: background 0.12s;
}

.group-item:hover {
  background: var(--bg2);
}

.group-item.selected {
  background: var(--blue-bg);
  border-left: 3px solid var(--blue-dim);
}

.group-item-left {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.severity-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}

.severity-dot.sev-critical { background: var(--red); }
.severity-dot.sev-major { background: var(--amber); }
.severity-dot.sev-minor { background: var(--amber); }

.group-label {
  font-size: 13px;
  color: var(--text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.group-item-right {
  display: flex;
  align-items: center;
  gap: 2px;
  flex-shrink: 0;
  margin-left: 8px;
}

.group-count {
  font-size: 12px;
  font-weight: 500;
}

.group-count.risk { color: var(--red); }
.group-count.pass { color: var(--green); }

/* 右面板 */
.right-panel {
  overflow-y: auto;
  max-height: 600px;
}

.detail-header {
  padding: 12px 16px;
  border-bottom: 1px solid var(--line);
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.detail-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--text);
  margin: 0;
}

.detail-stats {
  display: flex;
  gap: 12px;
}

.status-tag {
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
}

.status-tag.compliant { background: var(--green-dim, #d4edda); color: var(--green); }
.status-tag.risk { background: var(--red-dim); color: var(--red); }

/* 报告内容区域 */
.report-content {
  padding: 16px;
}

.report-loading {
  text-align: center;
  color: var(--muted);
  padding: 2rem;
}

.markdown-body {
  line-height: 1.7;
  word-break: break-word;
  color: var(--text);
}

.markdown-body :deep(h1) {
  font-size: 20px;
  font-weight: 700;
  margin: 0 0 12px;
  padding-bottom: 8px;
  border-bottom: 2px solid var(--line);
}

.markdown-body :deep(h2) {
  font-size: 17px;
  font-weight: 600;
  margin: 16px 0 8px;
  padding-bottom: 4px;
  border-bottom: 1px solid var(--line);
}

.markdown-body :deep(h3) {
  font-size: 15px;
  font-weight: 600;
  margin: 12px 0 6px;
}

.markdown-body :deep(p) {
  margin: 6px 0;
  font-size: 14px;
}

.markdown-body :deep(ul),
.markdown-body :deep(ol) {
  padding-left: 1.5em;
  margin: 6px 0;
}

.markdown-body :deep(li) {
  margin: 3px 0;
  font-size: 14px;
}

.markdown-body :deep(strong) {
  font-weight: 600;
}

.markdown-body :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: 8px 0;
  font-size: 13px;
}

.markdown-body :deep(th),
.markdown-body :deep(td) {
  border: 1px solid var(--line);
  padding: 6px 10px;
  text-align: left;
}

.markdown-body :deep(th) {
  background: var(--bg2);
  font-weight: 600;
}

.markdown-body :deep(code) {
  background: var(--bg2);
  padding: 1px 4px;
  border-radius: 3px;
  font-size: 13px;
}

.markdown-body :deep(hr) {
  border: none;
  border-top: 1px solid var(--line);
  margin: 12px 0;
}

.markdown-body :deep(blockquote) {
  border-left: 3px solid var(--blue-dim);
  padding-left: 12px;
  margin: 8px 0;
  color: var(--sub);
}

/* 占位 */
.detail-placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  min-height: 200px;
  color: var(--muted);
  font-size: 14px;
}

/* 空状态 */
.no-results {
  text-align: center;
  padding: 2rem;
  color: var(--sub);
}
</style>
