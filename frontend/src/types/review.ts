// Review types shared between SubAgentCard and TodoListCard

export interface CheckItem {
  id: string
  title: string
  description?: string
  status?: 'pending' | 'running' | 'completed' | 'failed'
}

export interface TodoItem {
  id: string
  rule_doc_name: string
  check_items: CheckItem[]
  status: 'pending' | 'running' | 'completed' | 'failed'
  result?: {
    findings: any[]
  }
  error_message?: string
}
