<script setup lang="ts">
import { ref, computed, onMounted } from "vue";
import { message, Modal } from "ant-design-vue";
import { announcementApi } from "@/api/client";
import type {
  AnnouncementManage,
  AnnouncementSeverity,
  AnnouncementCreateRequest,
} from "@/types";

/** 系统公告管理（内部用户）：发布 / 编辑 / 上下线 / 删除，并查看每条已读人数。 */

const list = ref<AnnouncementManage[]>([]);
const loading = ref(false);

const modalOpen = ref(false);
const editing = ref<AnnouncementManage | null>(null);
const submitting = ref(false);

interface FormState {
  title: string;
  content: string;
  severity: AnnouncementSeverity;
  is_active: boolean;
  published_at: string; // datetime-local 字符串
  expires_at: string; // datetime-local 字符串
}

const form = ref<FormState>(emptyForm());

function emptyForm(): FormState {
  return {
    title: "",
    content: "",
    severity: "info",
    is_active: true,
    published_at: "",
    expires_at: "",
  };
}

const severityOptions: { value: AnnouncementSeverity; label: string; color: string }[] = [
  { value: "info", label: "普通", color: "#1677ff" },
  { value: "important", label: "重要", color: "#fa8c16" },
  { value: "urgent", label: "紧急", color: "#d7041a" },
];

const columns = [
  { title: "标题", dataIndex: "title", key: "title", ellipsis: true },
  { title: "级别", dataIndex: "severity", key: "severity", width: 90 },
  { title: "状态", dataIndex: "is_active", key: "is_active", width: 90 },
  { title: "发布时间", dataIndex: "published_at", key: "published_at", width: 180 },
  { title: "过期时间", dataIndex: "expires_at", key: "expires_at", width: 180 },
  { title: "已读", dataIndex: "read", key: "read", width: 110 },
  { title: "操作", key: "action", width: 200 },
];

const modalTitle = computed(() => (editing.value ? "编辑公告" : "发布公告"));

async function fetchList() {
  loading.value = true;
  try {
    list.value = await announcementApi.listForManage({ include_inactive: true });
  } catch (e: unknown) {
    message.error(extractErr(e, "加载公告列表失败"));
  } finally {
    loading.value = false;
  }
}

function openCreate() {
  editing.value = null;
  form.value = emptyForm();
  modalOpen.value = true;
}

function openEdit(row: AnnouncementManage) {
  editing.value = row;
  form.value = {
    title: row.title,
    content: row.content,
    severity: row.severity,
    is_active: row.is_active,
    published_at: isoToLocalInput(row.published_at),
    expires_at: isoToLocalInput(row.expires_at),
  };
  modalOpen.value = true;
}

async function submit() {
  if (!form.value.title.trim()) {
    message.warning("请填写标题");
    return;
  }
  if (!form.value.content.trim()) {
    message.warning("请填写正文");
    return;
  }
  submitting.value = true;
  try {
    const payload: AnnouncementCreateRequest = {
      title: form.value.title.trim(),
      content: form.value.content.trim(),
      severity: form.value.severity,
      is_active: form.value.is_active,
      published_at: localInputToIso(form.value.published_at),
      expires_at: localInputToIso(form.value.expires_at),
    };
    if (editing.value) {
      await announcementApi.update(editing.value.id, payload);
      message.success("已保存");
    } else {
      await announcementApi.create(payload);
      message.success("已发布");
    }
    modalOpen.value = false;
    await fetchList();
  } catch (e: unknown) {
    message.error(extractErr(e, "保存失败"));
  } finally {
    submitting.value = false;
  }
}

async function toggleActive(row: AnnouncementManage) {
  try {
    await announcementApi.update(row.id, { is_active: !row.is_active });
    message.success(row.is_active ? "已下线" : "已上线");
    await fetchList();
  } catch (e: unknown) {
    message.error(extractErr(e, "操作失败"));
  }
}

function confirmDelete(row: AnnouncementManage) {
  Modal.confirm({
    title: "删除公告",
    content: `确定删除「${row.title}」吗？已读记录将一并删除，此操作不可撤销。`,
    okText: "删除",
    okType: "danger",
    cancelText: "取消",
    async onOk() {
      try {
        await announcementApi.remove(row.id);
        message.success("已删除");
        await fetchList();
      } catch (e: unknown) {
        message.error(extractErr(e, "删除失败"));
      }
    },
  });
}

// ---- 工具 ----

function severityLabel(s: AnnouncementSeverity): string {
  return severityOptions.find((o) => o.value === s)?.label ?? s;
}
function severityColor(s: AnnouncementSeverity): string {
  return severityOptions.find((o) => o.value === s)?.color ?? "#1677ff";
}

