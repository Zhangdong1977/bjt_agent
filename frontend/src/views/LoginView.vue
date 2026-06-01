<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const authStore = useAuthStore()

const username = ref('')
const password = ref('')
const error = ref('')

async function handleLogin() {
  error.value = ''
  try {
    await authStore.login(username.value, password.value)
    router.push({ name: 'home' })
  } catch (e: unknown) {
    if (e && typeof e === 'object' && 'response' in e) {
      const resp = (e as any).response
      error.value = resp?.data?.detail || '登录失败，请检查用户名和密码'
    } else {
      error.value = e instanceof Error ? e.message : '登录失败，请检查用户名和密码'
    }
  }
}
</script>

<template>
  <div class="auth-page">
    <!-- 背景装饰 -->
    <div class="auth-bg">
      <div class="auth-bg__orb auth-bg__orb--1"></div>
      <div class="auth-bg__orb auth-bg__orb--2"></div>
    </div>

    <div class="auth-card">
      <!-- Logo 区域 -->
      <div class="auth-logo">
        <img src="/logo.ico" alt="标书审查智能体" width="48" height="48" />
      </div>

      <h1 class="auth-title">标书审查智能体</h1>
      <p class="auth-subtitle">登录您的标捷通账户</p>

      <form @submit.prevent="handleLogin" class="auth-form">
        <div class="form-group">
          <label for="username">用户名</label>
          <input
            id="username"
            v-model="username"
            type="text"
            required
            autocomplete="username"
            placeholder="请输入用户名"
          />
        </div>

        <div class="form-group">
          <label for="password">密码</label>
          <input
            id="password"
            v-model="password"
            type="password"
            required
            autocomplete="current-password"
            placeholder="请输入密码"
          />
        </div>

        <div v-if="error" class="error-msg">{{ error }}</div>

        <button type="submit" :disabled="authStore.loading" class="auth-btn">
          <span v-if="authStore.loading" class="btn-loading"></span>
          {{ authStore.loading ? '登录中...' : '登录' }}
        </button>
      </form>
    </div>
  </div>
</template>

<style scoped>
.auth-page {
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 100vh;
  background: var(--bg);
  position: relative;
  overflow: hidden;
}

/* 背景装饰光斑 */
.auth-bg {
  position: absolute;
  inset: 0;
  pointer-events: none;
  overflow: hidden;
}

.auth-bg__orb {
  position: absolute;
  border-radius: 50%;
  filter: blur(80px);
  opacity: 0.15;
}

.auth-bg__orb--1 {
  width: 400px;
  height: 400px;
  background: var(--blue);
  top: -100px;
  right: -100px;
}

.auth-bg__orb--2 {
  width: 300px;
  height: 300px;
  background: var(--purple);
  bottom: -80px;
  left: -80px;
}

/* 卡片 */
.auth-card {
  position: relative;
  background: var(--bg1);
  padding: 2.5rem;
  border-radius: var(--r-lg);
  box-shadow: var(--shadow-lg);
  width: 100%;
  max-width: 400px;
  z-index: 1;
}

/* Logo */
.auth-logo {
  display: flex;
  justify-content: center;
  margin-bottom: 1.25rem;
}

/* 标题 */
.auth-title {
  text-align: center;
  color: var(--bright);
  font-size: 1.5rem;
  font-weight: 700;
  margin-bottom: 0.25rem;
  letter-spacing: -0.02em;
}

.auth-subtitle {
  text-align: center;
  color: var(--muted);
  font-size: 0.875rem;
  margin-bottom: 2rem;
}

/* 表单 */
.auth-form {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.form-group {
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
}

label {
  font-size: 0.8125rem;
  font-weight: 600;
  color: var(--sub);
  letter-spacing: 0.01em;
}

input {
  width: 100%;
  padding: 0.75rem 1rem;
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  font-size: 0.9375rem;
  background: var(--bg2);
  color: var(--text);
  transition: border-color 0.2s ease, box-shadow 0.2s ease;
  font-family: inherit;
}

input::placeholder {
  color: var(--muted);
  opacity: 0.6;
}

input:focus {
  outline: none;
  border-color: var(--blue);
  box-shadow: 0 0 0 3px var(--blue-bg);
}

/* 错误消息 */
.error-msg {
  color: var(--red);
  font-size: 0.8125rem;
  text-align: center;
  padding: 0.5rem;
  background: var(--red-bg);
  border-radius: var(--r-sm);
  border: 1px solid var(--red-dim);
}

/* 登录按钮 */
.auth-btn {
  width: 100%;
  padding: 0.75rem;
  background: var(--blue);
  color: #fff;
  border: none;
  border-radius: var(--r-sm);
  font-size: 0.9375rem;
  font-weight: 600;
  cursor: pointer;
  margin-top: 0.5rem;
  transition: all 0.2s ease;
  font-family: inherit;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
}

.auth-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.auth-btn:hover:not(:disabled) {
  filter: brightness(1.08);
  box-shadow: 0 4px 12px var(--blue-bg);
}

.auth-btn:active:not(:disabled) {
  transform: scale(0.98);
}

/* 加载动画 */
.btn-loading {
  width: 16px;
  height: 16px;
  border: 2px solid rgba(255, 255, 255, 0.3);
  border-top-color: #fff;
  border-radius: 50%;
  animation: spin 0.6s linear infinite;
}
</style>
