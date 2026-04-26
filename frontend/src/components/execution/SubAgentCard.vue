<script setup lang="ts">
import { ref, computed } from 'vue'

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
  currentStep?: number
  maxSteps?: number
}>()

const isOpen = ref(false)

function toggle() {
  isOpen.value = !isOpen.value
}

function getFindingClass(type: string) {
  const map: Record<string, string> = {
    crit: 'ft-crit',
    major: 'ft-major',
    pass: 'ft-pass'
  }
  return map[type] || 'ft-info'
}

// Progress calculation based on agent steps
const progress = computed(() => {
  if (props.maxSteps && props.maxSteps > 0 && props.currentStep) {
    return Math.round((props.currentStep / props.maxSteps) * 100)
  }
  if (props.status === 'done') return 100
  if (props.status === 'running') return 50
  return 0
})
</script>

<template>
  <div :class="['agent-card', `ac-${status}`, { open: isOpen }]">
    <div class="agent-card-head" @click="toggle">
      <div class="ac-avt">{{ agentId }}</div>
      <div class="ac-info">
        <div class="ac-title">{{ title }}</div>
        <div class="ac-sub">规则审查</div>
      </div>
      <div class="ac-right">
        <div class="pbar-outer">
          <div class="pbar-inner" :style="{ width: progress + '%' }"></div>
        </div>
        <span :class="['chip', status === 'done' ? 'chip-done' : status === 'running' ? 'chip-run' : 'chip-wait']">
          {{ status === 'done' ? '完成' : status === 'running' ? '执行中' : '等待' }}
        </span>
        <span class="chevron">›</span>
      </div>
    </div>
    <div class="agent-card-body">
      <div class="dep-section">
        <div class="dep-sec-label">规则执行状态</div>
        <div class="dep-chain">
          <div v-if="status === 'running'" class="run-log">
            <span class="log-cursor"></span>
            正在执行规则审查...
          </div>
          <div v-else-if="status === 'done'" class="dep-pill dp-done">
            <span class="dp-dot"></span>
            规则审查完成
          </div>
          <div v-else class="dep-pill dp-wait">
            <span class="dp-dot"></span>
            等待执行
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
.agent-card.ac-running { border-color: var(--blue-dim); }
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
.agent-card.ac-running .agent-card-head { background: var(--blue-bg); }
.agent-card.ac-done .agent-card-head { background: var(--green-bg); }

.ac-avt {
  width: 26px; height: 26px;
  border-radius: 4px;
  display: flex; align-items: center; justify-content: center;
  font-size: 11px; font-weight: 600;
  flex-shrink: 0;
}
.ac-done .ac-avt { background: var(--green-bg); color: var(--green); border: 1px solid var(--green-dim); }
.ac-running .ac-avt { background: var(--blue-bg); color: var(--blue); border: 1px solid var(--blue-dim); }
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
.ac-running .pbar-inner { background: var(--blue); }
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
.dp-run { background: var(--blue-bg); border-color: var(--blue-dim); color: var(--blue); }
.dp-wait { background: var(--bg3); border-color: var(--line2); color: var(--muted); }
.dp-fail { background: var(--red-bg); border-color: var(--red-dim); color: var(--red); }

.dp-dot { width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; }
.dp-done .dp-dot { background: var(--green); }
.dp-run .dp-dot { background: var(--blue); animation: blink 1s infinite; }
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
  background: var(--blue);
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
.chip-run { background: var(--blue-bg); border-color: var(--blue-dim); color: var(--blue); }
.chip-wait { background: var(--bg3); border-color: var(--line2); color: var(--muted); }
</style>
