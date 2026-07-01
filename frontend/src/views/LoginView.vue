<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import loginBgUrl from '@/assets/images/clientLogin/loginBg.jpg'
import loginLeftUrl from '@/assets/images/clientLogin/loginLeft.png'
import logoUrl from '@/assets/images/clientLogin/logo.png'
import { getRegisterUrl } from '@/utils/externalLinks'

const registerUrl = getRegisterUrl()

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
  <main class="login-page" :style="{ backgroundImage: `url(${loginBgUrl})` }">
    <section class="login-shell" aria-label="标捷通账号登录">
      <div class="login-left" aria-hidden="true">
        <img class="brand-logo" :src="logoUrl" alt="" />
        <p class="brand-description">
          <span>标书审查智能体</span>面向投标文件合规性、响应性与风险点进行智能核查，帮助团队快速定位问题、提升标书质量。
        </p>
        <img class="left-visual" :src="loginLeftUrl" alt="" />
      </div>

      <div class="login-right">
        <div class="auth-tabs" role="tablist" aria-label="账号入口">
          <button class="auth-tab auth-tab--active" type="button" role="tab" aria-selected="true">登录</button>
          <a
            class="auth-tab"
            :href="registerUrl"
            role="tab"
            aria-selected="false"
          >
            注册
          </a>
        </div>

        <p class="login-hint">登录后上传投标文件，开始智能审查与问题追踪</p>

        <form class="login-form" @submit.prevent="handleLogin">
          <div class="form-item">
            <input
              id="username"
              v-model.trim="username"
              type="text"
              required
              autocomplete="username"
              aria-label="账号名或手机号"
              placeholder="请输入账号名(手机号)"
            />
          </div>

          <div class="form-item">
            <input
              id="password"
              v-model="password"
              type="password"
              required
              autocomplete="current-password"
              aria-label="登录密码"
              placeholder="请输入登录密码"
            />
          </div>

          <div v-if="error" class="error-msg" role="alert">{{ error }}</div>

          <button type="submit" :disabled="authStore.loading" class="submit-btn">
            <span v-if="authStore.loading" class="btn-loading" aria-hidden="true"></span>
            {{ authStore.loading ? '登 录 中...' : '登 录' }}
          </button>
        </form>
      </div>
    </section>
  </main>
</template>

<style scoped>
.login-page {
  min-height: 100vh;
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background-color: #eef2f7;
  background-repeat: no-repeat;
  background-position: center;
  background-size: cover;
  color: #333;
  font-family: "Microsoft YaHei", "PingFang SC", Arial, sans-serif;
}

.login-shell {
  width: min(1080px, 100%);
  min-height: 720px;
  display: grid;
  grid-template-columns: 1fr 1fr;
  box-shadow: 0 6px 50px rgba(215, 222, 235, 0.5);
}

.login-left {
  position: relative;
  overflow: hidden;
  text-align: center;
  background: rgba(173, 186, 214, 0.36);
  border: 1px solid #d0d7e6;
}

.brand-logo {
  width: 250px;
  height: auto;
  margin-top: 150px;
}

.brand-description {
  margin: 80px auto 0;
  max-width: 460px;
  padding: 0 40px;
  color: #fff;
  font-size: 18px;
  line-height: 40px;
  letter-spacing: 0;
}

.brand-description span {
  color: #dd0f1f;
}

.left-visual {
  position: absolute;
  right: 0;
  bottom: 0;
  width: 100%;
  height: 360px;
  object-fit: cover;
}

.login-right {
  background: #fff;
  padding: 60px 90px 0;
  box-shadow: 0 6px 50px rgba(215, 222, 235, 0.5);
}

.auth-tabs {
  display: flex;
  align-items: center;
  gap: 40px;
}

