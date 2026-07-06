<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { authApi } from '@/api/client'
import AgreementModal from '@/components/AgreementModal.vue'
import AnnouncementMarquee from '@/components/announcement/AnnouncementMarquee.vue'
import illustrationUrl from '@/assets/images/ui/login-illustration.png'
import logoUrl from '@/assets/images/ui/common-logo-white.png'
import iconUser from '@/assets/images/ui/login-input-username.png'
import iconPassword from '@/assets/images/ui/login-input-password.png'
import iconCaptcha from '@/assets/images/ui/login-input-captcha.png'

const router = useRouter()
const authStore = useAuthStore()

// ============ 用户协议 / 隐私政策 ============
// 登录与注册共用同一份勾选状态；未勾选时拦截提交并提示
const acceptedAgreement = ref(false)
const serviceAgreementOpen = ref(false)
const privacyAgreementOpen = ref(false)

// ============ 公共：图形验证码（登录/注册共用一份） ============
const captchaId = ref('')
const captchaImage = ref('')
const loginCaptchaCode = ref('')
const regCaptchaCode = ref('')

const activeTab = ref<'login' | 'register'>('login')

// ============ 登录表单 ============
const username = ref('')
const password = ref('')
const loginError = ref('')

// ============ 注册表单 ============
const regPhone = ref('')
const regNickname = ref('')
const regPassword = ref('')
const regConfirmPassword = ref('')
const regSmsCode = ref('')
const regError = ref('')
const regSuccess = ref('')
const regLoading = ref(false)
const smsSending = ref(false)
const smsCountdown = ref(0)
let smsTimer: ReturnType<typeof setInterval> | null = null

// ============ 密码强度实时校验（与后端 checkPasswordStrength 规则一致）============
// 规则：8-20 位，且大写/小写/数字/符号 4 类中至少包含 3 类
const passwordStrength = computed(() => {
  const pwd = regPassword.value
  if (!pwd) return { ok: false, text: '' }
  if (pwd.length < 8 || pwd.length > 20) {
    return { ok: false, text: '密码长度需在 8 到 20 位' }
  }
  let kinds = 0
  if (/[a-z]/.test(pwd)) kinds += 1
  if (/[A-Z]/.test(pwd)) kinds += 1
  if (/[0-9]/.test(pwd)) kinds += 1
  if (/[^a-zA-Z0-9]/.test(pwd)) kinds += 1
  if (kinds < 3) {
    return { ok: false, text: '密码需含大写/小写/数字/符号中至少 3 类' }
  }
  return { ok: true, text: '' }
})

async function fetchCaptcha() {
  try {
    const captcha = await authApi.getCaptcha()
    captchaId.value = captcha.captcha_id
    captchaImage.value = captcha.image
  } catch {
    captchaImage.value = ''
  }
}

function clearSmsTimer() {
  if (smsTimer) {
    clearInterval(smsTimer)
    smsTimer = null
  }
}

function startSmsCountdown() {
  smsCountdown.value = 60
  clearSmsTimer()
  smsTimer = setInterval(() => {
    smsCountdown.value -= 1
    if (smsCountdown.value <= 0) {
      clearSmsTimer()
    }
  }, 1000)
}

onMounted(fetchCaptcha)
onUnmounted(clearSmsTimer)

function extractDetail(e: unknown, fallback: string): string {
  if (e && typeof e === 'object' && 'response' in e) {
    const resp = (e as any).response
    return resp?.data?.detail || fallback
  }
  return e instanceof Error ? e.message : fallback
}

async function handleLogin() {
  loginError.value = ''
  if (!acceptedAgreement.value) {
    loginError.value = '请先阅读并同意《用户服务协议》和《隐私政策》'
    return
  }
  try {
    await authStore.login(
      username.value,
      password.value,
      captchaId.value,
      loginCaptchaCode.value,
    )
    router.push({ name: 'home' })
  } catch (e: unknown) {
    loginError.value = extractDetail(e, '登录失败，请检查用户名和密码')
    // 登录失败后旧验证码令牌不应复用：清空输入并刷新图片
    loginCaptchaCode.value = ''
    await fetchCaptcha()
  }
}

