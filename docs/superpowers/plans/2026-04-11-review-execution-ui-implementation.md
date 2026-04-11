# 审查执行页面 UI 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现高保真深色主题审查执行页面，包含全局主题切换和子代理时间线展开功能

**Architecture:**
- 全局主题系统：通过 CSS 变量和 localStorage 实现，支持深色/浅色模式切换
- 新建独立路由 `/projects/:id/review-execution`，使用两栏布局容器管理 SSE 连接和数据聚合
- 子代理卡片增加时间线展开功能，复用 ReviewTimeline 的 step 渲染逻辑

**Tech Stack:** Vue 3 Composition API, vue-router, pinia, Ant Design Vue

---

## 文件结构

### 新增文件

| 文件路径 | 职责 |
|----------|------|
| `frontend/src/assets/themes/dark.css` | 深色主题 CSS 变量 |
| `frontend/src/assets/themes/light.css` | 浅色主题 CSS 变量 |
| `frontend/src/composables/useTheme.ts` | 主题状态管理 composable |
| `frontend/src/components/execution/ExecutionHeader.vue` | 顶部栏组件 |
| `frontend/src/components/execution/ExecutionStepper.vue` | 四阶段步骤指示器 |
| `frontend/src/components/execution/LeftPane.vue` | 左侧主区域容器 |
| `frontend/src/components/execution/RightSidebar.vue` | 右侧边栏组件 |
| `frontend/src/components/execution/SubAgentTimeline.vue` | 子代理时间线组件 |
| `frontend/src/views/ReviewExecutionView.vue` | 主页面组件 |

### 修改文件

| 文件路径 | 修改内容 |
|----------|----------|
| `frontend/src/App.vue` | 引入全局主题 CSS，添加主题切换按钮 |
| `frontend/src/main.ts` | 导入主题初始化逻辑 |
| `frontend/src/router/index.ts` | 添加 `/projects/:id/review-execution` 路由 |
| `frontend/src/views/ProjectView.vue` | 修改"开始审查"按钮跳转到新页面 |
| `frontend/src/components/SubAgentCard.vue` | 增加"查看时间线"按钮 |

---

## Task 1: 创建全局主题 CSS 文件

**Files:**
- Create: `frontend/src/assets/themes/dark.css`
- Create: `frontend/src/assets/themes/light.css`

---

### Task 1.1: 创建深色主题 CSS

**Files:**
- Create: `frontend/src/assets/themes/dark.css`

- [ ] **Step 1: 创建深色主题文件**

```css
/* 深色主题变量 - 匹配 bidding_review_todo_tasklist.html */
:root {
  /* 背景色系 */
  --bg: #0a0a0a;
  --bg1: #111111;
  --bg2: #181818;
  --bg3: #1e1e1e;
  --bg4: #242424;

  /* 边框线系 */
  --line: #2a2a2a;
  --line2: #333333;
  --dim: #444444;

  /* 文字色系 */
  --muted: #666666;
  --sub: #888888;
  --text: #cccccc;
  --bright: #eeeeee;
  --white: #f5f5f5;

  /* 状态色系 */
  --green: #3dd68c;
  --green-dim: #1a4d35;
  --green-bg: #0d2318;

  --amber: #f0a429;
  --amber-dim: #4d3510;
  --amber-bg: #1e1500;

  --blue: #4da6ff;
  --blue-dim: #1a3a5c;
  --blue-bg: #0a1e30;

  --purple: #a78bfa;
  --purple-dim: #3b2f6b;
  --purple-bg: #1a1030;

  --red: #f87171;
  --red-dim: #4d1f1f;
  --red-bg: #200d0d;

  --teal: #2dd4bf;
  --teal-dim: #1a4040;
  --teal-bg: #0a2020;

  /* 基础变量 */
  --mono: 'JetBrains Mono', 'Geist Mono', 'Fira Code', monospace;
  --r: 6px;
  --r2: 10px;
}

/* 文字颜色 */
body.theme-dark {
  color: var(--text);
}

body.theme-dark h1,
body.theme-dark h2,
body.theme-dark h3 {
  color: var(--bright);
}
```

- [ ] **Step 2: 提交**

```bash
git add frontend/src/assets/themes/dark.css
git commit -m "feat: add dark theme CSS variables"
```

---

### Task 1.2: 创建浅色主题 CSS

**Files:**
- Create: `frontend/src/assets/themes/light.css`

- [ ] **Step 1: 创建浅色主题文件**

