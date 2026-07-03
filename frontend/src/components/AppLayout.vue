<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useBillingStore } from '@/stores/billing'
import AppSidebar from './AppSidebar.vue'
import PurchaseModal from './billing/PurchaseModal.vue'
import logoUrl from '@/assets/images/ui/common-logo-black.png'
import iconWallet from '@/assets/images/ui/common-icon-wallet.png'
import iconPoints from '@/assets/images/ui/common-icon-points.png'
import iconCart from '@/assets/images/ui/common-icon-cart-full.png'
import iconUser from '@/assets/images/ui/common-icon-user.png'
import wecomQrcode from '@/assets/images/ui/common-wecom-qrcode.jpg'

const router = useRouter()
const authStore = useAuthStore()
const billingStore = useBillingStore()
const rechargeOpen = ref(false)
const contactOpen = ref(false)
const officialSiteUrl = 'https://aibjt.com/'

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

function goOfficialSite() {
  window.open(officialSiteUrl, '_blank', 'noopener')
}
</script>

<template>
  <a-layout class="app-layout">
    <!-- 顶部品牌色条 -->
    <div class="brand-bar"></div>

    <a-layout-header class="app-header">
      <div class="header-left">
        <img :src="logoUrl" alt="标书审查智能体" class="header-logo" />
      </div>

      <div class="header-right">
        <div class="account-strip">
          <span
            class="metric metric--pill metric--wallet"
            :style="{ backgroundImage: `url(${iconWallet})` }"
            @click="rechargeOpen = true"
          >
            <span class="metric-value">{{ billingStore.balanceWen }}文</span>
          </span>
          <span
            class="metric metric--pill metric--points"
            :style="{ backgroundImage: `url(${iconPoints})` }"
          >
            <span class="metric-value">{{ billingStore.points }}积分</span>
          </span>
          <span class="metric metric--cart" title="购物车">
            <img :src="iconCart" alt="" />
          </span>
        </div>
        <button class="recharge-btn" @click="rechargeOpen = true">立即充值</button>
        <button class="outline-btn" @click="goOfficialSite">前往官网</button>
        <button class="outline-btn" @click="contactOpen = true">联系我们</button>
        <div class="header-divider"></div>
        <button class="profile-btn" @click="goProfile">
          <img :src="iconUser" alt="" />
          <span>{{ authStore.user?.nickname || authStore.user?.username }}</span>
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
    <a-modal
      :open="contactOpen"
      title="联系我们"
      :footer="null"
      width="360px"
      :destroy-on-close="false"
      @cancel="contactOpen = false"
    >
      <div class="contact-modal">
        <img :src="wecomQrcode" alt="企业微信二维码" class="contact-qrcode" />
        <p class="contact-tip">扫码添加专属客服企微，获取专属服务</p>
      </div>
    </a-modal>
  </a-layout>
</template>

<style scoped>
.app-layout {
  min-height: 100vh;
  background: #f5f7fa;
}

/* 顶部 2px 品牌色条 */
.brand-bar {
  height: 2px;
  background: linear-gradient(90deg, #D7041A, #B80015);
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
  background: #fff;
  padding: 0 28px;
  height: 64px;
  line-height: 64px;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.04);
  position: fixed;
  top: 2px;
  left: 0;
  right: 0;
  z-index: 99;
  font-family: "Microsoft YaHei", "PingFang SC", Arial, sans-serif;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 40px;
}

.header-logo {
  flex-shrink: 0;
  width: 110px;
  height: auto;
  object-fit: contain;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 16px;
}

.account-strip {
  display: flex;
  align-items: center;
  gap: 14px;
}

.metric {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  cursor: default;
  color: #555;
  font-size: 14px;
  font-weight: 500;
}

.metric img {
  width: auto;
  height: 24px;
  object-fit: contain;
}

/* 胶囊图标作为数值背景：图片左侧是小图标，右侧留白区叠数值文字 */
.metric--pill {
  height: 32px;
  padding: 0 14px 0 40px; /* 左侧留给图标区，右侧留白 */
  border: 0;
  background-repeat: no-repeat;
  background-position: left center;
  background-size: auto 32px; /* 按高度铺满，宽度按 3:1 比例约 96px */
  align-items: center;
  cursor: default;
}

