<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useProjectStore } from '@/stores/project'
import { documentsApi } from '@/api/client'
import type { DocumentContent } from '@/types'
import { ElMessage } from 'element-plus'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import ReviewResultsArea from '@/components/ReviewResultsArea.vue'
import TimelineArea from '@/components/TimelineArea.vue'
import DocumentParseProgress from '@/components/DocumentParseProgress.vue'

// Configure DOMPurify to allow base64 images and table tags
DOMPurify.addHook('afterSanitizeAttributes', (node) => {
  if (node.tagName === 'TABLE') {
    node.setAttribute('border', '1')
    node.setAttribute('style', 'border-collapse: collapse; width: 100%; margin: 0.5em 0;')
  }
  if (node.tagName === 'TH' || node.tagName === 'TD') {
    node.setAttribute('style', 'border: 1px solid #ddd; padding: 6px 10px;')
  }
  if (node.tagName === 'IMG') {
    node.setAttribute('style', 'max-width: 100%; height: auto; display: block; margin: 0.5em 0;')
  }
})

const route = useRoute()
const router = useRouter()
const projectStore = useProjectStore()

const projectId = computed(() => route.params.id as string)
const tenderDoc = computed(() => projectStore.documents.find(d => d.doc_type === 'tender'))
const bidDoc = computed(() => projectStore.documents.find(d => d.doc_type === 'bid'))

// Document viewer state
const showDocViewer = ref(false)
const docViewerContent = ref<DocumentContent | null>(null)
const docViewerLoading = ref(false)
const docViewerTitle = ref('')

onMounted(async () => {
  await projectStore.selectProject(projectId.value)
  // Fetch review tasks for TimelineArea to use
  await projectStore.fetchReviewTasks()
  // Fetch latest review results
  await projectStore.fetchReviewResults()
})

async function handleTaskComplete(_taskId: string) {
  // 任务完成时刷新审查结果
  await projectStore.fetchReviewResults()
  // 刷新任务列表
  await projectStore.fetchReviewTasks()
}

function renderMarkdown(content: string): string {
  const html = marked.parse(content) as string
  return DOMPurify.sanitize(html, {
    ADD_TAGS: ['table', 'thead', 'tbody', 'tr', 'th', 'td', 'img'],
    ADD_ATTR: ['border', 'style', 'src', 'alt', 'width', 'height']
  })
}

function goBack() {
  router.back()
}

async function handleUpload(event: Event, docType: 'tender' | 'bid') {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  try {
    await projectStore.uploadDocument(docType, file)
    ElMessage.success(`${docType === 'tender' ? '招标书' : '应标书'}上传成功`)
  } catch {
    ElMessage.error(`${docType === 'tender' ? '招标书' : '应标书'}上传失败`)
  }
}

