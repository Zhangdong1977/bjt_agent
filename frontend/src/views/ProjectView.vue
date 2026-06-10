<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useProjectStore } from '@/stores/project'
import { documentsApi } from '@/api/client'
import type { DocumentContent } from '@/types'
import { message } from 'ant-design-vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import DocumentParseProgress from '@/components/DocumentParseProgress.vue'
import { isLegacyDocFile, legacyDocWarning } from '@/utils/uploadValidation'

// Configure DOMPurify to allow base64 images and table tags
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

const route = useRoute()
const router = useRouter()
const projectStore = useProjectStore()

const projectId = computed(() => route.params.id as string)
const tenderDocs = computed(() => projectStore.documents.filter(d => d.doc_type === 'tender'))
const bidDocs = computed(() => projectStore.documents.filter(d => d.doc_type === 'bid'))
const hasParsedTender = computed(() => tenderDocs.value.some(d => d.status === 'parsed'))
const hasParsedBid = computed(() => bidDocs.value.some(d => d.status === 'parsed'))

// Get upload progress for a given doc type
const getUploadProgress = (docType: 'tender' | 'bid') => {
  const key = Object.keys(projectStore.uploadProgress).find(k => k.startsWith(`${docType}_`))
  return key ? projectStore.uploadProgress[key] : null
}

// Document viewer state
const showDocViewer = ref(false)
const docViewerContent = ref<DocumentContent | null>(null)
const docViewerLoading = ref(false)
const docViewerTitle = ref('')

onMounted(async () => {
  await projectStore.selectProject(projectId.value)
})

function renderMarkdown(content: string): string {
  const html = marked.parse(content) as string
  return DOMPurify.sanitize(html, {
    ADD_TAGS: ['table', 'thead', 'tbody', 'tr', 'th', 'td', 'img'],
    ADD_ATTR: ['border', 'style', 'src', 'alt', 'width', 'height']
  })
}

async function handleUpload(event: Event, docType: 'tender' | 'bid') {
  const input = event.target as HTMLInputElement
  const files = input.files
  if (!files?.length) return

  for (const file of files) {
    // .doc 旧版格式后端无法解析，提前拦截并给出友好提示
    if (isLegacyDocFile(file)) {
      message.warning(legacyDocWarning(file.name))
      continue
    }
    try {
      await projectStore.uploadDocument(docType, file)
      message.success(`${file.name} 上传成功`)
    } catch {
      message.error(`${file.name} 上传失败`)
    }
  }
  // Reset input so the same file can be re-uploaded
  input.value = ''
}

