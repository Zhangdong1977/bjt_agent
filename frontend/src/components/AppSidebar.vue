<script setup lang="ts">
import { useRouter, useRoute } from 'vue-router'
import { computed, ref, watch } from 'vue'
import { FileSearchOutlined, HistoryOutlined, ExperimentOutlined } from '@ant-design/icons-vue'
import { useAuthStore } from '@/stores/auth'
const router = useRouter()
const route = useRoute()
const authStore = useAuthStore()

const allMenuItems = [
  { key: '/home/check', label: '标书检查', icon: FileSearchOutlined, internalOnly: false },
  { key: '/home/history', label: '历史标书', icon: HistoryOutlined, internalOnly: false },
  { key: '/home/experience', label: '标书复盘', icon: ExperimentOutlined, internalOnly: true },
]

// 标书复盘仅内部用户可见
const menuItems = computed(() =>
  allMenuItems.filter((item) => !item.internalOnly || authStore.isInteriorUser)
)

const selectedKeys = ref<string[]>([route.path])

watch(() => route.path, (newPath) => {
  selectedKeys.value = [newPath]
})

function navigate(path: string) {
  router.push(path)
}
</script>

<template>
  <nav class="sidebar">
    <div class="sidebar-section-label">导航</div>
    <ul class="sidebar-menu">
      <li
        v-for="item in menuItems"
        :key="item.key"
        :class="['sidebar-item', { 'sidebar-item--active': selectedKeys.includes(item.key) }]"
        @click="navigate(item.key)"
      >
        <span class="sidebar-item__indicator"></span>
        <component :is="item.icon" class="sidebar-item__icon" />
        <span class="sidebar-item__label">{{ item.label }}</span>
      </li>
    </ul>
  </nav>
</template>

<style scoped>
.sidebar {
  padding: 16px 0;
  height: 100%;
}

.sidebar-section-label {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--muted);
  padding: 0 20px;
  margin-bottom: 8px;
}

.sidebar-menu {
  list-style: none;
  padding: 0;
  margin: 0;
}

.sidebar-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 20px;
  cursor: pointer;
  transition: all 0.2s ease;
  position: relative;
  color: var(--sub);
  font-size: 0.875rem;
  font-weight: 500;
}

.sidebar-item:hover {
  background: var(--bg3);
  color: var(--text);
}

.sidebar-item__indicator {
  position: absolute;
  left: 0;
  top: 50%;
  transform: translateY(-50%) scaleY(0);
  width: 3px;
  height: 20px;
  border-radius: 0 3px 3px 0;
  background: var(--blue);
  transition: transform 0.2s ease;
}

.sidebar-item--active {
  color: var(--blue);
  background: var(--blue-bg);
}

.sidebar-item--active .sidebar-item__indicator {
  transform: translateY(-50%) scaleY(1);
}

.sidebar-item--active:hover {
  background: var(--blue-bg);
  color: var(--blue);
}

.sidebar-item__icon {
  font-size: 16px;
  flex-shrink: 0;
}

.sidebar-item__label {
  white-space: nowrap;
}
</style>
