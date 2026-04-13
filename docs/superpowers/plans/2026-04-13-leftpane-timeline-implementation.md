# LeftPane Timeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 LeftPane.vue 改为垂直嵌套时间线布局，展示 MasterAgent → SubAgentExecutor → BidReviewAgent 的父子关系及 BidReviewAgent 内部 LLM 调用时间线。

**Architecture:** 采用垂直时间线布局，新增 `AgentTimelineItem`、`BidReviewAgentBlock`、`SubAgentExecutorBlock`、`FindingsBar` 四个组件，LeftPane 作为容器整合所有时间线节点。

**Tech Stack:** Vue 3 Composition API, TypeScript, 现有 CSS 变量系统

---

## 文件结构

| 文件 | 职责 |
|------|------|
| `frontend/src/components/execution/FindingsBar.vue` | Findings 结果展示标签栏 |
| `frontend/src/components/execution/AgentTimelineItem.vue` | 单个时间线节点（master/observation/tool_call） |
| `frontend/src/components/execution/BidReviewAgentBlock.vue` | BidReviewAgent 可折叠执行块 |
| `frontend/src/components/execution/SubAgentExecutorBlock.vue` | SubAgentExecutor 容器 |
| `frontend/src/components/execution/LeftPane.vue` | 主容器，重写整合所有组件 |

---

## Task 1: FindingsBar 组件

**Files:**
- Create: `frontend/src/components/execution/FindingsBar.vue`
- Test: 无需独立测试

- [ ] **Step 1: 创建 FindingsBar.vue**

```vue
<script setup lang="ts">
interface Finding {
  type: 'crit' | 'major' | 'pass'
  text: string
}

interface Props {
  findings: Finding[]
}

defineProps<Props>()

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
  <div class="findings-bar">
    <span
      v-for="(finding, idx) in findings"
      :key="idx"
      :class="['finding-tag', getFindingClass(finding.type)]"
    >
      {{ finding.text }}
    </span>
  </div>
</template>

<style scoped>
.findings-bar {
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
</style>
```

- [ ] **Step 2: 提交**

```bash
git add frontend/src/components/execution/FindingsBar.vue
git commit -m "feat(execution): add FindingsBar component"
```

---

## Task 2: AgentTimelineItem 组件

**Files:**
- Create: `frontend/src/components/execution/AgentTimelineItem.vue`
- Depend: FindingsBar.vue (Task 1)

- [ ] **Step 1: 创建 AgentTimelineItem.vue**

