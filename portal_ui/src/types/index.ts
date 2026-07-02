// TypeScript interfaces mirroring the Portal_API Pydantic schemas.
// Keep this file in sync with admin_portal/schemas/*.py

export interface Message {
  role: "system" | "user" | "assistant";
  content: string;
}

export interface ChatReq {
  model: string;
  messages: Message[];
  temperature: number;
}

/** Mirrors AuditEvent Pydantic schema (admin_portal/schemas/audit.py) */
export interface AuditEvent {
  audit_id: string;
  request_id: string;
  timestamp_utc: string;         // ISO-8601
  user_id: string;
  department: string | null;
  model_used: string | null;
  layer: string;
  event_type: string;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  latency_ms: number | null;
  pii_actions: string[];
  policy_decisions: string[];
  outcome: string;               // pass | block | flag | fallback
  error_code: string | null;
}

export interface AuditEventList {
  events: AuditEvent[];
}

export interface ModelRecord {
  name: string;
  version: string;
  backend: string;
  tasks: string[];
  status: "active" | "retired" | "staging";
}

export interface MetricsSummary {
  request_rate: number | null;
  error_rate: number | null;
  cache_hit_rate: number | null;
}

export interface PortalConfig {
  grafana_url: string;
}

export interface ErrorResponse {
  error: string;
  message: string;
  upstream?: string;
  allowed_values?: string[];
}

/** Thrown by portalClient when the server returns a non-2xx response. */
export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}
