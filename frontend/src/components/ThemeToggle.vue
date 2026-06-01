<script setup lang="ts">
import { computed } from 'vue'
import { useTheme } from '@/composables/useTheme'

const { theme, toggleTheme } = useTheme()

const isDark = computed(() => theme.value === 'dark')
const label = computed(() => theme.value === 'dark' ? '切换亮色模式' : '切换暗色模式')
</script>

<template>
  <button
    class="theme-toggle"
    @click="toggleTheme"
    :title="label"
    :aria-label="label"
  >
    <!-- 太阳图标（亮色模式） -->
    <svg v-if="isDark" class="toggle-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <circle cx="12" cy="12" r="5"/>
      <line x1="12" y1="1" x2="12" y2="3"/>
      <line x1="12" y1="21" x2="12" y2="23"/>
      <line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/>
      <line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/>
      <line x1="1" y1="12" x2="3" y2="12"/>
      <line x1="21" y1="12" x2="23" y2="12"/>
      <line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/>
      <line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>
    </svg>
    <!-- 月亮图标（暗色模式） -->
    <svg v-else class="toggle-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>
    </svg>
  </button>
</template>

<style scoped>
.theme-toggle {
  width: 32px;
  height: 32px;
  border-radius: var(--r-sm);
  border: 1px solid var(--line);
  background: var(--bg2);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s ease;
  color: var(--sub);
  padding: 0;
}

.theme-toggle:hover {
  background: var(--bg3);
  border-color: var(--line2);
  color: var(--text);
}

.theme-toggle:active {
  transform: scale(0.95);
}

.toggle-icon {
  transition: transform 0.3s ease;
}

.theme-toggle:hover .toggle-icon {
  transform: rotate(30deg);
}
</style>
