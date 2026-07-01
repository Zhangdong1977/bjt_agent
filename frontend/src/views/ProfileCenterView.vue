<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { message } from "ant-design-vue";
import { billingApi, profileApi } from "@/api/client";
import type { BillingOrder, ConsumptionRecord, Coupon, User } from "@/types";

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

const orderFilters = reactive({
  start_date: "",
  end_date: "",
  product_name: "",
});
const consumptionFilters = reactive({
  start_date: "",
  end_date: "",
  project_name: "",
});

const orderColumns = [
  { title: "序号", dataIndex: "index", width: 70 },
  { title: "订单编号", dataIndex: "order_no" },
  { title: "产品名称", dataIndex: "product_name" },
  { title: "下单时间", dataIndex: "created_at" },
  { title: "订单状态", dataIndex: "status" },
  { title: "订单金额", dataIndex: "order_amount_cents" },
  { title: "实际付款金额", dataIndex: "actual_payment_cents" },
  { title: "优惠券", dataIndex: "coupon_amount_cents" },
  { title: "订单有效期", dataIndex: "expires_at" },
  { title: "充值后余额", dataIndex: "current_balance_wen" },
];

const consumptionColumns = [
  { title: "序号", dataIndex: "index", width: 70 },
  { title: "消费时间", dataIndex: "consumed_at" },
  { title: "项目名称", dataIndex: "project_name" },
  { title: "消耗点数", dataIndex: "consumed_wen" },
  { title: "获得积分", dataIndex: "earned_points" },
  { title: "使用人", dataIndex: "used_by" },
];

const couponColumns = [
  { title: "序号", dataIndex: "index", width: 70 },
  { title: "兑换码", dataIndex: "code" },
  { title: "优惠券金额", dataIndex: "amount_cents" },
  { title: "有效期", dataIndex: "valid_until" },
  { title: "状态", dataIndex: "status" },
];

const orderRows = computed(() =>
  orders.value.map((item, index) => ({ ...item, index: index + 1 })),
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

function orderStatusText(status: string) {
  if (status === "completed") return "已完成";
  if (status === "pending") return "未付费";
  if (status === "cancelled") return "已取消";
  return status;
}

function couponStatusClass(status: string) {
  if (status === "未使用") return "badge-info";
  if (status === "已过期") return "badge-warning";
  if (status === "已使用") return "badge-success";
  return "badge-error";
}

function getApiErrorMessage(err: unknown, fallback: string) {
  const error = err as { response?: { data?: { detail?: string } } };
  return error.response?.data?.detail || fallback;
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
  });
}

async function loadConsumptions() {
  consumptions.value = await billingApi.listConsumptions({
    start_date: toApiDate(consumptionFilters.start_date),
    end_date: toApiDate(consumptionFilters.end_date, true),
    project_name: consumptionFilters.project_name || undefined,
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
    if (result.coupon?.status === "未使用" && result.coupon.amount_cents > 0) {
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
    <a-breadcrumb class="breadcrumb">
      <a-breadcrumb-item>首页</a-breadcrumb-item>
      <a-breadcrumb-item>个人中心</a-breadcrumb-item>
    </a-breadcrumb>

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
                <a-button type="primary" @click="loadOrders">查询</a-button>
              </div>
              <a-table :columns="orderColumns" :data-source="orderRows" row-key="id" size="middle" :scroll="{ x: 1120 }">
                <template #bodyCell="{ column, record }">
                  <template v-if="column.dataIndex === 'created_at'">
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
                  <template v-else-if="column.dataIndex === 'expires_at'">
                    {{ formatDateTime(record.expires_at) }}
                  </template>
                  <template v-else-if="column.dataIndex === 'current_balance_wen'">
                    {{ record.current_balance_wen != null ? `${record.current_balance_wen}文` : "-" }}
                  </template>
                </template>
              </a-table>
            </a-tab-pane>

            <a-tab-pane key="consumptions" tab="消费记录">
              <div class="query-row">
                <input v-model="consumptionFilters.start_date" type="date" />
                <input v-model="consumptionFilters.end_date" type="date" />
                <a-input v-model:value="consumptionFilters.project_name" placeholder="项目名称" class="query-input" />
                <a-button type="primary" @click="loadConsumptions">查询</a-button>
              </div>
              <a-table :columns="consumptionColumns" :data-source="consumptionRows" row-key="id" size="middle">
                <template #bodyCell="{ column, record }">
                  <template v-if="column.dataIndex === 'consumed_at'">
                    {{ formatDateTime(record.consumed_at) }}
                  </template>
                  <template v-else-if="column.dataIndex === 'consumed_wen'">
                    {{ record.consumed_wen }}文
                  </template>
                  <template v-else-if="column.dataIndex === 'earned_points'">
                    {{ record.earned_points }}分
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
                {{ formatCents(record.amount_cents) }}
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
    </a-spin>
  </div>
</template>

<style scoped>
.profile-center {
  max-width: 1280px;
  margin: 0 auto;
}

.breadcrumb {
  margin-bottom: 18px;
}

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

</style>
