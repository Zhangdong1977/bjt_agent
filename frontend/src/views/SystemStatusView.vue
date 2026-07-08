<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { systemStatusApi } from '@/api/client'
import type { SystemStatus } from '@/types'

// ---------------------------------------------------------------------------
// 状态
// ---------------------------------------------------------------------------

const status = ref<SystemStatus | null>(null)
const loading = ref(false)
const autoRefresh = ref(true)
let refreshTimer: ReturnType<typeof setInterval> | null = null

// 维护模式本地表单（与后端当前态分离开，避免轮询覆盖用户正在输入的 reason）
const enabled = ref(false)
const reason = ref('')
const reasonInitialized = ref(false)

// ---------------------------------------------------------------------------
// 拉取
// ---------------------------------------------------------------------------

function extractErr(e: unknown, fallback: string): string {
  if (e && typeof e === 'object' && 'response' in e) {
    const resp = (e as { response?: { data?: { detail?: string } } }).response
    return resp?.data?.detail || fallback
  }
  return e instanceof Error ? e.message : fallback
}

async function fetchStatus() {
  try {
    const data = await systemStatusApi.getStatus()
    status.value = data
    // 同步开关态（每次都同步，反映他人/他处的变更）
    enabled.value = data.maintenance.is_enabled
    // reason 只在首次初始化，之后保留用户输入
    if (!reasonInitialized.value) {
      reason.value = data.maintenance.reason || ''
      reasonInitialized.value = true
    }
  } catch (e) {
    message.error(extractErr(e, '加载系统状态失败'))
  } finally {
    loading.value = false
  }
}

function startAutoRefresh() {
  stopAutoRefresh()
  if (!autoRefresh.value) return
  refreshTimer = setInterval(fetchStatus, 5000)
}
function stopAutoRefresh() {
  if (refreshTimer) {
    clearInterval(refreshTimer)
    refreshTimer = null
  }
}
watch(autoRefresh, (val) => (val ? startAutoRefresh() : stopAutoRefresh()))

// ---------------------------------------------------------------------------
// 维护模式切换
// ---------------------------------------------------------------------------

function onToggle(val: boolean | string | number) {
  const target = Boolean(val)
  if (target) {
    // 开启：二次确认（误开会全站挡登录）
    Modal.confirm({
      title: '开启维护模式？',
      content: '开启后，普通用户将无法从登录页登录，直到关闭维护。内部用户仍可登录。',
      okText: '开启维护',
      okType: 'danger',
      cancelText: '取消',
      onOk: () => applyMaintenance(target),
      onCancel: () => {
        enabled.value = false // 还原开关
      },
    })
  } else {
    applyMaintenance(target)
  }
}

async function applyMaintenance(target: boolean) {
  try {
    await systemStatusApi.setMaintenance({ enabled: target, reason: reason.value })
    message.success(target ? '已开启维护模式' : '已关闭维护模式')
    await fetchStatus()
  } catch (e) {
    message.error(extractErr(e, '切换维护模式失败'))
    enabled.value = !target // 还原开关到实际态
  }
}

// ---------------------------------------------------------------------------
// 派生展示
// ---------------------------------------------------------------------------

const ov = computed(() => status.value?.overview)
const nodes = computed(() => status.value?.nodes ?? [])
const workers = computed(() => status.value?.workers ?? [])

const nodeColumns = [
  { title: '节点', dataIndex: 'label', key: 'label' },
  { title: '角色', key: 'roles' },
  { title: '状态', key: 'is_online' },
  { title: '在审任务', dataIndex: 'active_review_tasks', key: 'active_review_tasks', width: 100 },
  { title: '在解析任务', dataIndex: 'active_parser_tasks', key: 'active_parser_tasks', width: 110 },
  { title: '存活 worker', key: 'workers', width: 120 },
  { title: '累计处理', dataIndex: 'processed', key: 'processed', width: 100 },
]

const workerColumns = [
  { title: 'Worker', dataIndex: 'name', key: 'name' },
  { title: '节点', dataIndex: 'node', key: 'node' },
  { title: '角色', dataIndex: 'role', key: 'role', width: 110 },
  { title: '状态', key: 'alive', width: 90 },
  { title: '在审', dataIndex: 'active_review_tasks', key: 'active_review_tasks', width: 80 },
  { title: '在解析', dataIndex: 'active_parser_tasks', key: 'active_parser_tasks', width: 90 },
  { title: '累计', dataIndex: 'processed', key: 'processed', width: 90 },
]