.auth-tab {
  position: relative;
  height: 40px;
  padding: 0;
  border: 0;
  background: transparent;
  color: #333;
  cursor: pointer;
  font: inherit;
  font-size: 30px;
  font-weight: 500;
  line-height: 40px;
  letter-spacing: 0;
  text-decoration: none;
}

.auth-tab--active::after {
  content: "";
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 2px;
  background: #dd0f1f;
}

.auth-tab:not(.auth-tab--active):hover {
  color: #940e18;
}

.login-hint {
  margin-top: 24px;
  color: #777;
  font-size: 14px;
  line-height: 22px;
  letter-spacing: 0;
}

.login-form {
  margin-top: 58px;
}

.form-item {
  margin-bottom: 40px;
}

input {
  width: 100%;
  height: 40px;
  border: 0;
  border-bottom: 1px solid #e4e6f1;
  border-radius: 0;
  background: #fff;
  color: #333;
  font: inherit;
  font-size: 16px;
  outline: none;
  padding: 0;
  transition: border-color 0.2s ease;
}

input::placeholder {
  color: #999;
  opacity: 1;
}

input:focus {
  border-bottom-color: #dd0f1f;
}

.error-msg {
  margin: -12px 0 22px;
  color: #dd0f1f;
  font-size: 14px;
  line-height: 22px;
  letter-spacing: 0;
}

.submit-btn {
  width: 100%;
  height: 60px;
  margin-top: 80px;
  border: 1px solid #dd0f1f;
  border-radius: 4px;
  background: #dd0f1f;
  color: #fff;
  cursor: pointer;
  font: inherit;
  font-size: 16px;
  letter-spacing: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  transition: background-color 0.2s ease, border-color 0.2s ease;
}

.submit-btn:hover:not(:disabled) {
  background: #c90e1d;
  border-color: #c90e1d;
}

.submit-btn:disabled {
  cursor: not-allowed;
  opacity: 0.65;
}

.btn-loading {
  width: 16px;
  height: 16px;
  border: 2px solid rgba(255, 255, 255, 0.35);
  border-top-color: #fff;
  border-radius: 50%;
  animation: spin 0.6s linear infinite;
}

@media (max-width: 1199px) {
  .login-shell {
    min-height: 650px;
  }

  .brand-logo {
    margin-top: 100px;
  }

  .brand-description {
    margin-top: 95px;
    font-size: 16px;
  }

  .left-visual {
    height: 325px;
  }

  .login-right {
    padding: 50px;
  }

  .login-form {
    margin-top: 68px;
  }

  .submit-btn {
    margin-top: 70px;
  }
}

@media (max-width: 900px) {
  .login-page {
    padding: 16px;
  }

  .login-shell {
    min-height: auto;
    grid-template-columns: 1fr;
    max-width: 540px;
  }

  .login-left {
    min-height: 260px;
  }

  .brand-logo {
    width: 190px;
    margin-top: 38px;
  }

  .brand-description {
    margin-top: 22px;
    padding: 0 28px;
    font-size: 14px;
    line-height: 28px;
  }

  .left-visual {
    display: none;
  }

  .login-right {
    padding: 32px 32px 40px;
  }

  .auth-tab {
    font-size: 24px;
  }

  .login-form {
    margin-top: 36px;
  }

  .form-item {
    margin-bottom: 28px;
  }

  .submit-btn {
    margin-top: 28px;
    height: 48px;
  }
}

@media (max-width: 480px) {
  .login-page {
    padding: 0;
    align-items: stretch;
  }

  .login-shell {
    min-height: 100vh;
    box-shadow: none;
  }

  .login-left {
    min-height: 220px;
  }

  .brand-logo {
    width: 150px;
    margin-top: 36px;
  }

  .brand-description {
    font-size: 12px;
    line-height: 24px;
  }

  .login-right {
    padding: 28px 24px 36px;
  }

  .auth-tabs {
    gap: 30px;
  }

  .auth-tab {
    font-size: 22px;
  }

  input {
    font-size: 15px;
  }
}
</style>
