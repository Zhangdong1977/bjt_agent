<script setup lang="ts">
import { computed, ref } from 'vue'
import type { ReviewResponse, ReviewResult } from '@/types'
import { renderMarkdown } from '@/utils/markdown'

const props = defineProps<{
  reviewResults: ReviewResponse | null | undefined
}>()

const summary = computed(() => props.reviewResults?.summary ?? {
  category_count: 0,
  check_item_count: 0,
  risk_item_count: 0,
})

const findings = computed<ReviewResult[]>(() => props.reviewResults?.findings ?? [])

const hasFindings = computed(() => findings.value.length > 0)

const selectedKey = ref<string | null>(null)

interface FindingGroup {
  key: string
  label: string
  findings: ReviewResult[]
  nonCompliantCount: number
  compliantCount: number
  topSeverity: 'critical' | 'major' | 'minor'
}

const nonCompliantGroups = computed<FindingGroup[]>(() => {
  const map = new Map<string, FindingGroup>()

  for (const f of findings.value) {
    const key = f.rule_doc_name || f.requirement_key
    if (!map.has(key)) {
      map.set(key, {
        key,
        label: f.check_item_name || f.rule_doc_name || f.requirement_content || f.requirement_key,
        findings: [],
        nonCompliantCount: 0,
        compliantCount: 0,
        topSeverity: 'minor',
      })
    }
    const g = map.get(key)!
    g.findings.push(f)
    if (f.is_compliant) {
      g.compliantCount++
    } else {
      g.nonCompliantCount++
      if (f.severity === 'critical') g.topSeverity = 'critical'
      else if (f.severity === 'major' && g.topSeverity !== 'critical') g.topSeverity = 'major'
    }
  }

  // Only keep groups that have at least one non-compliant finding
  const filtered = [...map.values()].filter(g => g.nonCompliantCount > 0)

  // Sort: severity order (critical > major > minor), then by non-compliant count descending
  const severityOrder = { critical: 0, major: 1, minor: 2 }
  filtered.sort((a, b) => {
    const sevDiff = severityOrder[a.topSeverity] - severityOrder[b.topSeverity]
    if (sevDiff !== 0) return sevDiff
    return b.nonCompliantCount - a.nonCompliantCount
  })

  return filtered
})

const selectedGroup = computed<FindingGroup | null>(() => {
  if (!selectedKey.value) return null
  return nonCompliantGroups.value.find(g => g.key === selectedKey.value) ?? null
})

function selectGroup(key: string) {
  selectedKey.value = key
}

