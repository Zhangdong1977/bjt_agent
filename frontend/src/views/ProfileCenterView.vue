<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { message } from "ant-design-vue";
import { billingApi, profileApi } from "@/api/client";
import { useAuthStore } from "@/stores/auth";
import type { BillingOrder, ConsumptionAllocation, ConsumptionRecord, Coupon, User } from "@/types";

const authStore = useAuthStore();
const isInterior = computed(() => authStore.isInteriorUser);

const activeKey = ref("info");
const orderActiveKey = ref("orders");

const profile = ref<User | null>(null);
const profileForm = reactive({
  nickname: "",
  city: "",
  company: "",
  bidding_industries: "",
});
const passwordForm = reactive({
  old_password: "",
  new_password: "",
  confirm_new_password: "",
});

const orders = ref<BillingOrder[]>([]);
const consumptions = ref<ConsumptionRecord[]>([]);
const coupons = ref<Coupon[]>([]);
const loading = ref(false);
const couponCode = ref("");
const couponImporting = ref(false);
const allocationOpen = ref(false);
const allocations = ref<ConsumptionAllocation[]>([]);

const orderFilters = reactive({
  start_date: "",
  end_date: "",
  product_name: "",
  username: "",
  enterprise_name: "",
});
const consumptionFilters = reactive({
  start_date: "",
  end_date: "",
  project_name: "",
  username: "",
  enterprise_name: "",
});

// 内部用户视角多出「用户名/企业」归属列，便于在全站数据中区分归属
const ownershipColumns = isInterior.value
  ? [
      { title: "用户名", dataIndex: "username", width: 120 },
      { title: "企业", dataIndex: "enterprise_name", width: 160 },
    ]
  : [];

const orderColumns = computed(() => [
  { title: "序号", dataIndex: "index", width: 70 },
  ...ownershipColumns,
  { title: "订单编号", dataIndex: "order_no" },
  { title: "来源", dataIndex: "source", width: 90 },
  { title: "产品名称", dataIndex: "product_name" },
  { title: "下单时间", dataIndex: "created_at" },
  { title: "订单状态", dataIndex: "status" },
  { title: "订单金额", dataIndex: "order_amount_cents" },
  { title: "实际付款金额", dataIndex: "actual_payment_cents" },
  { title: "优惠券", dataIndex: "coupon_amount_cents" },
  { title: "积分抵扣金额", dataIndex: "points_amount_cents" },
  { title: "充值点数", dataIndex: "recharge_points" },
  { title: "赠送点数", dataIndex: "gift_points" },
  { title: "已使用点数", dataIndex: "consumed_points" },
  { title: "剩余点数", dataIndex: "remaining_points" },
  { title: "点数状态", dataIndex: "points_status" },
  { title: "点数到期时间", dataIndex: "points_expires_at" },
  { title: "充值后余额", dataIndex: "current_balance_wen" },
]);

const consumptionColumns = computed(() => [
  { title: "序号", dataIndex: "index", width: 70 },
  ...ownershipColumns,
  { title: "消费时间", dataIndex: "consumed_at", width: 170 },
  { title: "结算订单", dataIndex: "settlement_order_nos", width: 180 },
  { title: "项目名称", dataIndex: "project_name" },
  { title: "消费前点数", dataIndex: "points_before", width: 110 },
  { title: "销售点数", dataIndex: "sales_points", width: 100 },
  { title: "消费后剩余点数", dataIndex: "points_after", width: 120 },
  { title: "赠送点数扣除", dataIndex: "gift_points_used", width: 110 },
  { title: "充值点数扣除", dataIndex: "recharge_points_used", width: 110 },
  { title: "获得积分", dataIndex: "earned_points", width: 90 },
  { title: "使用人", dataIndex: "used_by", width: 110 },
  { title: "扣点详情", dataIndex: "actions", width: 100 },
]);

const couponColumns = [
  { title: "序号", dataIndex: "index", width: 70 },
  { title: "兑换码", dataIndex: "code" },
  { title: "优惠券金额", dataIndex: "amount_cents" },
  { title: "赠送点数", dataIndex: "gift_points" },
  { title: "有效期", dataIndex: "valid_until" },
  { title: "状态", dataIndex: "status" },
];

