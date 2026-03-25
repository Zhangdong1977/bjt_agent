import axios, { type AxiosInstance, type InternalAxiosRequestConfig } from 'axios'
import type {
  User,
  Token,
  Project,
  CreateProjectRequest,
  Document,
  DocumentContent,
  ReviewTask,
  ReviewResponse,
  AgentStep,
  ReviewResult
} from '@/types'

const API_BASE = '/api'

// Create axios instance
const apiClient: AxiosInstance = axios.create({
  baseURL: API_BASE,
  headers: {
    'Content-Type': 'application/json'
  }
})

// Token management
let accessToken: string | null = null

export function setAccessToken(token: string | null) {
  accessToken = token
  if (token) {
    localStorage.setItem('access_token', token)
  } else {
    localStorage.removeItem('access_token')
  }
}

export function getAccessToken(): string | null {
  if (!accessToken) {
    accessToken = localStorage.getItem('access_token')
  }
  return accessToken
}

// Request interceptor to add auth token
apiClient.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const token = getAccessToken()
  if (token && config.headers) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// Auth API
export const authApi = {
  async register(username: string, email: string, password: string): Promise<User> {
    const response = await apiClient.post('/auth/register', { username, email, password })
    return response.data
  },

  async login(username: string, password: string): Promise<Token> {
    const formData = new URLSearchParams()
    formData.append('username', username)
    formData.append('password', password)
    const response = await apiClient.post('/auth/login', formData, {
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' }
    })
    const token = response.data as Token
    setAccessToken(token.access_token)
    return token
  },

  async logout() {
    setAccessToken(null)
  },

  async getMe(): Promise<User> {
    const response = await apiClient.get('/auth/me')
    return response.data
  }
}

// Projects API
export const projectsApi = {
  async list(): Promise<Project[]> {
    const response = await apiClient.get('/projects')
    return response.data.projects
  },

  async get(id: string): Promise<Project> {
    const response = await apiClient.get(`/projects/${id}`)
    return response.data
  },

  async create(data: CreateProjectRequest): Promise<Project> {
    const response = await apiClient.post('/projects', data)
    return response.data
  },

  async delete(id: string): Promise<void> {
    await apiClient.delete(`/projects/${id}`)
  }
}

// Documents API
export const documentsApi = {
  async list(projectId: string): Promise<Document[]> {
    const response = await apiClient.get(`/projects/${projectId}/documents`)
    return response.data.documents
  },

  async upload(projectId: string, docType: 'tender' | 'bid', file: File): Promise<Document> {
    const formData = new FormData()
    formData.append('file', file)
    formData.append('doc_type', docType)
    const response = await apiClient.post(`/projects/${projectId}/documents`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' }
    })
    return response.data
  },

  async get(projectId: string, documentId: string): Promise<Document> {
    const response = await apiClient.get(`/projects/${projectId}/documents/${documentId}`)
    return response.data
  },

  async getContent(projectId: string, documentId: string): Promise<DocumentContent> {
    const response = await apiClient.get(`/projects/${projectId}/documents/${documentId}/content`)
    return response.data
  },

  async delete(projectId: string, documentId: string): Promise<void> {
    await apiClient.delete(`/projects/${projectId}/documents/${documentId}`)
  }
}

// Review API
export const reviewApi = {
  async start(projectId: string): Promise<ReviewTask> {
    const response = await apiClient.post(`/projects/${projectId}/review`)
    return response.data
  },

  async getResults(projectId: string): Promise<ReviewResponse> {
    const response = await apiClient.get(`/projects/${projectId}/review`)
    return response.data
  },

  async getTaskStatus(projectId: string, taskId: string): Promise<ReviewTask> {
    const response = await apiClient.get(`/projects/${projectId}/review/tasks/${taskId}`)
    return response.data
  },

  async cancel(projectId: string, taskId: string): Promise<ReviewTask> {
    const response = await apiClient.post(`/projects/${projectId}/review/tasks/${taskId}/cancel`)
    return response.data
  },

  async getSteps(projectId: string, taskId: string): Promise<AgentStep[]> {
    const response = await apiClient.get(`/projects/${projectId}/review/tasks/${taskId}/steps`)
    return response.data
  },

  async getResultsByTask(projectId: string, taskId: string): Promise<ReviewResult[]> {
    const response = await apiClient.get(`/projects/${projectId}/review/tasks/${taskId}/results`)
    return response.data
  }
}

// SSE Stream API
export function createSSEStream(taskId: string): EventSource {
  const token = getAccessToken()
  const url = token
    ? `/api/events/tasks/${taskId}/stream?token=${encodeURIComponent(token)}`
    : `/api/events/tasks/${taskId}/stream`
  return new EventSource(url)
}

export default apiClient
