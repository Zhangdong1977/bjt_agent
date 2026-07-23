<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'

import { documentsApi, duplicateApi } from '@/api/client'
import DocumentParseProgress from '@/components/DocumentParseProgress.vue'
import { useProjectStore } from '@/stores/project'
import type { Document, DocumentContent } from '@/types'
import { isLegacyDocFile, legacyDocWarning } from '@/utils/uploadValidation'
import illustration from '@/assets/images/ui/home-illustration.png'
import iconFileTheme from '@/assets/images/ui/common-icon-file-theme.png'

type DuplicateSide = 'duplicate_left' | 'duplicate_right'

interface SideConfig {
  role: DuplicateSide
  title: string
  hint: string
}

interface UploadState {
  file: File
  status: 'uploading' | 'error'
  percent: number
  loaded: number
  total: number
  error?: string
}

const sides: SideConfig[] = [
  { role: 'duplicate_left', title: 'A 方技术应标书', hint: '上传一份 A 方技术应标书' },
  { role: 'duplicate_right', title: 'B 方技术应标书', hint: '上传一份 B 方技术应标书' },
]

const router = useRouter()
const projectStore = useProjectStore()
const projectName = ref('')
const projectDesc = ref('')
const submitting = ref(false)
const inputs = new Map<DuplicateSide, HTMLInputElement>()
const uploads = reactive<Partial<Record<DuplicateSide, UploadState>>>({})

const viewerOpen = ref(false)
const viewerLoading = ref(false)
const viewerTitle = ref('')
const viewerContent = ref<DocumentContent | null>(null)

function setInput(role: DuplicateSide, element: any) {
  if (element) inputs.set(role, element as HTMLInputElement)
}

function draftFor(role: DuplicateSide): Document | undefined {
  return projectStore.documents.find((doc) => doc.project_id === null && doc.doc_type === role)
}

const canStart = computed(() =>
  sides.every(({ role }) => draftFor(role)?.status === 'parsed') &&
  sides.every(({ role }) => !uploads[role])
)

onMounted(() => {
  void projectStore.loadDraftDocuments(['duplicate_left', 'duplicate_right'], true)
})

function pick(role: DuplicateSide) {
  if (draftFor(role) || uploads[role]) return
  inputs.get(role)?.click()
}

async function chooseFile(event: Event, role: DuplicateSide) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  if (isLegacyDocFile(file)) {
    message.warning(legacyDocWarning(file.name))
    return
  }
  uploads[role] = {
    file,
    status: 'uploading',
    percent: 0,
    loaded: 0,
    total: file.size,
  }
  await upload(role)
}

async function upload(role: DuplicateSide) {
  const state = uploads[role]
  if (!state) return
  state.status = 'uploading'
  state.percent = 0
  try {
    await projectStore.uploadDraftDocument(role, state.file, (progress) => {
      const current = uploads[role]
      if (!current) return
      current.percent = progress.percent
      current.loaded = progress.loaded
      current.total = progress.total
    })
    delete uploads[role]
    message.success(`${state.file.name} 上传成功，已开始解析`)
  } catch (error) {
    state.status = 'error'
    state.error = error instanceof Error ? error.message : '上传失败'
  }
}

function clearUpload(role: DuplicateSide) {
  delete uploads[role]
}

async function remove(role: DuplicateSide) {
  const draft = draftFor(role)
  if (!draft) return
  try {
    await projectStore.deleteDraftDocument(draft.id)
    message.success('文档已删除')
  } catch {
    message.error('删除文档失败')
  }
}

async function preview(document: Document) {
  viewerOpen.value = true
  viewerLoading.value = true
  viewerTitle.value = document.original_filename
  viewerContent.value = null
  try {
    viewerContent.value = await documentsApi.getDraftContent(document.id)
  } catch {
    message.error('加载解析内容失败')
    viewerOpen.value = false
  } finally {
    viewerLoading.value = false
  }
}

