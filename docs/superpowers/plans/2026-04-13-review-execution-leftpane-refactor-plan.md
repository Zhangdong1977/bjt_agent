# Review Execution LeftPane 重构实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 LeftPane 从简单时间线重构为原型 bidding_review_todo_tasklist.html 的多代理执行 UI

**Architecture:** 采用组件化架构，5个新组件（SubAgentCard, SubAgentTimeline, TodoList, MasterOutputBlock, MergeBlock）+ LeftPane 作为组装层

**Tech Stack:** Vue 3 Composition API, TypeScript, CSS Variables (原型定义)

---

## 文件结构

```
frontend/src/components/execution/
├── SubAgentCard.vue       # [NEW] 可折叠子代理卡片
├── SubAgentTimeline.vue   # [NEW] 子代理时间线容器
├── TodoList.vue           # [NEW] 待办任务列表
├── MasterOutputBlock.vue   # [NEW] 主代理输出块
├── MergeBlock.vue         # [NEW] 合并阶段块
└── LeftPane.vue           # [MODIFY] 重构为组装组件
```

---

## Task 1: SubAgentCard 组件

**Files:**
- Create: `frontend/src/components/execution/SubAgentCard.vue`
- Test: 在浏览器中手动测试折叠/展开交互

- [ ] **Step 1: 创建 SubAgentCard.vue 基本结构**

