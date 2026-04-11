<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  totalDocs: number
  totalItems: number
  completedCount: number
  runningCount: number
  pendingCount: number
  criticalCount: number
  majorCount: number
  passedCount: number
  uncheckedCount: number
  progress: number
}>()

const overallProgress = computed(() => {
  if (props.totalItems === 0) return 0
  return Math.round((props.completedCount / props.totalItems) * 100)
})
</script>

<template>
  <div class="right-sidebar">
    <!-- 整体进度 -->
    <div class="sidebar-section">
      <div class="section-title">整体进度</div>
      <div class="overall-progress">
        <div class="progress-label">
          <span>子代理完成率</span>
          <span class="progress-pct">{{ overallProgress }}%</span>
        </div>
        <div class="progress-bar-outer">
          <div class="progress-bar-inner" :style="{ width: `${overallProgress}%` }">
            <div class="progress-shine"></div>
          </div>
        </div>
      </div>
    </div>

    <!-- 执行统计 -->
    <div class="sidebar-section">
      <div class="section-title">执行统计</div>
      <div class="stats-grid">
        <div class="stat-box">
          <div class="stat-val sv-purple">{{ totalDocs }}</div>
          <div class="stat-lbl">规则文档</div>
        </div>
        <div class="stat-box">
          <div class="stat-val sv-blue">{{ totalItems }}</div>
          <div class="stat-lbl">检查项总数</div>
        </div>
        <div class="stat-box">
          <div class="stat-val sv-green">{{ completedCount }}</div>
          <div class="stat-lbl">已完成</div>
        </div>
        <div class="stat-box">
          <div class="stat-val sv-amber">{{ runningCount + pendingCount }}</div>
          <div class="stat-lbl">进行中/等待</div>
        </div>
      </div>
    </div>

    <!-- 发现问题汇总 -->
    <div class="sidebar-section">
      <div class="section-title">已发现不符合项</div>
      <div class="finding-row fr-crit">
        <div class="fr-dot"></div>
        <div class="fr-label">严重缺陷</div>
        <div class="fr-count">{{ criticalCount }}</div>
      </div>
      <div class="finding-row fr-major">
        <div class="fr-dot"></div>
        <div class="fr-label">一般缺陷</div>
        <div class="fr-count">{{ majorCount }}</div>
      </div>
      <div class="finding-row fr-pass">
        <div class="fr-dot"></div>
        <div class="fr-label">已通过</div>
        <div class="fr-count">{{ passedCount }}</div>
      </div>
      <div class="finding-row fr-wait">
        <div class="fr-dot"></div>
        <div class="fr-label">未检查</div>
        <div class="fr-count">{{ uncheckedCount }}</div>
      </div>
    </div>

    <!-- 图例 -->
    <div class="sidebar-section">
      <div class="section-title">图例</div>
      <div class="legend-list">
        <div class="leg">
          <div class="leg-swatch" style="background: var(--purple)"></div>
          <span>主代理 (Master)</span>
        </div>
        <div class="leg">
          <div class="leg-swatch" style="background: var(--amber)"></div>
          <span>待办列表 (Todo)</span>
        </div>
        <div class="leg">
          <div class="leg-swatch" style="background: var(--blue)"></div>
          <span>子代理 (Sub-Agent)</span>
        </div>
        <div class="leg">
          <div class="leg-swatch" style="background: var(--teal)"></div>
          <span>合并质检 (Merging)</span>
        </div>
        <div class="leg">
          <div class="leg-swatch" style="background: var(--green)"></div>
          <span>完成 / 通过</span>
        </div>
        <div class="leg">
          <div class="leg-swatch" style="background: var(--red)"></div>
          <span>严重缺陷</span>
        </div>
      </div>
    </div>

    <!-- 操作按钮 -->
    <div class="sidebar-section">
      <div class="section-title">操作</div>
      <div class="actions">
        <button class="btn btn-ghost">取消审查</button>
        <button class="btn btn-primary">查看结果 →</button>
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
  position: relative;
  overflow: hidden;
}

.progress-shine {
  position: absolute;
  top: 0;
  left: -100%;
  width: 60%;
  height: 100%;
  background: linear-gradient(90deg, transparent, rgba(167,139,250,.5), transparent);
  animation: shine 1.8s ease-in-out infinite;
}

@keyframes shine {
  to { left: 160%; }
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

/* 发现行 */
.finding-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border-radius: var(--r);
  font-size: 12px;
  background: var(--bg2);
  border: 1px solid var(--line);
  margin-bottom: 5px;
}

.fr-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}

.fr-label {
  flex: 1;
  color: var(--text);
}

.fr-count {
  font-weight: 600;
}

.fr-crit .fr-dot { background: var(--red); }
.fr-major .fr-dot { background: var(--amber); }
.fr-pass .fr-dot { background: var(--green); }
.fr-wait .fr-dot { background: var(--dim); }

.fr-crit .fr-count { color: var(--red); }
.fr-major .fr-count { color: var(--amber); }
.fr-pass .fr-count { color: var(--green); }
.fr-wait .fr-count { color: var(--muted); }

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

.btn-primary {
  background: var(--purple-bg);
  border: 1px solid var(--purple-dim);
  color: var(--purple);
}

.btn-primary:hover {
  background: var(--purple-dim);
  border-color: var(--purple);
}
</style>