```css
/* 浅色主题变量 */
:root {
  /* 背景色系 */
  --bg: #f5f5f5;
  --bg1: #ffffff;
  --bg2: #fafafa;
  --bg3: #f0f0f0;
  --bg4: #e8e8e8;

  /* 边框线系 */
  --line: #e0e0e0;
  --line2: #d0d0d0;
  --dim: #b0b0b0;

  /* 文字色系 */
  --muted: #888888;
  --sub: #666666;
  --text: #333333;
  --bright: #111111;
  --white: #ffffff;

  /* 状态色系 */
  --green: #52c41a;
  --green-dim: #d9f7be;
  --green-bg: #f6ffed;

  --amber: #faad14;
  --amber-dim: #ffe58f;
  --amber-bg: #fffbe6;

  --blue: #1890ff;
  --blue-dim: #91d5ff;
  --blue-bg: #e6f7ff;

  --purple: #722ed1;
  --purple-dim: #d3adf7;
  --purple-bg: #f9f0ff;

  --red: #ff4d4f;
  --red-dim: #ffccc7;
  --red-bg: #fff1f0;

  --teal: #13c2c2;
  --teal-dim: #87e8de;
  --teal-bg: #e6fffb;

  /* 基础变量 */
  --mono: 'JetBrains Mono', 'Geist Mono', 'Fira Code', monospace;
  --r: 6px;
  --r2: 10px;
}
```

- [ ] **Step 2: 提交**

```bash
git add frontend/src/assets/themes/light.css
git commit -m "feat: add light theme CSS variables"
```

---

## Task 2: 创建 useTheme Composable

**Files:**
- Create: `frontend/src/composables/useTheme.ts`

---

- [ ] **Step 1: 创建 useTheme composable**

```typescript
import { ref, watch, onMounted } from 'vue'

type Theme = 'dark' | 'light'

const STORAGE_KEY = 'app-theme'

// 全局主题状态
const theme = ref<Theme>('dark')

// 初始化主题
function initTheme() {
  const stored = localStorage.getItem(STORAGE_KEY) as Theme | null
  if (stored === 'dark' || stored === 'light') {
    theme.value = stored
  } else {
    // 默认使用深色主题
    theme.value = 'dark'
  }
  applyTheme(theme.value)
}

// 应用主题到 DOM
function applyTheme(t: Theme) {
  document.body.classList.remove('theme-dark', 'theme-light')
  document.body.classList.add(`theme-${t}`)

  // 动态加载主题 CSS
  const existingLink = document.getElementById('theme-css')
  if (existingLink) {
    existingLink.remove()
  }
  const link = document.createElement('link')
  link.id = 'theme-css'
  link.rel = 'stylesheet'
  link.href = `/src/assets/themes/${t}.css`
  document.head.appendChild(link)
}

// 切换主题
function toggleTheme() {
  theme.value = theme.value === 'dark' ? 'light' : 'dark'
  localStorage.setItem(STORAGE_KEY, theme.value)
  applyTheme(theme.value)
}

// 设置特定主题
function setTheme(t: Theme) {
  theme.value = t
  localStorage.setItem(STORAGE_KEY, t)
  applyTheme(t)
}

export function useTheme() {
  onMounted(() => {
    initTheme()
  })

  watch(theme, (newTheme) => {
    applyTheme(newTheme)
  })

  return {
    theme,
    toggleTheme,
    setTheme,
    initTheme
  }
}
```

- [ ] **Step 2: 提交**

```bash
git add frontend/src/composables/useTheme.ts
git commit -m "feat: add useTheme composable for global theme management"
```

---

## Task 3: 修改 App.vue 添加主题初始化

**Files:**
- Modify: `frontend/src/App.vue`

---

- [ ] **Step 1: 添加主题初始化逻辑**

修改 `frontend/src/App.vue`:

```vue
<script setup lang="ts">
import { onMounted } from 'vue'
import { RouterView } from 'vue-router'
import { ConfigProvider } from 'ant-design-vue'
import { useAuthStore } from '@/stores/auth'
import { useTheme } from '@/composables/useTheme'

const authStore = useAuthStore()
const { initTheme } = useTheme()

onMounted(() => {
  authStore.initialize()
  initTheme()
})
</script>

<template>
  <ConfigProvider :theme="{ token: { colorPrimary: '#6366f1' } }">
    <RouterView />
  </ConfigProvider>
</template>
```

- [ ] **Step 2: 提交**

```bash
git add frontend/src/App.vue
git commit -m "feat: integrate useTheme in App.vue for global theme initialization"
```

---

## Task 4: 创建 ExecutionHeader 组件

**Files:**
- Create: `frontend/src/components/execution/ExecutionHeader.vue`

---

- [ ] **Step 1: 创建 ExecutionHeader 组件**

