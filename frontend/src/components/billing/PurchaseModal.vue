<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from "vue";
import { message } from "ant-design-vue";
import { billingApi } from "@/api/client";
import type {
  BillingOrder,
  Coupon,
  OrderPreview,
  PaymentQr,
  RechargePackage,
} from "@/types";
import iconSelect from "@/assets/images/ui/plan-icon-select.png";
import iconOrder from "@/assets/images/ui/common-icon-order.png";
import iconCart from "@/assets/images/ui/common-icon-cart-full.png";
import dividerUrl from "@/assets/images/ui/plan-divider.png";
import pkgTrial from "@/assets/images/ui/plan-icon-trial.png";
import pkgBasic from "@/assets/images/ui/plan-icon-basic.png";
import pkgPremium from "@/assets/images/ui/plan-icon-premium.png";
import pkgLuxury from "@/assets/images/ui/plan-icon-luxury.png";

// 套餐图标映射：名称关键词 -> 切图
const PACKAGE_ICONS: Array<{ keys: string[]; icon: string }> = [
  { keys: ["trial", "体验"], icon: pkgTrial },
  { keys: ["basic", "基础"], icon: pkgBasic },
  { keys: ["luxury", "豪华"], icon: pkgLuxury },
  { keys: ["premium", "尊享", "尊惠", "高级"], icon: pkgPremium },
];

function iconFor(pkg: RechargePackage): string {
  if (pkg.icon_url && /^(https?:|data:|\/)/.test(pkg.icon_url)) return pkg.icon_url;
  const haystack = `${pkg.code || ""} ${pkg.name || ""} ${pkg.icon_url || ""}`.toLowerCase();
  for (const entry of PACKAGE_ICONS) {
    if (entry.keys.some((k) => haystack.includes(k.toLowerCase()))) {
      return entry.icon;
    }
  }
  return pkgBasic;
}

const props = defineProps<{
  open: boolean;
}>();

const emit = defineEmits<{
  "update:open": [value: boolean];
  paid: [];
}>();

const packages = ref<RechargePackage[]>([]);
const coupons = ref<Coupon[]>([]);
const selectedPackageCode = ref("premium");
const selectedCouponId = ref<number | null>(null);
const couponCode = ref("");
const usePoints = ref<number | null>(0);
const acceptedAgreement = ref(false);
const preview = ref<OrderPreview | null>(null);
const qr = ref<PaymentQr | null>(null);
const order = ref<BillingOrder | null>(null);
const loading = ref(false);
const couponImporting = ref(false);
const previewLoading = ref(false);
const polling = ref(false);
let pollTimer: ReturnType<typeof setTimeout> | null = null;

const selectedPackage = computed(() =>
  packages.value.find((item) => item.code === selectedPackageCode.value),
);

// 点数有效期（月）：优先取订单预览返回值，回落到所选套餐
const validityMonths = computed(
  () => preview.value?.validity_months ?? selectedPackage.value?.validity_months ?? 0,
);

const availableCoupons = computed(() =>
  coupons.value
    .filter((coupon) => coupon.status === "未使用" && (coupon.amount_cents > 0 || coupon.gift_points > 0))
    .sort((a, b) => couponExpireTime(a) - couponExpireTime(b)),
);

const couponOptions = computed(() =>
  availableCoupons.value.map((coupon) => ({
    label: `${coupon.benefit_type === "gift" ? `赠送${formatPoints(coupon.gift_points)}点` : `${formatYuan(coupon.amount_cents)}优惠`}（门槛${formatYuan(coupon.threshold_amount_cents)}，有效期至 ${formatDate(coupon.valid_until)}）`,
    value: coupon.id,
  })),
);

function close() {
  stopPolling();
  emit("update:open", false);
}

function formatYuan(cents: number) {
  return `￥${(cents / 100).toFixed(2)}`;
}

// 点数统一按整数展示，不显示小数点后数字
function formatPoints(value?: number | null) {
  return String(Math.round(Number(value || 0)));
}

function formatDate(value?: string | null) {
  return value ? new Date(value).toLocaleDateString() : "-";
}

function couponExpireTime(coupon: Coupon) {
  return coupon.valid_until ? new Date(coupon.valid_until).getTime() : Number.MAX_SAFE_INTEGER;
}

function getApiErrorMessage(err: unknown, fallback: string) {
  const error = err as { response?: { data?: { detail?: string | { message?: string } } } };
  const detail = error.response?.data?.detail;
  return typeof detail === "string" ? detail : detail?.message || fallback;
}

