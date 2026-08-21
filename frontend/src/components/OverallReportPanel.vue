<script setup lang="ts">
import type { OverallReport, OverallRiskSectionEntry } from '@/types'

// 总体报告面板：评级徽标 + 风险等级分布 + 严重/重要/一般三节 + 评分项摘要。
// 纯展示组件；数据加载/补生成/轮询逻辑由父组件（ReviewResultsArea）负责。
const props = defineProps<{
  report: OverallReport | null
  // generating = 检查已完成但报告还在生成（任务 running 或补生成进行中）
  generating: boolean
  canRegenerate: boolean
  regenerating: boolean
}>()

const emit = defineEmits<{
  (e: 'regenerate'): void
}>()

// 严重红/重要橙/一般绿（与 PDF 导出配色一致）
const SEV_META: Record<
  'critical' | 'major' | 'minor',
  { title: string; color: string; bg: string }
> = {
  critical: { title: '严重风险', color: '#f5222d', bg: '#fff1f0' },
  major: { title: '重要风险', color: '#fa8c16', bg: '#fff7e6' },
  minor: { title: '一般风险', color: '#52c41a', bg: '#f6ffed' },
}

const LEVEL_META: Record<string, { color: string; bg: string }> = {
  高: { color: '#f5222d', bg: '#fff1f0' },
  中: { color: '#fa8c16', bg: '#fff7e6' },
  低: { color: '#52c41a', bg: '#f6ffed' },
}

const SECTIONS: Array<'critical' | 'major' | 'minor'> = ['critical', 'major', 'minor']

function sectionEntries(sev: 'critical' | 'major' | 'minor'): OverallRiskSectionEntry[] {
  return props.report?.risk_sections?.[sev] ?? []
}

function fmtScore(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—'
  return Number.isInteger(v) ? String(v) : String(v)
}
</script>

