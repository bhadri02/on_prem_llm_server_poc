# Frontend Integration Guide

Backend API reference for wiring a real frontend to the two static mockups:
`user-portal_8.html` (Chat) and `admin-portal_6.html` (Admin console).

Every endpoint here already exists and is tested. This document maps each UI
element in the mockups to its real backend call, states exact request/response
shapes, and calls out gaps the backend does **not** cover yet — so the
frontend can build around them consciously instead of discovering them at
runtime.

All Admin Portal endpoints are mounted under the `/portal` prefix (e.g.
`GET /portal/models`). Base URL is `admin_portal`'s `API_GATEWAY_URL` for chat,
otherwise `admin_portal` itself. There is **no browser-facing authentication**
on any `/portal/*` endpoint — this matches the existing POC posture (the
Portal UI already has no login system). The one endpoint that *is* guarded is
service-to-service (`GET /portal/keys/resolve`, called by the API Gateway, not
the browser).

---

## 1. Chat view (`user-portal_8.html`)

### 1.1 Sidebar — "Your entitled models" list

**Mockup behavior:** shows every model with a lock icon on ones the user
can't use (`m.entitled === false` → greyed out + lock icon; not hidden).

**Backend call:**

```
GET /portal/chat/models
```

Response — array of model objects from the Model Registry, each annotated
with `entitled`:

```json
[
  {
    "name": "llama3.2:3b",
    "version": "1.0.0",
    "backend": "ollama",
    "endpoint": "http://ollama:11434",
    "tasks": ["chat", "code", "reasoning", "summarization", "translation"],
    "status": "active",
    "vram_required_gb": null,
    "max_context_length": 8192,
    "fallback_model": null,
    "registered_at": "2026-01-01T00:00:00Z",
    "notes": null,
    "api_key_set": false,
    "entitled": true
  },
  {
    "name": "claude-sonnet-5",
    "backend": "anthropic",
    "status": "active",
    "api_key_set": true,
    "entitled": true,
    "...": "..."
  }
]
```

- Only `status == "active"` models are returned.
- `entitled` is computed from the identity behind the portal's own
  `GATEWAY_API_KEY` — see **§4 Model access model** for how per-user
  entitlement actually works once real per-user keys are wired in.
- Render `entitled: false` rows exactly like the mockup's `.locked` /
  lock-icon state — do not filter them out client-side; the backend already
  includes them intentionally so the UI can show what's *unavailable*, not
  just what's available.
- There's no `ready`/`cloud` boolean in this response the way the mockup's
  hardcoded `models` array has one. Derive "cloud" from `backend !== "ollama"`.
  There's no live per-model "ready" health flag for cloud backends — see
  §5.3.

Error: `502 {"error": "upstream_unavailable", "message": "...", "upstream": "model-registry"}`
if the Model Registry is unreachable.

### 1.2 "Model routing" card (auto-route display)

The mockup's `classifyMessage()` client-side task/model classifier is a
**demo stand-in only**. The real classification happens server-side, inside
the Intelligent Router, once the message is actually sent — there is no
separate "preview the routing decision" endpoint. The frontend cannot show
this card correctly until after the first message's response comes back.

Populate the card from the chat response (see §1.4): `model` (→ routed
model), `task_type` (→ task badge). There is no `complexity`/`word_count`
signal returned — drop that part of the mockup's sub-label, or reword it
generically (e.g. "Classified as {task_type}").

### 1.3 Sending a message

```
POST /portal/chat/completions
Content-Type: application/json

{
  "model": "llama3.2:3b",
  "messages": [
    {"role": "user", "content": "Summarize the attached Q3 sales report..."}
  ],
  "temperature": 0.7
}
```

- `model` is **required** by this endpoint's schema (`ChatRequest.model`,
  non-empty). This conflicts with the mockup's "auto-route, no model
  selection" UX — see §5.1 for the gap and recommended workaround.
- `messages`: only `role` (`system`|`user`|`assistant`) + `content` — no
  attachments, no function-calling fields.
- This is the **full conversation history** on every call — the backend is
  stateless per-request; there is no server-side session/thread concept (see
  §5.2). The frontend must resend prior turns itself.

### 1.4 Response shape

