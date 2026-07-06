<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import { feedbackApi } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import type { FeedbackResponse, FeedbackSummary, ProjectFeedbackSummary } from '@/types'
import FeedbackReviewCard from '@/components/feedback/FeedbackReviewCard.vue'

const authStore = useAuthStore()
const router = useRouter()
const loading = ref(true)
const summary = ref<FeedbackSummary | null>(null)
const recentFeedback = ref<FeedbackResponse[]>([])
const pendingFeedback = ref<FeedbackResponse[]>([])
const allFeedback = ref<FeedbackResponse[]>([])
const batchReviewing = ref(false)
const error = ref<string | null>(null)
const activeTab = ref<'overview' | 'pending' | 'all'>('overview')
const filterStatus = ref('')
const filterType = ref('')

const projectId = ref('')

// Project list state
const projectList = ref<ProjectFeedbackSummary[]>([])
const projectListLoading = ref(true)

// Pagination
const pagination = ref({ limit: 20, offset: 0, total: 0 })
const currentPage = computed(() =>
  Math.floor(pagination.value.offset / pagination.value.limit) + 1,
)
const totalPages = computed(() =>
  Math.ceil(pagination.value.total / pagination.value.limit) || 1,
)

// Visible page numbers for pagination control
const visiblePages = computed(() => {
  const total = totalPages.value
  const current = currentPage.value
  if (total <= 7) {
    return Array.from({ length: total }, (_, i) => i + 1)
  }
  const pages: number[] = [1]
  const start = Math.max(2, current - 1)
  const end = Math.min(total - 1, current + 1)
  if (start > 2) pages.push(-1) // ellipsis
  for (let i = start; i <= end; i++) pages.push(i)
  if (end < total - 1) pages.push(-1) // ellipsis
  pages.push(total)
  return pages
})

// Search / filter
const filterTimeRange = ref('')
const filterStartDate = ref('')
const filterEndDate = ref('')
const filterUsername = ref('')
const filterProjectName = ref('')
const filterProjectId = ref('')

const isInteriorUser = computed(() => authStore.isInteriorUser)

const pendingCount = computed(() => {
  if (summary.value?.by_status) {
    return summary.value.by_status.pending ?? 0
  }
  return pendingFeedback.value.length
})

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
  loading.value = false
  if (isInteriorUser.value) {
    await loadProjectList()
  }
})

async function loadProjectList() {
  projectListLoading.value = true
  try {
    const result = await feedbackApi.getProjectsSummary({
      limit: pagination.value.limit,
      offset: pagination.value.offset,
      time_range: filterTimeRange.value || undefined,
      start_date: filterStartDate.value || undefined,
      end_date: filterEndDate.value || undefined,
      username: filterUsername.value || undefined,
      project_name: filterProjectName.value || undefined,
      project_id: filterProjectId.value || undefined,
    })
    projectList.value = result.items
    pagination.value.total = result.total
  } catch {
    // Error handled silently
  } finally {
    projectListLoading.value = false
  }
}

function goToPage(page: number) {
  pagination.value.offset = (page - 1) * pagination.value.limit
  loadProjectList()
}

function applyFilters() {
  pagination.value.offset = 0
  loadProjectList()
}

function resetFilters() {
  filterTimeRange.value = ''
  filterStartDate.value = ''
  filterEndDate.value = ''
  filterUsername.value = ''
  filterProjectName.value = ''
  filterProjectId.value = ''
  pagination.value.offset = 0
  loadProjectList()
}

async function loadDashboard(pid: string) {
  loading.value = true
  error.value = null
  projectId.value = pid
  try {
    const requests: Promise<any>[] = [
      feedbackApi.getSummary(pid),
      feedbackApi.getMyFeedback(pid, 20),
    ]
    if (isInteriorUser.value) {
      requests.push(feedbackApi.getPendingFeedback(pid))
    }
    const results = await Promise.all(requests)
    summary.value = results[0]
    recentFeedback.value = results[1]
    if (results[2]) {
      pendingFeedback.value = results[2]
    }
    if (pendingCount.value > 0) {
      activeTab.value = 'pending'
    }
  } catch (err: any) {
    error.value = err?.response?.data?.detail || '加载失败'
  } finally {
    loading.value = false
  }
}