function normalizeUsePoints(value: number | null | undefined) {
  const parsed = Number(value ?? 0);
  if (!Number.isFinite(parsed)) return 0;
  return Math.max(0, Math.floor(parsed));
}

async function loadData() {
  loading.value = true;
  try {
    const [packageRows, couponRows] = await Promise.all([
      billingApi.listPackages(),
      billingApi.listCoupons(),
    ]);
    packages.value = packageRows;
    coupons.value = couponRows;
    if (!packages.value.some((item) => item.code === selectedPackageCode.value)) {
      selectedPackageCode.value = packages.value[0]?.code ?? "";
    }
    if (
      selectedCouponId.value &&
      !availableCoupons.value.some((coupon) => coupon.id === selectedCouponId.value)
    ) {
      selectedCouponId.value = null;
    }
    await refreshPreview();
  } finally {
    loading.value = false;
  }
}

async function importCoupon() {
  const code = couponCode.value.trim();
  if (!code) {
    message.warning("请输入优惠券兑换码");
    return;
  }
  couponImporting.value = true;
  try {
    const result = await billingApi.redeemCoupon(code);
    coupons.value = result.coupons;
    const redeemed =
      result.coupon ??
      result.coupons.find((coupon) => (coupon.code || "").trim().toLowerCase() === code.toLowerCase());
    const canUseRedeemed = redeemed?.status === "未使用" && (redeemed.amount_cents > 0 || redeemed.gift_points > 0);
    if (canUseRedeemed) {
      selectedCouponId.value = redeemed.id;
    } else if (
      selectedCouponId.value &&
      !availableCoupons.value.some((coupon) => coupon.id === selectedCouponId.value)
    ) {
      selectedCouponId.value = null;
    }
    couponCode.value = "";
    message.success(canUseRedeemed ? "优惠券已导入并选中" : "优惠券已导入，当前不可用于充值");
    await refreshPreview();
  } catch (err) {
    message.error(getApiErrorMessage(err, "优惠券导入失败"));
  } finally {
    couponImporting.value = false;
  }
}

async function refreshPreview() {
  if (!selectedPackageCode.value) return;
  previewLoading.value = true;
  const requestedPoints = normalizeUsePoints(usePoints.value);
  try {
    preview.value = await billingApi.previewOrder({
      package_code: selectedPackageCode.value,
      coupon_id: selectedCouponId.value,
      use_points: requestedPoints,
    });
    usePoints.value = preview.value.points_used;
  } catch (err) {
    if (selectedCouponId.value) message.warning(getApiErrorMessage(err, "该优惠券暂不可用，已为你取消选择"));
    selectedCouponId.value = null;
    preview.value = await billingApi.previewOrder({
      package_code: selectedPackageCode.value,
      coupon_id: null,
      use_points: requestedPoints,
    });
    usePoints.value = preview.value.points_used;
  } finally {
    previewLoading.value = false;
  }
}

async function submitOrder() {
  if (!acceptedAgreement.value) {
    message.warning("请先勾选同意用户协议");
    return;
  }
  loading.value = true;
  try {
    const createdOrder = await billingApi.createOrder({
      package_code: selectedPackageCode.value,
      coupon_id: selectedCouponId.value,
      use_points: normalizeUsePoints(usePoints.value),
      accepted_agreement: acceptedAgreement.value,
    });
    order.value = createdOrder;
    if (order.value.status === "completed") {
      message.success("充值成功");
      emit("paid");
      close();
      return;
    }
    try {
      qr.value = await billingApi.getPayQr(order.value.id);
    } catch (err) {
      message.error(getApiErrorMessage(err, "获取支付二维码失败，请稍后重试"));
      return;
    }
    startPolling();
  } catch (err) {
    message.error(getApiErrorMessage(err, "订单提交失败"));
  } finally {
    loading.value = false;
  }
}

async function pollOrderStatus() {
  if (!order.value) return;
  try {
    const result = await billingApi.getOrderStatus(order.value.id);
    if (result.status === "completed") {
      stopPolling();
      message.success("支付成功");
      emit("paid");
      close();
    } else if (result.status === "cancelled") {
      stopPolling();
      message.warning("订单已过期，请重新提交");
    }
  } catch {
    // 单次轮询失败忽略，下个 tick 继续
  }
}

function startPolling() {
  stopPolling();
  polling.value = true;
  void pollOrderStatus();
  pollTimer = setInterval(pollOrderStatus, 3000);
}