async function handleSendSms() {
  regError.value = ''
  if (!regPhone.value) {
    regError.value = '请先输入手机号'
    return
  }
  if (!/^1[3-9]\d{9}$/.test(regPhone.value)) {
    regError.value = '手机号格式不正确'
    return
  }
  if (!regCaptchaCode.value) {
    regError.value = '请先输入图形验证码'
    return
  }
  smsSending.value = true
  try {
    await authApi.sendSms(regPhone.value, captchaId.value, regCaptchaCode.value)
    startSmsCountdown()
  } catch (e: unknown) {
    regError.value = extractDetail(e, '验证码发送失败')
    // 图形验证码消费后失效，刷新
    regCaptchaCode.value = ''
    await fetchCaptcha()
  } finally {
    smsSending.value = false
  }
}

async function handleRegister() {
  regError.value = ''
  regSuccess.value = ''
  // 协议勾选拦截：优先于字段校验，让用户先看到协议要求
  if (!acceptedAgreement.value) {
    regError.value = '请先阅读并同意《用户服务协议》和《隐私政策》'
    return
  }
  // 前端基础校验（与后端 schema 互补，提前拦截以省一次往返）
  if (!regPhone.value || !regNickname.value || !regPassword.value || !regSmsCode.value) {
    regError.value = '请填写完整信息'
    return
  }
  if (regPassword.value !== regConfirmPassword.value) {
    regError.value = '两次输入的密码不一致'
    return
  }
  if (regPassword.value.length < 8 || regPassword.value.length > 20) {
    regError.value = '密码长度需在 8 到 20 个字符'
    return
  }
  regLoading.value = true
  try {
    await authApi.register({
      phone: regPhone.value,
      sms_code: regSmsCode.value,
      password: regPassword.value,
      confirm_password: regConfirmPassword.value,
      nickname: regNickname.value,
      captcha_id: captchaId.value,
      captcha_code: regCaptchaCode.value,
    })
    regSuccess.value = '注册成功，请登录'
    // 注册成功：切回登录 tab，预填手机号到用户名，清空注册表单
    username.value = regPhone.value
    activeTab.value = 'login'
    regPhone.value = ''
    regNickname.value = ''
    regPassword.value = ''
    regConfirmPassword.value = ''
    regSmsCode.value = ''
    regCaptchaCode.value = ''
    // 注册会消费图形验证码，刷新供登录用
    await fetchCaptcha()
  } catch (e: unknown) {
    regError.value = extractDetail(e, '注册失败')
    regCaptchaCode.value = ''
    await fetchCaptcha()
  } finally {
    regLoading.value = false
  }
}
</script>

