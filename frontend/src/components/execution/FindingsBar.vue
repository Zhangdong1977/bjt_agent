<script setup lang="ts">
interface Finding {
  type: 'crit' | 'major' | 'pass'
  text: string
}

interface Props {
  findings: Finding[]
}

defineProps<Props>()

function getFindingClass(type: 'crit' | 'major' | 'pass') {
  const map: Record<'crit' | 'major' | 'pass', string> = {
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
