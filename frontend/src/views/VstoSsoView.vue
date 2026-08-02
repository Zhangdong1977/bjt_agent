<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue";
import { useAuthStore } from "@/stores/auth";
import BlindCheckView from "@/views/BlindCheckView.vue";
import logoUrl from "@/assets/images/ui/common-logo-black.png";

const SSO_REQUEST = "bjt.vsto.sso.request";
const SSO_RESULT = "bjt.vsto.sso.result";
const authStore = useAuthStore();
const statusText = ref("正在读取插件登录状态…");
const errorText = ref("");
const ready = ref(false);
const insideVsto = ref(false);
let requestId = "";
let timeoutId: ReturnType<typeof setTimeout> | null = null;
let listener: ((event: MessageEvent) => void) | null = null;

type VstoWindow = Window & {
  chrome?: {
    webview?: {
      postMessage: (value: unknown) => void;
      addEventListener: (type: string, listener: (event: MessageEvent) => void) => void;
      removeEventListener: (type: string, listener: (event: MessageEvent) => void) => void;
    };
  };
};

function messageOf(error: unknown): string {
  if (error && typeof error === "object" && "response" in error) {
    const response = (error as { response?: { data?: { detail?: string } } }).response;
    if (response?.data?.detail) return response.data.detail;
  }
  return error instanceof Error ? error.message : "单点登录失败，请关闭页面后重试";
}

function requestTicket() {
  errorText.value = "";
  statusText.value = "正在读取插件登录状态…";
  const webview = (window as VstoWindow).chrome?.webview;
  if (!webview) {
    errorText.value = "该入口只能从 Word 插件中打开";
    return;
  }
  insideVsto.value = true;
  requestId = typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `sso-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  webview.postMessage({ type: SSO_REQUEST, request_id: requestId });
  if (timeoutId) clearTimeout(timeoutId);
  timeoutId = setTimeout(() => {
    errorText.value = "插件未返回登录票据，请确认插件已登录后重试";
  }, 10_000);
}

async function handleMessage(event: MessageEvent) {
  let value: unknown = event.data;
  if (typeof value === "string") {
    try { value = JSON.parse(value); } catch { return; }
  }
  if (!value || typeof value !== "object") return;
  const payload = value as Record<string, unknown>;
  if (payload.type !== SSO_RESULT || String(payload.request_id || "") !== requestId) return;
  if (timeoutId) {
    clearTimeout(timeoutId);
    timeoutId = null;
  }
  if (payload.success !== true || !payload.ticket) {
    errorText.value = String(payload.error || "插件登录状态无效，请重新登录插件");
    return;
  }
  statusText.value = "正在进入暗标合规检查…";
  try {
    await authStore.loginWithVstoTicket(String(payload.ticket));
    ready.value = true;
  } catch (error) {
    errorText.value = messageOf(error);
  }
}

onMounted(() => {
  const webview = (window as VstoWindow).chrome?.webview;
  if (!webview) {
    statusText.value = "此页面不提供浏览器入口";
    errorText.value = "暗标合规检查只能从 Word VSTO 插件的任务面板中打开";
    return;
  }
  if (authStore.isAuthenticated) {
    ready.value = true;
    return;
  }
  listener = handleMessage;
  webview.addEventListener("message", handleMessage);
  requestTicket();
});

onUnmounted(() => {
  if (timeoutId) clearTimeout(timeoutId);
  const webview = (window as VstoWindow).chrome?.webview;
  if (webview && listener) webview.removeEventListener("message", listener);
});
</script>

<template>
  <BlindCheckView v-if="ready" />
  <main v-else class="sso-page">
    <div class="brand-line" />
    <section class="sso-card">
      <img :src="logoUrl" alt="标书审查智能体" class="sso-logo">
      <div v-if="!errorText" class="spinner" aria-hidden="true" />
      <h1>暗标合规检查</h1>
      <p>{{ statusText }}</p>
      <div v-if="errorText" class="error" role="alert">{{ errorText }}</div>
      <button v-if="errorText && insideVsto" type="button" @click="requestTicket">重试</button>
    </section>
  </main>
</template>

<style scoped>
.sso-page{display:flex;min-height:100vh;align-items:center;justify-content:center;background:#f5f5f5;font-family:"Microsoft YaHei",sans-serif}.brand-line{position:fixed;top:0;left:0;right:0;height:3px;background:linear-gradient(90deg,#d7041a,#b80015)}.sso-card{width:min(420px,calc(100vw - 28px));padding:34px 24px;text-align:center;background:#fff;border:1px solid #e8e8e8;border-top:3px solid #d7041a;border-radius:12px;box-shadow:0 8px 28px rgba(40,28,30,.08)}.sso-logo{width:112px;height:auto;margin:0 auto 22px;object-fit:contain}h1{margin:16px 0 8px;color:#222;font-size:22px}p{margin:0;color:#777;font-size:13px}.spinner{width:32px;height:32px;margin:0 auto;border:3px solid #fee7e8;border-top-color:#d7041a;border-radius:50%;animation:spin .8s linear infinite}.error{margin-top:16px;padding:10px;border:1px solid #ffccc7;border-radius:7px;background:#fff1f0;color:#c23b3b;font-size:12px;line-height:1.6}button{margin-top:16px;padding:9px 24px;border:0;border-radius:7px;background:linear-gradient(90deg,#d7041a,#b80015);color:#fff;cursor:pointer;box-shadow:0 4px 12px rgba(215,4,26,.2)}@keyframes spin{to{transform:rotate(360deg)}}
</style>
