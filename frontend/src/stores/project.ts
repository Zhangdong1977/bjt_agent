import { defineStore } from 'pinia'
import { ref } from 'vue'
import { projectsApi, documentsApi, reviewApi } from '@/api/client'
import type { Project, Document, ReviewTask, ReviewResponse, SSEEvent } from '@/types'

export const useProjectStore = defineStore('project', () => {
  const projects = ref<Project[]>([])
  const currentProject = ref<Project | null>(null)
  const documents = ref<Document[]>([])
  const currentTask = ref<ReviewTask | null>(null)
  const reviewResults = ref<ReviewResponse | null>(null)
  const loading = ref(false)
  const reviewLoading = ref(false)
  const sseEventSource = ref<EventSource | null>(null)

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

  async function uploadDocument(docType: 'tender' | 'bid', file: File) {
    if (!currentProject.value) return
    const doc = await documentsApi.upload(currentProject.value.id, docType, file)
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

  function connectSSE(taskId: string) {
    disconnectSSE()
    sseEventSource.value = new EventSource(`/api/events/tasks/${taskId}/stream`)

    sseEventSource.value.onmessage = (event) => {
      try {
        const data: SSEEvent = JSON.parse(event.data)
        handleSSEEvent(data)
      } catch {
        console.error('Failed to parse SSE event')
      }
    }

    sseEventSource.value.onerror = () => {
      console.error('SSE connection error')
      disconnectSSE()
    }
  }

  function handleSSEEvent(event: SSEEvent) {
    switch (event.type) {
      case 'status':
        if (currentTask.value) {
          currentTask.value.status = event.status as ReviewTask['status']
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

  function disconnectSSE() {
    if (sseEventSource.value) {
      sseEventSource.value.close()
      sseEventSource.value = null
    }
  }

  function $reset() {
    projects.value = []
    currentProject.value = null
    documents.value = []
    currentTask.value = null
    reviewResults.value = null
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

  return {
    projects,
    currentProject,
    documents,
    currentTask,
    reviewResults,
    loading,
    reviewLoading,
    fetchProjects,
    createProject,
    deleteProject,
    selectProject,
    uploadDocument,
    deleteDocument,
    startReview,
    fetchReviewResults,
    handleSSEEvent,
    disconnectSSE,
    clearDocumentPoll,
    $reset
  }
})
