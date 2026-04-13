<script setup lang="ts">
import { computed } from 'vue'
import { useTheme } from '@/composables/useTheme'

const { theme, toggleTheme } = useTheme()

const icon = computed(() => theme.value === 'dark' ? '☀️' : '🌙')
const label = computed(() => theme.value === 'dark' ? '切换亮色模式' : '切换暗色模式')
</script>

<template>
  <button
    class="theme-toggle"
    @click="toggleTheme"
    :title="label"
    :aria-label="label"
  >
    <span class="theme-icon">{{ icon }}</span>
  </button>
</template>

<style scoped>
.theme-toggle {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  border: 1px solid var(--line);
  background: var(--bg2);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s ease;
  color: var(--text);
  font-size: 16px;
}

.theme-toggle:hover {
  background: var(--bg3);
  border-color: var(--purple);
  transform: scale(1.05);
}

.theme-toggle:active {
  transform: scale(0.95);
}

.theme-icon {
  line-height: 1;
}
</style>
