<script setup lang="ts">
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import AppSidebar from './AppSidebar.vue'

const router = useRouter()
const authStore = useAuthStore()

function logout() {
  authStore.logout()
  router.push({ name: 'login' })
}
</script>

<template>
  <a-layout class="app-layout">
    <a-layout-header class="app-header">
      <div class="header-left">
        <h1 class="logo">标书审查智能体</h1>
      </div>
      <div class="header-right">
        <span class="username">{{ authStore.user?.username }}</span>
        <a-button type="text" danger @click="logout">退出</a-button>
      </div>
    </a-layout-header>
    <a-layout>
      <a-layout-sider width="200" class="app-sider">
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

.app-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: white;
  padding: 0 24px;
  border-bottom: 1px solid #e8e8e8;
  height: 64px;
}

.header-left .logo {
  color: #6366f1;
  font-size: 1.25rem;
  font-weight: 600;
  margin: 0;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 16px;
}

.username {
  color: #666;
}

.app-sider {
  background: #fafafa;
}

.app-content {
  padding: 24px;
  background: #f5f3ff;
  min-height: calc(100vh - 64px);
}
</style>