function getSeverityLabel(severity: string): string {
  switch (severity) {
    case 'critical': return '严重'
    case 'major': return '主要'
    case 'minor': return '次要'
    default: return severity
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
      <!-- 左面板：风险项清单 -->
      <div class="left-panel">
        <div class="panel-header">
          <span>风险项清单</span>
          <span class="count-badge">{{ nonCompliantGroups.length }}</span>
        </div>
        <div class="group-list">
          <div
            v-for="group in nonCompliantGroups"
            :key="group.key"
            :class="['group-item', { selected: selectedKey === group.key }]"
            @click="selectGroup(group.key)"
          >
            <div class="group-item-left">
              <span :class="['severity-dot', getSeverityColorClass(group.topSeverity)]"></span>
              <span class="group-label">{{ group.label }}</span>
            </div>
            <div class="group-item-right">
              <span class="group-count risk">{{ group.nonCompliantCount }}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- 右面板：详情 -->
      <div class="right-panel">
        <template v-if="selectedGroup">
          <div class="detail-header">
            <h3 class="detail-title">{{ selectedGroup.label }}</h3>
            <div class="detail-stats">
              <span class="detail-stat">共 {{ selectedGroup.findings.length }} 项</span>
            </div>
          </div>
          <div class="detail-list">
            <div
              v-for="finding in selectedGroup.findings"
              :key="finding.id"
              :class="['detail-card', { 'card-risk': !finding.is_compliant }]"
            >
              <div class="card-header">
                <span :class="['severity-tag', getSeverityColorClass(finding.severity)]">
                  {{ getSeverityLabel(finding.severity) }}
                </span>
                <span :class="['status-tag', finding.is_compliant ? 'compliant' : 'risk']">
                  {{ finding.is_compliant ? '合规' : '风险项' }}
                </span>
              </div>
              <div class="card-body">
                <p class="requirement"><strong>要求:</strong> <span class="markdown-content" v-html="renderMarkdown(finding.requirement_content)"></span></p>
                <p class="bid-content"><strong>应标内容:</strong> <span class="markdown-content" v-html="renderMarkdown(finding.bid_content)"></span></p>
                <p v-if="finding.explanation" class="explanation"><span class="markdown-content" v-html="renderMarkdown(finding.explanation)"></span></p>
                <p v-if="finding.suggestion && !finding.is_compliant" class="suggestion">
                  <strong>建议:</strong> <span class="markdown-content" v-html="renderMarkdown(finding.suggestion)"></span>
                </p>
              </div>
            </div>
          </div>
        </template>
        <div v-else class="detail-placeholder">
          ← 选择左侧风险项查看详情
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
.group-count.total { color: var(--muted); }
.group-sep { color: var(--muted); font-size: 11px; }

/* 右面板 */
.right-panel {
  overflow-y: auto;
  max-height: 500px;
}

.detail-header {
  padding: 12px 16px;
  border-bottom: 1px solid var(--line);
}

.detail-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--text);
  margin: 0 0 6px;
}

.detail-stats {
  display: flex;
  gap: 12px;
}

.detail-stat {
  font-size: 12px;
  color: var(--muted);
}

.detail-stat.risk {
  color: var(--red);
  font-weight: 500;
}

.detail-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 12px 16px;
}

.detail-card {
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 12px;
}

.detail-card.card-risk {
  border-color: var(--red-dim);
  background: var(--red-bg);
}

.card-header {
  display: flex;
  gap: 6px;
  margin-bottom: 8px;
}

.severity-tag {
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
}

.severity-tag.sev-critical { background: var(--red-dim); color: var(--red); }
.severity-tag.sev-major { background: var(--amber-dim, #fff3cd); color: var(--amber); }
.severity-tag.sev-minor { background: var(--amber-dim, #fff3cd); color: var(--amber); }

.status-tag {
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
}

.status-tag.compliant { background: var(--green-dim, #d4edda); color: var(--green); }
.status-tag.risk { background: var(--red-dim); color: var(--red); }

.card-body p {
  margin: 4px 0;
  font-size: 13px;
  color: var(--text);
}

.card-body .explanation {
  color: var(--sub);
  font-style: italic;
}

.card-body .suggestion {
  color: var(--blue);
}

/* Markdown 渲染样式 */
.card-body :deep(.markdown-content) {
  line-height: 1.6;
  word-break: break-word;
}

.card-body :deep(.markdown-content p) {
  margin: 4px 0;
}

.card-body :deep(.markdown-content ul),
.card-body :deep(.markdown-content ol) {
  padding-left: 1.2em;
  margin: 4px 0;
}

.card-body :deep(.markdown-content li) {
  margin: 2px 0;
}

.card-body :deep(.markdown-content strong) {
  font-weight: 600;
}

.card-body :deep(.markdown-content table) {
  width: 100%;
  border-collapse: collapse;
  margin: 6px 0;
  font-size: 12px;
}

.card-body :deep(.markdown-content th),
.card-body :deep(.markdown-content td) {
  border: 1px solid var(--line);
  padding: 4px 8px;
  text-align: left;
}

.card-body :deep(.markdown-content th) {
  background: var(--bg2);
  font-weight: 600;
}

.card-body :deep(.markdown-content code) {
  background: var(--bg2);
  padding: 1px 4px;
  border-radius: 3px;
  font-size: 12px;
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
