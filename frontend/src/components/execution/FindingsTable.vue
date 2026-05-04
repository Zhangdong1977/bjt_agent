<script setup lang="ts">
import { ref } from 'vue'

interface Finding {
  id: string
  requirement_key: string
  requirement_content: string
  bid_content: string | null
  is_compliant: boolean
  severity: string
  location_page: number | null
  location_line: number | null
  suggestion: string | null
  explanation: string | null
}

defineProps<{
  findings: Finding[]
}>()

const expandedRows = ref<Set<string>>(new Set())

function toggleRow(id: string) {
  const next = new Set(expandedRows.value)
  if (next.has(id)) {
    next.delete(id)
  } else {
    next.add(id)
  }
  expandedRows.value = next
}

function getSeverityLabel(severity: string): string {
  const map: Record<string, string> = {
    critical: '严重',
    major: '重要',
    minor: '一般'
  }
  return map[severity] || severity
}
</script>

<template>
  <div class="findings-table-wrapper">
    <div class="findings-table-title">审查结果明细</div>
    <div class="findings-table-scroll">
      <table class="findings-table">
        <thead>
          <tr>
            <th class="col-idx">#</th>
            <th class="col-key">检查项</th>
            <th class="col-status">检查结果</th>
            <th class="col-severity">严重程度</th>
          </tr>
        </thead>
        <tbody>
          <template v-for="(finding, idx) in findings" :key="finding.id">
            <tr
              :class="['finding-row', { 'non-compliant': !finding.is_compliant, expandable: !finding.is_compliant }]"
              @click="!finding.is_compliant && toggleRow(finding.id)"
            >
              <td class="col-idx">{{ idx + 1 }}</td>
              <td class="col-key">
                <div class="req-content">{{ finding.requirement_content }}</div>
              </td>
              <td class="col-status">
                <span :class="['status-badge', finding.is_compliant ? 'pass' : 'fail']">
                  {{ finding.is_compliant ? '通过' : '不通过' }}
                </span>
              </td>
              <td class="col-severity">
                <span v-if="!finding.is_compliant" :class="['severity-badge', `sev-${finding.severity}`]">
                  {{ getSeverityLabel(finding.severity) }}
                </span>
                <span v-else class="severity-dash">-</span>
              </td>
            </tr>
            <tr v-if="!finding.is_compliant && expandedRows.has(finding.id)" class="detail-row">
              <td colspan="4">
                <div class="detail-content">
                  <div v-if="finding.bid_content" class="detail-field">
                    <span class="detail-label">应标内容:</span>
                    <span>{{ finding.bid_content }}</span>
                  </div>
                  <div v-if="finding.explanation" class="detail-field">
                    <span class="detail-label">说明:</span>
                    <span>{{ finding.explanation }}</span>
                  </div>
                  <div v-if="finding.suggestion" class="detail-field">
                    <span class="detail-label">建议:</span>
                    <span>{{ finding.suggestion }}</span>
                  </div>
                  <div v-if="finding.location_page" class="detail-field">
                    <span class="detail-label">位置:</span>
                    <span>第 {{ finding.location_page }} 页{{ finding.location_line ? `, 第 ${finding.location_line} 行` : '' }}</span>
                  </div>
                </div>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.findings-table-wrapper {
  background: var(--bg1);
  border-radius: var(--r);
  padding: 12px;
  margin-top: 14px;
}

.findings-table-title {
  font-size: 11px;
  font-weight: 500;
  color: var(--sub);
  margin-bottom: 10px;
}

.findings-table-scroll {
  max-height: 400px;
  overflow-y: auto;
}

.findings-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}

.findings-table thead th {
  font-size: 10px;
  font-weight: 600;
  color: var(--muted);
  text-align: left;
  padding: 6px 8px;
  border-bottom: 1px solid var(--line);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.findings-table tbody tr.finding-row {
  border-bottom: 1px solid var(--line);
  transition: background 0.15s;
}

.findings-table tbody tr.finding-row:hover {
  background: var(--bg2);
}

.findings-table tbody tr.finding-row.non-compliant {
  background: var(--red-bg);
}

.findings-table tbody tr.finding-row.non-compliant:hover {
  background: var(--red-bg);
  filter: brightness(0.97);
}

.findings-table tbody tr.finding-row.expandable {
  cursor: pointer;
}

.col-idx {
  width: 32px;
  text-align: center;
  color: var(--muted);
  padding: 8px 6px;
}

.col-key {
  padding: 8px;
}

.col-status {
  width: 72px;
  text-align: center;
  padding: 8px 6px;
}

.col-severity {
  width: 72px;
  text-align: center;
  padding: 8px 6px;
}

.req-content {
  word-break: break-word;
  line-height: 1.5;
  color: var(--text);
}

.status-badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 3px;
  font-size: 11px;
  font-weight: 500;
}

.status-badge.pass {
  background: var(--green-bg);
  border: 1px solid var(--green-dim);
  color: var(--green);
}

.status-badge.fail {
  background: var(--red-bg);
  border: 1px solid var(--red-dim);
  color: var(--red);
}

.severity-badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 3px;
  font-size: 11px;
  font-weight: 500;
}

.sev-critical {
  background: var(--red-bg);
  border: 1px solid var(--red-dim);
  color: var(--red);
}

.sev-major {
  background: var(--amber-bg);
  border: 1px solid var(--amber-dim);
  color: var(--amber);
}

.sev-minor {
  background: var(--blue-bg);
  border: 1px solid var(--blue-dim);
  color: var(--blue);
}

.severity-dash {
  color: var(--muted);
}

.detail-row td {
  padding: 0 8px 8px;
  border-bottom: 1px solid var(--line);
}

.detail-content {
  background: var(--bg2);
  border-radius: var(--r);
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.detail-field {
  font-size: 12px;
  line-height: 1.5;
  color: var(--text);
  word-break: break-word;
}

.detail-label {
  font-weight: 500;
  color: var(--sub);
  margin-right: 4px;
}
</style>