```json
{
  "id": "chatcmpl-<request_id>",
  "object": "chat.completion",
  "created": 1739200000,
  "model": "llama3.2:3b",
  "choices": [
    {
      "index": 0,
      "message": { "role": "assistant", "content": "..." },
      "finish_reason": "stop"
    }
  ],
  "usage": { "prompt_tokens": 42, "completion_tokens": 17, "total_tokens": 59 },
  "task_type": "summarization",
  "cache_hit": false
}
```

- `model` reflects the **actually-routed** model (not necessarily what you
  sent, if auto-routing overrode it) — use this to update the sidebar's
  "Model routing" card and the message's meta line (mockup shows
  `llama3.2:3b · 14:02:09 · cache miss · 340 tokens`; `cache_hit` +
  `usage.total_tokens` give you both of those).
- `task_type` and `cache_hit` are additive fields beyond the OpenAI shape —
  safe for any OpenAI-compatible client to ignore, but this is what the
  mockup's task classification badge should read from.

### 1.5 Error responses (chat)

| Status | Body | Meaning | UI treatment |
|---|---|---|---|
| 400 | `{"error": {"code": "400", "message": "Bad request"}}` | Malformed request body | Inline validation error |
| 401 | `{"error": {"code": "401", "message": "Unauthorized"}}` | API key missing/invalid | Force re-auth (n/a today — no login) |
| 403 | `{"error": {"code": "403", "message": "Forbidden"}}` | Key has no active roles | Show access-denied state |
| 403 | `{"error": "policy_denied", "request_id": "..."}` | Role not permitted for this task type | Show "your role can't use this feature" |
| 403 | `{"error": "model_not_entitled", "request_id": "...", "allowed_models": [...]}` | Pinned model not in caller's entitlements | Show `allowed_models` as the fallback picker |
| 502 | `{"error": {"code": "502", "message": "Bad gateway"}}` | Downstream pipeline failure | Generic retry banner |
| 503 | `{"error": {"code": "503", "message": "Identity service unavailable"}}` | Admin Portal (identity) down | Generic retry banner |

Note the two error shapes are **not consistent** — auth-layer errors nest
under `error.code`/`error.message`; router-layer errors are a flat
`{"error": "<code>", ...}`. The frontend's error handler needs to branch on
whether `error` is a string or an object.

### 1.6 "Export" button

No backend support. There's no `GET /portal/chat/sessions/{id}/export` or
equivalent — sessions aren't persisted server-side at all (see §5.2). Either
implement export as a pure client-side download of the in-memory transcript,
or treat it as out of scope for this pass.

### 1.7 Session list (left sidebar, multiple past chats)

No backend support — chat history is **not persisted**. The mockup's
`sessions` array is entirely client-state. If multi-session history is
wanted, it needs a new backend feature (not present today); for now, treat
Chat as a single ephemeral conversation per page load, matching the MVP's
documented no-persistence scope (streaming itself is implemented — see §5.2
item 3).

---

## 2. Admin console (`admin-portal_6.html`)

### 2.1 Dashboard

| Mockup element | Backend call | Notes |
|---|---|---|
| Requests/sec, Error rate | `GET /portal/metrics/summary` | `{request_rate, error_rate, cache_hit_rate, active_users}` — all `float | null`, `active_users: int | null`. `null` means "no data yet", not zero — render as `—`. |
| Cache hit rate | same call | `cache_hit_rate` field |
| Active users | same call | `active_users` — new field; `null` if the DB query itself fails (never blocks the rest of the summary) |
| Requests by task type (bar chart) | **no backend endpoint** | See §5.4 — there is no task-type breakdown metric exposed today. |
| Recent audit events | `GET /portal/audit/events?limit=5` | See §2.5 shape below |

`GET /portal/metrics/summary` error: `502 {"error": "upstream_unavailable", "upstream": "prometheus"}`.

### 2.1.1 AI Governance & Security panel (Phase 8)

Implemented in `portal_ui` as its own view (`views/GovernanceView.tsx`, nav link
"Governance", admin-only, route `/governance`) rather than folded into the
Dashboard tab — the mockup has no equivalent panel, since this data didn't
exist until this phase.

**Why a separate endpoint from `/portal/metrics/summary`:** that endpoint
depends on a live Prometheus server for *live per-second rates*, and in the
default local dev setup (no Prometheus running — see §5.7) it returns `null`
for every rate field. The data below — how many requests were blocked and
why, how many tokens have been consumed, which models are actually serving
traffic — comes straight from the Audit Store's own SQLite audit trail
instead, computed on demand from real historical events. It's always
populated whenever the platform has processed any traffic, with no extra
infrastructure required. Use `/portal/metrics/summary` for live rate/error/
cache-hit percentages; use this endpoint for durable counts, reasons, and
totals.

