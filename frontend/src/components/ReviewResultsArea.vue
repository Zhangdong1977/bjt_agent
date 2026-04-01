<script setup lang="ts">
import { computed } from 'vue'
import { useProjectStore } from '@/stores/project'

const projectStore = useProjectStore()

const reviewResults = computed(() => projectStore.reviewResults)
const hasResults = computed(() => reviewResults.value && reviewResults.value.findings && reviewResults.value.findings.length > 0)

function getSeverityClass(severity: string) {
  switch (severity) {
    case 'critical': return 'severity-critical'
    case 'major': return 'severity-major'
    case 'minor': return 'severity-minor'
    default: return ''
  }
}
</script>

<template>
  <div class="review-results-area">
    <div v-if="hasResults" class="summary">
      <div class="summary-item">
        <span class="summary-value">{{ reviewResults.summary.total_requirements }}</span>
        <span class="summary-label">总计</span>
      </div>
      <div class="summary-item success">
        <span class="summary-value">{{ reviewResults.summary.compliant }}</span>
        <span class="summary-label">合规</span>
      </div>
      <div class="summary-item error">
        <span class="summary-value">{{ reviewResults.summary.non_compliant }}</span>
        <span class="summary-label">不合规</span>
      </div>
      <div class="summary-item critical">
        <span class="summary-value">{{ reviewResults.summary.critical }}</span>
        <span class="summary-label">严重</span>
      </div>
      <div class="summary-item major">
        <span class="summary-value">{{ reviewResults.summary.major }}</span>
        <span class="summary-label">主要</span>
      </div>
      <div class="summary-item minor">
        <span class="summary-value">{{ reviewResults.summary.minor }}</span>
        <span class="summary-label">次要</span>
      </div>
    </div>

    <div v-if="hasResults" class="findings-list">
      <div
        v-for="finding in reviewResults.findings"
        :key="finding.id"
        :class="['finding-card', { 'non-compliant': !finding.is_compliant }]"
      >
        <div class="finding-header">
          <span :class="['severity-badge', getSeverityClass(finding.severity)]">
            {{ finding.severity }}
          </span>
          <span :class="['compliance-badge', finding.is_compliant ? 'compliant' : 'non-compliant']">
            {{ finding.is_compliant ? '合规' : '不合规' }}
          </span>
        </div>
        <div class="finding-body">
          <p class="requirement"><strong>要求:</strong> {{ finding.requirement_content }}</p>
          <p class="bid-content"><strong>应标内容:</strong> {{ finding.bid_content }}</p>
          <p v-if="finding.explanation" class="explanation">{{ finding.explanation }}</p>
          <p v-if="finding.suggestion && !finding.is_compliant" class="suggestion">
            <strong>建议:</strong> {{ finding.suggestion }}
          </p>
        </div>
      </div>
    </div>

    <div v-if="!hasResults" class="no-results">
      <p>暂无审查结果</p>
    </div>
  </div>
</template>

<style scoped>
.review-results-area {
  padding: 1rem;
}

.summary {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 1rem;
  margin-bottom: 2rem;
}

.summary-item {
  text-align: center;
  padding: 1rem;
  background: #f5f5f5;
  border-radius: 8px;
}

.summary-value {
  display: block;
  font-size: 2rem;
  font-weight: bold;
  color: #333;
}

.summary-label {
  color: #666;
  font-size: 0.9rem;
}

.summary-item.success .summary-value { color: #68d391; }
.summary-item.error .summary-value { color: #e53e3e; }
.summary-item.critical .summary-value { color: #c53030; }
.summary-item.major .summary-value { color: #dd6b20; }
.summary-item.minor .summary-value { color: #d69e2e; }

.findings-list {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.finding-card {
  border: 1px solid #ddd;
  border-radius: 8px;
  padding: 1rem;
}

.finding-card.non-compliant {
  border-color: #fc8181;
  background: #fff5f5;
}

.finding-header {
  display: flex;
  gap: 0.5rem;
  margin-bottom: 0.5rem;
}

.severity-badge, .compliance-badge {
  padding: 0.25rem 0.75rem;
  border-radius: 4px;
  font-size: 0.8rem;
  font-weight: 500;
}

.severity-critical { background: #c53030; color: white; }
.severity-major { background: #dd6b20; color: white; }
.severity-minor { background: #d69e2e; color: white; }

.compliance-badge.compliant { background: #68d391; color: white; }
.compliance-badge.non-compliant { background: #fc8181; color: white; }

.finding-body p {
  margin: 0.5rem 0;
  color: #333;
}

.explanation {
  color: #666;
  font-style: italic;
}

.suggestion {
  color: #6366f1;
}

.no-results {
  text-align: center;
  padding: 2rem;
  color: #666;
}
</style>
