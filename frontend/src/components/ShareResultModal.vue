<script setup lang="ts">
import { ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { shareApi, type ShareTokenInfo } from '@/api/client'

const props = defineProps<{
  open: boolean
  projectId: string
  taskId: string
}>()

const emit = defineEmits<{
  (e: 'update:open', value: boolean): void
}>()

const loading = ref(false)
const tokenInfo = ref<ShareTokenInfo | null>(null)
const errorMsg = ref('')
// 完整可访问 URL：origin + /shared/{token}
const shareUrl = ref('')

async function ensureToken() {
  if (!props.projectId || !props.taskId) return
  loading.value = true
  errorMsg.value = ''
  try {
    const info = await shareApi.createToken(props.projectId, props.taskId)
    tokenInfo.value = info
    // share_url 是站内相对路径（/shared/{token}），拼成绝对链接便于二维码/复制。
    shareUrl.value = `${window.location.origin}${info.share_url}`
  } catch (e: any) {
    errorMsg.value = e?.response?.data?.detail || '生成分享链接失败，请稍后重试'
  } finally {
    loading.value = false
  }
}

// 打开时拉取令牌；关闭后重置以便下次重新生成。
watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) {
      ensureToken()
    } else {
      tokenInfo.value = null
      shareUrl.value = ''
      errorMsg.value = ''
    }
  },
)

async function copyLink() {
  if (!shareUrl.value) return
  try {
    await navigator.clipboard.writeText(shareUrl.value)
    message.success('链接已复制到剪贴板')
  } catch {
    // clipboard API 在非 https / 旧浏览器下可能不可用，回退到选中文本。
    message.warning('复制失败，请手动选中链接复制')
  }
}

function handleClose() {
  emit('update:open', false)
}
</script>

<template>
  <a-modal
    :open="open"
    title="分享审查结果"
    :footer="null"
    width="420px"
    :destroy-on-close="true"
    @cancel="handleClose"
  >
    <a-spin :spinning="loading">
      <div v-if="errorMsg" class="share-error">
        <a-alert type="error" show-icon :message="errorMsg" />
      </div>

      <div v-else-if="tokenInfo" class="share-body">
        <p class="share-tip">
          复制链接或扫码二维码分享给他人。对方需登录账号后即可查看本次审查结果。
        </p>

        <div class="share-link-row">
          <a-input :value="shareUrl" read-only />
          <a-button type="primary" @click="copyLink">复制</a-button>
        </div>

        <div class="qr-box">
          <a-qrcode :value="shareUrl" :size="190" />
        </div>

        <p v-if="tokenInfo.expires_at" class="share-expire">
          有效期至：{{ new Date(tokenInfo.expires_at).toLocaleString('zh-CN') }}
        </p>
      </div>
    </a-spin>
  </a-modal>
</template>

<style scoped>
.share-body {
  padding: 4px 0;
}

.share-tip {
  margin: 0 0 12px;
  color: var(--sub, #888);
  font-size: 13px;
  line-height: 1.6;
}

.share-link-row {
  display: flex;
  gap: 8px;
  margin-bottom: 20px;
}

.qr-box {
  display: flex;
  justify-content: center;
  padding: 8px;
  background: #fff;
  border: 1px solid var(--line, #eee);
  border-radius: 8px;
}

.share-expire {
  margin: 12px 0 0;
  text-align: center;
  color: var(--sub, #999);
  font-size: 12px;
}

.share-error {
  padding: 12px 0;
}
</style>
