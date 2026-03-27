import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { authApi, getAccessToken, clearTokens } from '@/api/client'
import type { User } from '@/types'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<User | null>(null)
  const loading = ref(false)
  const initialized = ref(false)

  const isAuthenticated = computed(() => !!user.value)

  async function initialize() {
    if (initialized.value) return
    if (!getAccessToken()) {
      initialized.value = true
      return
    }
    try {
      user.value = await authApi.getMe()
    } catch {
      clearTokens()
    } finally {
      initialized.value = true
    }
  }

  async function login(username: string, password: string) {
    loading.value = true
    try {
      await authApi.login(username, password)
      user.value = await authApi.getMe()
    } finally {
      loading.value = false
    }
  }

  async function register(username: string, email: string, password: string) {
    loading.value = true
    try {
      await authApi.register(username, email, password)
      await login(username, password)
    } finally {
      loading.value = false
    }
  }

  function logout() {
    authApi.logout()
    user.value = null
  }

  return {
    user,
    loading,
    initialized,
    isAuthenticated,
    initialize,
    login,
    register,
    logout
  }
})