```vue
<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { useTheme } from '@/composables/useTheme'

const props = defineProps<{
  projectName: string
  status: 'running' | 'completed' | 'pending' | 'failed'
}>()

const router = useRouter()
const { theme, toggleTheme } = useTheme()

const isDark = computed(() => theme.value === 'dark')

function goBack() {
  router.back()
}
</script>

<template>
  <div class="execution-header">
    <div class="header-left">
      <button class="back-btn" @click="goBack">← 返回</button>
      <h1 class="project-title">{{ projectName }}</h1>
    </div>
    <div class="header-right">
      <!-- 主题切换按钮 -->
      <button class="theme-toggle" @click="toggleTheme">
        {{ isDark ? '☀️' : '🌙' }}
      </button>
      <!-- 状态徽章 -->
      <span :class="['status-badge', `status-${status}`]">
        <span v-if="status === 'running'" class="live-dot"></span>
        {{ status === 'running' ? '进行中' : status === 'completed' ? '已完成' : status === 'failed' ? '失败' : '等待' }}
      </span>
    </div>
  </div>
</template>

<style scoped>
.execution-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 20px;
  background: var(--bg1);
  border-bottom: 1px solid var(--line);
  height: 56px;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 16px;
}

.back-btn {
  padding: 6px 12px;
  background: transparent;
  border: 1px solid var(--line2);
  border-radius: var(--r);
  color: var(--sub);
  cursor: pointer;
  font-size: 13px;
}

.back-btn:hover {
  background: var(--bg3);
  color: var(--text);
}

.project-title {
  font-size: 15px;
  font-weight: 500;
  color: var(--bright);
}

.header-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.theme-toggle {
  width: 36px;
  height: 36px;
  border-radius: var(--r);
  background: var(--bg3);
  border: 1px solid var(--line2);
  cursor: pointer;
  font-size: 16px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.theme-toggle:hover {
  background: var(--bg4);
}

.status-badge {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 12px;
  border-radius: var(--r);
  font-size: 12px;
  font-weight: 500;
}

.status-running {
  background: var(--purple-bg);
  border: 1px solid var(--purple-dim);
  color: var(--purple);
}

.status-completed {
  background: var(--green-bg);
  border: 1px solid var(--green-dim);
  color: var(--green);
}

.status-failed {
  background: var(--red-bg);
  border: 1px solid var(--red-dim);
  color: var(--red);
}

.status-pending {
  background: var(--bg3);
  border: 1px solid var(--line2);
  color: var(--muted);
}

.live-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--purple);
  animation: blink 1.4s ease-in-out infinite;
}

@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.25; }
}
</style>
```

- [ ] **Step 2: 提交**

```bash
git add frontend/src/components/execution/ExecutionHeader.vue
git commit -m "feat: add ExecutionHeader component with theme toggle"
```

---

## Task 5: 创建 ExecutionStepper 组件

**Files:**
- Create: `frontend/src/components/execution/ExecutionStepper.vue`

---

- [ ] **Step 1: 创建 ExecutionStepper 组件**

```vue
<script setup lang="ts">
defineProps<{
  phase: 'master' | 'todo' | 'sub_agents' | 'merging' | 'completed'
}>()

const steps = [
  { key: 'master', label: '解析规则库' },
  { key: 'todo', label: '生成待办' },
  { key: 'sub_agents', label: '子代理执行' },
  { key: 'merging', label: '合并质检' }
]

function getStepClass(stepKey: string, currentPhase: string) {
  const phaseOrder = ['master', 'todo', 'sub_agents', 'merging', 'completed']
  const currentIndex = phaseOrder.indexOf(currentPhase)
  const stepIndex = phaseOrder.indexOf(stepKey)

  if (stepIndex < currentIndex) return 's-done'
  if (stepIndex === currentIndex) return 's-active'
  return 's-wait'
}
</script>

<template>
  <div class="stepper">
    <div
      v-for="(step, index) in steps"
      :key="step.key"
      :class="['step', getStepClass(step.key, phase)]"
    >
      <div class="step-n">
        <span v-if="getStepClass(step.key, phase) === 's-done'">✓</span>
        <span v-else>{{ index + 1 }}</span>
      </div>
      <span class="step-label">{{ step.label }}</span>
    </div>
  </div>
</template>

<style scoped>
.stepper {
  display: flex;
  align-items: center;
  margin-bottom: 24px;
}

.step {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  position: relative;
}

.step::after {
  content: '';
  flex: 1;
  height: 1px;
  background: var(--line2);
  margin: 0 8px;
}

.step:last-child::after {
  display: none;
}

.step-n {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 10px;
  font-weight: 600;
  flex-shrink: 0;
  border: 1px solid;
}

.s-done .step-n {
  background: var(--green-bg);
  border-color: var(--green-dim);
  color: var(--green);
}

.s-active .step-n {
  background: var(--purple-bg);
  border-color: var(--purple-dim);
  color: var(--purple);
}

.s-wait .step-n {
  background: var(--bg2);
  border-color: var(--line2);
  color: var(--muted);
}

.step-label {
  font-size: 11px;
  white-space: nowrap;
}

.s-done .step-label {
  color: var(--green);
}

.s-active .step-label {
  color: var(--purple);
}

.s-wait .step-label {
  color: var(--muted);
}

.s-done::after {
  background: var(--green-dim);
}

.s-active::after {
  background: var(--line2);
}
</style>
```

- [ ] **Step 2: 提交**

```bash
git add frontend/src/components/execution/ExecutionStepper.vue
git commit -m "feat: add ExecutionStepper component for 4-phase progress display"
```

