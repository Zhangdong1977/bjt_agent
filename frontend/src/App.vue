<script setup lang="ts">
import { onMounted, computed } from 'vue'
import { RouterView } from 'vue-router'
import { ConfigProvider, theme } from 'ant-design-vue'
import { useAuthStore } from '@/stores/auth'
import { useTheme } from '@/composables/useTheme'

const authStore = useAuthStore()
const { theme: appTheme, initTheme } = useTheme()

onMounted(() => {
  authStore.initialize()
  initTheme()
})

const isDark = computed(() => appTheme.value === 'dark')

const antdTheme = computed(() => ({
  token: {
    colorPrimary: '#4f6ef7',
    borderRadius: 8,
    fontFamily: "'DM Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
    fontSize: 14,
  },
  algorithm: isDark.value ? theme.darkAlgorithm : theme.defaultAlgorithm,
}))
</script>

<template>
  <ConfigProvider :theme="antdTheme">
    <RouterView />
  </ConfigProvider>
</template>

<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:ital,opsz,wght@0,9..40,400;0,9..40,500;0,9..40,600;0,9..40,700;1,9..40,400&family=Plus+Jakarta+Sans:wght@500;600;700&display=swap');
@import '@/assets/themes/themes.css';
@import '@/assets/themes/common.css';

* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

body {
  font-family: 'DM Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  background: var(--bg);
  color: var(--text);
  line-height: 1.6;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  text-rendering: optimizeSpeed;
}

h1, h2, h3, h4, h5, h6 {
  font-family: 'Plus Jakarta Sans', 'DM Sans', sans-serif;
  font-weight: 700;
  letter-spacing: -0.02em;
}

#app {
  min-height: 100vh;
}

/* 全局焦点样式 */
:focus-visible {
  outline: 2px solid var(--blue);
  outline-offset: 2px;
}

/* 平滑滚动 */
html {
  scroll-behavior: smooth;
}
</style>
