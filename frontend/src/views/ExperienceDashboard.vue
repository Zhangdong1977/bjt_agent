<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { feedbackApi } from '@/api/client'
import type { FeedbackResponse, FeedbackSummary } from '@/types'

const loading = ref(true)
const summary = ref<FeedbackSummary | null>(null)
const recentFeedback = ref<FeedbackResponse[]>([])
const error = ref<string | null>(null)

// Use a placeholder project ID — in production this would come from route or global state
// The dashboard aggregates across all projects the user has access to
const projectId = ref('')

const totalFeedback = computed(() => summary.value?.total_feedback ?? 0)
const agreementRate = computed(() =>
  summary.value ? `${(summary.value.agreement_rate * 100).toFixed(1)}%` : '-',
)
const topContradicted = computed(() => summary.value?.top_contradicted_rules ?? [])

const typeLabels: Record<string, string> = {
  confirm: '同意',
  contradict: '反对',
  refine: '修正',
}

const statusLabels: Record<string, string> = {
  pending: '待审核',
  accepted: '已接受',
  rejected: '已拒绝',
  superseded: '已覆盖',
}

onMounted(async () => {
  // Dashboard requires a project context for now.
  // TODO: Add global dashboard endpoint that aggregates across all projects.
  // For now, show instructions when no project is selected.
  loading.value = false
})

async function loadDashboard(pid: string) {
  loading.value = true
  error.value = null
  projectId.value = pid
  try {
    const [summaryData, feedbackData] = await Promise.all([
      feedbackApi.getSummary(pid),
      feedbackApi.getMyFeedback(pid, 20),
    ])
    summary.value = summaryData
    recentFeedback.value = feedbackData
  } catch (err: any) {
    error.value = err?.response?.data?.detail || '加载失败'
  } finally {
    loading.value = false
  }
}

function formatDate(dateStr: string): string {
  return new Date(dateStr).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}
</script>

<template>
  <div class="experience-dashboard">
    <div class="dashboard-header">
      <h2>经验仪表盘</h2>
      <p class="dashboard-desc">审查经验自学习系统的运行状态与反馈统计</p>
    </div>

    <!-- No project selected state -->
    <div v-if="!projectId && !loading" class="dashboard-empty">
      <p>请输入项目 ID 查看经验仪表盘数据</p>
      <div class="project-input">
        <input
          v-model="projectId"
          class="input-field"
          placeholder="输入项目 ID"
          @keyup.enter="loadDashboard(projectId)"
        />
        <button class="load-btn" @click="loadDashboard(projectId)">加载</button>
      </div>
    </div>

    <!-- Loading -->
    <div v-if="loading" class="dashboard-loading">加载中...</div>

    <!-- Error -->
    <div v-if="error" class="dashboard-error">{{ error }}</div>

    <!-- Dashboard content -->
    <template v-if="summary && !loading">
      <!-- KPI cards -->
      <div class="kpi-grid">
        <div class="kpi-card">
          <div class="kpi-value">{{ summary.by_type.confirm ?? 0 }}</div>
          <div class="kpi-label">确认反馈</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-value kpi-red">{{ summary.by_type.contradict ?? 0 }}</div>
          <div class="kpi-label">反对反馈</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-value kpi-amber">{{ summary.by_type.refine ?? 0 }}</div>
          <div class="kpi-label">修正反馈</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-value kpi-blue">{{ agreementRate }}</div>
          <div class="kpi-label">用户同意率</div>
        </div>
      </div>

      <!-- Feedback distribution + top contradicted -->
      <div class="charts-row">
        <div class="chart-card">
          <h3 class="chart-title">反馈分布</h3>
          <div class="distribution-bars">
            <div v-for="(count, type) in summary.by_type" :key="type" class="dist-item">
              <span class="dist-label">{{ typeLabels[type] || type }}</span>
              <div class="dist-bar-bg">
                <div
                  :class="['dist-bar-fill', type]"
                  :style="{ width: `${totalFeedback > 0 ? (count / totalFeedback) * 100 : 0}%` }"
                ></div>
              </div>
              <span class="dist-count">{{ count }}</span>
            </div>
          </div>
        </div>

        <div class="chart-card">
          <h3 class="chart-title">反对最多的规则文档</h3>
          <div v-if="topContradicted.length === 0" class="empty-hint">暂无反对记录</div>
          <div v-for="item in topContradicted" :key="item.rule_doc_name" class="contradict-item">
            <span class="contradict-name">{{ item.rule_doc_name?.replace('.md', '') }}</span>
            <span class="contradict-count">{{ item.count }} 次</span>
          </div>
        </div>
      </div>

      <!-- Recent feedback -->
      <div class="chart-card">
        <h3 class="chart-title">最近反馈</h3>
        <div v-if="recentFeedback.length === 0" class="empty-hint">暂无反馈记录</div>
        <div class="feedback-table" v-else>
          <div class="fb-table-header">
            <span>时间</span>
            <span>类型</span>
            <span>状态</span>
            <span>说明</span>
          </div>
          <div v-for="fb in recentFeedback" :key="fb.id" class="fb-table-row">
            <span class="fb-time">{{ formatDate(fb.created_at) }}</span>
            <span :class="['fb-type-badge', fb.feedback_type]">
              {{ typeLabels[fb.feedback_type] || fb.feedback_type }}
            </span>
            <span :class="['fb-status', fb.status]">
              {{ statusLabels[fb.status] || fb.status }}
            </span>
            <span class="fb-comment">{{ fb.comment || fb.contradict_reason || '-' }}</span>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.experience-dashboard {
  max-width: 960px;
  margin: 0 auto;
  padding: 24px 20px;
}

