// User types
export interface User {
  id: string;
  username: string;
  email: string;
  created_at: string;
  nickname?: string | null;
  city?: string | null;
  company?: string | null;
  bidding_industries?: string | null;
  interior_user?: boolean;
  concurrency?: number;
}

export interface Token {
  access_token: string;
  refresh_token?: string;
  token_type: string;
}

export interface Captcha {
  captcha_id: string;
  image: string;
  expires_in: number;
}

export interface Wallet {
  balance_wen: number;
  points: number;
  recharge_balance_points: number;
  gift_balance_points: number;
  total_balance_points: number;
  low_balance_threshold: number;
  low_balance: boolean;
}

export interface RechargePackage {
  code: string;
  name: string;
  amount_cents: number;
  balance_wen: number;
  caution?: string | null;
  icon_url?: string | null;
  recharge_points: number;
  gift_points: number;
  total_points: number;
  validity_months: number;
  loyalty_deduction_limit?: number | null;
}

export interface Coupon {
  id: number;
  code?: string | null;
  amount_cents: number;
  amount_yuan: number;
  valid_until?: string | null;
  status: string;
  raw_status?: number | null;
  product_type: "plugin" | "check" | "generate";
  benefit_type: "cash" | "gift";
  threshold_amount_cents: number;
  gift_points: number;
}

export interface CouponRedeemResponse {
  coupon?: Coupon | null;
  coupons: Coupon[];
}

export interface OrderPreviewRequest {
  package_code: string;
  coupon_id?: number | null;
  use_points: number;
}

export interface OrderPreview {
  package_code: string;
  product_name: string;
  order_amount_cents: number;
  coupon_amount_cents: number;
  points_used: number;
  points_amount_cents: number;
  actual_payment_cents: number;
  package_balance_wen: number;
  current_balance_wen: number;
  current_points: number;
  recharge_points: number;
  gift_points: number;
  total_points: number;
  validity_months: number;
  loyalty_deduction_limit?: number | null;
  current_recharge_points: number;
  current_gift_points: number;
  coupon_benefit_type?: "cash" | "gift" | null;
  coupon_gift_points: number;
}

export interface BillingOrder {
  id: string;
  order_no: string;
  source: "recharge" | "gift";
  product_name: string;
  created_at: string;
  status: string;
  order_amount_cents: number;
  actual_payment_cents: number;
  coupon_code?: string | null;
  coupon_amount_cents: number;
  points_used: number;
  points_amount_cents: number;
  expires_at: string;
  paid_at?: string | null;
  balance_after_wen?: number | null;
  current_balance_wen?: number | null;
  username?: string | null;
  enterprise_name?: string | null;
  recharge_points: number;
  gift_points: number;
  total_points: number;
  recharge_balance_after?: number | null;
  gift_balance_after?: number | null;
  unit_value_yuan?: number | null;
  validity_months: number;
  coupon_benefit_type?: "cash" | "gift" | null;
  coupon_gift_points: number;
  consumed_points: number;
  remaining_points: number;
  points_expires_at?: string | null;
  points_status: "active" | "expired" | "exhausted" | "not_credited";
}

export interface ConsumptionRecord {
  id: string;
  consumed_at: string;
  project_name: string;
  consumed_wen: number;
  earned_points: number;
  used_by: string;
  cost_cny?: number | null;
  username?: string | null;
  enterprise_name?: string | null;
  cost_points?: number | null;
  sales_multiplier?: number | null;
  sales_points?: number | null;
  gift_points_used: number;
  recharge_points_used: number;
  // 消费前/后的充值/赠送点数余额
  recharge_balance_before?: number | null;
  gift_balance_before?: number | null;
  recharge_balance_after?: number | null;
  gift_balance_after?: number | null;
  // 本次扣点消耗的充值订单编号（多张以 ", " 连接）
  settlement_order_nos?: string | null;
  weighted_unit_value_yuan?: number | null;
  folded_income_yuan?: number | null;
  profit_yuan?: number | null;
  profit_margin?: number | null;
}

export interface ConsumptionAllocation {
  id: string;
  lot_id?: string | null;
  lot_type: "recharge" | "gift";
  source_type?: string | null;
  source_id?: string | null;
  points: number;
  unit_value_yuan: number;
  folded_income_yuan: number;
  expires_at?: string | null;
}

export interface PaymentQr {
  order_id: string;
  order_no: string;
  actual_payment_cents: number;
  qr_payload: string;
  expires_at: string;
}

export interface OrderStatus {
  order_id: string;
  order_no: string;
  status: string;
  paid_at?: string | null;
  balance_after_wen?: number | null;
}

export interface ProfileUpdateRequest {
  nickname?: string | null;
  city?: string | null;
  company?: string | null;
  bidding_industries?: string | null;
}