function goToProjectDetail(proj: ProjectFeedbackSummary) {
  router.push({
    name: 'review-results',
    params: { id: proj.project_id },
    query: { from: 'experience' },
  })
}

function clearProject() {
  projectId.value = ''
  summary.value = null
  recentFeedback.value = []
  pendingFeedback.value = []
  allFeedback.value = []
  activeTab.value = 'overview'
  error.value = null
  loadProjectList()
}

async function loadAllFeedback() {
  if (!projectId.value) return
  try {
    const params: any = { limit: 200 }
    if (filterStatus.value) params.status = filterStatus.value
    if (filterType.value) params.feedback_type = filterType.value
    allFeedback.value = await feedbackApi.getAllFeedback(projectId.value, params)
  } catch {
    // Error handled silently
  }
}

async function handleReviewed(feedback: FeedbackResponse) {
  pendingFeedback.value = pendingFeedback.value.filter(f => f.id !== feedback.id)
  if (projectId.value) {
    try {
      summary.value = await feedbackApi.getSummary(projectId.value)
    } catch {
      // Refresh silently
    }
  }
}

async function handleBatchReview(action: 'accept' | 'reject') {
  if (!projectId.value || batchReviewing.value) return
  batchReviewing.value = true
  try {
    await feedbackApi.batchReviewFeedback(projectId.value, action)
    pendingFeedback.value = []
    summary.value = await feedbackApi.getSummary(projectId.value)
  } catch {
    // Error handled silently
  } finally {
    batchReviewing.value = false
  }
}