function renderedContent(): string {
  if (!viewerContent.value) return ''
  const source = viewerContent.value.format === 'markdown'
    ? marked.parse(viewerContent.value.content) as string
    : viewerContent.value.content
  return DOMPurify.sanitize(source, {
    ADD_TAGS: ['table', 'thead', 'tbody', 'tr', 'th', 'td', 'img'],
    ADD_ATTR: ['src', 'alt', 'style', 'border'],
  })
}

async function startDuplicateCheck() {
  if (!projectName.value.trim()) {
    message.warning('请输入项目名称')
    return
  }
  if (!canStart.value) {
    message.warning('请确保 A、B 两份技术应标书均已解析完成')
    return
  }
  submitting.value = true
  let createdProjectId: string | null = null
  let documentsAttached = false
  try {
    const left = draftFor('duplicate_left')!
    const right = draftFor('duplicate_right')!
    const project = await projectStore.createProject(
      projectName.value.trim(),
      projectDesc.value.trim() || undefined,
      'duplicate',
    )
    createdProjectId = project.id
    await duplicateApi.attachDuplicatePair(project.id, left.id, right.id)
    documentsAttached = true
    await projectStore.selectProject(project.id)
    projectStore.currentTask = await duplicateApi.start(project.id)
    await router.push({ name: 'duplicate-execution', params: { id: project.id } })
  } catch (error: any) {
    if (createdProjectId && !documentsAttached) {
      await projectStore.deleteProject(createdProjectId).catch(() => undefined)
    }
    const detail = error?.response?.data?.detail
    const text = typeof detail === 'object' ? detail?.message : detail
    message.error(text || error?.message || '启动查重失败，请重试')
  } finally {
    submitting.value = false
  }
}

