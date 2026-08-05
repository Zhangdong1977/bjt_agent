import { defineStore } from "pinia";
import { computed, ref } from "vue";
import { billingApi } from "@/api/client";
import type { Wallet } from "@/types";
import { Modal } from "ant-design-vue";

export const useBillingStore = defineStore("billing", () => {
  const wallet = ref<Wallet | null>(null);
  const loading = ref(false);

  const balanceWen = computed(() => wallet.value?.total_balance_points ?? wallet.value?.balance_wen ?? 0);
  const rechargeBalance = computed(() => wallet.value?.recharge_balance_points ?? 0);
  const giftBalance = computed(() => wallet.value?.gift_balance_points ?? 0);
  const points = computed(() => wallet.value?.points ?? 0);
  const remindedScenes = new Set<string>();

  async function fetchWallet() {
    loading.value = true;
    try {
      wallet.value = await billingApi.getWallet();
    } finally {
      loading.value = false;
    }
  }

  async function remindLowBalance(scene: string, force = false) {
    await fetchWallet();
    if (!wallet.value?.low_balance || (!force && remindedScenes.has(scene))) return false;
    remindedScenes.add(scene);
    Modal.warning({
      title: "点数余额不足提醒",
      content: `当前可用点数 ${Math.round(wallet.value.total_balance_points)}，低于提醒阈值 ${Math.round(wallet.value.low_balance_threshold)}，建议及时充值以免影响任务结算。`,
      okText: "我知道了",
    });
    return true;
  }

  function reset() {
    wallet.value = null;
    remindedScenes.clear();
  }

  return {
    wallet,
    loading,
    balanceWen,
    rechargeBalance,
    giftBalance,
    points,
    fetchWallet,
    remindLowBalance,
    reset,
  };
});