<template>
  <main class="login-page">
    <!-- 顶部跑马灯：展示系统公告（无公告时自动隐藏） -->
    <AnnouncementMarquee />
    <!-- 左侧插画：靠左、高度顶满、比例不变 -->
    <div class="login-art-wrap">
      <img class="login-art" :src="illustrationUrl" alt="" aria-hidden="true" />
      <!-- 品牌 logo：叠加在插画左上角 -->
      <img class="login-logo" :src="logoUrl" alt="标捷通" />
    </div>
    <!-- 登录表单：靠右浮动，与图片右缘重叠产生立体感 -->
    <section class="login-right" aria-label="标捷通账号登录">
      <div class="login-card">
        <div class="auth-tabs" role="tablist" aria-label="账号入口">
          <button
            class="auth-tab"
            :class="{ 'auth-tab--active': activeTab === 'login' }"
            type="button"
            role="tab"
            aria-selected="true"
            @click="activeTab = 'login'"
          >
            登录
          </button>
          <button
            class="auth-tab"
            :class="{ 'auth-tab--active': activeTab === 'register' }"
            type="button"
            role="tab"
            aria-selected="false"
            @click="activeTab = 'register'"
          >
            注册
          </button>
        </div>

        <!-- 登录表单 -->
        <template v-if="activeTab === 'login'">
          <p class="login-hint">登录后上传投标文件，开始智能审查与问题追踪</p>

          <form class="login-form" @submit.prevent="handleLogin">
            <div class="form-item">
              <img class="input-icon" :src="iconUser" alt="" />
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
              <img class="input-icon" :src="iconPassword" alt="" />
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

            <div class="form-item form-item--captcha">
              <img class="input-icon" :src="iconCaptcha" alt="" />
              <input
                id="captcha"
                v-model.trim="loginCaptchaCode"
                type="text"
                inputmode="numeric"
                maxlength="4"
                required
                autocomplete="off"
                aria-label="图形验证码"
                placeholder="请输入验证码"
              />
              <img
                v-if="captchaImage"
                class="captcha-img"
                :src="captchaImage"
                alt="图形验证码"
                title="看不清？点击刷新"
                @click="fetchCaptcha"
              />
              <span v-else class="captcha-placeholder" @click="fetchCaptcha">点击加载</span>
            </div>

            <div v-if="loginError" class="error-msg" role="alert">{{ loginError }}</div>

            <div class="agreement-row">
              <label class="agreement-label">
                <input v-model="acceptedAgreement" type="checkbox" class="agreement-check" />
                <span>我已阅读并同意</span>
              </label>
              <a
                href="/agreements/user-service"
                class="agreement-link"
                @click.prevent="serviceAgreementOpen = true"
              >《用户服务协议》</a>
              <span>和</span>
              <a
                href="/agreements/privacy"
                class="agreement-link"
                @click.prevent="privacyAgreementOpen = true"
              >《隐私政策》</a>
            </div>

            <button type="submit" :disabled="authStore.loading" class="submit-btn">
              <span v-if="authStore.loading" class="btn-loading" aria-hidden="true"></span>
              {{ authStore.loading ? '登 录 中...' : '登 录' }}
            </button>
          </form>
        </template>

        <!-- 注册表单 -->
        <template v-else>
          <p class="login-hint">注册即开通 AI 标书检查功能，注册后即可登录</p>

          <form class="login-form" @submit.prevent="handleRegister">
            <div class="form-item">
              <img class="input-icon" :src="iconUser" alt="" />
              <input
                v-model.trim="regPhone"
                type="text"
                inputmode="numeric"
                maxlength="11"
                required
                autocomplete="off"
                aria-label="手机号"
                placeholder="请输入手机号"
              />
            </div>

            <div class="form-item">
              <img class="input-icon" :src="iconUser" alt="" />
              <input
                v-model.trim="regNickname"
                type="text"
                required
                autocomplete="off"
                aria-label="昵称"
                placeholder="请输入昵称"
              />
            </div>

            <div class="form-item form-item--captcha">
              <img class="input-icon" :src="iconCaptcha" alt="" />
              <input
                v-model.trim="regCaptchaCode"
                type="text"
                inputmode="numeric"
                maxlength="4"
                required
                autocomplete="off"
                aria-label="图形验证码"
                placeholder="请输入图形验证码"
              />
              <img
                v-if="captchaImage"
                class="captcha-img"
                :src="captchaImage"
                alt="图形验证码"
                title="看不清？点击刷新"
                @click="fetchCaptcha"
              />
              <span v-else class="captcha-placeholder" @click="fetchCaptcha">点击加载</span>
            </div>

            <div class="form-item form-item--sms">
              <img class="input-icon" :src="iconCaptcha" alt="" />
              <input
                v-model.trim="regSmsCode"
                type="text"
                inputmode="numeric"
                maxlength="6"
                required
                autocomplete="one-time-code"
                aria-label="短信验证码"
                placeholder="请输入短信验证码"
              />
              <button
                type="button"
                class="sms-btn"
                :disabled="smsCountdown > 0 || smsSending"
                @click="handleSendSms"
              >
                {{ smsCountdown > 0 ? `${smsCountdown}s 后重发` : (smsSending ? '发送中...' : '获取验证码') }}
              </button>
            </div>

            <div class="form-item">
              <img class="input-icon" :src="iconPassword" alt="" />
              <input
                v-model="regPassword"
                type="password"
                required
                autocomplete="new-password"
                aria-label="设置密码"
                placeholder="设置密码(8-20位,含大小写/数字/符号3类)"
              />
            </div>
            <div v-if="regPassword && !passwordStrength.ok" class="pwd-hint" role="note">
              {{ passwordStrength.text }}
            </div>

            <div class="form-item">
              <img class="input-icon" :src="iconPassword" alt="" />
              <input
                v-model="regConfirmPassword"
                type="password"
                required
                autocomplete="new-password"
                aria-label="确认密码"
                placeholder="请再次输入密码"
              />
            </div>

            <div v-if="regError" class="error-msg" role="alert">{{ regError }}</div>
            <div v-if="regSuccess" class="success-msg" role="status">{{ regSuccess }}</div>

            <div class="agreement-row">
              <label class="agreement-label">
                <input v-model="acceptedAgreement" type="checkbox" class="agreement-check" />
                <span>我已阅读并同意</span>
              </label>
              <a
                href="/agreements/user-service"
                class="agreement-link"
                @click.prevent="serviceAgreementOpen = true"
              >《用户服务协议》</a>
              <span>和</span>
              <a
                href="/agreements/privacy"
                class="agreement-link"
                @click.prevent="privacyAgreementOpen = true"
              >《隐私政策》</a>
            </div>

            <button type="submit" :disabled="regLoading" class="submit-btn">
              <span v-if="regLoading" class="btn-loading" aria-hidden="true"></span>
              {{ regLoading ? '注 册 中...' : '注 册' }}
            </button>
          </form>
        </template>
      </div>
    </section>

    <!-- 协议模态框：登录/注册共用 -->
    <AgreementModal v-model:open="serviceAgreementOpen" type="service" />
    <AgreementModal v-model:open="privacyAgreementOpen" type="privacy" />

    <!-- 底部版权 -->
    <footer class="login-copyright">
      2026 版权所有 郑州迪维勒普科技有限公司　版本号:V1.0.0
    </footer>
  </main>
