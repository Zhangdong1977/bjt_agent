<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useProjectStore } from '@/stores/project'
import { documentsApi } from '@/api/client'
import type { DocumentContent } from '@/types'
import { ElMessage, ElScrollbar } from 'element-plus'
import { Loading } from '@element-plus/icons-vue'

const route = useRoute()
const router = useRouter()
const projectStore = useProjectStore()

const projectId = computed(() => route.params.id as string)
const tenderDoc = computed(() => projectStore.documents.find(d => d.doc_type === 'tender'))
const bidDoc = computed(() => projectStore.documents.find(d => d.doc_type === 'bid'))
const canStartReview = computed(() => tenderDoc.value?.status === 'parsed' && bidDoc.value?.status === 'parsed')

// Document viewer state
const showDocViewer = ref(false)
const docViewerContent = ref<DocumentContent | null>(null)
const docViewerLoading = ref(false)
const docViewerTitle = ref('')

onMounted(() => {
  projectStore.selectProject(projectId.value)
})

function goBack() {
  router.push({ name: 'home' })
}

async function handleUpload(event: Event, docType: 'tender' | 'bid') {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  try {
    await projectStore.uploadDocument(docType, file)
    ElMessage.success(`${docType === 'tender' ? 'Tender' : 'Bid'} document uploaded successfully`)
  } catch {
    ElMessage.error(`Failed to upload ${docType === 'tender' ? 'tender' : 'bid'} document`)
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
    docViewerTitle.value = doc ? `${doc.doc_type === 'tender' ? 'Tender' : 'Bid'} Document - ${doc.original_filename}` : 'Document'
  } catch (error) {
    console.error('Failed to load document:', error)
    alert('Failed to load document content')
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
  if (confirm('Are you sure you want to delete this document?')) {
    await projectStore.deleteDocument(docId)
  }
}

async function startReview() {
  try {
    await projectStore.startReview()
    ElMessage.info('Review started, connecting to event stream...')
  } catch {
    ElMessage.error('Failed to start review')
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
        <button @click="goBack" class="back-btn">← Back</button>
        <h1>{{ projectStore.currentProject?.name || 'Project' }}</h1>
      </div>
    </header>

    <main class="content">
      <!-- Documents Section -->
      <section class="section">
        <h2>Documents</h2>

        <div class="documents-grid">
          <!-- Tender Document -->
          <div class="document-card">
            <h3>Tender Document (招标书)</h3>
            <div v-if="tenderDoc" class="document-info">
              <p class="filename">{{ tenderDoc.original_filename }}</p>
              <span :class="['status', getStatusClass(tenderDoc.status)]">
                {{ tenderDoc.status }}
              </span>
              <p v-if="tenderDoc.page_count" class="doc-meta">
                {{ tenderDoc.page_count }} pages, {{ tenderDoc.word_count }} words
              </p>
              <button
                v-if="tenderDoc.status === 'parsed'"
                class="view-btn"
                @click="handleViewDoc(tenderDoc.id)"
              >
                View Content
              </button>
              <button
                class="delete-btn"
                @click="handleDeleteDoc(tenderDoc.id)"
              >
                Delete
              </button>
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
                Click to upload PDF or Word
              </label>
            </div>
          </div>

          <!-- Bid Document -->
          <div class="document-card">
            <h3>Bid Document (应标书)</h3>
            <div v-if="bidDoc" class="document-info">
              <p class="filename">{{ bidDoc.original_filename }}</p>
              <span :class="['status', getStatusClass(bidDoc.status)]">
                {{ bidDoc.status }}
              </span>
              <p v-if="bidDoc.page_count" class="doc-meta">
                {{ bidDoc.page_count }} pages, {{ bidDoc.word_count }} words
              </p>
              <button
                v-if="bidDoc.status === 'parsed'"
                class="view-btn"
                @click="handleViewDoc(bidDoc.id)"
              >
                View Content
              </button>
              <button
                class="delete-btn"
                @click="handleDeleteDoc(bidDoc.id)"
              >
                Delete
              </button>
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
                Click to upload PDF or Word
              </label>
            </div>
          </div>
        </div>
      </section>

      <!-- Review Section -->
      <section class="section">
        <h2>Review</h2>

        <div class="review-controls">
          <button
            v-if="!projectStore.currentTask || projectStore.currentTask.status === 'completed' || projectStore.currentTask.status === 'failed'"
            class="primary-btn"
            :disabled="!canStartReview || projectStore.reviewLoading"
            @click="startReview"
          >
            {{ projectStore.reviewLoading ? 'Starting...' : 'Start Review' }}
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
          Please upload both tender and bid documents to start the review.
        </p>

        <!-- Agent Timeline -->
        <div v-if="projectStore.currentTask && projectStore.currentTask.status === 'running'" class="timeline">
          <h3>Agent Progress</h3>
          <el-scrollbar height="300px">
            <div class="timeline-steps">
              <div
                v-for="(step, index) in projectStore.agentSteps"
                :key="index"
                :class="['timeline-step', `step-${step.step_type}`]"
              >
                <div class="step-indicator">
                  <span class="step-number">{{ step.step_number }}</span>
                </div>
                <div class="step-content">
                  <span class="step-type">
                    {{ step.step_type === 'tool_call' ? `${step.tool_name || 'tool'}` : 'Thought' }}
                  </span>
                  <p class="step-text">{{ step.content }}</p>
                </div>
              </div>
              <div v-if="projectStore.agentSteps.length === 0" class="timeline-empty">
                <el-icon class="is-loading"><Loading /></el-icon> Waiting for agent to start...
              </div>
            </div>
          </el-scrollbar>
        </div>
      </section>

      <!-- Results Section -->
      <section v-if="projectStore.reviewResults" class="section">
        <h2>Review Results</h2>

        <div class="summary">
          <div class="summary-item">
            <span class="summary-value">{{ projectStore.reviewResults.summary.total_requirements }}</span>
            <span class="summary-label">Total</span>
          </div>
          <div class="summary-item success">
            <span class="summary-value">{{ projectStore.reviewResults.summary.compliant }}</span>
            <span class="summary-label">Compliant</span>
          </div>
          <div class="summary-item error">
            <span class="summary-value">{{ projectStore.reviewResults.summary.non_compliant }}</span>
            <span class="summary-label">Non-Compliant</span>
          </div>
          <div class="summary-item critical">
            <span class="summary-value">{{ projectStore.reviewResults.summary.critical }}</span>
            <span class="summary-label">Critical</span>
          </div>
          <div class="summary-item major">
            <span class="summary-value">{{ projectStore.reviewResults.summary.major }}</span>
            <span class="summary-label">Major</span>
          </div>
          <div class="summary-item minor">
            <span class="summary-value">{{ projectStore.reviewResults.summary.minor }}</span>
            <span class="summary-label">Minor</span>
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
                {{ finding.is_compliant ? 'Compliant' : 'Non-Compliant' }}
              </span>
            </div>
            <div class="finding-body">
              <p class="requirement"><strong>Requirement:</strong> {{ finding.requirement_content }}</p>
              <p class="bid-content"><strong>Bid Content:</strong> {{ finding.bid_content }}</p>
              <p v-if="finding.explanation" class="explanation">{{ finding.explanation }}</p>
              <p v-if="finding.suggestion && !finding.is_compliant" class="suggestion">
                <strong>Suggestion:</strong> {{ finding.suggestion }}
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
          <div v-if="docViewerLoading" class="loading">Loading document...</div>
          <div v-else-if="docViewerContent" class="doc-content">
            <pre class="markdown-content">{{ docViewerContent.md_content || 'No content available' }}</pre>
            <div v-if="docViewerContent.images?.length" class="doc-images">
              <h4>Images ({{ docViewerContent.images.length }})</h4>
              <div class="images-grid">
                <img
                  v-for="(img, idx) in docViewerContent.images"
                  :key="idx"
                  :src="img"
                  :alt="`Page image ${idx + 1}`"
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
  color: #333;
  margin-bottom: 1.5rem;
  padding-bottom: 0.5rem;
  border-bottom: 2px solid #667eea;
}

.documents-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: 1.5rem;
}

.document-card {
  border: 2px dashed #ddd;
  border-radius: 8px;
  padding: 1.5rem;
  text-align: center;
}

.document-card h3 {
  color: #333;
  margin-bottom: 1rem;
}

.document-info {
  text-align: left;
}

.filename {
  color: #333;
  font-weight: 500;
  word-break: break-all;
}

.doc-meta {
  color: #666;
  font-size: 0.9rem;
  margin: 0.5rem 0;
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
  border: 2px dashed #667eea;
  border-radius: 8px;
  color: #667eea;
  cursor: pointer;
}

.upload-label:hover {
  background: #f8f8ff;
}

.status {
  display: inline-block;
  padding: 0.25rem 0.75rem;
  border-radius: 4px;
  font-size: 0.8rem;
  font-weight: 500;
  margin-top: 0.5rem;
}

.status-pending {
  background: #ddd;
  color: #666;
}

.status-running {
  background: #f6e05e;
  color: #744210;
}

.status-success {
  background: #68d391;
  color: #22543d;
}

.status-error {
  background: #fc8181;
  color: #742a2a;
}

.view-btn, .delete-btn {
  padding: 0.5rem 1rem;
  border: none;
  border-radius: 4px;
  cursor: pointer;
  margin-top: 0.5rem;
  margin-right: 0.5rem;
}

.view-btn {
  background: #667eea;
  color: white;
}

.delete-btn {
  background: #e53e3e;
  color: white;
}

.review-controls {
  display: flex;
  align-items: center;
  gap: 1rem;
}

.primary-btn {
  padding: 0.75rem 1.5rem;
  background: #667eea;
  color: white;
  border: none;
  border-radius: 4px;
  cursor: pointer;
}

.primary-btn:disabled {
  background: #ccc;
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
  color: #667eea;
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

/* Agent Timeline */
.timeline {
  margin-top: 1.5rem;
  padding-top: 1rem;
  border-top: 1px solid #eee;
}

.timeline h3 {
  color: #555;
  font-size: 1rem;
  margin-bottom: 1rem;
}

.timeline-steps {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  max-height: 300px;
  overflow-y: auto;
}

.timeline-step {
  display: flex;
  gap: 0.75rem;
  align-items: flex-start;
}

.step-indicator {
  flex-shrink: 0;
  width: 1.75rem;
  height: 1.75rem;
  border-radius: 50%;
  background: #667eea;
  color: white;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.75rem;
  font-weight: bold;
}

.step-content {
  flex: 1;
  background: #f5f5f5;
  padding: 0.5rem 0.75rem;
  border-radius: 4px;
}

.step-type {
  font-size: 0.85rem;
  font-weight: 500;
  color: #555;
}

.step-text {
  margin: 0.25rem 0 0 0;
  font-size: 0.9rem;
  color: #333;
  white-space: pre-wrap;
  word-break: break-word;
}

.step-tool_call .step-indicator {
  background: #f6ad55;
}

.step-observation .step-indicator {
  background: #68d391;
}

.timeline-empty {
  color: #666;
  font-style: italic;
  padding: 1rem;
  text-align: center;
}
</style>