```vue
<script setup lang="ts">
import { ref } from 'vue'

interface CheckItem {
  name: string
  status: 'done' | 'run' | 'wait' | 'fail'
}

interface Finding {
  type: 'crit' | 'major' | 'pass'
  text: string
}

const props = defineProps<{
  agentId: string
  title: string
  ruleFile: string
  checkItems: CheckItem[]
  status: 'done' | 'running' | 'wait'
  findings: Finding[]
  runningLog?: string
}>()

const isOpen = ref(false)

function toggle() {
  isOpen.value = !isOpen.value
}

function getItemClass(status: string) {
  const map: Record<string, string> = {
    done: 'dp-done',
    run: 'dp-run',
    wait: 'dp-wait',
    fail: 'dp-fail'
  }
  return map[status] || 'dp-wait'
}

function getFindingClass(type: string) {
  const map: Record<string, string> = {
    crit: 'ft-crit',
    major: 'ft-major',
    pass: 'ft-pass'
  }
  return map[type] || 'ft-info'
}
</script>

<template>
  <div :class="['agent-card', `ac-${status}`, { open: isOpen }]">
    <div class="agent-card-head" @click="toggle">
      <div class="ac-avt">{{ agentId }}</div>
      <div class="ac-info">
        <div class="ac-title">{{ title }}</div>
        <div class="ac-sub">{{ ruleFile }}</div>
      </div>
      <div class="ac-right">
        <div class="pbar-outer">
          <div class="pbar-inner" :style="{ width: status === 'done' ? '100%' : status === 'running' ? '50%' : '0%' }"></div>
        </div>
        <span :class="['chip', status === 'done' ? 'chip-done' : status === 'running' ? 'chip-run' : 'chip-wait']">
          {{ status === 'done' ? '完成' : status === 'running' ? '执行中' : '等待' }}
        </span>
        <span class="chevron">›</span>
      </div>
    </div>
    <div class="agent-card-body">
      <div class="dep-section">
        <div class="dep-sec-label">检查项执行链</div>
        <div class="dep-chain">
          <div v-for="(item, idx) in checkItems" :key="idx" class="dep-node">
            <span :class="['dep-pill', getItemClass(item.status)]">
              <span class="dp-dot"></span>
              {{ item.name }}
            </span>
            <span v-if="idx < checkItems.length - 1" class="dep-arr">→</span>
          </div>
        </div>
      </div>
      <div v-if="status === 'running' && runningLog" class="run-log">
        <span class="log-cursor"></span>
        {{ runningLog }}
      </div>
      <div class="findings">
        <span v-for="(finding, idx) in findings" :key="idx" :class="['finding-tag', getFindingClass(finding.type)]">
          {{ finding.text }}
        </span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.agent-card {
  background: var(--bg1);
  border: 1px solid var(--line);
  border-radius: var(--r2);
  overflow: hidden;
  margin-bottom: 8px;
}
.agent-card.ac-active { border-color: var(--purple-dim); }
.agent-card.ac-done { border-color: var(--green-dim); }

.agent-card-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  background: var(--bg2);
  border-bottom: 1px solid var(--line);
  cursor: pointer;
  user-select: none;
}
.agent-card.ac-active .agent-card-head { background: #160f28; }
.agent-card.ac-done .agent-card-head { background: #0c1d14; }

.ac-avt {
  width: 26px; height: 26px;
  border-radius: 4px;
  display: flex; align-items: center; justify-content: center;
  font-size: 11px; font-weight: 600;
  flex-shrink: 0;
}
.ac-done .ac-avt { background: var(--green-bg); color: var(--green); border: 1px solid var(--green-dim); }
.ac-active .ac-avt { background: var(--purple-bg); color: var(--purple); border: 1px solid var(--purple-dim); }
.ac-wait .ac-avt { background: var(--bg3); color: var(--muted); border: 1px solid var(--line2); }

.ac-info { flex: 1; min-width: 0; }
.ac-title { font-size: 12px; font-weight: 500; color: var(--bright); }
.ac-sub { font-size: 11px; color: var(--muted); margin-top: 1px; }
.ac-right { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }

.pbar-outer {
  width: 72px; height: 3px;
  background: var(--bg4);
  border-radius: 2px;
  overflow: hidden;
}
.pbar-inner { height: 100%; border-radius: 2px; transition: width 0.5s ease; }
.ac-done .pbar-inner { background: var(--green); }
.ac-active .pbar-inner { background: var(--purple); }
.ac-wait .pbar-inner { background: var(--dim); }

.chevron { font-size: 10px; color: var(--dim); transition: transform 0.2s; }
.agent-card.open .chevron { transform: rotate(90deg); }

.agent-card-body {
  padding: 12px 14px;
  display: none;
  flex-direction: column;
  gap: 12px;
}
.agent-card.open .agent-card-body { display: flex; }

.dep-sec-label {
  font-size: 10px;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  margin-bottom: 8px;
}

.dep-chain {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 3px;
}
.dep-node { display: flex; align-items: center; gap: 3px; }

.dep-pill {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 4px 9px;
  border-radius: var(--r);
  border: 1px solid;
  font-size: 11px;
  font-weight: 500;
  white-space: nowrap;
}
.dp-done { background: var(--green-bg); border-color: var(--green-dim); color: var(--green); }
.dp-run { background: var(--purple-bg); border-color: var(--purple-dim); color: var(--purple); }
.dp-wait { background: var(--bg3); border-color: var(--line2); color: var(--muted); }
.dp-fail { background: var(--red-bg); border-color: var(--red-dim); color: var(--red); }

.dp-dot { width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; }
.dp-done .dp-dot { background: var(--green); }
.dp-run .dp-dot { background: var(--purple); animation: blink 1s infinite; }
.dp-wait .dp-dot { background: var(--muted); }
.dp-fail .dp-dot { background: var(--red); }

.dep-arr { color: var(--dim); font-size: 10px; padding: 0 1px; }

.findings {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  padding-top: 10px;
  border-top: 1px solid var(--line);
}
.finding-tag {
  font-size: 10px;
  font-weight: 500;
  padding: 3px 8px;
  border-radius: 3px;
  border: 1px solid;
  display: flex;
  align-items: center;
  gap: 4px;
}
.ft-crit { background: var(--red-bg); border-color: var(--red-dim); color: var(--red); }
.ft-major { background: var(--amber-bg); border-color: var(--amber-dim); color: var(--amber); }
.ft-pass { background: var(--green-bg); border-color: var(--green-dim); color: var(--green); }

.run-log {
  font-size: 11px;
  color: var(--muted);
  background: var(--bg3);
  border: 1px solid var(--line);
  border-radius: var(--r);
  padding: 8px 10px;
  font-style: italic;
  display: flex;
  align-items: center;
  gap: 6px;
}
.log-cursor {
  width: 6px; height: 12px;
  background: var(--purple);
  display: inline-block;
  border-radius: 1px;
  animation: blink 0.9s step-end infinite;
}
@keyframes blink { 0%,100% { opacity: 1 } 50% { opacity: 0 } }

/* Chip styles */
.chip {
  font-size: 10px;
  font-weight: 500;
  padding: 2px 7px;
  border-radius: 3px;
  border: 1px solid;
}
.chip-done { background: var(--green-bg); border-color: var(--green-dim); color: var(--green); }
.chip-run { background: var(--purple-bg); border-color: var(--purple-dim); color: var(--purple); }
.chip-wait { background: var(--bg3); border-color: var(--line2); color: var(--muted); }
</style>
```

