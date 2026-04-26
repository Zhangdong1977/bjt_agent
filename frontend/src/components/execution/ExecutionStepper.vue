<script setup lang="ts">
defineProps<{
  phase: 'pending' | 'running' | 'completed' | 'failed'
}>()

const steps = [
  { key: 'pending', label: '等待开始' },
  { key: 'running', label: '审查中' },
  { key: 'completed', label: '已完成' }
]

function getStepClass(stepKey: string, currentPhase: string) {
  const phaseOrder = ['pending', 'running', 'completed', 'failed']
  const currentIndex = phaseOrder.indexOf(currentPhase)
  const stepIndex = phaseOrder.indexOf(stepKey)

  if (currentPhase === 'failed') {
    // All steps are done, last one is failed
    if (stepKey === 'completed') return 's-done'
    if (stepKey === 'running') return 's-done'
    if (stepKey === 'pending') return 's-done'
    return 's-wait'
  }

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
    <!-- Failed state indicator -->
    <div v-if="phase === 'failed'" class="step step-failed">
      <div class="step-n">✗</div>
      <span class="step-label">失败</span>
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
  background: var(--blue-bg);
  border-color: var(--blue-dim);
  color: var(--blue);
  animation: pulse 1.4s ease-in-out infinite;
}

.s-wait .step-n {
  background: var(--bg2);
  border-color: var(--line2);
  color: var(--muted);
}

.step-failed .step-n {
  background: var(--red-bg);
  border-color: var(--red-dim);
  color: var(--red);
}

.step-label {
  font-size: 11px;
  white-space: nowrap;
}

.s-done .step-label {
  color: var(--green);
}

.s-active .step-label {
  color: var(--blue);
}

.s-wait .step-label {
  color: var(--muted);
}

.step-failed .step-label {
  color: var(--red);
}

.s-done::after {
  background: var(--green-dim);
}

.s-active::after {
  background: var(--line2);
}

@keyframes pulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.6; transform: scale(0.9); }
}
</style>