function switchTab(tab: 'overview' | 'pending' | 'all') {
  activeTab.value = tab
  if (tab === 'all' && allFeedback.value.length === 0) {
    loadAllFeedback()
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
      <h2>标书复盘</h2>
      <p class="dashboard-desc">审查经验自学习系统的运行状态与反馈审核</p>
    </div>

    <!-- Project list view (interior users, primary view) -->
    <template v-if="!projectId && !loading && isInteriorUser">
      <div class="project-list-card">
        <!-- Filter bar -->
        <div class="project-filter-bar">
          <select v-model="filterTimeRange" class="filter-select">
            <option value="">全部时间</option>
            <option value="today">今天</option>
            <option value="7d">近7天</option>
            <option value="30d">近30天</option>
            <option value="custom">自定义</option>
          </select>
          <template v-if="filterTimeRange === 'custom'">
            <input
              v-model="filterStartDate"
              type="date"
              class="filter-input date-input"
              placeholder="开始日期"
            />
            <input
              v-model="filterEndDate"
              type="date"
              class="filter-input date-input"
              placeholder="结束日期"
            />
          </template>
          <input
            v-model="filterUsername"
            class="filter-input"
            placeholder="用户名"
            @keyup.enter="applyFilters"
          />
          <input
            v-model="filterProjectName"
            class="filter-input"
            placeholder="项目名称"
            @keyup.enter="applyFilters"
          />
          <input
            v-model="filterProjectId"
            class="filter-input"
            placeholder="项目 ID"
            @keyup.enter="applyFilters"
          />
          <button class="filter-btn" @click="applyFilters">搜索</button>
          <button class="filter-btn filter-btn-secondary" @click="resetFilters">重置</button>
        </div>

        <div v-if="projectListLoading" class="dashboard-loading">加载中...</div>
        <template v-else>
          <div v-if="projectList.length === 0" class="empty-state">
            <div class="empty-icon">📋</div>
            <div class="empty-text">暂无项目</div>
          </div>
          <template v-else>
            <table class="project-table">
              <thead>
                <tr>
                  <th>用户</th>
                  <th>项目名称</th>
                  <th>项目 ID</th>
                  <th class="num-col">反馈总数</th>
                  <th class="num-col">已审核</th>
                  <th class="num-col">待审核</th>
                  <th>创建时间</th>
                  <th>状态</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="proj in projectList"
                  :key="proj.project_id"
                  :class="['project-row', { 'project-row-deleted': proj.is_deleted }]"
                >
                  <td class="username-cell">{{ proj.username }}</td>
                  <td class="project-name-cell">{{ proj.project_name }}</td>
                  <td class="id-cell">{{ proj.project_id.substring(0, 8) }}…</td>
                  <td class="num-col">{{ proj.total_feedback }}</td>
                  <td class="num-col">{{ proj.reviewed_feedback }}</td>
                  <td class="num-col">
                    <span v-if="proj.unreviewed_feedback > 0" class="pending-badge">
                      {{ proj.unreviewed_feedback }}
                    </span>
                    <span v-else class="num-zero">0</span>
                  </td>
                  <td class="time-cell">{{ formatDate(proj.created_at) }}</td>
                  <td class="status-cell">
                    <span v-if="proj.is_deleted" class="status-badge status-deleted">已删除</span>
                    <span v-else-if="proj.review_completed" class="status-badge status-completed">审核完成</span>
                    <span v-else-if="proj.has_review" class="status-badge status-running">审核未完成</span>
                    <span v-else-if="proj.has_documents" class="status-badge status-pending">待审核</span>
                    <span v-else class="status-badge status-empty">未上传文档</span>
                  </td>
                  <td class="action-cell">
                    <button
                      class="row-action-btn detail-btn"
                      @click.stop="goToProjectDetail(proj)"
                    >查看详情</button>
                    <button
                      class="row-action-btn feedback-btn"
                      @click.stop="loadDashboard(proj.project_id)"
                    >反馈处理</button>
                  </td>
                </tr>
              </tbody>
            </table>

            <!-- Pagination -->
            <div v-if="pagination.total > 0" class="pagination-bar">
              <span class="pagination-info">
                共 {{ pagination.total }} 条 &nbsp; 第 {{ currentPage }} / {{ totalPages }} 页
              </span>
              <div class="pagination-controls">
                <button
                  class="page-btn"
                  :disabled="currentPage <= 1"
                  @click="goToPage(currentPage - 1)"
                >上一页</button>
                <template v-for="p in visiblePages" :key="p">
                  <span v-if="p === -1" class="page-ellipsis">…</span>
                  <button
                    v-else
                    :class="['page-btn', { active: p === currentPage }]"
                    @click="goToPage(p)"
                  >{{ p }}</button>
                </template>
                <button
                  class="page-btn"
                  :disabled="currentPage >= totalPages"
                  @click="goToPage(currentPage + 1)"
                >下一页</button>
              </div>
            </div>
          </template>
        </template>
      </div>
    </template>

    <!-- No project selected state (non-interior fallback) -->
    <div v-if="!projectId && !loading && !isInteriorUser" class="dashboard-empty">
      <p>请联系管理员查看标书复盘数据</p>
    </div>

    <!-- Back to project list -->
    <div v-if="projectId && !loading" class="back-nav">
      <a class="back-link" @click="clearProject">← 返回项目列表</a>
    </div>

    <!-- Loading -->
    <div v-if="loading" class="dashboard-loading">加载中...</div>

    <!-- Error -->
    <div v-if="error" class="dashboard-error">{{ error }}</div>

    <!-- Dashboard content -->
    <template v-if="summary && !loading">
      <!-- Tab navigation -->
      <div class="tab-nav">
        <button
          :class="['tab-btn', { active: activeTab === 'overview' }]"
          @click="switchTab('overview')"
        >
          概览
        </button>
        <button
          v-if="isInteriorUser"
          :class="['tab-btn', { active: activeTab === 'pending' }]"
          @click="switchTab('pending')"
        >
          待审核
          <span v-if="pendingCount > 0" class="tab-badge">{{ pendingCount }}</span>
        </button>
        <button
          v-if="isInteriorUser"
          :class="['tab-btn', { active: activeTab === 'all' }]"
          @click="switchTab('all')"
        >
          全部反馈
        </button>
      </div>

      <!-- Overview tab -->
      <template v-if="activeTab === 'overview'">
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

      <!-- Pending review tab -->
      <template v-if="activeTab === 'pending' && isInteriorUser">
        <div class="section-card">
          <div class="section-header">
            <h3 class="section-title">待审核反馈</h3>
            <p class="section-desc">审核用户提交的反馈，接受的反馈将进入经验提取工作流</p>
          </div>

          <div v-if="pendingFeedback.length === 0" class="empty-state">
            <div class="empty-icon">✓</div>
            <div class="empty-text">所有反馈已审核完毕</div>
          </div>

          <template v-else>
            <div class="batch-action-bar">
              <span class="batch-info">共 {{ pendingFeedback.length }} 条待审核</span>
              <div class="batch-actions">
                <button
                  class="batch-btn batch-accept"
                  :disabled="batchReviewing"
                  @click="handleBatchReview('accept')"
                >
                  {{ batchReviewing ? '处理中...' : '全部接受' }}
                </button>
                <button
                  class="batch-btn batch-reject"
                  :disabled="batchReviewing"
                  @click="handleBatchReview('reject')"
                >
                  全部拒绝
                </button>
              </div>
            </div>

            <div class="review-list">
              <FeedbackReviewCard
                v-for="fb in pendingFeedback"
                :key="fb.id"
                :feedback="fb"
                :project-id="projectId"
                @reviewed="handleReviewed"
              />
            </div>
          </template>
        </div>
      </template>

      <!-- All feedback tab -->
      <template v-if="activeTab === 'all' && isInteriorUser">
        <div class="section-card">
          <div class="section-header">
            <h3 class="section-title">全部反馈</h3>
            <div class="filter-bar">
              <select v-model="filterStatus" class="filter-select" @change="loadAllFeedback()">
                <option value="">全部状态</option>
                <option value="pending">待审核</option>
                <option value="accepted">已接受</option>
                <option value="rejected">已拒绝</option>
              </select>
              <select v-model="filterType" class="filter-select" @change="loadAllFeedback()">
                <option value="">全部类型</option>
                <option value="confirm">同意</option>
                <option value="contradict">反对</option>
                <option value="refine">修正</option>
              </select>
            </div>
          </div>

          <div v-if="allFeedback.length === 0" class="empty-hint">暂无反馈记录</div>

          <div v-else class="review-list">
            <FeedbackReviewCard
              v-for="fb in allFeedback"
              :key="fb.id"
              :feedback="fb"
              :project-id="projectId"
              @reviewed="handleReviewed"
            />
          </div>
        </div>
      </template>
    </template>
  </div>
</template>

<style scoped>
.experience-dashboard {
  width: 100%;
  padding: 24px 32px;
  box-sizing: border-box;
}

.dashboard-header {
  margin-bottom: 28px;
}

.dashboard-header h2 {
  color: var(--text);
  font-size: 22px;
  font-weight: 600;
  margin: 0 0 4px;
}

.dashboard-desc {
  color: var(--muted);
  font-size: 13px;
  margin: 0;
}

/* Tab navigation */
.tab-nav {
  display: flex;
  gap: 2px;
  margin-bottom: 20px;
  border-bottom: 1px solid var(--line);
  padding-bottom: 0;
}

.tab-btn {
  padding: 8px 18px;
  background: transparent;
  color: var(--sub);
  border: none;
  border-bottom: 2px solid transparent;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s;
  display: flex;
  align-items: center;
  gap: 6px;
}

.tab-btn:hover {
  color: var(--text);
}

.tab-btn.active {
  color: var(--blue);
  border-bottom-color: var(--blue);
}

.tab-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  background: var(--red);
  color: var(--white);
  font-size: 10px;
  font-weight: 600;
  border-radius: 9px;
}

/* KPI cards */
.kpi-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
  margin-bottom: 24px;
}