---

## Task 6: 创建 RightSidebar 组件

**Files:**
- Create: `frontend/src/components/execution/RightSidebar.vue`

---

- [ ] **Step 1: 创建 RightSidebar 组件**

```vue
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
```

- [ ] **Step 2: 提交**

```bash
git add frontend/src/components/execution/RightSidebar.vue
git commit -m "feat: add RightSidebar component with stats and findings summary"
```

---

## Task 7: 创建 SubAgentTimeline 组件

**Files:**
- Create: `frontend/src/components/execution/SubAgentTimeline.vue`

---

- [ ] **Step 1: 创建 SubAgentTimeline 组件**

```vue
<script setup lang="ts">
import { computed } from 'vue'

interface ToolCall {
  name: string
  arguments: Record<string, any>
}

interface ToolResult {
  name: string
  result: any
}

interface TimelineStep {
  step_number: number
  step_type: string
  content: string
  timestamp: Date
  tool_args?: {
    tool_calls?: ToolCall[]
  }
  tool_result?: {
    tool_results?: ToolResult[]
  }
}

const props = defineProps<{
  steps: TimelineStep[]
}>()

const toolNameMap: Record<string, string> = {
  search_tender_doc: '搜索文档',
  rag_search: '搜索知识库',
  comparator: '内容比对',
}

function getStepColor(stepType: string): string {
  const colorMap: Record<string, string> = {
    tool_call: '#fa8c16',
    observation: '#52c41a',
    thought: '#1890ff',
  }
  return colorMap[stepType] || '#d9d9d9'
}

function getStepLabel(stepType: string): string {
  if (stepType === 'observation') return '观察'
  if (stepType === 'thought') return '思考过程'
  return stepType
}

function formatToolResult(result: ToolResult): string {
  if (!result) return ''
  if (result.result && typeof result.result === 'object') {
    const r = result.result as any
    if (r.status === 'success' && r.content) {
      return r.content.slice(0, 100) + (r.content.length > 100 ? '...' : '')
    }
    if (r.status === 'error') return `失败: ${r.error || 'unknown'}`
  }
  return JSON.stringify(result).slice(0, 100)
}

function formatTime(date: Date): string {
  return new Date(date).toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
}
</script>

<template>
  <div class="sub-agent-timeline">
    <div class="timeline-header">
      <span class="timeline-title">执行时间线</span>
      <span class="step-count">{{ steps.length }} 步骤</span>
    </div>
    <div class="timeline-content">
      <div
        v-for="step in steps"
        :key="step.step_number"
        :class="['timeline-step', `step-${step.step_type}`]"
      >
        <div class="step-header">
          <span class="step-type" :style="{ color: getStepColor(step.step_type) }">
            {{ getStepLabel(step.step_type) }}
          </span>
          <span class="step-time">{{ formatTime(step.timestamp) }}</span>
        </div>
        <div v-if="step.content" class="step-content">
          {{ step.content }}
        </div>
        <div v-if="step.tool_args?.tool_calls?.length" class="tool-calls">
          <div
            v-for="(toolCall, idx) in step.tool_args.tool_calls"
            :key="idx"
            class="tool-call-item"
          >
            <span class="tool-name">{{ toolNameMap[toolCall.name] || toolCall.name }}</span>
            <span class="tool-arrow">→</span>
            <span class="tool-result">
              {{ step.tool_result?.tool_results?.[idx] ? formatToolResult(step.tool_result.tool_results[idx]) : '等待结果' }}
            </span>
          </div>
        </div>
      </div>
      <div v-if="steps.length === 0" class="empty-state">
        暂无执行记录
      </div>
    </div>
  </div>
</template>

<style scoped>
.sub-agent-timeline {
  margin-top: 12px;
  padding: 12px;
  background: var(--bg3);
  border: 1px solid var(--line);
  border-radius: var(--r);
}

.timeline-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--line2);
}

.timeline-title {
  font-size: 12px;
  font-weight: 500;
  color: var(--text);
}

.step-count {
  font-size: 10px;
  color: var(--muted);
}

.timeline-content {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 300px;
  overflow-y: auto;
}

.timeline-step {
  padding: 8px 10px;
  background: var(--bg2);
  border-radius: var(--r);
  border-left: 3px solid;
}

.step-tool_call {
  border-left-color: #fa8c16;
}

.step-observation {
  border-left-color: #52c41a;
}

.step-thought {
  border-left-color: #1890ff;
}

.step-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 4px;
}

.step-type {
  font-size: 10px;
  font-weight: 600;
  text-transform: uppercase;
}

.step-time {
  font-size: 10px;
  color: var(--muted);
}

.step-content {
  font-size: 11px;
  color: var(--sub);
  line-height: 1.5;
  margin-bottom: 6px;
}

.tool-calls {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.tool-call-item {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 10px;
}

.tool-name {
  color: var(--blue);
  font-weight: 500;
}

.tool-arrow {
  color: var(--dim);
}

.tool-result {
  color: var(--green);
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.empty-state {
  text-align: center;
  padding: 20px;
  color: var(--muted);
  font-size: 12px;
}
</style>
```

