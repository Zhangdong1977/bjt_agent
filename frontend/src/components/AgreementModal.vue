<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { renderMarkdown } from '@/utils/markdown'

// 协议类型：用户服务协议 / 隐私政策
type AgreementType = 'service' | 'privacy'

const props = defineProps<{
  open: boolean
  type: AgreementType
}>()

const emit = defineEmits<{
  'update:open': [value: boolean]
}>()

// 文件名 ↔ 标题 ↔ URL 映射
const META: Record<AgreementType, { title: string; url: string }> = {
  service: { title: '标捷通用户服务协议', url: '/agreements/user-service.md' },
  privacy: { title: '标捷通隐私政策', url: '/agreements/privacy.md' },
}

const title = computed(() => META[props.type].title)

// 拉取的原始 md 文本，按类型缓存避免重复请求
const cache = ref<Record<AgreementType, string>>({
  service: '',
  privacy: '',
})
const loading = ref(false)

const html = computed(() => renderMarkdown(cache.value[props.type]))

async function load() {
  if (cache.value[props.type]) return
  loading.value = true
  try {
    const resp = await fetch(META[props.type].url)
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    cache.value[props.type] = await resp.text()
  } catch {
    cache.value[props.type] = '协议内容加载失败，请稍后重试。'
  } finally {
    loading.value = false
  }
}

// 打开时按需拉取
watch(
  () => props.open,
  (open) => {
    if (open) load()
  },
)
</script>

<template>
  <a-modal
    :open="open"
    :title="title"
    :width="720"
    :footer="null"
    :destroy-on-close="false"
    wrap-class-name="agreement-modal"
    @update:open="(v: boolean) => emit('update:open', v)"
  >
    <a-spin :spinning="loading">
      <div class="agreement-body" v-html="html"></div>
    </a-spin>
  </a-modal>
</template>

<style scoped>
/* 协议正文容器：滚动、紧凑排版 */
.agreement-body {
  max-height: 64vh;
  overflow-y: auto;
  padding: 4px 8px 8px 0;
  color: #333;
  font-size: 14px;
  line-height: 1.9;
  text-align: justify;
}

.agreement-body :deep(h1),
.agreement-body :deep(h2),
.agreement-body :deep(h3),
.agreement-body :deep(h4) {
  font-weight: 600;
  color: #222;
  line-height: 1.5;
  margin: 18px 0 10px;
}

.agreement-body :deep(h1) {
  font-size: 20px;
  text-align: center;
}

.agreement-body :deep(h2) {
  font-size: 16px;
}

.agreement-body :deep(h3) {
  font-size: 15px;
  color: #D7041A;
}

.agreement-body :deep(h4) {
  font-size: 14px;
}

.agreement-body :deep(p) {
  margin: 8px 0;
}

.agreement-body :deep(strong) {
  font-weight: 600;
  color: #111;
}

.agreement-body :deep(ul),
.agreement-body :deep(ol) {
  margin: 8px 0;
  padding-left: 22px;
}

.agreement-body :deep(li) {
  margin: 4px 0;
}

.agreement-body :deep(a) {
  color: #D7041A;
  text-decoration: none;
}

.agreement-body :deep(hr) {
  border: 0;
  border-top: 1px solid #e4e6f1;
  margin: 16px 0;
}

.agreement-body :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: 10px 0;
}

.agreement-body :deep(th),
.agreement-body :deep(td) {
  border: 1px solid #e4e6f1;
  padding: 6px 10px;
  text-align: left;
}

.agreement-body :deep(th) {
  background: #f7f8fa;
  font-weight: 600;
}
</style>
