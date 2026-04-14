<script setup lang="ts">
import { computed } from 'vue'
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
  type: 'crit' | 'major' | 'minor' | 'pass'
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
  subAgentStepsMap?: Record<string, TimelineStep[]>
}

const props = defineProps<Props>()

const agentsWithSteps = computed(() => {
  if (!props.subAgentStepsMap) return props.agents
  const stepsValues = Object.values(props.subAgentStepsMap)
  return props.agents.map((agent, idx) => ({
    ...agent,
    steps: stepsValues[idx] || agent.steps
  }))
})
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
      <span class="executor-count">{{ agentsWithSteps.length }} 个子代理</span>
    </div>
    <div class="executor-body">
      <BidReviewAgentBlock
        v-for="agent in agentsWithSteps"
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