- [ ] **Step 2: 手动测试组件**

打开 `/review-execution` 页面，检查：
1. 组件是否正常渲染
2. 点击头部是否切换展开/折叠
3. 状态 chip 是否正确显示

- [ ] **Step 3: 提交**

```bash
git add frontend/src/components/execution/SubAgentCard.vue
git commit -m "feat: add SubAgentCard component with collapsible design

- Agent header with avatar, title, progress bar
- Check item execution chain (dep-chain) with status pills
- Findings tags (crit/major/pass)
- Running log with blinking cursor animation
- Click to expand/collapse

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 2: TodoList 组件

**Files:**
- Create: `frontend/src/components/execution/TodoList.vue`
- Test: 手动测试任务卡片显示

- [ ] **Step 1: 创建 TodoList.vue**

```vue
<script setup lang="ts">
interface TodoItem {
  id: string
  name: string
  ruleFile: string
  checkItemsCount: number
  depsType: 'sequential' | 'branching'
  status: 'done' | 'running' | 'wait'
  agentId: string
}

defineProps<{
  items: TodoItem[]
}>()

function getStatusClass(status: string) {
  return {
    'td-done': status === 'done',
    'td-run': status === 'running',
    'td-wait': status === 'wait'
  }
}

function getAgentTagClass(status: string) {
  return {
    'tag-done': status === 'done',
    'tag-run': status === 'running',
    'tag-wait': status === 'wait'
  }
}
</script>

<template>
  <div class="todo-list">
    <div
      v-for="item in items"
      :key="item.id"
      :class="['todo-item', getStatusClass(item.status)]"
    >
      <div class="todo-check">
        <span v-if="item.status === 'done'">✓</span>
        <span v-else-if="item.status === 'running'" class="todo-spin"></span>
      </div>
      <div class="todo-body">
        <div class="todo-name">{{ item.name }}</div>
        <div class="todo-meta">
          <span class="file">{{ item.ruleFile }}</span>
          <span class="sep">·</span>
          <span>{{ item.checkItemsCount }} 个检查项 · {{ item.depsType === 'sequential' ? '顺序依赖' : '分支依赖' }}</span>
        </div>
      </div>
      <div class="todo-right">
        <span :class="['agent-tag', getAgentTagClass(item.status)]">{{ item.agentId }}</span>
        <span :class="['chip', item.status === 'done' ? 'chip-done' : item.status === 'running' ? 'chip-run' : 'chip-wait']">
          {{ item.status === 'done' ? '完成' : item.status === 'running' ? '执行中' : '等待' }}
        </span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.todo-list { display: flex; flex-direction: column; gap: 3px; }

.todo-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 14px;
  border-radius: var(--r);
  transition: background 0.15s;
}
.todo-item:hover { background: var(--bg3); }

.todo-check {
  width: 16px; height: 16px;
  border-radius: 3px;
  border: 1px solid;
  display: flex; align-items: center; justify-content: center;
  flex-shrink: 0;
  font-size: 9px;
}
.todo-item.td-done .todo-check { border-color: var(--green-dim); background: var(--green-bg); color: var(--green); }
.todo-item.td-run .todo-check { border-color: var(--purple-dim); background: var(--purple-bg); color: var(--purple); }
.todo-item.td-wait .todo-check { border-color: var(--line2); background: var(--bg2); color: transparent; }