const orderRows = computed(() =>
  orders.value.map((item, index) => ({ ...item, index: index + 1 })),
);
// 「当前扣费订单」是个人钱包视图：只展示当前登录账户自己正在扣费的订单。
// 内部用户的订单列表会含全站订单（订单记录表格用），这里必须按归属收敛到本人，
// 否则会把其它账户的可用订单卡片一起渲染出来。
const currentUsername = computed(() => authStore.user?.username ?? "");
const activeOrderCards = computed(() =>
  orders.value.filter(
    (item) =>
      item.points_status === "active" &&
      Number(item.remaining_points) > 0 &&
      // 非内部用户后端已按 user_id 过滤；内部用户在这里二次收敛到本人
      (!isInterior.value || item.username === currentUsername.value),
  ),
);
const consumptionRows = computed(() =>
  consumptions.value.map((item, index) => ({ ...item, index: index + 1 })),
);
const couponRows = computed(() =>
  coupons.value.map((item, index) => ({ ...item, index: index + 1 })),
);

function toApiDate(value: string, end = false) {
  if (!value) return undefined;
  return `${value}T${end ? "23:59:59" : "00:00:00"}+08:00`;
}

function formatCents(cents: number) {
  return `￥${(cents / 100).toFixed(2)}`;
}

function formatDateTime(value?: string | null) {
  return value ? new Date(value).toLocaleString() : "-";
}

function formatDate(value?: string | null) {
  return value ? new Date(value).toLocaleDateString() : "-";
}

function formatPoints(value?: number | null) {
  const points = Number(value || 0);
  // 点数统一按整数展示，不显示小数点后数字
  return String(Math.round(points));
}

function remainingPointsPercent(order: BillingOrder) {
  const totalPoints = Number(order.total_points || 0);
  if (totalPoints <= 0) return 0;
  return Math.min(100, Math.max(0, Number(order.remaining_points || 0) / totalPoints * 100));
}

function orderStatusText(status: string) {
  if (status === "completed") return "已完成";
  if (status === "pending") return "未付费";
  if (status === "cancelled") return "已取消";
  return status;
}

function orderPointsStatusText(status: BillingOrder["points_status"]) {
  if (status === "active") return "可使用";
  if (status === "expired") return "已过期";
  if (status === "exhausted") return "已用完";
  return "未生效";
}

function orderPointsStatusClass(status: BillingOrder["points_status"]) {
  if (status === "active") return "badge-success";
  if (status === "expired") return "badge-warning";
  if (status === "exhausted") return "badge-info";
  return "badge-error";
}

function orderSourceText(source: string) {
  if (source === "gift") return "赠送";
  if (source === "recharge") return "充值";
  return source || "-";
}

function orderSourceClass(source: string) {
  if (source === "gift") return "badge-info";
  if (source === "recharge") return "badge-success";
  return "badge-info";
}

function couponStatusClass(status: string) {
  if (status === "未使用") return "badge-info";
  if (status === "已过期") return "badge-warning";
  if (status === "已使用") return "badge-success";
  return "badge-error";
}

function getApiErrorMessage(err: unknown, fallback: string) {
  const error = err as { response?: { data?: { detail?: string | { message?: string } } } };
  const detail = error.response?.data?.detail;
  return typeof detail === "string" ? detail : detail?.message || fallback;
}

async function loadProfile() {
  profile.value = await profileApi.getProfile();
  profileForm.nickname = profile.value.nickname ?? "";
  profileForm.city = profile.value.city ?? "";
  profileForm.company = profile.value.company ?? "";
  profileForm.bidding_industries = profile.value.bidding_industries ?? "";
}

async function saveProfile() {
  profile.value = await profileApi.updateProfile({
    nickname: profileForm.nickname,
    city: profileForm.city,
    company: profileForm.company,
    bidding_industries: profileForm.bidding_industries,
  });
  message.success("信息已保存");
}

