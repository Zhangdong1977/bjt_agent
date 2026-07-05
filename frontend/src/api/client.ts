import axios, {
  type AxiosInstance,
  type InternalAxiosRequestConfig,
} from "axios";
import type {
  User,
  Token,
  Captcha,
  Project,
  CreateProjectRequest,
  Document,
  DocumentContent,
  ReviewTask,
  ReviewTaskListItem,
  ReviewResponse,
  AgentStep,
  ReviewResult,
  TodoItem,
  FeedbackResponse,
  FeedbackSummary,
  BatchFeedbackResponse,
  BatchFeedbackReviewResponse,
  FeedbackCreateRequest,
  PaginatedProjectSummary,
  Wallet,
  RechargePackage,
  Coupon,
  CouponRedeemResponse,
  OrderPreviewRequest,
  OrderPreview,
  BillingOrder,
  ConsumptionRecord,
  PaymentQr,
  OrderStatus,
  ProfileUpdateRequest,
} from "@/types";

const API_BASE = import.meta.env.VITE_API_BASE || "/api";

// Create axios instance
const apiClient: AxiosInstance = axios.create({
  baseURL: API_BASE,
  headers: {
    "Content-Type": "application/json",
  },
});

// Token management
let accessToken: string | null = null;
let refreshToken: string | null = null;
let isRefreshing = false;
let refreshSubscribers: Array<(token: string) => void> = [];

export function setAccessToken(token: string | null) {
  accessToken = token;
  if (token) {
    localStorage.setItem("access_token", token);
  } else {
    localStorage.removeItem("access_token");
  }
}

export function getAccessToken(): string | null {
  if (!accessToken) {
    accessToken = localStorage.getItem("access_token");
  }
  return accessToken;
}

export function setRefreshToken(token: string | null) {
  refreshToken = token;
  if (token) {
    localStorage.setItem("refresh_token", token);
  } else {
    localStorage.removeItem("refresh_token");
  }
}

export function getRefreshToken(): string | null {
  if (!refreshToken) {
    refreshToken = localStorage.getItem("refresh_token");
  }
  return refreshToken;
}

export function clearTokens() {
  accessToken = null;
  refreshToken = null;
  localStorage.removeItem("access_token");
  localStorage.removeItem("refresh_token");
}

// Subscribe to token refresh
function subscribeTokenRefresh(callback: (token: string) => void) {
  refreshSubscribers.push(callback);
}

// Notify all subscribers of new token
function onTokenRefreshed(newToken: string) {
  refreshSubscribers.forEach((callback) => callback(newToken));
  refreshSubscribers = [];
}

// Refresh access token using refresh token
async function refreshAccessToken(): Promise<string | null> {
  const currentRefreshToken = getRefreshToken();
  if (!currentRefreshToken) {
    return null;
  }

  try {
    const response = await axios.post(`${API_BASE}/auth/refresh`, {
      refresh_token: currentRefreshToken,
    });
    const tokenData = response.data as Token;
    setAccessToken(tokenData.access_token);
    if (tokenData.refresh_token) {
      setRefreshToken(tokenData.refresh_token);
    }
    return tokenData.access_token;
  } catch {
    clearTokens();
    return null;
  }
}

// Request interceptor to add auth token
apiClient.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const token = getAccessToken();
  if (token && config.headers) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// Response interceptor to handle 401 errors (token expired)
apiClient.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config;

    // Skip token refresh for login requests (they return 401 for invalid creds)
    const isLoginRequest = originalRequest.url?.includes("/auth/login");

    // Handle 401 - try token refresh (skip for login requests)
    if (
      error.response?.status === 401 &&
      !originalRequest._retry &&
      !isLoginRequest
    ) {
      if (isRefreshing) {
        // Already refreshing, wait for new token
        return new Promise((resolve) => {
          subscribeTokenRefresh((token: string) => {
            originalRequest.headers.Authorization = `Bearer ${token}`;
            resolve(apiClient(originalRequest));
          });
        });
      }

      originalRequest._retry = true;
      isRefreshing = true;

      const newToken = await refreshAccessToken();
      isRefreshing = false;

      if (newToken) {
        onTokenRefreshed(newToken);
        originalRequest.headers.Authorization = `Bearer ${newToken}`;
        return apiClient(originalRequest);
      } else {
        // Refresh failed - redirect to login
        if (window.location.pathname !== "/login") {
          window.location.href = "/login";
        }
        return Promise.reject(error);
      }
    }

    return Promise.reject(error);
  },
);

