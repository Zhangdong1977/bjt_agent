<script setup lang="ts">
import { useRouter, useRoute } from 'vue-router'
import { ref, watch } from 'vue'
import { FileSearchOutlined, HistoryOutlined, BookOutlined } from '@ant-design/icons-vue'

const router = useRouter()
const route = useRoute()

const menuItems = [
  { key: '/home/check', label: '标书检查', icon: FileSearchOutlined },
  { key: '/home/history', label: '历史标书', icon: HistoryOutlined },
  { key: '/home/knowledge', label: '知识库', icon: BookOutlined },
]

const selectedKeys = ref<string[]>([route.path])

watch(() => route.path, (newPath) => {
  selectedKeys.value = [newPath]
})

function navigate(path: string) {
  router.push(path)
}

function handleMenuClick(e: { key: string }) {
  navigate(e.key)
}
</script>

<template>
  <a-menu
    v-model:selectedKeys="selectedKeys"
    mode="inline"
    theme="light"
    class="app-sidebar"
    @click="handleMenuClick"
  >
    <a-menu-item v-for="item in menuItems" :key="item.key">
      <template #icon>
        <component :is="item.icon" />
      </template>
      {{ item.label }}
    </a-menu-item>
  </a-menu>
</template>

<style scoped>
.app-sidebar {
  height: 100%;
  background: #fafafa;
  border-right: 1px solid #e8e8e8;
}

.app-sidebar :deep(.ant-menu-item) {
  margin: 4px 8px;
  border-radius: 8px;
}

.app-sidebar :deep(.ant-menu-item-selected) {
  background: linear-gradient(90deg, rgba(99, 102, 241, 0.1) 0%, transparent 100%);
  border-left: 2px solid #6366f1;
}
</style>