async function changePassword() {
  if (!passwordForm.old_password || !passwordForm.new_password || !passwordForm.confirm_new_password) {
    message.warning("请输入旧密码和两次新密码");
    return;
  }
  if (passwordForm.new_password !== passwordForm.confirm_new_password) {
    message.warning("两次输入的新密码不一致");
    return;
  }
  try {
    await profileApi.changePassword(
      passwordForm.old_password,
      passwordForm.new_password,
      passwordForm.confirm_new_password,
    );
    passwordForm.old_password = "";
    passwordForm.new_password = "";
    passwordForm.confirm_new_password = "";
    message.success("密码已修改");
  } catch (err) {
    const error = err as { response?: { data?: { detail?: string } } };
    message.error(error.response?.data?.detail || "密码修改失败");
  }
}

async function loadOrders() {
  orders.value = await billingApi.listOrders({
    start_date: toApiDate(orderFilters.start_date),
    end_date: toApiDate(orderFilters.end_date, true),
    product_name: orderFilters.product_name || undefined,
    username: orderFilters.username || undefined,
    enterprise_name: orderFilters.enterprise_name || undefined,
  });
}

async function loadConsumptions() {
  consumptions.value = await billingApi.listConsumptions({
    start_date: toApiDate(consumptionFilters.start_date),
    end_date: toApiDate(consumptionFilters.end_date, true),
    project_name: consumptionFilters.project_name || undefined,
    username: consumptionFilters.username || undefined,
    enterprise_name: consumptionFilters.enterprise_name || undefined,
  });
}

async function loadCoupons() {
  coupons.value = await billingApi.listCoupons();
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
    couponCode.value = "";
    if (result.coupon?.status === "未使用" && (result.coupon.amount_cents > 0 || result.coupon.gift_points > 0)) {
      message.success("优惠券已导入，可在充值时使用");
    } else {
      message.success(`优惠券已导入，当前状态：${result.coupon?.status ?? "未知"}`);
    }
  } catch (err) {
    message.error(getApiErrorMessage(err, "优惠券导入失败"));
  } finally {
    couponImporting.value = false;
  }
}

async function showAllocations(record: ConsumptionRecord) {
  allocations.value = await billingApi.getConsumptionAllocations(record.id);
  allocationOpen.value = true;
}

async function loadAll() {
  loading.value = true;
  try {
    await Promise.all([loadProfile(), loadOrders(), loadConsumptions(), loadCoupons()]);
  } finally {
    loading.value = false;
  }
}

onMounted(() => {
  void loadAll();
});
</script>