- [ ] **Step 2: 提交**

```bash
git add frontend/src/components/execution/SubAgentTimeline.vue
git commit -m "feat: add SubAgentTimeline component for sub-agent step display"
```

---

## Task 8: 创建 LeftPane 组件

**Files:**
- Create: `frontend/src/components/execution/LeftPane.vue`

---

- [ ] **Step 1: 创建 LeftPane 组件**

```vue
<script setup lang="ts">
import { computed } from 'vue'
import TodoListCard from '@/components/TodoListCard.vue'
import SubAgentCard from '@/components/SubAgentCard.vue'
import type { TodoItem } from '@/types/review'

const props = defineProps<{
  phase: 'master' | 'todo' | 'sub_agents' | 'merging' | 'completed'
  todos: TodoItem[]
  masterOutput?: {
    totalDocs: number
    totalItems: number
    ruleDocs: Array<{ name: string; items: number }>
  }
}>()

const emit = defineEmits<{
  (e: 'toggleTimeline', todoId: string): void
}>()

const agentIndexMap = computed(() => {
  const map = new Map<string, number>()
  let idx = 1
  for (const todo of props.todos) {
    map.set(todo.id, idx++)
  }
  return map
})
</script>

<template>
  <div class="left-pane">
    <!-- 主代理输出块 - 解析阶段 -->
    <div v-if="phase !== 'master' || masterOutput" class="phase-block">
      <div class="phase-label">主代理 · 解析阶段</div>
      <div class="output-block">
        <div class="output-header">
          <div class="output-header-icon master-icon">
            <svg viewBox="0 0 11 11" fill="none">
              <circle cx="5.5" cy="5.5" r="4" stroke="#a78bfa" stroke-width="1.2"/>
              <path d="M5.5 3.5v2.5l1.5 1" stroke="#a78bfa" stroke-width="1.1" stroke-linecap="round"/>
            </svg>
          </div>
          <span class="output-title">主代理 — 规则解析</span>
          <span class="chip chip-master">MASTER</span>
        </div>
        <div class="output-body">
          <div v-if="masterOutput" class="output-line">
            <span class="prompt">›</span>
            <span class="cmd">
              <span class="keyword">Scanning</span>
              <span class="path">{{ masterOutput.ruleDocs.length }} rule documents</span>
              <span class="ok">found {{ masterOutput.totalDocs }} docs</span>
            </span>
          </div>
          <div v-if="masterOutput" v-for="doc in masterOutput.ruleDocs" :key="doc.name" class="output-line">
            <span class="prompt">›</span>
            <span class="cmd">
              <span class="keyword">Parsing</span>
              <span class="path">{{ doc.name }}</span>
              <span class="val">→ {{ doc.items }} items</span>
            </span>
          </div>
        </div>
      </div>
    </div>

    <!-- 待办列表 -->
    <div v-if="phase !== 'master'" class="phase-block">
      <div class="phase-label">待办任务列表</div>
      <div class="output-block">
        <div class="output-header">
          <div class="output-header-icon todo-icon">
            <svg viewBox="0 0 11 11" fill="none">
              <path d="M2 3h7M2 5.5h7M2 8h4.5" stroke="#f0a429" stroke-width="1.2" stroke-linecap="round"/>
            </svg>
          </div>
          <span class="output-title">待办任务列表</span>
          <span class="chip chip-todo">TODO · {{ todos.length }} tasks</span>
        </div>
        <div class="output-body">
          <TodoListCard
            v-for="todo in todos"
            :key="todo.id"
            :todo="todo"
            class="todo-item-wrapper"
          />
        </div>
      </div>
    </div>

    <!-- 子代理卡片组 -->
    <div v-if="phase === 'sub_agents' || phase === 'merging' || phase === 'completed'" class="phase-block">
      <div class="phase-label">子代理并行执行</div>
      <div class="agent-list">
        <SubAgentCard
          v-for="todo in todos"
          :key="todo.id"
          :todo="todo"
          :agent-index="agentIndexMap.get(todo.id) || 1"
        />
      </div>
    </div>

    <!-- 合并块 -->
    <div v-if="phase === 'merging' || phase === 'completed'" class="phase-block">
      <div class="phase-label">合并与质检阶段</div>
      <div class="merge-block">
        <div class="merge-header">
          <div class="output-header-icon merge-icon">
            <svg viewBox="0 0 11 11" fill="none">
              <path d="M2 5.5h7M5.5 2l3.5 3.5-3.5 3.5" stroke="#2dd4bf" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
          </div>
          <span class="output-title">结果合并与质检</span>
          <span :class="['chip', phase === 'completed' ? 'chip-done' : 'chip-wait']">
            {{ phase === 'completed' ? '完成' : '等待中' }}
          </span>
        </div>
        <div class="merge-steps">
          <div class="merge-step"><div class="m-dot"></div>汇总子代理结果</div>
          <span class="merge-arr">→</span>
          <div class="merge-step"><div class="m-dot"></div>去重与标准化</div>
          <span class="merge-arr">→</span>
          <div class="merge-step"><div class="m-dot"></div>优先级排序</div>
          <span class="merge-arr">→</span>
          <div class="merge-step"><div class="m-dot"></div>异常二次校验</div>
          <span class="merge-arr">→</span>
          <div class="merge-step"><div class="m-dot"></div>生成审查报告</div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.left-pane {
  padding: 20px 24px;
  border-right: 1px solid var(--line);
  overflow-y: auto;
  height: 100%;
}

.phase-block {
  margin-bottom: 24px;
}

.phase-label {
  font-size: 10px;
  font-weight: 600;
  color: var(--muted);
  letter-spacing: 0.1em;
  text-transform: uppercase;
  margin-bottom: 10px;
  display: flex;
  align-items: center;
  gap: 8px;
}

.phase-label::after {
  content: '';
  flex: 1;
  height: 1px;
  background: var(--line);
}

/* 输出块 */
.output-block {
  background: var(--bg1);
  border: 1px solid var(--line);
  border-radius: var(--r2);
  overflow: hidden;
}

.output-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  border-bottom: 1px solid var(--line);
  background: var(--bg2);
}

.output-header-icon {
  width: 20px;
  height: 20px;
  border-radius: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.master-icon {
  background: var(--purple-bg);
  border: 1px solid var(--purple-dim);
}

.todo-icon {
  background: var(--amber-bg);
  border: 1px solid var(--amber-dim);
}

.merge-icon {
  background: var(--teal-bg);
  border: 1px solid var(--teal-dim);
}

.output-title {
  font-size: 12px;
  font-weight: 500;
  color: var(--bright);
  flex: 1;
}

.output-body {
  padding: 12px 14px;
}

.output-line {
  font-size: 12px;
  color: var(--sub);
  line-height: 1.7;
  display: flex;
  align-items: flex-start;
  gap: 8px;
}

.prompt {
  color: var(--dim);
  flex-shrink: 0;
}

.cmd {
  color: var(--text);
}

.keyword {
  color: var(--purple);
}

.path {
  color: var(--blue);
}

.val {
  color: var(--amber);
}

.ok {
  color: var(--green);
}

.todo-item-wrapper {
  margin-bottom: 4px;
}

/* 代理列表 */
.agent-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

/* 合并块 */
.merge-block {
  background: var(--bg1);
  border: 1px solid var(--line);
  border-radius: var(--r2);
  padding: 14px;
}

.merge-block.opacity-50 {
  opacity: 0.5;
}

.merge-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--line);
  margin-bottom: 10px;
}

.merge-steps {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
}

.merge-step {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 11px;
  color: var(--muted);
}

.m-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--line2);
}

.merge-arr {
  color: var(--dim);
  font-size: 11px;
}

/* Chip 样式 */
.chip {
  font-size: 10px;
  font-weight: 500;
  padding: 2px 7px;
  border-radius: 3px;
  border: 1px solid;
}

.chip-master {
  background: var(--purple-bg);
  border-color: var(--purple-dim);
  color: var(--purple);
}

.chip-todo {
  background: var(--amber-bg);
  border-color: var(--amber-dim);
  color: var(--amber);
}

.chip-done {
  background: var(--green-bg);
  border-color: var(--green-dim);
  color: var(--green);
}

.chip-wait {
  background: var(--bg3);
  border-color: var(--line2);
  color: var(--muted);
}
</style>
```

