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
  stream?: boolean;
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
  status: "active" | "retired" | "staging" | "pending";
  size?: string;
  contextWindow?: string;
  license?: string;
  endpoint?: string;
  api_key_set?: boolean;
  notes?: string | null;
  /** Present only on GET /portal/chat/models — true if the caller's key(s) are entitled to this model. */
  entitled?: boolean;
}

/** Body for POST /portal/models (admin_portal/schemas/models.py::ModelRegisterRequest) */
export interface ModelRegisterReq {
  name: string;
  version: string;
  backend: string;
  endpoint: string;
  tasks: string[];
  status?: "active" | "retired" | "staging";
  api_key?: string | null;
}

export interface MetricsSummary {
  request_rate: number | null;
  error_rate: number | null;
  cache_hit_rate: number | null;
}

/** Mirrors admin_portal/schemas/governance.py::GovernanceSummary */
export interface GovernanceSummary {
  total_events: number;
  by_outcome: Record<string, number>;
  by_layer: Record<string, number>;
  requests_blocked_total: number;
  blocked_by_reason: Record<string, number>;
  injection_flagged_total: number;
  pii_detections_total: number;
  token_usage: {
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
  };
  model_usage: Record<string, number>;
}

export interface PortalConfig {
  grafana_url: string;
}

/** Mirrors admin_portal/schemas/users.py::UserOut */
export interface User {
  user_id: string;
  username: string;
  email: string | null;
  department: string | null;
  status: "active" | "inactive";
  roles: string[];
  created_at: string;
  updated_at: string;
}

/** Mirrors admin_portal/schemas/roles.py::RoleOut */
export interface Role {
  role_name: string;
  description: string | null;
}

/** Mirrors admin_portal/schemas/roles.py::RolePermissionsOut */
export interface RolePermissions {
  role_name: string;
  permissions: Record<string, boolean>;
}

/** Mirrors admin_portal/schemas/keys.py::ApiKeyOut */
export interface ApiKey {
  key_id: string;
  key_prefix: string;
  label: string | null;
  status: "active" | "revoked" | "expired";
  expires_at: string | null;
  /** Requests/min for this key alone — every key has its own concrete limit,
   *  no platform-wide fallback (see api_gateway/middleware/rate_limit.py). */
  rate_limit_rpm: number;
  created_at: string;
  last_used_at: string | null;
  model_entitlements: string[];
}

/** Mirrors admin_portal/schemas/keys.py::ApiKeyCreated */
export interface ApiKeyCreated extends ApiKey {
  raw_key: string;
}

/** Mirrors admin_portal/schemas/keys.py::ApiKeyWithOwner — GET /portal/keys/ (admin-wide) */
export interface ApiKeyWithOwner extends ApiKey {
  user_id: string;
  owner_username: string;
}

export interface ErrorResponse {
  error: string;
  message: string;
  upstream?: string;
  allowed_values?: string[];
}

/** Mirrors admin_portal/schemas/auth.py::MeResponse — GET /portal/auth/me, POST /portal/auth/login */
export interface Identity {
  user_id: string;
  username: string;
  department: string | null;
  roles: string[];
}

/** Thrown by portalClient when the server returns a non-2xx response. */
export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}