```vue
<script setup lang="ts">
interface ToolCall {
  name: string
  arguments: Record<string, any>
}

interface ToolResult {
  name: string
  result: any
}

interface Props {
  stepNumber: number
  stepType: 'master' | 'observation' | 'tool_call' | 'thought' | 'tool_result'
  content: string
  timestamp: Date
  toolCalls?: ToolCall[]
  toolResults?: ToolResult[]
  status?: 'pending' | 'running' | 'completed' | 'error'
  duration?: number
}

const props = defineProps<Props>()

// 工具名称映射
const toolNameMap: Record<string, string> = {
  search_tender_doc: '搜索文档',
  rag_search: '搜索知识库',
  comparator: '内容比对',
}

function formatTime(date: Date): string {
  return new Date(date).toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false
  })
}

function getStepColor(stepType: string): string {
  const colorMap: Record<string, string> = {
    master: 'var(--purple)',
    tool_call: 'var(--amber)',
    observation: 'var(--green)',
    thought: 'var(--blue)',
  }
  return colorMap[stepType] || 'var(--dim)'
}

function getCardClass(stepType: string): string {
  return `card-${stepType}`
}

function formatToolArg(key: string, value: any): string {
  if (key === 'doc_type') {
    return value === 'tender' ? '文档类型: 招标书' : '文档类型: 投标书'
  }
  if (typeof value === 'string' && value.length > 100) {
    return `${key}: ${value.slice(0, 100)}...`
  }
  return `${key}: ${value}`
}

function formatToolResult(result: any): string {
  if (result?.status === 'success' && result?.content) {
    return result.content.slice(0, 200) + (result.content.length > 200 ? '...' : '')
  }
  if (result?.status === 'error') {
    return `失败: ${result.error || 'unknown'}`
  }
  return JSON.stringify(result).slice(0, 100)
}
</script>

<template>
  <div class="timeline-item">
    <div class="timeline-dot" :style="{ background: getStepColor(stepType) }"></div>
    <div :class="['timeline-card', getCardClass(stepType)]">
      <div class="card-header">
        <span class="step-number">#{{ stepNumber }}</span>
        <span class="step-label">{{ stepType === 'master' ? '主代理' : stepType === 'observation' ? '观察' : stepType === 'tool_call' ? '工具调用' : '思考' }}</span>
        <span v-if="status === 'running'" class="status-running">RUNNING</span>
        <span class="timestamp">{{ formatTime(timestamp) }}</span>
      </div>
      <p v-if="content" class="step-text">{{ content }}</p>
      <!-- 工具调用详情 -->
      <div v-if="toolCalls?.length" class="tool-calls-section">
        <div v-for="(tc, idx) in toolCalls" :key="idx" class="tool-call-item">
          <div class="tool-call-header">
            <span class="tool-name">{{ toolNameMap[tc.name] || tc.name }}</span>
          </div>
          <div class="tool-call-args">
            <span v-for="(value, key) in tc.arguments" :key="key" class="param-tag">
              {{ formatToolArg(key, value) }}
            </span>
          </div>
          <div v-if="toolResults?.[idx]" class="tool-call-result">
            <span class="result-text">{{ formatToolResult(toolResults[idx].result) }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.timeline-item {
  display: flex;
  gap: 12px;
  margin-bottom: 12px;
  position: relative;
}

.timeline-item::before {
  content: '';
  position: absolute;
  left: 5px;
  top: 20px;
  bottom: -12px;
  width: 2px;
  background: var(--line);
}

.timeline-dot {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  flex-shrink: 0;
  margin-top: 4px;
  z-index: 1;
}

.timeline-card {
  flex: 1;
  padding: 10px 14px;
  border-radius: var(--r2);
  border-left: 3px solid;
}

.card-master { background: var(--purple-bg); border-color: var(--purple); }
.card-observation { background: var(--green-bg); border-color: var(--green); }
.card-tool_call { background: var(--amber-bg); border-color: var(--amber); }
.card-thought { background: var(--blue-bg); border-color: var(--blue); }

.card-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.step-number {
  font-size: 11px;
  font-weight: 600;
  color: var(--dim);
}

.step-label {
  font-size: 12px;
  font-weight: 500;
  color: var(--text);
}

.status-running {
  font-size: 10px;
  color: var(--blue);
  background: var(--blue-bg);
  padding: 2px 6px;
  border-radius: 3px;
}

.timestamp {
  margin-left: auto;
  font-size: 10px;
  color: var(--muted);
}

.step-text {
  font-size: 12px;
  color: var(--text);
  line-height: 1.6;
  margin: 0;
}

.tool-calls-section {
  margin-top: 10px;
  padding: 8px;
  background: var(--bg3);
  border-radius: var(--r);
}

.tool-call-item {
  background: var(--bg1);
  border: 1px solid var(--line);
  border-radius: var(--r);
  padding: 8px 10px;
  margin-bottom: 6px;
}

.tool-call-item:last-child {
  margin-bottom: 0;
}

.tool-name {
  font-size: 11px;
  font-weight: 600;
  color: var(--purple);
}

.tool-call-args {
  margin-top: 4px;
  font-size: 11px;
  color: var(--muted);
}

.param-tag {
  display: inline-block;
  margin-right: 10px;
}

.tool-call-result {
  margin-top: 6px;
  padding-top: 6px;
  border-top: 1px dashed var(--line);
  font-size: 11px;
  color: var(--green);
}
</style>
```

- [ ] **Step 2: 提交**

```bash
git add frontend/src/components/execution/AgentTimelineItem.vue
git commit -m "feat(execution): add AgentTimelineItem component"
```

---

## Task 3: BidReviewAgentBlock 组件

**Files:**
- Create: `frontend/src/components/execution/BidReviewAgentBlock.vue`
- Depend: AgentTimelineItem.vue (Task 2), FindingsBar.vue (Task 1)

- [ ] **Step 1: 创建 BidReviewAgentBlock.vue**

