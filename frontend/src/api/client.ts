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
let refreshToken: string | null = null
let isRefreshing = false
let refreshSubscribers: Array<(token: string) => void> = []

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

export function setRefreshToken(token: string | null) {
  refreshToken = token
  if (token) {
    localStorage.setItem('refresh_token', token)
  } else {
    localStorage.removeItem('refresh_token')
  }
}

export function getRefreshToken(): string | null {
  if (!refreshToken) {
    refreshToken = localStorage.getItem('refresh_token')
  }
  return refreshToken
}

export function clearTokens() {
  accessToken = null
  refreshToken = null
  localStorage.removeItem('access_token')
  localStorage.removeItem('refresh_token')
}

// Subscribe to token refresh
function subscribeTokenRefresh(callback: (token: string) => void) {
  refreshSubscribers.push(callback)
}

// Notify all subscribers of new token
function onTokenRefreshed(newToken: string) {
  refreshSubscribers.forEach(callback => callback(newToken))
  refreshSubscribers = []
}

// Refresh access token using refresh token
async function refreshAccessToken(): Promise<string | null> {
  const currentRefreshToken = getRefreshToken()
  if (!currentRefreshToken) {
    return null
  }

  try {
    const response = await axios.post(`${API_BASE}/auth/refresh`, {
      refresh_token: currentRefreshToken
    })
    const tokenData = response.data as Token
    setAccessToken(tokenData.access_token)
    if (tokenData.refresh_token) {
      setRefreshToken(tokenData.refresh_token)
    }
    return tokenData.access_token
  } catch {
    clearTokens()
    return null
  }
}

// Request interceptor to add auth token
apiClient.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const token = getAccessToken()
  if (token && config.headers) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// Response interceptor to handle 401 errors (token expired)
apiClient.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config

    // Skip token refresh for login requests (they return 401 for invalid creds)
    const isLoginRequest = originalRequest.url?.includes('/auth/login')

    // Handle 401 - try token refresh (skip for login requests)
    if (error.response?.status === 401 && !originalRequest._retry && !isLoginRequest) {
      if (isRefreshing) {
        // Already refreshing, wait for new token
        return new Promise((resolve) => {
          subscribeTokenRefresh((token: string) => {
            originalRequest.headers.Authorization = `Bearer ${token}`
            resolve(apiClient(originalRequest))
          })
        })
      }

      originalRequest._retry = true
      isRefreshing = true

      const newToken = await refreshAccessToken()
      isRefreshing = false

      if (newToken) {
        onTokenRefreshed(newToken)
        originalRequest.headers.Authorization = `Bearer ${newToken}`
        return apiClient(originalRequest)
      } else {
        // Refresh failed - redirect to login
        if (window.location.pathname !== '/login') {
          window.location.href = '/login'
        }
        return Promise.reject(error)
      }
    }

    return Promise.reject(error)
  }
)

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
    if (token.refresh_token) {
      setRefreshToken(token.refresh_token)
    }
    return token
  },

  async logout() {
    clearTokens()
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
export interface UploadProgress {
  loaded: number
  total: number
  percent: number
}

export const documentsApi = {
  async list(projectId: string): Promise<Document[]> {
    const response = await apiClient.get(`/projects/${projectId}/documents`)
    return response.data.documents
  },

  async upload(
    projectId: string,
    docType: 'tender' | 'bid',
    file: File,
    onProgress?: (progress: UploadProgress) => void
  ): Promise<Document> {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest()
      const formData = new FormData()
      formData.append('file', file)

      xhr.open('POST', `${API_BASE}/projects/${projectId}/documents?doc_type=${docType}`)

      const token = getAccessToken()
      if (token) {
        xhr.setRequestHeader('Authorization', `Bearer ${token}`)
      }

      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable && onProgress) {
          onProgress({
            loaded: event.loaded,
            total: event.total,
            percent: Math.round((event.loaded / event.total) * 100)
          })
        }
      }

      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          try {
            resolve(JSON.parse(xhr.responseText))
          } catch {
            reject(new Error('Invalid response'))
          }
        } else if (xhr.status === 401) {
          // Handle 401 for XHR upload - will trigger token refresh via interceptor
          reject(new Error('Unauthorized - please login again'))
        } else {
          reject(new Error(`Upload failed with status ${xhr.status}`))
        }
      }

      xhr.onerror = () => reject(new Error('Network error'))
      xhr.send(formData)
    })
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

// Knowledge API
export const knowledgeApi = {
  listDocuments: () => apiClient.get('/knowledge/documents'),
  deleteDocument: (id: string) => apiClient.delete(`/knowledge/documents/${id}`),
  uploadDocument(
    file: File,
    onProgress?: (progress: UploadProgress) => void
  ): Promise<unknown> {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest()
      const formData = new FormData()
      formData.append('file', file)

      xhr.open('POST', `${API_BASE}/knowledge/upload`)

      const token = getAccessToken()
      if (token) {
        xhr.setRequestHeader('Authorization', `Bearer ${token}`)
      }

      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable && onProgress) {
          onProgress({
            loaded: event.loaded,
            total: event.total,
            percent: Math.round((event.loaded / event.total) * 100)
          })
        }
      }

      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          try {
            resolve(JSON.parse(xhr.responseText))
          } catch {
            reject(new Error('Invalid response'))
          }
        } else if (xhr.status === 401) {
          reject(new Error('Unauthorized - please login again'))
        } else {
          reject(new Error(`Upload failed with status ${xhr.status}`))
        }
      }

      xhr.onerror = () => reject(new Error('Network error'))
      xhr.send(formData)
    })
  },

  ragSearch: (query: string, limit: number = 10) => {
    return apiClient.post('/knowledge/rag-search', { query, limit })
  },

  getDocumentContent: (docId: string) => {
    return apiClient.get(`/knowledge/documents/${docId}/content`)
  },

  getDocumentShards: (docId: string) => {
    return apiClient.get(`/knowledge/documents/${docId}/shards`)
  },

  globalSearch: (query: string, limit: number = 20) => {
    return apiClient.post('/knowledge/search', { query, limit })
  },

  getIndexStatus: () => {
    return apiClient.get('/knowledge/index-status')
  }
}

export default apiClient
