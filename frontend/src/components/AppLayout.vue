<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { GiftOutlined, GlobalOutlined, UserOutlined, WalletOutlined } from '@ant-design/icons-vue'
import { useAuthStore } from '@/stores/auth'
import { useBillingStore } from '@/stores/billing'
import AppSidebar from './AppSidebar.vue'
import ThemeToggle from './ThemeToggle.vue'
import PurchaseModal from './billing/PurchaseModal.vue'
import { getOfficialSiteUrl } from '@/utils/externalLinks'

const router = useRouter()
const authStore = useAuthStore()
const billingStore = useBillingStore()
const rechargeOpen = ref(false)

onMounted(() => {
  void billingStore.fetchWallet()
})

function logout() {
  authStore.logout()
  billingStore.reset()
  router.push({ name: 'login' })
}

function goProfile() {
  router.push({ name: 'profile-center' })
}

function openOfficialSite() {
  window.open(getOfficialSiteUrl(), '_blank', 'noopener,noreferrer')
}
</script>

<template>
  <a-layout class="app-layout">
    <!-- 顶部品牌色条 -->
    <div class="brand-bar"></div>

    <a-layout-header class="app-header">
      <div class="header-left">
        <img src="/logo.ico" alt="标书审查智能体" class="header-logo-icon" />
        <h1 class="header-title">标书审查智能体</h1>
      </div>
      <div class="header-right">
        <div class="wallet-strip">
          <span class="metric-pill">
            <WalletOutlined />
            {{ billingStore.balanceWen }}文
          </span>
          <span class="metric-pill">
            <GiftOutlined />
            {{ billingStore.points }}积分
          </span>
        </div>
        <button class="recharge-btn" @click="rechargeOpen = true">
          <WalletOutlined />
          立即充值
        </button>
        <button class="plugin-btn" @click="openOfficialSite">
          <GlobalOutlined />
          标捷通快速编标插件
        </button>
        <ThemeToggle />
        <div class="header-divider"></div>
        <button class="profile-btn" @click="goProfile">
          <UserOutlined />
          {{ authStore.user?.nickname || authStore.user?.username }}
        </button>
        <button class="logout-btn" @click="logout">退出</button>
      </div>
    </a-layout-header>
    <a-layout class="app-body">
      <a-layout-sider width="220" class="app-sider" :trigger="null">
        <AppSidebar />
      </a-layout-sider>
      <a-layout-content class="app-content">
        <router-view />
      </a-layout-content>
    </a-layout>
    <PurchaseModal v-model:open="rechargeOpen" @paid="billingStore.fetchWallet" />
  </a-layout>
</template>

<style scoped>
.app-layout {
  min-height: 100vh;
}

/* 顶部 2px 品牌色条 */
.brand-bar {
  height: 2px;
  background: linear-gradient(90deg, var(--blue), var(--purple));
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  z-index: 100;
}

.app-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: var(--bg1);
  padding: 0 24px;
  height: 56px;
  line-height: 56px;
  box-shadow: var(--shadow-sm);
  position: fixed;
  top: 2px;
  left: 0;
  right: 0;
  z-index: 99;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 10px;
}

.header-logo-icon {
  flex-shrink: 0;
  width: 28px;
  height: 28px;
  object-fit: contain;
}

.header-title {
  font-family: 'Plus Jakarta Sans', 'DM Sans', sans-serif;
  color: var(--bright);
  font-size: 1.1rem;
  font-weight: 700;
  margin: 0;
  letter-spacing: -0.02em;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 10px;
}

.header-divider {
  width: 1px;
  height: 20px;
  background: var(--line);
}

.wallet-strip {
  display: flex;
  align-items: center;
  gap: 6px;
}

.metric-pill {
  height: 30px;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 0 9px;
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  background: var(--bg2);
  color: var(--bright);
  font-size: 0.8rem;
  font-weight: 600;
  line-height: 1;
}

.recharge-btn,
.plugin-btn,
.profile-btn {
  height: 32px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: none;
  border-radius: var(--r-sm);
  cursor: pointer;
  font-size: 0.8125rem;
  font-weight: 600;
  font-family: inherit;
  transition: filter 0.2s ease, background 0.2s ease, color 0.2s ease;
  white-space: nowrap;
}

.recharge-btn {
  padding: 0 12px;
  background: var(--blue);
  color: #fff;
}

.plugin-btn {
  padding: 0 12px;
  background: var(--amber);
  color: #171717;
}

.profile-btn {
  padding: 0 8px;
  background: transparent;
  color: var(--sub);
}

.recharge-btn:hover,
.plugin-btn:hover {
  filter: brightness(1.08);
}

.profile-btn:hover {
  color: var(--blue);
  background: var(--blue-bg);
}

.logout-btn {
  background: none;
  border: none;
  color: var(--muted);
  font-size: 0.8125rem;
  cursor: pointer;
  padding: 4px 8px;
  border-radius: var(--r-sm);
  transition: all 0.2s ease;
  font-family: inherit;
}

.logout-btn:hover {
  color: var(--red);
  background: var(--red-bg);
}

.app-body {
  margin-top: 58px; /* 56px header + 2px brand bar */
}

.app-sider {
  background: var(--bg2);
  position: fixed;
  top: 58px;
  left: 0;
  bottom: 0;
  overflow-y: auto;
}

.app-content {
  margin-left: 220px;
  padding: 24px;
  background: var(--bg);
  min-height: calc(100vh - 58px);
  overflow: auto;
}

@media (max-width: 767px) {
  .app-content {
    margin-left: 0;
    padding: 16px;
  }

  .app-sider {
    display: none;
  }

  .header-title {
    font-size: 0.95rem;
  }

  .wallet-strip,
  .plugin-btn {
    display: none;
  }
}
</style>
