# LeftPane 合并进度展示设计

## 目标

在 `LeftPane.vue` 中正确处理和展示 SSE `merging` 事件，在合并阶段显示进度动画，而不是只在完成后才显示合并块。

## 问题分析

**当前问题：**
- `ReviewExecutionView.vue` 接收 `merging` / `merged` 事件但只打日志，未更新 UI
- `LeftPane.vue` 只在 `phase === 'completed'` 时显示合并结果，没有合并中状态
- `ReviewTimeline.vue` 有正确的合并动画实现，但未被 `ReviewExecutionView` 使用

**根本原因：**
- `ReviewExecutionView.vue` 没有维护 `isMerging` / `mergeProgress` 状态
- `LeftPane.vue` 的 `phase` 类型只有 `pending | running | completed | failed`，缺少 `merging` 状态

## 解决方案

### 1. ReviewExecutionView.vue 修改

新增状态：
```typescript
const isMerging = ref(false)
const mergeProgress = ref('')
```

新增事件处理：
```typescript
case 'merging':
  isMerging.value = true
  mergeProgress.value = event.message || '正在合并历史结果...'
  break

case 'merged':
case 'merging_completed':
  isMerging.value = false
  mergeProgress.value = ''
  break
```

新增 props 传递给 LeftPane：
```typescript
:is-merging="isMerging"
:merge-progress="mergeProgress"
```

### 2. LeftPane.vue 修改

#### Props 扩展
```typescript
interface Props {
  // ... 现有 props
  isMerging?: boolean
  mergeProgress?: string
}
```

#### Phase 类型扩展
```typescript
type Phase = 'pending' | 'running' | 'merging' | 'completed' | 'failed'
```

#### 模板修改

新增合并中状态渲染（替换原来的硬编码 `phase === 'completed'` 条件）：

```vue
<!-- 合并阶段 -->
<div v-if="phase === 'merging'" class="phase-block">
  <div class="phase-label">合并与质检阶段</div>
  <div class="merge-block merge-block-running">
    <div class="merge-block-header">
      <div class="merge-status">
        <span class="merge-icon spin">⟳</span>
        <span>{{ mergeProgress || '正在合并历史结果...' }}</span>
      </div>
      <span class="chip chip-run">进行中</span>
    </div>
    <!-- 合并步骤（静态展示，无后端步骤） -->
    <div class="merge-steps">
      <div v-for="(step, idx) in mergeSteps" :key="idx" class="merge-step">
        <div :class="['m-dot', idx === 0 ? 'md-run' : 'md-wait']"></div>
        <span>{{ step }}</span>
        <span v-if="idx < mergeSteps.length - 1" class="merge-step-arr">→</span>
      </div>
    </div>
  </div>
</div>

<!-- 已完成状态（保留原有逻辑） -->
<div v-if="phase === 'completed'" class="phase-block">
  ...
</div>
```

#### 样式修改

新增 `.merge-block-running` 状态样式：
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
```

## 数据流

```
后端 SSE                    ReviewExecutionView          LeftPane
   │                              │                        │
   ├── merging ──────────────────►│ isMerging = true       │
   │   {message: "..."}          │ mergeProgress = msg    │ phase = 'merging'
   │                              │                        │ isMerging = true
   │                              │                        │ mergeProgress = msg
   │                              │                        │
   ├── merged ────────────────────►│ isMerging = false     │
   │   {merged_count, total}       │ mergeProgress = ''    │ phase = 'completed'
   │                              │                        │ 显示合并统计
```

## 测试要点

1. 收到 `merging` 事件时，左侧面板显示 amber 渐变卡片 + 旋转图标
2. 进度文字正确显示 `event.message` 的值
3. 收到 `merged` / `merging_completed` 事件后，切换到完成状态
4. `ReviewExecutionView` 的 `phase` 保持 `running`（不影响 stepper 显示）

## 文件清单

- `frontend/src/views/ReviewExecutionView.vue` — 新增 `isMerging`, `mergeProgress` 状态和事件处理
- `frontend/src/components/execution/LeftPane.vue` — 新增 `merging` phase 渲染，新增样式
