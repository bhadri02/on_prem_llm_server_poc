/**
 * portalClient.ts
 *
 * Typed fetch wrapper for all Portal_API calls.
 * All paths are relative so the Vite dev proxy / nginx routes them correctly.
 * Non-2xx responses are converted to ApiError instances.
 */

import {
  ApiError,
  ApiKey,
  ApiKeyCreated,
  ApiKeyWithOwner,
  AuditEventList,
  ChatReq,
  Identity,
  MetricsSummary,
  ModelRecord,
  ModelRegisterReq,
  PortalConfig,
  Role,
  RolePermissions,
  User,
} from "../types";

// ---------------------------------------------------------------------------
// Internal helper
// ---------------------------------------------------------------------------

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let message: string;
    try {
      const body = await res.json() as { message?: string };
      message = body.message ?? JSON.stringify(body);
    } catch {
      message = await res.text();
    }
    throw new ApiError(res.status, message);
  }
  return res.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// Auth (Phase 6 — real login/session for both users and admins)
// ---------------------------------------------------------------------------

/** POST /portal/auth/login — sets an httpOnly session cookie on success. */
export async function login(username: string, password: string): Promise<Identity> {
  const res = await fetch("/portal/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  return handleResponse<Identity>(res);
}

/** POST /portal/auth/logout — clears the session cookie. */
export async function logout(): Promise<void> {
  await fetch("/portal/auth/logout", { method: "POST" });
}

/** GET /portal/auth/me — throws ApiError(401) if not logged in. */
export async function getMe(): Promise<Identity> {
  const res = await fetch("/portal/auth/me");
  return handleResponse<Identity>(res);
}

// ---------------------------------------------------------------------------
// Playground
// ---------------------------------------------------------------------------

/**
 * POST /portal/playground/chat
 *
 * Returns the raw upstream API Gateway response (any JSON shape).
 * Callers should extract `request_id` and the response content themselves.
 */
export async function postChat(req: ChatReq): Promise<unknown> {
  const res = await fetch("/portal/playground/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  return handleResponse<unknown>(res);
}

// ---------------------------------------------------------------------------
// Audit
// ---------------------------------------------------------------------------

export interface AuditQueryParams {
  from?: string;
  to?: string;
  limit?: number;
}

/**
 * Converts a datetime-local string ("YYYY-MM-DDTHH:mm") to a UTC ISO-8601
 * string the audit store accepts ("YYYY-MM-DDTHH:mm:00Z").
 * If the value already has a timezone suffix it is returned unchanged.
 */
function toUtcIso(value: string): string {
  // datetime-local produces exactly 16 chars: "YYYY-MM-DDTHH:mm"
  if (value.length === 16 && !value.endsWith("Z")) {
    return value + ":00Z";
  }
  return value;
}

/**
 * GET /portal/audit/events
 * Accepts optional from/to (ISO-8601) and limit query parameters.
 */
export async function getAuditEvents(params: AuditQueryParams = {}): Promise<AuditEventList> {
  const query = new URLSearchParams();
  if (params.from !== undefined) query.set("from", toUtcIso(params.from));
  if (params.to !== undefined) query.set("to", toUtcIso(params.to));
  if (params.limit !== undefined) query.set("limit", String(params.limit));

  const qs = query.toString();
  const res = await fetch(`/portal/audit/events${qs ? `?${qs}` : ""}`);
  return handleResponse<AuditEventList>(res);
}

/**
 * GET /portal/audit/requests/{requestId}
 * Returns all AuditEvents associated with a single request_id.
 */
export async function getAuditRequest(requestId: string): Promise<AuditEventList> {
  const res = await fetch(`/portal/audit/requests/${encodeURIComponent(requestId)}`);
  return handleResponse<AuditEventList>(res);
}

// ---------------------------------------------------------------------------
// Models
// ---------------------------------------------------------------------------

/**
 * GET /portal/models
 * Proxied through admin portal to Model Registry with auth handled server-side.
 */
export async function getModels(): Promise<{ models: ModelRecord[] }> {
  const res = await fetch("/portal/models");
  // Registry returns a raw array — wrap it in { models: [...] }
  if (!res.ok) {
    let message: string;
    try {
      const body = await res.json() as { message?: string };
      message = body.message ?? JSON.stringify(body);
    } catch {
      message = await res.text();
    }
    throw new ApiError(res.status, message);
  }
  const arr = await res.json() as ModelRecord[];
  return { models: arr };
}

/**
 * PATCH /portal/models/{name}/status
 * Updates the lifecycle status of a registered model.
 */
export async function patchModelStatus(
  name: string,
  status: ModelRecord["status"],
): Promise<ModelRecord> {
  const res = await fetch(`/portal/models/${encodeURIComponent(name)}/status`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status }),
  });
  return handleResponse<ModelRecord>(res);
}