```vue
<script setup lang="ts">
import { ref } from 'vue'
import AgentTimelineItem from './AgentTimelineItem.vue'
import FindingsBar from './FindingsBar.vue'

interface ToolCall {
  name: string
  arguments: Record<string, any>
}

interface ToolResult {
  name: string
  result: any
}

interface CheckItem {
  name: string
  status: 'done' | 'run' | 'wait' | 'fail'
}

interface Finding {
  type: 'crit' | 'major' | 'pass'
  text: string
}

interface TimelineStep {
  step_number: number
  step_type: 'master' | 'observation' | 'tool_call' | 'thought' | 'tool_result'
  content: string
  timestamp: Date
  tool_args?: { tool_calls?: ToolCall[] }
  tool_result?: { tool_results?: ToolResult[] }
}

interface Props {
  agentId: string
  title: string
  ruleFile: string
  status: 'done' | 'running' | 'wait'
  checkItems?: CheckItem[]
  steps: TimelineStep[]
  findings: Finding[]
}

defineProps<Props>()

const isOpen = ref(false)

function toggle() {
  isOpen.value = !isOpen.value
}

function getStatusText(status: string) {
  if (status === 'done') return '完成'
  if (status === 'running') return '执行中'
  return '等待'
}
</script>

<template>
  <div :class="['bid-review-agent-block', `bra-${status}`]">
    <div class="block-header" @click="toggle">
      <div class="bra-avatar">{{ agentId }}</div>
      <div class="bra-info">
        <div class="bra-title">{{ title }}</div>
        <div class="bra-sub">{{ ruleFile }}</div>
      </div>
      <div class="bra-right">
        <div class="pbar-outer">
          <div class="pbar-inner" :style="{ width: status === 'done' ? '100%' : status === 'running' ? '50%' : '0%' }"></div>
        </div>
        <span :class="['chip', status === 'done' ? 'chip-done' : status === 'running' ? 'chip-run' : 'chip-wait']">
          {{ getStatusText(status) }}
        </span>
        <span class="chevron" :class="{ open: isOpen }">›</span>
      </div>
    </div>
    <div class="block-body" v-show="isOpen">
      <!-- 内部时间线 -->
      <div v-if="steps.length > 0" class="internal-timeline">
        <AgentTimelineItem
          v-for="step in steps"
          :key="step.step_number"
          :step-number="step.step_number"
          :step-type="step.step_type as any"
          :content="step.content"
          :timestamp="step.timestamp"
          :tool-calls="step.tool_args?.tool_calls"
          :tool-results="step.tool_result?.tool_results"
        />
      </div>
      <!-- Findings -->
      <FindingsBar v-if="findings.length > 0" :findings="findings" />
    </div>
  </div>
</template>

<style scoped>
.bid-review-agent-block {
  background: var(--bg1);
  border: 1px solid var(--line);
  border-radius: var(--r2);
  overflow: hidden;
  margin-bottom: 8px;
}

.bra-running { border-color: var(--purple-dim); }
.bra-done { border-color: var(--green-dim); }

.block-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  background: var(--bg2);
  cursor: pointer;
  user-select: none;
}

.bra-running .block-header { background: var(--purple-bg); }
.bra-done .block-header { background: var(--green-bg); }

.bra-avatar {
  width: 26px;
  height: 26px;
  border-radius: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  font-weight: 600;
  flex-shrink: 0;
}

.bra-done .bra-avatar { background: var(--green-bg); color: var(--green); border: 1px solid var(--green-dim); }
.bra-running .bra-avatar { background: var(--purple-bg); color: var(--purple); border: 1px solid var(--purple-dim); }
.bra-wait .bra-avatar { background: var(--bg3); color: var(--muted); border: 1px solid var(--line2); }

.bra-info { flex: 1; min-width: 0; }
.bra-title { font-size: 12px; font-weight: 500; color: var(--bright); }
.bra-sub { font-size: 11px; color: var(--muted); margin-top: 1px; }

.bra-right { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }

.pbar-outer {
  width: 72px;
  height: 3px;
  background: var(--bg4);
  border-radius: 2px;
  overflow: hidden;
}

.pbar-inner {
  height: 100%;
  border-radius: 2px;
  transition: width 0.5s ease;
}

.bra-done .pbar-inner { background: var(--green); }
.bra-running .pbar-inner { background: var(--purple); }
.bra-wait .pbar-inner { background: var(--dim); }

.chevron {
  font-size: 10px;
  color: var(--dim);
  transition: transform 0.2s;
}

.chevron.open { transform: rotate(90deg); }

.block-body {
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.internal-timeline {
  padding-left: 8px;
  border-left: 2px solid var(--line);
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
</style>
```

- [ ] **Step 2: 提交**

```bash
git add frontend/src/components/execution/BidReviewAgentBlock.vue
git commit -m "feat(execution): add BidReviewAgentBlock component"
```

---

## Task 4: SubAgentExecutorBlock 组件