.todo-body { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.todo-name { font-size: 12px; font-weight: 500; color: var(--bright); }
.todo-item.td-done .todo-name { color: var(--sub); text-decoration: line-through; text-decoration-color: var(--dim); }
.todo-meta { font-size: 11px; color: var(--muted); display: flex; align-items: center; gap: 6px; }
.todo-meta .file { color: var(--blue); opacity: 0.7; }
.todo-meta .sep { color: var(--dim); }

.todo-right { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }

.agent-tag {
  font-size: 10px;
  font-weight: 600;
  padding: 2px 7px;
  border-radius: 3px;
  letter-spacing: 0.03em;
}
.tag-done { background: var(--green-bg); border: 1px solid var(--green-dim); color: var(--green); }
.tag-run { background: var(--purple-bg); border: 1px solid var(--purple-dim); color: var(--purple); }
.tag-wait { background: var(--bg3); border: 1px solid var(--line2); color: var(--muted); }

.todo-spin {
  width: 10px; height: 10px;
  border-radius: 50%;
  border: 1.5px solid var(--purple-dim);
  border-top-color: var(--purple);
  animation: spin 0.8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }

.chip {
  font-size: 10px;
  font-weight: 500;
  padding: 2px 7px;
  border-radius: 3px;
  border: 1px solid;
}
.chip-done { background: var(--green-bg); border-color: var(--green-dim); color: var(--green); }
.chip-run { background: var(--purple-bg); border-color: var(--purple-dim); color: var(--purple); }
.chip-wait { background: var(--bg3); border-color: var(--line2); color: var(--muted); }
</style>
```

- [ ] **Step 2: 手动测试**

确认任务卡片正确显示状态图标和 Agent 标签

- [ ] **Step 3: 提交**

```bash
git add frontend/src/components/execution/TodoList.vue
git commit -m "feat: add TodoList component for task card display

- Shows 4 rule document task cards
- Status indicators: done/running/wait
- Agent tags (A1/A2/A3/A4)
- Rule file and check item count display

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 3: MasterOutputBlock 组件

**Files:**
- Create: `frontend/src/components/execution/MasterOutputBlock.vue`

- [ ] **Step 1: 创建 MasterOutputBlock.vue**

```vue
<script setup lang="ts">
interface ToolCall {
  name: string
  arguments: Record<string, any>
}

interface MasterStep {
  step_number: number
  content: string
  timestamp: Date
  tool_calls?: ToolCall[]
  tool_results?: any[]
}

defineProps<{
  steps: MasterStep[]
}>()

function formatTime(date: Date): string {
  return new Date(date).toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false
  })
}

function formatToolResult(result: any): string {
  if (!result) return ''
  if (result.status === 'success' && result.content) {
    return result.content.slice(0, 100) + (result.content.length > 100 ? '...' : '')
  }
  if (result.status === 'error') return `失败: ${result.error || 'unknown'}`
  return JSON.stringify(result).slice(0, 100)
}
</script>

<template>
  <div class="output-block">
    <div class="output-header">
      <div class="output-header-icon" style="background:var(--purple-bg);border:1px solid var(--purple-dim)">
        <svg viewBox="0 0 11 11" fill="none">
          <circle cx="5.5" cy="5.5" r="4" stroke="#a78bfa" stroke-width="1.2"/>
          <path d="M5.5 3.5v2.5l1.5 1" stroke="#a78bfa" stroke-width="1.1" stroke-linecap="round"/>
        </svg>
      </div>
      <span class="output-header-title">主代理 — 规则解析</span>
      <div class="output-header-meta">
        <span class="chip chip-master">MASTER</span>
        <span class="ts" v-if="steps.length > 0">{{ formatTime(new Date(steps[steps.length - 1].timestamp)) }}</span>
      </div>
    </div>
    <div class="output-body">
      <div v-for="step in steps" :key="step.step_number" class="output-line">
        <span class="prompt">›</span>
        <span class="cmd">{{ step.content }}</span>
      </div>
      <div v-if="steps.length > 0 && steps[0].tool_calls?.length" class="tool-call">
        <span class="tool-call-fn">{{ steps[0].tool_calls[0].name }}</span>
        <span class="tool-call-arrow">·</span>
        <span class="tool-call-arg">{{ JSON.stringify(steps[0].tool_calls[0].arguments).slice(0, 50) }}...</span>
        <span class="tool-call-arrow">→</span>
        <span class="tool-call-result" v-if="steps[0].tool_results?.[0]">
          {{ formatToolResult(steps[0].tool_results[0].result) }}
        </span>
      </div>
    </div>
  </div>
</template>

<style scoped>
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
  width: 20px; height: 20px;
  border-radius: 4px;
  display: flex; align-items: center; justify-content: center;
}

.output-header-title {
  font-size: 12px;
  font-weight: 500;
  color: var(--bright);
  flex: 1;
}

.output-header-meta { display: flex; align-items: center; gap: 8px; }

.chip {
  font-size: 10px;
  font-weight: 500;
  padding: 2px 7px;
  border-radius: 3px;
  border: 1px solid;
}
.chip-master { background: var(--purple-bg); border-color: var(--purple-dim); color: var(--purple); }

.ts { font-size: 11px; color: var(--dim); font-style: italic; }

.output-body { padding: 12px 14px; }

.output-line {
  font-size: 12px;
  color: var(--sub);
  line-height: 1.7;
  display: flex;
  align-items: flex-start;
  gap: 8px;
}
.output-line .prompt { color: var(--dim); flex-shrink: 0; }
.output-line .cmd { color: var(--text); }

.tool-call {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  background: var(--bg3);
  border: 1px solid var(--line2);
  border-radius: var(--r);
  margin-top: 8px;
  font-size: 11px;
}
.tool-call-fn { color: var(--blue); font-weight: 500; }
.tool-call-arg { color: var(--muted); }
.tool-call-arrow { color: var(--dim); }
.tool-call-result { color: var(--green); }
</style>
```

- [ ] **Step 2: 提交**

```bash
git add frontend/src/components/execution/MasterOutputBlock.vue
git commit -m "feat: add MasterOutputBlock component for master agent output

- Displays master agent parsing output
- Shows tool call results
- Purple MASTER chip indicator

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 4: MergeBlock 组件

**Files:**
- Create: `frontend/src/components/execution/MergeBlock.vue`

- [ ] **Step 1: 创建 MergeBlock.vue**

```vue
<script setup lang="ts">
defineProps<{
  status: 'wait' | 'running' | 'done'
}>()

const steps = [
  '汇总子代理结果',
  '去重与标准化',
  '优先级排序',
  '异常二次校验',
  '生成审查报告'
]
</script>

<template>
  <div class="merge-block">
    <div class="output-header" style="background:transparent;padding:0 0 10px 0;border-bottom:1px solid var(--line);">
      <div class="output-header-icon" style="background:var(--teal-bg);border:1px solid var(--teal-dim)">
        <svg viewBox="0 0 11 11" fill="none">
          <path d="M2 5.5h7M5.5 2l3.5 3.5-3.5 3.5" stroke="#2dd4bf" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
      </div>
      <span class="output-header-title">结果合并与质检</span>
      <span :class="['chip', status === 'done' ? 'chip-done' : status === 'running' ? 'chip-run' : 'chip-wait']">
        {{ status === 'done' ? '完成' : status === 'running' ? '进行中' : '等待' }}
      </span>
    </div>
    <div class="merge-steps">
      <div v-for="(step, idx) in steps" :key="idx" class="merge-step">
        <div :class="['m-dot', status === 'done' ? 'md-done' : status === 'running' && idx === 0 ? 'md-run' : '']"></div>
        <span>{{ step }}</span>
        <span v-if="idx < steps.length - 1" class="merge-step-arr">→</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.merge-block {
  background: var(--bg1);
  border: 1px solid var(--line);
  border-radius: var(--r2);
  padding: 14px;
  opacity: 0.85;
}

.output-header {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}

.output-header-icon {
  width: 20px; height: 20px;
  border-radius: 4px;
  display: flex; align-items: center; justify-content: center;
}

.output-header-title {
  font-size: 12px;
  font-weight: 500;
  color: var(--bright);
  flex: 1;
}

.chip {
  font-size: 10px;
  font-weight: 500;
  padding: 2px 7px;
  border-radius: 3px;
  border: 1px solid;
}
.chip-done { background: var(--green-bg); border-color: var(--green-dim); color: var(--green); }
.chip-run { background: var(--purple-bg); border-color: var(--purple-dim); color: var(--purple); }
.chip-wait { background: var(--bg3); border-color: var(--line2); color: var(--muted); }

.merge-steps {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 10px;
}

.merge-step {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 11px;
  color: var(--muted);
}

.m-dot {
  width: 7px; height: 7px;
  border-radius: 50%;
  background: var(--line2);
}
.m-dot.md-done { background: var(--green); }
.m-dot.md-run { background: var(--purple); animation: blink 1s infinite; }
@keyframes blink { 0%,100% { opacity: 1 } 50% { opacity: 0 } }

.merge-step-arr { color: var(--dim); font-size: 11px; }
</style>
```

- [ ] **Step 2: 提交**

```bash
git add frontend/src/components/execution/MergeBlock.vue
git commit -m "feat: add MergeBlock component for merge phase display

- Shows 5-step merge process flow
- Steps: 汇总→去重→排序→校验→报告
- Teal styling for merge phase

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 5: SubAgentTimeline 容器组件

**Files:**
- Create: `frontend/src/components/execution/SubAgentTimeline.vue`

- [ ] **Step 1: 创建 SubAgentTimeline.vue**

```vue
<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import SubAgentCard from './SubAgentCard.vue'

interface CheckItem {
  name: string
  status: 'done' | 'run' | 'wait' | 'fail'
}

interface Finding {
  type: 'crit' | 'major' | 'pass'
  text: string
}

interface SubAgent {
  agentId: string
  title: string
  ruleFile: string
  checkItems: CheckItem[]
  status: 'done' | 'running' | 'wait'
  findings: Finding[]
  runningLog?: string
}

defineProps<{
  agents: SubAgent[]
}>()

const allOpen = ref(false)

function toggleAll() {
  allOpen.value = !allOpen.value
}

function handleKeydown(e: KeyboardEvent) {
  if (e.code === 'Space') {
    e.preventDefault()
    toggleAll()
  }
}

onMounted(() => {
  document.addEventListener('keydown', handleKeydown)
})

onUnmounted(() => {
  document.removeEventListener('keydown', handleKeydown)
})
</script>

<template>
  <div class="sub-agent-timeline">
    <div class="sec-label">
      子代理并行执行
      <button @click="toggleAll" class="toggle-all-btn">展开/折叠</button>
    </div>
    <div class="agent-list">
      <SubAgentCard
        v-for="agent in agents"
        :key="agent.agentId"
        :agent-id="agent.agentId"
        :title="agent.title"
        :rule-file="agent.ruleFile"
        :check-items="agent.checkItems"
        :status="agent.status"
        :findings="agent.findings"
        :running-log="agent.runningLog"
        :class="{ 'force-open': allOpen }"
      />
    </div>
  </div>
</template>

<style scoped>
.sub-agent-timeline {
  margin-bottom: 24px;
}

.sec-label {
  font-size: 10px;
  font-weight: 600;
  color: var(--muted);
  letter-spacing: 0.1em;
  text-transform: uppercase;
  margin-bottom: 10px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.sec-label::after {
  content: '';
  flex: 1;
  height: 1px;
  background: var(--line);
  margin-left: 8px;
}

.toggle-all-btn {
  font-size: 10px;
  padding: 2px 8px;
  background: var(--bg3);
  border: 1px solid var(--line2);
  border-radius: 3px;
  color: var(--muted);
  cursor: pointer;
  font-family: var(--mono);
}

.toggle-all-btn:hover {
  background: var(--bg4);
  color: var(--text);
}

.agent-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
</style>
```

- [ ] **Step 2: 提交**

```bash
git add frontend/src/components/execution/SubAgentTimeline.vue
git commit -m "feat: add SubAgentTimeline container component

- Manages multiple SubAgentCard instances
- Space key toggles all cards
- Section label with expand/collapse button

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 6: LeftPane 重构整合

**Files:**
- Modify: `frontend/src/components/execution/LeftPane.vue`

- [ ] **Step 1: 阅读当前 LeftPane.vue**

确认现有组件的样式变量和布局

- [ ] **Step 2: 重构 LeftPane.vue**

保留现有样式系统，重构为组装组件：

```vue
<script setup lang="ts">
import { computed } from 'vue'
import TodoList from './TodoList.vue'
import MasterOutputBlock from './MasterOutputBlock.vue'
import SubAgentTimeline from './SubAgentTimeline.vue'
import MergeBlock from './MergeBlock.vue'

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
  phase: 'pending' | 'running' | 'completed' | 'failed'
  steps: TimelineStep[]
  errorMessage?: string | null
}>()

