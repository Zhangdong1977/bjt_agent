<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { documentsApi, duplicateCheckApi, projectsApi } from '@/api/client'
import type { Document, DocumentContent } from '@/types'
import DocumentParseProgress from '@/components/DocumentParseProgress.vue'
import { isLegacyDocFile, legacyDocWarning } from '@/utils/uploadValidation'

const router = useRouter()
const projectName = ref('')
const projectDescription = ref('')
const documents = ref<Document[]>([])
const uploading = ref(false)
const uploadPercent = ref(0)
const submitting = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)
const timers = new Map<string, number>()
const previewOpen = ref(false)
const previewLoading = ref(false)
const preview = ref<DocumentContent | null>(null)
const previewTitle = ref('')

const allFinished = computed(() => documents.value.length > 0 && documents.value.every(d => d.status === 'parsed' || d.status === 'failed'))
const canStart = computed(() => documents.value.length >= 2 && documents.value.length <= 5 && allFinished.value && documents.value.every(d => d.status === 'parsed'))

function replaceDocument(updated: Document) {
  const index = documents.value.findIndex(d => d.id === updated.id)
  if (index >= 0) documents.value[index] = { ...documents.value[index], ...updated }
}

function stopPolling(id: string) {
  const timer = timers.get(id)
  if (timer) window.clearInterval(timer)
  timers.delete(id)
}

function poll(id: string) {
  stopPolling(id)
  const refresh = async () => {
    try {
      const updated = await documentsApi.getDraft(id)
      replaceDocument(updated)
      if (updated.status === 'parsed' || updated.status === 'failed') stopPolling(id)
    } catch {
      stopPolling(id)
    }
  }
  timers.set(id, window.setInterval(refresh, 2000))
  void refresh()
}

onMounted(async () => {
  documents.value = await documentsApi.listDrafts('duplicate_bid')
  documents.value.filter(d => d.status === 'pending' || d.status === 'parsing').forEach(d => poll(d.id))
})

onUnmounted(() => timers.forEach((_, id) => stopPolling(id)))

async function uploadFiles(event: Event) {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files || [])
  input.value = ''
  if (!files.length) return
  if (documents.value.length + files.length > 5) {
    message.warning('每个查重项目最多上传 5 份标书')
    return
  }
  const valid = files.filter(file => {
    if (isLegacyDocFile(file)) {
      message.warning(legacyDocWarning(file.name))
      return false
    }
    return true
  })
  uploading.value = true
  try {
    for (const file of valid) {
      uploadPercent.value = 0
      const doc = await documentsApi.uploadDraft('duplicate_bid', file, p => { uploadPercent.value = p.percent })
      documents.value.push(doc)
      poll(doc.id)
      message.success(`${file.name} 上传成功，开始解析`)
    }
  } catch (error) {
    message.error(error instanceof Error ? error.message : '上传失败')
  } finally {
    uploading.value = false
    uploadPercent.value = 0
  }
}

async function removeDocument(doc: Document) {
  await documentsApi.deleteDraft(doc.id)
  stopPolling(doc.id)
  documents.value = documents.value.filter(item => item.id !== doc.id)
  message.success('文档已删除')
}

async function showPreview(doc: Document) {
  previewOpen.value = true
  previewLoading.value = true
  previewTitle.value = doc.original_filename
  preview.value = null
  try {
    preview.value = await documentsApi.getDraftContent(doc.id)
  } catch {
    message.error('加载解析内容失败')
  } finally {
    previewLoading.value = false
  }
}

async function startDuplicateCheck() {
  if (!projectName.value.trim()) return void message.warning('请输入项目名称')
  if (!canStart.value) return void message.warning('请上传 2 至 5 份并确保全部解析成功')
  submitting.value = true
  try {
    const project = await projectsApi.create({
      name: projectName.value.trim(),
      description: projectDescription.value.trim() || undefined,
      project_type: 'duplicate',
    })
    for (const doc of documents.value) await documentsApi.attach(doc.id, project.id)
    const task = await duplicateCheckApi.start(project.id)
    await router.push({ name: 'duplicate-execution', params: { id: project.id }, query: { taskId: task.id } })
  } catch (error: any) {
    const detail = error?.response?.data?.detail
    message.error(typeof detail === 'string' ? detail : detail?.message || '启动查重失败')
  } finally {
    submitting.value = false
  }
}

