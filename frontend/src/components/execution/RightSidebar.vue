<script setup lang="ts">
import { computed, ref, watch, onUnmounted } from 'vue'
import { duplicateApi, reviewApi } from '@/api/client'

const props = defineProps<{
  totalSteps: number
  completedCount: number
  progressPercent?: number
  findingsCount: number
  phase: 'pending' | 'running' | 'completed' | 'failed'
  taskStartTime?: number
  durationSeconds?: number | null
  projectId?: string
  taskId?: string
  mode?: 'review' | 'duplicate'
}>()

const emit = defineEmits<{
  (e: 'cancelled'): void
  (e: 'view-results'): void
}>()

const isAbandoning = ref(false)
const cancelError = ref<string | null>(null)

// 任务总运行时间
const totalRuntime = ref(0)
let runtimeTimer: ReturnType<typeof setInterval> | null = null

watch(() => props.phase, (newPhase) => {
  if (newPhase === 'running') {
    // 实时计时：使用 taskStartTime 前端计数
    if (runtimeTimer) clearInterval(runtimeTimer)
    runtimeTimer = setInterval(() => {
      if (props.taskStartTime) {
        totalRuntime.value = Math.round((Date.now() - props.taskStartTime) / 1000)
      }
    }, 1000)
  } else {
    if (runtimeTimer) {
      clearInterval(runtimeTimer)
      runtimeTimer = null
    }
    // 完成或失败时，优先使用数据库持久化的 duration_seconds
    if (props.durationSeconds != null) {
      totalRuntime.value = props.durationSeconds
    } else if (props.taskStartTime && (newPhase === 'completed' || newPhase === 'failed')) {
      totalRuntime.value = Math.round((Date.now() - props.taskStartTime) / 1000)
    }
  }
}, { immediate: true })

// phase 已经是 completed/failed 时，直接使用持久化值（历史查看场景）
watch(() => props.durationSeconds, (val) => {
  if (val != null && props.phase !== 'running') {
    totalRuntime.value = val
  }
}, { immediate: true })

onUnmounted(() => {
  if (runtimeTimer) clearInterval(runtimeTimer)
})

function formatRuntime(seconds: number): string {
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = seconds % 60
  return `${h.toString().padStart(2, '0')}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`
}

const statusText = computed(() => {
  switch (props.phase) {
    case 'pending': return '等待开始'
    case 'running': return props.mode === 'duplicate' ? '查重中' : '审查中'
    case 'completed': return '已完成'
    case 'failed': return '失败'
    default: return '未知'
  }
})

async function abandonReview() {
  if (isAbandoning.value) return
  if (!props.projectId || !props.taskId) return
  isAbandoning.value = true
  cancelError.value = null
  try {
    const api = props.mode === 'duplicate' ? duplicateApi : reviewApi
    await api.cancel(props.projectId, props.taskId)
    emit('cancelled')
  } catch (error) {
    console.error('放弃任务失败:', error)
    cancelError.value = '放弃检查失败，请稍后重试'
  } finally {
    isAbandoning.value = false
  }
}

const showViewResults = computed(() =>
  props.phase === 'completed'
)

function viewResults() {
  emit('view-results')
}
</script>

<template>
  <div class="right-sidebar">
    <!-- 状态指示 -->
    <div class="sidebar-section">
      <div class="section-title">{{ mode === 'duplicate' ? '查重状态' : '审查状态' }}</div>
      <div :class="['status-indicator', `status-${phase}`]">
        <div class="status-dot"></div>
        <span class="status-text">{{ statusText }}</span>
      </div>
    </div>

    <!-- 执行统计 -->
    <div class="sidebar-section">
      <div class="section-title">执行统计</div>
      <!-- 整体进度条：基于子代理完成情况 -->
      <div class="progress-block">
        <div class="progress-header">
          <span class="progress-label">整体进度</span>
          <span class="progress-pct">{{ progressPercent ?? 0 }}%</span>
        </div>
        <div class="progress-bar">
          <div
            class="progress-fill"
            :class="{ 'fill-done': phase === 'completed', 'fill-fail': phase === 'failed' }"
            :style="{ width: `${progressPercent ?? 0}%` }"
          ></div>
        </div>
        <div class="progress-meta">{{ completedCount }} / {{ totalSteps }} 子代理</div>
      </div>
      <div class="stats-grid">
        <div class="stat-box">
          <div class="stat-val sv-cyan">{{ totalSteps }}</div>
          <div class="stat-lbl">子代理总数</div>
        </div>
        <div class="stat-box">
          <div class="stat-val sv-green">{{ completedCount }}</div>
          <div class="stat-lbl">已完成</div>
        </div>
        <div class="stat-box">
          <div class="stat-val sv-amber">{{ findingsCount }}</div>
          <div class="stat-lbl">{{ mode === 'duplicate' ? '查重结果' : '发现问题' }}</div>
        </div>
        <div class="stat-box stat-box-wide">
          <div class="stat-val sv-blue">{{ formatRuntime(totalRuntime) }}</div>
          <div class="stat-lbl">运行时间</div>
        </div>
      </div>
    </div>

    <!-- 操作按钮 -->
    <div class="sidebar-section">
      <div class="section-title">操作</div>
      <div class="actions">
        <button
          v-if="showViewResults"
          class="btn btn-view-results"
          @click="viewResults"
        >
          查看结果
        </button>
        <button
          v-else
          class="btn btn-abandon"
          :disabled="isAbandoning || phase === 'completed' || phase === 'failed'"
          @click="abandonReview"
        >
          {{ isAbandoning ? '放弃中...' : (mode === 'duplicate' ? '放弃查重' : '放弃检查') }}
        </button>
        <div v-if="cancelError" class="cancel-error">{{ cancelError }}</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.right-sidebar {
  padding: 16px;
  background: var(--bg1);
  height: 100%;
  overflow-y: auto;
}