async function handleViewDoc(documentId: string) {
  if (!projectStore.currentProject) return

  docViewerLoading.value = true
  showDocViewer.value = true
  docViewerContent.value = null

  try {
    docViewerContent.value = await documentsApi.getContent(projectStore.currentProject.id, documentId)
    const doc = projectStore.documents.find(d => d.id === documentId)
    docViewerTitle.value = doc ? `${doc.doc_type === 'tender' ? '招标书' : '应标书'} - ${doc.original_filename}` : '文档'
  } catch (error) {
    console.error('Failed to load document:', error)
    ElMessage.error('加载文档内容失败')
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
    <header class="header">
      <div class="header-left">
        <button @click="goBack" class="back-btn">← 返回</button>
        <h1>{{ projectStore.currentProject?.name || '项目' }}</h1>
      </div>
    </header>

    <main class="content">
      <!-- Documents Section -->
      <section class="section">
        <h2>文档</h2>

        <div class="documents-grid">
          <!-- Tender Document -->
          <div class="document-card">
            <h3>招标书</h3>
            <div v-if="tenderDoc" class="document-info">
              <div class="doc-header">
                <div class="doc-icon">📄</div>
                <div class="doc-main">
                  <p class="filename">{{ tenderDoc.original_filename }}</p>
                  <DocumentParseProgress
                    v-if="tenderDoc.status === 'parsing' || tenderDoc.status === 'pending'"
                    :document-id="tenderDoc.id"
                    :stage="tenderDoc.parse_progress?.stage || 'extracting_text'"
                    :processed="tenderDoc.parse_progress?.processed || 0"
                    :total="tenderDoc.parse_progress?.total || 1"
                    :eta-seconds="tenderDoc.parse_progress?.etaSeconds || 0"
                  />
                  <span
                    v-else
                    :class="['status', getStatusClass(tenderDoc.status)]"
                  >
                    {{ tenderDoc.status }}
                  </span>
                  <p v-if="tenderDoc.page_count" class="doc-meta">
                    {{ tenderDoc.page_count }} 页, {{ tenderDoc.word_count }} 字
                  </p>
                </div>
              </div>
              <div class="doc-actions">
                <button
                  v-if="tenderDoc.status === 'parsed'"
                  class="view-btn"
                  @click="handleViewDoc(tenderDoc.id)"
                >
                  查看内容
                </button>
                <button
                  class="delete-btn"
                  @click="handleDeleteDoc(tenderDoc.id)"
                >
                  删除
                </button>
              </div>
            </div>
            <div v-else class="upload-area">
              <el-progress
                v-if="projectStore.uploadProgress['tender']"
                :percentage="projectStore.uploadProgress['tender'].percent"
                :stroke-width="8"
                :show-text="true"
              />
              <input
                v-else
                type="file"
                accept=".pdf,.docx,.doc"
                :id="'tender-upload'"
                class="file-input"
                @change="handleUpload($event, 'tender')"
              />
              <label v-if="!projectStore.uploadProgress['tender']" for="tender-upload" class="upload-label">
                点击上传 PDF 或 Word 文件
              </label>
            </div>
          </div>

          <!-- Bid Document -->
          <div class="document-card">
            <h3>应标书</h3>
            <div v-if="bidDoc" class="document-info">
              <div class="doc-header">
                <div class="doc-icon">📄</div>
                <div class="doc-main">
                  <p class="filename">{{ bidDoc.original_filename }}</p>
                  <DocumentParseProgress
                    v-if="bidDoc.status === 'parsing' || bidDoc.status === 'pending'"
                    :document-id="bidDoc.id"
                    :stage="bidDoc.parse_progress?.stage || 'extracting_text'"
                    :processed="bidDoc.parse_progress?.processed || 0"
                    :total="bidDoc.parse_progress?.total || 1"
                    :eta-seconds="bidDoc.parse_progress?.etaSeconds || 0"
                  />
                  <span
                    v-else
                    :class="['status', getStatusClass(bidDoc.status)]"
                  >
                    {{ bidDoc.status }}
                  </span>
                  <p v-if="bidDoc.page_count" class="doc-meta">
                    {{ bidDoc.page_count }} 页, {{ bidDoc.word_count }} 字
                  </p>
                </div>
              </div>
              <div class="doc-actions">
                <button
                  v-if="bidDoc.status === 'parsed'"
                  class="view-btn"
                  @click="handleViewDoc(bidDoc.id)"
                >
                  查看内容
                </button>
                <button
                  class="delete-btn"
                  @click="handleDeleteDoc(bidDoc.id)"
                >
                  删除
                </button>
              </div>
            </div>
            <div v-else class="upload-area">
              <el-progress
                v-if="projectStore.uploadProgress['bid']"
                :percentage="projectStore.uploadProgress['bid'].percent"
                :stroke-width="8"
                :show-text="true"
              />
              <input
                v-else
                type="file"
                accept=".pdf,.docx,.doc"
                :id="'bid-upload'"
                class="file-input"
                @change="handleUpload($event, 'bid')"
              />
              <label v-if="!projectStore.uploadProgress['bid']" for="bid-upload" class="upload-label">
                点击上传 PDF 或 Word 文件
              </label>
            </div>
          </div>
        </div>
      </section>

      <!-- Timeline Section -->
      <section class="section">
        <h2>时间线</h2>
        <TimelineArea
          :project-id="projectId"
          @task-complete="handleTaskComplete"
        />
      </section>

      <!-- Results Section -->
      <section class="section">
        <h2>审查结果</h2>
        <ReviewResultsArea :review-results="projectStore.reviewResults" />
      </section>
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
              class="markdown-body markdown-content"
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
  min-height: 100vh;
}

.header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 1rem 2rem;
  background: white;
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
}

.header-left {
  display: flex;
  align-items: center;
  gap: 1rem;
}

.back-btn {
  padding: 0.5rem 1rem;
  background: #ddd;
  color: #333;
  border: none;
  border-radius: 4px;
  cursor: pointer;
}

.header h1 {
  color: #333;
  font-size: 1.5rem;
}

.content {
  max-width: 1200px;
  margin: 2rem auto;
  padding: 0 2rem;
}

.section {
  background: white;
  padding: 1.5rem;
  border-radius: 8px;
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
  margin-bottom: 2rem;
}

.section h2 {
  color: #1e1b4b;
  margin-bottom: 1.5rem;
  padding-bottom: 0.5rem;
  border-bottom: 2px solid #6366f1;
}

.documents-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: 1.5rem;
}

.document-card {
  border: 1px solid #e5e7eb;
  border-radius: 12px;
  padding: 1.25rem;
  background: #fff;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
  transition: box-shadow 0.2s ease;
}

.document-card:hover {
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
}

.document-card h3 {
  color: #1e1b4b;
  font-size: 0.85rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin-bottom: 1rem;
  padding-bottom: 0.75rem;
  border-bottom: 1px solid #f3f4f6;
}

