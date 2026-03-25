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
  created_at: string
}

export interface DocumentContent {
  md_content: string
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
  created_at: string
}

// SSE Event types
export interface SSEEvent {
  type: 'status' | 'progress' | 'step' | 'complete' | 'error'
  task_id: string
  status?: string
  message?: string
  step_number?: number
  step_type?: string
  content?: string
  findings_count?: number
}
