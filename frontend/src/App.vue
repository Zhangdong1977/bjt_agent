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
    colorPrimary: '#a78bfa',
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
@import url('https://fonts.googleapis.com/css2?family=Open+Sans:wght@400;500;600;700&family=Poppins:wght@500;600;700&display=swap');
@import '@/assets/themes/themes.css';
@import '@/assets/themes/common.css';

* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

body {
  font-family: 'Open Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  background: var(--bg);
  color: var(--text);
  line-height: 1.6;
}

h1, h2, h3, h4, h5, h6 {
  font-family: 'Poppins', sans-serif;
  font-weight: 600;
}

#app {
  min-height: 100vh;
}
</style>