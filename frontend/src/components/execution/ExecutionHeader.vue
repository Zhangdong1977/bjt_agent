<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { useTheme } from '@/composables/useTheme'

defineProps<{
  projectName: string
  status: 'running' | 'completed' | 'pending' | 'failed'
}>()

const router = useRouter()
const { theme, toggleTheme } = useTheme()

const isDark = computed(() => theme.value === 'dark')

function goBack() {
  router.back()
}
</script>

<template>
  <div class="execution-header">
    <div class="header-left">
      <button class="back-btn" @click="goBack">← 返回</button>
      <h1 class="project-title">{{ projectName }}</h1>
    </div>
    <div class="header-right">
      <!-- 主题切换按钮 -->
      <button class="theme-toggle" @click="toggleTheme">
        {{ isDark ? '☀️' : '🌙' }}
      </button>
      <!-- 状态徽章 -->
      <span :class="['status-badge', `status-${status}`]">
        <span v-if="status === 'running'" class="live-dot"></span>
        {{ status === 'running' ? '进行中' : status === 'completed' ? '已完成' : status === 'failed' ? '失败' : '等待' }}
      </span>
    </div>
  </div>
</template>

<style scoped>
.execution-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 20px;
  background: var(--bg1);
  border-bottom: 1px solid var(--line);
  height: 56px;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 16px;
}

.back-btn {
  padding: 6px 12px;
  background: transparent;
  border: 1px solid var(--line2);
  border-radius: var(--r);
  color: var(--sub);
  cursor: pointer;
  font-size: 13px;
}

.back-btn:hover {
  background: var(--bg3);
  color: var(--text);
}

.project-title {
  font-size: 15px;
  font-weight: 500;
  color: var(--bright);
}

.header-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.theme-toggle {
  width: 36px;
  height: 36px;
  border-radius: var(--r);
  background: var(--bg3);
  border: 1px solid var(--line2);
  cursor: pointer;
  font-size: 16px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.theme-toggle:hover {
  background: var(--bg4);
}

.status-badge {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 12px;
  border-radius: var(--r);
  font-size: 12px;
  font-weight: 500;
}

.status-running {
  background: var(--purple-bg);
  border: 1px solid var(--purple-dim);
  color: var(--purple);
}

.status-completed {
  background: var(--green-bg);
  border: 1px solid var(--green-dim);
  color: var(--green);
}

.status-failed {
  background: var(--red-bg);
  border: 1px solid var(--red-dim);
  color: var(--red);
}

.status-pending {
  background: var(--bg3);
  border: 1px solid var(--line2);
  color: var(--muted);
}

.live-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--purple);
  animation: blink 1.4s ease-in-out infinite;
}

@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.25; }
}
</style>