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
    error.value = e instanceof Error ? e.message : '登录失败'
  }
}
</script>

<template>
  <div class="auth-container">
    <div class="auth-card">
      <h1>标书审查智能体</h1>
      <h2>登录</h2>

      <form @submit.prevent="handleLogin">
        <div class="form-group">
          <label for="username">用户名</label>
          <input
            id="username"
            v-model="username"
            type="text"
            required
            autocomplete="username"
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
          />
        </div>

        <div v-if="error" class="error">{{ error }}</div>

        <button type="submit" :disabled="authStore.loading">
          {{ authStore.loading ? '登录中...' : '登录' }}
        </button>
      </form>

      <p class="switch-auth">
        还没有账号？<router-link to="/register">注册</router-link>
      </p>
    </div>
  </div>
</template>

<style scoped>
.auth-container {
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 100vh;
  background: var(--bg);
}

.auth-card {
  background: var(--bg1);
  padding: 2rem;
  border-radius: 12px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.15);
  width: 100%;
  max-width: 400px;
}

h1 {
  text-align: center;
  color: var(--blue);
  margin-bottom: 0.5rem;
  font-size: 1.5rem;
}

h2 {
  text-align: center;
  color: var(--text);
  margin-bottom: 1.5rem;
  font-weight: 500;
}

.form-group {
  margin-bottom: 1rem;
}

label {
  display: block;
  margin-bottom: 0.5rem;
  color: var(--sub);
}

input {
  width: 100%;
  padding: 0.75rem;
  border: 1px solid var(--line);
  border-radius: 4px;
  font-size: 1rem;
  background: var(--bg2);
  color: var(--text);
}

input:focus {
  outline: none;
  border-color: var(--blue);
  box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.15);
}

button {
  width: 100%;
  padding: 0.75rem;
  background: var(--blue);
  color: var(--white);
  border: none;
  border-radius: 6px;
  font-size: 1rem;
  cursor: pointer;
  margin-top: 1rem;
  font-weight: 500;
  transition: background-color 0.2s ease, transform 0.1s ease;
}

button:disabled {
  opacity: 0.7;
  cursor: not-allowed;
}

button:hover:not(:disabled) {
  background: var(--blue-dim);
}

button:active:not(:disabled) {
  transform: scale(0.98);
}

.error {
  color: var(--red);
  margin-top: 1rem;
  text-align: center;
}

.switch-auth {
  text-align: center;
  margin-top: 1rem;
  color: var(--sub);
}

.switch-auth a {
  color: var(--blue);
  text-decoration: none;
  font-weight: 500;
}

.switch-auth a:hover {
  text-decoration: underline;
}
</style>
