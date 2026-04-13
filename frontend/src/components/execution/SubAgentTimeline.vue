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
