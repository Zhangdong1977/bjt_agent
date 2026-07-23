<script setup lang="ts">
import { ref, reactive, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useProjectStore } from '@/stores/project'
import { documentsApi } from '@/api/client'
import type { DocumentContent } from '@/types'
import { message } from 'ant-design-vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import DocumentParseProgress from '@/components/DocumentParseProgress.vue'
import { isLegacyDocFile, legacyDocWarning } from '@/utils/uploadValidation'
import illustration from '@/assets/images/ui/home-illustration.png'
import iconFileTheme from '@/assets/images/ui/common-icon-file-theme.png'
import iconSearch from '@/assets/images/ui/common-icon-search.png'

// DOMPurify 全局 hook：给解析后的 table/img 加默认样式
DOMPurify.addHook('afterSanitizeAttributes', (node) => {
  if (node.tagName === 'TABLE') {
    node.setAttribute('border', '1')
    node.setAttribute('style', 'border-collapse: collapse; width: 100%; margin: 0.5em 0;')
  }
  if (node.tagName === 'TH' || node.tagName === 'TD') {
    node.setAttribute('style', 'border: 1px solid var(--line); padding: 6px 10px;')
  }
  if (node.tagName === 'IMG') {
    node.setAttribute('style', 'max-width: 100%; height: auto; display: block; margin: 0.5em 0;')
  }
})

const router = useRouter()
const projectStore = useProjectStore()

const projectName = ref('')
const projectDesc = ref('')
const submitting = ref(false)

// 草稿文档（project_id === null）按类型分组
const tenderDrafts = computed(() =>
  projectStore.documents.filter((d) => d.project_id === null && d.doc_type === 'tender')
)
const bidDrafts = computed(() =>
  projectStore.documents.filter((d) => d.project_id === null && d.doc_type === 'bid')
)
// 所有草稿是否都已解析完成（不再有 pending/parsing）
const allDraftsParsed = computed(() => {
  const all = [...tenderDrafts.value, ...bidDrafts.value]
  if (all.length === 0) return false
  return all.every((d) => d.status === 'parsed' || d.status === 'failed')
})
const hasParsedTender = computed(() => tenderDrafts.value.some((d) => d.status === 'parsed'))
const hasParsedBid = computed(() => bidDrafts.value.some((d) => d.status === 'parsed'))
// 「开始检查」启用条件：招标≥1 parsed + 投标≥1 parsed + 所有草稿解析完成
const canStartCheck = computed(() =>
  allDraftsParsed.value && hasParsedTender.value && hasParsedBid.value
)

// 文档查看器
const showDocViewer = ref(false)
const docViewerContent = ref<DocumentContent | null>(null)
const docViewerLoading = ref(false)
const docViewerTitle = ref('')

const tenderInput = ref<HTMLInputElement | null>(null)
const bidInput = ref<HTMLInputElement | null>(null)

// ============================================================
// 临时上传卡片：上传进行中（XHR 字节进度）期间占位，上传成功后由 doc 卡接管为「解析中」。
// 不进 store —— 只在 CheckView 内部有意义（草稿场景）。
// ============================================================
interface TempUploadItem {
  localId: string
  filename: string
  docType: 'tender' | 'bid'
  status: 'queued' | 'uploading' | 'error'
  percent: number
  loaded: number
  total: number
  errorMsg?: string
  file: File
}

// reactive 让数组元素的属性变更触发响应式（ref 包数组只对数组本身响应，
// 直接改 item.percent 不会重渲染 UI —— 这是 108M 大文件上传时进度条卡 0% 的真因）
const tempUploads = reactive<TempUploadItem[]>([])

const tenderTempUploads = computed(() =>
  tempUploads.filter((t) => t.docType === 'tender'),
)
const bidTempUploads = computed(() =>
  tempUploads.filter((t) => t.docType === 'bid'),
)

