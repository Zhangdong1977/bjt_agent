<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useProjectStore } from '@/stores/project'
import { documentsApi } from '@/api/client'
import type { DocumentContent } from '@/types'
import { ElMessage } from 'element-plus'
import ReviewTimeline from '@/components/ReviewTimeline.vue'

const route = useRoute()
const router = useRouter()
const projectStore = useProjectStore()

const projectId = computed(() => route.params.id as string)
const timelineRef = ref<InstanceType<typeof ReviewTimeline> | null>(null)
const tenderDoc = computed(() => projectStore.documents.find(d => d.doc_type === 'tender'))
const bidDoc = computed(() => projectStore.documents.find(d => d.doc_type === 'bid'))
const canStartReview = computed(() => tenderDoc.value?.status === 'parsed' && bidDoc.value?.status === 'parsed')

const showHistoricalTimeline = ref(false)
const historicalSteps = ref<TimelineStep[]>([])

interface TimelineStep {
  step_number: number
  step_type: string
  tool_name?: string
  content: string
  timestamp: Date
}

const completedTasks = computed(() =>
  projectStore.reviewTasks.filter(t => t.status === 'completed')
)

// Document viewer state
const showDocViewer = ref(false)
const docViewerContent = ref<DocumentContent | null>(null)
const docViewerLoading = ref(false)
const docViewerTitle = ref('')

onMounted(async () => {
  await projectStore.selectProject(projectId.value)
  await projectStore.fetchReviewTasks()
})

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

async function startReview() {
  try {
    await projectStore.startReview()
    ElMessage.info('审查已启动，正在连接事件流...')
    // 连接 ReviewTimeline 组件
    if (projectStore.currentTask?.id) {
      timelineRef.value?.connect(projectStore.currentTask.id)
    }
  } catch {
    ElMessage.error('启动审查失败')
  }
}

async function handleRerunReview() {
  clearHistoricalTimeline()
  await startReview()
  // Refresh the task list after re-run
  await projectStore.fetchReviewTasks()
}

const selectedHistoryTaskId = ref('')
const originalTaskId = ref<string | null>(null)

async function loadHistoricalTimeline() {
  if (!selectedHistoryTaskId.value || !projectStore.currentProject) return
  try {
    // Save current task ID for restoration when exiting historical mode
    originalTaskId.value = projectStore.currentTask?.id || null
    await projectStore.selectReviewTask(selectedHistoryTaskId.value)
    await projectStore.loadHistoricalSteps(selectedHistoryTaskId.value)
    historicalSteps.value = projectStore.agentSteps.map(s => ({
      step_number: s.step_number,
      step_type: s.step_type,
      tool_name: s.tool_name,
      content: s.content,
      timestamp: s.timestamp,
      tool_args: s.tool_args,
      tool_result: s.tool_result,
    }))
    showHistoricalTimeline.value = true
  } catch (error) {
    ElMessage.error('加载历史时间线失败')
  }
}

function clearHistoricalTimeline() {
  showHistoricalTimeline.value = false
  selectedHistoryTaskId.value = ''
  historicalSteps.value = []
  // Restore the original task
  if (originalTaskId.value) {
    projectStore.selectReviewTask(originalTaskId.value)
  } else {
    projectStore.currentTask = null
  }
  originalTaskId.value = null
}

