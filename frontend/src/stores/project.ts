import { defineStore } from 'pinia'
import { ref } from 'vue'
import { projectsApi, documentsApi, reviewApi, getAccessToken } from '@/api/client'
import type { Project, Document, ReviewTask, ReviewResponse, SSEEvent, UploadProgress, ReviewTaskListItem } from '@/types'

export interface AgentStep {
  step_number: number
  step_type: string
  tool_name?: string
  content: string
  timestamp: Date
}

export const useProjectStore = defineStore('project', () => {
  const projects = ref<Project[]>([])
  const currentProject = ref<Project | null>(null)
  const documents = ref<Document[]>([])
  const currentTask = ref<ReviewTask | null>(null)
  const reviewResults = ref<ReviewResponse | null>(null)
  const loading = ref(false)
  const reviewLoading = ref(false)
  const sseEventSource = ref<EventSource | null>(null)

  // Agent timeline steps
  const agentSteps = ref<AgentStep[]>([])

  // Historical review tasks
  const reviewTasks = ref<ReviewTaskListItem[]>([])
  const selectedTaskId = ref<string | null>(null)

  // Upload progress state
  const uploadProgress = ref<Record<string, UploadProgress>>({})

  async function fetchProjects() {
    loading.value = true
    try {
      projects.value = await projectsApi.list()
    } finally {
      loading.value = false
    }
  }

  async function createProject(name: string, description?: string) {
    loading.value = true
    try {
      const project = await projectsApi.create({ name, description })
      projects.value.unshift(project)
      return project
    } finally {
      loading.value = false
    }
  }

  async function deleteProject(id: string) {
    await projectsApi.delete(id)
    projects.value = projects.value.filter(p => p.id !== id)
    if (currentProject.value?.id === id) {
      currentProject.value = null
    }
  }

  async function selectProject(id: string) {
    loading.value = true
    try {
      currentProject.value = await projectsApi.get(id)
      documents.value = await documentsApi.list(id)
    } finally {
      loading.value = false
    }
  }

  async function uploadDocument(
    docType: 'tender' | 'bid',
    file: File,
    onProgress?: (progress: UploadProgress) => void
  ) {
    if (!currentProject.value) return
    const doc = await documentsApi.upload(
      currentProject.value.id,
      docType,
      file,
      (progress) => {
        uploadProgress.value[docType] = progress
        onProgress?.(progress)
      }
    )
    delete uploadProgress.value[docType]
    documents.value.push(doc)
    // Start polling for document status updates
    pollDocumentStatus(doc.id)
    return doc
  }

  let documentPollIntervals: Record<string, number> = {}

  async function pollDocumentStatus(documentId: string) {
    if (!currentProject.value) return

    const checkStatus = async () => {
      try {
        const updatedDoc = await documentsApi.get(currentProject.value!.id, documentId)
        const index = documents.value.findIndex(d => d.id === documentId)
        if (index !== -1) {
          documents.value[index] = updatedDoc
        }

        // Stop polling if status is final
        if (updatedDoc.status === 'parsed' || updatedDoc.status === 'failed') {
          if (documentPollIntervals[documentId]) {
            clearInterval(documentPollIntervals[documentId])
            delete documentPollIntervals[documentId]
          }
        }
      } catch (err) {
        console.error('Failed to poll document status:', err)
        // Stop polling on error
        if (documentPollIntervals[documentId]) {
          clearInterval(documentPollIntervals[documentId])
          delete documentPollIntervals[documentId]
        }
      }
    }

    // Poll every 2 seconds
    documentPollIntervals[documentId] = window.setInterval(checkStatus, 2000)
    // Also check immediately
    await checkStatus()
  }

  async function deleteDocument(documentId: string) {
    if (!currentProject.value) return
    // Stop polling for this document
    clearDocumentPoll(documentId)
    await documentsApi.delete(currentProject.value.id, documentId)
    documents.value = documents.value.filter(d => d.id !== documentId)
  }

  async function startReview() {
    if (!currentProject.value) return
    reviewLoading.value = true
    resetAgentSteps()
    try {
      currentTask.value = await reviewApi.start(currentProject.value.id)
      connectSSE(currentTask.value.id)
    } finally {
      reviewLoading.value = false
    }
  }

  async function fetchReviewResults() {
    if (!currentProject.value) return
    reviewResults.value = await reviewApi.getResults(currentProject.value.id)
  }

  // SSE reconnection state
  let sseReconnectAttempts = 0
  const MAX_RECONNECT_DELAY = 30000 // 30 seconds max
  const BASE_RECONNECT_DELAY = 1000 // 1 second base
  let sseReconnectTimeout: number | null = null
  let currentSseTaskId: string | null = null

  function getReconnectDelay(): number {
    // Exponential backoff: 1s, 2s, 4s, 8s, 16s, 30s (capped)
    const delay = Math.min(BASE_RECONNECT_DELAY * Math.pow(2, sseReconnectAttempts), MAX_RECONNECT_DELAY)
    // Add jitter (0-500ms)
    return delay + Math.random() * 500
  }

  function connectSSE(taskId: string) {
    disconnectSSE()
    currentSseTaskId = taskId
    sseReconnectAttempts = 0

    const token = getAccessToken()
    const url = token
      ? `/api/events/tasks/${taskId}/stream?token=${encodeURIComponent(token)}`
      : `/api/events/tasks/${taskId}/stream`
    sseEventSource.value = new EventSource(url)

    sseEventSource.value.onmessage = (event) => {
      try {
        const data: SSEEvent = JSON.parse(event.data)
        console.log('SSE event received:', data)
        handleSSEEvent(data)
      } catch (err) {
        console.error('Failed to parse SSE event:', err, 'Raw data:', event.data)
      }
    }

    sseEventSource.value.onerror = (e) => {
      // If task is completed or failed, don't reconnect
      if (currentTask.value?.status === 'completed' || currentTask.value?.status === 'failed') {
        disconnectSSE()
        return
      }

      // Exponential backoff reconnection
      const delay = getReconnectDelay()
      sseReconnectAttempts++

      console.error(`SSE connection error, reconnecting in ${Math.round(delay)}ms (attempt ${sseReconnectAttempts}):`, e)

      if (sseReconnectTimeout) {
        clearTimeout(sseReconnectTimeout)
      }

      sseReconnectTimeout = window.setTimeout(() => {
        if (currentSseTaskId && (currentTask.value?.status === 'running' || !currentTask.value)) {
          connectSSE(currentSseTaskId)
        }
      }, delay)
    }
  }

  function handleSSEEvent(event: SSEEvent) {
    switch (event.type) {
      case 'status':
        if (currentTask.value) {
          currentTask.value.status = event.status as ReviewTask['status']
          console.log('[SSE] Status updated:', currentTask.value.status)
        }
        break
      case 'progress':
        // Progress events don't need special handling, just log for debugging
        console.log('[SSE] Progress:', event.message)
        break
      case 'step':
        // Also update status to 'running' if we receive step events
        // This handles the case where the initial status event was missed
        // due to SSE connection being established before Celery task started
        if (currentTask.value && currentTask.value.status === 'pending') {
          currentTask.value.status = 'running'
          console.log('[SSE] Status auto-updated to running from step event')
        }
        if (event.step_number !== undefined) {
          // Deduplicate by step_number to prevent duplicate entries on SSE reconnect
          const exists = agentSteps.value.some(s => s.step_number === event.step_number)
          if (!exists) {
            // Force new array reference to trigger Vue reactivity
            const newSteps = [...agentSteps.value, {
              step_number: event.step_number,
              step_type: event.step_type || 'unknown',
              tool_name: event.tool_name,
              content: event.content || '',
              timestamp: new Date(),
            }]
            agentSteps.value = newSteps
            console.log('[SSE] Step added, total steps:', agentSteps.value.length)
          } else {
            console.log('[SSE] Duplicate step ignored:', event.step_number)
          }
        }
        break
      case 'complete':
        if (currentTask.value) {
          currentTask.value.status = 'completed'
        }
        fetchReviewResults()
        disconnectSSE()
        break
      case 'error':
        if (currentTask.value) {
          currentTask.value.status = 'failed'
          currentTask.value.error_message = event.message || 'Unknown error'
        }
        disconnectSSE()
        break
    }
  }

  function resetAgentSteps() {
    agentSteps.value = []
  }

  function disconnectSSE() {
    if (sseEventSource.value) {
      sseEventSource.value.close()
      sseEventSource.value = null
    }
    if (sseReconnectTimeout) {
      clearTimeout(sseReconnectTimeout)
      sseReconnectTimeout = null
    }
    currentSseTaskId = null
    sseReconnectAttempts = 0
  }

  function $reset() {
    projects.value = []
    currentProject.value = null
    documents.value = []
    currentTask.value = null
    reviewResults.value = null
    uploadProgress.value = {}
    disconnectSSE()
    // Clear all document polling intervals
    Object.values(documentPollIntervals).forEach(intervalId => clearInterval(intervalId))
    documentPollIntervals = {}
  }

  function clearDocumentPoll(documentId: string) {
    if (documentPollIntervals[documentId]) {
      clearInterval(documentPollIntervals[documentId])
      delete documentPollIntervals[documentId]
    }
  }

  async function fetchReviewTasks() {
    if (!currentProject.value) return
    reviewTasks.value = await reviewApi.getTasks(currentProject.value.id)
  }

  async function selectReviewTask(taskId: string | null) {
    if (!taskId) {
      currentTask.value = null
      return
    }
    selectedTaskId.value = taskId
    // Find the task in reviewTasks
    const task = reviewTasks.value.find(t => t.id === taskId)
    if (task) {
      currentTask.value = {
        id: task.id,
        project_id: task.project_id,
        status: task.status,
        started_at: task.started_at,
        completed_at: task.completed_at,
        error_message: null,
        created_at: task.created_at,
      }
    }
  }

  async function loadHistoricalSteps(taskId: string) {
    if (!currentProject.value) return
    const steps = await reviewApi.getSteps(currentProject.value.id, taskId)
    agentSteps.value = steps.map(s => ({
      step_number: s.step_number,
      step_type: s.step_type,
      tool_name: s.tool_name || undefined,
      content: s.content,
      timestamp: new Date(s.created_at),
    }))
  }

  return {
    projects,
    currentProject,
    documents,
    currentTask,
    reviewResults,
    loading,
    reviewLoading,
    agentSteps,
    uploadProgress,
    reviewTasks,
    selectedTaskId,
    fetchProjects,
    createProject,
    deleteProject,
    selectProject,
    uploadDocument,
    deleteDocument,
    startReview,
    fetchReviewResults,
    handleSSEEvent,
    resetAgentSteps,
    connectSSE,
    disconnectSSE,
    clearDocumentPoll,
    fetchReviewTasks,
    selectReviewTask,
    loadHistoricalSteps,
    $reset
  }
})