async function handleViewDoc(documentId: string) {
  if (!projectStore.currentProject) return

  docViewerLoading.value = true
  showDocViewer.value = true
  docViewerContent.value = null

  try {
    docViewerContent.value = await documentsApi.getContent(projectStore.currentProject.id, documentId)
    const doc = projectStore.documents.find(d => d.id === documentId)
    docViewerTitle.value = doc ? `${doc.doc_type === 'tender' ? '招标文件' : '投标文件'} - ${doc.original_filename}` : '文档'
  } catch (error) {
    console.error('Failed to load document:', error)
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

async function handleDeleteDoc(docId: string) {
  if (confirm('确定要删除此文档吗？')) {
    await projectStore.deleteDocument(docId)
  }
}

async function handleStartReview() {
  try {
    await projectStore.startReview()
    router.push({ name: 'review-execution', params: { id: projectId.value } })
  } catch {
    message.error('启动审查失败')
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
  <div class="project-view">
    <main class="content">
      <!-- Documents Section -->
      <section class="section">
        <h2>招标文件、投标文件</h2>

        <div class="documents-grid">
          <!-- Tender Documents -->
          <div class="document-card">
            <h3>招标文件 ({{ tenderDocs.length }})</h3>

            <!-- Document list -->
            <div v-if="tenderDocs.length > 0" class="doc-list">
              <div v-for="doc in tenderDocs" :key="doc.id" class="doc-item">
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
                    <span v-else :class="['status', getStatusClass(doc.status)]">
                      {{ doc.status }}
                    </span>
                    <p v-if="doc.page_count" class="doc-meta text-mono">
                      {{ doc.page_count }} 页, {{ doc.word_count }} 字
                    </p>
                  </div>
                </div>
                <div class="doc-actions">
                  <button
                    v-if="doc.status === 'parsed'"
                    class="view-btn"
                    @click="handleViewDoc(doc.id)"
                  >
                    查看内容
                  </button>
                  <button
                    class="delete-btn"
                    @click="handleDeleteDoc(doc.id)"
                  >
                    删除
                  </button>
                </div>
              </div>
            </div>

            <!-- Upload area -->
            <div v-if="tenderDocs.length >= 10" class="upload-limit">
              已达上限（10个文件）
            </div>
            <div v-else class="upload-area">
              <a-progress
                v-if="getUploadProgress('tender')"
                :percent="getUploadProgress('tender')!.percent"
                :stroke-width="8"
                :show-info="true"
              />
              <input
                v-else
                type="file"
                accept=".pdf,.docx,.doc"
                :id="'tender-upload'"
                class="file-input"
                multiple
                @change="handleUpload($event, 'tender')"
              />
              <label v-if="!getUploadProgress('tender')" for="tender-upload" class="upload-label">
                点击上传 PDF 或 Word（.docx）文件
              </label>
            </div>
          </div>

          <!-- Bid Documents -->
          <div class="document-card">
            <h3>投标文件 ({{ bidDocs.length }})</h3>

            <!-- Document list -->
            <div v-if="bidDocs.length > 0" class="doc-list">
              <div v-for="doc in bidDocs" :key="doc.id" class="doc-item">
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
                    <span v-else :class="['status', getStatusClass(doc.status)]">
                      {{ doc.status }}
                    </span>
                    <p v-if="doc.page_count" class="doc-meta text-mono">
                      {{ doc.page_count }} 页, {{ doc.word_count }} 字
                    </p>
                  </div>
                </div>
                <div class="doc-actions">
                  <button
                    v-if="doc.status === 'parsed'"
                    class="view-btn"
                    @click="handleViewDoc(doc.id)"
                  >
                    查看内容
                  </button>
                  <button
                    class="delete-btn"
                    @click="handleDeleteDoc(doc.id)"
                  >
                    删除
                  </button>
                </div>
              </div>
            </div>

            <!-- Upload area -->
            <div v-if="bidDocs.length >= 10" class="upload-limit">
              已达上限（10个文件）
            </div>
            <div v-else class="upload-area">
              <a-progress
                v-if="getUploadProgress('bid')"
                :percent="getUploadProgress('bid')!.percent"
                :stroke-width="8"
                :show-info="true"
              />
              <input
                v-else
                type="file"
                accept=".pdf,.docx,.doc"
                :id="'bid-upload'"
                class="file-input"
                multiple
                @change="handleUpload($event, 'bid')"
              />
              <label v-if="!getUploadProgress('bid')" for="bid-upload" class="upload-label">
                点击上传 PDF 或 Word（.docx）文件
              </label>
            </div>
          </div>
        </div>
      </section>

      <!-- Start Review -->
      <div class="review-action-bar">
        <button
          class="start-review-btn"
          :disabled="!hasParsedTender || !hasParsedBid"
          @click="handleStartReview"
        >
          {{ projectStore.reviewLoading ? '启动中...' : '立即检查' }}
        </button>
      </div>
    </main>

    <!-- Document Viewer Modal -->
    <div v-if="showDocViewer" class="modal-overlay" @click.self="closeDocViewer">
      <div class="doc-viewer-modal">
        <div class="doc-viewer-header">
          <h3>{{ docViewerTitle }}</h3>
          <button @click="closeDocViewer" class="close-btn">×</button>
        </div>
        <div class="doc-viewer-body">
          <div v-if="docViewerLoading" class="loading">正在加载文档...</div>
          <div v-else-if="docViewerContent" class="doc-content">
            <!-- Markdown 渲染 (DOCX/DOC) -->
            <div
              v-if="docViewerContent.format === 'markdown'"
              class="markdown-content"
              v-html="renderMarkdown(docViewerContent.content)"
            />
            <!-- HTML 渲染 (PDF) -->
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
.project-view {
}

.content {
  max-width: 1200px;
  margin: 20px auto;
  padding: 0 20px;
}

.section {
  background: var(--bg1);
  padding: 1.5rem;
  border-radius: var(--r-lg);
  border: 1px solid var(--line);
  margin-bottom: 2rem;
}

.section h2 {
  color: var(--text);
  margin-bottom: 1.5rem;
  padding-bottom: 0.5rem;
  border-bottom: 2px solid var(--blue);
}

.documents-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: 1.5rem;
}

.document-card {
  border: 1px solid var(--line);
  border-radius: var(--r);
  padding: 1.25rem;
  background: var(--bg1);
  transition: box-shadow 0.25s ease;
}

.document-card:hover {
  box-shadow: var(--shadow-md);
}

.document-card h3 {
  color: var(--muted);
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  margin-bottom: 1rem;
  padding-bottom: 0.75rem;
  border-bottom: 1px solid var(--line);
}

.doc-list {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  margin-bottom: 0.75rem;
}

.doc-item {
  padding: 0.75rem;
  background: var(--bg2);
  border-radius: var(--r-sm);
  border: 1px solid var(--line);
}

.doc-header {
  display: flex;
  align-items: flex-start;
  gap: 0.75rem;
  margin-bottom: 0.5rem;
}

.doc-icon {
  flex-shrink: 0;
  width: 36px;
  height: 36px;
  background: var(--bg3);
  border-radius: var(--r-sm);
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
  color: var(--bright);
  font-weight: 600;
  font-size: 0.9375rem;
  word-break: break-all;
  margin: 0;
  line-height: 1.3;
}

.doc-meta {
  color: var(--muted);
  font-size: 0.8rem;
  margin: 0.25rem 0 0 0;
}

.upload-area {
  position: relative;
}

.upload-limit {
  text-align: center;
  padding: 1rem;
  color: var(--muted);
  font-size: 0.85rem;
  background: var(--bg2);
  border-radius: var(--r-sm);
  border: 1px solid var(--line);
}

.file-input {
  position: absolute;
  width: 100%;
  height: 100%;
  top: 0;
  left: 0;
  opacity: 0;
  cursor: pointer;
}

.upload-label {
  display: block;
  padding: 1.5rem;
  border: 2px dashed var(--blue-dim);
  border-radius: var(--r);
  color: var(--blue);
  cursor: pointer;
  text-align: center;
  font-size: 0.875rem;
  transition: all 0.2s ease;
}

.upload-label:hover {
  background: var(--blue-bg);
  border-color: var(--blue);
}

.status {
  display: inline-flex;
  align-items: center;
  padding: 0.2rem 0.6rem;
  border-radius: var(--r-full);
  font-size: 0.75rem;
  font-weight: 600;
}

.status-pending {
  background: var(--bg3);
  color: var(--muted);
}

.status-running {
  background: var(--amber-bg);
  color: var(--amber);
}

.status-success {
  background: var(--green-bg);
  color: var(--green);
}

.status-error {
  background: var(--red-bg);
  color: var(--red);
}

.parse-error-block {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}

.error-message {
  color: var(--red);
  font-size: 0.78rem;
  margin: 0;
  padding: 0.4rem 0.6rem;
  background: var(--red-bg);
  border-radius: var(--r-sm);
  line-height: 1.4;
}

.doc-actions {
  display: flex;
  gap: 0.5rem;
  margin-top: 0.5rem;
}

.view-btn, .delete-btn {
  flex: 1;
  padding: 0.4rem 0.6rem;
  border: none;
  border-radius: var(--r-sm);
  cursor: pointer;
  font-size: 0.78rem;
  font-weight: 500;
  transition: all 0.2s ease;
  font-family: inherit;
}

.view-btn {
  background: var(--blue);
  color: #fff;
}

.view-btn:hover {
  filter: brightness(1.08);
}

.delete-btn {
  background: var(--bg3);
  color: var(--muted);
}

.delete-btn:hover {
  background: var(--red-bg);
  color: var(--red);
}

/* Document Viewer Modal */
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
  background: var(--bg1);
  border-radius: var(--r-lg);
  border: 1px solid var(--line);
  box-shadow: var(--shadow-lg);
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
  padding: 1rem 1.5rem;
  border-bottom: 1px solid var(--line);
  background: var(--bg2);
}

.doc-viewer-header h3 {
  margin: 0;
  color: var(--bright);
  font-size: 1rem;
}

.close-btn {
  background: none;
  border: none;
  font-size: 1.5rem;
  cursor: pointer;
  color: var(--sub);
  padding: 0;
  width: 2rem;
  height: 2rem;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--r-sm);
  transition: all 0.2s ease;
}

.close-btn:hover {
  color: var(--text);
  background: var(--bg3);
}

.doc-viewer-body {
  flex: 1;
  overflow: auto;
  padding: 1.5rem;
}

.loading {
  text-align: center;
  padding: 2rem;
  color: var(--sub);
}

.doc-content {
  font-size: 0.9375rem;
}

.html-content {
  color: var(--text);
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
  border: 1px solid var(--line);
  padding: 0.4em 0.8em;
}

.html-content :deep(img) {
  max-width: 100%;
  height: auto;
}

.no-content {
  color: var(--muted);
  text-align: center;
  padding: 2rem;
}

/* Markdown — uses global .markdown-content from common.css */

.review-action-bar {
  display: flex;
  justify-content: center;
  margin-top: 1.5rem;
}

.start-review-btn {
  padding: 0.75rem 2.5rem;
  background: var(--blue);
  color: #fff;
  border: none;
  border-radius: var(--r);
  cursor: pointer;
  font-size: 1rem;
  font-weight: 600;
  transition: all 0.2s ease;
  font-family: inherit;
}

.start-review-btn:hover:not(:disabled) {
  filter: brightness(1.08);
  transform: translateY(-1px);
  box-shadow: 0 4px 12px var(--blue-bg);
}

.start-review-btn:active:not(:disabled) {
  transform: scale(0.98);
}

.start-review-btn:disabled {
  background: var(--dim);
  cursor: not-allowed;
  opacity: 0.5;
}

@media (max-width: 767px) {
  .documents-grid {
    grid-template-columns: 1fr;
  }

  .content {
    padding: 0 1rem;
  }

  .section {
    padding: 1rem;
  }
}
</style>