.sidebar-section {
  margin-bottom: 20px;
}

.section-title {
  font-size: 10px;
  font-weight: 600;
  color: var(--muted);
  letter-spacing: 0.1em;
  text-transform: uppercase;
  margin-bottom: 10px;
  display: flex;
  align-items: center;
  gap: 6px;
}

.section-title::after {
  content: '';
  flex: 1;
  height: 1px;
  background: var(--line);
}

/* 状态指示 */
.status-indicator {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px;
  background: var(--bg2);
  border: 1px solid var(--line);
  border-radius: var(--r);
}

.status-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
}

.status-pending .status-dot { background: var(--muted); }
.status-running .status-dot { background: var(--blue); animation: pulse 1.4s ease-in-out infinite; }
.status-completed .status-dot { background: var(--green); }
.status-failed .status-dot { background: var(--red); }

.status-text {
  font-size: 14px;
  font-weight: 500;
  color: var(--text);
}

.status-pending .status-text { color: var(--muted); }
.status-running .status-text { color: var(--blue); }
.status-completed .status-text { color: var(--green); }
.status-failed .status-text { color: var(--red); }

@keyframes pulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.5; transform: scale(0.8); }
}

/* 整体进度条 */
.progress-block {
  margin-bottom: 12px;
  padding: 10px 12px;
  background: var(--bg2);
  border: 1px solid var(--line);
  border-radius: var(--r);
}

.progress-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.progress-label {
  font-size: 11px;
  color: var(--muted);
}

.progress-pct {
  font-size: 14px;
  font-weight: 600;
  color: var(--blue);
}

.progress-bar {
  height: 6px;
  background: var(--bg);
  border-radius: 3px;
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  width: 0;
  background: var(--blue);
  border-radius: 3px;
  transition: width 0.3s ease;
}

.progress-fill.fill-done {
  background: var(--green);
}

.progress-fill.fill-fail {
  background: var(--red);
}

.progress-meta {
  font-size: 10px;
  color: var(--muted);
  margin-top: 6px;
}

/* 统计网格 */
.stats-grid {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: 6px;
}

.stat-box-wide {
  grid-column: 1 / -1;
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

.sv-purple { color: var(--blue); }
.sv-green { color: var(--green); }
.sv-amber { color: var(--amber); }
.sv-red { color: var(--red); }
.sv-blue { color: var(--blue); }
.sv-cyan { color: var(--teal); }

.stat-lbl {
  font-size: 10px;
  color: var(--muted);
  margin-top: 4px;
}

/* 操作按钮 */
.actions {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.cancel-error {
  color: var(--red);
  font-size: 12px;
  line-height: 1.4;
}

.btn {
  flex: 1;
  padding: 9px 12px;
  font-size: 12px;
  font-weight: 500;
  border-radius: var(--r);
  cursor: pointer;
  transition: all 0.15s;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
}

.btn-ghost {
  background: transparent;
  border: 1px solid var(--line2);
  color: var(--sub);
}

.btn-ghost:hover {
  background: var(--bg2);
  border-color: var(--dim);
  color: var(--text);
}

.btn-abandon {
  background: var(--red-bg);
  border: 1px solid var(--red-dim);
  color: var(--red);
}

.btn-abandon:hover:not(:disabled) {
  background: var(--red-bg);
  border-color: var(--red);
  filter: brightness(1.1);
}

.btn-abandon:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.btn-view-results {
  background: var(--green-bg);
  border: 1px solid var(--green-dim);
  color: var(--green);
}

.btn-view-results:hover {
  filter: brightness(1.1);
}
</style>
