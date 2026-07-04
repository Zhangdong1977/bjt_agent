<script setup lang="ts">
import { useRouter, useRoute } from 'vue-router'
import { computed, ref, watch } from 'vue'
import { useAuthStore } from '@/stores/auth'
import iconCheck from '@/assets/images/ui/common-icon-check.png'
import iconCalendar from '@/assets/images/ui/common-icon-calendar.png'
import iconUser from '@/assets/images/ui/common-icon-user.png'
import iconDashboard from '@/assets/images/ui/common-icon-dashboard.png'
import illustrationLuxury from '@/assets/images/ui/common-illustration-luxury.png'
const router = useRouter()
const route = useRoute()
const authStore = useAuthStore()

const allMenuItems = [
  { key: '/home/check', label: '标书检查', subtitle: '创建新项目，上传标书', icon: iconCheck, internalOnly: false },
  { key: '/home/history', label: '历史标书', subtitle: '查看历史审查记录与报告', icon: iconCalendar, internalOnly: false },
  { key: '/home/profile', label: '用户中心', subtitle: '管理账号权限，维护个人信息', icon: iconUser, internalOnly: false },
  { key: '/home/experience', label: '标书复盘', subtitle: '复盘项目，优化审查质量', icon: iconDashboard, internalOnly: true },
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
        <img :src="item.icon" alt="" class="sidebar-item__icon" />
        <span class="sidebar-item__text">
          <span class="sidebar-item__label">{{ item.label }}</span>
          <span class="sidebar-item__subtitle">{{ item.subtitle }}</span>
        </span>
      </li>
    </ul>
    <div class="sidebar-illustration">
      <img :src="illustrationLuxury" alt="" />
    </div>
  </nav>
</template>

<style scoped>
.sidebar {
  padding: 16px 0 0;
  height: 100%;
  display: flex;
  flex-direction: column;
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
  flex: 1 1 auto;
}

.sidebar-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 20px;
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
  background: #D7041A;
  transition: transform 0.2s ease;
}

.sidebar-item--active {
  color: #D7041A;
  background: #fff5f6;
}

.sidebar-item--active .sidebar-item__indicator {
  transform: translateY(-50%) scaleY(1);
}

.sidebar-item--active:hover {
  background: #fff5f6;
  color: #D7041A;
}

.sidebar-item__icon {
  width: 20px;
  height: 20px;
  object-fit: contain;
  flex-shrink: 0;
}

.sidebar-item__text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  line-height: 1.5;
  min-width: 0;
}

.sidebar-item__label {
  white-space: nowrap;
}

.sidebar-item__subtitle {
  font-size: 11px;
  color: #999;
  font-weight: 400;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* active 时副标题保持灰色（不随主标题变红），保持层次 */

.sidebar-illustration {
  flex-shrink: 0;
  line-height: 0; /* 消除 inline 图片下方基线缝隙 */
}

.sidebar-illustration img {
  width: 100%;
  height: auto;
  display: block;
}
</style>