// 模拟数据 - 实际应从 steps 解析
const masterSteps = computed(() =>
  props.steps.filter(s => s.step_type === 'master')
)

const todoItems = [
  { id: '1', name: '检查投标方资质合规性', ruleFile: 'rule_001_资质要求.md', checkItemsCount: 5, depsType: 'sequential' as const, status: 'done' as const, agentId: 'A1' },
  { id: '2', name: '核验技术方案规格参数', ruleFile: 'rule_002_技术规格.md', checkItemsCount: 8, depsType: 'branching' as const, status: 'done' as const, agentId: 'A2' },
  { id: '3', name: '审核商务条款与合同约定', ruleFile: 'rule_003_商务条款.md', checkItemsCount: 4, depsType: 'sequential' as const, status: 'running' as const, agentId: 'A3' },
  { id: '4', name: '验证环保合规与节能指标', ruleFile: 'rule_004_环保要求.md', checkItemsCount: 3, depsType: 'sequential' as const, status: 'wait' as const, agentId: 'A4' }
]

const subAgents = computed(() => [
  {
    agentId: 'A1',
    title: '检查投标方资质合规性',
    ruleFile: 'rule_001_资质要求.md · 5 个检查项',
    checkItems: [
      { name: '营业执照', status: 'done' as const },
      { name: '资质等级', status: 'done' as const },
      { name: '信用评级', status: 'done' as const },
      { name: '业绩证明', status: 'done' as const },
      { name: '人员配置', status: 'fail' as const }
    ],
    status: 'done' as const,
    findings: [
      { type: 'crit' as const, text: '严重: 项目经理资质证书缺失' },
      { type: 'major' as const, text: '一般: 业绩年限不足 1 项' },
      { type: 'pass' as const, text: '通过: 3 项' }
    ]
  },
  {
    agentId: 'A2',
    title: '核验技术方案规格参数',
    ruleFile: 'rule_002_技术规格.md · 8 个检查项',
    checkItems: [
      { name: '基础架构', status: 'done' as const },
      { name: '网络带宽', status: 'done' as const },
      { name: '安全等级', status: 'done' as const },
      { name: '灾备方案', status: 'done' as const },
      { name: '响应时间', status: 'done' as const },
      { name: '+3项', status: 'done' as const }
    ],
    status: 'done' as const,
    findings: [
      { type: 'major' as const, text: '一般: 灾备恢复时间超标' },
      { type: 'major' as const, text: '一般: 带宽冗余描述不清' },
      { type: 'major' as const, text: '一般: 数据加密方案缺细节' },
      { type: 'pass' as const, text: '通过: 5 项' }
    ]
  },
  {
    agentId: 'A3',
    title: '审核商务条款与合同约定',
    ruleFile: 'rule_003_商务条款.md · 4 个检查项',
    checkItems: [
      { name: '合同条款', status: 'done' as const },
      { name: '付款方式', status: 'done' as const },
      { name: '投标有效期', status: 'run' as const },
      { name: '保证金条款', status: 'wait' as const }
    ],
    status: 'running' as const,
    runningLog: '正在比对"投标有效期"条款 — 检查是否满足 ≥ 90 天要求...',
    findings: [
      { type: 'pass' as const, text: '通过: 合同条款 · 付款方式' }
    ]
  },
  {
    agentId: 'A4',
    title: '验证环保合规与节能指标',
    ruleFile: 'rule_004_环保要求.md · 3 个检查项',
    checkItems: [
      { name: '环保认证', status: 'wait' as const },
      { name: '碳排放指标', status: 'wait' as const },
      { name: '废料处理方案', status: 'wait' as const }
    ],
    status: 'wait' as const,
    findings: []
  }
])
</script>

