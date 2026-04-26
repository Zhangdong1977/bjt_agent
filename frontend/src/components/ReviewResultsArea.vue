<script setup lang="ts">
import { computed } from 'vue'
import type { ReviewResponse } from '@/types'

const props = defineProps<{
  reviewResults: ReviewResponse | null | undefined
}>()

const hasFindings = computed(() => props.reviewResults && props.reviewResults.findings && props.reviewResults.findings.length > 0)

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
    <div v-if="hasFindings" class="summary">
      <div class="summary-item">
        <span class="summary-value">{{ props.reviewResults?.summary.total_requirements }}</span>
        <span class="summary-label">总计</span>
      </div>
      <div class="summary-item success">
        <span class="summary-value">{{ props.reviewResults?.summary.compliant }}</span>
        <span class="summary-label">合规</span>
      </div>
      <div class="summary-item error">
        <span class="summary-value">{{ props.reviewResults?.summary.non_compliant }}</span>
        <span class="summary-label">不合规</span>
      </div>
      <div class="summary-item critical">
        <span class="summary-value">{{ props.reviewResults?.summary.critical }}</span>
        <span class="summary-label">严重</span>
      </div>
      <div class="summary-item major">
        <span class="summary-value">{{ props.reviewResults?.summary.major }}</span>
        <span class="summary-label">主要</span>
      </div>
      <div class="summary-item minor">
        <span class="summary-value">{{ props.reviewResults?.summary.minor }}</span>
        <span class="summary-label">次要</span>
      </div>
    </div>

    <div v-if="hasFindings" class="findings-list">
      <div
        v-for="finding in props.reviewResults?.findings"
        :key="finding?.id"
        :class="['finding-card', { 'non-compliant': !finding?.is_compliant }]"
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

    <div v-if="!hasFindings" class="no-results">
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
  background: var(--bg2);
  border-radius: 8px;
}

.summary-value {
  display: block;
  font-size: 2rem;
  font-weight: bold;
  color: var(--text);
}

.summary-label {
  color: var(--sub);
  font-size: 0.9rem;
}

.summary-item.success .summary-value { color: var(--green); }
.summary-item.error .summary-value { color: var(--red); }
.summary-item.critical .summary-value { color: var(--red); }
.summary-item.major .summary-value { color: var(--amber); }
.summary-item.minor .summary-value { color: var(--amber); }

.findings-list {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.finding-card {
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 1rem;
}

.finding-card.non-compliant {
  border-color: var(--red-dim);
  background: var(--red-bg);
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

.severity-critical { background: var(--red); color: var(--white); }
.severity-major { background: var(--amber); color: var(--white); }
.severity-minor { background: var(--amber); color: var(--white); }

.compliance-badge.compliant { background: var(--green); color: var(--white); }
.compliance-badge.non-compliant { background: var(--red); color: var(--white); }

.finding-body p {
  margin: 0.5rem 0;
  color: var(--text);
}

.explanation {
  color: var(--sub);
  font-style: italic;
}

.suggestion {
  color: var(--blue);
}

.no-results {
  text-align: center;
  padding: 2rem;
  color: var(--sub);
}
</style>