**Files:**
- Create: `frontend/src/components/execution/SubAgentExecutorBlock.vue`
- Depend: BidReviewAgentBlock.vue (Task 3)

- [ ] **Step 1: 创建 SubAgentExecutorBlock.vue**

```vue
<script setup lang="ts">
import BidReviewAgentBlock from './BidReviewAgentBlock.vue'

interface ToolCall {
  name: string
  arguments: Record<string, any>
}

interface ToolResult {
  name: string
  result: any
}

interface CheckItem {
  name: string
  status: 'done' | 'run' | 'wait' | 'fail'
}

interface Finding {
  type: 'crit' | 'major' | 'pass'
  text: string
}

interface TimelineStep {
  step_number: number
  step_type: string
  content: string
  timestamp: Date
  tool_args?: { tool_calls?: ToolCall[] }
  tool_result?: { tool_results?: ToolResult[] }
}

interface SubAgentData {
  agentId: string
  title: string
  ruleFile: string
  status: 'done' | 'running' | 'wait'
  checkItems?: CheckItem[]
  steps: TimelineStep[]
  findings: Finding[]
}

interface Props {
  agents: SubAgentData[]
}

defineProps<Props>()
</script>

<template>
  <div class="sub-agent-executor-block">
    <div class="executor-header">
      <div class="executor-icon">
        <svg viewBox="0 0 11 11" fill="none">
          <circle cx="5.5" cy="5.5" r="4" stroke="var(--purple)" stroke-width="1.2"/>
          <path d="M5.5 3.5v2.5l1.5 1" stroke="var(--purple)" stroke-width="1.1" stroke-linecap="round"/>
        </svg>
      </div>
      <span class="executor-title">SubAgentExecutor</span>
      <span class="executor-count">{{ agents.length }} 个子代理</span>
    </div>
    <div class="executor-body">
      <BidReviewAgentBlock
        v-for="agent in agents"
        :key="agent.agentId"
        :agent-id="agent.agentId"
        :title="agent.title"
        :rule-file="agent.ruleFile"
        :status="agent.status"
        :check-items="agent.checkItems"
        :steps="agent.steps"
        :findings="agent.findings"
      />
    </div>
  </div>
</template>

<style scoped>
.sub-agent-executor-block {
  margin-bottom: 16px;
}

.executor-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 14px;
  background: var(--purple-bg);
  border: 1px solid var(--purple-dim);
  border-radius: var(--r2);
  margin-bottom: 8px;
}

.executor-icon {
  width: 20px;
  height: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.executor-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--purple);
}

.executor-count {
  margin-left: auto;
  font-size: 11px;
  color: var(--purple);
  opacity: 0.7;
}

.executor-body {
  padding-left: 16px;
  border-left: 2px solid var(--purple-dim);
}
</style>
```

- [ ] **Step 2: 提交**

```bash
git add frontend/src/components/execution/SubAgentExecutorBlock.vue
git commit -m "feat(execution): add SubAgentExecutorBlock component"
```

---

## Task 5: 重写 LeftPane.vue

**Files:**
- Modify: `frontend/src/components/execution/LeftPane.vue`
- Depend: 所有组件已完成 (Task 1-4)

- [ ] **Step 1: 重写 LeftPane.vue**

保留现有 props 接口，新增 `subAgentTimelines` prop 用于 BidReviewAgent 内部时间线数据。主要变更：

1. 移除旧的 `TodoList`、`MasterOutputBlock`、`SubAgentTimeline` 引用
2. 引入新的 `AgentTimelineItem`、`BidReviewAgentBlock`、`SubAgentExecutorBlock`
3. 时间线按垂直嵌套结构展示