.metric--wallet {
  cursor: pointer;
  transition: filter 0.2s ease;
}

.metric--wallet:hover {
  filter: brightness(1.04);
}

.metric--cart {
  cursor: pointer;
}

.metric--cart img {
  width: 22px;
  height: 21px;
}

.metric-value {
  color: #333;
}

.recharge-btn {
  height: 34px;
  line-height: 34px; /* 覆盖从 .app-header 继承的 64px，使文字垂直居中 */
  padding: 0 18px;
  border: 0;
  border-radius: 6px;
  background: linear-gradient(90deg, #D7041A 0%, #B80015 100%);
  color: #fff;
  cursor: pointer;
  font-family: inherit; /* 只继承字体族，避免 font 简写连带重置 line-height */
  font-size: 13px;
  font-weight: 500;
  letter-spacing: 1px;
  box-shadow: 0 4px 12px rgba(215, 4, 26, 0.28);
  transition: filter 0.2s ease, transform 0.1s ease;
}

.recharge-btn:hover {
  filter: brightness(1.06);
}

.recharge-btn:active {
  transform: scale(0.98);
}

/* 白底红边按钮：前往官网 / 联系我们，配色与主色调一致 */
.outline-btn {
  height: 34px;
  line-height: 34px;
  padding: 0 16px;
  border: 1px solid #D7041A;
  border-radius: 6px;
  background: #fff;
  color: #D7041A;
  cursor: pointer;
  font-family: inherit;
  font-size: 13px;
  font-weight: 500;
  letter-spacing: 1px;
  transition: background 0.2s ease, color 0.2s ease, transform 0.1s ease;
}

.outline-btn:hover {
  background: #D7041A;
  color: #fff;
}

.outline-btn:active {
  transform: scale(0.98);
}

/* 联系我们企微二维码弹窗 */
.contact-modal {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 8px 0 4px;
}

.contact-qrcode {
  width: 100%;
  max-width: 280px;
  height: auto;
  object-fit: contain;
}

.contact-tip {
  margin: 16px 0 0;
  color: #555;
  font-size: 14px;
  text-align: center;
}

.header-divider {
  width: 1px;
  height: 22px;
  background: #e4e6f1;
}

.profile-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 34px;
  padding: 0 4px;
  border: 0;
  background: transparent;
  color: #555;
  cursor: pointer;
  font-family: inherit;
  font-size: 14px;
  transition: color 0.2s ease;
}

.profile-btn img {
  width: 22px;
  height: 22px;
  object-fit: contain;
}

.profile-btn:hover {
  color: #D7041A;
}

.logout-btn {
  background: none;
  border: none;
  color: #999;
  font-size: 13px;
  cursor: pointer;
  padding: 4px 8px;
  transition: color 0.2s ease;
  font-family: inherit;
}

.logout-btn:hover {
  color: #D7041A;
}

.app-body {
  margin-top: 66px; /* 64px header + 2px brand bar */
}

.app-sider {
  background: #fff;
  position: fixed;
  top: 66px;
  left: 0;
  bottom: 0;
  overflow-y: auto;
  border-right: 1px solid #f0f0f0;
}

.app-content {
  margin-left: 220px;
  padding: 24px;
  background: #f5f7fa;
  min-height: calc(100vh - 66px);
  overflow: auto;
}

@media (max-width: 991px) {
  .app-content {
    margin-left: 0;
    padding: 16px;
  }

  .app-sider {
    display: none;
  }

  .account-strip {
    gap: 10px;
  }

  /* 胶囊图标缩小，但保留数值文字（背景图需配文字才完整） */
  .metric--pill {
    height: 28px;
    padding: 0 10px 0 34px;
    background-size: auto 28px;
    font-size: 13px;
  }
}

@media (max-width: 767px) {
  .header-left {
    gap: 12px;
  }

  .header-logo {
    width: 88px;
  }

  .recharge-btn {
    padding: 0 12px;
  }

  .outline-btn {
    padding: 0 12px;
  }
}
</style>