function formatBytes(value: number): string {
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / 1024 / 1024).toFixed(1)} MB`
}
</script>

<template>
  <div class="duplicate-check-view">
    <input
      v-for="side in sides"
      :key="side.role"
      :ref="(el) => setInput(side.role, el)"
      type="file"
      accept=".pdf,.docx"
      hidden
      @change="chooseFile($event, side.role)"
    />

    <section class="card project-card">
      <div class="project-main">
        <h2>创建查重项目</h2>
        <p>上传 A、B 两份技术应标书，由规则子代理并行检查重复内容。</p>
        <label>项目名称<span>*</span></label>
        <input v-model="projectName" maxlength="60" placeholder="请输入项目名称" />
        <label>项目描述<small>（可选）</small></label>
        <textarea v-model="projectDesc" maxlength="300" rows="3" placeholder="请输入项目描述" />
      </div>
      <img :src="illustration" alt="" />
    </section>

    <section class="card documents-card">
      <header>
        <img :src="iconFileTheme" alt="" />
        <div>
          <h2>技术应标书查重</h2>
          <p>每侧仅支持一份 PDF 或 Word（.docx），上传后自动解析。</p>
        </div>
      </header>

      <div class="side-grid">
        <article v-for="side in sides" :key="side.role" class="side-card">
          <h3>{{ side.title }}</h3>

          <div v-if="uploads[side.role]" class="document-item">
            <strong>{{ uploads[side.role]!.file.name }}</strong>
            <template v-if="uploads[side.role]!.status === 'uploading'">
              <div class="progress-meta">
                <span>上传中 {{ uploads[side.role]!.percent }}%</span>
                <span>{{ formatBytes(uploads[side.role]!.loaded) }} / {{ formatBytes(uploads[side.role]!.total) }}</span>
              </div>
              <a-progress :percent="uploads[side.role]!.percent" :show-info="false" />
            </template>
            <template v-else>
              <p class="error">{{ uploads[side.role]!.error }}</p>
              <div class="actions">
                <button @click="upload(side.role)">重试</button>
                <button @click="clearUpload(side.role)">移除</button>
              </div>
            </template>
          </div>

          <div v-else-if="draftFor(side.role)" class="document-item">
            <strong>{{ draftFor(side.role)!.original_filename }}</strong>
            <DocumentParseProgress
              v-if="['pending', 'parsing'].includes(draftFor(side.role)!.status)"
              :document-id="draftFor(side.role)!.id"
              :stage="draftFor(side.role)!.parse_progress?.stage || 'extracting_text'"
              :processed="draftFor(side.role)!.parse_progress?.processed || 0"
              :total="draftFor(side.role)!.parse_progress?.total || 1"
              :eta-seconds="draftFor(side.role)!.parse_progress?.etaSeconds || 0"
            />
            <p v-else-if="draftFor(side.role)!.status === 'failed'" class="error">
              解析失败：{{ draftFor(side.role)!.parse_error || '请删除后重新上传' }}
            </p>
            <p v-else class="success">解析完成</p>
            <div class="actions">
              <button v-if="draftFor(side.role)!.status === 'parsed'" @click="preview(draftFor(side.role)!)">查看内容</button>
              <button class="danger" @click="remove(side.role)">删除</button>
            </div>
          </div>

          <button v-else class="upload-button" @click="pick(side.role)">+ {{ side.hint }}</button>
        </article>
      </div>

      <div class="start-area">
        <span v-if="!canStart">两份文件均解析完成后可开始查重</span>
        <button :disabled="!canStart || submitting" @click="startDuplicateCheck">
          {{ submitting ? '正在启动…' : '开始查重' }}
        </button>
      </div>
    </section>

    <a-modal v-model:open="viewerOpen" :title="viewerTitle" width="900px" :footer="null">
      <a-spin :spinning="viewerLoading">
        <div class="document-preview" v-html="renderedContent()"></div>
      </a-spin>
    </a-modal>
  </div>
</template>

<style scoped>
.duplicate-check-view { display: flex; flex-direction: column; gap: 20px; }
.card { background: #fff; border: 1px solid #e8eaf0; border-radius: 10px; padding: 24px; }
.project-card { display: flex; justify-content: space-between; gap: 30px; }
.project-main { flex: 1; max-width: 760px; }
.project-card > img { width: 250px; object-fit: contain; }
h2 { margin: 0 0 6px; font-size: 20px; color: #222; }
p { color: #777; }
label { display: block; margin: 18px 0 7px; color: #444; font-weight: 500; }
label span { color: #d7041a; margin-left: 3px; }
label small { color: #999; font-weight: 400; }
input, textarea { width: 100%; box-sizing: border-box; border: 1px solid #dfe2ea; border-radius: 6px; padding: 10px 12px; font: inherit; }
.documents-card > header { display: flex; gap: 12px; align-items: center; margin-bottom: 22px; }
.documents-card > header img { width: 38px; }
.documents-card header p { margin: 0; }
.side-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }
.side-card { min-height: 230px; border: 1px solid #e1e4eb; border-radius: 8px; padding: 18px; }
.side-card h3 { margin: 0 0 16px; }
.upload-button { width: 100%; min-height: 150px; border: 1px dashed #d7041a; background: #fff8f8; color: #d7041a; border-radius: 8px; cursor: pointer; }
.document-item { border: 1px solid #eceef3; background: #fafbfc; border-radius: 8px; padding: 16px; }
.document-item strong { display: block; margin-bottom: 12px; word-break: break-all; }
.progress-meta { display: flex; justify-content: space-between; color: #777; font-size: 12px; }
.error { color: #c62828; }
.success { color: #18864b; }
.actions { display: flex; gap: 8px; margin-top: 12px; }
.actions button { border: 1px solid #ccd1db; background: #fff; border-radius: 5px; padding: 6px 13px; cursor: pointer; }
.actions .danger { color: #c62828; }
.start-area { display: flex; justify-content: flex-end; align-items: center; gap: 16px; margin-top: 22px; }
.start-area span { color: #999; }
.start-area button { border: 0; border-radius: 6px; padding: 11px 34px; background: #d7041a; color: #fff; cursor: pointer; }
.start-area button:disabled { background: #bbb; cursor: not-allowed; }
.document-preview { max-height: 70vh; overflow: auto; line-height: 1.7; }
.document-preview :deep(table) { border-collapse: collapse; width: 100%; }
.document-preview :deep(td), .document-preview :deep(th) { border: 1px solid #ddd; padding: 6px; }
@media (max-width: 900px) { .side-grid { grid-template-columns: 1fr; } .project-card > img { display: none; } }
</style>