- [ ] **Step 2: 提交**

```bash
git add frontend/src/components/execution/LeftPane.vue
git commit -m "feat: add LeftPane component as container for master/todo/sub-agent sections"
```

---

## Task 9: 创建 ReviewExecutionView 主页面

**Files:**
- Create: `frontend/src/views/ReviewExecutionView.vue`

---

- [ ] **Step 1: 创建 ReviewExecutionView 组件**

```vue
<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import { useProjectStore } from '@/stores/project'
import ExecutionHeader from '@/components/execution/ExecutionHeader.vue'
import ExecutionStepper from '@/components/execution/ExecutionStepper.vue'
import LeftPane from '@/components/execution/LeftPane.vue'
import RightSidebar from '@/components/execution/RightSidebar.vue'
import type { TodoItem } from '@/types/review'

const API_BASE = import.meta.env.VITE_API_BASE || '/api'
const route = useRoute()
const projectStore = useProjectStore()

const projectId = computed(() => route.params.id as string)

// SSE 连接
let eventSource: EventSource | null = null

// 状态
const phase = ref<'master' | 'todo' | 'sub_agents' | 'merging' | 'completed'>('master')
const todos = ref<Map<string, TodoItem>>(new Map())
const masterOutput = ref<{
  totalDocs: number
  totalItems: number
  ruleDocs: Array<{ name: string; items: number }>
} | null>(null)

// 统计数据
const totalDocs = computed(() => masterOutput.value?.totalDocs || 0)
const totalItems = computed(() => masterOutput.value?.totalItems || 0)
const completedCount = computed(() => Array.from(todos.value.values()).filter(t => t.status === 'completed').length)
const runningCount = computed(() => Array.from(todos.value.values()).filter(t => t.status === 'running').length)
const pendingCount = computed(() => Array.from(todos.value.values()).filter(t => t.status === 'pending').length)

// 发现统计
const allFindings = computed(() => {
  const findings: any[] = []
  for (const todo of todos.value.values()) {
    if (todo.result?.findings) {
      findings.push(...todo.result.findings)
    }
  }
  return findings
})

const criticalCount = computed(() => allFindings.value.filter(f => f.severity === 'critical').length)
const majorCount = computed(() => allFindings.value.filter(f => f.severity === 'major').length)
const passedCount = computed(() => allFindings.value.filter(f => f.is_compliant).length)
const uncheckedCount = computed(() => totalItems.value - allFindings.value.length - (pendingCount.value * 5))

const progress = computed(() => {
  if (totalItems.value === 0) return 0
  return Math.round((completedCount.value / totalItems.value) * 100)
})

const currentStatus = computed(() => {
  if (projectStore.currentTask?.status) return projectStore.currentTask.status
  if (phase.value === 'completed') return 'completed'
  if (phase.value === 'master') return 'running'
  return phase.value as any
})

// SSE 事件处理
function handleSSEEvent(event: any) {
  switch (event.type) {
    case 'master_started':
      phase.value = 'master'
      break
    case 'master_scan_completed':
      masterOutput.value = {
        totalDocs: event.total_docs,
        totalItems: event.total_items,
        ruleDocs: event.rule_docs || []
      }
      break
    case 'todo_created':
      const todoItem: TodoItem = {
        id: event.todo_id || '',
        rule_doc_name: event.rule_doc_name || '',
        check_items: (event.check_items || []).map((item: any) => ({
          id: item.id || '',
          title: item.title || '',
          status: 'pending'
        })),
        status: 'pending'
      }
      todos.value.set(todoItem.id, todoItem)
      break
    case 'todo_list_completed':
      phase.value = 'sub_agents'
      break
    case 'sub_agent_started':
      const todo = todos.value.get(event.todo_id)
      if (todo) {
        todo.status = 'running'
        todos.value.set(todo.id, { ...todo })
      }
      break
    case 'sub_agent_completed':
      const completedTodo = todos.value.get(event.todo_id)
      if (completedTodo) {
        completedTodo.status = 'completed'
        completedTodo.result = { findings: event.findings || [] }
        todos.value.set(completedTodo.id, { ...completedTodo })
      }
      break
    case 'sub_agent_failed':
      const failedTodo = todos.value.get(event.todo_id)
      if (failedTodo) {
        failedTodo.status = 'failed'
        failedTodo.error_message = event.error
        todos.value.set(failedTodo.id, { ...failedTodo })
      }
      break
    case 'merging_started':
      phase.value = 'merging'
      break
    case 'merging_completed':
      phase.value = 'completed'
      break
  }
}

function connect() {
  if (!projectStore.currentTask?.id) return

  disconnect()
  eventSource = new EventSource(`${API_BASE}/events/tasks/${projectStore.currentTask.id}/stream`)

  eventSource.onmessage = (e) => {
    try {
      const data = JSON.parse(e.data)
      handleSSEEvent(data)
    } catch (err) {
      console.error('Failed to parse SSE event:', err)
    }
  }

  eventSource.onerror = () => {
    disconnect()
  }
}

function disconnect() {
  if (eventSource) {
    eventSource.close()
    eventSource = null
  }
}

onMounted(async () => {
  await projectStore.selectProject(projectId.value)
  await projectStore.fetchReviewTasks()

  if (projectStore.currentTask?.id) {
    connect()
  }
})

onUnmounted(() => {
  disconnect()
})
</script>

<template>
  <div class="review-execution-view">
    <ExecutionHeader
      :project-name="projectStore.currentProject?.name || '项目'"
      :status="currentStatus"
    />

    <div class="main-layout">
      <ExecutionStepper :phase="phase" />

      <div class="content-area">
        <LeftPane
          :phase="phase"
          :todos="Array.from(todos.values())"
          :master-output="masterOutput"
        />

        <RightSidebar
          :total-docs="totalDocs"
          :total-items="totalItems"
          :completed-count="completedCount"
          :running-count="runningCount"
          :pending-count="pendingCount"
          :critical-count="criticalCount"
          :major-count="majorCount"
          :passed-count="passedCount"
          :unchecked-count="uncheckedCount"
          :progress="progress"
        />
      </div>
    </div>
  </div>
</template>

<style scoped>
.review-execution-view {
  min-height: 100vh;
  background: var(--bg);
}

.main-layout {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 56px);
  padding: 20px;
}

.content-area {
  display: grid;
  grid-template-columns: 1fr 320px;
  flex: 1;
  min-height: 0;
  border-radius: var(--r2);
  overflow: hidden;
  border: 1px solid var(--line);
}
</style>
```