<template>
  <div class="left-pane">
    <!-- 错误信息块 -->
    <div v-if="errorMessage" class="phase-block">
      <div class="phase-label">错误</div>
      <div class="error-block">
        <div class="error-icon">⚠️</div>
        <div class="error-content">
          <span class="error-label">审查失败:</span>
          <span class="error-message">{{ errorMessage }}</span>
        </div>
      </div>
    </div>

    <!-- 待办任务列表 -->
    <div v-if="phase === 'running' || phase === 'completed'" class="phase-block">
      <div class="phase-label">待办任务列表</div>
      <div class="output-block">
        <div class="output-header">
          <div class="output-header-icon" style="background:var(--amber-bg);border:1px solid var(--amber-dim)">
            <svg viewBox="0 0 11 11" fill="none">
              <path d="M2 3h7M2 5.5h7M2 8h4.5" stroke="#f0a429" stroke-width="1.2" stroke-linecap="round"/>
            </svg>
          </div>
          <span class="output-header-title">待办任务列表</span>
          <div class="output-header-meta">
            <span class="chip chip-todo">TODO · {{ todoItems.length }} tasks</span>
          </div>
        </div>
        <div class="output-body">
          <TodoList :items="todoItems" />
        </div>
      </div>
    </div>

    <!-- 主代理输出 -->
    <div v-if="masterSteps.length > 0" class="phase-block">
      <div class="phase-label">主代理 · 解析阶段</div>
      <MasterOutputBlock :steps="masterSteps" />
    </div>

    <!-- 子代理时间线 -->
    <div v-if="phase === 'running' || phase === 'completed'" class="phase-block">
      <SubAgentTimeline :agents="subAgents" />
    </div>

    <!-- 合并阶段 -->
    <div v-if="phase === 'merging' || phase === 'completed'" class="phase-block">
      <div class="phase-label">合并与质检阶段</div>
      <MergeBlock :status="phase === 'completed' ? 'done' : phase === 'merging' ? 'running' : 'wait'" />
    </div>

    <!-- 空状态 -->
    <div v-if="phase === 'pending'" class="phase-block">
      <div class="phase-label">等待开始</div>
      <div class="output-block">
        <div class="output-header">
          <div class="output-header-icon wait-icon">
            <svg viewBox="0 0 11 11" fill="none">
              <circle cx="5.5" cy="5.5" r="4" stroke="var(--muted)" stroke-width="1.2"/>
              <path d="M5.5 4v2l1.5 1" stroke="var(--muted)" stroke-width="1.1" stroke-linecap="round"/>
            </svg>
          </div>
          <span class="output-title">等待智能体启动</span>
          <span class="chip chip-wait">等待</span>
        </div>
        <div class="output-body">
          <div class="wait-status">
            <span>正在等待服务器响应...</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 复用现有样式 */
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
  flex-shrink: 0;
}