// Auth API
export const authApi = {
  async getCaptcha(): Promise<Captcha> {
    const response = await apiClient.get("/auth/captcha");
    return response.data as Captcha;
  },

  async login(
    username: string,
    password: string,
    captchaId: string,
    captchaCode: string,
  ): Promise<Token> {
    const response = await apiClient.post("/auth/login", {
      username,
      password,
      captcha_id: captchaId,
      captcha_code: captchaCode,
    });
    const token = response.data as Token;
    setAccessToken(token.access_token);
    if (token.refresh_token) {
      setRefreshToken(token.refresh_token);
    }
    return token;
  },

  async logout() {
    clearTokens();
  },

  async getMe(): Promise<User> {
    const response = await apiClient.get("/auth/me");
    return response.data;
  },

  /** 站内注册·下发短信验证码（先校验图形验证码，后端转发到运营平台 /aiGetCode）。 */
  async sendSms(
    phone: string,
    captchaId: string,
    captchaCode: string,
  ): Promise<void> {
    await apiClient.post("/auth/send-sms", {
      phone,
      captcha_id: captchaId,
      captcha_code: captchaCode,
    });
  },

  /** 站内注册（后端转发到运营平台 /aiRegister，注册即开通 use_check=1）。 */
  async register(payload: {
    phone: string;
    sms_code: string;
    password: string;
    confirm_password: string;
    nickname: string;
    captcha_id: string;
    captcha_code: string;
  }): Promise<void> {
    await apiClient.post("/auth/register", payload);
  },
};

export const profileApi = {
  async getProfile(): Promise<User> {
    const response = await apiClient.get("/profile/me");
    return response.data;
  },

  async updateProfile(data: ProfileUpdateRequest): Promise<User> {
    const response = await apiClient.put("/profile/me", data);
    return response.data;
  },

  async changePassword(
    oldPassword: string,
    newPassword: string,
    confirmNewPassword: string,
  ): Promise<void> {
    await apiClient.post("/profile/password", {
      old_password: oldPassword,
      new_password: newPassword,
      confirm_new_password: confirmNewPassword,
    });
  },
};

export const billingApi = {
  async getWallet(): Promise<Wallet> {
    const response = await apiClient.get("/billing/wallet");
    return response.data;
  },

  async listPackages(): Promise<RechargePackage[]> {
    const response = await apiClient.get("/billing/packages");
    return response.data;
  },

  async listCoupons(): Promise<Coupon[]> {
    const response = await apiClient.get("/billing/coupons");
    return response.data;
  },

  async redeemCoupon(code: string): Promise<CouponRedeemResponse> {
    const response = await apiClient.post("/billing/coupons/redeem", { code });
    return response.data;
  },

  async previewOrder(data: OrderPreviewRequest): Promise<OrderPreview> {
    const response = await apiClient.post("/billing/orders/preview", data);
    return response.data;
  },

  async createOrder(data: OrderPreviewRequest & { accepted_agreement: boolean }): Promise<BillingOrder> {
    const response = await apiClient.post("/billing/orders", data);
    return response.data;
  },

  async listOrders(params?: {
    start_date?: string;
    end_date?: string;
    product_name?: string;
  }): Promise<BillingOrder[]> {
    const response = await apiClient.get("/billing/orders", { params });
    return response.data.orders;
  },

  async getPayQr(orderId: string): Promise<PaymentQr> {
    const response = await apiClient.get(`/billing/orders/${orderId}/pay-qrcode`);
    return response.data;
  },

  async mockPay(orderId: string): Promise<OrderStatus> {
    const response = await apiClient.post(`/billing/orders/${orderId}/mock-pay`);
    return response.data;
  },

  async getOrderStatus(orderId: string): Promise<OrderStatus> {
    const response = await apiClient.get(`/billing/orders/${orderId}/status`);
    return response.data;
  },

  async listConsumptions(params?: {
    start_date?: string;
    end_date?: string;
    project_name?: string;
  }): Promise<ConsumptionRecord[]> {
    const response = await apiClient.get("/billing/consumptions", { params });
    return response.data.consumptions;
  },
};

