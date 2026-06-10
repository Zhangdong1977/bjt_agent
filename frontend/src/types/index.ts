// User types
export interface User {
  id: string;
  username: string;
  email: string;
  created_at: string;
  interior_user?: boolean;
  concurrency?: number;
}

export interface Token {
  access_token: string;
  refresh_token?: string;
  token_type: string;
}

// Project types
export interface Project {
  id: string;
  name: string;
  description: string | null;
  user_id: string;
  created_at: string;
  updated_at: string;
}

export interface CreateProjectRequest {
  name: string;
  description?: string;
}

// Document parse progress (from SSE events)
export interface ParseProgress {
  stage: string;
  processed: number;
  total: number;
  etaSeconds: number;
}

// Document types
export interface Document {
  id: string;
  project_id: string;
  doc_type: "tender" | "bid";
  original_filename: string;
  file_path: string;
  parsed_md_path: string | null;
  parsed_images_dir: string | null;
  page_count: number | null;
  word_count: number | null;
  status: "pending" | "parsing" | "parsed" | "failed";
  parse_error: string | null;
  parse_progress?: ParseProgress;
  created_at: string;
}

export interface DocumentContent {
  content: string;
  images: string[];
  format: "markdown" | "html";
}

// Review types
export interface ReviewTask {
  id: string;
  project_id: string;
  status: "pending" | "running" | "completed" | "failed" | "cancelled";
  started_at: string | null;
  completed_at: string | null;
  duration_seconds: number | null;
  error_message: string | null;
  created_at: string;
}

export interface ReviewTaskListItem {
  id: string;
  project_id: string;
  status: "pending" | "running" | "completed" | "failed" | "cancelled";
  started_at: string | null;
  completed_at: string | null;
  duration_seconds: number | null;
  error_message: string | null;
  created_at: string;
}

export interface ReviewResult {
  id: string;
  task_id: string;
  requirement_key: string;
  requirement_content: string;
  bid_content: string;
  is_compliant: boolean;
  severity: "critical" | "major" | "minor";
  location_page: number | null;
  location_line: number | null;
  suggestion: string | null;
  explanation: string | null;
  rule_doc_name: string | null;
  check_item_name: string | null;
  created_at: string;
}

export interface ReviewSummary {
  category_count: number;
  check_item_count: number;
  risk_item_count: number;
}

export interface ReviewResponse {
  summary: ReviewSummary;
  findings: ReviewResult[];
}

export interface AgentStep {
  id: string;
  task_id: string;
  step_number: number;
  step_type: "thought" | "tool" | "observation";
  content: string;
  tool_name: string | null;
  tool_args?: Record<string, any>;
  tool_result?: Record<string, any>;
  created_at: string;
}

// TodoItem type - represents a sub-agent execution unit
export interface TodoItem {
  id: string;
  project_id: string;
  session_id: string;
  rule_doc_path: string;
  rule_doc_name: string;
  check_items: Array<{ id: string; title: string }> | null;
  status: "pending" | "running" | "completed" | "failed";
  result: { findings: ReviewResult[] } | null;
  error_message: string | null;
  retry_count: number;
  max_retries: number;
  max_steps: number;
  brain_capacity: number;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
}

// Tool Result type
export interface ToolResult {
  status: "success" | "error";
  content?: string;
  error?: string;
  count?: number;
  data?: any;
}

// SSE Event types
export interface SSEEvent {
  type:
    | "status"
    | "progress"
    | "step"
    | "complete"
    | "error"
    | "merging"
    | "merged"
    | "master_started"
    | "master_scan_completed"
    | "todo_created"
    | "todo_list_completed"
    | "sub_agent_started"
    | "sub_agent_progress"
    | "sub_agent_step"
    | "sub_agent_completed"
    | "sub_agent_failed"
    | "merging_started"
    | "merging_completed";
  task_id: string;
  status?: string;
  message?: string;
  step_number?: number;
  step_type?: string;
  tool_name?: string;
  tool_args?: Record<string, any>;
  tool_result?: ToolResult;
  // Backend sends flat arrays for step events
  tool_calls?: Array<{ name: string; arguments: Record<string, any> }>;
  tool_results?: Array<{ name: string; result: any }>;
  content?: string;
  findings_count?: number;
  merged_count?: number;
  total_count?: number;
  // Master agent event properties
  total_docs?: number;
  rule_docs?: string[];
  // Todo/sub-agent event properties
  todo_id?: string;
  rule_doc_name?: string;
  check_items?: Array<{ id: string; title: string }>;
  progress?: number;
  current_check?: string;
  error?: string;
}

// Upload types
export interface UploadProgress {
  loaded: number;
  total: number;
  percent: number;
}

// Feedback types
export interface FeedbackResponse {
  id: string;
  finding_id: string;
  user_id: string;
  project_id: string;
  task_id: string;
  feedback_type: "confirm" | "contradict" | "refine";
  contradict_reason: string | null;
  corrected_severity: string | null;
  corrected_suggestion: string | null;
  corrected_is_compliant: boolean | null;
  comment: string | null;
  status: "pending" | "accepted" | "rejected" | "superseded";
  reviewed_by: string | null;
  reviewed_at: string | null;
  affected_skill_id: string | null;
  confidence_delta: number;
  batch_id: string | null;
  rule_doc_name: string | null;
  created_at: string;
}

export interface FeedbackSummary {
  total_feedback: number;
  by_type: Record<string, number>;
  by_status: Record<string, number>;
  agreement_rate: number;
  top_contradicted_rules: Array<{ rule_doc_name: string; count: number }>;
}

export interface BatchFeedbackResponse {
  created_count: number;
  superseded_count: number;
}

export interface BatchFeedbackReviewResponse {
  reviewed_count: number;
  action: string;
}

export interface FeedbackCreateRequest {
  feedback_type: "confirm" | "contradict" | "refine";
  contradict_reason?:
    | "should_comply"
    | "severity_too_high"
    | "severity_too_low"
    | "item_not_applicable";
  corrected_severity?: "critical" | "major" | "minor";
  corrected_suggestion?: string;
  corrected_is_compliant?: boolean;
  comment?: string;
}

// RAG Search types
export interface RAGSearchResult {
  source: string;
  snippet: string;
  score: number;
}

export interface RAGSearchResponse {
  results: RAGSearchResult[];
  queryTime: number;
  totalResults: number;
}

export interface DocumentContentResponse {
  content: string;
  filename: string;
}

// Project feedback summary for experience dashboard
export interface ProjectFeedbackSummary {
  project_id: string;
  project_name: string;
  user_id: string;
  username: string;
  total_feedback: number;
  reviewed_feedback: number;
  unreviewed_feedback: number;
  created_at: string;
}

// Paginated response wrapper for project summary list
export interface PaginatedProjectSummary {
  items: ProjectFeedbackSummary[];
  total: number;
  limit: number;
  offset: number;
}