/**
 * POST /portal/models
 * Registers a new model (proxies to the Model Registry). `api_key` is
 * required in practice for non-"ollama" backends; never echoed back.
 */
export async function registerModel(req: ModelRegisterReq): Promise<ModelRecord> {
  const res = await fetch("/portal/models", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  return handleResponse<ModelRecord>(res);
}

/**
 * PATCH /portal/models/{name}/api-key
 * Sets/updates the provider API key used to dispatch to a cloud model.
 */
export async function patchModelApiKey(name: string, apiKey: string): Promise<ModelRecord> {
  const res = await fetch(`/portal/models/${encodeURIComponent(name)}/api-key`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ api_key: apiKey }),
  });
  return handleResponse<ModelRecord>(res);
}

// ---------------------------------------------------------------------------
// Metrics & Config
// ---------------------------------------------------------------------------

/**
 * GET /portal/metrics/summary
 */
export async function getMetricsSummary(): Promise<MetricsSummary> {
  const res = await fetch("/portal/metrics/summary");
  return handleResponse<MetricsSummary>(res);
}

/**
 * GET /portal/config
 * Returns portal runtime configuration, including the Grafana embed URL.
 */
export async function getConfig(): Promise<PortalConfig> {
  const res = await fetch("/portal/config");
  return handleResponse<PortalConfig>(res);
}

// ---------------------------------------------------------------------------
// Chat (Phase 4 — non-streaming Chat view)
// ---------------------------------------------------------------------------

/**
 * GET /portal/chat/models
 * Returns the active model list filtered by the portal's key entitlements.
 */
export async function getChatModels(): Promise<ModelRecord[]> {
  const res = await fetch("/portal/chat/models");
  return handleResponse<ModelRecord[]>(res);
}

/**
 * POST /portal/chat/completions
 * Returns the raw upstream API Gateway response (OpenAI-shaped JSON).
 */
export async function postChatCompletion(req: ChatReq): Promise<unknown> {
  const res = await fetch("/portal/chat/completions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  return handleResponse<unknown>(res);
}

// ---------------------------------------------------------------------------
// Users / Roles / API keys (Phase 3 — RBAC admin management)
// ---------------------------------------------------------------------------

export interface UserCreateReq {
  username: string;
  email?: string | null;
  department?: string | null;
  roles?: string[];
  password?: string | null;
}

/** GET /portal/users/ */
export async function getUsers(): Promise<User[]> {
  const res = await fetch("/portal/users/");
  return handleResponse<User[]>(res);
}

/** POST /portal/users/ */
export async function createUser(req: UserCreateReq): Promise<User> {
  const res = await fetch("/portal/users/", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  return handleResponse<User>(res);
}

/** PATCH /portal/users/{userId}/roles */
export async function patchUserRoles(userId: string, roles: string[]): Promise<User> {
  const res = await fetch(`/portal/users/${encodeURIComponent(userId)}/roles`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ roles }),
  });
  return handleResponse<User>(res);
}

