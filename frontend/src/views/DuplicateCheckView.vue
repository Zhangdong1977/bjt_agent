<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'

import { documentsApi, duplicateApi } from '@/api/client'
import DocumentParseProgress from '@/components/DocumentParseProgress.vue'
import { useProjectStore } from '@/stores/project'
import { useAuthStore } from '@/stores/auth'
import { useBillingStore } from '@/stores/billing'
import type { Document, DocumentContent } from '@/types'
import { isLegacyDocFile, legacyDocWarning, uploadDocumentWarning } from '@/utils/uploadValidation'
import illustration from '@/assets/images/ui/home-illustration.png'
import iconFileTheme from '@/assets/images/ui/common-icon-file-theme.png'

type DuplicateSide = 'duplicate_left' | 'duplicate_right'
type DuplicateBatchRole = 'duplicate_bid'
type DuplicateSourceRole = 'duplicate_tender' | 'duplicate_public_reference'
type DuplicateUploadRole = DuplicateSide | DuplicateBatchRole | DuplicateSourceRole

interface SideConfig {
  role: DuplicateUploadRole
  title: string
  hint: string
  multiple?: boolean
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
const batchSide: SideConfig = {
  role: 'duplicate_bid',
  title: '批量应标书',
  hint: '添加一份应标书（3-10 份）',
  multiple: true,
}
const sourceRoles: SideConfig[] = [
  {
    role: 'duplicate_tender',
    title: '招标文件（可选）',
    hint: '添加本项目招标文件',
    multiple: true,
  },
  {
    role: 'duplicate_public_reference',
    title: '公共参考资料（可选）',
    hint: '添加说明书、标准或公共模板',
    multiple: true,
  },
]
const uploadRoles = [...sides, batchSide, ...sourceRoles]

const router = useRouter()
const projectStore = useProjectStore()
const billingStore = useBillingStore()
const authStore = useAuthStore()
const projectName = ref('')
const projectDesc = ref('')
const duplicateMode = ref<'pair' | 'batch'>('pair')
const batchModeEnabled = ref(false)
const submitting = ref(false)
const inputs = new Map<DuplicateUploadRole, HTMLInputElement>()
const uploads = reactive<Partial<Record<DuplicateUploadRole, UploadState>>>({})

const viewerOpen = ref(false)
const viewerLoading = ref(false)
const viewerTitle = ref('')
const viewerContent = ref<DocumentContent | null>(null)

function setInput(role: DuplicateUploadRole, element: any) {
  if (element) inputs.set(role, element as HTMLInputElement)
}

function draftsFor(role: DuplicateUploadRole): Document[] {
  return projectStore.documents.filter((doc) => doc.project_id === null && doc.doc_type === role)
}

function draftFor(role: DuplicateUploadRole): Document | undefined {
  return projectStore.documents.find((doc) => doc.project_id === null && doc.doc_type === role)
}

const canStart = computed(() => {
  const uploadsFinished = uploadRoles.every(({ role }) => !uploads[role])
  const sourcesReady = sourceRoles.every(({ role }) =>
    draftsFor(role).every((doc) => doc.status === 'parsed'),
  )
  if (duplicateMode.value === 'batch') {
    const members = draftsFor('duplicate_bid')
    return members.length >= 3 && members.length <= 10 &&
      members.every((doc) => ['parsed', 'failed'].includes(doc.status)) &&
      uploadsFinished && sourcesReady
  }
  return sides.every(({ role }) => draftFor(role)?.status === 'parsed') &&
    uploadsFinished && sourcesReady
})

onMounted(() => {
  void duplicateApi.getReleaseCapabilities()
    .then((capabilities) => {
      batchModeEnabled.value = capabilities.features.batch === true
    })
    .catch(() => {
      batchModeEnabled.value = false
    })
  void projectStore.loadDraftDocuments(
    ['duplicate_left', 'duplicate_right', 'duplicate_bid', 'duplicate_tender', 'duplicate_public_reference'],
    true,
  )
})

function pick(role: DuplicateUploadRole) {
  const config = uploadRoles.find((item) => item.role === role)
  if ((!config?.multiple && draftsFor(role).length > 0) || uploads[role]) return
  inputs.get(role)?.click()
}

async function chooseFile(event: Event, role: DuplicateUploadRole) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  if (isLegacyDocFile(file)) {
    message.warning(legacyDocWarning(file.name))
    return
  }
  const uploadWarning = uploadDocumentWarning(file)
  if (uploadWarning) {
    message.warning(uploadWarning)
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

async function upload(role: DuplicateUploadRole) {
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

function clearUpload(role: DuplicateUploadRole) {
  delete uploads[role]
}

function openArtifacts(documentId: string) {
  void router.push({ name: 'draft-document-artifacts', params: { documentId } })
}

async function remove(role: DuplicateUploadRole) {
  const draft = draftFor(role)
  if (!draft) return
  try {
    await projectStore.deleteDraftDocument(draft.id)
    message.success('文档已删除')
  } catch {
    message.error('删除文档失败')
  }
}

async function removeDocument(document: Document) {
  try {
    await projectStore.deleteDraftDocument(document.id)
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
    message.warning(
      duplicateMode.value === 'batch'
        ? '请准备 3-10 份应标书并等待解析完成'
        : '请确保 A、B 两份技术应标书均已解析完成',
    )
    return
  }
  await billingStore.remindLowBalance('task', true)
  submitting.value = true
  let createdProjectId: string | null = null
  let documentsAttached = false
  try {
    const sourceDocumentIds = sourceRoles.flatMap(({ role }) =>
      draftsFor(role).map((document) => document.id),
    )
    const project = await projectStore.createProject(
      projectName.value.trim(),
      projectDesc.value.trim() || undefined,
      'duplicate',
      duplicateMode.value,
    )
    createdProjectId = project.id
    if (duplicateMode.value === 'batch') {
      const members = draftsFor('duplicate_bid').map((document, index) => ({
        document_id: document.id,
        party_key: document.duplicate_party_key || `party-${index + 1}`,
        display_name: document.duplicate_display_name || document.original_filename,
        ordinal: document.duplicate_ordinal ?? index,
      }))
      await duplicateApi.attachDuplicateBatch(project.id, members, sourceDocumentIds)
    } else {
      const left = draftFor('duplicate_left')!
      const right = draftFor('duplicate_right')!
      await duplicateApi.attachDuplicatePair(project.id, left.id, right.id, sourceDocumentIds)
    }
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
      v-for="side in uploadRoles"
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

      <div v-if="batchModeEnabled" class="mode-switch">
        <label>查重模式</label>
        <label class="mode-option">
          <input v-model="duplicateMode" type="radio" value="pair" />
          A/B 对照
        </label>
        <label class="mode-option">
          <input v-model="duplicateMode" type="radio" value="batch" />
          多文件矩阵（3-10 份）
        </label>
      </div>

      <div v-if="duplicateMode === 'pair'" class="side-grid">
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
              <button
                v-if="draftFor(side.role)!.status === 'parsed' && authStore.isInteriorUser"
                @click="openArtifacts(draftFor(side.role)!.id)"
              >解析诊断</button>
              <button class="danger" @click="remove(side.role)">删除</button>
            </div>
          </div>

          <button v-else class="upload-button" @click="pick(side.role)">+ {{ side.hint }}</button>
        </article>
      </div>

      <div v-else class="batch-members">
        <div class="batch-heading">
          <div>
            <h3>批量应标书</h3>
            <p>每份文档可填写投标人标签或使用匿名编号；解析失败的文档会保留并降级覆盖度。</p>
          </div>
          <span>{{ draftsFor('duplicate_bid').length }} / 10 份</span>
        </div>
        <div v-for="(document, index) in draftsFor('duplicate_bid')" :key="document.id" class="batch-member document-item">
          <div class="batch-member-head">
            <strong>{{ document.duplicate_display_name || document.original_filename }}</strong>
            <span>编号 {{ document.duplicate_party_key || `party-${index + 1}` }}</span>
          </div>
          <DocumentParseProgress
            v-if="['pending', 'parsing'].includes(document.status)"
            :document-id="document.id"
            :stage="document.parse_progress?.stage || 'extracting_text'"
            :processed="document.parse_progress?.processed || 0"
            :total="document.parse_progress?.total || 1"
            :eta-seconds="document.parse_progress?.etaSeconds || 0"
          />
          <p v-else-if="document.status === 'failed'" class="error">解析失败：{{ document.parse_error || '该文档将按覆盖不足处理' }}</p>
          <p v-else class="success">解析完成</p>
          <div class="actions">
            <button v-if="document.status === 'parsed'" @click="preview(document)">查看内容</button>
            <button v-if="document.status === 'parsed' && authStore.isInteriorUser" @click="openArtifacts(document.id)">解析诊断</button>
            <button class="danger" @click="removeDocument(document)">删除</button>
          </div>
        </div>
        <div v-if="uploads.duplicate_bid" class="document-item">
          <strong>{{ uploads.duplicate_bid.file.name }}</strong>
          <a-progress :percent="uploads.duplicate_bid.percent" :show-info="true" />
        </div>
        <button v-if="!uploads.duplicate_bid && draftsFor('duplicate_bid').length < 10" class="upload-button compact" @click="pick('duplicate_bid')">
          + 添加应标书
        </button>
      </div>

      <div class="source-heading">
        <h3>判定依据（可选）</h3>
        <p>来源文件会保存不可变快照、版本和 hash；没有具体来源证据时，系统不会把结论标成“招标要求”或“公共规范”。</p>
      </div>
      <div class="side-grid source-grid">
        <article v-for="source in sourceRoles" :key="source.role" class="side-card source-card">
          <h3>{{ source.title }}</h3>
          <div
            v-for="document in draftsFor(source.role)"
            :key="document.id"
            class="document-item source-document"
          >
            <strong>{{ document.original_filename }}</strong>
            <DocumentParseProgress
              v-if="['pending', 'parsing'].includes(document.status)"
              :document-id="document.id"
              :stage="document.parse_progress?.stage || 'extracting_text'"
              :processed="document.parse_progress?.processed || 0"
              :total="document.parse_progress?.total || 1"
              :eta-seconds="document.parse_progress?.etaSeconds || 0"
            />
            <p v-else-if="document.status === 'failed'" class="error">
              解析失败：{{ document.parse_error || '请删除后重新上传' }}
            </p>
            <p v-else class="success">
              已固化 · {{ document.source_snapshot_hash ? document.source_snapshot_hash.slice(0, 12) : '等待生成 hash' }}
            </p>
            <div class="actions">
              <button v-if="document.status === 'parsed'" @click="preview(document)">查看内容</button>
              <button
                v-if="document.status === 'parsed' && authStore.isInteriorUser"
                @click="openArtifacts(document.id)"
              >解析诊断</button>
              <button class="danger" @click="removeDocument(document)">删除</button>
            </div>
          </div>
          <div v-if="uploads[source.role]" class="document-item">
            <strong>{{ uploads[source.role]!.file.name }}</strong>
            <a-progress
              v-if="uploads[source.role]!.status === 'uploading'"
              :percent="uploads[source.role]!.percent"
              :show-info="true"
            />
            <div v-else class="actions">
              <span class="error">{{ uploads[source.role]!.error }}</span>
              <button @click="upload(source.role)">重试</button>
              <button @click="clearUpload(source.role)">移除</button>
            </div>
          </div>
          <button
            v-if="!uploads[source.role]"
            class="upload-button compact"
            @click="pick(source.role)"
          >+ {{ source.hint }}</button>
        </article>
      </div>

      <div class="start-area">
        <span v-if="!canStart">
          {{ duplicateMode === 'batch' ? '请准备 3-10 份已解析（或解析失败可降级）的应标书' : '两份文件均解析完成后可开始查重' }}
        </span>
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
.mode-switch { display: flex; align-items: center; gap: 16px; margin-bottom: 18px; padding: 12px 14px; background: #f8f9fb; border-radius: 7px; }
.mode-switch > label:first-child { margin: 0; font-weight: 600; }
.mode-option { display: inline-flex; align-items: center; gap: 5px; margin: 0; font-weight: 400; }
.mode-option input { width: auto; }
.side-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }
.batch-members { display: flex; flex-direction: column; gap: 10px; }
.batch-heading { display: flex; justify-content: space-between; align-items: flex-start; }
.batch-heading h3 { margin: 0; }
.batch-heading p { margin: 5px 0 0; }
.batch-member { margin: 0; }
.batch-member-head { display: flex; justify-content: space-between; gap: 12px; }
.source-heading { margin-top: 24px; border-top: 1px solid #eceef3; padding-top: 20px; }
.source-heading h3 { margin: 0; }
.source-heading p { margin: 6px 0 14px; }
.source-grid .side-card { min-height: 180px; }
.source-document { margin-bottom: 10px; }
.side-card { min-height: 230px; border: 1px solid #e1e4eb; border-radius: 8px; padding: 18px; }
.side-card h3 { margin: 0 0 16px; }
.upload-button { width: 100%; min-height: 150px; border: 1px dashed #d7041a; background: #fff8f8; color: #d7041a; border-radius: 8px; cursor: pointer; }
.upload-button.compact { min-height: 70px; }
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