// Project types
export interface Project {
  id: string;
  name: string;
  description: string | null;
  project_type: "review" | "duplicate" | "bid_draft";
  duplicate_mode?: "pair" | "batch";
  user_id: string;
  status: string;
  is_deleted: boolean;
  deleted_at: string | null;
  deleted_by_user_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface CreateProjectRequest {
  name: string;
  description?: string;
  project_type?: "review" | "duplicate" | "bid_draft";
  duplicate_mode?: "pair" | "batch";
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
  project_id: string | null;
  owner_user_id: string | null;
  doc_type: DocumentType;
  original_filename: string;
  file_path: string;
  // The backend uses the explicit parsed_markdown_path name; keep the legacy
  // alias for older responses during the migration window.
  parsed_markdown_path?: string | null;
  parsed_md_path?: string | null;
  parsed_html_path?: string | null;
  parsed_images_dir: string | null;
  parser_name?: string | null;
  parser_version?: string | null;
  coverage_summary?: DocumentCoverageSummary | null;
  source_version?: string | null;
  source_snapshot_hash?: string | null;
  source_uri?: string | null;
  source_published_at?: string | null;
  source_metadata?: Record<string, unknown> | null;
  duplicate_party_key?: string | null;
  duplicate_display_name?: string | null;
  duplicate_ordinal?: number | null;
  page_count: number | null;
  word_count: number | null;
  status: "pending" | "parsing" | "parsed" | "failed";
  parse_error: string | null;
  parse_progress?: ParseProgress;
  created_at: string;
}

export type CoverageStatus = 'complete' | 'partial' | 'insufficient'

export interface DocumentCoverageSummary {
  status: CoverageStatus;
  pages_total: number | null;
  pages_parsed: number | null;
  page_ratio: number | null;
  text_units: number;
  text_covered_units: number;
  text_ratio: number;
  table_count: number;
  structured_table_count: number;
  table_ratio: number;
  image_count: number;
  hashed_image_count: number;
  ocr_image_count: number;
  image_hash_ratio: number;
  image_ocr_ratio: number;
  scanned_page_count?: number;
  ocr_page_count?: number;
  failed_ocr_page_count?: number;
  unresolved_objects: number;
  warnings: string[];
}

export interface DocumentArtifactBlock {
  block_id: string;
  document_id: string;
  document_role: string | null;
  content_type: string;
  section_path: string[];
  page_number: number | null;
  start_line: number | null;
  end_line: number | null;
  raw_text: string;
  normalized_hash: string;
  numbers: string[];
  models: string[];
  units: string[];
  dates: string[];
  identifiers: string[];
  table_id: string | null;
  row_index: number | null;
  header_map: Record<string, string> | null;
  image_path: string | null;
  image_sha256: string | null;
  perceptual_hash?: string | null;
  ocr_confidence: number | null;
  image_width?: number | null;
  image_height?: number | null;
  parent_block_id?: string | null;
  ocr_provider?: string | null;
  ocr_error?: string | null;
  vision_description?: string | null;
  parser_name: string;
  parser_version: string;
  artifact_hash: string;
  source_basis: string;
}

export interface DocumentArtifactManifest {
  schema_version: string;
  document_id: string;
  document_role: string | null;
  generated_at: string;
  source: {
    name: string;
    sha256: string | null;
    size_bytes: number | null;
    available: boolean;
  };
  artifacts: Record<string, {
    name: string;
    sha256: string | null;
    size_bytes: number | null;
    available: boolean;
  }>;
  parser_name: string;
  parser_version: string;
  evidence_block_count: number;
  counts: Record<string, number>;
  coverage: DocumentCoverageSummary;
  warnings: string[];
}

export interface DocumentArtifactsResponse {
  document_id: string;
  manifest: DocumentArtifactManifest | null;
  coverage: DocumentCoverageSummary | null;
  blocks: DocumentArtifactBlock[];
  block_count: number;
  truncated: boolean;
}

export type DocumentType =
  | "tender"
  | "bid"
  | "duplicate_left"
  | "duplicate_right"
  | "duplicate_bid"
  | "duplicate_tender"
  | "duplicate_public_reference";

export interface DocumentContent {
  content: string;
  images: string[];
  format: "markdown" | "html";
}

// Review types
export interface RuleDocInfo {
  /** 规则文档文件名（含 .md），发起检查时按此回传 */
  name: string;
  /** 展示名（去扩展名），如 "A001 检查投标文件填写完整性" */
  stem: string;
  /** 弹窗默认是否勾选（当前除 E001 签字盖章检查外默认全选） */
  default_selected: boolean;
}

export interface ReviewTask {
  id: string;
  project_id: string;
  task_type?: "review" | "duplicate";
  duplicate_mode?: "pair" | "batch";
  duplicate_algorithm_version?: string | null;
  duplicate_feature_snapshot?: Record<string, any> | null;
  selected_rule_docs?: string[] | null;
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
  task_type?: "review" | "duplicate";
  duplicate_mode?: "pair" | "batch";
  duplicate_algorithm_version?: string | null;
  status: "pending" | "running" | "completed" | "failed" | "cancelled";
  started_at: string | null;
  completed_at: string | null;
  duration_seconds: number | null;
  error_message: string | null;
  created_at: string;
}

export type DuplicateVerdict = "reasonable" | "suspicious" | "unknown";
export type DuplicateSourceBasis = "tender" | "public" | "bidder_authored" | "unknown";

export interface DuplicateResult {
  id: string;
  task_id: string;
  todo_id: string | null;
  rule_doc_name: string;
  check_item_name: string;
  verdict: DuplicateVerdict;
  source_basis: DuplicateSourceBasis;
  similarity_score: number;
  confidence?: number | null;
  coverage_status?: CoverageStatus;
  channel_scores?: Record<string, number> | null;
  match_type: string;
  left_document_id: string;
  left_filename: string | null;
  left_excerpt: string;
  left_location: Record<string, any>;
  right_document_id: string;
  right_filename: string | null;
  right_excerpt: string;
  right_location: Record<string, any>;
  explanation: string;
  suggestion: string | null;
  evidence: Record<string, any> | null;
  created_at: string;
}

export interface DuplicateTodoItem
  extends Omit<TodoItem, "rule_doc_path" | "result"> {
  result: { findings: DuplicateResult[] } | null;
}

export interface DuplicateResultsResponse {
  summary: {
    rule_count: number;
    completed_rule_count: number;
    reasonable_count: number;
    suspicious_count: number;
    unknown_count: number;
    coverage_status?: CoverageStatus;
    coverage_warnings?: string[];
  };
  findings: DuplicateResult[];
  todos: DuplicateTodoItem[];
}

export interface DuplicateDocumentMember {
  task_id: string;
  document_id: string;
  party_key: string;
  display_name: string;
  ordinal: number;
  metadata?: Record<string, any> | null;
  filename?: string | null;
  status?: string | null;
  coverage_status?: CoverageStatus | null;
}

export interface DuplicatePairSummary {
  id: string;
  task_id: string;
  left_document_id: string;
  right_document_id: string;
  left_display_name?: string | null;
  right_display_name?: string | null;
  candidate_count: number;
  finding_count: number;
  suspicious_count: number;
  unknown_count: number;
  max_evidence_strength: number | null;
  coverage_status: CoverageStatus;
  channel_hits: Record<string, number> | null;
}

export interface DuplicateEvidenceOccurrence {
  id: string;
  task_id: string;
  finding_id: string | null;
  cluster_id: string | null;
  document_id: string;
  filename?: string | null;
  display_name?: string | null;
  block_id: string | null;
  excerpt: string;
  location: Record<string, any>;
  channel: string | null;
}

export interface DuplicateEvidenceCluster {
  id: string;
  task_id: string;
  finding_id: string | null;
  cluster_key: string;
  content_type: string;
  document_ids: string[];
  occurrence_count: number;
  representative_excerpt: string;
  evidence_strength: number | null;
  coverage_status: CoverageStatus;
  metadata: Record<string, any> | null;
  occurrences?: DuplicateEvidenceOccurrence[];
}

export interface DuplicateMatrixResponse {
  task_id: string;
  mode: "pair" | "batch";
  coverage_status: CoverageStatus;
  coverage_warnings: string[];
  members: DuplicateDocumentMember[];
  pairs: DuplicatePairSummary[];
}

export interface DuplicateSourceEvidence {
  source_reference_id: string;
  source_basis: 'tender' | 'public';
  source_document_id: string;
  source_filename: string;
  source_block_id: string;
  source_excerpt: string;
  source_location: Record<string, unknown>;
  source_snapshot_hash: string;
  source_version: string;
  source_uri?: string | null;
  retrieval_score: number;
}

export interface DuplicateTableComparison {
  table_candidate_id: string;
  score: number;
  header_similarity: number;
  row_alignment_score: number;
  numeric_signature_score: number;
  rare_cell_overlap: number;
  table_structure_score: number;
  shared_rare_cells: string[];
  left: Record<string, any>;
  right: Record<string, any>;
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

// ---- 总体报告（报告生成 Agent 汇总各子 agent 输出，结构对应后端 report_agent.py） ----

export interface OverallRiskSectionEntry {
  rule_doc: string;
  rule_doc_code: string;
  count: number;
  summary: string;
  rejection_related?: boolean; // 仅 critical 节：是否涉及废标条款
}

export interface OverallScoreItem {
  code: string;
  name: string;
  full_score: number | null;
  estimated_score: number | null;
  note: string;
}

export interface OverallReport {
  schema_version: number;
  generated_at: string;
  degraded: boolean; // true = LLM 精简失败，描述为原文摘录
  summary: {
    category_count: number;
    check_item_count: number;
    risk_item_count: number;
    severity_dist: { critical: number; major: number; minor: number };
    failed_categories?: string[];
  };
  rejection_risk: { level: "高" | "中" | "低"; reason: string };
  risk_sections: {
    critical: OverallRiskSectionEntry[];
    major: OverallRiskSectionEntry[];
    minor: OverallRiskSectionEntry[];
  };
  score_items: OverallScoreItem[];
}

export interface OverallReportResponse {
  task_id: string;
  status: string; // 任务状态：running = 报告生成中；completed = 已定稿
  report: OverallReport | null;
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
    | "sub_agent_step_count"
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
  project_type: string;
  user_id: string;
  username: string;
  total_feedback: number;
  reviewed_feedback: number;
  unreviewed_feedback: number;
  created_at: string;
  is_deleted: boolean;
  has_documents: boolean;
  has_review: boolean;
  review_completed: boolean;
}

// Paginated response wrapper for project summary list
export interface PaginatedProjectSummary {
  items: ProjectFeedbackSummary[];
  total: number;
  limit: number;
  offset: number;
}

// ---------------------------------------------------------------------------
// System announcements (系统公告)
// ---------------------------------------------------------------------------

export type AnnouncementSeverity = "info" | "important" | "urgent";

/** 登录页跑马灯用的公开公告（未登录可读，仅公开展示字段）。 */
export interface PublicAnnouncement {
  id: string;
  title: string;
  content: string;
  severity: AnnouncementSeverity;
  published_at: string;
}

/** 已登录用户视角的公告（带当前用户的已读状态）。 */
export interface Announcement {
  id: string;
  title: string;
  content: string;
  severity: AnnouncementSeverity;
  is_active: boolean;
  published_at: string;
  expires_at: string | null;
  created_by: string | null;
  created_by_name: string | null;
  created_at: string;
  updated_at: string;
  is_read: boolean;
  read_at: string | null;
}

/** 管理端视角（内部用户）：含已读统计，不含个人已读状态。 */
export interface AnnouncementManage {
  id: string;
  title: string;
  content: string;
  severity: AnnouncementSeverity;
  is_active: boolean;
  published_at: string;
  expires_at: string | null;
  created_by: string | null;
  created_by_name: string | null;
  created_at: string;
  updated_at: string;
  read_count: number;
  total_users: number;
}

export interface AnnouncementCreateRequest {
  title: string;
  content: string;
  severity?: AnnouncementSeverity;
  is_active?: boolean;
  published_at?: string | null;
  expires_at?: string | null;
}

export interface AnnouncementUpdateRequest {
  title?: string;
  content?: string;
  severity?: AnnouncementSeverity;
  is_active?: boolean;
  published_at?: string | null;
  expires_at?: string | null;
}

export interface AnnouncementListResponse {
  items: Announcement[];
  total: number;
  unread_count: number;
}

export interface UnreadCountResponse {
  unread_count: number;
}

export interface MarkAllReadResponse {
  marked_count: number;
}

// ---------------------------------------------------------------------------
// System status（系统状态 / 维护模式）
// ---------------------------------------------------------------------------

export interface MaintenancePublic {
  is_enabled: boolean;
  reason: string;
  started_at: string | null;
}

export interface MaintenanceState {
  is_enabled: boolean;
  reason: string;
  started_at: string | null;
  updated_by: string | null;
  updated_at: string | null;
}

export interface MaintenanceUpdateRequest {
  enabled: boolean;
  reason?: string;
}

export interface WorkerNode {
  name: string;
  node: string;
  role: "review" | "parser" | "standalone";
  index: number | null;
  alive: boolean;
  active_review_tasks: number;
  active_parser_tasks: number;
  processed: number;
  uptime: number | null;
}

export interface ClusterNode {
  name: string;
  label: string;
  roles: string[];
  alive_workers: number;
  total_workers: number;
  active_review_tasks: number;
  active_parser_tasks: number;
  processed: number;
  is_online: boolean;
}

export interface QueueDepths {
  review: number | null;
  parser: number | null;
}

export interface SystemOverview {
  running_reviews: number;
  parsing_documents: number;
  review_queue: number | null;
  parser_queue: number | null;
  alive_workers: number;
  total_workers: number;
  degraded: boolean;
}

export interface SystemStatus {
  maintenance: MaintenanceState;
  overview: SystemOverview;
  nodes: ClusterNode[];
  workers: WorkerNode[];
  queue_depths: QueueDepths;
}

