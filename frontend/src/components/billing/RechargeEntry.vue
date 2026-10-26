<script setup lang="ts">
/**
 * 独立功能页（/vsto/*）余额条旁的充值入口。
 * 点击弹出独立充值窗口（/recharge，完整宽度展示，避免 TaskPane 窄容器挤压错位）；
 * 窗口被宿主拦截时降级为页内 PurchaseModal（recharge.ts 的 rechargeOpen）。
 * 支付成功/窗口关闭时 recharge.ts 会刷新钱包，页面余额条随之更新。
 */
import PurchaseModal from "./PurchaseModal.vue";
import { openRecharge, rechargeOpen } from "./recharge";
</script>

<template>
  <button type="button" class="recharge-entry-btn" @click="openRecharge">充值</button>
  <PurchaseModal v-model:open="rechargeOpen" />
</template>

<style scoped>
/* 与各页 metric-pill 胶囊（高 29px、圆角 999px）同尺寸，放在余额条尾部视觉统一 */
.recharge-entry-btn {
  flex: 0 0 auto;
  height: 29px;
  padding: 0 13px;
  border: 0;
  border-radius: 999px;
  background: linear-gradient(90deg, #d7041a 0%, #b80015 100%);
  color: #fff;
  font: inherit;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.05em;
  white-space: nowrap;
  cursor: pointer;
  box-shadow: 0 2px 8px rgba(215, 4, 26, 0.25);
  transition: filter 0.2s ease, transform 0.1s ease;
}

.recharge-entry-btn:hover {
  filter: brightness(1.06);
}

.recharge-entry-btn:active {
  transform: scale(0.97);
}
</style>