// Decode JWT claims from access token
export function getTokenClaims(): {
  interior_user: boolean;
  concurrency: number;
} {
  const token = getAccessToken();
  if (!token) return { interior_user: false, concurrency: 2 };
  try {
    const payload = JSON.parse(atob(token.split(".")[1]));
    return {
      interior_user: payload.interior_user ?? false,
      concurrency: payload.concurrency ?? 2,
    };
  } catch {
    return { interior_user: false, concurrency: 2 };
  }
}

// Projects API
export const projectsApi = {
  async list(): Promise<Project[]> {
    const response = await apiClient.get("/projects");
    return response.data.projects;
  },

  async get(id: string): Promise<Project> {
    const response = await apiClient.get(`/projects/${id}`);
    return response.data;
  },

  async create(data: CreateProjectRequest): Promise<Project> {
    const response = await apiClient.post("/projects", data);
    return response.data;
  },

  async delete(id: string): Promise<void> {
    await apiClient.delete(`/projects/${id}`);
  },
};

// Documents API
export interface UploadProgress {
  loaded: number;
  total: number;
  percent: number;
}

export const documentsApi = {
  async list(projectId: string): Promise<Document[]> {
    const response = await apiClient.get(`/projects/${projectId}/documents`);
    return response.data.documents;
  },

  async upload(
    projectId: string,
    docType: "tender" | "bid",
    file: File,
    onProgress?: (progress: UploadProgress) => void,
  ): Promise<Document> {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      const formData = new FormData();
      formData.append("file", file);

      xhr.open(
        "POST",
        `${API_BASE}/projects/${projectId}/documents?doc_type=${docType}`,
      );

      const token = getAccessToken();
      if (token) {
        xhr.setRequestHeader("Authorization", `Bearer ${token}`);
      }

      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable && onProgress) {
          onProgress({
            loaded: event.loaded,
            total: event.total,
            percent: Math.round((event.loaded / event.total) * 100),
          });
        }
      };

      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          try {
            resolve(JSON.parse(xhr.responseText));
          } catch {
            reject(new Error("服务器返回的数据格式不正确，请稍后重试"));
          }
        } else if (xhr.status === 401) {
          // Handle 401 for XHR upload - will trigger token refresh via interceptor
          reject(new Error("登录状态已失效，请重新登录"));
        } else {
          try {
            const errorData = JSON.parse(xhr.responseText);
            reject(
              new Error(
                errorData.detail || `上传失败（HTTP ${xhr.status}），请稍后重试`,
              ),
            );
          } catch {
            reject(new Error(`上传失败（HTTP ${xhr.status}），请稍后重试`));
          }
        }
      };

      xhr.onerror = () => reject(new Error("网络连接异常，请检查网络后重试"));
      xhr.send(formData);
    });
  },

  async get(projectId: string, documentId: string): Promise<Document> {
    const response = await apiClient.get(
      `/projects/${projectId}/documents/${documentId}`,
    );
    return response.data;
  },

  async getContent(
    projectId: string,
    documentId: string,
  ): Promise<DocumentContent> {
    const response = await apiClient.get(
      `/projects/${projectId}/documents/${documentId}/content`,
    );
    return response.data;
  },

  async delete(projectId: string, documentId: string): Promise<void> {
    await apiClient.delete(`/projects/${projectId}/documents/${documentId}`);
  },

  // ===== 草稿文档（独立于项目）：选文件即上传解析，点「开始检查」时才关联到项目 =====

  async uploadDraft(
    docType: "tender" | "bid",
    file: File,
    onProgress?: (progress: UploadProgress) => void,
  ): Promise<Document> {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      const formData = new FormData();
      formData.append("file", file);

      xhr.open(
        "POST",
        `${API_BASE}/documents/upload?doc_type=${docType}`,
      );

      const token = getAccessToken();
      if (token) {
        xhr.setRequestHeader("Authorization", `Bearer ${token}`);
      }

      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable && onProgress) {
          onProgress({
            loaded: event.loaded,
            total: event.total,
            percent: Math.round((event.loaded / event.total) * 100),
          });
        }
      };

      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          try {
            resolve(JSON.parse(xhr.responseText));
          } catch {
            reject(new Error("服务器返回的数据格式不正确，请稍后重试"));
          }
        } else if (xhr.status === 401) {
          reject(new Error("登录状态已失效，请重新登录"));
        } else {
          try {
            const errorData = JSON.parse(xhr.responseText);
            reject(new Error(errorData.detail || `上传失败（HTTP ${xhr.status}）`));
          } catch {
            reject(new Error(`上传失败（HTTP ${xhr.status}）`));
          }
        }
      };

      xhr.onerror = () => reject(new Error("网络连接异常，请检查网络后重试"));
      xhr.send(formData);
    });
  },

  async listDrafts(): Promise<Document[]> {
    const response = await apiClient.get(`/documents/drafts`);
    return response.data.documents;
  },

  async attach(documentId: string, projectId: string): Promise<Document> {
    const response = await apiClient.post(
      `/documents/${documentId}/attach?project_id=${projectId}`,
    );
    return response.data;
  },

  async deleteDraft(documentId: string): Promise<void> {
    await apiClient.delete(`/documents/${documentId}`);
  },

  async getDraftContent(documentId: string): Promise<DocumentContent> {
    const response = await apiClient.get(`/documents/${documentId}/content`);
    return response.data;
  },
};

