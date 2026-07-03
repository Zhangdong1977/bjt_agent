import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { authApi, getAccessToken, clearTokens, getTokenClaims } from '@/api/client'
import type { User } from '@/types'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<User | null>(null)
  const loading = ref(false)
  const initialized = ref(false)
  const isInteriorUser = ref(false)
  const concurrency = ref(2)

  const isAuthenticated = computed(() => !!user.value)

  function _restoreClaims() {
    const claims = getTokenClaims()
    isInteriorUser.value = claims.interior_user
    concurrency.value = claims.concurrency
  }

  async function initialize() {
    if (initialized.value) return
    if (!getAccessToken()) {
      initialized.value = true
      return
    }
    try {
      user.value = await authApi.getMe()
      _restoreClaims()
    } catch {
      clearTokens()
    } finally {
      initialized.value = true
    }
  }

  async function login(
    username: string,
    password: string,
    captchaId: string,
    captchaCode: string,
  ) {
    loading.value = true
    try {
      await authApi.login(username, password, captchaId, captchaCode)
      user.value = await authApi.getMe()
      _restoreClaims()
    } finally {
      loading.value = false
    }
  }

  function logout() {
    authApi.logout()
    user.value = null
    isInteriorUser.value = false
    concurrency.value = 2
  }

  return {
    user,
    loading,
    initialized,
    isAuthenticated,
    isInteriorUser,
    concurrency,
    initialize,
    login,
    logout,
  }
})
