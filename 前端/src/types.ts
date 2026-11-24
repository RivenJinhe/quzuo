/** JSON contracts shared by the product and the API testbench. */
export type TaskType = 'review' | 'travel' | 'activity'

export interface Account {
  id: string
  username: string
  created_at: string
}
export interface TrialStatus {
  trial_id: string
  expires_at: string
  purge_at: string
  writable: boolean
  readable?: boolean
  days_left: number
}
export interface AuthStatus {
  logged_in: boolean
  account: Account | null
  trial: TrialStatus | null
}

export interface Page<T> {
  items: T[]
  next_cursor: string | null
}
export interface Topic {
  topic: string
  count: number
}
export interface Material {
  id: string
  title: string
  topic: string
  excerpt: string
  /** 正文字数。列表接口只给开头一段摘要，字数看这个字段。 */
  chars?: number
  source_kind: 'web' | 'courseware' | 'manual'
  source_file: string | null
  source_url: string | null
  source_page_start: number | null
  source_page_end: number | null
  note: string | null
  status: string
  version: number
  created_at: string
  updated_at: string
}
export interface ExtractedMaterial {
  source_file: string
  kind: string
  format: string
  page_count: number
  page_start: number
  page_end: number
  chars: number
  excerpt: string
  markdown: string
  truncated: boolean
  suggested_title: string
  pages: { page: number; chars: number }[]
}
export interface Candidate {
  material_id: string
  title: string
  topic: string
  status: string
  usable: boolean
  reason: string
  unusable_reason?: string | null
  preselected?: boolean
  score?: number
}
export interface Model {
  id: string
  provider: string
  provider_label: string
  model: string
  available: boolean
  default: boolean
  reason: string
}
export interface ModelCatalog {
  default: string
  items: Model[]
  compare_limit: number
}

interface ConditionChange {
  field: string
  from: unknown
  to: unknown
  label?: string
}
interface Turn {
  at: string
  version: number
  text: string
  summary: string
  applied: ConditionChange[]
  unresolved: string[]
}
export interface Conversation {
  id: string
  goal: string
  task_type: TaskType
  material_ids: string[]
  conditions: Record<string, unknown>
  retained: Record<string, unknown>
  status: string
  version: number
  turns: Turn[]
  created_at: string
  updated_at: string
}
export interface RetrievedMaterials {
  candidates: Candidate[]
  total: number
  material_limit: number
}
export interface ConversationCreated extends RetrievedMaterials {
  conversation: Conversation
}
export interface GenerationComparison {
  generation_id: string
  model_id: string
  status: string
  poll_url: string
}
export interface GenerationSubmission {
  conversation_id: string
  conversation_version: number
  status: string
  poll_interval_seconds: number
  id?: string
  model_id?: string
  poll_url?: string
  comparisons?: GenerationComparison[]
}
export interface TurnResponse {
  conversation: Conversation
  turn: Turn
  summary: string
  applied: ConditionChange[]
  unresolved: string[]
  generation: GenerationSubmission | null
  comparisons?: GenerationComparison[]
}

export interface Citation {
  id: string
  generation_id: string
  material_id: string | null
  material_title: string | null
  claim_text: string
  excerpt_snapshot: string
  source_kind: string
  source_url: string | null
  source_file: string | null
  source_page: string | number | null
  source_page_start: number | null
  source_page_end: number | null
  jump_url: string | null
}
export interface GenerationResult {
  [key: string]: unknown
  headline?: string
  items?: {
    title: string
    minutes?: number | null
    detail?: string
    must_keep?: boolean
    citations?: { citation_id?: string; id?: string; material_id?: string; page?: string | number }[]
  }[]
  timeline?: { start?: string; arrive?: string; leave?: string; end?: string; deadline?: string }
  budget?: Record<string, unknown>
  pending_facts?: string[]
  private_note?: string
  rationale?: string
}
export interface Generation {
  id: string
  conversation_id: string
  conversation_version: number
  status: string
  created_at: string
  finished_at: string | null
  error_code: string | null
  error_message: string | null
  block_reason: string | null
  result: GenerationResult | null
  changes: { kind: string; label: string }[] | null
  options: { label?: string; send?: string; effect?: string }[] | null
  result_sections: { title: string; lines: string[]; sources?: { id: string; label: string }[] }[]
  validation: {
    block?: { message?: string }
    checks?: { code: string; level?: string; ok: boolean; detail?: unknown }[]
    notices?: { code: string; message?: string; detail?: unknown }[]
    corrections?: { code: string; material?: string; to?: unknown }[]
  } | null
  usage: { total_tokens?: number; elapsed_ms?: number; cost_cny?: number | null; model?: string } | null
}
export interface Plan {
  id: string
  conversation_id: string
  generation_id: string | null
  task_type: TaskType
  title: string
  body: string
  body_private_note: string | null
  rationale: string | null
  confirmed_conditions: Record<string, unknown>
  citation_ids: string[]
  version: number
  created_at: string
  updated_at: string
}
export interface PublicPlan {
  title: string
  text: string
}