<template>
  <div class="profile-center">
    <a-spin :spinning="loading">
      <a-tabs v-model:activeKey="activeKey" class="center-tabs">
        <a-tab-pane key="info" tab="我的信息">
          <div class="info-grid">
            <section class="panel">
              <h2>我的信息</h2>
              <a-form layout="vertical">
                <a-form-item label="登录账号">
                  <a-input :value="profile?.username" disabled />
                </a-form-item>
                <a-form-item label="用户昵称">
                  <a-input v-model:value="profileForm.nickname" />
                </a-form-item>
                <a-form-item label="所在城市">
                  <a-input v-model:value="profileForm.city" />
                </a-form-item>
                <a-form-item label="公司">
                  <a-input v-model:value="profileForm.company" />
                </a-form-item>
                <a-form-item label="常用投标行业">
                  <a-textarea v-model:value="profileForm.bidding_industries" :rows="4" />
                </a-form-item>
                <a-button type="primary" @click="saveProfile">保存信息</a-button>
              </a-form>
            </section>
          </div>
        </a-tab-pane>

        <a-tab-pane key="active-orders" tab="当前扣费订单">
          <div v-if="activeOrderCards.length" class="active-order-grid">
            <article v-for="order in activeOrderCards" :key="order.id" class="active-order-card">
              <div class="active-order-card__header">
                <h2>{{ order.product_name || "未命名订单" }}</h2>
                <span class="badge badge-success">使用中</span>
              </div>
              <dl class="active-order-card__dates">
                <div>
                  <dt>创建时间</dt>
                  <dd>{{ formatDateTime(order.created_at) }}</dd>
                </div>
                <div>
                  <dt>到期时间</dt>
                  <dd>{{ formatDateTime(order.points_expires_at) }}</dd>
                </div>
              </dl>
              <div class="active-order-card__balance">
                <div class="active-order-card__balance-label">
                  <span>剩余点数</span>
                  <strong>
                    {{ formatPoints(order.remaining_points) }} / {{ formatPoints(order.total_points) }} 点
                  </strong>
                </div>
                <a-progress
                  :percent="remainingPointsPercent(order)"
                  :show-info="false"
                  stroke-color="#1677ff"
                />
              </div>
            </article>
          </div>
          <a-empty v-else description="暂无可用订单" class="active-order-empty" />
        </a-tab-pane>

        <a-tab-pane key="password" tab="修改密码">
          <div class="password-panel">
            <section class="panel">
              <h2>修改密码</h2>
              <a-form layout="vertical">
                <a-form-item label="旧密码">
                  <a-input-password v-model:value="passwordForm.old_password" />
                </a-form-item>
                <a-form-item label="新密码">
                  <a-input-password v-model:value="passwordForm.new_password" />
                </a-form-item>
                <a-form-item label="确认新密码">
                  <a-input-password v-model:value="passwordForm.confirm_new_password" />
                </a-form-item>
                <a-button type="primary" @click="changePassword">修改密码</a-button>
              </a-form>
            </section>
          </div>
        </a-tab-pane>

        <a-tab-pane key="orders" tab="订单与消费">
          <a-tabs v-model:activeKey="orderActiveKey">
            <a-tab-pane key="orders" tab="订单记录">
              <div class="query-row">
                <input v-model="orderFilters.start_date" type="date" />
                <input v-model="orderFilters.end_date" type="date" />
                <a-input v-model:value="orderFilters.product_name" placeholder="产品名称" class="query-input" />
                <template v-if="isInterior">
                  <a-input v-model:value="orderFilters.username" placeholder="用户名" class="query-input" />
                  <a-input v-model:value="orderFilters.enterprise_name" placeholder="企业" class="query-input" />
                </template>
                <a-button type="primary" @click="loadOrders">查询</a-button>
              </div>
              <a-table :columns="orderColumns" :data-source="orderRows" row-key="id" size="middle" :scroll="{ x: 1800 }">
                <template #bodyCell="{ column, record }">
                  <template v-if="column.dataIndex === 'order_no'">
                    {{ record.order_no || "-" }}
                  </template>
                  <template v-else-if="column.dataIndex === 'username'">
                    {{ record.username || "-" }}
                  </template>
                  <template v-else-if="column.dataIndex === 'enterprise_name'">
                    {{ record.enterprise_name || "-" }}
                  </template>
                  <template v-else-if="column.dataIndex === 'source'">
                    <span :class="['badge', orderSourceClass(record.source)]">
                      {{ orderSourceText(record.source) }}
                    </span>
                  </template>
                  <template v-else-if="column.dataIndex === 'created_at'">
                    {{ formatDateTime(record.created_at) }}
                  </template>
                  <template v-else-if="column.dataIndex === 'status'">
                    <span :class="['badge', record.status === 'completed' ? 'badge-success' : 'badge-warning']">
                      {{ orderStatusText(record.status) }}
                    </span>
                  </template>
                  <template v-else-if="column.dataIndex === 'order_amount_cents'">
                    {{ formatCents(record.order_amount_cents) }}
                  </template>
                  <template v-else-if="column.dataIndex === 'actual_payment_cents'">
                    {{ formatCents(record.actual_payment_cents) }}
                  </template>
                  <template v-else-if="column.dataIndex === 'coupon_amount_cents'">
                    {{ record.coupon_amount_cents ? formatCents(record.coupon_amount_cents) : "-" }}
                  </template>
                  <template v-else-if="column.dataIndex === 'points_amount_cents'">
                    {{ record.points_amount_cents ? formatCents(record.points_amount_cents) : "-" }}
                  </template>
                  <template v-else-if="column.dataIndex === 'consumed_points' || column.dataIndex === 'remaining_points' || column.dataIndex === 'recharge_points' || column.dataIndex === 'gift_points'">
                    {{ formatPoints(record[column.dataIndex]) }}点
                  </template>
                  <template v-else-if="column.dataIndex === 'points_status'">
                    <span :class="['badge', orderPointsStatusClass(record.points_status)]">
                      {{ orderPointsStatusText(record.points_status) }}
                    </span>
                  </template>
                  <template v-else-if="column.dataIndex === 'points_expires_at'">
                    {{ formatDateTime(record.points_expires_at) }}
                  </template>
                  <template v-else-if="column.dataIndex === 'current_balance_wen'">
                    {{ record.current_balance_wen != null ? `${formatPoints(record.current_balance_wen)}点` : "-" }}
                  </template>
                </template>
              </a-table>
            </a-tab-pane>

            <a-tab-pane key="consumptions" tab="消费记录">
              <div class="query-row">
                <input v-model="consumptionFilters.start_date" type="date" />
                <input v-model="consumptionFilters.end_date" type="date" />
                <a-input v-model:value="consumptionFilters.project_name" placeholder="项目名称" class="query-input" />
                <template v-if="isInterior">
                  <a-input v-model:value="consumptionFilters.username" placeholder="用户名" class="query-input" />
                  <a-input v-model:value="consumptionFilters.enterprise_name" placeholder="企业" class="query-input" />
                </template>
                <a-button type="primary" @click="loadConsumptions">查询</a-button>
              </div>
              <a-table :columns="consumptionColumns" :data-source="consumptionRows" row-key="id" size="middle" :scroll="{ x: 1500 }">
                <template #bodyCell="{ column, record }">
                  <template v-if="column.dataIndex === 'username'">
                    {{ record.username || "-" }}
                  </template>
                  <template v-else-if="column.dataIndex === 'enterprise_name'">
                    {{ record.enterprise_name || "-" }}
                  </template>
                  <template v-else-if="column.dataIndex === 'consumed_at'">
                    {{ formatDateTime(record.consumed_at) }}
                  </template>
                  <template v-else-if="column.dataIndex === 'settlement_order_nos'">
                    {{ record.settlement_order_nos || "-" }}
                  </template>
                  <template v-else-if="column.dataIndex === 'points_before'">
                    {{ formatPoints((Number(record.recharge_balance_before) || 0) + (Number(record.gift_balance_before) || 0)) }}点
                  </template>
                  <template v-else-if="column.dataIndex === 'sales_points' || column.dataIndex === 'gift_points_used' || column.dataIndex === 'recharge_points_used'">
                    {{ formatPoints(record[column.dataIndex]) }}点
                  </template>
                  <template v-else-if="column.dataIndex === 'points_after'">
                    {{ formatPoints((Number(record.recharge_balance_after) || 0) + (Number(record.gift_balance_after) || 0)) }}点
                  </template>
                  <template v-else-if="column.dataIndex === 'earned_points'">
                    {{ record.earned_points }}分
                  </template>
                  <template v-else-if="column.dataIndex === 'actions'">
                    <a-button type="link" size="small" @click="showAllocations(record)">查看</a-button>
                  </template>
                </template>
              </a-table>
            </a-tab-pane>
          </a-tabs>
        </a-tab-pane>

        <a-tab-pane key="coupons" tab="优惠券">
          <div class="coupon-toolbar">
            <a-input
              v-model:value="couponCode"
              placeholder="输入优惠券兑换码"
              class="coupon-code-input"
              :disabled="couponImporting"
              @pressEnter="importCoupon"
            />
            <a-button type="primary" :loading="couponImporting" @click="importCoupon">
              导入优惠券
            </a-button>
          </div>
          <a-table
            :columns="couponColumns"
            :data-source="couponRows"
            row-key="id"
            size="middle"
            :scroll="{ x: 760 }"
          >
            <template #bodyCell="{ column, record }">
              <template v-if="column.dataIndex === 'code'">
                {{ record.code || "-" }}
              </template>
              <template v-if="column.dataIndex === 'amount_cents'">
                {{ record.benefit_type === 'cash' ? formatCents(record.amount_cents) : '-' }}
              </template>
              <template v-else-if="column.dataIndex === 'gift_points'">
                {{ record.benefit_type === 'gift' ? `${formatPoints(record.gift_points)}点` : '-' }}
              </template>
              <template v-else-if="column.dataIndex === 'valid_until'">
                {{ formatDate(record.valid_until) }}
              </template>
              <template v-else-if="column.dataIndex === 'status'">
                <span :class="['badge', couponStatusClass(record.status)]">
                  {{ record.status }}
                </span>
              </template>
            </template>
          </a-table>
        </a-tab-pane>
      </a-tabs>
      <a-modal v-model:open="allocationOpen" title="本次消费扣点明细" :footer="null" width="760px">
        <a-table :data-source="allocations" row-key="id" size="small" :pagination="false">
          <a-table-column title="点数类型" data-index="lot_type"><template #default="{ text }">{{ text === 'gift' ? '赠送点数' : '充值点数' }}</template></a-table-column>
          <a-table-column title="扣除点数" data-index="points"><template #default="{ text }">{{ formatPoints(text) }}</template></a-table-column>
          <a-table-column title="每点折合价值" data-index="unit_value_yuan"><template #default="{ text }">￥{{ Number(text || 0).toFixed(6) }}</template></a-table-column>
          <a-table-column title="折合收入" data-index="folded_income_yuan"><template #default="{ text }">￥{{ Number(text || 0).toFixed(2) }}</template></a-table-column>
          <a-table-column title="该批次到期时间" data-index="expires_at"><template #default="{ text }">{{ formatDateTime(text) }}</template></a-table-column>
        </a-table>
      </a-modal>
    </a-spin>
  </div>