function stopPolling() {
  polling.value = false;
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

onUnmounted(stopPolling);

watch(
  () => props.open,
  (value) => {
    if (value) {
      qr.value = null;
      order.value = null;
      acceptedAgreement.value = false;
      void loadData();
    } else {
      stopPolling();
    }
  },
);

watch(
  [selectedPackageCode, selectedCouponId, usePoints],
  () => {
    if (props.open && !qr.value) {
      void refreshPreview();
    }
  },
);
</script>

<template>
  <a-modal
    :open="open"
    title="套餐购买"
    width="960px"
    :footer="null"
    :destroy-on-close="false"
    @cancel="close"
  >
    <a-spin :spinning="loading && !qr">
      <template v-if="!qr">
      <div class="promo-banner">
        <span class="promo-tag">重磅优惠</span>
        <span class="promo-text">为庆祝标捷通智能标书检查服务上线，推出重磅优惠活动，充值满30元，赠送5元；满100元，赠送20元；满300元，赠送100元；满1000元，赠送500元！充值越多，赠送越多！活动随时下架，欢迎尽快充值！</span>
      </div>
      <div class="purchase-body">
        <!-- 左侧：套餐选择区 -->
        <div class="package-section">
          <div class="section-head">
            <img :src="iconSelect" alt="" class="section-head-icon" />
            <span>套餐选择</span>
          </div>

          <div class="package-grid">
            <button
              v-for="item in packages"
              :key="item.code"
              type="button"
              :class="['package-card', { active: item.code === selectedPackageCode }]"
              @click="selectedPackageCode = item.code"
            >
              <span v-if="item.code === selectedPackageCode" class="selected-bar"></span>
              <img class="package-icon" :src="iconFor(item)" alt="" />
              <strong class="package-name">{{ item.name }}</strong>
              <span
                class="package-divider"
                :style="{ backgroundImage: `url(${dividerUrl})` }"
              ></span>
              <span class="package-balance">{{ formatPoints(item.total_points) }}点</span>
              <span class="package-breakdown">充值{{ formatPoints(item.recharge_points) }} + 赠送{{ formatPoints(item.gift_points) }}</span>
              <span v-if="item.validity_months" class="package-validity">有效期 {{ item.validity_months }} 个月</span>
              <span class="package-price">{{ formatYuan(item.amount_cents) }}</span>
              <span v-if="item.caution" class="package-caution">{{ item.caution }}</span>
            </button>
          </div>

          <div class="recharge-notes">
            <div class="recharge-notes-title">会员充值说明：</div>
            <ol class="recharge-notes-list">
              <li>标捷通智能标书检查采用有偿服务模式，用户需购买指定金额的套餐，兑换"点"数（1元人民币相当于10点检查点数），用于检查服务业务。</li>
              <li>每次检查系统会根据检查内容的自动计算消耗的"点"数，并扣除对应的用户账户余额。</li>
              <li>平台不定期推出优惠活动，充值指定金额，会赠送一定的检查点数，会员可多关注平台充值活动。</li>
              <li>每消耗1点检查点，系统会赠送1积分，积分可在下次充值时使用。注意，具体可以使用的积分数量，详见充值页面提示。</li>
              <li>各充值套餐均设有对应的有效期，请留意到期时间，有效期结束后自动清零。不同时期购买的套餐，单独计算有效期。</li>
              <li>已购买的套餐，不可转让，不可退换。</li>
            </ol>
          </div>
        </div>

        <!-- 右侧：订单详情区 -->
        <div class="order-section">
          <div class="order-head">
            <img :src="iconOrder" alt="" class="order-head-icon" />
            <span>订单详情</span>
          </div>

          <div class="summary-row">
            <span>已选套餐</span>
            <strong>{{ selectedPackage?.name || "-" }}</strong>
          </div>
          <div class="summary-row">
            <span>套餐价格</span>
            <strong>{{ preview ? formatYuan(preview.order_amount_cents) : "-" }}</strong>
          </div>
          <div class="summary-row">
            <span>到账点数</span>
            <strong>{{ formatPoints(preview?.total_points ?? selectedPackage?.total_points ?? 0) }}点</strong>
          </div>
          <div v-if="validityMonths" class="summary-row">
            <span>点数有效期</span>
            <strong>{{ validityMonths }} 个月</strong>
          </div>
          <div v-if="preview?.coupon_gift_points" class="summary-row">
            <span>赠送券额外点数</span><strong>+{{ formatPoints(preview.coupon_gift_points) }}点</strong>
          </div>

          <label class="field-label">导入优惠券</label>
          <div class="coupon-import">
            <a-input
              v-model:value="couponCode"
              placeholder="输入优惠券兑换码"
              class="coupon-code-input"
              :disabled="couponImporting"
              @pressEnter="importCoupon"
            />
            <a-button :loading="couponImporting" @click="importCoupon">导入</a-button>
          </div>

          <label class="field-label">选择优惠券</label>
          <a-select
            v-model:value="selectedCouponId"
            allow-clear
            :options="couponOptions"
            placeholder="不使用优惠券"
            class="full-input"
          />

          <div v-if="availableCoupons.length" class="coupon-hint">
            可用 {{ availableCoupons.length }} 张，最近到期：
            {{ formatDate(availableCoupons[0]?.valid_until) }}
          </div>

          <label class="field-label">积分抵扣</label>
          <a-input-number
            v-model:value="usePoints"
            :min="0"
            :max="preview?.current_points ?? 0"
            :step="1"
            class="full-input"
          />

          <div class="summary-row final">
            <span>待支付</span>
            <strong>{{ preview ? formatYuan(preview.actual_payment_cents) : "-" }}</strong>
          </div>

          <a-checkbox v-model:checked="acceptedAgreement" class="agreement-check">
            我已阅读并同意服务协议
          </a-checkbox>

          <button
            type="button"
            class="submit-btn"
            :disabled="loading || previewLoading || !acceptedAgreement"
            @click="submitOrder"
          >
            提交订单
          </button>

          <div class="cart-hint">
            <img :src="iconCart" alt="" />
            <span>提交后将生成订单，可使用微信/支付宝扫码支付</span>
          </div>
        </div>
      </div>
      </template>

      <div v-else class="pay-panel">
        <div class="qr-box">
          <a-qrcode :value="qr.qr_payload" :size="190" />
        </div>
        <div class="pay-summary">
          <h3>{{ selectedPackage?.name }}</h3>
          <p>订单编号：{{ qr.order_no }}</p>
          <p>支付金额：{{ formatYuan(qr.actual_payment_cents) }}</p>
          <p>有效期至：{{ new Date(qr.expires_at).toLocaleString() }}</p>
          <p class="pay-hint">请使用手机扫码完成支付，到账后自动刷新</p>
          <a-button type="primary" size="large" :loading="polling" @click="pollOrderStatus">
            我已支付，刷新状态
          </a-button>
        </div>
      </div>
    </a-spin>
  </a-modal>
</template>

<style scoped>
.purchase-body {
  display: grid;
  grid-template-columns: minmax(0, 1.4fr) 320px;
  gap: 24px;
  font-family: "Microsoft YaHei", "PingFang SC", Arial, sans-serif;
}

/* ============ 顶部优惠活动横幅 ============ */
.promo-banner {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  margin-bottom: 18px;
  padding: 12px 16px;
  border: 1px solid #f0d5da;
  border-radius: 8px;
  background: linear-gradient(90deg, #fff5f6 0%, #fffaf0 100%);
}

.promo-tag {
  flex-shrink: 0;
  padding: 2px 10px;
  border-radius: 4px;
  background: linear-gradient(90deg, #D7041A 0%, #B80015 100%);
  color: #fff;
  font-size: 12px;
  font-weight: 600;
  line-height: 20px;
  white-space: nowrap;
}

.promo-text {
  color: #7a1a22;
  font-size: 13px;
  line-height: 20px;
}

/* ============ 区块标题 ============ */
.section-head,
.order-head {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 16px;
  font-weight: 600;
  color: #222;
  margin-bottom: 14px;
}

.section-head-icon {
  width: 28px;
  height: auto;
  object-fit: contain;
}

.order-head-icon {
  width: 24px;
  height: auto;
  object-fit: contain;
}

/* ============ 套餐卡片 ============ */
.package-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}

.package-card {
  position: relative;
  min-height: 280px;
  border: 1px solid #e4e6f1;
  background: #fff;
  color: #333;
  border-radius: 10px;
  padding: 22px 10px 18px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: flex-start;
  gap: 8px;
  cursor: pointer;
  overflow: hidden;
  transition: border-color 0.2s ease, box-shadow 0.2s ease, transform 0.2s ease;
  font-family: inherit;
}

.package-card:hover {
  border-color: #D7041A;
  box-shadow: 0 6px 18px rgba(215, 4, 26, 0.12);
  transform: translateY(-1px);
}

.package-card.active {
  border-color: #D7041A;
  box-shadow: 0 8px 22px rgba(215, 4, 26, 0.18);
  background: linear-gradient(180deg, #fff 0%, #fff7f8 100%);
}

/* 选中态顶部红条（用 CSS 渲染，对应 plan-selected-bar.png 语义） */
.selected-bar {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 4px;
  background: linear-gradient(90deg, #D7041A, #B80015);
}

.package-icon {
  width: 56px;
  height: 56px;
  object-fit: contain;
  margin-top: 4px;
}

.package-name {
  font-size: 15px;
  font-weight: 600;
  color: #333;
}

.package-divider {
  width: 100px;
  height: 1px;
  background-repeat: no-repeat;
  background-position: center;
  background-size: contain;
  opacity: 0.6;
}

.package-balance {
  font-size: 20px;
  font-weight: 700;
  color: #D7041A;
  letter-spacing: 0.5px;
}

.package-breakdown {
  margin-top: -4px;
  color: #8a8f99;
  font-size: 11px;
}

.package-validity {
  margin-top: -2px;
  color: #6b7280;
  font-size: 12px;
  font-weight: 500;
}

.package-price {
  min-width: 76px;
  height: 28px;
  padding: 0 10px;
  border: 1px solid #f0d5da;
  border-radius: 14px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: #D7041A;
  background: #fff5f6;
  font-size: 13px;
  font-weight: 600;
}

.package-caution {
  margin-top: 4px;
  color: #f0a429;
  font-size: 11px;
  line-height: 1.4;
  text-align: center;
}

/* ============ 左侧底部：会员充值说明 ============ */
.recharge-notes {
  margin-top: 18px;
  padding: 14px 16px;
  border: 1px solid #eef0f5;
  border-radius: 8px;
  background: #fafbfd;
  align-self: start;
}

.recharge-notes-title {
  font-size: 13px;
  font-weight: 600;
  color: #444;
  margin-bottom: 8px;
}

.recharge-notes-list {
  margin: 0;
  padding-left: 18px;
  color: #8a8f99;
  font-size: 12px;
  line-height: 1.7;
}

.recharge-notes-list li {
  margin-bottom: 2px;
}

/* ============ 订单区 ============ */
.order-section {
  display: flex;
  flex-direction: column;
  gap: 12px;
  background: #fafbfd;
  border: 1px solid #eef0f5;
  border-radius: 10px;
  padding: 18px 18px 20px;
}

.summary-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  color: #888;
  font-size: 13px;
}

.summary-row strong {
  color: #222;
  font-size: 15px;
  font-weight: 600;
}

.summary-row.final {
  margin-top: 4px;
  padding-top: 12px;
  border-top: 1px dashed #e4e6f1;
}

.summary-row.final strong {
  font-size: 22px;
  font-weight: 700;
  color: #D7041A;
}

.field-label {
  color: #888;
  font-size: 12px;
  margin-top: 2px;
}

.full-input {
  width: 100%;
}

.coupon-import {
  display: flex;
  gap: 6px;
}

.coupon-code-input {
  flex: 1;
}

.coupon-hint {
  color: #999;
  font-size: 11px;
  margin-top: -4px;
}

.agreement-check {
  margin-top: 4px;
  font-size: 12px;
  color: #888;
}

/* 提交订单大按钮（与「立即充值」同款 CSS 渐变按钮，订单区内满宽） */
.submit-btn {
  width: 100%;
  height: 52px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 0;
  border-radius: 8px;
  background: linear-gradient(90deg, #D7041A 0%, #B80015 100%);
  color: #fff;
  cursor: pointer;
  font-family: inherit;
  font-size: 18px;
  font-weight: 600;
  letter-spacing: 4px;
  box-shadow: 0 6px 16px rgba(215, 4, 26, 0.28);
  transition: filter 0.2s ease, transform 0.1s ease;
}

.submit-btn:hover:not(:disabled) {
  filter: brightness(1.06);
}

.submit-btn:active:not(:disabled) {
  transform: scale(0.98);
}

.submit-btn:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

.cart-hint {
  display: flex;
  align-items: center;
  gap: 6px;
  color: #aaa;
  font-size: 11px;
  line-height: 1.5;
}

.cart-hint img {
  width: 18px;
  height: 17px;
  object-fit: contain;
  flex-shrink: 0;
}

/* ============ 支付二维码面板 ============ */
.pay-panel {
  display: grid;
  grid-template-columns: 240px 1fr;
  gap: 28px;
  align-items: center;
}

.qr-box {
  width: 224px;
  height: 224px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px solid #e4e6f1;
  border-radius: 10px;
  background: #fff;
}

.pay-summary {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.pay-summary h3,
.pay-summary p {
  margin: 0;
}

.pay-summary p {
  color: #666;
  font-size: 14px;
}

.pay-hint {
  color: #f0a429;
  font-size: 13px;
}

@media (max-width: 900px) {
  .purchase-body,
  .pay-panel {
    grid-template-columns: 1fr;
  }

  .package-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .package-card {
    min-height: 240px;
  }
}
</style>
