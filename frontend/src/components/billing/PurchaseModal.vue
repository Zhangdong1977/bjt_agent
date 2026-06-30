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
const payLoading = ref(false);
const polling = ref(false);
let pollTimer: ReturnType<typeof setTimeout> | null = null;

const selectedPackage = computed(() =>
  packages.value.find((item) => item.code === selectedPackageCode.value),
);

const availableCoupons = computed(() =>
  coupons.value
    .filter((coupon) => coupon.status === "未使用" && coupon.amount_cents > 0)
    .sort((a, b) => couponExpireTime(a) - couponExpireTime(b)),
);

const couponOptions = computed(() =>
  availableCoupons.value.map((coupon) => ({
    label: `${formatYuan(coupon.amount_cents)} 优惠券（有效期至 ${formatDate(coupon.valid_until)}）`,
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

function formatDate(value?: string | null) {
  return value ? new Date(value).toLocaleDateString() : "-";
}

function couponExpireTime(coupon: Coupon) {
  return coupon.valid_until ? new Date(coupon.valid_until).getTime() : Number.MAX_SAFE_INTEGER;
}

function getApiErrorMessage(err: unknown, fallback: string) {
  const error = err as { response?: { data?: { detail?: string } } };
  return error.response?.data?.detail || fallback;
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
    const canUseRedeemed = redeemed?.status === "未使用" && redeemed.amount_cents > 0;
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
  } catch {
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
    order.value = await billingApi.createOrder({
      package_code: selectedPackageCode.value,
      coupon_id: selectedCouponId.value,
      use_points: normalizeUsePoints(usePoints.value),
      accepted_agreement: acceptedAgreement.value,
    });
    if (order.value.status === "completed") {
      message.success("充值成功");
      emit("paid");
      close();
      return;
    }
    qr.value = await billingApi.getPayQr(order.value.id);
    if (qr.value.payment_mode === "real") {
      startPolling();
    }
  } finally {
    loading.value = false;
  }
}

async function completeMockPayment() {
  if (!order.value) return;
  payLoading.value = true;
  try {
    await billingApi.mockPay(order.value.id);
    message.success("充值成功");
    emit("paid");
    close();
  } finally {
    payLoading.value = false;
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
    width="920px"
    :footer="null"
    :destroy-on-close="false"
    @cancel="close"
  >
    <a-spin :spinning="loading && !qr">
      <div v-if="!qr" class="purchase-body">
        <div class="package-grid">
          <button
            v-for="item in packages"
            :key="item.code"
            type="button"
            :class="['package-card', { active: item.code === selectedPackageCode }]"
            @click="selectedPackageCode = item.code"
          >
            <strong>{{ item.name }}</strong>
            <span class="package-balance">{{ item.balance_wen }}文</span>
            <span class="package-price">{{ formatYuan(item.amount_cents) }}</span>
            <span v-if="item.caution" class="package-caution">{{ item.caution }}</span>
          </button>
        </div>

        <div class="order-panel">
          <div class="summary-row">
            <span>已选套餐价格</span>
            <strong>{{ preview ? formatYuan(preview.order_amount_cents) : "-" }}</strong>
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

          <label class="field-label">选择积分抵扣</label>
          <a-input-number
            v-model:value="usePoints"
            :min="0"
            :max="preview?.current_points ?? 0"
            :step="10"
            class="full-input"
          />

          <div class="summary-row final">
            <span>待支付价格</span>
            <strong>{{ preview ? formatYuan(preview.actual_payment_cents) : "-" }}</strong>
          </div>

          <a-checkbox v-model:checked="acceptedAgreement">
            我已阅读并同意服务协议
          </a-checkbox>

          <a-button
            type="primary"
            size="large"
            block
            :loading="loading || previewLoading"
            @click="submitOrder"
          >
            提交订单
          </a-button>
        </div>
      </div>

      <div v-else class="pay-panel">
        <div class="qr-box">
          <a-qrcode :value="qr.qr_payload" :size="190" />
        </div>
        <div class="pay-summary">
          <h3>{{ selectedPackage?.name }}</h3>
          <p>订单编号：{{ qr.order_no }}</p>
          <p>支付金额：{{ formatYuan(qr.actual_payment_cents) }}</p>
          <p>有效期至：{{ new Date(qr.expires_at).toLocaleString() }}</p>
          <template v-if="qr.payment_mode === 'real'">
            <p class="pay-hint">请使用手机扫码完成支付，到账后自动刷新</p>
            <a-button type="primary" size="large" :loading="polling" @click="pollOrderStatus">
              我已支付，刷新状态
            </a-button>
          </template>
          <a-button
            v-else
            type="primary"
            size="large"
            :loading="payLoading"
            @click="completeMockPayment"
          >
            模拟支付完成
          </a-button>
        </div>
      </div>
    </a-spin>
  </a-modal>
</template>

<style scoped>
.purchase-body {
  display: grid;
  grid-template-columns: minmax(0, 1.3fr) 320px;
  gap: 28px;
}

.package-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 14px;
}

.package-card {
  min-height: 300px;
  border: 1px solid var(--line);
  background: var(--bg1);
  color: var(--text);
  border-radius: var(--r);
  padding: 18px 14px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: space-between;
  cursor: pointer;
  transition: border-color 0.2s ease, box-shadow 0.2s ease, transform 0.2s ease;
  font-family: inherit;
}

.package-card:hover,
.package-card.active {
  border-color: var(--blue);
  box-shadow: 0 6px 18px color-mix(in srgb, var(--blue) 18%, transparent);
  transform: translateY(-1px);
}

.package-card strong {
  font-size: 1rem;
  color: var(--bright);
}

.package-balance {
  font-size: 1.35rem;
  font-weight: 700;
  color: var(--blue);
}

.package-price {
  width: 88px;
  height: 30px;
  border: 1px solid var(--line2);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--r-sm);
  color: var(--bright);
  background: var(--bg2);
}

.package-caution {
  min-height: 34px;
  color: var(--amber);
  font-size: 0.78rem;
  line-height: 1.4;
  text-align: center;
}

.order-panel {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.summary-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  color: var(--sub);
}

.summary-row strong {
  color: var(--bright);
  font-size: 1.25rem;
}

.summary-row.final strong {
  font-size: 1.5rem;
  color: var(--blue);
}

.field-label {
  color: var(--sub);
  font-size: 0.86rem;
}

.full-input {
  width: 100%;
}

.coupon-import {
  display: flex;
  gap: 8px;
}

.coupon-code-input {
  flex: 1;
}

.coupon-hint {
  color: var(--muted);
  font-size: 0.78rem;
}

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
  border: 1px solid var(--line);
  border-radius: var(--r);
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
  color: var(--sub);
}

.pay-hint {
  color: var(--amber);
  font-size: 0.82rem;
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
    min-height: 220px;
  }
}
</style>