</template>

<style scoped>
.center-tabs {
  background: var(--bg1);
  border: 1px solid var(--line);
  border-radius: var(--r);
  padding: 16px 20px 22px;
}

.info-grid {
  max-width: 760px;
}

.password-panel {
  max-width: 480px;
}

.active-order-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 16px;
  max-height: 70vh;
  overflow-y: auto;
}

.active-order-card {
  min-width: 0;
  padding: 20px;
  border: 1px solid var(--line);
  border-radius: var(--r);
  background: var(--bg1);
  box-shadow: 0 4px 14px rgb(0 0 0 / 4%);
}

.active-order-card__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 18px;
}

.active-order-card__header h2 {
  min-width: 0;
  margin: 0;
  color: var(--text);
  font-size: 1.05rem;
  line-height: 1.5;
  overflow-wrap: anywhere;
}

.active-order-card__dates {
  display: grid;
  gap: 10px;
  margin: 0 0 20px;
}

.active-order-card__dates > div,
.active-order-card__balance-label {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 16px;
}

.active-order-card__dates dt,
.active-order-card__balance-label span {
  flex: 0 0 auto;
  color: var(--muted);
}

.active-order-card__dates dd {
  min-width: 0;
  margin: 0;
  color: var(--text);
  text-align: right;
}