</template>

<style scoped>
.login-page {
  min-height: 100vh;
  width: 100%;
  position: relative;
  background-color: #f1f4f7;
  color: #333;
  font-family: "Microsoft YaHei", "PingFang SC", Arial, sans-serif;
  display: flex;
  align-items: stretch;
  justify-content: flex-start;
  overflow: hidden;
}

/* ============ 左侧插画：靠左、高度顶满、比例不变 ============ */
.login-art-wrap {
  flex: 0 0 auto;
  position: relative;
  height: 100vh;
  display: block;
}

.login-art {
  height: 100vh;
  width: auto;
  object-fit: contain;
  object-position: left top;
  display: block;
  user-select: none;
}

/* 品牌 logo：绝对定位叠在插画左上角 */
.login-logo {
  position: absolute;
  top: 36px;
  left: 48px;
  height: 40px;
  width: auto;
  object-fit: contain;
  user-select: none;
  pointer-events: none;
  z-index: 1;
}

/* ============ 右侧表单区：卡片覆盖图片右缘（立体感） ============ */
.login-right {
  flex: 0 0 auto;
  width: 760px;
  /* 关键：整块向左平移，使卡片左缘覆盖图片右缘约 346px */
  margin-left: -360px;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  padding-right: 6vw;
  position: relative;
  z-index: 2;
}

.login-card {
  width: 420px;
  max-width: 92vw;
  background: #fff;
  border-radius: 16px;
  padding: 48px 44px 40px;
  box-shadow: 0 18px 50px rgba(0, 0, 0, 0.16);
}

/* ============ 底部版权 ============ */
.login-copyright {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 36px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: rgba(85, 85, 85, 0.78);
  font-size: 12px;
  letter-spacing: 0.5px;
  background: linear-gradient(180deg, rgba(241, 244, 247, 0) 0%, rgba(241, 244, 247, 0.85) 60%);
  pointer-events: none;
  z-index: 3;
}

.auth-tabs {
  display: flex;
  align-items: center;
  gap: 40px;
  margin-bottom: 14px;
}

.auth-tab {
  position: relative;
  height: 42px;
  padding: 0;
  border: 0;
  background: transparent;
  color: #999;
  cursor: pointer;
  font-family: inherit;
  font-size: 26px;
  font-weight: 500;
  line-height: 42px;
  letter-spacing: 1px;
  text-decoration: none;
  transition: color 0.2s ease;
}

.auth-tab--active {
  color: #333;
}

.auth-tab--active::after {
  content: "";
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 3px;
  border-radius: 2px;
  background: #D7041A;
}

.auth-tab:not(.auth-tab--active):hover {
  color: #B80015;
}

.login-hint {
  color: #888;
  font-size: 13px;
  line-height: 22px;
  letter-spacing: 0;
}

.login-form {
  margin-top: 36px;
}

.form-item {
  position: relative;
  display: flex;
  align-items: center;
  margin-bottom: 26px;
  border-bottom: 1px solid #e4e6f1;
  transition: border-color 0.2s ease;
}

.form-item:focus-within {
  border-bottom-color: #D7041A;
}

.input-icon {
  flex-shrink: 0;
  width: 22px;
  height: 22px;
  object-fit: contain;
  margin-right: 12px;
  opacity: 0.85;
}

.form-item input {
  flex: 1;
  width: 100%;
  height: 42px;
  border: 0;
  background: transparent;
  color: #333;
  font-family: inherit;
  font-size: 15px;
  outline: none;
  padding: 0;
}

.form-item input::placeholder {
  color: #b0b0b0;
}

.form-item--captcha input {
  flex: 1;
}