/** PATCH /portal/users/{userId} — status only, for now */
export async function patchUserStatus(userId: string, status: "active" | "inactive"): Promise<User> {
  const res = await fetch(`/portal/users/${encodeURIComponent(userId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status }),
  });
  return handleResponse<User>(res);
}

/** DELETE /portal/users/{userId} — soft-delete (status -> inactive) */
export async function deactivateUser(userId: string): Promise<void> {
  const res = await fetch(`/portal/users/${encodeURIComponent(userId)}`, { method: "DELETE" });
  if (!res.ok) {
    let message: string;
    try {
      const body = (await res.json()) as { message?: string };
      message = body.message ?? JSON.stringify(body);
    } catch {
      message = await res.text();
    }
    throw new ApiError(res.status, message);
  }
}

/** PATCH /portal/users/{userId}/password — admin sets/resets a user's login password */
export async function resetUserPassword(userId: string, password: string): Promise<User> {
  const res = await fetch(`/portal/users/${encodeURIComponent(userId)}/password`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password }),
  });
  return handleResponse<User>(res);
}

/** GET /portal/roles/ */
export async function getRoles(): Promise<Role[]> {
  const res = await fetch("/portal/roles/");
  return handleResponse<Role[]>(res);
}

/** GET /portal/roles/{role}/permissions */
export async function getRolePermissions(role: string): Promise<RolePermissions> {
  const res = await fetch(`/portal/roles/${encodeURIComponent(role)}/permissions`);
  return handleResponse<RolePermissions>(res);
}

/**
 * PATCH /portal/roles/{role}/permissions
 * Persists to the role_permissions table — does NOT take live effect on
 * routing until policy_matrix.yaml is hand-edited and the Router restarted.
 * See docs/FRONTEND_INTEGRATION.md for the full explanation.
 */
export async function patchRolePermissions(
  role: string,
  permissions: Record<string, boolean>,
): Promise<RolePermissions> {
  const res = await fetch(`/portal/roles/${encodeURIComponent(role)}/permissions`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ permissions }),
  });
  return handleResponse<RolePermissions>(res);
}

/** GET /portal/keys/ — admin-wide key listing across all users, owner joined in. */
export async function getAllKeys(): Promise<ApiKeyWithOwner[]> {
  const res = await fetch("/portal/keys/");
  return handleResponse<ApiKeyWithOwner[]>(res);
}

/** POST /portal/users/{userId}/keys — raw key returned once, on creation */
export async function createApiKey(
  userId: string,
  opts: { label?: string; model_entitlements?: string[] } = {},
): Promise<ApiKeyCreated> {
  const res = await fetch(`/portal/users/${encodeURIComponent(userId)}/keys`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ label: opts.label ?? null, model_entitlements: opts.model_entitlements ?? [] }),
  });
  return handleResponse<ApiKeyCreated>(res);
}

/** GET /portal/users/{userId}/keys */
export async function listApiKeys(userId: string): Promise<ApiKey[]> {
  const res = await fetch(`/portal/users/${encodeURIComponent(userId)}/keys`);
  return handleResponse<ApiKey[]>(res);
}

/** DELETE /portal/users/{userId}/keys/{keyId} — revoke */
export async function revokeApiKey(userId: string, keyId: string): Promise<ApiKey> {
  const res = await fetch(
    `/portal/users/${encodeURIComponent(userId)}/keys/${encodeURIComponent(keyId)}`,
    { method: "DELETE" },
  );
  return handleResponse<ApiKey>(res);
}

/** PATCH /portal/users/{userId}/keys/{keyId}/models */
export async function patchKeyEntitlements(
  userId: string,
  keyId: string,
  modelEntitlements: string[],
): Promise<ApiKey> {
  const res = await fetch(
    `/portal/users/${encodeURIComponent(userId)}/keys/${encodeURIComponent(keyId)}/models`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model_entitlements: modelEntitlements }),
    },
  );
  return handleResponse<ApiKey>(res);
}
