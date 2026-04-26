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
    <div class="output-header transparent">
      <div class="output-header-icon teal">
        <svg viewBox="0 0 11 11" fill="none">
          <path d="M2 5.5h7M5.5 2l3.5 3.5-3.5 3.5" stroke="var(--teal)" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/>
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
.output-header.transparent {
  background: transparent;
  padding: 0 0 10px 0;
  border-bottom: 1px solid var(--line);
}
.output-header-icon.teal {
  background: var(--teal-bg);
  border: 1px solid var(--teal-dim);
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
.chip-run { background: var(--blue-bg); border-color: var(--blue-dim); color: var(--blue); }
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
.m-dot.md-run { background: var(--blue); animation: blink 1s infinite; }
@keyframes blink { 0%,100% { opacity: 1 } 50% { opacity: 0 } }

.merge-step-arr { color: var(--dim); font-size: 11px; }
</style>