function roleColor(role: string): string {
  if (role === 'review') return 'blue'
  if (role === 'parser') return 'orange'
  return 'default'
}

function queueText(v: number | null | undefined): string {
  return v === null || v === undefined ? '—' : String(v)
}

function fmtTime(s: string | null | undefined): string {
  if (!s) return '—'
  const d = new Date(s)
  return Number.isNaN(d.getTime()) ? s : d.toLocaleString('zh-CN', { hour12: false })
}

onMounted(() => {
  loading.value = true
  void fetchStatus()
  startAutoRefresh()
})
onUnmounted(stopAutoRefresh)
</script>

<template>
  <div class="system-status">
    <div class="page-head">
      <div>
        <h2 class="page-title">系统状态</h2>
        <p class="page-sub">查看各工作节点运行状态、任务队列，并管理系统维护模式。</p>
      </div>
      <div class="head-actions">
        <span class="auto-label">自动刷新（5s）</span>
        <a-switch v-model:checked="autoRefresh" size="small" />
        <a-button :loading="loading" size="small" @click="fetchStatus">手动刷新</a-button>
      </div>
    </div>

    <!-- 维护模式卡片 -->
    <section class="card maintenance-card">
      <div class="maint-row">
        <div class="maint-state">
          <span
            class="status-dot"
            :class="status?.maintenance.is_enabled ? 'failed' : 'completed'"
          />
          <span class="maint-label">维护模式</span>
          <a-tag :color="status?.maintenance.is_enabled ? 'red' : 'green'">
            {{ status?.maintenance.is_enabled ? '维护中' : '正常运行' }}
          </a-tag>
        </div>
        <div class="maint-toggle">
          <a-switch :checked="enabled" @change="onToggle" />
          <span class="toggle-hint">{{ enabled ? '已开启' : '关闭' }}</span>
        </div>
      </div>
      <div class="maint-reason">
        <label class="field-label">维护原因（展示给被拦截的登录用户）</label>
        <a-textarea
          v-model:value="reason"
          :rows="2"
          :maxlength="500"
          placeholder="如：系统升级，预计 30 分钟"
        />
      </div>
      <div class="maint-meta" v-if="status?.maintenance">
        <span>开启时刻：{{ fmtTime(status.maintenance.started_at) }}</span>
        <span>最近操作：{{ fmtTime(status.maintenance.updated_at) }}</span>
      </div>
    </section>

    <!-- 降级提示 -->
    <a-alert
      v-if="ov?.degraded"
      type="warning"
      show-icon
      message="无法读取到工作节点实时状态"
      description="celery / broker 可能不可达，下方节点数据可能为空。请检查 celery worker 与 Redis broker 连接。"
      style="margin-bottom: 16px"
    />

    <!-- KPI 概览 -->
    <div class="kpi-grid">
      <div class="kpi-card">
        <div class="kpi-value kpi-blue">{{ ov?.running_reviews ?? '—' }}</div>
        <div class="kpi-label">在审任务</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-value kpi-amber">{{ ov?.parsing_documents ?? '—' }}</div>
        <div class="kpi-label">解析中文档</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-value">{{ queueText(ov?.review_queue) }}</div>
        <div class="kpi-label">review 队列</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-value">{{ queueText(ov?.parser_queue) }}</div>
        <div class="kpi-label">parser 队列</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-value" :class="(ov?.alive_workers ?? 0) > 0 ? 'kpi-green' : 'kpi-red'">
          {{ ov?.alive_workers ?? 0 }} / {{ ov?.total_workers ?? 0 }}
        </div>
        <div class="kpi-label">存活 worker</div>
      </div>
    </div>

    <!-- 节点表 -->
    <section class="card">
      <h3 class="section-title">工作节点</h3>
      <a-table
        :columns="nodeColumns"
        :data-source="nodes"
        row-key="name"
        :loading="loading"
        :pagination="false"
        size="middle"
        :locale="{ emptyText: '暂无节点（未配置 cluster_node_specs 且无 worker 在线）' }"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'roles'">
            <a-tag v-for="r in record.roles" :key="r" :color="roleColor(r)">{{ r }}</a-tag>
            <span v-if="!record.roles || record.roles.length === 0" class="muted">—</span>
          </template>
          <template v-else-if="column.key === 'is_online'">
            <span class="status-dot" :class="record.is_online ? 'completed' : 'failed'" />
            <span :class="record.is_online ? 'online-text' : 'offline-text'">
              {{ record.is_online ? '在线' : '离线' }}
            </span>
          </template>
          <template v-else-if="column.key === 'workers'">
            <span :class="record.alive_workers > 0 ? 'num-ok' : 'num-warn'">
              {{ record.alive_workers }} / {{ record.total_workers }}
            </span>
          </template>
        </template>
      </a-table>
    </section>

    <!-- Worker 明细 -->
    <section class="card">
      <h3 class="section-title">Worker 明细</h3>
      <a-table
        :columns="workerColumns"
        :data-source="workers"
        row-key="name"
        :pagination="false"
        size="middle"
        :locale="{ emptyText: '暂无 worker' }"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'alive'">
            <span class="status-dot" :class="record.alive ? 'completed' : 'failed'" />
            <span>{{ record.alive ? '存活' : '无响应' }}</span>
          </template>
          <template v-else-if="column.key === 'role'">
            <a-tag :color="roleColor(record.role)">{{ record.role }}</a-tag>
          </template>
        </template>
      </a-table>
    </section>
  </div>
