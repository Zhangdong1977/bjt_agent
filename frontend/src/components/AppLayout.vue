<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useBillingStore } from '@/stores/billing'
import { useAnnouncementStore } from '@/stores/announcement'
import AppSidebar from './AppSidebar.vue'
import PurchaseModal from './billing/PurchaseModal.vue'
import AnnouncementPopup from './announcement/AnnouncementPopup.vue'
import AnnouncementInbox from './announcement/AnnouncementInbox.vue'
import AnnouncementMarquee from './announcement/AnnouncementMarquee.vue'
import logoUrl from '@/assets/images/ui/common-logo-black.png'
import iconWallet from '@/assets/images/ui/common-icon-wallet.png'
import iconPoints from '@/assets/images/ui/common-icon-points.png'
import iconCart from '@/assets/images/ui/common-icon-cart-full.png'
import iconUser from '@/assets/images/ui/common-icon-user.png'
import wecomQrcode from '@/assets/images/ui/common-wecom-qrcode.jpg'

const router = useRouter()
const authStore = useAuthStore()
const billingStore = useBillingStore()
const announcementStore = useAnnouncementStore()
const rechargeOpen = ref(false)
const contactOpen = ref(false)
const inboxOpen = ref(false)
const marqueeVisible = ref(false)
const officialSiteUrl = 'https://aibjt.com/'

onMounted(() => {
  void billingStore.fetchWallet()
  // 拉取未读公告：驱动顶栏角标 + 自动弹窗
  void announcementStore.initialize()
})

function logout() {
  authStore.logout()
  billingStore.reset()
  announcementStore.reset()
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
  <a-layout class="app-layout" :class="{ 'has-marquee': marqueeVisible }">
    <!-- 顶部系统公告跑马灯：无公告时整条隐藏，--marquee-h 回落为 0，布局自动复位 -->
    <AnnouncementMarquee variant="bar" v-model:visible="marqueeVisible" />
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
        <a-badge
          :count="announcementStore.unreadCount"
          :overflow-count="99"
          :offset="[-4, 4]"
        >
          <button class="inbox-btn" title="系统公告" @click="inboxOpen = true">
            <svg viewBox="0 0 1024 1024" width="20" height="20" aria-hidden="true">
              <path
                fill="currentColor"
                d="M512 928c45.1 0 81.7-36.6 81.7-81.7H430.3c0 45.1 36.6 81.7 81.7 81.7zm253.4-244.3V482.2c0-127.5-84.9-233.9-209.5-262.6v-28.4c0-34.5-28-62.5-62.5-62.5s-62.5 28-62.5 62.5v28.4c-124.6 28.7-209.5 135.1-209.5 262.6v201.5L136 755.1v41.7h752v-41.7l-85.6-71.4zM768 728H256v-12.8l62.4-52.1V482.2c0-97.3 60.4-180.5 150.9-206.9l55.5-16.1v-63.4l1.4-.4 1.4.4v63.4l55.5 16.1c90.5 26.4 150.9 109.6 150.9 206.9v180.9L768 715.2V728z"
              />
            </svg>
          </button>
        </a-badge>
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
    <AnnouncementPopup />
    <AnnouncementInbox v-model:open="inboxOpen" />
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
  /* 顶部跑马灯高度：有公告时 32px，无公告 0（布局回落，向后兼容） */
  --marquee-h: 0px;
}
.app-layout.has-marquee {
  --marquee-h: 32px;
}

/* 顶部 2px 品牌色条 */
.brand-bar {
  height: 2px;
  background: linear-gradient(90deg, #D7041A, #B80015);
  position: fixed;
  top: var(--marquee-h);
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
  top: calc(2px + var(--marquee-h));
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

/* 系统公告铃铛 */
.inbox-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  padding: 0;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: #666;
  cursor: pointer;
  transition: background 0.2s ease, color 0.2s ease;
}

.inbox-btn:hover {
  background: #fff5f6;
  color: #d7041a;
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
  margin-top: calc(66px + var(--marquee-h)); /* 64px header + 2px brand bar + 跑马灯 */
}

.app-sider {
  background: #fff;
  position: fixed;
  top: calc(66px + var(--marquee-h));
  left: 0;
  bottom: 0;
  overflow-y: auto;
  border-right: 1px solid #f0f0f0;
}

.app-content {
  margin-left: 220px;
  padding: 24px;
  background: #f5f7fa;
  min-height: calc(100vh - 66px - var(--marquee-h));
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