```
GET /portal/governance/summary
GET /portal/governance/summary?from=2026-01-01T00:00:00Z&to=2026-02-01T00:00:00Z
```

Response (`admin_portal/schemas/governance.py::GovernanceSummary`, proxying
`audit_store`'s `GET /audit/governance/summary`):

```json
{
  "total_events": 1204,
  "by_outcome": {"pass": 1080, "block": 96, "error": 28},
  "by_layer": {"api_gateway": 0, "security": 602, "router": 602},
  "requests_blocked_total": 96,
  "blocked_by_reason": {
    "injection_detected": 12,
    "content_safety_violation": 4,
    "policy_denied": 60,
    "model_not_entitled": 20
  },
  "injection_flagged_total": 12,
  "pii_detections_total": 37,
  "token_usage": {
    "prompt_tokens": 184200,
    "completion_tokens": 96400,
    "total_tokens": 280600
  },
  "model_usage": {"llama3.2:3b": 410, "qwen2.5:3b": 96}
}
```

| Field | Maps to the user's ask | Notes |
|---|---|---|
| `requests_blocked_total` / `blocked_by_reason` | "requests rejected/blocked due to unsafe, malicious, or policy-violating prompts" + "guardrail trigger count and rejection reasons" | Reason keys: `injection_detected`, `content_safety_violation` (security_layer, pre-model); `policy_denied`, `model_not_entitled` (intelligent_router, RBAC). `api_gateway`'s own `auth_fail`/`rate_limited` blocks are **not** included — see the gap callout below. |
| `injection_flagged_total` | "requests flagged for potential prompt injection" | Same event as the `injection_detected` block reason — injection scoring is binary in this POC (§5.8), so flagged and blocked are identical here, not two separate counts. |
| — (no separate field) | "responses blocked due to security/guardrail violations" | Not a distinct field: `security_layer`'s post-pipeline only ever masks PII in the response, it has no response-blocking capability at all (confirmed in code, not just undocumented) — see §5.9. |
| `token_usage` | "token consumption and LLM usage statistics" | Summed from real per-request `prompt_tokens`/`completion_tokens` on router audit events (both cache-hit and live-inference paths). |
| `by_outcome`, `total_events` | "number of failed, rejected, and successfully processed requests" | `by_outcome` keys are `pass`/`block`/`error`/`flag`; there is no single "failed" bucket — `error` (5xx/internal) is distinct from `block` (guardrail/policy denial). |
| `pii_detections_total` | "any other relevant AI governance/security metric" | Count of masked PII entities (request + response side), summed across all events' `pii_actions` arrays. |
| `model_usage` | "any other relevant AI governance/security metric" | Successfully-served request counts per model (`inference_complete` + `cache_hit` router events) — a real per-model traffic split, unlike the aspirational task-type breakdown in §5.4. |

**Known gap in this data (intentional-for-now):** `api_gateway`'s own audit
events (`auth_fail`, `auth_pass`, `rate_limited`, `request_received`,
`response_sent` — see `api_gateway/services/audit.py`) are written to
**stdout only**, never POSTed to the Audit Store. This means 401/403 auth
failures and 429 rate-limit rejections are visible in the gateway's
container logs but are **not** reflected anywhere in this endpoint's counts
— only `security_layer` and `intelligent_router` audit events make it into
the trail this summary reads from. If gateway-layer denial counts are ever
needed here, `api_gateway` would need to start POSTing to the Audit Store
the same fire-and-forget way `security_layer`/`intelligent_router` already
do (see `security_layer/audit_client.py` / `intelligent_router/audit_client.py`
for the pattern to copy).

`GET /portal/governance/summary` error: `502 {"error": "upstream_unavailable", "upstream": "audit-store"}`.
A malformed `from`/`to` is relayed through from the Audit Store unchanged as
`422 {"message": "invalid time parameter(s)", "errors": {...}}` (not
reshaped into the `ErrorResponse` envelope used elsewhere in this service).

### 2.2 Users & Roles tab

**All users table / user detail panel:**

```
POST   /portal/users/                        create
GET    /portal/users/                        list (includes roles[])
GET    /portal/users/{user_id}                detail
PATCH  /portal/users/{user_id}                {"status": "active"|"inactive"}
PATCH  /portal/users/{user_id}/roles          {"roles": ["developer"]}  — full replace
DELETE /portal/users/{user_id}                soft-delete (sets status=inactive), 204
```

`UserOut`:
```json
{
  "user_id": "uuid",
  "username": "rafael",
  "email": "rafael@company.com",
  "department": "Engineering",
  "status": "active",
  "roles": ["developer"],
  "created_at": "...",
  "updated_at": "..."
}
```

Mockup differences to reconcile:
- Mockup's "Change role" implies a single role per user; backend is
  `roles: list[str]` (a user can hold multiple). `PATCH .../roles` **replaces
  the whole list** — if the UI only ever shows/edits one role, always send a
  single-element array.
- Mockup's `email`/`name` split: backend only has `username` + optional
  `email`, no separate display name. Use `username` as the display name or
  add a name field client-side only.
- `POST /portal/users/` returns `409 {"error": "already_exists", ...}` if
  `username` is taken, `422 {"error": "invalid_role", ...}` for an unknown
  role in the request.

**Per-user model access** (mockup's "Per-user model access" matrix /
checkbox row in the user detail panel):

There is **no `user.modelAccess` field** in the backend. Per §4, model
access is derived from the union of `model_entitlements` across a user's
*active API keys*, not stored on the user directly. To reproduce the
mockup's per-user toggle UX:
- Read: fetch the user's keys (`GET /portal/users/{id}/keys`) and union their
  `model_entitlements` arrays for display.
- Write: toggling a model "on" for a user means adding it to
  `model_entitlements` on **every active key** that user owns (via
  `PATCH /portal/users/{user_id}/keys/{key_id}/models`, looping over all of
  that user's active keys). Toggling off removes it from all of them.
- If the user has **zero** active keys, there is nothing to grant — the UI
  should prompt "generate a key first" rather than silently doing nothing.

**Role permission matrix** (read-only display + the mockup's editable toggle):

```
GET   /portal/roles/                          list roles + descriptions
GET   /portal/roles/{role}/permissions        {"role_name": "...", "permissions": {"chat": true, "code": false, ...}}
PATCH /portal/roles/{role}/permissions        {"permissions": {"code": true}}  — partial patch, upserted
```

✅ **Now live**: `PATCH /portal/roles/{role}/permissions` persists to the
`role_permissions` Postgres table, and `intelligent_router` polls
`GET /portal/policy/matrix` (an internal, key-gated endpoint) on a TTL cache
(`POLICY_CACHE_TTL_SECONDS`, default 15s — see
`intelligent_router/services/policy_resolver.py`). A change here takes
effect on real chat/completions enforcement within that window — no Router
restart needed. The mockup's toast ("Security Layer will pick it up on next
cache refresh") is now accurate. `policy_matrix.yaml` still exists as the
Router's fail-fast startup baseline and offline fallback if admin_portal is
ever unreachable, but it's no longer the ongoing source of truth.

⚠️ **Separate, still-static gate**: `security_layer` has its own coarse
"can this identity call the platform at all" check (`ALLOWED_ROLES` in
`security_layer/policy.py`) — a hardcoded Python frozenset, not backed by
this table. `viewer` is excluded from it entirely, so granting `viewer` a
task in this matrix updates what `GET` reports but can never take live
effect — `viewer` is rejected one layer earlier regardless. Every other
role (`analyst`/`developer`/`admin`) passes that gate and is fully governed
by this matrix. Changing `ALLOWED_ROLES` itself still requires a code
change + `security_layer` restart.

The mockup's task columns (`chat, code, reasoning, summarization,
translation, admin`) mostly match `TaskType` in the registry schema, except
**`admin` is not a real `task_type`** anywhere in the pipeline — there's no
concept of an "admin" task being routed/blocked. Either drop that column or
treat it as a purely UI-side flag unrelated to the permissions API.

### 2.3 API Keys tab

```
GET /portal/keys/                              admin-wide listing (all users' keys, owner joined in)
```

```json
[
  {
    "key_id": "uuid",
    "key_prefix": "sk-8f21",
    "label": "dev laptop key",
    "status": "active",
    "expires_at": "2026-12-01T00:00:00Z",
    "rate_limit_rpm": null,
    "created_at": "...",
    "last_used_at": null,
    "model_entitlements": ["llama3.2:3b", "claude-sonnet-5"],
    "user_id": "uuid",
    "owner_username": "rafael"
  }
]
```

Per-user key operations (used from both the Users tab's inline key list and
a dedicated Keys tab row action):

```
POST   /portal/users/{user_id}/keys                    generate — {"label", "model_entitlements", "expires_at", "rate_limit_rpm"}
GET    /portal/users/{user_id}/keys                     list (masked — never returns the raw key)
DELETE /portal/users/{user_id}/keys/{key_id}            revoke
PATCH  /portal/users/{user_id}/keys/{key_id}/models     {"model_entitlements": [...]}  — full replace
```

**Raw key display:** `POST /portal/users/{user_id}/keys` is the **only**
call that ever returns the plaintext key (field `raw_key` on the response,
alongside the normal masked fields). The mockup's `masked: 'sk-8f21•••••••'`
display style should be built by the frontend at creation time from
`raw_key` (show once, then discard) — every subsequent listing only ever has
`key_prefix`, never the full key. There is no "reveal key later" flow —
if the user navigates away without copying it, it's gone (matches the
mockup's own implied one-time-reveal UX for "Legacy POC key" etc., though the
mockup doesn't actually dramatize this).

**Gap:** the admin-wide `GET /portal/keys/` listing has no revoke/edit
action of its own — those actions require `user_id` (routed as
`/users/{user_id}/keys/{key_id}`), which `ApiKeyWithOwner` conveniently
includes. Use the `user_id` field from each row to build those action URLs
from the flat table, rather than needing a separate per-user fetch.

### 2.4 Model Registry tab

```
GET   /portal/models                              list (ModelRecordPublic[] — api_key never included, only api_key_set: bool)
POST  /portal/models                              register — see body below
PATCH /portal/models/{name}/status                {"status": "active"|"retired"|"staging"}
PATCH /portal/models/{name}/api-key               {"api_key": "sk-ant-..."} — cloud models only
```

`POST /portal/models` body:
```json
{
  "name": "claude-sonnet-5",
  "version": "1.0",
  "backend": "anthropic",
  "endpoint": "https://api.anthropic.com",
  "tasks": ["chat", "code", "reasoning", "summarization", "translation"],
  "status": "staging",
  "vram_required_gb": null,
  "max_context_length": 200000,
  "fallback_model": "llama3.2:3b",
  "notes": null,
  "api_key": "sk-ant-api03-..."
}
```
- `name` must match `^[a-zA-Z0-9._:-]+$` (colon allowed for Ollama-style
  tags like `llama3.2:3b`).
- `api_key` is **required in practice** for any non-Ollama `backend` (the
  registry doesn't hard-validate this — see §5.5 — but the model is
  undispatchable without it). Mirror the mockup's own client-side check
  ("A provider API key is required for cloud models") until the backend
  enforces it itself.
- `409` if a model with that `name` already exists.
- Response is `ModelRecordPublic` — includes `api_key_set: bool`, never the
  key itself.

⚠️ **Known gap — "Register model" does not make the model routable.**
This endpoint only writes to the Model Registry's JSON catalog. The
Intelligent Router dispatches strictly from a separate static file,
`model_matrix.yaml`, loaded once at startup — it has no idea the Model
Registry exists. Registering `claude-sonnet-5` here does **not** let a real
chat request actually route to it until an operator manually adds a
matching entry to `model_matrix.yaml` and restarts the Router. Recommend
surfacing this exact caveat as a persistent banner/tooltip next to
"Register model" (e.g. "New models require a platform restart before they
can serve traffic") rather than implying it's instantly live, since the
mockup's toast ("registered in staging") reads as immediate.

`PATCH /portal/models/{name}/api-key`: `404 {"error": "not_found", ...}` if
the model doesn't exist. Use this for the mockup's "Set/Update API key"
modal — note the modal's `saveModelApiKey` always calls the same action
regardless of set-vs-update; the backend endpoint is already idempotent
that way (PATCH, not distinct set/update calls).

**Missing:** there's no `DELETE /portal/models/{name}` and no dedicated
"context length" field surfaced anywhere except `max_context_length` (an
`int`, e.g. `200000` — the mockup displays it pre-formatted as `"200k"`;
format client-side).

### 2.5 Audit Log tab

```
GET /portal/audit/events?from=<iso8601>&to=<iso8601>&limit=<1-200>
GET /portal/audit/requests/{request_id}
```

Response (`AuditEventList`):
```json
{
  "events": [
    {
      "request_id": "uuid",
      "event_type": "policy_denied",
      "user_id": "uuid-or-null",
      "method": "POST",
      "path": "/v1/chat/completions",
      "status_code": 403,
      "outcome": "block",
      "timestamp_utc": "...",
      "...": "additional fields per event_type"
    }
  ]
}
```
- Sorted descending by `timestamp_utc` — already done server-side, no
  client-side sort needed.
- `limit` out of `[1, 200]`, or invalid `from`/`to`, or `from > to` → `400
  {"error": "validation_error", "message": "...", "allowed_values": [...]}`.
- `GET .../requests/{id}` requires a UUID v4 `request_id`; malformed →
  `400`. No matching records → `200 {"events": []}`, not a 404.
- Mockup's audit rows show a human `user` (email) — the real event has only
  `user_id` (a UUID) or `null` (e.g. for pre-auth failures like a missing
  header). Resolving `user_id` → display name requires a client-side lookup
  against `GET /portal/users/` — there's no server-side join here, unlike
  the API Keys listing.
- Mockup's "Layer" column (`API Gateway`, `Security Layer`, ...) isn't a
  literal field — infer it from `event_type` conventions (`auth_*` →
  Gateway, `policy_denied`/`security_block` → Security/Router layer,
  `response_sent` → Gateway) or extend the audit event schema — not done in
  this pass.
- "Export" button: no backend CSV/export endpoint. Client-side
  CSV-from-JSON generation of the currently-loaded page is the only option
  today (no server-side full-export).

---

## 3. Auth model summary

- **Browser ↔ Admin Portal (`/portal/*`)**: no auth today. Anyone who can
  reach the Admin Portal's HTTP port can call every admin endpoint. This
  matches the existing POC's Playground/Models/Audit endpoints — not a new
  gap introduced in this pass, just extended to the new surface.
- **Admin Portal → Model Registry**: `X-Api-Key: REGISTRY_API_KEY` (service
  secret, not user-specific).
- **Browser/client → API Gateway (`/v1/chat/completions`)**: `X-Api-Key:
  <per-user-key>` header, resolved server-side against the Admin Portal DB
  (never trusts client-claimed identity). This is the *only* place real
  per-user identity is enforced today — the Admin Portal's own `/portal/*`
  surface uses one fixed `GATEWAY_API_KEY` for its own proxy calls,
  regardless of which browser session is asking.
- **API Gateway → Admin Portal (`/portal/keys/resolve`)**: internal-only,
  guarded by `X-Portal-Internal-Key: ADMIN_PORTAL_INTERNAL_KEY`.

Practical implication for the frontend: today, "logging in" to the Admin
Portal UI as a specific admin user is cosmetic — there's no session, no
token, and no per-admin permission check on any `/portal/*` route. If a real
login/session layer is wanted for the admin console, that is new work not
covered by this backend pass.

---

## 4. Model access model (per user)

Per an explicit product decision made during this backend pass: **there is
no standalone "user's model access" field.** A user's effective model access
is the **union of `model_entitlements` across all of that user's active API
keys**. An empty `model_entitlements` array on a key means "entitled to
every active model" (backward-compat default from Phase 2 — this is why the
seeded "Legacy POC key" has no entitlement rows and works with everything).

Consequences for the UI:
- A user with zero keys has zero model access, by definition — the UI
  should make key-generation feel like a required setup step, not an
  optional extra.
- Toggling "model access" for a *user* (as opposed to a specific key) is a
  derived operation: it must fan out to every active key that user owns
  (§2.2). There is no atomic single-call way to do this server-side today.
- If a user has two keys with *different* entitlement sets, the mockup's
  single-row-per-user model matrix is ambiguous — recommend showing the
  union for display, but requiring the admin to expand to per-key editing
  (already exposed via §2.3) to actually diverge them.

---

## 5. Known gaps (do not silently work around these — flag/design for them)

1. **Chat requires a pinned `model`; the mockup UX is fully auto-routed.**
   `POST /portal/chat/completions` schema requires a non-empty `model`. Note
   that pinning now genuinely works end-to-end (previously a real bug meant
   an explicit `model` was silently ignored in favor of auto-routing unless
   it happened to match the task's default — fixed in
   `api_gateway/services/normalizer.py`), so this trade-off is now real, not
   moot: the frontend needs to either (a) always send the user's
   *default/first entitled* model as a genuine pin (loses the "auto"
   semantics — the Router will not override a real pin), or (b) this
   endpoint needs a backend change to accept an omitted `model` and forward
   to the Router's real auto-routing mode (the Router already supports
   this internally — see `intelligent_router/pipeline.py` Stage 2 — the
   Admin Portal's chat proxy just doesn't expose it). **Recommend (b)** as a
   fast follow; flagging here rather than quietly picking (a), since (a)
   would silently defeat the whole "smart routing" value proposition the
   mockup is selling.
2. **No chat session/history persistence.** Every `/portal/chat/completions`
   call is fully stateless server-side; multi-turn context and multi-session
   history are entirely client-managed in memory. Refreshing the page loses
   everything. Not in scope for this backend pass.
3. **Real streaming — implemented.** Both mockups' copy implies token-by-token
   streaming ("Responses stream token-by-token...") — this is no longer
   aspirational. `POST /portal/chat/completions` with `"stream": true` relays
   real SSE all the way through every hop (inference_adapter -> Ollama or
   Anthropic -> intelligent_router -> security_layer, which chunk-rescans and
   masks PII on the fly via `StreamingPiiMasker` -> api_gateway, which reframes
   as OpenAI-compatible `chat.completion.chunk` events -> admin_portal, which
   relays api_gateway's already-correct SSE bytes unchanged). `portal_ui`'s
   `ChatView.tsx` consumes it via `portalClient.streamChatCompletion()`,
   appending deltas to the in-progress assistant message as they arrive. The
   Playground view (`admin_portal/routers/playground.py`) intentionally still
   uses the buffered, non-streaming path — not updated in this pass.
4. **No task-type request-volume breakdown metric.** The dashboard's "Requests
   by task type" bar chart has no backing endpoint — Prometheus metrics
   exist per-model and per-department, not per-task-type in an
   easily-queryable aggregate form today. Needs either a new PromQL query
   (if `task_type` is already a label on `llm_api_gateway_requests_total` —
   verify before assuming) or a new counter.
5. **No cloud-model live health/readiness signal.** For Ollama models, the
   Router does a real health probe before each dispatch. For cloud
   (`backend != "ollama"`) models, the Router **skips the health check
   entirely** and assumes healthy (see `intelligent_router/pipeline.py`
   Stage 3) — there's no equivalent of the mockup's `ready: true/false` per
   model for cloud backends. A model with a bad/missing API key will only
   surface as a failure at actual dispatch time (a request error), not as a
   dashboard "offline" indicator.
6. **`ModelRegisterRequest` doesn't enforce `api_key` for cloud backends.**
   The Pydantic schema allows registering a `backend="anthropic"` model with
   no `api_key` — it'll just fail at inference time instead of at
   registration time. Client-side validation (like the mockup already
   does) is currently the only guard.
7. **Two separate, differently-dynamic policy gates — don't conflate them
   in the UI.** `security_layer`'s coarse "can this identity call the
   platform at all" check (`ALLOWED_ROLES`, a hardcoded Python frozenset)
   excludes `viewer` entirely and is **not** backed by the
   `role_permissions` table at all — changing it requires a code change +
   `security_layer` restart, unlike the fine-grained (role, task_type)
   matrix in §2.2 (which is now live via `intelligent_router`'s TTL poll).
   If the admin UI ever wants to expose "why was this denied," it needs to
   distinguish these two failure modes — both currently surface as the same
   `{"error": "policy_denied", ...}` shape, just with a different response
   envelope (`security_layer`'s nests under `"detail"`; the Router's is
   flat) — that's the only client-visible signal separating them today.
8. **Injection scoring is binary, not graduated (§2.1.1).** `security_layer`'s
   injection scanner (`security_layer/injection.py`) returns either `1.0`
   (regex match → block) or `0.0` (no match → pass) — there is no partial
   score or confidence band. This means "flagged for potential injection"
   and "blocked for injection" are, today, literally the same event
   (`injection_detected` in `GET /portal/governance/summary`'s
   `blocked_by_reason`) — don't design a UI that implies a separate
   "flagged but not blocked" state exists yet.
9. **`security_layer`'s post-pipeline cannot block a response, only mask
   PII in it (§2.1.1).** `security_layer/pipeline.py::run_post_pipeline`
   has exactly one capability: PII masking on `response.content`. There is
   no response-side content-safety or injection check, and therefore no
   "responses blocked due to security/guardrail violations" metric to
   expose — confirmed by reading the pipeline code, not merely undocumented.
   If this capability is ever added, it would need its own audit event +
   `blocked_by_reason` key before it could show up in the governance panel.
10. **`api_gateway`'s own audit events never reach the Audit Store (§2.1.1).**
   `api_gateway/services/audit.py::emit_audit_event` only `print()`s to
   stdout — `auth_fail`, `auth_pass`, `rate_limited`, `request_received`,
   and `response_sent` are all invisible to `GET /portal/governance/summary`
   and `GET /portal/audit/events` alike. Only `security_layer` and
   `intelligent_router` actually POST to the Audit Store today. Any UI count
   of "401s" or "429s" needs a different data source (stdout log scraping,
   or a future fix to make `api_gateway` POST like the other two layers) —
   don't assume the Audit Store is a complete request log across all layers.

---

## 6. Quick endpoint index

| Method | Path | Purpose |
|---|---|---|
| POST | `/v1/chat/completions` | Real chat completion (API Gateway, per-user key) |
| GET | `/portal/chat/models` | Entitlement-annotated model list for Chat view |
| POST | `/portal/chat/completions` | Chat completion via Admin Portal proxy (uses portal's own key) |
| GET | `/portal/config` | `{grafana_url}` runtime config |
| GET | `/portal/metrics/summary` | Dashboard KPIs (live Prometheus rates; `null` fields with no Prometheus running) |
| GET | `/portal/governance/summary` | Blocked/reason/injection/PII/token/model-usage counts, from the Audit Store's real trail (§2.1.1) |
| GET/POST | `/portal/users/` | List / create users |
| GET/PATCH/DELETE | `/portal/users/{id}` | User detail / status update / deactivate |
| PATCH | `/portal/users/{id}/roles` | Replace a user's roles |
| POST/GET | `/portal/users/{id}/keys` | Generate / list a user's keys |
| DELETE | `/portal/users/{id}/keys/{key_id}` | Revoke a key |
| PATCH | `/portal/users/{id}/keys/{key_id}/models` | Replace a key's model entitlements |
| GET | `/portal/keys/` | Admin-wide key listing (owner joined in) |
| GET | `/portal/roles/` | List roles |
| GET/PATCH | `/portal/roles/{role}/permissions` | Read / edit (persisted, not yet live) task permissions |
| GET/POST | `/portal/models` | List / register models |
| PATCH | `/portal/models/{name}/status` | Change model lifecycle status |
| PATCH | `/portal/models/{name}/api-key` | Set/update a cloud model's provider key |
| GET | `/portal/audit/events` | Time-windowed audit event list |
| GET | `/portal/audit/requests/{request_id}` | Audit trail for one request |

---

## 7. Existing `portal_ui` reference implementation — status

`portal_ui/` (the React/Vite app already in this repo) is a **partial**
prior implementation, not the target UI — the two HTML mockups are the
design source of truth going forward. It's useful as a working reference for
the `fetch`/`handleResponse<T>` client pattern (`portal_ui/src/api/portalClient.ts`)
and existing view structure, but it has NOT been updated for this pass's
backend changes:
- `ChatView.tsx` / `portalClient.ts::getChatModels()` still assume the old
  pre-filtered (entitled-only, no `entitled` field) `/portal/chat/models`
  response shape — it will render every returned model as equally usable,
  ignoring `entitled: false` rows, until updated.
- No client code exists yet for `/portal/models` register/api-key,
  `/portal/keys/` (admin-wide), or `/portal/roles/{role}/permissions` PATCH.
- `views/GovernanceView.tsx` (§2.1.1) **is** wired up to its real endpoint —
  unlike `TokenMetricsView.tsx`, which remains fully hardcoded demo data
  (`MODEL_TOKEN_DATA`/`RANGE_DATA` literals) and was left untouched rather
  than retrofitted, since its per-model bar-chart shape doesn't map cleanly
  onto the new endpoint's counts. If real per-model token consumption is
  ever needed there too, `getGovernanceSummary()` (`portalClient.ts`) already
  returns `token_usage`/`model_usage` — it just isn't wired into that view.

This is a known, intentional gap from this pass (backend-only scope) — not
an oversight to silently patch.
