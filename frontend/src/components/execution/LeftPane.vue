<script setup lang="ts">
import { computed } from 'vue'
import AgentTimelineItem from './AgentTimelineItem.vue'
import BidReviewAgentBlock from './BidReviewAgentBlock.vue'
import SubAgentExecutorBlock from './SubAgentExecutorBlock.vue'

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

interface TodoItemState {
  id: string
  rule_doc_name: string
  check_items: CheckItemState[]
  status: 'pending' | 'running' | 'completed' | 'failed'
  result?: {
    findings: any[]
  }
}

interface CheckItemState {
  id: string
  title: string
  status: 'pending' | 'running' | 'completed' | 'failed'
}

interface Props {
  phase: 'pending' | 'running' | 'completed' | 'failed'
  steps: TimelineStep[]
  errorMessage?: string | null
  todos?: TodoItemState[]
}

const props = defineProps<Props>()

// BidReviewAgent 模式检测
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

// 子代理列表
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
      steps: []  // 内部时间线数据
    }))
  }
  return []
})

const hasSubAgentExecutor = computed(() => {
  return subAgents.value.length > 0
})

// BidReviewAgent 模式：创建合成代理对象来展示观察结果
const bidReviewAgentData = computed(() => {
  if (isBidReviewAgentMode.value && observations.value.length > 0) {
    return [{
      agentId: 'BR',
      title: 'BidReviewAgent',
      ruleFile: '审查执行中',
      status: mapSubAgentStatus(props.phase === 'completed' ? 'completed' : props.phase === 'running' ? 'running' : 'pending'),
      checkItems: [],
      steps: observations.value,
      findings: []
    }]
  }
  return []
})

function mapStatus(status: string): 'done' | 'running' | 'wait' {
  if (status === 'completed') return 'done'
  if (status === 'running') return 'running'
  return 'wait'
}

function mapCheckItemStatus(status: string): 'done' | 'run' | 'wait' | 'fail' {
  if (status === 'completed') return 'done'
  if (status === 'running') return 'run'
  if (status === 'failed') return 'fail'
  return 'wait'
}

function mapSubAgentStatus(status: string): 'done' | 'running' | 'wait' {
  if (status === 'completed') return 'done'
  if (status === 'running') return 'running'
  return 'wait'
}

function formatTime(date: Date): string {
  return new Date(date).toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false
  })
}
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
        v-for="agent in bidReviewAgentData"
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

/* Reuse existing styles from old LeftPane */
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

.output-body {
  padding: 12px 14px;
}

.wait-status {
  font-size: 12px;
  color: var(--muted);
}

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

.chip {
  font-size: 10px;
  font-weight: 500;
  padding: 2px 7px;
  border-radius: 3px;
  border: 1px solid;
}

.chip-wait { background: var(--bg3); border-color: var(--line2); color: var(--muted); }
</style>