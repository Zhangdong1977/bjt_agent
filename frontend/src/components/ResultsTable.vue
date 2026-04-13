<script setup lang="ts">
import SeverityBadge from './SeverityBadge.vue'
import type { ReviewResult } from '@/types'

defineProps<{
  findings: ReviewResult[]
}>()
</script>

<template>
  <div class="results-table">
    <table>
      <thead>
        <tr>
          <th>严重程度</th>
          <th>状态</th>
          <th>要求</th>
          <th>应标内容</th>
          <th>建议</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="finding in findings"
          :key="finding.id"
          :class="{ 'non-compliant': !finding.is_compliant }"
        >
          <td>
            <SeverityBadge :severity="finding.severity" />
          </td>
          <td>
            <span :class="['compliance-badge', finding.is_compliant ? 'compliant' : 'non-compliant']">
              {{ finding.is_compliant ? '合规' : '不合规' }}
            </span>
          </td>
          <td class="requirement-cell">{{ finding.requirement_content }}</td>
          <td class="bid-content-cell">{{ finding.bid_content || '无' }}</td>
          <td class="suggestion-cell">
            <template v-if="!finding.is_compliant && finding.suggestion">
              {{ finding.suggestion }}
            </template>
            <template v-else>-</template>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
.results-table {
  width: 100%;
  overflow-x: auto;
}

table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.9rem;
}

thead {
  background: var(--bg3);
}

th {
  text-align: left;
  padding: 0.75rem;
  border-bottom: 2px solid var(--line);
  color: var(--text);
  font-weight: 600;
}

td {
  padding: 0.75rem;
  border-bottom: 1px solid var(--line);
  vertical-align: top;
}

tr:hover {
  background: var(--bg3);
}

tr.non-compliant {
  background: var(--red-bg);
}

tr.non-compliant:hover {
  background: var(--red-bg);
}

.compliance-badge {
  padding: 0.25rem 0.5rem;
  border-radius: 4px;
  font-size: 0.75rem;
  font-weight: 500;
}

.compliance-badge.compliant {
  background: var(--green);
  color: var(--white);
}

.compliance-badge.non-compliant {
  background: var(--red);
  color: var(--white);
}

.requirement-cell,
.bid-content-cell,
.suggestion-cell {
  max-width: 250px;
  word-break: break-word;
}

.suggestion-cell {
  color: var(--purple);
  font-style: italic;
}
</style>
