<script setup lang="ts">
import { computed, ref, watch, onUnmounted } from 'vue'
import { reviewApi } from '@/api/client'

const props = defineProps<{
  totalSteps: number
  completedCount: number
  findingsCount: number
  phase: 'pending' | 'running' | 'completed' | 'failed'
  taskStartTime?: number
  durationSeconds?: number | null
  projectId?: string
  taskId?: string
}>()

const emit = defineEmits<{
  (e: 'cancelled'): void
  (e: 'view-results'): void
}>()

const isAbandoning = ref(false)

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
    case 'running': return '审查中'
    case 'completed': return '已完成'
    case 'failed': return '失败'
    default: return '未知'
  }
})

async function abandonReview() {
  if (isAbandoning.value) return
  if (!props.projectId || !props.taskId) return
  isAbandoning.value = true
  try {
    await reviewApi.cancel(props.projectId, props.taskId)
    emit('cancelled')
  } catch (error) {
    console.error('放弃审查失败:', error)
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
      <div class="section-title">审查状态</div>
      <div :class="['status-indicator', `status-${phase}`]">
        <div class="status-dot"></div>
        <span class="status-text">{{ statusText }}</span>
      </div>
    </div>

    <!-- 执行统计 -->
    <div class="sidebar-section">
      <div class="section-title">执行统计</div>
      <div class="stats-grid">
        <div class="stat-box">
          <div class="stat-val sv-cyan">{{ totalSteps }}</div>
          <div class="stat-lbl">总步骤数</div>
        </div>
        <div class="stat-box">
          <div class="stat-val sv-green">{{ completedCount }}</div>
          <div class="stat-lbl">已完成</div>
        </div>
        <div class="stat-box">
          <div class="stat-val sv-amber">{{ findingsCount }}</div>
          <div class="stat-lbl">发现问题</div>
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
          {{ isAbandoning ? '放弃中...' : '放弃检查' }}
        </button>
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
.sv-cyan { color: #06b6d4; }

.stat-lbl {
  font-size: 10px;
  color: var(--muted);
  margin-top: 4px;
}

/* 操作按钮 */
.actions {
  display: flex;
  gap: 8px;
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
  background: #fef2f2;
  border: 1px solid #fecaca;
  color: #dc2626;
}

.btn-abandon:hover:not(:disabled) {
  background: #fee2e2;
  border-color: #fca5a5;
}

.btn-abandon:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.btn-view-results {
  background: #f0fdf4;
  border: 1px solid #86efac;
  color: #16a34a;
}

.btn-view-results:hover {
  background: #dcfce7;
  border-color: #4ade80;
}
</style>