```vue
<script setup lang="ts">
import { computed } from 'vue'
import AgentTimelineItem from './AgentTimelineItem.vue'
import BidReviewAgentBlock from './BidReviewAgentBlock.vue'
import SubAgentExecutorBlock from './SubAgentExecutorBlock.vue'

// ... 保留现有类型定义和 props ...

// BidReviewAgent 模式检测：没有 todos 但有 steps
const isBidReviewAgentMode = computed(() => {
  return (!props.todos || props.todos.length === 0) && props.steps.length > 0
})

// 主代理步骤
const masterSteps = computed(() => {
  return props.steps.filter(s => s.step_type !== 'observation')
})

// 观察列表
const observations = computed(() => {
  return props.steps.filter(s => s.step_type === 'observation')
})

// 子代理列表（现有逻辑保留）
const subAgents = computed(() => {
  if (props.todos && props.todos.length > 0) {
    return props.todos.map((todo, idx) => ({
      agentId: `A${idx + 1}`,
      title: todo.rule_doc_name.replace('.md', ''),
      ruleFile: `${todo.rule_doc_name} · ${todo.check_items?.length || 0} 个检查项`,
      checkItems: todo.check_items?.map(item => ({
        name: item.title,
        status: mapCheckItemStatus(item.status)
      })) || [],
      status: mapSubAgentStatus(todo.status),
      findings: todo.result?.findings?.map((f: any) => ({
        type: f.severity === 'critical' ? 'crit' as const : f.severity === 'major' ? 'major' as const : 'pass' as const,
        text: f.is_compliant ? '通过' : `${f.severity || '问题'}: ${f.explanation || ''}`
      })) || [],
      steps: []  // 从 subAgentTimelines 获取
    }))
  }
  return []
})

// SubAgentExecutor 模式：多个 BidReviewAgent
const hasSubAgentExecutor = computed(() => {
  return subAgents.value.length > 0
})

// 辅助函数保留
function mapStatus(status: string): 'done' | 'running' | 'wait' { ... }
function mapCheckItemStatus(status: string): 'done' | 'run' | 'wait' | 'fail' { ... }
function mapSubAgentStatus(status: string): 'done' | 'running' | 'wait' { ... }
function formatTime(date: Date): string { ... }
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

    <!-- BidReviewAgent 模式 -->
    <div v-if="isBidReviewAgentMode" class="phase-block">
      <div class="phase-label">BidReviewAgent 执行</div>
      <BidReviewAgentBlock
        v-for="agent in subAgents"
        :key="agent.agentId"
        :agent-id="agent.agentId"
        :title="agent.title"
        :rule-file="agent.ruleFile"
        :status="agent.status"
        :check-items="agent.checkItems"
        :steps="observations"
        :findings="agent.findings"
      />
    </div>

    <!-- MasterAgent 模式 -->
    <template v-if="!isBidReviewAgentMode && (phase === 'running' || phase === 'completed')">
      <!-- MasterAgent 时间线 -->
      <div class="phase-block">
        <div class="phase-label">MasterAgent · 规则解析</div>
        <div class="master-timeline">
          <AgentTimelineItem
            v-for="step in masterSteps"
            :key="step.step_number"
            :step-number="step.step_number"
            :step-type="step.step_type as any"
            :content="step.content"
            :timestamp="step.timestamp"
            :tool-calls="step.tool_args?.tool_calls"
            :tool-results="step.tool_result?.tool_results"
          />
        </div>
      </div>

      <!-- SubAgentExecutor -->
      <div v-if="hasSubAgentExecutor" class="phase-block">
        <SubAgentExecutorBlock :agents="subAgents" />
      </div>
    </template>

    <!-- 合并阶段 -->
    <div v-if="!isBidReviewAgentMode && phase === 'completed'" class="phase-block">
      <div class="phase-label">合并与质检阶段</div>
      <div class="merge-block">
        <div class="merge-status">
          <span class="merge-icon">✓</span>
          <span>MasterAgent 已汇总所有子代理结果</span>
        </div>
      </div>
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
/* 复用现有样式，保持视觉一致 */
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

.master-timeline {
  padding-left: 8px;
  border-left: 2px solid var(--purple-dim);
}

.merge-block {
  background: var(--green-bg);
  border: 1px solid var(--green-dim);
  border-radius: var(--r2);
  padding: 14px;
}

.merge-status {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12px;
  color: var(--green);
}

.merge-icon {
  font-size: 16px;
}

/* 复用现有样式... */
</style>
```

- [ ] **Step 2: 提交**

```bash
git add frontend/src/components/execution/LeftPane.vue
git commit -m "refactor(execution): rewrite LeftPane with nested vertical timeline"
```

---

## 自检清单

- [ ] FindingsBar 组件符合现有视觉风格
- [ ] AgentTimelineItem 支持 master/observation/tool_call/thought 四种类型
- [ ] BidReviewAgentBlock 可折叠，保留现有动画
- [ ] SubAgentExecutorBlock 正确缩进子代理
- [ ] LeftPane 正确整合所有组件
- [ ] 保留所有 CSS 变量引用
- [ ] 空状态、错误状态正常显示

---

**Plan complete and saved to `docs/superpowers/plans/2026-04-13-leftpane-timeline-implementation.md`**

**Two execution options:**

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
