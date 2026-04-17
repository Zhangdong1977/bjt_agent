<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'

interface TimelineStep {
  step_number: number
  step_type: string
  content: string
  timestamp: Date
  tool_args?: { tool_calls?: any[] }
  tool_result?: { tool_results?: any[] }
}

interface TodoItemState {
  id: string
  status: 'pending' | 'running' | 'completed' | 'failed'
}

const props = defineProps<{
  totalSteps: number
  completedCount: number
  findingsCount: number
  phase: 'pending' | 'running' | 'completed' | 'failed'
  subAgentStepsMap?: Record<string, TimelineStep[]>
  maxStepsMap?: Record<string, number>
  taskStartTime?: number
  todos?: TodoItemState[]
}>()

const router = useRouter()

// 执行容量进度
const execCapacityProgress = computed(() => {
  if (!props.maxStepsMap || Object.keys(props.maxStepsMap).length === 0) {
    return { current: 0, total: 0, percent: 0 }
  }

  let totalCapacity = 0
  let usedCapacity = 0

  for (const [todoId, maxSteps] of Object.entries(props.maxStepsMap)) {
    totalCapacity += maxSteps
    const steps = props.subAgentStepsMap?.[todoId] || []
    usedCapacity += Math.min(steps.length, maxSteps)
  }

  return {
    current: usedCapacity,
    total: totalCapacity,
    percent: totalCapacity > 0 ? Math.round((usedCapacity / totalCapacity) * 100) : 0
  }
})

// 完成任务数
const completedTasksCount = computed(() => {
  return props.todos?.filter(t => t.status === 'completed').length || 0
})

// 总任务数
const totalTasksCount = computed(() => {
  return props.todos?.length || 0
})

// 任务执行平均耗时（秒）
const averageDuration = computed(() => {
  if (!props.taskStartTime || completedTasksCount.value === 0) return 0
  const elapsed = Date.now() - props.taskStartTime
  return Math.round(elapsed / 1000 / completedTasksCount.value)
})

const statusText = computed(() => {
  switch (props.phase) {
    case 'pending': return '等待开始'
    case 'running': return '审查中'
    case 'completed': return '已完成'
    case 'failed': return '失败'
    default: return '未知'
  }
})

function goBack() {
  router.push({ name: 'project', params: { id: router.currentRoute.value.params.id } })
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

    <!-- 执行进度 -->
    <div class="sidebar-section">
      <div class="section-title">执行进度</div>
      <div class="overall-progress">
        <div class="progress-label">
          <span>执行容量</span>
          <span class="progress-pct">{{ execCapacityProgress.percent }}%</span>
        </div>
        <div class="progress-bar-outer">
          <div
            class="progress-bar-inner"
            :style="{ width: `${execCapacityProgress.percent}%` }"
            :class="{ 'animate': phase === 'running' }"
          ></div>
        </div>
        <div class="progress-stats">
          <span>{{ execCapacityProgress.current }} / {{ execCapacityProgress.total }} 步骤</span>
        </div>
      </div>
    </div>

    <!-- 执行统计 -->
    <div class="sidebar-section">
      <div class="section-title">执行统计</div>
      <div class="stats-grid">
        <div class="stat-box">
          <div class="stat-val sv-purple">{{ totalTasksCount }}</div>
          <div class="stat-lbl">任务总数</div>
        </div>
        <div class="stat-box">
          <div class="stat-val sv-green">{{ completedTasksCount }}</div>
          <div class="stat-lbl">已完成</div>
        </div>
        <div class="stat-box">
          <div class="stat-val sv-amber">{{ totalTasksCount - completedTasksCount }}</div>
          <div class="stat-lbl">进行中</div>
        </div>
        <div class="stat-box">
          <div class="stat-val sv-blue">{{ averageDuration }}s</div>
          <div class="stat-lbl">平均耗时</div>
        </div>
      </div>
    </div>

    <!-- 图例 -->
    <div class="sidebar-section">
      <div class="section-title">图例</div>
      <div class="legend-list">
        <div class="leg">
          <div class="leg-swatch" style="background: var(--purple)"></div>
          <span>主代理</span>
        </div>
        <div class="leg">
          <div class="leg-swatch" style="background: var(--blue)"></div>
          <span>思考</span>
        </div>
        <div class="leg">
          <div class="leg-swatch" style="background: var(--amber)"></div>
          <span>工具调用</span>
        </div>
        <div class="leg">
          <div class="leg-swatch" style="background: var(--green)"></div>
          <span>观察</span>
        </div>
        <div class="leg">
          <div class="leg-swatch" style="background: var(--green)"></div>
          <span>工具结果</span>
        </div>
      </div>
    </div>

    <!-- 操作按钮 -->
    <div class="sidebar-section">
      <div class="section-title">操作</div>
      <div class="actions">
        <button class="btn btn-ghost" @click="goBack">返回项目</button>
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
.status-running .status-dot { background: var(--purple); animation: pulse 1.4s ease-in-out infinite; }
.status-completed .status-dot { background: var(--green); }
.status-failed .status-dot { background: var(--red); }

.status-text {
  font-size: 14px;
  font-weight: 500;
  color: var(--text);
}

.status-pending .status-text { color: var(--muted); }
.status-running .status-text { color: var(--purple); }
.status-completed .status-text { color: var(--green); }
.status-failed .status-text { color: var(--red); }

@keyframes pulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.5; transform: scale(0.8); }
}

/* 进度条 */
.overall-progress {
  background: var(--bg2);
  border: 1px solid var(--line);
  border-radius: var(--r);
  padding: 12px;
}

.progress-label {
  display: flex;
  justify-content: space-between;
  font-size: 11px;
  color: var(--muted);
  margin-bottom: 7px;
}

.progress-pct {
  color: var(--purple);
  font-weight: 600;
}

.progress-bar-outer {
  height: 5px;
  background: var(--bg4);
  border-radius: 3px;
  overflow: hidden;
}

.progress-bar-inner {
  height: 100%;
  border-radius: 3px;
  background: var(--purple);
  transition: width 0.3s ease;
}

.progress-bar-inner.animate {
  animation: shine 1.8s ease-in-out infinite;
}

@keyframes shine {
  0% { opacity: 0.8; }
  50% { opacity: 1; }
  100% { opacity: 0.8; }
}

.progress-stats {
  margin-top: 6px;
  font-size: 10px;
  color: var(--muted);
  text-align: right;
}

/* 统计网格 */
.stats-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px;
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

.sv-purple { color: var(--purple); }
.sv-green { color: var(--green); }
.sv-amber { color: var(--amber); }
.sv-red { color: var(--red); }
.sv-blue { color: var(--blue); }

.stat-lbl {
  font-size: 10px;
  color: var(--muted);
  margin-top: 4px;
}

/* 图例 */
.legend-list {
  display: flex;
  flex-direction: column;
  gap: 7px;
}

.leg {
  display: flex;
  align-items: center;
  gap: 9px;
  font-size: 11px;
  color: var(--sub);
}

.leg-swatch {
  width: 3px;
  height: 14px;
  border-radius: 2px;
  flex-shrink: 0;
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
</style>