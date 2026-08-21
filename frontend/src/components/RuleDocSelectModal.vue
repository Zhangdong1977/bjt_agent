<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { reviewApi } from '@/api/client'
import type { RuleDocInfo } from '@/types'

const props = defineProps<{
  open: boolean
}>()

const emit = defineEmits<{
  (e: 'update:open', value: boolean): void
  (e: 'confirm', selectedRuleDocs: string[]): void
}>()

const loading = ref(false)
const errorMsg = ref('')
const ruleDocs = ref<RuleDocInfo[]>([])
const checked = ref<string[]>([])

const allNames = computed(() => ruleDocs.value.map((d) => d.name))
const defaultNames = computed(() =>
  ruleDocs.value.filter((d) => d.default_selected).map((d) => d.name),
)
const allChecked = computed(
  () => ruleDocs.value.length > 0 && checked.value.length === ruleDocs.value.length,
)
const indeterminate = computed(() => checked.value.length > 0 && !allChecked.value)

async function loadRuleDocs() {
  loading.value = true
  errorMsg.value = ''
  try {
    ruleDocs.value = await reviewApi.getRuleDocs()
    checked.value = defaultNames.value
  } catch (e: any) {
    errorMsg.value = e?.response?.data?.detail || '加载检查项列表失败，请稍后重试'
  } finally {
    loading.value = false
  }
}

// 每次打开都重新拉取列表并恢复默认勾选，规则库可能在上次打开后有变化
watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) {
      loadRuleDocs()
    } else {
      ruleDocs.value = []
      checked.value = []
      errorMsg.value = ''
    }
  },
)

function toggleAll() {
  checked.value = allChecked.value ? [] : allNames.value
}

function resetDefaults() {
  checked.value = defaultNames.value
}

function handleConfirm() {
  if (loading.value || errorMsg.value) return
  if (checked.value.length === 0) {
    message.warning('请至少选择一个检查项大类')
    return
  }
  emit('confirm', [...checked.value])
  emit('update:open', false)
}

function handleClose() {
  emit('update:open', false)
}
</script>

<template>
  <a-modal
    :open="open"
    title="选择检查项"
    width="620px"
    :destroy-on-close="true"
    @cancel="handleClose"
  >
    <a-spin :spinning="loading">
      <div v-if="errorMsg" class="rule-error">
        <a-alert type="error" show-icon :message="errorMsg" />
        <a-button class="rule-retry" @click="loadRuleDocs">重试</a-button>
      </div>

      <div v-else>
        <p class="rule-tip">
          请选择本次需要检查的项目大类，未勾选的大类将不进行检查（默认不检查签字盖章）。
          <span class="rule-count">已选 {{ checked.length }} / {{ ruleDocs.length }} 项</span>
        </p>

        <div class="rule-toolbar">
          <a-checkbox
            :checked="allChecked"
            :indeterminate="indeterminate"
            @change="toggleAll"
          >
            全选
          </a-checkbox>
          <a-button type="link" size="small" class="rule-reset" @click="resetDefaults">
            恢复默认
          </a-button>
        </div>

        <a-checkbox-group v-model:value="checked" class="rule-group">
          <a-checkbox v-for="doc in ruleDocs" :key="doc.name" :value="doc.name" class="rule-item">
            {{ doc.stem }}
          </a-checkbox>
        </a-checkbox-group>
      </div>
    </a-spin>

    <template #footer>
      <a-button @click="handleClose">取消</a-button>
      <a-button
        type="primary"
        :disabled="!!errorMsg || checked.length === 0"
        @click="handleConfirm"
      >
        开始检查
      </a-button>
    </template>
  </a-modal>
</template>

<style scoped>
.rule-tip {
  margin: 0 0 8px;
  color: var(--sub, #888);
  font-size: 13px;
  line-height: 1.6;
}

.rule-count {
  margin-left: 8px;
  color: var(--sub, #999);
}

.rule-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 0;
  margin-bottom: 4px;
  border-bottom: 1px solid var(--line, #eee);
}

.rule-reset {
  padding: 0;
}

.rule-group {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 4px 16px;
  max-height: 320px;
  overflow-y: auto;
  padding: 8px 0 0;
}

/* 大类名较长（如 "A002 检查投标文件结构与招标文件提供的模板一致性"），允许换行 */
.rule-item {
  align-items: flex-start;
  margin-right: 0;
  white-space: normal;
  line-height: 1.5;
}

.rule-item :deep(span) {
  font-size: 13px;
}

.rule-error {
  padding: 12px 0;
  text-align: center;
}

.rule-retry {
  margin-top: 12px;
}
</style>
