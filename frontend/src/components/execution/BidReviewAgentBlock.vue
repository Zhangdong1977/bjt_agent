<script setup lang="ts">
import { ref, computed } from 'vue'
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

interface Props {
  agentId: string
  title: string
  ruleFile: string
  status: 'done' | 'running' | 'wait' | 'fail'
  checkItems?: CheckItem[]
  steps: TimelineStep[]
  findings: Finding[]
  allowExpand?: boolean
  currentStep?: number
  maxSteps?: number
  brainCapacity?: number
}

const props = withDefaults(defineProps<Props>(), {
  currentStep: 0,
  maxSteps: 0,
  brainCapacity: undefined
})

const isOpen = ref(false)

const brainPercent = computed(() => {
  // Use persisted brain capacity from database (for completed/failed agents)
  if (props.brainCapacity !== undefined) return Math.round(props.brainCapacity)
  // Calculate from step progress
  if (props.maxSteps > 0) {
    return Math.min(Math.round((props.currentStep / props.maxSteps) * 100), 100)
  }
  if (props.status === 'running') return 50
  return 0
})

function toggle() {
  if (!props.allowExpand && props.allowExpand !== undefined) return
  isOpen.value = !isOpen.value
}

function getStatusText(status: string) {
  if (status === 'done') return '完成'
  if (status === 'running') return '执行中'
  if (status === 'fail') return '失败'
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
        <div class="brain-cap">
          <span class="brain-label">脑容量</span>
          <div class="brain-pbar-outer">
            <div
              class="brain-pbar-inner"
              :style="{ width: `${brainPercent}%` }"
              :class="{ 'brain-animate': status === 'running' }"
            ></div>
          </div>
          <span class="brain-pct">{{ brainPercent }}%</span>
        </div>
        <span :class="['chip', status === 'done' ? 'chip-done' : status === 'running' ? 'chip-run' : status === 'fail' ? 'chip-fail' : 'chip-wait']">
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

.bra-running { border-color: var(--blue-dim); }
.bra-done { border-color: var(--green-dim); }
.bra-fail { border-color: var(--red-dim); }

.block-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  background: var(--bg2);
  cursor: pointer;
  user-select: none;
}

.bra-running .block-header { background: var(--blue-bg); }
.bra-done .block-header { background: var(--green-bg); }
.bra-fail .block-header { background: var(--red-bg); }

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
.bra-running .bra-avatar { background: var(--blue-bg); color: var(--blue); border: 1px solid var(--blue-dim); }
.bra-wait .bra-avatar { background: var(--bg3); color: var(--muted); border: 1px solid var(--line2); }
.bra-fail .bra-avatar { background: var(--red-bg); color: var(--red); border: 1px solid var(--red-dim); }

.bra-info { flex: 1; min-width: 0; }
.bra-title { font-size: 12px; font-weight: 500; color: var(--bright); }
.bra-sub { font-size: 11px; color: var(--muted); margin-top: 1px; }

.bra-right { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }

.brain-cap {
  display: flex;
  align-items: center;
  gap: 5px;
}

.brain-label {
  font-size: 10px;
  color: var(--muted);
  white-space: nowrap;
}

.bra-running .brain-label { color: var(--blue); }
.bra-done .brain-label { color: var(--green); }

.brain-pbar-outer {
  width: 48px;
  height: 4px;
  background: var(--bg4);
  border-radius: 2px;
  overflow: hidden;
}

.brain-pbar-inner {
  height: 100%;
  border-radius: 2px;
  transition: width 0.5s ease;
}

.brain-animate {
  animation: brain-shine 1.8s ease-in-out infinite;
}

@keyframes brain-shine {
  0% { opacity: 0.8; }
  50% { opacity: 1; }
  100% { opacity: 0.8; }
}

.bra-done .brain-pbar-inner { background: var(--green); }
.bra-running .brain-pbar-inner { background: var(--blue); }
.bra-wait .brain-pbar-inner { background: var(--dim); }
.bra-fail .brain-pbar-inner { background: var(--red); }

.brain-pct {
  font-size: 10px;
  font-weight: 600;
  color: var(--muted);
  min-width: 28px;
  text-align: right;
}

.bra-running .brain-pct { color: var(--blue); }
.bra-done .brain-pct { color: var(--green); }

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
.chip-run { background: var(--blue-bg); border-color: var(--blue-dim); color: var(--blue); }
.chip-wait { background: var(--bg3); border-color: var(--line2); color: var(--muted); }
.chip-fail { background: var(--red-bg); border-color: var(--red-dim); color: var(--red); }
</style>