- [ ] **Step 2: 提交**

```bash
git add frontend/src/views/ReviewExecutionView.vue
git commit -m "feat: add ReviewExecutionView as main page for execution UI"
```

---

## Task 10: 修改 SubAgentCard 添加时间线按钮

**Files:**
- Modify: `frontend/src/components/SubAgentCard.vue`

---

- [ ] **Step 1: 添加时间线按钮到 SubAgentCard**

在 SubAgentCard.vue 中：

1. 添加 `showTimeline` ref
2. 添加 `toggleTimeline` 方法
3. 在模板中添加"查看时间线"按钮
4. 当 `showTimeline` 为 true 时，显示 SubAgentTimeline 组件

修改 `<script setup>` 部分：

```typescript
const isOpen = ref(false)
const showTimeline = ref(false)

const toggle = () => {
  isOpen.value = !isOpen.value
}

const toggleTimeline = () => {
  showTimeline.value = !showTimeline.value
}
```

修改模板部分，在卡片底部添加按钮和条件渲染的时间线：

```vue
<!-- 在 .agent-card-body 末尾添加 -->
<div v-if="todo.status === 'completed'" class="timeline-toggle">
  <button class="timeline-btn" @click.stop="toggleTimeline">
    {{ showTimeline ? '收起时间线' : '查看时间线' }}
    <span class="btn-icon">{{ showTimeline ? '↑' : '↓' }}</span>
  </button>
</div>

<!-- 在 agent-card-body 末尾添加 SubAgentTimeline -->
<SubAgentTimeline
  v-if="showTimeline"
  :steps="[]"
/>
```