<template>
  <div class="overall-panel">
    <!-- 有报告：正常渲染 -->
    <template v-if="report">
      <div class="panel-head">
        <span class="panel-title">总体报告</span>
        <span
          v-if="report.degraded"
          class="degraded-tag"
          title="精简描述生成失败，已降级为原始结论摘录"
        >降级版</span>
      </div>

      <!-- 评级 + 分布 -->
      <div class="overview-row">
        <div
          class="level-badge"
          :style="{
            color: LEVEL_META[report.rejection_risk?.level]?.color || '#222',
            background: LEVEL_META[report.rejection_risk?.level]?.bg || '#f5f5f5',
          }"
        >
          废标风险评级：{{ report.rejection_risk?.level ?? '—' }}
        </div>
        <div class="dist-chips">
          <span
            v-for="(meta, sev) in SEV_META"
            :key="sev"
            class="dist-chip"
            :style="{ color: meta.color, background: meta.bg }"
          >
            {{ meta.title.replace('风险', '') }} {{ report.summary?.severity_dist?.[sev] ?? 0 }} 项
          </span>
        </div>
      </div>
      <p v-if="report.rejection_risk?.reason" class="level-reason">
        {{ report.rejection_risk.reason }}
      </p>
      <p v-if="report.summary?.failed_categories?.length" class="failed-note">
        以下大类子检查未成功，结果可能不完整：{{ report.summary.failed_categories.join('、') }}
      </p>

      <!-- 三节风险 -->
      <div v-for="sev in SECTIONS" :key="sev" class="risk-section">
        <div class="section-title" :style="{ color: SEV_META[sev].color }">
          <span class="section-bar" :style="{ background: SEV_META[sev].color }"></span>
          {{ SEV_META[sev].title }}
        </div>
        <template v-if="sectionEntries(sev).length">
          <div
            v-for="entry in sectionEntries(sev)"
            :key="entry.rule_doc_code + entry.rule_doc"
            class="risk-entry"
            :style="{ borderLeftColor: SEV_META[sev].color, background: SEV_META[sev].bg }"
          >
            <div class="entry-head">
              <span class="entry-doc" :style="{ color: SEV_META[sev].color }">
                {{ entry.rule_doc }}
              </span>
              <span class="entry-count">{{ entry.count }} 项</span>
              <span
                v-if="sev === 'critical' && entry.rejection_related"
                class="rejection-tag"
              >涉及废标条款</span>
            </div>
            <p class="entry-summary">{{ entry.summary }}</p>
          </div>
        </template>
        <div v-else class="section-empty">无</div>
      </div>

      <!-- 评分项得分摘要 -->
      <div v-if="report.score_items?.length" class="risk-section">
        <div class="section-title section-score">
          <span class="section-bar bar-score"></span>
          评分项得分摘要
        </div>
        <table class="score-table">
          <thead>
            <tr>
              <th>评分项</th>
              <th>满分</th>
              <th>预估得分</th>
              <th>说明</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in report.score_items" :key="item.code + item.name">
              <td>{{ item.name }}</td>
              <td class="num">{{ fmtScore(item.full_score) }}</td>
              <td class="num">{{ fmtScore(item.estimated_score) }}</td>
              <td>{{ item.note || '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>

    <!-- 报告生成中 -->
    <div v-else-if="generating" class="pending-box">
      <span class="spin"></span>
      检查已完成，正在汇总生成总体报告…
    </div>

    <!-- 无报告且可补生成（历史任务） -->
    <div v-else-if="canRegenerate" class="pending-box">
      <span>本任务尚未生成总体报告</span>
      <button class="gen-btn" :disabled="regenerating" @click="emit('regenerate')">
        {{ regenerating ? '生成中…' : '生成总体报告' }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.overall-panel {
  border: 1px solid var(--line);
  border-radius: var(--r);
  background: var(--bg1);
  padding: 16px;
  margin-bottom: 18px;
}

.panel-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}

.panel-title {
  font-size: 16px;
  font-weight: 700;
  color: var(--text);
}

.degraded-tag {
  font-size: 11px;
  color: var(--muted);
  border: 1px solid var(--line);
  border-radius: 4px;
  padding: 1px 6px;
}

.overview-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
}

.level-badge {
  font-size: 14px;
  font-weight: 700;
  padding: 4px 12px;
  border-radius: 6px;
}

.dist-chips {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.dist-chip {
  font-size: 12px;
  font-weight: 600;
  padding: 3px 10px;
  border-radius: 12px;
}

.level-reason {
  margin: 10px 0 0;
  font-size: 13px;
  color: var(--sub, #666);
  line-height: 1.6;
}

.failed-note {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--muted);
}

.risk-section {
  margin-top: 16px;
}

.section-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  font-weight: 700;
  margin-bottom: 8px;
}

.section-bar {
  width: 4px;
  height: 14px;
  border-radius: 2px;
  flex-shrink: 0;
}

.section-score {
  color: var(--text);
}

.bar-score {
  background: var(--blue-dim, #2090f0);
}

.risk-entry {
  border-left: 3px solid;
  border-radius: 6px;
  padding: 8px 12px;
  margin-bottom: 8px;
}

.entry-head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.entry-doc {
  font-size: 13px;
  font-weight: 700;
}

.entry-count {
  font-size: 12px;
  color: var(--muted);
}

.rejection-tag {
  font-size: 11px;
  font-weight: 600;
  color: #f5222d;
  border: 1px solid #f5222d;
  border-radius: 4px;
  padding: 0 6px;
}

.entry-summary {
  margin: 4px 0 0;
  font-size: 13px;
  color: var(--text);
  line-height: 1.7;
}

.section-empty {
  font-size: 13px;
  color: var(--muted);
  padding: 2px 0 0 12px;
}

.score-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.score-table th,
.score-table td {
  border: 1px solid var(--line);
  padding: 6px 10px;
  text-align: left;
  vertical-align: top;
}

.score-table th {
  background: var(--bg2);
  font-weight: 600;
}

.score-table td.num {
  text-align: center;
  white-space: nowrap;
}

.pending-box {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 13px;
  color: var(--sub, #666);
}

.gen-btn {
  font-size: 13px;
  color: #fff;
  background: var(--blue-dim, #2090f0);
  border: none;
  border-radius: 6px;
  padding: 5px 14px;
  cursor: pointer;
}

.gen-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.spin {
  width: 14px;
  height: 14px;
  border: 2px solid var(--line);
  border-top-color: var(--blue-dim, #2090f0);
  border-radius: 50%;
  animation: overall-spin 0.9s linear infinite;
  flex-shrink: 0;
}

@keyframes overall-spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
