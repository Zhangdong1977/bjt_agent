import { defineStore } from "pinia";
import { computed, ref } from "vue";
import { billingApi } from "@/api/client";
import type { Wallet } from "@/types";

export const useBillingStore = defineStore("billing", () => {
  const wallet = ref<Wallet | null>(null);
  const loading = ref(false);

  const balanceWen = computed(() => wallet.value?.balance_wen ?? 0);
  const points = computed(() => wallet.value?.points ?? 0);

  async function fetchWallet() {
    loading.value = true;
    try {
      wallet.value = await billingApi.getWallet();
    } finally {
      loading.value = false;
    }
  }

  function reset() {
    wallet.value = null;
  }

  return {
    wallet,
    loading,
    balanceWen,
    points,
    fetchWallet,
    reset,
  };
});