.wait-icon {
  background: var(--bg3);
  border: 1px solid var(--line2);
}

.output-title {
  font-size: 12px;
  font-weight: 500;
  color: var(--bright);
  flex: 1;
}

.output-header-meta { display: flex; align-items: center; gap: 8px; }

.chip {
  font-size: 10px;
  font-weight: 500;
  padding: 2px 7px;
  border-radius: 3px;
  border: 1px solid;
}
.chip-todo { background: var(--amber-bg); border-color: var(--amber-dim); color: var(--amber); }
.chip-wait { background: var(--bg3); border-color: var(--line2); color: var(--muted); }

.output-body { padding: 12px 14px; }

.error-block {
  background: var(--red-bg);
  border: 1px solid var(--red-dim);
  border-radius: var(--r2);
  padding: 14px;
  display: flex;
  align-items: flex-start;
  gap: 12px;
}

.error-icon { font-size: 20px; }

.error-content {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.error-label {
  font-size: 12px;
  font-weight: 500;
  color: var(--red);
}

.error-message {
  font-size: 12px;
  color: var(--text);
}

.wait-status {
  font-size: 12px;
  color: var(--muted);
}
</style>
```

- [ ] **Step 3: 手动测试完整页面**

1. 打开 `/review-execution` 页面
2. 检查 LeftPane 是否正常渲染
3. 测试 TodoList 显示
4. 测试 SubAgentCard 折叠/展开
5. 测试键盘快捷键 Space

- [ ] **Step 4: 提交**

```bash
git add frontend/src/components/execution/LeftPane.vue
git commit -m "refactor:重构 LeftPane 为多代理执行 UI

- 集成 TodoList 组件
- 集成 SubAgentTimeline 组件
- 集成 MasterOutputBlock 组件
- 集成 MergeBlock 组件
- 使用模拟数据展示完整流程

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 7: 整合测试

**Files:**
- Modify: `frontend/src/views/ReviewExecutionView.vue` (如需要)

- [ ] **Step 1: 检查 SSE 事件与组件数据映射**

确认 SSE 事件数据结构是否需要扩展以支持新组件

- [ ] **Step 2: 测试完整流程**

1. 启动审查任务
2. 观察 SSE 事件流
3. 验证各组件正确渲染

- [ ] **Step 3: 提交**

---

## 自检清单

完成计划后，对照设计文档进行自检：

- [ ] SubAgentCard 组件完整实现（折叠、执行链、findings）
- [ ] TodoList 组件完整实现
- [ ] MasterOutputBlock 组件完整实现
- [ ] MergeBlock 组件完整实现
- [ ] SubAgentTimeline 容器实现
- [ ] LeftPane 重构整合
- [ ] 键盘快捷键 (Space, R) 实现
- [ ] 所有组件样式与原型一致
