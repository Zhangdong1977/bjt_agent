/**
 * 独立功能页（AppLayout 之外：/vsto/bid-draft、/vsto/polish、/vsto/bid-wizard、
 * /vsto/blind-check）的充值入口与 402 计费闸门提示。
 *
 * 这些页面自带余额条但原先没有充值入口，402 余额不足只落一行页面小字，
 * 用户无从知道去哪里充值。这里提供：
 *  - <RechargeEntry />：余额条旁的「充值」按钮（弹独立充值窗口，见 openRecharge）
 *  - notifyInsufficientBalance()：402 时弹「点数余额不足」提示框，一键去充值
 *
 * 充值界面在独立弹出窗口（/recharge）中进行：Word TaskPane 只有 ~400px 宽，
 * 960px 的 PurchaseModal 在页面内会被压缩错位；弹窗与任务面板同源同 profile，
 * 共享 localStorage 登录态。支付成功经 postMessage 通知本页刷新余额，
 * 弹窗关闭时兜底再刷一次。弹窗被宿主拦截时降级为页内 Modal（RechargeEntry 渲染）。
 */
import { h, ref } from "vue";
import { Modal } from "ant-design-vue";
import { useBillingStore } from "@/stores/billing";

/** 降级路径：弹独立窗口失败（被拦截）时，用页内 PurchaseModal 兜底 */
export const rechargeOpen = ref(false);

let rechargeWindow: Window | null = null;
let closeWatcher: ReturnType<typeof setInterval> | null = null;
let paidMessageHooked = false;

function refreshWallet() {
  void useBillingStore().fetchWallet().catch(() => undefined);
}

/** 监听充值窗口的支付成功消息（只注册一次），立即刷新余额 */
function hookPaidMessage() {
  if (paidMessageHooked) return;
  paidMessageHooked = true;
  window.addEventListener("message", (event) => {
    if (event.origin !== window.location.origin) return;
    if ((event.data as { type?: string } | null)?.type === "bjt.recharge.paid") {
      refreshWallet();
    }
  });
}

export function openRecharge() {
  hookPaidMessage();
  if (rechargeWindow && !rechargeWindow.closed) {
    rechargeWindow.focus();
    return;
  }
  const width = 1060;
  const height = 840;
  const left = Math.max(0, Math.round((window.screen.availWidth - width) / 2));
  const top = Math.max(0, Math.round((window.screen.availHeight - height) / 2));
  const popup = window.open(
    "/recharge",
    "bjt-recharge",
    `width=${width},height=${height},left=${left},top=${top}`,
  );
  if (!popup) {
    // WebView2 宿主等拦截了弹窗：降级页内 Modal（窄 TaskPane 下偏挤，但流程可用）
    rechargeOpen.value = true;
    return;
  }
  rechargeWindow = popup;
  if (closeWatcher) clearInterval(closeWatcher);
  closeWatcher = setInterval(() => {
    if (rechargeWindow?.closed) {
      if (closeWatcher) clearInterval(closeWatcher);
      closeWatcher = null;
      rechargeWindow = null;
      refreshWallet(); // 没等到 paid 消息（未支付/宿主限制 postMessage）也能同步余额
    }
  }, 1000);
}

type StatusDetail = { status?: number; detail?: unknown };

/** 兼容 axios 错误（response.status / response.data.detail）与手工构造错误（status / detail）两种形态。 */
function statusAndDetail(error: unknown): StatusDetail {
  const err = error as
    | (StatusDetail & { response?: { status?: number; data?: { detail?: unknown } } })
    | null
    | undefined;
  return {
    status: err?.status ?? err?.response?.status,
    detail: err?.response?.data?.detail ?? err?.detail,
  };
}

/**
 * 402 余额不足时弹友好提示框（后端 INSUFFICIENT_BALANCE 的 message + 当前可用点数），
 * 「去充值」弹出独立充值窗口。
 *
 * 返回 true 表示错误已接管（调用方不必再写页面错误文案）；非 402 返回 false，
 * 调用方继续走原有 friendlyError/errorText 逻辑。
 */
export function notifyInsufficientBalance(
  error: unknown,
  fallbackMessage = "点数余额不足，无法发起任务。",
): boolean {
  const { status, detail } = statusAndDetail(error);
  if (status !== 402) return false;
  // 三种文案来源：detail.message（axios/bidWizard request）→ error.message（uploadFile 把
  // detail 压成 message 字符串）→ 兜底。泛化的「请求失败（402）」不算有效文案。
  const rawMessage =
    detail && typeof detail === "object" && typeof (detail as { message?: unknown }).message === "string"
      ? (detail as { message: string }).message
      : error instanceof Error
        ? error.message
        : "";
  const message =
    rawMessage && !/^(请求失败|上传失败)（402）$/.test(rawMessage.trim())
      ? rawMessage
      : fallbackMessage;
  const billingStore = useBillingStore();
  const points = billingStore.wallet?.total_balance_points;
  Modal.confirm({
    title: "点数余额不足",
    content: h("div", { style: "line-height:1.8" }, [
      h("p", { style: "margin:0" }, message),
      h(
        "p",
        { style: "margin:4px 0 0;color:#888;font-size:12px" },
        points == null
          ? "充值完成后即可继续发起任务。"
          : `当前可用点数：${Math.round(points)} 点。充值完成后即可继续发起任务。`,
      ),
    ]),
    okText: "去充值",
    cancelText: "稍后再说",
    onOk: openRecharge,
  });
  // 弹窗展示的同时刷新一次钱包，保证提示里的余额与余额条一致
  refreshWallet();
  return true;
}