.captcha-img {
  flex-shrink: 0;
  width: 96px;
  height: 34px;
  border: 1px solid #e4e6f1;
  border-radius: 4px;
  background: #fff;
  object-fit: cover;
  cursor: pointer;
  user-select: none;
}

.captcha-placeholder {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 96px;
  height: 34px;
  border: 1px solid #e4e6f1;
  border-radius: 4px;
  color: #b0b0b0;
  font-size: 12px;
  cursor: pointer;
  user-select: none;
}

/* ============ 注册表单：短信验证码输入项 ============ */
.form-item--sms input {
  padding-right: 108px;
}

.sms-btn {
  position: absolute;
  right: 0;
  top: 50%;
  transform: translateY(-50%);
  flex-shrink: 0;
  height: 30px;
  padding: 0 10px;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: #D7041A;
  cursor: pointer;
  font-family: inherit;
  font-size: 13px;
  white-space: nowrap;
}

.sms-btn:hover:not(:disabled) {
  text-decoration: underline;
}

.sms-btn:disabled {
  color: #b0b0b0;
  cursor: not-allowed;
}

.error-msg {
  margin: -10px 0 18px;
  color: #D7041A;
  font-size: 13px;
  line-height: 22px;
}

.pwd-hint {
  margin: -6px 0 14px;
  color: #999;
  font-size: 12px;
  line-height: 20px;
}

.success-msg {
  margin: -10px 0 18px;
  color: #52c41a;
  font-size: 13px;
  line-height: 22px;
}

/* ============ 协议勾选行 ============ */
.agreement-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  margin: -6px 0 4px;
  color: #888;
  font-size: 13px;
  line-height: 22px;
  user-select: none;
}

.agreement-label {
  display: inline-flex;
  align-items: center;
  cursor: pointer;
}

.agreement-check {
  appearance: none;
  -webkit-appearance: none;
  width: 16px;
  height: 16px;
  margin: 0 6px 0 0;
  border: 1px solid #c0c4cc;
  border-radius: 3px;
  background: #fff;
  cursor: pointer;
  position: relative;
  transition: border-color 0.2s ease, background-color 0.2s ease;
  flex-shrink: 0;
}

.agreement-check:checked {
  border-color: #D7041A;
  background: #D7041A;
}

.agreement-check:checked::after {
  content: "";
  position: absolute;
  left: 4px;
  top: 1px;
  width: 4px;
  height: 8px;
  border: solid #fff;
  border-width: 0 2px 2px 0;
  transform: rotate(45deg);
}

.agreement-link {
  color: #D7041A;
  text-decoration: none;
  cursor: pointer;
}

.agreement-link:hover {
  text-decoration: underline;
}

.submit-btn {
  width: 100%;
  height: 52px;
  margin-top: 18px;
  border: 0;
  border-radius: 6px;
  background: linear-gradient(90deg, #D7041A 0%, #B80015 100%);
  color: #fff;
  cursor: pointer;
  font-family: inherit;
  font-size: 16px;
  font-weight: 500;
  letter-spacing: 4px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  box-shadow: 0 6px 18px rgba(215, 4, 26, 0.32);
  transition: filter 0.2s ease, box-shadow 0.2s ease, transform 0.1s ease;
}

.submit-btn:hover:not(:disabled) {
  filter: brightness(1.06);
  box-shadow: 0 8px 22px rgba(215, 4, 26, 0.4);
}

.submit-btn:active:not(:disabled) {
  transform: scale(0.99);
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

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

/* ============ 响应式 ============ */
@media (max-width: 1199px) {
  .login-right {
    width: 560px;
    margin-left: -160px;
  }

  .login-card {
    width: 400px;
    padding: 40px 36px 34px;
  }
}

@media (max-width: 768px) {
  /* 窄屏：图片缩成背景，表单卡片居中显示 */
  .login-art-wrap {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
  }

  .login-art {
    width: 100%;
    height: 100%;
    object-fit: cover;
    opacity: 0.5;
  }

  .login-logo {
    top: 20px;
    left: 20px;
    height: 32px;
  }

  .login-right {
    position: relative;
    width: 100%;
    margin-left: 0;
    padding: 24px;
    justify-content: center;
  }

  .login-card {
    width: 100%;
    max-width: 420px;
    padding: 36px 28px 30px;
  }
}

@media (max-width: 480px) {
  .auth-tab {
    font-size: 22px;
  }

  .submit-btn {
    height: 48px;
    letter-spacing: 2px;
  }
}
</style>