添加样式：

```css
.timeline-toggle {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--line);
}

.timeline-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  background: var(--bg3);
  border: 1px solid var(--line2);
  border-radius: var(--r);
  color: var(--sub);
  font-size: 11px;
  cursor: pointer;
}

.timeline-btn:hover {
  background: var(--bg4);
  color: var(--text);
}

.btn-icon {
  font-size: 10px;
}
```

- [ ] **Step 2: 提交**

```bash
git add frontend/src/components/SubAgentCard.vue
git commit -m "feat: add timeline toggle button to SubAgentCard"
```

---

## Task 11: 添加路由并修改跳转逻辑

**Files:**
- Modify: `frontend/src/router/index.ts`
- Modify: `frontend/src/views/ProjectView.vue`

---

- [ ] **Step 1: 添加路由**

在 `router/index.ts` 中添加新路由：

```typescript
{
  path: '/projects/:id/review-execution',
  name: 'review-execution',
  component: () => import('@/views/ReviewExecutionView.vue'),
  meta: { requiresAuth: true }
}
```

- [ ] **Step 2: 修改 ProjectView 跳转逻辑**

找到"开始审查"按钮，修改其点击事件：

```typescript
function startReview() {
  // ... 现有逻辑 ...

  // 跳转到审查执行页面
  router.push({
    name: 'review-execution',
    params: { id: projectId.value }
  })
}
```

- [ ] **Step 3: 提交**

```bash
git add frontend/src/router/index.ts frontend/src/views/ProjectView.vue
git commit -m "feat: add review-execution route and redirect to new page"
```

---

## Task 12: 集成测试与验证

**Files:**
- Modify: `frontend/src/main.ts` (如需要)

---

- [ ] **Step 1: 验证主题切换功能**

在浏览器中：
1. 打开项目页面
2. 点击"开始审查"
3. 验证页面以深色主题显示
4. 点击右上角主题切换按钮
5. 验证页面变为浅色主题
6. 验证刷新后主题保持

- [ ] **Step 2: 验证 SSE 事件**

1. 启动后端服务
2. 开始审查
3. 验证四阶段步骤指示器正确更新
4. 验证待办列表显示正确
5. 验证子代理卡片状态更新

- [ ] **Step 3: 验证子代理时间线**

1. 完成一个子代理任务
2. 点击"查看时间线"按钮
3. 验证时间线展开并显示步骤

- [ ] **Step 4: 提交最终更改**

```bash
git push origin master
```

---

## 实现顺序

1. Task 1: 创建主题 CSS 文件
2. Task 2: 创建 useTheme composable
3. Task 3: 修改 App.vue
4. Task 4: 创建 ExecutionHeader
5. Task 5: 创建 ExecutionStepper
6. Task 6: 创建 RightSidebar
7. Task 7: 创建 SubAgentTimeline
8. Task 8: 创建 LeftPane
9. Task 9: 创建 ReviewExecutionView
10. Task 10: 修改 SubAgentCard
11. Task 11: 添加路由
12. Task 12: 集成测试