.kpi-card {
  background: var(--bg1);
  border: 1px solid var(--line);
  border-radius: var(--r);
  padding: 20px 24px;
  text-align: center;
}

.kpi-value {
  font-size: 32px;
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
  gap: 16px;
  margin-bottom: 24px;
}

.chart-card {
  background: var(--bg1);
  border: 1px solid var(--line);
  border-radius: var(--r);
  padding: 20px;
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
  grid-template-columns: 120px 72px 72px 1fr;
  gap: 12px;
  padding: 8px 0;
  border-bottom: 1px solid var(--line);
  font-size: 11px;
  font-weight: 600;
  color: var(--muted);
  text-transform: uppercase;
}

.fb-table-row {
  display: grid;
  grid-template-columns: 120px 72px 72px 1fr;
  gap: 12px;
  padding: 8px 0;
  border-bottom: 1px solid var(--line);
  font-size: 13px;
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

/* Section card for review tabs */
.section-card {
  background: var(--bg1);
  border: 1px solid var(--line);
  border-radius: var(--r);
  padding: 24px;
}

.section-header {
  margin-bottom: 16px;
}

.section-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text);
  margin: 0 0 4px;
}

.section-desc {
  font-size: 12px;
  color: var(--muted);
  margin: 0;
}

/* Filter bar */
.filter-bar {
  display: flex;
  gap: 8px;
  margin-top: 8px;
}

