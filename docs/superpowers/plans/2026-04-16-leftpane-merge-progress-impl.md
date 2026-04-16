# LeftPane 合并进度展示实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 LeftPane.vue 中正确处理 SSE `merging` 事件，当收到合并开始事件时显示进度动画（amber 渐变卡片 + 旋转图标），收到 `merged` 事件后切换到完成状态。

**Architecture:** 修改 ReviewExecutionView.vue 添加 isMerging/mergeProgress 状态并处理 SSE 事件，修改 LeftPane.vue 添加 merging phase 渲染和对应样式。

**Tech Stack:** Vue 3 Composition API, TypeScript, Pinia store

---

## 文件结构

- `frontend/src/views/ReviewExecutionView.vue` — 新增 isMerging, mergeProgress 状态和 SSE 事件处理
- `frontend/src/components/execution/LeftPane.vue` — 新增 merging phase 渲染，新增 .merge-block-running 样式

---

## Task 1: ReviewExecutionView.vue 新增合并状态和事件处理

**Files:**
- Modify: `frontend/src/views/ReviewExecutionView.vue`

- [ ] **Step 1: 在 phase ref 声明后添加 isMerging 和 mergeProgress 状态**

在 `frontend/src/views/ReviewExecutionView.vue` 第 22 行附近，找到 `const phase = ref(...)` 声明，在其后添加：

```typescript
// 合并阶段状态
const isMerging = ref(false)
const mergeProgress = ref('')
```

- [ ] **Step 2: 在 handleSSEEvent 函数的 switch 语句中添加 merging 事件处理**

在 `frontend/src/views/ReviewExecutionView.vue` 约第 117-122 行，找到 `case 'merging_started':` 和 `case 'merging_completed':` 的处理，在其后添加：

```typescript
case 'merging':
  // 合并历史结果
  isMerging.value = true
  mergeProgress.value = event.message || '正在合并历史结果...'
  break

case 'merged':
  // 合并完成
  isMerging.value = false
  mergeProgress.value = ''
  break
```

注意：保留现有的 `merging_started` 和 `merging_completed` 处理逻辑不变。

- [ ] **Step 3: 在 LeftPane 组件的 props 绑定中添加 isMerging 和 mergeProgress**

在 `frontend/src/views/ReviewExecutionView.vue` 约第 488-495 行，找到 LeftPane 组件绑定，修改为：

```vue
<LeftPane
  :phase="phase"
  :steps="timelineSteps"
  :error-message="errorMessage"
  :todos="todoList"
  :sub-agent-steps-map="subAgentStepsMap"
  :merged-stats="mergedStats"
  :is-merging="isMerging"
  :merge-progress="mergeProgress"
/>
```

- [ ] **Step 4: 提交更改**

```bash
git add frontend/src/views/ReviewExecutionView.vue
git commit -m "feat: add isMerging and mergeProgress state for SSE merging events"
```

---

## Task 2: LeftPane.vue 新增 merging phase 渲染和样式

**Files:**
- Modify: `frontend/src/components/execution/LeftPane.vue`

- [ ] **Step 1: 在 Props 接口中添加 isMerging 和 mergeProgress 属性**

在 `frontend/src/components/execution/LeftPane.vue` 约第 54-61 行，找到 `interface Props`，修改为：

```typescript
interface Props {
  phase: 'pending' | 'running' | 'completed' | 'failed'
  steps: TimelineStep[]
  errorMessage?: string | null
  todos?: TodoItemState[]
  subAgentStepsMap?: Record<string, TimelineStep[]>
  mergedStats?: MergedStats | null
  isMerging?: boolean        // 新增
  mergeProgress?: string     // 新增
}
```

- [ ] **Step 2: 在 defineProps 中添加新的 props（带默认值）**

在 `frontend/src/components/execution/LeftPane.vue` 约第 63 行，找到 `const props = defineProps<Props>()`，修改为：

```typescript
const props = withDefaults(defineProps<Props>(), {
  isMerging: false,
  mergeProgress: ''
})
```

- [ ] **Step 3: 修改合并阶段模板，新增正在合并状态渲染**

在 `frontend/src/components/execution/LeftPane.vue` 约第 246-292 行，找到 `<!-- 合并阶段 -->` 的 div 块，修改为：