.dashboard-header {
  margin-bottom: 24px;
}

.dashboard-header h2 {
  color: var(--text);
  font-size: 20px;
  font-weight: 600;
  margin: 0 0 4px;
}

.dashboard-desc {
  color: var(--muted);
  font-size: 13px;
  margin: 0;
}

/* KPI cards */
.kpi-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 8px;
  margin-bottom: 20px;
}

.kpi-card {
  background: var(--bg1);
  border: 1px solid var(--line);
  border-radius: var(--r);
  padding: 16px;
  text-align: center;
}

.kpi-value {
  font-size: 28px;
  font-weight: 700;
  color: var(--text);
  line-height: 1;
}

.kpi-red { color: var(--red); }
.kpi-amber { color: var(--amber); }
.kpi-blue { color: var(--blue); }

.kpi-label {
  font-size: 11px;
  color: var(--muted);
  margin-top: 6px;
}

/* Charts row */
.charts-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  margin-bottom: 20px;
}

.chart-card {
  background: var(--bg1);
  border: 1px solid var(--line);
  border-radius: var(--r);
  padding: 16px;
}

.chart-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
  margin: 0 0 12px;
}

/* Distribution bars */
.distribution-bars {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.dist-item {
  display: grid;
  grid-template-columns: 48px 1fr 36px;
  align-items: center;
  gap: 8px;
}

.dist-label {
  font-size: 12px;
  color: var(--sub);
}

.dist-bar-bg {
  height: 8px;
  background: var(--bg2);
  border-radius: 4px;
  overflow: hidden;
}

.dist-bar-fill {
  height: 100%;
  border-radius: 4px;
  transition: width 0.3s;
}

.dist-bar-fill.confirm { background: var(--green); }
.dist-bar-fill.contradict { background: var(--red); }
.dist-bar-fill.refine { background: var(--amber); }

.dist-count {
  font-size: 12px;
  font-weight: 600;
  color: var(--text);
  text-align: right;
}

/* Contradicted rules */
.contradict-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 0;
  border-bottom: 1px solid var(--line);
}

.contradict-item:last-child {
  border-bottom: none;
}

.contradict-name {
  font-size: 12px;
  color: var(--text);
}

.contradict-count {
  font-size: 12px;
  font-weight: 600;
  color: var(--red);
}

/* Recent feedback table */
.feedback-table {
  display: flex;
  flex-direction: column;
}

.fb-table-header {
  display: grid;
  grid-template-columns: 100px 60px 60px 1fr;
  gap: 8px;
  padding: 6px 0;
  border-bottom: 1px solid var(--line);
  font-size: 10px;
  font-weight: 600;
  color: var(--muted);
  text-transform: uppercase;
}

.fb-table-row {
  display: grid;
  grid-template-columns: 100px 60px 60px 1fr;
  gap: 8px;
  padding: 6px 0;
  border-bottom: 1px solid var(--line);
  font-size: 12px;
  align-items: center;
}

.fb-table-row:last-child {
  border-bottom: none;
}

.fb-time {
  color: var(--muted);
}

.fb-type-badge {
  display: inline-block;
  padding: 1px 6px;
  border-radius: 3px;
  font-size: 10px;
  font-weight: 500;
  text-align: center;
}

.fb-type-badge.confirm {
  background: var(--green-bg);
  color: var(--green);
}

.fb-type-badge.contradict {
  background: var(--red-bg);
  color: var(--red);
}

.fb-type-badge.refine {
  background: var(--amber-bg);
  color: var(--amber);
}

.fb-status {
  font-size: 11px;
}

.fb-status.accepted { color: var(--green); }
.fb-status.pending { color: var(--amber); }
.fb-status.rejected { color: var(--red); }

.fb-comment {
  color: var(--sub);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* Empty / loading states */
.empty-hint {
  color: var(--muted);
  font-size: 12px;
  font-style: italic;
}

.dashboard-loading {
  text-align: center;
  padding: 48px;
  color: var(--muted);
}

.dashboard-error {
  background: var(--red-bg);
  color: var(--red);
  padding: 10px 14px;
  border-radius: var(--r);
  font-size: 13px;
}

.dashboard-empty {
  text-align: center;
  padding: 48px;
  color: var(--muted);
}

.project-input {
  display: flex;
  gap: 8px;
  justify-content: center;
  margin-top: 12px;
}

.input-field {
  padding: 6px 12px;
  border: 1px solid var(--line);
  border-radius: var(--r);
  background: var(--bg2);
  color: var(--text);
  font-size: 13px;
  min-width: 240px;
}

.load-btn {
  padding: 6px 16px;
  background: var(--blue);
  color: var(--white);
  border: none;
  border-radius: var(--r);
  cursor: pointer;
  font-size: 13px;
  font-weight: 500;
}

.load-btn:hover {
  filter: brightness(1.1);
}

@media (max-width: 767px) {
  .kpi-grid {
    grid-template-columns: repeat(2, 1fr);
  }

  .charts-row {
    grid-template-columns: 1fr;
  }

  .fb-table-header,
  .fb-table-row {
    grid-template-columns: 80px 50px 50px 1fr;
  }
}
</style>