.filter-select {
  padding: 4px 8px;
  border: 1px solid var(--line);
  border-radius: var(--r);
  background: var(--bg2);
  color: var(--text);
  font-size: 12px;
}

.filter-select:focus {
  outline: none;
  border-color: var(--blue);
}

/* Review list */
.review-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

/* Batch action bar */
.batch-action-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  background: var(--bg2);
  border: 1px solid var(--line);
  border-radius: var(--r);
  margin-bottom: 14px;
}

.batch-info {
  font-size: 12px;
  color: var(--sub);
}

.batch-actions {
  display: flex;
  gap: 8px;
}

.batch-btn {
  padding: 5px 14px;
  border: 1px solid var(--line);
  border-radius: var(--r);
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s;
}

.batch-btn:disabled {
  opacity: 0.5;
  cursor: default;
}

.batch-accept {
  background: var(--green);
  color: #fff;
  border-color: var(--green);
}

.batch-accept:hover:not(:disabled) {
  opacity: 0.85;
}

.batch-reject {
  background: transparent;
  color: var(--red);
  border-color: var(--red);
}

.batch-reject:hover:not(:disabled) {
  background: var(--red-bg);
}

/* Empty state */
.empty-state {
  text-align: center;
  padding: 40px 20px;
}

.empty-icon {
  font-size: 36px;
  color: var(--green);
  margin-bottom: 8px;
}

.empty-text {
  font-size: 13px;
  color: var(--muted);
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

/* Project list */
.project-list-card {
  background: var(--bg1);
  border: 1px solid var(--line);
  border-radius: var(--r-lg, var(--r));
  overflow: hidden;
}

/* Filter bar */
.project-filter-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  padding: 14px 16px;
  background: var(--bg2);
  border-bottom: 1px solid var(--line);
  align-items: center;
}

.filter-input {
  padding: 6px 12px;
  border: 1px solid var(--line);
  border-radius: var(--r);
  font-size: 13px;
  background: var(--bg1);
  color: var(--text);
  width: 160px;
  box-sizing: border-box;
}

.filter-input:focus {
  outline: none;
  border-color: var(--blue);
}

.filter-input::placeholder {
  color: var(--muted);
}

.date-input {
  width: 140px;
}

.filter-btn {
  padding: 5px 14px;
  border: 1px solid var(--blue);
  border-radius: var(--r);
  background: var(--blue);
  color: #fff;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: opacity 0.15s;
}

.filter-btn:hover {
  opacity: 0.85;
}

.filter-btn-secondary {
  background: transparent;
  color: var(--sub);
  border-color: var(--line);
}

.filter-btn-secondary:hover {
  border-color: var(--sub);
}

.project-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 14px;
}

.project-table thead {
  background: var(--bg2);
}

.project-table th {
  padding: 12px 16px;
  text-align: left;
  font-size: 11px;
  font-weight: 600;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.3px;
  border-bottom: 1px solid var(--line);
}

.project-table td {
  padding: 12px 16px;
  border-bottom: 1px solid var(--line);
  color: var(--text);
}

.num-col {
  text-align: right;
  width: 100px;
}

.project-row {
  cursor: pointer;
  transition: background 0.12s;
}

.project-row:hover {
  background: var(--bg2);
}

.project-row:last-child td {
  border-bottom: none;
}