```vue
<!-- 正在合并状态 -->
<div v-if="phase !== 'completed' && phase !== 'failed' && isMerging" class="phase-block">
  <div class="phase-label">合并与质检阶段</div>
  <div class="merge-block merge-block-running">
    <div class="merge-block-header">
      <div class="merge-status">
        <span class="merge-icon spin">⟳</span>
        <span>{{ mergeProgress || '正在合并历史结果...' }}</span>
      </div>
      <span class="chip chip-run">进行中</span>
    </div>
    <div class="merge-steps">
      <div v-for="(step, idx) in mergeSteps" :key="idx" class="merge-step">
        <div :class="['m-dot', idx === 0 ? 'md-run' : 'md-wait']"></div>
        <span>{{ step }}</span>
        <span v-if="idx < mergeSteps.length - 1" class="merge-step-arr">→</span>
      </div>
    </div>
  </div>
</div>

<!-- 已完成状态 -->
<div v-if="phase === 'completed'" class="phase-block">
  <div class="phase-label">合并与质检阶段</div>
  <div class="merge-block">
    <div class="merge-block-header">
      <div class="merge-status">
        <span class="merge-icon">✓</span>
        <span>MasterAgent 已汇总所有子代理结果</span>
      </div>
      <span class="chip chip-done">完成</span>
    </div>
    <!-- 合并步骤 -->
    <div class="merge-steps">
      <div v-for="(step, idx) in mergeSteps" :key="idx" class="merge-step">
        <div class="m-dot md-done"></div>
        <span>{{ step }}</span>
        <span v-if="idx < mergeSteps.length - 1" class="merge-step-arr">→</span>
      </div>
    </div>
    <!-- 合并结果统计 -->
    <div class="merge-result-summary">
      <div class="summary-title">合并结果摘要</div>
      <div class="summary-stats">
        <div class="stat-item">
          <span class="stat-num">{{ mergeStats.total }}</span>
          <span class="stat-label">发现问题</span>
        </div>
        <div class="stat-item stat-critical" v-if="mergeStats.critical > 0">
          <span class="stat-num">{{ mergeStats.critical }}</span>
          <span class="stat-label">严重</span>
        </div>
        <div class="stat-item stat-major" v-if="mergeStats.major > 0">
          <span class="stat-num">{{ mergeStats.major }}</span>
          <span class="stat-label">重要</span>
        </div>
        <div class="stat-item stat-minor" v-if="mergeStats.minor > 0">
          <span class="stat-num">{{ mergeStats.minor }}</span>
          <span class="stat-label">一般</span>
        </div>
        <div class="stat-item stat-compliant" v-if="mergeStats.compliant > 0">
          <span class="stat-num">{{ mergeStats.compliant }}</span>
          <span class="stat-label">通过</span>
        </div>
      </div>
    </div>
  </div>
</div>
```

- [ ] **Step 4: 在样式部分新增 .merge-block-running 和相关样式**

在 `frontend/src/components/execution/LeftPane.vue` 约第 354 行附近，找到 `.merge-block` 样式，在其后添加：

```css
.merge-block-running {
  background: var(--amber-bg);
  border-color: var(--amber-dim);
}

.merge-block-running .merge-status {
  color: var(--amber);
}

.merge-block-running .m-dot.md-run {
  background: var(--amber);
  animation: blink 1s infinite;
}

.merge-block-running .m-dot.md-wait {
  background: var(--line2);
}

.chip-run {
  background: var(--purple-bg);
  border-color: var(--purple-dim);
  color: var(--purple);
}

.merge-icon.spin {
  display: inline-block;
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
```

注意：如果 CSS 变量 `--amber`, `--amber-bg`, `--amber-dim` 不存在，需要在组件或全局样式中定义。

- [ ] **Step 5: 提交更改**

```bash
git add frontend/src/components/execution/LeftPane.vue
git commit -m "feat: add merging state UI in LeftPane with progress animation"
```

---

## Task 3: 验证

- [ ] **Step 1: 检查 TypeScript 类型是否正确**

确认所有新增的 props 和 state 类型正确，没有类型错误。

- [ ] **Step 2: 测试 SSE 事件流程**

1. 启动后端服务和前端
2. 开始一个审查任务
3. 观察当 SSE 发送 `merging` 事件时，左侧面板是否显示 amber 渐变卡片 + 旋转图标
4. 观察当 SSE 发送 `merged` 事件后，是否切换到完成状态并显示统计

---

## 自检清单

- [ ] ReviewExecutionView.vue 的 isMerging 和 mergeProgress 状态正确添加
- [ ] handleSSEEvent 中正确处理 'merging' 和 'merged' 事件
- [ ] LeftPane 组件正确接收 isMerging 和 mergeProgress props
- [ ] LeftPane.vue 的 merging 状态渲染使用 v-if="isMerging" 条件
- [ ] .merge-block-running 样式正确定义（amber 色系）
- [ ] 旋转动画 spin keyframes 正确定义
- [ ] 完成后 phase === 'completed' 的渲染逻辑保持不变
- [ ] 所有更改已提交
