<script setup lang="ts">
import { computed } from 'vue'

interface Finding {
  type: 'crit' | 'major' | 'minor' | 'pass'
  text: string
}

interface Props {
  findings: Finding[]
}

const props = defineProps<Props>()

// Filter out pass-type findings and empty texts
const filteredFindings = computed(() => {
  return props.findings.filter(f => f.type !== 'pass' && f.text.trim().length > 0)
})

function getFindingClass(type: 'crit' | 'major' | 'minor' | 'pass') {
  const map: Record<'crit' | 'major' | 'minor' | 'pass', string> = {
    crit: 'ft-crit',
    major: 'ft-major',
    minor: 'ft-minor',
    pass: 'ft-pass'
  }
  return map[type] || 'ft-info'
}

function truncateText(text: string, maxLen = 60): string {
  if (text.length <= maxLen) return text
  // Find the last sentence boundary within limit
  const boundaries = ['。', '！', '？', '. ', '! ', '? ', '；', '; ']
  for (const boundary of boundaries) {
    const idx = text.lastIndexOf(boundary, maxLen)
    if (idx > maxLen * 0.5) {  // At least 50% of maxLen
      return text.slice(0, idx + 1) + '...'
    }
  }
  // Fallback: cut at word boundary (space)
  const spaceIdx = text.lastIndexOf(' ', maxLen)
  if (spaceIdx > maxLen * 0.7) {
    return text.slice(0, spaceIdx) + '...'
  }
  return text.slice(0, maxLen) + '...'
}
</script>

<template>
  <div class="findings-bar">
    <span
      v-for="(finding, idx) in filteredFindings"
      :key="idx"
      :class="['finding-tag', getFindingClass(finding.type)]"
      :title="finding.text"
    >
      {{ truncateText(finding.text, 55) }}
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
.ft-minor { background: var(--blue-bg); border-color: var(--blue-dim); color: var(--blue); }
.ft-pass { background: var(--green-bg); border-color: var(--green-dim); color: var(--green); }
</style>
