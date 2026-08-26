import { defineStore } from "pinia";
import { computed, ref } from "vue";
import { billingApi, configApi } from "@/api/client";
import type { CloudFeatures } from "@/api/client";
import type { Wallet } from "@/types";
import { Modal } from "ant-design-vue";

export const useBillingStore = defineStore("billing", () => {
  const wallet = ref<Wallet | null>(null);
  const features = ref<CloudFeatures | null>(null);
  const loading = ref(false);
  // 私有云模式：钱包语义映射为共享次数池（recharge=AI剩余，gift=OCR剩余）
  const privateCloud = computed(() => features.value?.private_cloud === true);

  async function fetchFeatures() {
    if (features.value) return features.value;
    try {
      features.value = await configApi.getFeatures();
    } catch {
      features.value = null;
    }
    return features.value;
  }

  const balanceWen = computed(() => wallet.value?.total_balance_points ?? wallet.value?.balance_wen ?? 0);
  const rechargeBalance = computed(() => wallet.value?.recharge_balance_points ?? 0);
  const giftBalance = computed(() => wallet.value?.gift_balance_points ?? 0);
  const points = computed(() => wallet.value?.points ?? 0);
  const remindedScenes = new Set<string>();

  async function fetchWallet() {
    void fetchFeatures();
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
      title: privateCloud.value ? "剩余次数不足提醒" : "点数余额不足提醒",
      content: privateCloud.value
        ? `当前 AI 剩余次数 ${Math.round(wallet.value.total_balance_points)}，请联系企业管理员扩容。`
        : `当前可用点数 ${Math.round(wallet.value.total_balance_points)}，低于提醒阈值 ${Math.round(wallet.value.low_balance_threshold)}，建议及时充值以免影响任务结算。`,
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
    features,
    privateCloud,
    loading,
    balanceWen,
    rechargeBalance,
    giftBalance,
    points,
    fetchWallet,
    fetchFeatures,
    remindLowBalance,
    reset,
  };
});