</template>

<style scoped>
.system-status {
  padding: 0 0 24px;
}
.page-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  margin-bottom: 20px;
}
.page-title {
  margin: 0;
  font-size: 22px;
  font-weight: 700;
}
.page-sub {
  margin: 6px 0 0;
  color: var(--muted, #888);
  font-size: 13px;
}
.head-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}
.auto-label {
  color: var(--muted, #888);
  font-size: 13px;
}
.card {
  background: var(--bg1, #fff);
  border: 1px solid var(--line, #e0e0e0);
  border-radius: 12px;
  padding: 18px 20px;
  margin-bottom: 16px;
}
.section-title {
  margin: 0 0 12px;
  font-size: 16px;
  font-weight: 600;
}

/* 维护卡片 */
.maintenance-card {
  border-left: 4px solid var(--blue, #d7041a);
}
.maint-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}
.maint-state {
  display: flex;
  align-items: center;
  gap: 8px;
}
.maint-label {
  font-weight: 600;
  font-size: 15px;
}
.maint-toggle {
  display: flex;
  align-items: center;
  gap: 8px;
}
.toggle-hint {
  color: var(--muted, #888);
  font-size: 13px;
}
.maint-reason {
  margin-top: 14px;
}
.field-label {
  display: block;
  margin-bottom: 6px;
  font-size: 13px;
  color: var(--muted, #888);
}
.maint-meta {
  margin-top: 12px;
  display: flex;
  gap: 24px;
  font-size: 12px;
  color: var(--muted, #888);
}

/* KPI */
.kpi-grid {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 14px;
  margin-bottom: 16px;
}
.kpi-card {
  background: var(--bg1, #fff);
  border: 1px solid var(--line, #e0e0e0);
  border-radius: 12px;
  padding: 16px;
  text-align: center;
}
.kpi-value {
  font-size: 26px;
  font-weight: 700;
  color: #333;
}
.kpi-label {
  margin-top: 4px;
  font-size: 12px;
  color: var(--muted, #888);
}
.kpi-blue { color: var(--blue, #d7041a); }
.kpi-amber { color: var(--amber, #faad14); }
.kpi-green { color: var(--green, #52c41a); }
.kpi-red { color: var(--red, #ff4d4f); }

/* 状态点（沿用全局 .status-dot 的脉冲动画语义） */
.status-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-right: 6px;
  vertical-align: middle;
}
.status-dot.completed {
  background: var(--green, #52c41a);
}
.status-dot.failed {
  background: var(--red, #ff4d4f);
  animation: pulse 1.6s ease-in-out infinite;
}
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}
.online-text { color: var(--green, #52c41a); }
.offline-text { color: var(--red, #ff4d4f); }
.num-ok { color: var(--green, #52c41a); font-weight: 600; }
.num-warn { color: var(--red, #ff4d4f); font-weight: 600; }
.muted { color: var(--muted, #888); }

@media (max-width: 900px) {
  .kpi-grid { grid-template-columns: repeat(2, 1fr); }
  .page-head { flex-direction: column; }
}
</style>