// 该 docType 是否有任意一张卡正在传/排队中，用于禁用上传按钮
const isUploadingTender = computed(() =>
  tenderTempUploads.value.some((t) => t.status === 'uploading' || t.status === 'queued'),
)
const isUploadingBid = computed(() =>
  bidTempUploads.value.some((t) => t.status === 'uploading' || t.status === 'queued'),
)

function formatBytes(n: number): string {
  if (n < 1024) return n + ' B'
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB'
  if (n < 1024 * 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + ' M'
  return (n / 1024 / 1024 / 1024).toFixed(2) + ' G'
}

onMounted(() => {
  // 恢复解析中的草稿（续上进度）；已结束的草稿在 store 内静默清理，使卡片回到初始状态
  void projectStore.loadDraftDocuments()
})

onUnmounted(() => {
  // 离开页面时清理解析完成的草稿的轮询（未关联项目的草稿若已完成且未检查，可后续清理）
})

function pickTender() {
  tenderInput.value?.click()
}

function pickBid() {
  bidInput.value?.click()
}

// 选完文件：一次性为所有选中文件创建临时卡（queued），再串行上传。
// 上传期间临时卡显示字节进度，成功后由 doc 卡接管为「解析中」。
async function handleUpload(event: Event, docType: 'tender' | 'bid') {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files ?? [])
  input.value = ''
  if (!files.length) return

  // ① 旧版 .doc 直接拦截，不创建临时卡
  const validFiles: File[] = []
  for (const file of files) {
    if (isLegacyDocFile(file)) {
      message.warning(legacyDocWarning(file.name))
      continue
    }
    validFiles.push(file)
  }
  if (!validFiles.length) return

  // ② 一次性入队：所有文件先标为 queued，UI 立刻出现 N 张「等待中」卡
  const items: TempUploadItem[] = validFiles.map((file) => ({
    localId: `tmp_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
    filename: file.name,
    docType,
    status: 'queued',
    percent: 0,
    loaded: 0,
    total: file.size,
    file,
  }))
  tempUploads.push(...items)

  // ③ 串行上传(保持原有 for-await 顺序语义)
  for (const item of items) {
    await uploadOne(item)
  }
}

// 单文件上传（handleUpload 与 retryTempUpload 共用）
async function uploadOne(item: TempUploadItem) {
  // 从 reactive 数组里按 localId 取出 Proxy 引用,后续修改走 Proxy 才能触发响应式。
  // 直接改 push 前的原始对象,Vue 检测不到 —— 这是 reactive 数组的陷阱。
  const findReactive = (): TempUploadItem | undefined =>
    tempUploads.find((t) => t.localId === item.localId)

  const reactiveItem = findReactive()
  if (!reactiveItem) return
  reactiveItem.status = 'uploading'
  reactiveItem.percent = 0
  reactiveItem.loaded = 0
  try {
    await projectStore.uploadDraftDocument(item.docType, item.file, (p) => {
      // 始终从数组里取最新的 Proxy 引用(避免闭包捕获旧引用)
      const cur = findReactive()
      if (!cur) return
      cur.percent = p.percent
      cur.loaded = p.loaded
      cur.total = p.total
    })
    // 成功：移除临时卡，doc 卡会从 store 自动出现并接管「解析中」
    const idx = tempUploads.findIndex((t) => t.localId === item.localId)
    if (idx !== -1) tempUploads.splice(idx, 1)
    message.success(`${item.filename} 上传成功，开始解析`)
  } catch (err) {
    const cur = findReactive()
    if (cur) {
      cur.status = 'error'
      cur.errorMsg = err instanceof Error ? err.message : '上传失败'
    }
    // 不弹 toast，错误已显示在卡片上
  }
}

function retryTempUpload(item: TempUploadItem) {
  void uploadOne(item)
}

function removeTempUpload(item: TempUploadItem) {
  const idx = tempUploads.findIndex((t) => t.localId === item.localId)
  if (idx !== -1) tempUploads.splice(idx, 1)
}

async function handleDeleteDraft(docId: string) {
  if (!confirm('确定要删除此文档吗？')) return
  try {
    await projectStore.deleteDraftDocument(docId)
    message.success('文档已删除')
  } catch {
    message.error('删除文档失败')
  }
}

function renderMarkdown(content: string): string {
  const html = marked.parse(content) as string
  return DOMPurify.sanitize(html, {
    ADD_TAGS: ['table', 'thead', 'tbody', 'tr', 'th', 'td', 'img'],
    ADD_ATTR: ['border', 'style', 'src', 'alt', 'width', 'height'],
  })
}

// 查看草稿文档解析内容
async function handleViewDoc(docId: string) {
  docViewerLoading.value = true
  showDocViewer.value = true
  docViewerContent.value = null
  try {
    docViewerContent.value = await documentsApi.getDraftContent(docId)
    const doc = projectStore.documents.find((d) => d.id === docId)
    docViewerTitle.value = doc
      ? `${doc.doc_type === 'tender' ? '招标文件' : '投标文件'} - ${doc.original_filename}`
      : '文档'
  } catch (err) {
    console.error('Failed to load document:', err)
    message.error('加载文档内容失败')
    showDocViewer.value = false
  } finally {
    docViewerLoading.value = false
  }
}

function closeDocViewer() {
  showDocViewer.value = false
  docViewerContent.value = null
}

// 开始检查：创建项目 → 关联所有草稿文档 → 选择项目 → 启动审查 → 跳转
async function startCheck() {
  if (!projectName.value.trim()) {
    message.warning('请输入项目名称')
    return
  }
  if (!canStartCheck.value) {
    message.warning('请确保招标和投标文件各有至少一份解析完成，且所有文件解析结束')
    return
  }

  submitting.value = true
  try {
    // ① 创建项目
    const project = await projectStore.createProject(
      projectName.value.trim(),
      projectDesc.value.trim() || undefined,
    )
    if (!project) {
      message.error('创建项目失败')
      return
    }

    // ② 把所有草稿文档关联到该项目
    await projectStore.attachDraftDocuments(project.id, ['tender', 'bid'])

    // ③ 选择项目（加载文档列表，接续 SSE）
    await projectStore.selectProject(project.id)

    // ④ 启动审查
    await projectStore.startReview()

    // ⑤ 跳转审查执行页
    router.push({ name: 'review-execution', params: { id: project.id } })
  } catch (err) {
    const error = err as { response?: { status?: number; data?: { detail?: unknown } } }
    const detail = error.response?.data?.detail
    if (error.response?.status === 402 && typeof detail === 'object' && detail && 'message' in detail) {
      message.warning(String((detail as { message: unknown }).message))
    } else {
      message.error('操作失败，请重试')
    }
  } finally {
    submitting.value = false
  }
}

function getStatusClass(status: string) {
  switch (status) {
    case 'parsed':
    case 'completed':
      return 'status-success'
    case 'parsing':
    case 'running':
      return 'status-running'
    case 'failed':
      return 'status-error'
    default:
      return 'status-pending'
  }
}
</script>

<template>
  <div class="check-view">
    <input ref="tenderInput" type="file" accept=".pdf,.docx" multiple hidden @change="handleUpload($event, 'tender')" />
    <input ref="bidInput" type="file" accept=".pdf,.docx" multiple hidden @change="handleUpload($event, 'bid')" />

    <!-- 卡片①：创建新项目 -->
    <section class="card card-project">
      <div class="card-project__main">
        <header class="card-head">
          <h2 class="card-title">创建新项目</h2>
          <p class="card-subtitle">填写项目基本信息，上传标书文件，AI 智能审查立即开始！</p>
        </header>

        <div class="field">
          <label class="field-label">项目名称<span class="field-required">*</span></label>
          <input
            v-model="projectName"
            type="text"
            placeholder="请输入项目名称"
            class="text-input"
            maxlength="60"
          />
        </div>

        <div class="field">
          <label class="field-label">项目描述<span class="field-optional">（可选）</span></label>
          <textarea
            v-model="projectDesc"
            placeholder="请输入项目描述（可选）"
            class="text-input desc-input"
            rows="3"
            maxlength="300"
          ></textarea>
        </div>
      </div>

      <div class="card-project__art">
        <img :src="illustration" alt="" />
      </div>
    </section>

    <!-- 卡片②：招标文件、投标文件（选文件即上传解析） -->
    <section class="card card-files">
      <header class="card-head card-head--with-icon">
        <img :src="iconFileTheme" alt="" class="card-head__icon" />
        <div>
          <h2 class="card-title">招标文件、投标文件</h2>
          <p class="card-subtitle">上传招标文件和投标文件，开始智能审查，支持多文档分类上传</p>
        </div>
      </header>

      <div class="documents-grid">
        <!-- 招标文件 -->
        <div class="document-card">
          <h3>招标文件 ({{ tenderDrafts.length + tenderTempUploads.length }})</h3>

          <div v-if="tenderDrafts.length > 0 || tenderTempUploads.length > 0" class="doc-list">
            <!-- 临时上传卡片：上传中（XHR 字节进度）占位，成功后由下方 doc 卡接管 -->
            <div
              v-for="item in tenderTempUploads"
              :key="item.localId"
              class="doc-item temp-upload-item"
            >
              <div class="doc-header">
                <div class="doc-icon">📄</div>
                <div class="doc-main">
                  <p class="filename">{{ item.filename }}</p>
                  <template v-if="item.status === 'uploading' || item.status === 'queued'">
                    <div class="upload-progress-row">
                      <span class="upload-status-text">
                        {{ item.status === 'queued' ? '等待中' : '上传中 ' + item.percent + '%' }}
                      </span>
                      <span class="upload-bytes text-mono">
                        {{ formatBytes(item.loaded) }} / {{ formatBytes(item.total) }}
                      </span>
                    </div>
                    <a-progress
                      :percent="item.percent"
                      :status="item.status === 'queued' ? 'normal' : 'active'"
                      :show-info="false"
                      :stroke-width="6"
                    />
                  </template>
                  <div v-else-if="item.status === 'error'" class="parse-error-block">
                    <span class="status status-error">上传失败</span>
                    <p class="error-message">{{ item.errorMsg || '上传失败，请重试' }}</p>
                  </div>
                </div>
              </div>
              <div v-if="item.status === 'error'" class="doc-actions">
                <button class="view-btn" @click="retryTempUpload(item)">重试</button>
                <button class="delete-btn" @click="removeTempUpload(item)">移除</button>
              </div>
            </div>

            <div v-for="doc in tenderDrafts" :key="doc.id" class="doc-item">
              <div class="doc-header">
                <div class="doc-icon">📄</div>
                <div class="doc-main">
                  <p class="filename">{{ doc.original_filename }}</p>
                  <DocumentParseProgress
                    v-if="doc.status === 'parsing' || doc.status === 'pending'"
                    :document-id="doc.id"
                    :stage="doc.parse_progress?.stage || 'extracting_text'"
                    :processed="doc.parse_progress?.processed || 0"
                    :total="doc.parse_progress?.total || 1"
                    :eta-seconds="doc.parse_progress?.etaSeconds || 0"
                  />
                  <div v-else-if="doc.status === 'failed'" class="parse-error-block">
                    <span class="status status-error">解析失败</span>
                    <p class="error-message">{{ doc.parse_error || '文档解析失败，请重试' }}</p>
                  </div>
                  <span v-else :class="['status', getStatusClass(doc.status)]">解析完成</span>
                  <p v-if="doc.page_count" class="doc-meta text-mono">
                    {{ doc.page_count }} 页, {{ doc.word_count }} 字
                  </p>
                </div>
              </div>
              <div class="doc-actions">
                <button v-if="doc.status === 'parsed'" class="view-btn" @click="handleViewDoc(doc.id)">查看内容</button>
                <button class="delete-btn" @click="handleDeleteDraft(doc.id)">删除</button>
              </div>
            </div>
          </div>

          <div v-if="tenderDrafts.length + tenderTempUploads.length >= 10" class="upload-limit">已达上限（10个文件）</div>
          <div v-else class="upload-area">
            <button
              type="button"
              class="upload-pick-btn"
              :disabled="isUploadingTender"
              @click="pickTender"
            >
              {{ isUploadingTender ? '上传中…' : '+ 添加招标文件（PDF / Word .docx）' }}
            </button>
          </div>
        </div>

        <!-- 投标文件 -->
        <div class="document-card">
          <h3>投标文件 ({{ bidDrafts.length + bidTempUploads.length }})</h3>

          <div v-if="bidDrafts.length > 0 || bidTempUploads.length > 0" class="doc-list">
            <!-- 临时上传卡片：上传中（XHR 字节进度）占位，成功后由下方 doc 卡接管 -->
            <div
              v-for="item in bidTempUploads"
              :key="item.localId"
              class="doc-item temp-upload-item"
            >
              <div class="doc-header">
                <div class="doc-icon">📄</div>
                <div class="doc-main">
                  <p class="filename">{{ item.filename }}</p>
                  <template v-if="item.status === 'uploading' || item.status === 'queued'">
                    <div class="upload-progress-row">
                      <span class="upload-status-text">
                        {{ item.status === 'queued' ? '等待中' : '上传中 ' + item.percent + '%' }}
                      </span>
                      <span class="upload-bytes text-mono">
                        {{ formatBytes(item.loaded) }} / {{ formatBytes(item.total) }}
                      </span>
                    </div>
                    <a-progress
                      :percent="item.percent"
                      :status="item.status === 'queued' ? 'normal' : 'active'"
                      :show-info="false"
                      :stroke-width="6"
                    />
                  </template>
                  <div v-else-if="item.status === 'error'" class="parse-error-block">
                    <span class="status status-error">上传失败</span>
                    <p class="error-message">{{ item.errorMsg || '上传失败，请重试' }}</p>
                  </div>
                </div>
              </div>
              <div v-if="item.status === 'error'" class="doc-actions">
                <button class="view-btn" @click="retryTempUpload(item)">重试</button>
                <button class="delete-btn" @click="removeTempUpload(item)">移除</button>
              </div>
            </div>

            <div v-for="doc in bidDrafts" :key="doc.id" class="doc-item">
              <div class="doc-header">
                <div class="doc-icon">📄</div>
                <div class="doc-main">
                  <p class="filename">{{ doc.original_filename }}</p>
                  <DocumentParseProgress
                    v-if="doc.status === 'parsing' || doc.status === 'pending'"
                    :document-id="doc.id"
                    :stage="doc.parse_progress?.stage || 'extracting_text'"
                    :processed="doc.parse_progress?.processed || 0"
                    :total="doc.parse_progress?.total || 1"
                    :eta-seconds="doc.parse_progress?.etaSeconds || 0"
                  />
                  <div v-else-if="doc.status === 'failed'" class="parse-error-block">
                    <span class="status status-error">解析失败</span>
                    <p class="error-message">{{ doc.parse_error || '文档解析失败，请重试' }}</p>
                  </div>
                  <span v-else :class="['status', getStatusClass(doc.status)]">解析完成</span>
                  <p v-if="doc.page_count" class="doc-meta text-mono">
                    {{ doc.page_count }} 页, {{ doc.word_count }} 字
                  </p>
                </div>
              </div>
              <div class="doc-actions">
                <button v-if="doc.status === 'parsed'" class="view-btn" @click="handleViewDoc(doc.id)">查看内容</button>
                <button class="delete-btn" @click="handleDeleteDraft(doc.id)">删除</button>
              </div>
            </div>
          </div>

          <div v-if="bidDrafts.length + bidTempUploads.length >= 10" class="upload-limit">已达上限（10个文件）</div>
          <div v-else class="upload-area">
            <button
              type="button"
              class="upload-pick-btn"
              :disabled="isUploadingBid"
              @click="pickBid"
            >
              {{ isUploadingBid ? '上传中…' : '+ 添加投标文件（PDF / Word .docx）' }}
            </button>
          </div>
        </div>
      </div>

      <p class="upload-note">支持 PDF、Docx 格式，单个文件不超过 500MB；上传后立即开始解析</p>
    </section>

    <!-- 开始检查按钮：条件具备时启用 -->
    <button class="check-btn" :disabled="!canStartCheck || submitting" @click="startCheck">
      <img :src="iconSearch" alt="" class="check-btn__icon" />
      <span>{{ submitting ? '提交中...' : '立即检查' }}</span>
    </button>

    <!-- 文档查看器 Modal -->
    <div v-if="showDocViewer" class="modal-overlay" @click.self="closeDocViewer">
      <div class="doc-viewer-modal">
        <div class="doc-viewer-header">
          <h3>{{ docViewerTitle }}</h3>
          <button class="close-btn" @click="closeDocViewer">×</button>
        </div>
        <div class="doc-viewer-body">
          <div v-if="docViewerLoading" class="loading">正在加载文档...</div>
          <div v-else-if="docViewerContent" class="doc-content">
            <div
              v-if="docViewerContent.format === 'markdown'"
              class="markdown-content"
              v-html="renderMarkdown(docViewerContent.content)"
            />
            <div
              v-else-if="docViewerContent.content"
              class="html-content"
              v-html="DOMPurify.sanitize(docViewerContent.content)"
            />
            <div v-else class="no-content">无内容</div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.check-view {
  display: flex;
  flex-direction: column;
  gap: 24px;
  font-family: "Microsoft YaHei", "PingFang SC", Arial, sans-serif;
}

/* ============ 卡片通用 ============ */
.card {
  background: #fff;
  border: 1px solid #eef0f5;
  border-radius: 12px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.04);
  padding: 28px;
}

.card-head {
  margin-bottom: 22px;
}

.card-head--with-icon {
  display: flex;
  align-items: center;
  gap: 12px;
}

.card-head__icon {
  width: 33px;
  height: 50px;
  object-fit: contain;
  flex-shrink: 0;
}

.card-title {
  margin: 0;
  font-size: 20px;
  font-weight: 700;
  color: #222;
  letter-spacing: 0.5px;
}

.card-subtitle {
  margin: 8px 0 0;
  color: #888;
  font-size: 13px;
  line-height: 1.6;
}

/* ============ 卡片①：创建新项目 ============ */
.card-project {
  display: grid;
  grid-template-columns: 1fr 320px;
  gap: 32px;
  align-items: center;
}

.card-project__main {
  min-width: 0;
}

.card-project__art {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
}

.card-project__art img {
  width: 100%;
  max-width: 320px;
  height: 100%;
  max-height: 240px;
  object-fit: contain;
}

.field {
  margin-bottom: 18px;
}

.field:last-child {
  margin-bottom: 0;
}

.field-label {
  display: block;
  margin-bottom: 8px;
  font-size: 13px;
  font-weight: 600;
  color: #555;
}

.field-optional {
  color: #bbb;
  font-weight: 400;
}

.field-required {
  color: #D7041A;
  margin-left: 2px;
}

.text-input {
  width: 100%;
  height: 44px;
  padding: 0 16px;
  border: 1px solid #e4e6f1;
  border-radius: 8px;
  background: #fff;
  font-size: 14px;
  font-family: inherit;
  color: #333;
  outline: none;
  transition: border-color 0.2s ease, box-shadow 0.2s ease;
}

.text-input::placeholder {
  color: #b0b0b0;
}

.text-input:focus {
  border-color: #D7041A;
  box-shadow: 0 0 0 3px rgba(215, 4, 26, 0.1);
}

.desc-input {
  height: auto;
  padding: 12px 16px;
  resize: vertical;
  line-height: 1.6;
}

/* ============ 卡片②：文档上传 + 解析进度 ============ */
.documents-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: 20px;
  margin-bottom: 16px;
}

.document-card {
  border: 1px solid #eef0f5;
  border-radius: 10px;
  padding: 18px;
  background: #fff;
}

.document-card h3 {
  color: #888;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.06em;
  margin-bottom: 14px;
  padding-bottom: 10px;
  border-bottom: 1px solid #eef0f5;
}

.doc-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-bottom: 12px;
}

.doc-item {
  padding: 12px;
  background: #fafbfd;
  border-radius: 8px;
  border: 1px solid #eef0f5;
}

.doc-header {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  margin-bottom: 8px;
}

.doc-icon {
  flex-shrink: 0;
  width: 36px;
  height: 36px;
  background: #f0f2f7;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 1.1rem;
}

.doc-main {
  flex: 1;
  min-width: 0;
}

.filename {
  color: #222;
  font-weight: 600;
  font-size: 0.9375rem;
  word-break: break-all;
  margin: 0;
  line-height: 1.3;
}

.doc-meta {
  color: #999;
  font-size: 0.8rem;
  margin: 4px 0 0 0;
}

.status {
  display: inline-flex;
  align-items: center;
  padding: 2px 10px;
  border-radius: 9999px;
  font-size: 12px;
  font-weight: 600;
}

.status-pending {
  background: #f0f2f7;
  color: #888;
}

.status-running {
  background: #fffbe6;
  color: #faad14;
}

.status-success {
  background: #f6ffed;
  color: #52c41a;
}

.status-error {
  background: #fff1f0;
  color: #ff4d4f;
}

.parse-error-block {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.error-message {
  color: #ff4d4f;
  font-size: 12px;
  margin: 0;
  padding: 6px 10px;
  background: #fff1f0;
  border-radius: 6px;
  line-height: 1.4;
}

.doc-actions {
  display: flex;
  gap: 8px;
  margin-top: 8px;
}

.view-btn,
.delete-btn {
  flex: 1;
  padding: 6px 10px;
  border: none;
  border-radius: 6px;
  cursor: pointer;
  font-size: 12px;
  font-weight: 500;
  font-family: inherit;
  transition: all 0.2s ease;
}

.view-btn {
  background: #D7041A;
  color: #fff;
}

.view-btn:hover {
  filter: brightness(1.08);
}

.delete-btn {
  background: #f0f2f7;
  color: #888;
}

.delete-btn:hover {
  background: #fff1f0;
  color: #ff4d4f;
}

.upload-area {
  display: flex;
}

.upload-pick-btn {
  flex: 1;
  padding: 18px;
  border: 1.5px dashed #F1515D;
  border-radius: 10px;
  background: #fafbff;
  color: #D7041A;
  cursor: pointer;
  font-size: 13px;
  font-weight: 500;
  font-family: inherit;
  transition: all 0.2s ease;
}

.upload-pick-btn:hover {
  background: #FEE7E8;
  border-color: #D7041A;
}

.upload-pick-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
  border-color: #d9d9d9;
  color: #999;
  background: #f5f5f5;
}
.upload-pick-btn:disabled:hover {
  background: #f5f5f5;
  border-color: #d9d9d9;
}

/* 临时上传卡片（上传中占位，未持久化到后端） */
.temp-upload-item {
  background: #fafbfc;
}
.upload-progress-row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin: 4px 0 6px;
  font-size: 12px;
}
.upload-status-text {
  color: #1677ff;
  font-weight: 500;
}
.upload-bytes {
  color: #8c8c8c;
}

.upload-limit {
  text-align: center;
  padding: 16px;
  color: #999;
  font-size: 0.85rem;
  background: #fafbfd;
  border-radius: 8px;
  border: 1px solid #eef0f5;
}

.upload-note {
  margin: 0;
  color: #aaa;
  font-size: 12px;
  text-align: center;
}

/* ============ 开始检查按钮 ============ */
.check-btn {
  align-self: center;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  height: 52px;
  min-width: 260px;
  padding: 0 56px;
  border: 0;
  border-radius: 8px;
  background: linear-gradient(90deg, #D7041A 0%, #B80015 100%);
  color: #fff;
  cursor: pointer;
  font-family: inherit;
  font-size: 18px;
  font-weight: 600;
  letter-spacing: 4px;
  box-shadow: 0 6px 16px rgba(215, 4, 26, 0.28);
  transition: filter 0.2s ease, transform 0.1s ease;
}

.check-btn__icon {
  width: 20px;
  height: 20px;
  object-fit: contain;
}

.check-btn:hover:not(:disabled) {
  filter: brightness(1.06);
}

.check-btn:active:not(:disabled) {
  transform: scale(0.98);
}

.check-btn:disabled {
  cursor: not-allowed;
  opacity: 0.5;
  box-shadow: none;
}

/* ============ 文档查看器 Modal ============ */
.modal-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.6);
  display: flex;
  justify-content: center;
  align-items: center;
  z-index: 100;
  backdrop-filter: blur(4px);
}

.doc-viewer-modal {
  background: #fff;
  border-radius: 12px;
  border: 1px solid #eef0f5;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.15);
  width: 90%;
  max-width: 900px;
  max-height: 80vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.doc-viewer-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 14px 24px;
  border-bottom: 1px solid #eef0f5;
  background: #fafbfd;
}

.doc-viewer-header h3 {
  margin: 0;
  color: #222;
  font-size: 1rem;
}

.close-btn {
  background: none;
  border: none;
  font-size: 1.5rem;
  cursor: pointer;
  color: #888;
  padding: 0;
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 6px;
  transition: all 0.2s ease;
}

.close-btn:hover {
  color: #333;
  background: #f0f2f7;
}

.doc-viewer-body {
  flex: 1;
  overflow: auto;
  padding: 24px;
}

.loading {
  text-align: center;
  padding: 32px;
  color: #888;
}

.doc-content {
  font-size: 0.9375rem;
}

.html-content {
  color: #333;
  line-height: 1.6;
  margin: 0;
}

.html-content :deep(h1),
.html-content :deep(h2),
.html-content :deep(h3),
.html-content :deep(h4),
.html-content :deep(h5),
.html-content :deep(h6) {
  margin-top: 1em;
  margin-bottom: 0.5em;
  font-weight: 600;
}

.html-content :deep(p) {
  margin: 0.5em 0;
}

.html-content :deep(ul),
.html-content :deep(ol) {
  margin: 0.5em 0;
  padding-left: 1.5em;
}

.html-content :deep(table) {
  border-collapse: collapse;
  margin: 0.5em 0;
}

.html-content :deep(th),
.html-content :deep(td) {
  border: 1px solid #e0e0e0;
  padding: 0.4em 0.8em;
}

.html-content :deep(img) {
  max-width: 100%;
  height: auto;
}

.no-content {
  color: #999;
  text-align: center;
  padding: 32px;
}

/* Markdown 内容复用全局 .markdown-content 样式（common.css） */

/* ============ 响应式 ============ */
@media (max-width: 900px) {
  .card-project {
    grid-template-columns: 1fr;
    gap: 20px;
  }

  .card-project__art {
    order: -1;
  }

  .card-project__art img {
    max-width: 220px;
    max-height: 160px;
  }
}

@media (max-width: 640px) {
  .documents-grid {
    grid-template-columns: 1fr;
  }

  .card-title {
    font-size: 18px;
  }

  .check-btn {
    width: 100%;
    max-width: 360px;
  }
}
</style>
