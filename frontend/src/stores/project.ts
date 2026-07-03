import { defineStore } from "pinia";
import { ref } from "vue";
import {
  projectsApi,
  documentsApi,
  reviewApi,
  getAccessToken,
} from "@/api/client";
import type {
  Project,
  Document,
  ReviewTask,
  ReviewResponse,
  SSEEvent,
  UploadProgress,
  ReviewTaskListItem,
} from "@/types";

export interface AgentStep {
  step_number: number;
  step_type: string;
  tool_name?: string;
  content: string;
  timestamp: Date;
  tool_args?: Record<string, any>;
  tool_result?: Record<string, any>;
}

export const useProjectStore = defineStore("project", () => {
  const API_BASE = import.meta.env.VITE_API_BASE || "/api";
  const projects = ref<Project[]>([]);
  const currentProject = ref<Project | null>(null);
  const documents = ref<Document[]>([]);
  const currentTask = ref<ReviewTask | null>(null);
  const reviewResults = ref<ReviewResponse | null>(null);
  const loading = ref(false);
  const reviewLoading = ref(false);
  const sseEventSource = ref<EventSource | null>(null);

  // Agent timeline steps
  const agentSteps = ref<AgentStep[]>([]);

  // Historical review tasks
  const reviewTasks = ref<ReviewTaskListItem[]>([]);
  const selectedTaskId = ref<string | null>(null);

  // Upload progress state
  const uploadProgress = ref<Record<string, UploadProgress>>({});

  // Track last valid absolute processed count per document for monotonicity enforcement
  const lastProcessedCount = ref<Record<string, number>>({});

  // Heartbeat state
  let heartbeatTimer: number | null = null;
  const HEARTBEAT_INTERVAL = 10000; // 10 seconds

  // Document parse SSE connections
  const docParseSSEConnections = ref<Record<string, EventSource>>({});

  async function fetchProjects() {
    loading.value = true;
    try {
      projects.value = await projectsApi.list();
    } finally {
      loading.value = false;
    }
  }

  async function createProject(name: string, description?: string) {
    loading.value = true;
    try {
      const project = await projectsApi.create({ name, description });
      projects.value.unshift(project);
      return project;
    } finally {
      loading.value = false;
    }
  }

  async function deleteProject(id: string) {
    await projectsApi.delete(id);
    projects.value = projects.value.filter((p) => p.id !== id);
    if (currentProject.value?.id === id) {
      currentProject.value = null;
    }
  }

  async function selectProject(id: string) {
    loading.value = true;
    try {
      currentProject.value = await projectsApi.get(id);
      // 隔离 documents.list 失败：内部用户在复盘页查看他人项目时，若文档端点异常
      // 不应阻断 fetchReviewTasks 等后续加载。降级为空文档列表。
      try {
        documents.value = await documentsApi.list(id);
      } catch (err) {
        console.warn(
          "[selectProject] Failed to load documents, falling back to empty list:",
          err,
        );
        documents.value = [];
      }
      // Connect SSE for any documents that are still parsing
      for (const doc of documents.value) {
        if (doc.status === "parsing" || doc.status === "pending") {
          connectDocParseSSE(doc.id);
          pollDocumentStatus(doc.id);
        }
      }
    } finally {
      loading.value = false;
    }
  }

  async function uploadDocument(
    docType: "tender" | "bid",
    file: File,
    onProgress?: (progress: UploadProgress) => void,
  ) {
    if (!currentProject.value) return;
    const uploadKey = `${docType}_${Date.now()}`;
    try {
      const doc = await documentsApi.upload(
        currentProject.value.id,
        docType,
        file,
        (progress) => {
          uploadProgress.value[uploadKey] = progress;
          onProgress?.(progress);
        },
      );
      documents.value.push(doc);
      pollDocumentStatus(doc.id);
      connectDocParseSSE(doc.id);
      return doc;
    } finally {
      delete uploadProgress.value[uploadKey];
    }
  }

  let documentPollIntervals: Record<string, number> = {};
  const documentPollRetries: Record<string, number> = {};
  const MAX_POLL_RETRIES = 5;

  async function pollDocumentStatus(documentId: string) {
    if (!currentProject.value) return;

    const checkStatus = async () => {
      try {
        const updatedDoc = await documentsApi.get(
          currentProject.value!.id,
          documentId,
        );
        const index = documents.value.findIndex((d) => d.id === documentId);
        if (index !== -1) {
          // Preserve parse_progress from SSE while updating other fields from API
          documents.value[index] = {
            ...updatedDoc,
            parse_progress: documents.value[index].parse_progress,
          };
        }

        // Reset retry count on successful request
        delete documentPollRetries[documentId];

        // Stop polling if status is final
        if (updatedDoc.status === "parsed" || updatedDoc.status === "failed") {
          clearDocumentPoll(documentId);
          // Also disconnect SSE since we have final status
          disconnectDocParseSSE(documentId);
        }
      } catch (err) {
        console.error("Failed to poll document status:", err);
        // Retry on error instead of stopping immediately
        const retries = (documentPollRetries[documentId] || 0) + 1;
        documentPollRetries[documentId] = retries;
        if (retries >= MAX_POLL_RETRIES) {
          console.error(
            `[pollDocumentStatus] Max retries (${MAX_POLL_RETRIES}) reached for document ${documentId}, stopping polling.`,
          );
          clearDocumentPoll(documentId);
          delete documentPollRetries[documentId];
        }
      }
    };

    // Poll every 2 seconds
    documentPollIntervals[documentId] = window.setInterval(checkStatus, 2000);
    // Also check immediately
    await checkStatus();
  }

  function connectDocParseSSE(documentId: string) {
    disconnectDocParseSSE(documentId);
    const token = getAccessToken();
    const url = token
      ? `${API_BASE}/events/documents/${documentId}/stream?token=${encodeURIComponent(token)}`
      : `${API_BASE}/events/documents/${documentId}/stream`;
    console.log(
      "[connectDocParseSSE] Connecting to SSE URL:",
      url,
      "documentId:",
      documentId,
    );
    const es = new EventSource(url);
    es.onopen = () => {
      console.log(
        "[connectDocParseSSE] SSE connection opened for documentId:",
        documentId,
      );
    };
    es.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        console.log(
          "[connectDocParseSSE] SSE event received for documentId:",
          documentId,
          "type:",
          data.type,
          "data:",
          data,
        );
        if (data.type === "parse_progress") {
          updateDocumentParseProgress(documentId, data);
        }
      } catch (err) {
        console.error("[connectDocParseSSE] Failed to parse SSE event:", err);
      }
    };
    es.onerror = (err) => {
      console.error(
        "[connectDocParseSSE] SSE error for documentId:",
        documentId,
        "readyState:",
        es.readyState,
        "error:",
        err,
      );
      // Silently ignore SSE errors — frontend falls back to polling
      disconnectDocParseSSE(documentId);
    };
    docParseSSEConnections.value[documentId] = es;
  }

  function disconnectDocParseSSE(documentId: string) {
    const es = docParseSSEConnections.value[documentId];
    if (es) {
      es.close();
      delete docParseSSEConnections.value[documentId];
    }
  }

  // Track last stage per document to detect stage transitions
  const lastStage = ref<Record<string, string>>({});

  function updateDocumentParseProgress(
    documentId: string,
    data: {
      stage: string;
      processed: number;
      total: number;
      eta_seconds: number;
    },
  ) {
    // Handle completion event — update document status and clean up resources
    if (data.stage === "failed") {
      console.log(
        "[updateDocumentParseProgress] Parse failed for documentId:",
        documentId,
      );
      const index = documents.value.findIndex((d) => d.id === documentId);
      if (index !== -1) {
        documents.value[index].status = "failed";
        documents.value[index].parse_error =
          "文档解析失败，请重试或更换文件格式。";
      }
      delete lastProcessedCount.value[documentId];
      delete lastStage.value[documentId];
      clearDocumentPoll(documentId);
      disconnectDocParseSSE(documentId);
      return;
    }

    if (data.stage === "completed") {
      console.log(
        "[updateDocumentParseProgress] Parse completed for documentId:",
        documentId,
      );
      const index = documents.value.findIndex((d) => d.id === documentId);
      if (index !== -1) {
        documents.value[index].status = "parsed";
      }
      // Clean up tracking state
      delete lastProcessedCount.value[documentId];
      delete lastStage.value[documentId];
      clearDocumentPoll(documentId);
      disconnectDocParseSSE(documentId);
      return;
    }

    // Reset monotonic counter on stage transition (e.g. "extracting_text" -> "processing_images")
    if (
      lastStage.value[documentId] &&
      lastStage.value[documentId] !== data.stage
    ) {
      lastProcessedCount.value[documentId] = -1;
    }
    lastStage.value[documentId] = data.stage;

    // Discard regressive progress updates (handles out-of-order SSE messages)
    // Compare absolute processed count, not percentage, since total changes across batches
    const lastCount = lastProcessedCount.value[documentId] ?? -1;
    if (data.processed < lastCount) {
      console.warn(
        "[updateDocumentParseProgress] Discarding regressive progress update:",
        data.processed,
        "<",
        lastCount,
      );
      return;
    }
    lastProcessedCount.value[documentId] = data.processed;

    const index = documents.value.findIndex((d) => d.id === documentId);
    console.log(
      "[updateDocumentParseProgress] documentId:",
      documentId,
      "found index:",
      index,
      "documents count:",
      documents.value.length,
      "data:",
      data,
    );
    if (index !== -1) {
      // Directly mutate the document object's parse_progress for better reactivity
      const doc = documents.value[index];
      doc.parse_progress = {
        stage: data.stage,
        processed: data.processed,
        total: data.total,
        etaSeconds: data.eta_seconds,
      };
      console.log(
        "[updateDocumentParseProgress] After update, parse_progress:",
        doc.parse_progress,
      );
    } else {
      console.warn(
        "[updateDocumentParseProgress] Document not found in array!",
        documentId,
      );
    }
  }

  async function deleteDocument(documentId: string) {
    if (!currentProject.value) return;
    // Stop polling for this document
    clearDocumentPoll(documentId);
    await documentsApi.delete(currentProject.value.id, documentId);
    documents.value = documents.value.filter((d) => d.id !== documentId);
  }

  async function startReview() {
    if (!currentProject.value) return;
    reviewLoading.value = true;
    resetAgentSteps();
    try {
      currentTask.value = await reviewApi.start(currentProject.value.id);
    } finally {
      reviewLoading.value = false;
    }
  }

  async function fetchReviewResults() {
    if (!currentProject.value) return;
    const results = await reviewApi.getResults(currentProject.value.id);
    console.log("[fetchReviewResults] results:", JSON.stringify(results));
    reviewResults.value = results;
  }

  // SSE reconnection state — managed by useSSE composable
  // (exponential backoff + Last-Event-ID handled internally)

  function connectSSE(taskId: string) {
    disconnectSSE();

    const token = getAccessToken();
    const url = token
      ? `${API_BASE}/events/tasks/${taskId}/stream?token=${encodeURIComponent(token)}`
      : `${API_BASE}/events/tasks/${taskId}/stream`;

    console.log("[connectSSE] Connecting to SSE URL:", url, "taskId:", taskId);

    const es = new EventSource(url);
    sseEventSource.value = es;

    es.onopen = () => {
      console.log(
        "[connectSSE] SSE connection opened successfully for taskId:",
        taskId,
      );
      // Start heartbeat when SSE connects
      if (currentProject.value && taskId) {
        startHeartbeat(currentProject.value.id, taskId);
      }
    };

    es.onmessage = (event) => {
      try {
        const data: SSEEvent = JSON.parse(event.data);
        console.log(
          "[connectSSE] SSE event received:",
          data.type,
          "taskId:",
          taskId,
          "data:",
          data,
        );
        handleSSEEvent(data);
      } catch (err) {
        console.error(
          "[connectSSE] Failed to parse SSE event:",
          err,
          "Raw data:",
          event.data,
        );
      }
    };

    es.onerror = () => {
      // If task is completed or failed, don't reconnect
      if (
        currentTask.value?.status === "completed" ||
        currentTask.value?.status === "failed"
      ) {
        stopHeartbeat();
        disconnectSSE();
        return;
      }

      // 让浏览器原生 EventSource 自动重连
      // 后端发送的 `id:` 字段会被浏览器记录，
      // 重连时自动附加 `Last-Event-ID` 请求头，实现断线续传
      console.log(
        "[connectSSE] SSE connection error, browser will auto-reconnect with Last-Event-ID",
      );
    };
  }

  function handleSSEEvent(event: SSEEvent) {
    switch (event.type) {
      case "status":
        if (currentTask.value) {
          currentTask.value.status = event.status as ReviewTask["status"];
          console.log("[SSE] Status updated:", currentTask.value.status);
        }
        break;
      case "progress":
        // Progress events don't need special handling, just log for debugging
        console.log("[SSE] Progress:", event.message);
        break;
      case "step":
        // Also update status to 'running' if we receive step events
        // This handles the case where the initial status event was missed
        // due to SSE connection being established before Celery task started
        if (currentTask.value && currentTask.value.status === "pending") {
          currentTask.value.status = "running";
          console.log("[SSE] Status auto-updated to running from step event");
        }
        if (event.step_number !== undefined) {
          // Deduplicate by step_number + step_type to prevent duplicate entries on SSE reconnect
          const exists = agentSteps.value.some(
            (s) =>
              s.step_number === event.step_number &&
              s.step_type === event.step_type,
          );
          if (!exists) {
            // Force new array reference to trigger Vue reactivity
            const newSteps = [
              ...agentSteps.value,
              {
                step_number: event.step_number,
                step_type: event.step_type || "unknown",
                tool_name: event.tool_name,
                content: event.content || "",
                timestamp: new Date(),
              },
            ];
            agentSteps.value = newSteps;
            console.log(
              "[SSE] Step added, total steps:",
              agentSteps.value.length,
            );
          } else {
            console.log("[SSE] Duplicate step ignored:", event.step_number);
          }
        }
        break;
      case "merging":
        console.log("[SSE] 正在合并历史结果...", event.message);
        // Could show a global loading indicator here
        break;
      case "merged":
        console.log(
          "[SSE] 合并完成, merged_count:",
          event.merged_count,
          "total_count:",
          event.total_count,
        );
        fetchReviewResults();
        // Stop heartbeat and disconnect after merge is complete
        stopHeartbeat();
        disconnectSSE();
        break;
      case "complete":
        if (currentTask.value) {
          currentTask.value.status = "completed";
        }
        console.log("[SSE] Review complete, fetching results...");
        fetchReviewResults();
        // Don't disconnect SSE here - wait for 'merged' event from merge task
        // The merge task runs asynchronously after review completes
        // and sends 'merged' event when done, which will trigger another fetchReviewResults
        break;
      case "error":
        if (currentTask.value) {
          currentTask.value.status = "failed";
          currentTask.value.error_message = event.message || "审查失败，暂无详细原因";
        }
        stopHeartbeat();
        disconnectSSE();
        break;
    }
  }

  function resetAgentSteps() {
    agentSteps.value = [];
  }

  function disconnectSSE() {
    if (sseEventSource.value) {
      sseEventSource.value.close();
      sseEventSource.value = null;
    }
  }

  function $reset() {
    projects.value = [];
    currentProject.value = null;
    documents.value = [];
    currentTask.value = null;
    reviewResults.value = null;
    uploadProgress.value = {};
    stopHeartbeat();
    disconnectSSE();
    // Clear all document polling intervals
    Object.values(documentPollIntervals).forEach((intervalId) =>
      clearInterval(intervalId),
    );
    documentPollIntervals = {};
    // Close all SSE connections to prevent memory leaks
    Object.values(docParseSSEConnections.value).forEach((es) => es.close());
    docParseSSEConnections.value = {};
  }

  function clearDocumentPoll(documentId: string) {
    if (documentPollIntervals[documentId]) {
      clearInterval(documentPollIntervals[documentId]);
      delete documentPollIntervals[documentId];
    }
    delete documentPollRetries[documentId];
  }

  // ============================================================
  // 草稿文档（独立于项目）：选文件即上传解析，点「开始检查」时才关联到项目。
  // draft 文档统一存进 documents.value（project_id === null 即为草稿）。
  // ============================================================

  async function uploadDraftDocument(
    docType: "tender" | "bid",
    file: File,
  ) {
    const uploadKey = `draft_${docType}_${Date.now()}`;
    try {
      const doc = await documentsApi.uploadDraft(docType, file, (progress) => {
        uploadProgress.value[uploadKey] = progress;
      });
      documents.value.push(doc);
      // SSE 按 document_id 推送解析进度，与 project 无关，可直接复用
      connectDocParseSSE(doc.id);
      pollDraftStatus(doc.id);
      return doc;
    } finally {
      delete uploadProgress.value[uploadKey];
    }
  }

  // 草稿文档轮询：用 listDrafts 拉取全部草稿状态，更新对应 doc（不依赖 currentProject）
  function pollDraftStatus(documentId: string) {
    if (documentPollIntervals[documentId]) return;

    const checkStatus = async () => {
      try {
        const drafts = await documentsApi.listDrafts();
        const updatedDoc = drafts.find((d) => d.id === documentId);
        if (updatedDoc) {
          const index = documents.value.findIndex((d) => d.id === documentId);
          if (index !== -1) {
            documents.value[index] = {
              ...updatedDoc,
              parse_progress: documents.value[index].parse_progress,
            };
          }
        }
        delete documentPollRetries[documentId];

        const current = documents.value.find((d) => d.id === documentId);
        if (current && (current.status === "parsed" || current.status === "failed")) {
          clearDocumentPoll(documentId);
          disconnectDocParseSSE(documentId);
        }
      } catch (err) {
        console.error("Failed to poll draft document status:", err);
        documentPollRetries[documentId] = (documentPollRetries[documentId] || 0) + 1;
        if (documentPollRetries[documentId] >= MAX_POLL_RETRIES) {
          clearDocumentPoll(documentId);
        }
      }
    };

    void checkStatus();
    documentPollIntervals[documentId] = window.setInterval(checkStatus, 2000);
  }

  // 加载当前用户的所有草稿文档（进入检查页时恢复未完成的解析）
  async function loadDraftDocuments() {
    try {
      const drafts = await documentsApi.listDrafts();
      // 合并：替换掉旧的草稿条目，保留项目文档
      documents.value = documents.value.filter((d) => d.project_id !== null);
      documents.value.push(...drafts);
      // 为仍在解析的草稿重连 SSE + 轮询
      for (const doc of drafts) {
        if (doc.status === "pending" || doc.status === "parsing") {
          connectDocParseSSE(doc.id);
          pollDraftStatus(doc.id);
        }
      }
    } catch (err) {
      console.error("Failed to load draft documents:", err);
    }
  }

  // 删除草稿文档
  async function deleteDraftDocument(documentId: string) {
    clearDocumentPoll(documentId);
    disconnectDocParseSSE(documentId);
    await documentsApi.deleteDraft(documentId);
    documents.value = documents.value.filter((d) => d.id !== documentId);
  }

  // 把所有草稿文档关联到指定项目（点「开始检查」创建项目后调用）
  async function attachDraftDocuments(projectId: string) {
    const drafts = documents.value.filter((d) => d.project_id === null);
    for (const doc of drafts) {
      try {
        const updated = await documentsApi.attach(doc.id, projectId);
        const index = documents.value.findIndex((d) => d.id === doc.id);
        if (index !== -1) {
          documents.value[index] = { ...updated, parse_progress: documents.value[index].parse_progress };
        }
      } catch (err) {
        console.error(`Failed to attach document ${doc.id}:`, err);
      }
    }
  }

  async function fetchReviewTasks() {
    if (!currentProject.value) return;
    reviewTasks.value = await reviewApi.getTasks(currentProject.value.id);
  }

  async function selectReviewTask(taskId: string | null) {
    if (!taskId) {
      currentTask.value = null;
      return;
    }
    selectedTaskId.value = taskId;
    // Find the task in reviewTasks
    const task = reviewTasks.value.find((t) => t.id === taskId);
    if (task) {
      currentTask.value = {
        id: task.id,
        project_id: task.project_id,
        status: task.status,
        started_at: task.started_at,
        completed_at: task.completed_at,
        duration_seconds: task.duration_seconds ?? null,
        error_message: task.error_message ?? null,
        created_at: task.created_at,
      };
    }
  }

  async function loadHistoricalSteps(taskId: string) {
    if (!currentProject.value) return;
    const steps = await reviewApi.getSteps(currentProject.value.id, taskId);

    // Group steps by step_number, then interleave observation before tool_calls
    const groupedSteps: typeof agentSteps.value = [];

    // Group by step_number
    const byNumber = new Map<number, typeof steps>();
    for (const s of steps) {
      const existing = byNumber.get(s.step_number);
      if (existing) {
        existing.push(s);
      } else {
        byNumber.set(s.step_number, [s]);
      }
    }

    // For each step_number, sort: observation/thought first, then tool
    const sortOrder: Record<string, number> = {
      observation: 0,
      thought: 1,
      tool_call: 2,
    };
    const sortedNumbers = Array.from(byNumber.keys()).sort((a, b) => a - b);

    for (const num of sortedNumbers) {
      const group = byNumber.get(num)!;
      // Sort within group: observation/thought before tool
      group.sort(
        (a, b) =>
          (sortOrder[a.step_type] ?? 99) - (sortOrder[b.step_type] ?? 99),
      );

      for (const s of group) {
        groupedSteps.push({
          step_number: s.step_number,
          step_type: s.step_type,
          tool_name: s.tool_name || undefined,
          content: s.content,
          timestamp: s.created_at ? new Date(s.created_at) : new Date(),
          tool_args: s.tool_args || undefined,
          tool_result: s.tool_result || undefined,
        });
      }
    }

    // Merge tool_result into corresponding tool_call nodes
    for (const step of groupedSteps) {
      if (step.step_type === "tool_result") {
        const pairedStep = groupedSteps.find(
          (s) =>
            s.step_number === step.step_number - 1 &&
            s.tool_name === step.tool_name &&
            s.step_type === "tool_call",
        );
        if (pairedStep) {
          pairedStep.tool_result = step.tool_result;
          (step as any)._merged = true;
        }
      }
    }

    // Filter out merged tool_result nodes
    agentSteps.value = groupedSteps.filter(
      (s) => !(s.step_type === "tool_result" && (s as any)._merged),
    );
  }

  // Track current heartbeat context for visibility change handler
  let heartbeatProjectId: string | null = null;
  let heartbeatTaskId: string | null = null;

  function handleVisibilityChange() {
    if (
      document.visibilityState === "visible" &&
      heartbeatProjectId &&
      heartbeatTaskId
    ) {
      // Page became visible — send an immediate heartbeat
      reviewApi
        .heartbeat(heartbeatProjectId, heartbeatTaskId)
        .catch(console.warn);
    }
  }

  function startHeartbeat(projectId: string, taskId: string) {
    stopHeartbeat();
    if (!projectId || !taskId) return;

    heartbeatProjectId = projectId;
    heartbeatTaskId = taskId;

    heartbeatTimer = window.setInterval(async () => {
      try {
        await reviewApi.heartbeat(projectId, taskId);
      } catch (err) {
        console.warn("[startHeartbeat] Heartbeat failed:", err);
        // Don't stop - the task might still be running and we want to keep trying
      }
    }, HEARTBEAT_INTERVAL);

    // Also send immediately
    reviewApi.heartbeat(projectId, taskId).catch(console.warn);

    // Listen for visibility changes to send heartbeat on tab restore
    document.addEventListener("visibilitychange", handleVisibilityChange);
  }

  function stopHeartbeat() {
    if (heartbeatTimer !== null) {
      clearInterval(heartbeatTimer);
      heartbeatTimer = null;
    }
    heartbeatProjectId = null;
    heartbeatTaskId = null;
    document.removeEventListener("visibilitychange", handleVisibilityChange);
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
    docParseSSEConnections,
    connectDocParseSSE,
    disconnectDocParseSSE,
    updateDocumentParseProgress,
    reviewTasks,
    selectedTaskId,
    fetchProjects,
    createProject,
    deleteProject,
    selectProject,
    uploadDocument,
    deleteDocument,
    uploadDraftDocument,
    loadDraftDocuments,
    deleteDraftDocument,
    attachDraftDocuments,
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
    startHeartbeat,
    stopHeartbeat,
    $reset,
  };
});