function formatDate(dateStr: string | null): string {
  if (!dateStr) return 'N/A'
  return new Date(dateStr).toLocaleString('zh-CN')
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

function getSeverityClass(severity: string) {
  switch (severity) {
    case 'critical':
      return 'severity-critical'
    case 'major':
      return 'severity-major'
    case 'minor':
      return 'severity-minor'
    default:
      return ''
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
                  <span :class="['status', getStatusClass(tenderDoc.status)]">
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
                  <span :class="['status', getStatusClass(bidDoc.status)]">
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

      <!-- Review Section -->
      <section class="section">
        <h2>审查</h2>

        <!-- Review History Selector -->
        <div v-if="completedTasks.length > 0" class="history-selector">
          <label>查看历史记录:</label>
          <select v-model="selectedHistoryTaskId">
            <option value="">-- 选择历史任务 --</option>
            <option v-for="task in completedTasks" :key="task.id" :value="task.id">
              {{ formatDate(task.completed_at) }} - {{ task.status }}
            </option>
          </select>
          <button v-if="selectedHistoryTaskId" @click="loadHistoricalTimeline" class="secondary-btn">
            加载历史时间线
          </button>
          <button v-if="selectedHistoryTaskId" @click="clearHistoricalTimeline" class="secondary-btn">
            关闭历史
          </button>
        </div>

        <div v-if="showHistoricalTimeline" class="rerun-section">
          <button @click="handleRerunReview" class="primary-btn">
            重新审查
          </button>
        </div>

        <div class="review-controls">
          <button
            v-if="!showHistoricalTimeline && (!projectStore.currentTask || projectStore.currentTask.status === 'completed' || projectStore.currentTask.status === 'failed')"
            class="primary-btn"
            :disabled="!canStartReview || projectStore.reviewLoading"
            @click="startReview"
          >
            {{ projectStore.reviewLoading ? '启动中...' : '开始审查' }}
          </button>

          <div v-if="projectStore.currentTask" class="task-status">
            <span :class="['status', getStatusClass(projectStore.currentTask.status)]">
              {{ projectStore.currentTask.status }}
            </span>
            <p v-if="projectStore.currentTask.error_message" class="error-msg">
              {{ projectStore.currentTask.error_message }}
            </p>
          </div>
        </div>

        <p v-if="!canStartReview && !tenderDoc && !bidDoc" class="hint">
          请上传招标书和应标书以开始审查。
        </p>

        <!-- Agent Timeline (Live or Historical) -->
        <ReviewTimeline
          v-if="projectStore.currentTask"
          ref="timelineRef"
          :task-id="projectStore.currentTask.id"
          :initial-steps="showHistoricalTimeline ? historicalSteps : []"
          :historical-mode="showHistoricalTimeline"
        />
      </section>

      <!-- Results Section -->
      <section v-if="projectStore.reviewResults" class="section">
        <h2>审查结果</h2>

        <div class="summary">
          <div class="summary-item">
            <span class="summary-value">{{ projectStore.reviewResults.summary.total_requirements }}</span>
            <span class="summary-label">总计</span>
          </div>
          <div class="summary-item success">
            <span class="summary-value">{{ projectStore.reviewResults.summary.compliant }}</span>
            <span class="summary-label">合规</span>
          </div>
          <div class="summary-item error">
            <span class="summary-value">{{ projectStore.reviewResults.summary.non_compliant }}</span>
            <span class="summary-label">不合规</span>
          </div>
          <div class="summary-item critical">
            <span class="summary-value">{{ projectStore.reviewResults.summary.critical }}</span>
            <span class="summary-label">严重</span>
          </div>
          <div class="summary-item major">
            <span class="summary-value">{{ projectStore.reviewResults.summary.major }}</span>
            <span class="summary-label">主要</span>
          </div>
          <div class="summary-item minor">
            <span class="summary-value">{{ projectStore.reviewResults.summary.minor }}</span>
            <span class="summary-label">次要</span>
          </div>
        </div>

        <div class="findings-list">
          <div
            v-for="finding in projectStore.reviewResults.findings"
            :key="finding.id"
            :class="['finding-card', { 'non-compliant': !finding.is_compliant }]"
          >
            <div class="finding-header">
              <span :class="['severity-badge', getSeverityClass(finding.severity)]">
                {{ finding.severity }}
              </span>
              <span :class="['compliance-badge', finding.is_compliant ? 'compliant' : 'non-compliant']">
                {{ finding.is_compliant ? '合规' : '不合规' }}
              </span>
            </div>
            <div class="finding-body">
              <p class="requirement"><strong>要求:</strong> {{ finding.requirement_content }}</p>
              <p class="bid-content"><strong>应标内容:</strong> {{ finding.bid_content }}</p>
              <p v-if="finding.explanation" class="explanation">{{ finding.explanation }}</p>
              <p v-if="finding.suggestion && !finding.is_compliant" class="suggestion">
                <strong>建议:</strong> {{ finding.suggestion }}
              </p>
            </div>
          </div>
        </div>
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
            <pre class="markdown-content">{{ docViewerContent.md_content || '无内容' }}</pre>
            <div v-if="docViewerContent.images?.length" class="doc-images">
              <h4>图片 ({{ docViewerContent.images.length }})</h4>
              <div class="images-grid">
                <img
                  v-for="(img, idx) in docViewerContent.images"
                  :key="idx"
                  :src="img"
                  :alt="`页面图片 ${idx + 1}`"
                  class="doc-image"
                />
              </div>
            </div>
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

.review-controls {
  display: flex;
  align-items: center;
  gap: 1rem;
}

.primary-btn {
  padding: 0.75rem 1.5rem;
  background: #6366f1;
  color: white;
  border: none;
  border-radius: 6px;
  cursor: pointer;
  font-weight: 500;
  transition: background-color 0.2s ease, transform 0.1s ease;
}

.primary-btn:hover {
  background: #4f46e5;
}

.primary-btn:active {
  transform: scale(0.98);
}

.primary-btn:disabled {
  background: #d1d5db;
  cursor: not-allowed;
}

.hint {
  color: #666;
  margin-top: 1rem;
}

.summary {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 1rem;
  margin-bottom: 2rem;
}

.summary-item {
  text-align: center;
  padding: 1rem;
  background: #f5f5f5;
  border-radius: 8px;
}

.summary-value {
  display: block;
  font-size: 2rem;
  font-weight: bold;
  color: #333;
}

.summary-label {
  color: #666;
  font-size: 0.9rem;
}

.summary-item.success .summary-value { color: #68d391; }
.summary-item.error .summary-value { color: #e53e3e; }
.summary-item.critical .summary-value { color: #c53030; }
.summary-item.major .summary-value { color: #dd6b20; }
.summary-item.minor .summary-value { color: #d69e2e; }

.findings-list {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.finding-card {
  border: 1px solid #ddd;
  border-radius: 8px;
  padding: 1rem;
}

.finding-card.non-compliant {
  border-color: #fc8181;
  background: #fff5f5;
}

.finding-header {
  display: flex;
  gap: 0.5rem;
  margin-bottom: 0.5rem;
}

.severity-badge, .compliance-badge {
  padding: 0.25rem 0.75rem;
  border-radius: 4px;
  font-size: 0.8rem;
  font-weight: 500;
}

.severity-critical { background: #c53030; color: white; }
.severity-major { background: #dd6b20; color: white; }
.severity-minor { background: #d69e2e; color: white; }

.compliance-badge.compliant { background: #68d391; color: white; }
.compliance-badge.non-compliant { background: #fc8181; color: white; }

.finding-body p {
  margin: 0.5rem 0;
  color: #333;
}

.explanation {
  color: #666;
  font-style: italic;
}

.suggestion {
  color: #6366f1;
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

.markdown-content {
  white-space: pre-wrap;
  word-wrap: break-word;
  font-family: inherit;
  color: #333;
  line-height: 1.6;
  margin: 0;
}

.doc-images {
  margin-top: 1.5rem;
  padding-top: 1rem;
  border-top: 1px solid #eee;
}

.doc-images h4 {
  margin-bottom: 1rem;
  color: #555;
}

.images-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 1rem;
}

.doc-image {
  max-width: 100%;
  height: auto;
  border: 1px solid #ddd;
  border-radius: 4px;
}

.history-selector {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin-bottom: 1rem;
  padding: 0.75rem;
  background: #f5f5f5;
  border-radius: 8px;
}

.history-selector label {
  font-weight: 500;
  color: #333;
}

.history-selector select {
  padding: 0.5rem;
  border: 1px solid #ddd;
  border-radius: 4px;
  min-width: 200px;
}

.secondary-btn {
  padding: 0.5rem 1rem;
  background: #6b7280;
  color: white;
  border: none;
  border-radius: 4px;
  cursor: pointer;
}

.secondary-btn:hover {
  background: #4b5563;
}

.rerun-section {
  margin-top: 1rem;
  padding-top: 1rem;
  border-top: 1px dashed #ddd;
}

/* Agent Timeline */
</style>