function isoToLocalInput(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(
    d.getHours(),
  )}:${pad(d.getMinutes())}`;
}

function localInputToIso(v: string): string | null {
  if (!v) return null;
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return null;
  return d.toISOString();
}

function formatTime(iso: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("zh-CN", { hour12: false });
  } catch {
    return iso;
  }
}

function extractErr(e: unknown, fallback: string): string {
  if (e && typeof e === "object" && "response" in e) {
    const resp = (e as { response?: { data?: { detail?: string } } }).response;
    return resp?.data?.detail || fallback;
  }
  return e instanceof Error ? e.message : fallback;
}

onMounted(fetchList);
</script>

<template>
  <div class="announce-manage">
    <div class="page-head">
      <h2 class="page-title">系统公告管理</h2>
      <a-button type="primary" @click="openCreate">+ 发布公告</a-button>
    </div>

    <a-table
      :columns="columns"
      :data-source="list"
      :loading="loading"
      :pagination="{ pageSize: 20, showSizeChanger: false }"
      row-key="id"
      size="middle"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'severity'">
          <span
            class="sev-tag"
            :style="{ color: severityColor(record.severity), borderColor: severityColor(record.severity) }"
          >
            {{ severityLabel(record.severity) }}
          </span>
        </template>
        <template v-else-if="column.key === 'is_active'">
          <a-tag :color="record.is_active ? 'green' : 'default'">
            {{ record.is_active ? "上线" : "下线" }}
          </a-tag>
        </template>
        <template v-else-if="column.key === 'published_at'">
          {{ formatTime(record.published_at) }}
        </template>
        <template v-else-if="column.key === 'expires_at'">
          {{ formatTime(record.expires_at) }}
        </template>
        <template v-else-if="column.key === 'read'">
          {{ record.read_count }} / {{ record.total_users }}
        </template>
        <template v-else-if="column.key === 'action'">
          <a-button type="link" size="small" @click="openEdit(record)">编辑</a-button>
          <a-button type="link" size="small" @click="toggleActive(record)">
            {{ record.is_active ? "下线" : "上线" }}
          </a-button>
          <a-button type="link" size="small" danger @click="confirmDelete(record)">
            删除
          </a-button>
        </template>
      </template>
    </a-table>

    <a-modal
      :open="modalOpen"
      :title="modalTitle"
      :confirm-loading="submitting"
      :width="600"
      :destroy-on-close="false"
      ok-text="保存"
      cancel-text="取消"
      @ok="submit"
      @cancel="modalOpen = false"
    >
      <div class="form-row">
        <label class="form-label">标题</label>
        <a-input v-model:value="form.title" placeholder="公告标题" :maxlength="200" />
      </div>
      <div class="form-row">
        <label class="form-label">正文</label>
        <a-textarea
          v-model:value="form.content"
          placeholder="公告正文"
          :auto-size="{ minRows: 4, maxRows: 10 }"
        />
      </div>
      <div class="form-row form-row--inline">
        <div class="form-cell">
          <label class="form-label">级别</label>
          <a-select v-model:value="form.severity" style="width: 100%">
            <a-select-option
              v-for="o in severityOptions"
              :key="o.value"
              :value="o.value"
            >
              {{ o.label }}
            </a-select-option>
          </a-select>
        </div>
        <div class="form-cell">
          <label class="form-label">是否上线</label>
          <a-switch v-model:checked="form.is_active" />
        </div>
      </div>
      <div class="form-row form-row--inline">
        <div class="form-cell">
          <label class="form-label">发布时间（留空=立即）</label>
          <input class="dt-input" type="datetime-local" v-model="form.published_at" />
        </div>
        <div class="form-cell">
          <label class="form-label">过期时间（可选）</label>
          <input class="dt-input" type="datetime-local" v-model="form.expires_at" />
        </div>
      </div>
    </a-modal>
  </div>
</template>

<style scoped>
.announce-manage {
  background: #fff;
  border-radius: 12px;
  padding: 20px 24px;
}

.page-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.page-title {
  margin: 0;
  font-size: 18px;
  font-weight: 700;
  color: #222;
}

.sev-tag {
  display: inline-block;
  padding: 0 8px;
  border: 1px solid currentColor;
  border-radius: 4px;
  font-size: 12px;
  line-height: 20px;
  font-weight: 600;
}

.form-row {
  margin-bottom: 14px;
}

.form-row--inline {
  display: flex;
  gap: 16px;
}

.form-cell {
  flex: 1 1 0;
  min-width: 0;
}

.form-label {
  display: block;
  font-size: 13px;
  color: #666;
  margin-bottom: 6px;
}

.dt-input {
  width: 100%;
  height: 32px;
  padding: 0 11px;
  border: 1px solid #d9d9d9;
  border-radius: 6px;
  font-size: 14px;
  color: #333;
  background: #fff;
}

.dt-input:focus {
  outline: none;
  border-color: #d7041a;
  box-shadow: 0 0 0 2px rgba(215, 4, 26, 0.12);
}
</style>
