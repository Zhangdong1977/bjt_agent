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

function getSeverityLabel(severity: string) {
  switch (severity) {
    case 'critical':
      return '严重'
    case 'major':
      return '主要'
    case 'minor':
      return '次要'
    default:
      return severity
  }
}
</script>

<template>
  <span :class="['severity-badge', getSeverityClass(severity)]">
    {{ getSeverityLabel(severity) }}
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
  background: var(--red);
  color: var(--white);
}

.severity-major {
  background: var(--amber);
  color: var(--white);
}

.severity-minor {
  background: var(--amber);
  color: var(--white);
}
</style>
