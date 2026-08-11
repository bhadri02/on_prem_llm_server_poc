# API Integration Guide (for a new frontend)

This is a standalone reference for building a **new** production frontend
against this platform's real backend APIs. It does not assume you're using
or looking at `portal_ui` — that app is an internal developer/reference UI,
not the product frontend. Everything here is derived directly from the
current backend code (routers + Pydantic schemas), not from `portal_ui`'s
implementation.

---

## 1. The two backends you're integrating with

| | Admin Portal (`admin_portal`) | API Gateway (`api_gateway`) |
|---|---|---|
| Base path | `/portal/*` | `/v1/*` |
| Default port | `8084` | `8080` |
| Auth | **Session cookie** (`POST /portal/auth/login`) | **`X-Api-Key` header** |
| What it's for | Login, chat proxy, users/roles/keys admin, model admin, audit/governance/metrics | The real OpenAI-compatible chat completion API |

These are two separate services. A browser-based frontend needs a plan for
reaching both from one page without hitting CORS — see [§2](#2-integration-pattern-same-origin-reverse-proxy).

**For a logged-in-user product** (most likely what you're building), you
mostly only need Admin Portal — it has its own chat proxy
(`POST /portal/chat/completions`) that forwards to the API Gateway
server-side, using the logged-in user's own credentials. You don't need to
manage API keys client-side at all in that flow. See [§4](#4-chat).

The API Gateway's `/v1/*` surface exists for API-key-based, non-browser
integrations (server-to-server, external tools, Postman/curl) — use it
directly only if your product is that kind of integration instead of a
logged-in web app.

---

## 2. Integration pattern: same-origin reverse proxy

Recommended, and what this project's own reference deployment does
(`deploy/nginx/portal.conf` in `docker-compose.prod.yml`): put your
frontend and a reverse proxy in front of both backends, so the browser only
ever talks to one origin:

```
https://yourapp.example.com/          -> your frontend's static files
https://yourapp.example.com/portal/*  -> proxy to admin_portal:8084
https://yourapp.example.com/v1/*      -> proxy to api_gateway:8080   (only if you need direct API-key access)
```

This means:
- No CORS configuration needed anywhere.
- The httpOnly session cookie from login just works on every `/portal/*`
  call — you never read, store, or manage it in JS.
- Your frontend code calls **relative paths** (`fetch("/portal/auth/login")`),
  never a hardcoded host/port.

If you instead call both backends cross-origin (different domains/ports),
you'll need `credentials: "include"` on every `/portal/*` fetch and CORS
enabled on `admin_portal` for your frontend's origin — that isn't configured
today and would need a backend change; reverse-proxying is simpler and is
the supported path.

---

## 3. Authentication

### 3.1 Login

```
POST /portal/auth/login
Content-Type: application/json

{ "username": "alice", "password": "..." }
```

**200 OK** — also sets an httpOnly session cookie (`portal_session` by
default) on the response. You don't need to do anything with it manually;
the browser stores and resends it automatically on same-origin requests.

```json
{
  "user_id": "00000000-0000-0000-0000-000000000001",
  "username": "alice",
  "department": "engineering",
  "roles": ["developer"]
}
```

**401** — `{"detail": {"error": "invalid_credentials", "message": "Invalid username or password."}}`

### 3.2 Get current session

```
GET /portal/auth/me
```

Use this on app load to check if the user is already logged in (e.g. a
returning session cookie). **200** returns the same shape as login's
response body. **401** (`{"detail": {"error": "unauthorized", "message": "Not logged in."}}`)
means show the login screen. A distinct **401** body
(`{"detail": {"error": "session_expired", ...}}`) is returned specifically
when the session has expired — you can use this to show a
"session expired, please log in again" message instead of a generic one.

### 3.3 Logout

```
POST /portal/auth/logout
```

**204 No Content.** Clears the session cookie server-side and client-side.

### 3.4 Session lifetime

Sessions expire after `SESSION_TTL_HOURS` (8 hours by default, server-configured).
There is no refresh-token flow — when a session expires, the user needs to
log in again. Every `/portal/*` endpoint (other than `/auth/login`,
`/health`, `/config`) returns **401** once the session is invalid or
expired, so a global fetch-wrapper that redirects to login on any 401 from
`/portal/*` is the simplest way to handle this.

---

## 4. Chat

### 4.1 Send a message (recommended path — session-based)

```
POST /portal/chat/completions
Content-Type: application/json
(session cookie sent automatically)

{
  "model": "llama3.2:3b",
  "messages": [
    { "role": "user", "content": "Hello" }
  ],
  "temperature": 0.7
}
```

- `model` — **required**, non-empty string. Must be one of the model names
  from [§4.3](#43-list-available-models).
- `messages` — required, at least one entry. `role` is one of
  `"system" | "user" | "assistant"`.
- `temperature` — optional, `0.0`–`2.0`, defaults to `0.7`.

This call is **not streaming** — you get the full response back in one
JSON body (no `stream` option exists on this platform's pipeline today).
Build your UI accordingly (e.g. a "thinking…" spinner, not a token-by-token
render).

**200 OK** — OpenAI-shaped response, `extra` fields allowed/passed through:

```json
{
  "id": "chatcmpl-...",
  "object": "chat.completion",
  "created": 1786440674,
  "model": "llama3.2:3b",
  "choices": [
    { "index": 0, "message": { "role": "assistant", "content": "..." }, "finish_reason": "stop" }
  ],
  "usage": { "prompt_tokens": 37, "completion_tokens": 3, "total_tokens": 40 },
  "task_type": "chat",
  "cache_hit": false
}
```

`cache_hit: true` means the response was served from the semantic cache
rather than a fresh model call — same shape either way, harmless to ignore
if you don't need it, useful if you want to show a "⚡ cached" badge.

**Errors** — this endpoint proxies whatever the API Gateway returns, so see
[§8](#8-error-reference) for the full set (400 injection/content-safety
block, 403 policy/entitlement denial, 429 rate limit, 502 upstream down).
On a gateway-unreachable failure specifically, this endpoint returns its
own envelope instead: `502 {"error": "upstream_unavailable", "message": "...", "upstream": "api-gateway"}`.

### 4.2 Why session-based instead of the raw API Gateway?

Using `/portal/chat/completions` means:
- No API key to store, rotate, or leak in the browser.
- The request is made under **the logged-in user's own resolved identity**
  (their roles, their model entitlements) — RBAC denials (`policy_denied`,
  `model_not_entitled`) reflect the actual user, not a shared service key.
- One less auth mechanism for your frontend to implement.

Only call `/v1/chat/completions` directly (see [§4.4](#44-direct-api-gateway-access-advanced))
if you have a specific reason to (e.g. a non-browser client, or a
deliberately API-key-scoped integration).

### 4.3 List available models

```
GET /portal/chat/models
(session cookie sent automatically)
```

**200 OK** — every *active* model, each annotated with whether the current
user is entitled to use it:

```json
[
  { "name": "llama3.2:3b", "version": "1.0.0", "backend": "ollama", "tasks": ["chat","code",...], "status": "active", "entitled": true },
  { "name": "claude-sonnet-4-5", "version": "1.0", "backend": "anthropic", "tasks": ["chat"], "status": "active", "entitled": false }
]
```

Non-entitled models are **included, not hidden** — show them
greyed-out/locked in your model picker rather than filtering them out, so
users understand what exists and can request access, rather than not
knowing a model exists at all.

### 4.4 Direct API Gateway access (advanced)

Only relevant if you're not going through Admin Portal's proxy.

```
POST /v1/chat/completions
X-Api-Key: <a real per-user API key>
Content-Type: application/json

{ "model": "llama3.2:3b", "messages": [...] }
```

Same request/response shape as §4.1. API keys are minted via the Users/Keys
admin endpoints ([§5.3](#53-api-keys)) — the raw key value is shown **exactly
once**, at creation time, and never retrievable again; your frontend must
capture and display it to the admin immediately (e.g. a "copy this now,
you won't see it again" dialog).

```
GET /v1/models
X-Api-Key: <key>
```

Returns `{"object": "list", "data": [{"id": "...", "object": "model"}, ...]}`.

**Don't use this for real model discovery — it's currently a hardcoded,
static placeholder list** (`["llama3"]` at the time of writing, not even a
real registered model name), not a reflection of what's actually
registered or routable. Use `/portal/chat/models` ([§4.3](#43-list-available-models))
or `/portal/models` ([§5.4](#54-models)) instead — both return the real,
live registry. This endpoint exists only for literal OpenAI-client-library
compatibility (some clients probe `/v1/models` on connect); don't build
your model picker from it.

---

## 5. Admin management

Everything in this section requires the user to be logged in
(`get_current_session`); endpoints marked **admin-only** additionally
require the `"admin"` role (`require_admin` — a plain **403** otherwise:
`{"detail": {"error": "forbidden", "message": "admin role required."}}`).

### 5.1 Users — admin-only

| Method | Path | Body | Notes |
|---|---|---|---|
| `POST` | `/portal/users/` | `{username, email?, department?, roles?: string[], password?}` | `201`. `password` optional — a user with none set can't log in until an admin sets one. `409` if username taken. |
| `GET` | `/portal/users/` | — | List all users. |
| `GET` | `/portal/users/{user_id}` | — | `404` if not found. |
| `PATCH` | `/portal/users/{user_id}` | `{status: "active"\|"inactive"}` | |
| `PATCH` | `/portal/users/{user_id}/roles` | `{roles: string[]}` | Full replace, not merge. `422` if any role name doesn't exist. |
| `DELETE` | `/portal/users/{user_id}` | — | `204`. Soft-delete (sets `status: "inactive"`), not a hard delete. |
| `PATCH` | `/portal/users/{user_id}/password` | `{password: string}` | Admin-set/reset. |

`UserOut` shape (returned by all of the above except `DELETE`):
```json
{ "user_id": "...", "username": "...", "email": null, "department": null, "status": "active", "roles": ["developer"], "created_at": "...", "updated_at": "..." }
```

### 5.2 Roles & permissions

| Method | Path | Auth | Body |
|---|---|---|---|
| `GET` | `/portal/roles/` | any logged-in user | — |
| `GET` | `/portal/roles/{role}/permissions` | any logged-in user | — |
| `PATCH` | `/portal/roles/{role}/permissions` | **admin-only** | `{permissions: {task_type: bool}}` |

`RolePermissionsOut`: `{"role_name": "analyst", "permissions": {"chat": true, "code": false, ...}}`.
`task_type` keys are: `chat`, `code`, `reasoning`, `summarization`, `translation`.

The `PATCH` takes effect on real request enforcement within ~15 seconds
(the Router polls this on a TTL cache) — **no backend restart needed**. One
caveat worth knowing: the `"viewer"` role is blocked from calling the
platform *at all* by a separate, unrelated, hardcoded gate — granting it
permissions here has no visible effect for that specific role. Every other
role is fully governed by this endpoint.

### 5.3 API keys

Two scopes: per-user (`/portal/users/{id}/keys`) and admin-wide listing
(`/portal/keys/`).

| Method | Path | Auth | Body |
|---|---|---|---|
| `POST` | `/portal/users/{user_id}/keys` | admin-only (nested under users router) | `{label?, model_entitlements?: string[], expires_at?, rate_limit_rpm?}` |
| `GET` | `/portal/users/{user_id}/keys` | admin-only | — |
| `DELETE` | `/portal/users/{user_id}/keys/{key_id}` | admin-only | — (revokes, doesn't hard-delete) |
| `PATCH` | `/portal/users/{user_id}/keys/{key_id}/models` | admin-only | `{model_entitlements: string[]}` (full replace) |
| `GET` | `/portal/keys/` | admin-only | — every key across every user, with owner identity joined in |

`model_entitlements: []` (empty) means **unrestricted — access to every
model**, not "no access." This is a backward-compat default, not a bug —
make this explicit in your admin UI's copy (e.g. "Empty = all models" next
to the field) so admins don't assume empty means locked out.

`POST /users/{id}/keys` response (`ApiKeyCreated`) includes `raw_key` —
**the only time the real key value is ever returned.** Every other read
(`GET`) only returns `key_prefix` (first few characters) for display —
there is no way to retrieve a lost key value; the admin has to revoke and
re-create it.

### 5.4 Models

| Method | Path | Auth | Body |
|---|---|---|---|
| `GET` | `/portal/models` | any logged-in user | — full registry list, all statuses |
| `POST` | `/portal/models` | admin-only | `ModelRegisterRequest` (below) |
| `PATCH` | `/portal/models/{name}/status` | admin-only | `{status: "active"\|"retired"\|"staging"}` |
| `PATCH` | `/portal/models/{name}/api-key` | admin-only | `{api_key: string}` |

```ts
// ModelRegisterRequest
{
  name: string, version: string, backend: string, endpoint: string,
  tasks: string[], status?: "active"|"retired"|"staging",  // default "staging"
  vram_required_gb?: number, max_context_length?: number,
  fallback_model?: string, notes?: string,
  api_key?: string   // required for cloud backends e.g. backend="anthropic"
}
```

`api_key` is **write-only** — never echoed back in any response; `GET`
responses only ever show a boolean (`api_key_set`), never the value.

**Important, tell your backend/platform team about this if you're building
model-registration UI:** registering a model here does **not** make it
routable by itself — a separate config file (`model_matrix.yaml` /
`model_matrix.docker.yaml` depending on deployment) needs a matching entry
and the Router needs a restart. If your admin UI lets someone register a
model, the UI should say so explicitly (e.g. "Registered — contact platform
ops to make this routable") rather than implying it's immediately usable.

For a cloud model (`backend` other than `"ollama"`), the `name` **must be
the literal provider API model ID** (e.g. Anthropic's real model string),
not an arbitrary label — it's sent verbatim to the provider's API. Getting
this wrong doesn't error at registration time; it silently falls back to a
local model at chat time. Validate/warn on this in your UI if you can.

---

## 6. Observability (for an admin dashboard)

### 6.1 Audit trail

```
GET /portal/audit/events?from=<ISO-8601>&to=<ISO-8601>&limit=<1-200>
GET /portal/audit/requests/{request_id}
```

Admin-only (both require the session cookie; use `require_admin`-gated UI
routes). Returns a flat list of audit events (`{events: [...]}`), each:

```json
{
  "audit_id": "...", "request_id": "...", "timestamp_utc": "...",
  "user_id": "...", "layer": "security"|"router"|"api_gateway"|...,
  "event_type": "request_received"|"security_block"|"policy_denied"|"inference_complete"|...,
  "model_used": "llama3.2:3b", "prompt_tokens": 26, "completion_tokens": 8,
  "latency_ms": 1424, "outcome": "pass"|"block"|"error"|"flag",
  "error_code": "injection_detected", "pii_actions": [], "policy_decisions": []
}
```

`GET /portal/audit/requests/{request_id}` returns the full chain of events
for one request across every layer — useful for a "trace this request"
detail view. Every chat response includes an `id` you can use as this
`request_id` for a "view audit trail" link/button next to each message.

**Known real gap:** `api_gateway`'s own 401/403/429 rejections are **not**
in this trail (stdout-only, not persisted) — only security/router-layer
decisions are. Don't build a dashboard feature that assumes 100% coverage
of every rejection type from this endpoint alone.

### 6.2 Governance / security summary

```
GET /portal/governance/summary?from=<ISO-8601>&to=<ISO-8601>
```

Admin-only. A single pre-aggregated endpoint — use this instead of
computing rollups client-side from raw `/audit/events`:

```json
{
  "total_events": 145,
  "by_outcome": { "pass": 140, "block": 5 },
  "by_layer": { "security": 80, "router": 65 },
  "requests_blocked_total": 5,
  "blocked_by_reason": { "injection_detected": 2, "policy_denied": 2, "model_not_entitled": 1 },
  "injection_flagged_total": 2,
  "pii_detections_total": 6,
  "token_usage": { "prompt_tokens": 347, "completion_tokens": 515, "total_tokens": 862 },
  "model_usage": { "llama3.2:3b": 48, "qwen2.5:3b": 10, "claude-sonnet-4-5": 4 }
}
```

This is **always populated** (computed from the real audit trail) —
reliable for a dashboard even with no Prometheus deployed.

### 6.3 Live rate metrics (optional, needs Prometheus)

```
GET /portal/metrics/summary
```

Returns live `request_rate`/`error_rate`/`cache_hit_rate`/`active_users` —
but returns **`502` whenever no Prometheus is reachable**, which is common
in smaller/on-prem deployments. Treat this as a "nice to have if available"
widget, not something your dashboard depends on — build the governance
dashboard around §6.2 first, and layer this in as progressive enhancement
(catch the 502, hide the widget, don't error the whole page).

---

## 7. Config

```
GET /portal/config
```

No auth required. Returns `{"grafana_url": "..."}` — use this if you want
to link out to a Grafana dashboard from your admin UI rather than
hardcoding the URL client-side.

---

## 8. Error reference

Every backend in this platform returns JSON errors, but the **shape isn't
uniform across layers** — build your error handling around this table
rather than assuming one envelope everywhere.

| Source | Shape | Example |
|---|---|---|
| Admin Portal (most endpoints) | `{"detail": {"error": "...", "message": "..."}}` | `{"detail": {"error": "not_found", "message": "User 'x' not found."}}` |
| Admin Portal (proxy failures) | `{"error": "...", "message": "...", "upstream": "..."}` (no `detail` wrapper) | `{"error": "upstream_unavailable", "message": "...", "upstream": "api-gateway"}` |
| API Gateway (auth/rate-limit) | `{"error": {"code": "...", "message": "..."}}` | `{"error": {"code": "401", "message": "Unauthorized"}}` |
| Security Layer blocks (via Gateway) | `{"detail": {"error": "...", "request_id": "..."}}` | `{"detail": {"error": "injection_detected", "request_id": "..."}}` |
| Router denials, 403 only (via Gateway) | flat `{"error": "...", "request_id": "...", ...extra}` | `{"error": "model_not_entitled", "request_id": "...", "allowed_models": [...]}` |

**Practical approach:** write one error-parsing helper that checks, in
order: `body.detail?.error ?? body.detail?.message`, then `body.error?.code`
(string) or `body.error` (string, for the flat router shape), then falls
back to a generic message. That covers every shape above.

**Important — on `/v1/chat/completions` (and therefore `/portal/chat/completions`,
which just relays whatever this endpoint returns), the API Gateway only
passes through `400`, `403`, and `429` from downstream unchanged.** Every
other downstream failure — a `422` (invalid pinned model) or `503` (no
healthy backend) from the Router — gets collapsed into a generic
`502 {"error": {"code": "502", "message": "Bad gateway"}}` with **no**
`invalid_pinned_model`/`all_backends_exhausted` detail preserved. Don't
build UI logic that branches on 422/503 for chat requests specifically —
you won't see those codes there even though the backend pipeline produces
them internally. (A malformed request body — e.g. missing `messages` —
*is* caught earlier and returns `400 {"error": {"code": "400", "message": "Bad request"}}`
directly from the Gateway's own validation, not this path.)

### Common status codes you'll see on chat requests

| Status | Meaning | error / error_code |
|---|---|---|
| `200` | Success | — |
| `400` | Blocked before reaching the model, or a malformed request body | `injection_detected`, `content_safety_violation`, or a generic bad-request body |
| `401` | Not logged in / invalid or missing API key | `unauthorized`, `invalid_credentials` |
| `403` | Logged in, but not permitted | `forbidden` (not admin), `policy_denied` (role can't do this task type), `model_not_entitled` (key/user can't use this model) |
| `429` | Rate limited | — (also carries a `Retry-After` header) |
| `502` | A downstream service is unreachable, **or** an internal routing failure that isn't 400/403/429 (pinned an invalid model, no healthy backend, etc. — see note above) | `upstream_unavailable`, or a generic `"502"` with no further detail |

(`422` and `503` are real internal statuses the Router produces, but as
noted above, `/v1/chat/completions` never surfaces them to a client as
such — they arrive as `502`. They remain meaningful on the **admin/management**
endpoints in §5, which don't go through this same collapsing logic — e.g.
`PATCH /portal/users/{id}/roles` really does return a `422` you can branch on.)

`403` specifically needs two different UI treatments: `policy_denied` means
"your role can't do this kind of task at all" (e.g. a `viewer` trying to
chat) — show a permissions message, not a retry button. `model_not_entitled`
means "you can chat, just not with this specific model" — the response
includes `allowed_models: [...]`, so you can immediately offer to switch
the model picker to one of those instead of just showing a dead end.

---

## 9. Quick endpoint index

```
Auth
  POST   /portal/auth/login
  POST   /portal/auth/logout
  GET    /portal/auth/me

Chat
  POST   /portal/chat/completions        (recommended — session-based)
  GET    /portal/chat/models
  POST   /v1/chat/completions            (advanced — API-key based)
  GET    /v1/models

Users & Keys (admin-only unless noted)
  POST   /portal/users/
  GET    /portal/users/
  GET    /portal/users/{id}
  PATCH  /portal/users/{id}
  PATCH  /portal/users/{id}/roles
  DELETE /portal/users/{id}
  PATCH  /portal/users/{id}/password
  POST   /portal/users/{id}/keys
  GET    /portal/users/{id}/keys
  DELETE /portal/users/{id}/keys/{key_id}
  PATCH  /portal/users/{id}/keys/{key_id}/models
  GET    /portal/keys/

Roles
  GET    /portal/roles/                       (any logged-in user)
  GET    /portal/roles/{role}/permissions     (any logged-in user)
  PATCH  /portal/roles/{role}/permissions     (admin-only)

Models
  GET    /portal/models                       (any logged-in user)
  POST   /portal/models                       (admin-only)
  PATCH  /portal/models/{name}/status         (admin-only)
  PATCH  /portal/models/{name}/api-key        (admin-only)

Observability (admin-only)
  GET    /portal/audit/events
  GET    /portal/audit/requests/{request_id}
  GET    /portal/governance/summary
  GET    /portal/metrics/summary              (502 without Prometheus)

Misc
  GET    /portal/config                       (no auth)
  GET    /portal/health                       (no auth)
```

Full request/response schemas are also browsable live via each service's
own OpenAPI docs (`GET /portal/docs` on Admin Portal) if you want to
generate a typed client instead of hand-writing one from this doc.