// Review API
export const reviewApi = {
  async start(projectId: string): Promise<ReviewTask> {
    const response = await apiClient.post(`/projects/${projectId}/review`);
    return response.data;
  },

  async getResults(projectId: string): Promise<ReviewResponse> {
    const response = await apiClient.get(`/projects/${projectId}/review`);
    return response.data;
  },

  async getTaskStatus(projectId: string, taskId: string): Promise<ReviewTask> {
    const response = await apiClient.get(
      `/projects/${projectId}/review/tasks/${taskId}`,
    );
    return response.data;
  },

  async cancel(projectId: string, taskId: string): Promise<ReviewTask> {
    const response = await apiClient.post(
      `/projects/${projectId}/review/tasks/${taskId}/cancel`,
    );
    return response.data;
  },

  async heartbeat(
    projectId: string,
    taskId: string,
  ): Promise<{ status: string; last_heartbeat?: string; message?: string }> {
    const response = await apiClient.post(
      `/projects/${projectId}/review/tasks/${taskId}/heartbeat`,
    );
    return response.data;
  },

  async getSteps(projectId: string, taskId: string): Promise<AgentStep[]> {
    const response = await apiClient.get(
      `/projects/${projectId}/review/tasks/${taskId}/steps`,
    );
    return response.data;
  },

  async getResultsByTask(
    projectId: string,
    taskId: string,
  ): Promise<ReviewResult[]> {
    const response = await apiClient.get(
      `/projects/${projectId}/review/tasks/${taskId}/results`,
    );
    return response.data;
  },

  async getTasks(projectId: string): Promise<ReviewTaskListItem[]> {
    const response = await apiClient.get(`/projects/${projectId}/review/tasks`);
    return response.data;
  },

  async getTodosByTask(projectId: string, taskId: string): Promise<TodoItem[]> {
    const response = await apiClient.get(
      `/projects/${projectId}/review/tasks/${taskId}/todos`,
    );
    return response.data;
  },

  async getTodoReport(
    projectId: string,
    taskId: string,
    todoId: string,
  ): Promise<string> {
    const response = await apiClient.get(
      `/projects/${projectId}/review/tasks/${taskId}/todos/${todoId}/report`,
      { responseType: "text" },
    );
    return response.data;
  },
};

// SSE Stream API
export function createSSEStream(taskId: string): EventSource {
  const token = getAccessToken();
  const url = token
    ? `${API_BASE}/events/tasks/${taskId}/stream?token=${encodeURIComponent(token)}`
    : `${API_BASE}/events/tasks/${taskId}/stream`;
  return new EventSource(url);
}

// Knowledge API
export const knowledgeApi = {
  listDocuments: () => apiClient.get("/knowledge/documents"),
  deleteDocument: (id: string) =>
    apiClient.delete(`/knowledge/documents/${id}`),
  uploadDocument(
    file: File,
    onProgress?: (progress: UploadProgress) => void,
  ): Promise<unknown> {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      const formData = new FormData();
      formData.append("file", file);

      xhr.open("POST", `${API_BASE}/knowledge/upload`);

      const token = getAccessToken();
      if (token) {
        xhr.setRequestHeader("Authorization", `Bearer ${token}`);
      }

      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable && onProgress) {
          onProgress({
            loaded: event.loaded,
            total: event.total,
            percent: Math.round((event.loaded / event.total) * 100),
          });
        }
      };

      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          try {
            resolve(JSON.parse(xhr.responseText));
          } catch {
            reject(new Error("服务器返回的数据格式不正确，请稍后重试"));
          }
        } else if (xhr.status === 401) {
          reject(new Error("登录状态已失效，请重新登录"));
        } else {
          reject(new Error(`上传失败（HTTP ${xhr.status}），请稍后重试`));
        }
      };

      xhr.onerror = () => reject(new Error("网络连接异常，请检查网络后重试"));
      xhr.send(formData);
    });
  },

  ragSearch: (query: string, limit: number = 10) => {
    return apiClient.post("/knowledge/rag-search", { query, limit });
  },

  getDocumentContent: (docId: string) => {
    return apiClient.get(`/knowledge/documents/${docId}/content`);
  },

  getDocumentShards: (docId: string) => {
    return apiClient.get(`/knowledge/documents/${docId}/shards`);
  },

  globalSearch: (query: string, limit: number = 20) => {
    return apiClient.post("/knowledge/search", { query, limit });
  },

  getIndexStatus: () => {
    return apiClient.get("/knowledge/index-status");
  },
};