.active-order-card__balance-label {
  margin-bottom: 6px;
}

.active-order-card__balance-label strong {
  color: var(--text);
  font-weight: 600;
}

.active-order-empty {
  padding: 48px 0 36px;
}

.panel {
  border: 1px solid var(--line);
  border-radius: var(--r);
  padding: 18px;
  background: var(--bg1);
}

.panel h2 {
  font-size: 1.05rem;
  margin: 0 0 16px;
}

.query-row {
  display: flex;
  gap: 10px;
  align-items: center;
  margin-bottom: 14px;
  flex-wrap: wrap;
}

.query-row input[type="date"] {
  height: 32px;
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  padding: 0 10px;
  background: var(--bg1);
  color: var(--text);
  font-family: inherit;
}

.query-input {
  width: 220px;
}

.coupon-toolbar {
  display: flex;
  gap: 10px;
  align-items: center;
  margin-bottom: 14px;
  flex-wrap: wrap;
}

.coupon-code-input {
  width: 320px;
  max-width: 100%;
}

@media (max-width: 576px) {
  .active-order-grid {
    grid-template-columns: 1fr;
  }

  .active-order-card {
    padding: 16px;
  }

  .active-order-card__dates > div {
    align-items: flex-start;
    flex-direction: column;
    gap: 2px;
  }

  .active-order-card__dates dd {
    text-align: left;
  }
}

</style>