.username-cell {
  color: var(--sub);
  font-weight: 500;
  max-width: 160px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.project-name-cell {
  font-weight: 500;
  max-width: none;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.id-cell {
  font-family: monospace;
  font-size: 11px;
  color: var(--muted);
  width: 90px;
}

.action-cell {
  white-space: nowrap;
}

.row-action-btn {
  padding: 5px 12px;
  border: 1px solid transparent;
  border-radius: var(--r);
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: opacity 0.15s;
}

.row-action-btn:hover {
  opacity: 0.85;
}

.row-action-btn + .row-action-btn {
  margin-left: 8px;
}

.detail-btn {
  background: transparent;
  color: var(--blue);
  border-color: var(--blue);
}

.feedback-btn {
  background: var(--blue);
  color: #fff;
}

.pending-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 22px;
  height: 20px;
  padding: 0 6px;
  background: var(--amber-bg);
  color: var(--amber);
  font-size: 11px;
  font-weight: 600;
  border-radius: 10px;
}

.status-cell {
  white-space: nowrap;
}

.status-badge {
  display: inline-flex;
  align-items: center;
  height: 20px;
  padding: 0 8px;
  font-size: 11px;
  font-weight: 600;
  border-radius: 10px;
}

.status-deleted {
  background: var(--bg3);
  color: var(--muted);
}

.status-completed {
  background: var(--green-bg);
  color: var(--green);
}

.status-running {
  background: var(--amber-bg);
  color: var(--amber);
}

.status-pending {
  background: var(--bg3);
  color: var(--sub);
}

.status-empty {
  background: var(--bg2);
  color: var(--dim);
  border: 1px dashed var(--line);
}

.project-row-deleted {
  opacity: 0.55;
}

.project-row-deleted .project-name-cell {
  text-decoration: line-through;
  color: var(--muted);
}

.num-zero {
  color: var(--muted);
}

.time-cell {
  color: var(--muted);
  font-size: 12px;
  white-space: nowrap;
}

/* Pagination */
.pagination-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 16px;
  border-top: 1px solid var(--line);
  font-size: 13px;
  color: var(--sub);
}

.pagination-info {
  font-size: 12px;
  color: var(--muted);
}

.pagination-controls {
  display: flex;
  align-items: center;
  gap: 4px;
}

.page-btn {
  padding: 4px 10px;
  border: 1px solid var(--line);
  border-radius: var(--r);
  background: var(--bg1);
  color: var(--text);
  font-size: 12px;
  cursor: pointer;
  transition: all 0.12s;
  min-width: 32px;
  text-align: center;
}

.page-btn:hover:not(:disabled) {
  border-color: var(--blue);
  color: var(--blue);
}

.page-btn:disabled {
  opacity: 0.35;
  cursor: default;
}

.page-btn.active {
  background: var(--blue);
  color: #fff;
  border-color: var(--blue);
}

.page-ellipsis {
  padding: 0 4px;
  color: var(--muted);
}

/* Back navigation */
.back-nav {
  margin-bottom: 12px;
}

.back-link {
  color: var(--blue);
  font-size: 13px;
  cursor: pointer;
  transition: opacity 0.15s;
}

.back-link:hover {
  opacity: 0.8;
}

@media (max-width: 767px) {
  .experience-dashboard {
    padding: 16px 12px;
  }

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

  .project-filter-bar {
    flex-direction: column;
    align-items: stretch;
  }

  .filter-input {
    width: 100%;
  }

  .date-input {
    width: 100%;
  }

  .project-table th,
  .project-table td {
    padding: 8px 6px;
    font-size: 12px;
  }

  .username-cell {
    max-width: 60px;
  }

  .project-name-cell {
    max-width: 120px;
  }

  .id-cell {
    display: none;
  }

  .time-cell {
    display: none;
  }

  .pagination-bar {
    flex-direction: column;
    gap: 8px;
    align-items: flex-start;
  }
}

/* Large screens: extra breathing room */
@media (min-width: 1400px) {
  .kpi-card {
    padding: 24px 32px;
  }

  .kpi-value {
    font-size: 36px;
  }

  .fb-table-header,
  .fb-table-row {
    grid-template-columns: 140px 80px 80px 1fr;
    gap: 16px;
  }

  .project-table th,
  .project-table td {
    padding: 14px 20px;
  }

  .username-cell {
    max-width: 200px;
  }
}
</style>
