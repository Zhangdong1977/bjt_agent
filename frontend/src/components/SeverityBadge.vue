<script setup lang="ts">
defineProps<{
  severity: 'critical' | 'major' | 'minor'
}>()

function getSeverityClass(severity: string) {
  switch (severity) {
    case 'critical':
      return 'severity-critical'
    case 'major':
      return 'severity-major'
    case 'minor':
      return 'severity-minor'
    default:
      return ''
  }
}
</script>

<template>
  <span :class="['severity-badge', getSeverityClass(severity)]">
    {{ severity }}
  </span>
</template>

<style scoped>
.severity-badge {
  padding: 0.25rem 0.75rem;
  border-radius: 4px;
  font-size: 0.8rem;
  font-weight: 500;
  text-transform: capitalize;
}

.severity-critical {
  background: #c53030;
  color: white;
}

.severity-major {
  background: #dd6b20;
  color: white;
}

.severity-minor {
  background: #d69e2e;
  color: white;
}
</style>
