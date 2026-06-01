<script setup lang="ts">
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import AppSidebar from './AppSidebar.vue'
import ThemeToggle from './ThemeToggle.vue'

const router = useRouter()
const authStore = useAuthStore()

function logout() {
  authStore.logout()
  router.push({ name: 'login' })
}
</script>

<template>
  <a-layout class="app-layout">
    <!-- 顶部品牌色条 -->
    <div class="brand-bar"></div>

    <a-layout-header class="app-header">
      <div class="header-left">
        <img src="/logo.ico" alt="标书审查智能体" class="header-logo-icon" />
        <h1 class="header-title">标书审查智能体</h1>
      </div>
      <div class="header-right">
        <ThemeToggle />
        <div class="header-divider"></div>
        <span class="header-username">{{ authStore.user?.username }}</span>
        <button class="logout-btn" @click="logout">退出</button>
      </div>
    </a-layout-header>
    <a-layout class="app-body">
      <a-layout-sider width="220" class="app-sider" :trigger="null">
        <AppSidebar />
      </a-layout-sider>
      <a-layout-content class="app-content">
        <router-view />
      </a-layout-content>
    </a-layout>
  </a-layout>
</template>

<style scoped>
.app-layout {
  min-height: 100vh;
}

/* 顶部 2px 品牌色条 */
.brand-bar {
  height: 2px;
  background: linear-gradient(90deg, var(--blue), var(--purple));
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  z-index: 100;
}

.app-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: var(--bg1);
  padding: 0 24px;
  height: 56px;
  line-height: 56px;
  box-shadow: var(--shadow-sm);
  position: fixed;
  top: 2px;
  left: 0;
  right: 0;
  z-index: 99;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 10px;
}

.header-logo-icon {
  flex-shrink: 0;
  width: 28px;
  height: 28px;
  object-fit: contain;
}

.header-title {
  font-family: 'Plus Jakarta Sans', 'DM Sans', sans-serif;
  color: var(--bright);
  font-size: 1.1rem;
  font-weight: 700;
  margin: 0;
  letter-spacing: -0.02em;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.header-divider {
  width: 1px;
  height: 20px;
  background: var(--line);
}

.header-username {
  color: var(--sub);
  font-size: 0.8125rem;
  font-weight: 500;
}

.logout-btn {
  background: none;
  border: none;
  color: var(--muted);
  font-size: 0.8125rem;
  cursor: pointer;
  padding: 4px 8px;
  border-radius: var(--r-sm);
  transition: all 0.2s ease;
  font-family: inherit;
}

.logout-btn:hover {
  color: var(--red);
  background: var(--red-bg);
}

.app-body {
  margin-top: 58px; /* 56px header + 2px brand bar */
}

.app-sider {
  background: var(--bg2);
  position: fixed;
  top: 58px;
  left: 0;
  bottom: 0;
  overflow-y: auto;
}

.app-content {
  margin-left: 220px;
  padding: 24px;
  background: var(--bg);
  min-height: calc(100vh - 58px);
  overflow: auto;
}

@media (max-width: 767px) {
  .app-content {
    margin-left: 0;
    padding: 16px;
  }

  .app-sider {
    display: none;
  }

  .header-title {
    font-size: 0.95rem;
  }
}
</style>
