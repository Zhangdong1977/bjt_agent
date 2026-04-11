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