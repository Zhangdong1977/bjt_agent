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
          <th>Severity</th>
          <th>Status</th>
          <th>Requirement</th>
          <th>Bid Content</th>
          <th>Suggestion</th>
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
              {{ finding.is_compliant ? 'Compliant' : 'Non-Compliant' }}
            </span>
          </td>
          <td class="requirement-cell">{{ finding.requirement_content }}</td>
          <td class="bid-content-cell">{{ finding.bid_content || 'N/A' }}</td>
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
  background: #f5f5f5;
}

th {
  text-align: left;
  padding: 0.75rem;
  border-bottom: 2px solid #ddd;
  color: #333;
  font-weight: 600;
}

td {
  padding: 0.75rem;
  border-bottom: 1px solid #eee;
  vertical-align: top;
}

tr:hover {
  background: #fafafa;
}

tr.non-compliant {
  background: #fff5f5;
}

tr.non-compliant:hover {
  background: #fff0f0;
}

.compliance-badge {
  padding: 0.25rem 0.5rem;
  border-radius: 4px;
  font-size: 0.75rem;
  font-weight: 500;
}

.compliance-badge.compliant {
  background: #68d391;
  color: white;
}

.compliance-badge.non-compliant {
  background: #fc8181;
  color: white;
}

.requirement-cell,
.bid-content-cell,
.suggestion-cell {
  max-width: 250px;
  word-break: break-word;
}

.suggestion-cell {
  color: #667eea;
  font-style: italic;
}
</style>