// Feedback API
export const feedbackApi = {
  submitFeedback: async (
    projectId: string,
    findingId: string,
    data: FeedbackCreateRequest,
  ): Promise<FeedbackResponse> => {
    const response = await apiClient.post(
      `/projects/${projectId}/findings/${findingId}/feedback`,
      data,
    );
    return response.data;
  },

  getForFinding: async (
    projectId: string,
    findingId: string,
  ): Promise<FeedbackResponse[]> => {
    const response = await apiClient.get(
      `/projects/${projectId}/findings/${findingId}/feedback`,
    );
    return response.data;
  },

  batchConfirm: async (
    projectId: string,
    taskId: string,
    ruleDocName?: string | null,
    comment?: string,
  ): Promise<BatchFeedbackResponse> => {
    const response = await apiClient.post(
      `/projects/${projectId}/tasks/${taskId}/batch-feedback`,
      { rule_doc_name: ruleDocName ?? null, comment: comment ?? null },
    );
    return response.data;
  },

  getSummary: async (projectId: string): Promise<FeedbackSummary> => {
    const response = await apiClient.get(
      `/projects/${projectId}/feedback/summary`,
    );
    return response.data;
  },

  getMyFeedback: async (
    projectId: string,
    limit = 50,
    offset = 0,
  ): Promise<FeedbackResponse[]> => {
    const response = await apiClient.get(
      `/projects/${projectId}/feedback/history`,
      { params: { limit, offset } },
    );
    return response.data;
  },

  getPendingFeedback: async (
    projectId: string,
    limit = 100,
    offset = 0,
  ): Promise<FeedbackResponse[]> => {
    const response = await apiClient.get(
      `/projects/${projectId}/feedback/pending`,
      { params: { limit, offset } },
    );
    return response.data;
  },

  getAllFeedback: async (
    projectId: string,
    params?: {
      status?: string;
      feedback_type?: string;
      limit?: number;
      offset?: number;
    },
  ): Promise<FeedbackResponse[]> => {
    const response = await apiClient.get(
      `/projects/${projectId}/feedback/all`,
      { params },
    );
    return response.data;
  },

  getDashboard: async (projectId: string) => {
    const response = await apiClient.get(
      `/projects/${projectId}/experience/dashboard`,
    );
    return response.data;
  },

  reviewFeedback: async (
    projectId: string,
    feedbackId: string,
    action: "accept" | "reject",
    reason?: string,
  ): Promise<FeedbackResponse> => {
    const response = await apiClient.patch(
      `/projects/${projectId}/feedback/${feedbackId}/review`,
      { action, reason: reason ?? null },
    );
    return response.data;
  },

  batchReviewFeedback: async (
    projectId: string,
    action: "accept" | "reject",
    reason?: string,
    filters?: { task_id?: string; batch_id?: string },
  ): Promise<BatchFeedbackReviewResponse> => {
    const response = await apiClient.post(
      `/projects/${projectId}/feedback/batch-review`,
      { action, reason: reason ?? null, ...filters },
    );
    return response.data;
  },

  getProjectsSummary: async (params?: {
    limit?: number;
    offset?: number;
    time_range?: string;
    start_date?: string;
    end_date?: string;
    username?: string;
    project_name?: string;
    project_id?: string;
  }): Promise<PaginatedProjectSummary> => {
    const response = await apiClient.get("/experience/projects-summary", {
      params,
    });
    return response.data;
  },
};

export default apiClient;