.document-info {
  text-align: left;
}

.doc-header {
  display: flex;
  align-items: flex-start;
  gap: 0.75rem;
  margin-bottom: 0.75rem;
}

.doc-icon {
  flex-shrink: 0;
  width: 36px;
  height: 36px;
  background: #f5f3ff;
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
  color: #111827;
  font-weight: 600;
  font-size: 0.95rem;
  word-break: break-all;
  margin: 0;
  line-height: 1.3;
}

.doc-meta {
  color: #6b7280;
  font-size: 0.8rem;
  margin: 0.25rem 0 0 0;
}

.upload-area {
  position: relative;
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
  padding: 2rem;
  border: 2px dashed #6366f1;
  border-radius: 8px;
  color: #6366f1;
  cursor: pointer;
  transition: background-color 0.2s ease;
}

.upload-label:hover {
  background: #f5f3ff;
}

.status {
  display: inline-flex;
  align-items: center;
  padding: 0.2rem 0.6rem;
  border-radius: 9999px;
  font-size: 0.75rem;
  font-weight: 600;
}

.status-pending {
  background: #f3f4f6;
  color: #6b7280;
}

.status-running {
  background: #fef9c3;
  color: #854d0e;
}

.status-success {
  background: #dcfce7;
  color: #166534;
}

.status-error {
  background: #fee2e2;
  color: #991b1b;
}

.doc-actions {
  display: flex;
  gap: 0.5rem;
  margin-top: 0.75rem;
}

.view-btn, .delete-btn {
  flex: 1;
  padding: 0.5rem 0.75rem;
  border: none;
  border-radius: 8px;
  cursor: pointer;
  font-size: 0.8rem;
  font-weight: 500;
  transition: all 0.2s ease;
}

.view-btn {
  background: #6366f1;
  color: white;
}

.view-btn:hover {
  background: #4f46e5;
}

.delete-btn {
  background: #f3f4f6;
  color: #6b7280;
}

.delete-btn:hover {
  background: #fee2e2;
  color: #dc2626;
}

/* Document Viewer Modal */
.modal-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  justify-content: center;
  align-items: center;
  z-index: 100;
}

.doc-viewer-modal {
  background: white;
  border-radius: 8px;
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
  border-bottom: 1px solid #ddd;
  background: #f8f8f8;
}

.doc-viewer-header h3 {
  margin: 0;
  color: #333;
  font-size: 1.1rem;
}

.close-btn {
  background: none;
  border: none;
  font-size: 1.5rem;
  cursor: pointer;
  color: #666;
  padding: 0;
  width: 2rem;
  height: 2rem;
  display: flex;
  align-items: center;
  justify-content: center;
}

.close-btn:hover {
  color: #333;
}

.doc-viewer-body {
  flex: 1;
  overflow: auto;
  padding: 1.5rem;
}

.loading {
  text-align: center;
  padding: 2rem;
  color: #666;
}

.doc-content {
  font-size: 0.95rem;
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
  border: 1px solid #ddd;
  padding: 0.4em 0.8em;
}

.html-content :deep(img) {
  max-width: 100%;
  height: auto;
}

.no-content {
  color: #999;
  text-align: center;
  padding: 2rem;
}

/* Markdown styles */
.markdown-content {
  line-height: 1.6;
  color: var(--text-primary);
}

.markdown-content h1,
.markdown-content h2,
.markdown-content h3 {
  margin-top: 1.5em;
  margin-bottom: 0.5em;
  font-weight: 600;
}

.markdown-content h1 { font-size: 1.75em; }
.markdown-content h2 { font-size: 1.5em; }
.markdown-content h3 { font-size: 1.25em; }

.markdown-content p {
  margin-bottom: 1em;
}

.markdown-content ul,
.markdown-content ol {
  margin-bottom: 1em;
  padding-left: 2em;
}

.markdown-content li {
  margin-bottom: 0.5em;
}

.markdown-content img {
  max-width: 100%;
  height: auto;
  display: block;
  margin: 1em 0;
}

.markdown-content table {
  border-collapse: collapse;
  width: 100%;
  margin-bottom: 1em;
}

.markdown-content th,
.markdown-content td {
  border: 1px solid #ddd;
  padding: 8px;
}

.markdown-content th {
  background-color: #f5f5f5;
  font-weight: 600;
}

.markdown-content code {
  background-color: #f5f5f5;
  padding: 0.2em 0.4em;
  border-radius: 3px;
  font-size: 0.9em;
}

.markdown-content pre {
  background-color: #f5f5f5;
  padding: 1em;
  border-radius: 6px;
  overflow-x: auto;
  margin-bottom: 1em;
}

.markdown-content pre code {
  background: none;
  padding: 0;
}

</style>
