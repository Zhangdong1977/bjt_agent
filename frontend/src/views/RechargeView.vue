<script setup lang="ts">
import { ref, watch } from "vue";
import PurchaseModal from "@/components/billing/PurchaseModal.vue";
import { getAccessToken } from "@/api/client";
import logoUrl from "@/assets/images/ui/common-logo-black.png";

/**
 * 独立充值窗口（路由 /recharge）：由 /vsto/* 功能页余额条「充值」按钮或 402 提示框的
 * 「去充值」通过 window.open 唤起。
 *
 * 为什么用独立窗口：Word TaskPane 只有 ~400px 宽，960px 的 PurchaseModal 在页面内
 * 会被 max-width 压缩导致布局错位；弹出独立窗口可以完整宽度展示充值界面。
 * WebView2 弹出的窗口与任务面板同源同 profile，共享 localStorage 登录态。
 * 支付成功 → postMessage 通知 opener 刷新余额，随后自动关窗；
 * 未支付关闭弹窗 → 直接关窗（opener 关窗时也会兜底刷新余额）。
 */
const hasToken = Boolean(getAccessToken());
const modalOpen = ref(hasToken);
const paid = ref(false);

function notifyOpener() {
  paid.value = true;
  try {
    window.opener?.postMessage({ type: "bjt.recharge.paid" }, window.location.origin);
  } catch {
    /* opener 可能已被关闭，忽略即可 */
  }
  setTimeout(() => window.close(), 1800);
}

watch(modalOpen, (open) => {
  // 未支付就关掉充值弹窗 = 结束本次充值，随窗一起关闭
  if (!open && !paid.value) setTimeout(() => window.close(), 200);
});
</script>

<template>
  <main class="recharge-view">
    <div class="brand-line" />
    <header class="panel-header">
      <img :src="logoUrl" alt="标书审查智能体" class="panel-logo">
      <span class="page-title">账户充值</span>
    </header>
    <section class="tip-card">
      <template v-if="hasToken">
        <p>请在弹出的充值窗口中选择套餐完成支付。</p>
        <p class="sub">支付成功后余额自动同步到任务面板，本窗口将自动关闭；也可随时关闭本窗口稍后再充。</p>
      </template>
      <template v-else>
        <p class="warn">未检测到登录状态，无法发起充值。</p>
        <p class="sub">请关闭本窗口，回到 Word 任务面板重新点击「充值」；若仍失败，请退出后重新登录再试。</p>
      </template>
    </section>
    <PurchaseModal v-model:open="modalOpen" @paid="notifyOpener" />
  </main>
</template>

<style scoped>
.recharge-view {
  min-height: 100vh;
  background: #f5f7fa;
  font-family: "Microsoft YaHei", "PingFang SC", Arial, sans-serif;
}

.brand-line {
  height: 3px;
  background: linear-gradient(90deg, #d7041a, #b80015);
}

.panel-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 24px;
  background: #fff;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.04);
}

.panel-logo {
  width: 110px;
  height: auto;
  object-fit: contain;
}

.page-title {
  color: #333;
  font-size: 16px;
  font-weight: 600;
}

.tip-card {
  margin: 24px auto 0;
  max-width: 720px;
  padding: 18px 22px;
  background: #fff;
  border: 1px solid #ececec;
  border-radius: 10px;
}

.tip-card p {
  margin: 0 0 6px;
  color: #555;
  font-size: 14px;
  line-height: 1.7;
}

.tip-card p:last-child {
  margin-bottom: 0;
}

.tip-card .sub {
  color: #999;
  font-size: 12px;
}

.tip-card .warn {
  color: #c53030;
}
</style>