function statusText(doc: Document) {
  return { pending: '等待解析', parsing: '解析中', parsed: '解析完成', failed: '解析失败' }[doc.status]
}
</script>

<template>
  <div class="duplicate-create">
    <a-card :bordered="false" class="card">
      <h2>创建查重项目</h2>
      <div class="project-fields">
        <a-input v-model:value="projectName" size="large" placeholder="请输入项目名称" :maxlength="255" />
        <a-textarea v-model:value="projectDescription" placeholder="项目描述（可选）" :rows="3" />
      </div>
    </a-card>

    <a-card :bordered="false" class="card">
      <div class="card-head">
        <div>
          <h2>待查重标书</h2>
          <p>上传 2～5 份投标文件，系统解析后将两两比对。</p>
        </div>
        <a-button type="primary" :disabled="uploading || documents.length >= 5" @click="fileInput?.click()">
          选择文件
        </a-button>
        <input ref="fileInput" hidden multiple type="file" accept=".pdf,.docx" @change="uploadFiles" />
      </div>
      <a-progress v-if="uploading" :percent="uploadPercent" />
      <a-empty v-if="!documents.length" description="请上传至少 2 份标书" />
      <div v-else class="document-list">
        <div v-for="doc in documents" :key="doc.id" class="document-row">
          <div class="doc-main">
            <strong>{{ doc.original_filename }}</strong>
            <DocumentParseProgress
              v-if="doc.status === 'pending' || doc.status === 'parsing'"
              :document-id="doc.id" stage="extracting_text" :processed="0" :total="1" :eta-seconds="0"
            />
            <span v-else :class="['status', `status--${doc.status}`]">{{ statusText(doc) }}</span>
            <small v-if="doc.parse_error" class="error">{{ doc.parse_error }}</small>
          </div>
          <a-space>
            <a-button v-if="doc.status === 'parsed'" size="small" @click="showPreview(doc)">查看内容</a-button>
            <a-popconfirm title="确定删除该文档？" @confirm="removeDocument(doc)">
              <a-button danger size="small">删除</a-button>
            </a-popconfirm>
          </a-space>
        </div>
      </div>
      <p class="hint">支持 PDF、DOCX，单文件不超过 500MB；解析失败的文件需删除后重新上传。</p>
    </a-card>

    <div class="actions">
      <a-button type="primary" size="large" :loading="submitting" :disabled="!canStart" @click="startDuplicateCheck">
        立即查重
      </a-button>
    </div>

    <a-modal v-model:open="previewOpen" :title="previewTitle" :footer="null" width="900px">
      <a-spin :spinning="previewLoading">
        <pre class="preview">{{ preview?.content }}</pre>
      </a-spin>
    </a-modal>
  </div>
</template>

<style scoped>
.duplicate-create { max-width: 1180px; margin: 0 auto; }
.card { margin-bottom: 20px; border-radius: 12px; box-shadow: var(--shadow-md); }
.card h2 { margin: 0 0 8px; font-size: 20px; }
.card p { color: #777; margin: 0; }
.project-fields { display: grid; gap: 14px; margin-top: 18px; }
.card-head { display: flex; align-items: center; justify-content: space-between; gap: 20px; margin-bottom: 20px; }
.document-list { display: grid; gap: 10px; }
.document-row { display: flex; justify-content: space-between; align-items: center; gap: 16px; padding: 14px; border: 1px solid #eee; border-radius: 8px; }
.doc-main { display: grid; gap: 6px; min-width: 0; }
.doc-main strong { overflow-wrap: anywhere; }
.status { font-size: 13px; }
.status--parsed { color: #2f9e44; }
.status--failed, .error { color: #d7041a; }
.hint { margin-top: 16px !important; font-size: 13px; }
.actions { text-align: center; padding: 8px 0 32px; }
.actions :deep(.ant-btn) { min-width: 180px; }
.preview { white-space: pre-wrap; max-height: 65vh; overflow: auto; font-family: inherit; }
</style>
