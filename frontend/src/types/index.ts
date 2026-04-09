// User types
export interface User {
  id: string
  username: string
  email: string
  created_at: string
}

export interface Token {
  access_token: string
  refresh_token?: string
  token_type: string
}

// Project types
export interface Project {
  id: string
  name: string
  description: string | null
  user_id: string
  created_at: string
  updated_at: string
}

export interface CreateProjectRequest {
  name: string
  description?: string
}

// Document types
export interface Document {
  id: string
  project_id: string
  doc_type: 'tender' | 'bid'
  original_filename: string
  file_path: string
  parsed_md_path: string | null
  parsed_images_dir: string | null
  page_count: number | null
  word_count: number | null
  status: 'pending' | 'parsing' | 'parsed' | 'failed'
  parse_error: string | null
  parse_progress?: {
    stage: string
    processed: number
    total: number
    etaSeconds: number
  }
  created_at: string
}

export interface DocumentContent {
  html_content: string
  images: string[]
}

// Review types
export interface ReviewTask {
  id: string
  project_id: string
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'
  started_at: string | null
  completed_at: string | null
  error_message: string | null
  created_at: string
}

export interface ReviewTaskListItem {
  id: string
  project_id: string
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'
  started_at: string | null
  completed_at: string | null
  created_at: string
}

export interface ReviewResult {
  id: string
  task_id: string
  requirement_key: string
  requirement_content: string
  bid_content: string
  is_compliant: boolean
  severity: 'critical' | 'major' | 'minor'
  location_page: number | null
  location_line: number | null
  suggestion: string | null
  explanation: string | null
  created_at: string
}

export interface ReviewSummary {
  total_requirements: number
  compliant: number
  non_compliant: number
  critical: number
  major: number
  minor: number
}

export interface ReviewResponse {
  summary: ReviewSummary
  findings: ReviewResult[]
}

export interface AgentStep {
  id: string
  task_id: string
  step_number: number
  step_type: 'thought' | 'tool' | 'observation'
  content: string
  tool_name: string | null
  tool_args?: Record<string, any>
  tool_result?: Record<string, any>
  created_at: string
}

// Tool Result type
export interface ToolResult {
  status: 'success' | 'error'
  content?: string
  error?: string
  count?: number
  data?: any
}

// SSE Event types
export interface SSEEvent {
  type: 'status' | 'progress' | 'step' | 'complete' | 'error' | 'merging' | 'merged'
  | 'master_started' | 'master_scan_completed'
  | 'todo_created' | 'todo_list_completed'
  | 'sub_agent_started' | 'sub_agent_progress' | 'sub_agent_completed' | 'sub_agent_failed'
  | 'merging_started' | 'merging_completed'
  task_id: string
  status?: string
  message?: string
  step_number?: number
  step_type?: string
  tool_name?: string
  tool_args?: Record<string, any>
  tool_result?: ToolResult
  // Backend sends flat arrays for step events
  tool_calls?: Array<{ name: string; arguments: Record<string, any> }>
  tool_results?: Array<{ name: string; result: any }>
  content?: string
  findings_count?: number
  merged_count?: number
  total_count?: number
  // Master agent event properties
  total_docs?: number
  rule_docs?: string[]
  // Todo/sub-agent event properties
  todo_id?: string
  rule_doc_name?: string
  check_items?: Array<{ id: string; title: string }>
  progress?: number
  current_check?: string
  error?: string
}

// Upload types
export interface UploadProgress {
  loaded: number
  total: number
  percent: number
}

// RAG Search types
export interface RAGSearchResult {
  source: string
  snippet: string
  score: number
}

export interface RAGSearchResponse {
  results: RAGSearchResult[]
  queryTime: number
  totalResults: number
}

export interface DocumentContentResponse {
  content: string
  filename: string
}
