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
  GovernanceSummary,
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

/**
 * Reads a Response body exactly once as text, then attempts to parse it as
 * JSON. Deliberately does NOT call res.json() and fall back to res.text() on
 * failure — res.json() consumes the body stream even when JSON.parse throws,
 * so a text() fallback after a failed json() always throws "body stream
 * already read" and masks the real error.
 */
async function readErrorMessage(res: Response): Promise<string> {
  const raw = await res.text();
  try {
    const body = JSON.parse(raw) as { message?: string };
    return body.message ?? JSON.stringify(body);
  } catch {
    return raw;
  }
}

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    throw new ApiError(res.status, await readErrorMessage(res));
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
// Playground — streaming function defined further below, alongside
// streamChatCompletion (both share the streamSSE helper).
// ---------------------------------------------------------------------------

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
    throw new ApiError(res.status, await readErrorMessage(res));
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
 * GET /portal/governance/summary
 *
 * Historical governance/security/usage counts computed from the Audit
 * Store's real audit trail — always populated, unlike metrics/summary's
 * live rates which require a reachable Prometheus. Accepts optional
 * from/to ISO-8601 UTC range params.
 */
export async function getGovernanceSummary(params: { from?: string; to?: string } = {}): Promise<GovernanceSummary> {
  const query = new URLSearchParams();
  if (params.from !== undefined) query.set("from", toUtcIso(params.from));
  if (params.to !== undefined) query.set("to", toUtcIso(params.to));

  const qs = query.toString();
  const res = await fetch(`/portal/governance/summary${qs ? `?${qs}` : ""}`);
  return handleResponse<GovernanceSummary>(res);
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

export interface ChatStreamHandlers {
  /** Called for each non-empty text delta, in arrival order. */
  onDelta: (content: string) => void;
  /** Called once when the stream ends successfully (finish_reason from the final chunk). */
  onDone: (finishReason: string) => void;
  /** Called at most once — either a transport failure or an in-band `{"error": ...}` frame. */
  onError: (message: string) => void;
  /** Called once, as soon as the request_id is known (parsed from the first chunk's
   *  `id` field, "chatcmpl-{request_id}") — optional, only Playground needs it today. */
  onId?: (requestId: string) => void;
}

/**
 * Shared SSE-consumption loop behind streamChatCompletion/streamPlaygroundChat
 * below. Both endpoints emit byte-identical OpenAI-compatible SSE (see
 * api_gateway/routers/chat.py's sse_relay(), relayed byte-for-byte by
 * admin_portal's sse_relay_with_inband_error) — only the URL differs. Every
 * "chat.completion.chunk" event before the last carries a text delta; the
 * final one carries the real finish_reason and no content; `data: [DONE]`
 * terminates the stream. Errors are signalled in-band as
 * `data: {"error": {...}}\n\n` followed by `[DONE]` — the HTTP status is
 * always 200 once streaming has started, so they can't be distinguished via
 * res.ok.
 *
 * Returns an abort function the caller can invoke (e.g. on unmount) to stop
 * reading and cancel the underlying fetch.
 */
function streamSSE(url: string, req: ChatReq, handlers: ChatStreamHandlers): () => void {
  const controller = new AbortController();
  let idEmitted = false;

  (async () => {
    let res: Response;
    try {
      res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...req, stream: true }),
        signal: controller.signal,
      });
    } catch (err) {
      if (controller.signal.aborted) return;
      handlers.onError(err instanceof Error ? err.message : String(err));
      return;
    }

    if (!res.ok || !res.body) {
      handlers.onError(await readErrorMessage(res));
      return;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        let sepIndex: number;
        while ((sepIndex = buffer.indexOf("\n\n")) !== -1) {
          const rawEvent = buffer.slice(0, sepIndex);
          buffer = buffer.slice(sepIndex + 2);

          const dataLine = rawEvent.split("\n").find((line) => line.startsWith("data:"));
          if (!dataLine) continue;
          const data = dataLine.slice(5).trim();
          if (data === "[DONE]") return;

          let parsed: unknown;
          try {
            parsed = JSON.parse(data);
          } catch {
            continue;
          }
          const obj = parsed as Record<string, unknown>;

          if ("error" in obj) {
            const errorObj = obj["error"] as Record<string, unknown> | undefined;
            const message = typeof errorObj?.["message"] === "string" ? (errorObj["message"] as string) : "Stream error";
            handlers.onError(message);
            return;
          }

          if (!idEmitted && handlers.onId) {
            const id = obj["id"];
            if (typeof id === "string" && id.startsWith("chatcmpl-")) {
              idEmitted = true;
              handlers.onId(id.slice("chatcmpl-".length));
            }
          }

          const choices = obj["choices"];
          if (Array.isArray(choices) && choices.length > 0) {
            const choice = choices[0] as Record<string, unknown>;
            const delta = choice["delta"] as Record<string, unknown> | undefined;
            const content = delta?.["content"];
            if (typeof content === "string" && content.length > 0) {
              handlers.onDelta(content);
            }
            const finishReason = choice["finish_reason"];
            if (typeof finishReason === "string") {
              handlers.onDone(finishReason);
            }
          }
        }
      }
    } catch (err) {
      if (controller.signal.aborted) return;
      handlers.onError(err instanceof Error ? err.message : String(err));
    }
  })();

  return () => controller.abort();
}

/** POST /portal/chat/completions with stream: true — see streamSSE for the wire format. */
export function streamChatCompletion(req: ChatReq, handlers: ChatStreamHandlers): () => void {
  return streamSSE("/portal/chat/completions", req, handlers);
}

/** POST /portal/playground/chat with stream: true — see streamSSE for the wire format. */
export function streamPlaygroundChat(req: ChatReq, handlers: ChatStreamHandlers): () => void {
  return streamSSE("/portal/playground/chat", req, handlers);
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
    throw new ApiError(res.status, await readErrorMessage(res));
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
  opts: { label?: string; model_entitlements?: string[]; rate_limit_rpm?: number } = {},
): Promise<ApiKeyCreated> {
  const res = await fetch(`/portal/users/${encodeURIComponent(userId)}/keys`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      label: opts.label ?? null,
      model_entitlements: opts.model_entitlements ?? [],
      ...(opts.rate_limit_rpm != null ? { rate_limit_rpm: opts.rate_limit_rpm } : {}),
    }),
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

/** PATCH /portal/users/{userId}/keys/{keyId}/rate-limit */
export async function patchKeyRateLimit(
  userId: string,
  keyId: string,
  rateLimitRpm: number,
): Promise<ApiKey> {
  const res = await fetch(
    `/portal/users/${encodeURIComponent(userId)}/keys/${encodeURIComponent(keyId)}/rate-limit`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rate_limit_rpm: rateLimitRpm }),
    },
  );
  return handleResponse<ApiKey>(res);
}
