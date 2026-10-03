export type Role = "customer" | "agent" | "admin";

export interface User {
  id: string;
  email: string;
  name: string;
  role: Role;
  region?: string | null;
  csrf_token?: string;
}

export interface Area {
  id: string;
  label: string;
  icon: string;
  issues: number;
}

export interface Catalog {
  areas: Area[];
  issues: { intent: string; label: string; area: string }[];
  known_incidents: { id: string; title: string; region: string; public_note: string }[];
  taxonomy_version: number;
}

export interface QuestionOption {
  id: string;
  label: string;
  neutral?: boolean;
}

export interface Question {
  id: string;
  type: "single" | "multi" | "text";
  kind: string;
  text: string;
  options: QuestionOption[];
  generated?: boolean;
  expected_gain?: number;
}

export interface IntakeAnswer {
  question_id: string;
  question: string;
  answer: string[] | null;
  text: string | null;
  information_gain_bits: number;
  generated: boolean;
}

export interface IntakeState {
  session_id: string;
  complaint: string;
  next_question: Question | null;
  done: boolean;
  candidates: { intent: string; label: string; probability: number }[];
  entropy_bits: number;
  answers: IntakeAnswer[];
  top_similarity: number;
  chose_other: boolean;
}

export interface Step {
  id: string;
  position: number;
  text: string;
  detail?: string | null;
  status: "pending" | "worked" | "did_not_work";
  status_note?: string | null;
  origin: "ai" | "agent";
  citations: string[];
  plan_version: number;
  customer_visible?: boolean;
}

export interface Message {
  id: string;
  author_role: "customer" | "agent" | "ai" | "system";
  body: string;
  options?: string[] | null;
  answered_option?: string | null;
  step_id?: string | null;
  created_at: string;
  visibility?: "public" | "internal";
  author_id?: string;
}

export interface TicketEvent {
  kind: string;
  actor_role: string;
  detail: Record<string, unknown> | null;
  created_at: string;
}

export interface CustomerTicket {
  id: string;
  subject: string;
  complaint: string;
  status: string;
  status_label: string;
  route: "self_service" | "assisted" | "human" | null;
  created_at: string;
  updated_at: string;
  resolved_at?: string | null;
  reopen_count: number;
  analysis_state: string;
  region?: string | null;
  issue: { label: string; area: string; severity: string; product: string } | null;
  why?: string | null;
  recurrence?: number | null;
  sla_due_at?: string | null;
  steps: Step[];
  messages: Message[];
  events: TicketEvent[];
  feedback?: { rating: number; comment: string } | null;
  assignee?: string | null;
  incident?: { id: string; title: string; status: string; public_note: string } | null;
  resolution?: { title: string; root_cause: string; resolution_steps: string[] } | null;
}

export interface TicketRow {
  id: string;
  subject: string;
  status: string;
  status_label: string;
  route: string | null;
  severity: string | null;
  intent_label?: string | null;
  created_at: string;
  updated_at: string;
  analysis_state: string;
  customer_email?: string;
  sla_hours_left?: number;
  reopen_count?: number;
  churn_risk?: boolean;
  incident_id?: string | null;
  confidence?: number | null;
  assignee_id?: string | null;
  region?: string | null;
}

export interface Source {
  id: string;
  kind: "ticket" | "kb";
  title: string;
  snippet: string;
  similarity: number | null;
  rerank?: number | null;
  rrf?: number;
  intent?: string;
  audience?: string;
  root_cause?: string | null;
  steps?: string[];
  origin?: string;
  outcome_score?: number | null;
}

export interface Triage {
  intent: string;
  intent_label: string;
  category: string;
  product: string;
  severity: string;
  severity_drivers: string[];
  sentiment: string;
  sentiment_score: number;
  emotions: string[];
  churn_risk: boolean;
  language: string;
  confidence: number;
  llm_confidence: number;
  knn_agreement: number;
  clarify_agreement: number | null;
  entities: { time_pattern?: string | null; actions_tried: string[]; device?: string | null; error_code?: string | null; impact?: string | null };
  evidence: { intent: string; severity: string; sentiment: string };
  notes: string[];
  rules_applied?: string[];
  prompt_injection?: boolean;
  taxonomy_version: number;
}

export interface Decision {
  route: string;
  reasons: string[];
  recurrence: number;
  decision_confidence: number;
  top_similarity: number;
  customer_reason: string;
  warnings?: string[];
  degraded?: string[];
  latency_ms?: Record<string, number | Record<string, number>>;
  models?: Record<string, unknown>;
  draft_meta?: {
    abstain?: boolean;
    abstain_reason?: string;
    probable_root_cause?: { text: string; citations: string[] } | null;
    escalate_if?: string[];
    citation_coverage?: number;
    citation_validity?: number;
    confidence?: number;
  };
}

export interface Copilot {
  summary: string;
  likely_root_causes: { text: string; likelihood: string; citations: string[] }[];
  next_actions: { text: string; owner: string; citations: string[] }[];
  clarifying_questions: { text: string; options: string[] }[];
  customer_reply_draft: string;
  risk_flags: string[];
  similar_incidents: { id: string; title: string; similarity: number | null; root_cause?: string | null; steps: string[]; outcome_score?: number | null; origin?: string }[];
  sources?: Source[];
  generated_at?: string;
  degraded?: boolean;
  model?: string;
}

export interface AgentTicket {
  id: string;
  subject: string;
  complaint: string;
  complaint_redacted: string;
  status: string;
  status_label: string;
  route: string | null;
  severity: string | null;
  intent: string | null;
  region: string | null;
  reopen_count: number;
  analysis_state: string;
  trace_id: string | null;
  created_at: string;
  updated_at: string;
  sla_due_at: string | null;
  triage: Triage | null;
  sources: Source[] | null;
  decision: Decision | null;
  copilot: Copilot | null;
  intake: { answers: IntakeAnswer[]; facts: Record<string, unknown>; posterior: Record<string, number> } | null;
  resolution_summary: Record<string, unknown> | null;
  steps: Step[];
  messages: Message[];
  events: (TicketEvent & { actor_id: string })[];
  feedback: { rating: number; comment: string } | null;
  customer: { email: string; name: string } | null;
  assignee: { email: string; name: string } | null;
  incident: { id: string; title: string; status: string } | null;
  assignee_id: string | null;
}
