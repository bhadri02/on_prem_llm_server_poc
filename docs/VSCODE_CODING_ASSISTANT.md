# VS Code Coding Assistant (Continue.dev integration)

This document covers connecting **Continue.dev** (an existing open-source VS
Code extension) to this platform's `api_gateway` so developers get an AI
coding assistant — chat + full agentic mode (multi-step file read/write) —
that runs entirely through the platform's existing governance pipeline
(RBAC, injection scanning, PII masking, content safety, audit trail). No new
service, no VS Code extension code — this is a client of the existing
OpenAI-compatible `/v1/chat/completions` endpoint, same as any other caller.

This is **Phase 1**: agent-mode via Continue's built-in text-based tool
fallback ("system message tools"), not native OpenAI/Anthropic
tool-calling. See [§5](#5-known-limitations--phase-2) for what that means in
practice and what a future Phase 2 would add.

---

## 1. Why this works with zero backend schema changes

Continue.dev's Agent mode needs two things: a chat-completions endpoint, and
a way to execute tools (read a file, write a file, run a command). Normally
"tool execution" implies the backend needs to speak OpenAI's `tools`/
`tool_calls` schema. Continue also ships a fallback that doesn't require
that: it embeds the available tools' descriptions directly into the system
prompt, asks the model to emit a specific text format when it wants to call
one, parses that text out of the plain assistant message, executes the tool
**locally inside VS Code**, and feeds the result back as the next user
turn — all using a completely ordinary chat endpoint.

That means every request from Continue still flows through the full
existing pipeline unchanged:

```
VS Code (Continue.dev)
  → api_gateway        (:8080)  Bearer-key auth, rate limit, normalize to IMF
  → security_layer      (:8081)  injection scan, content safety, PII mask
  → intelligent_router   (:8082)  task classify, model select, policy check
      → inference_adapter (:8087)  Ollama or Anthropic
  → security_layer (response PII mask)
  → api_gateway → VS Code (also writes the audit trail)
```

Nothing about RBAC, the security layer, the router, or auditing is bypassed
or weakened by this integration.

---

## 2. Issue a dedicated developer API key

Don't reuse the shared admin key or a personal login-session key for this —
issue each developer their own key, scoped and rate-limited for an agentic
workload (an Agent-mode task can fire many chat-completion calls in quick
succession as it works through a multi-step task — one per tool round-trip
— which is why the [rate-limit override](#21-why-a-higher-rate-limit) below
matters).

### 2.1 Why a higher rate limit

The platform's global default is `RATE_LIMIT_REQUESTS` per
`RATE_LIMIT_WINDOW_SECONDS` (see `local.env`) — plenty for a human sending
one chat message at a time, not enough for an agent that might make a dozen
tool round-trips while completing one task. `api_gateway`'s rate limiter now
honors a per-key `rate_limit_rpm` override (see `api_gateway/middleware/rate_limit.py`)
specifically for cases like this — set it well above the global default for
a coding-assistant key.

### 2.2 Create the user (if they don't already have one)

```
POST /portal/users/
Content-Type: application/json

{
  "username": "alice",
  "email": "alice@example.com",
  "department": "engineering",
  "roles": ["developer"]
}
```

`developer` clears both RBAC gates that matter for coding work: the
`security_layer` coarse gate (`ALLOWED_ROLES`) and the `intelligent_router`
`(role, task_type)` policy matrix for the `code`/`chat`/`reasoning` task
types (see the policy matrix seed / `GET /portal/policy/matrix` for the
current live rules).

### 2.3 Issue the key

```
POST /portal/users/{user_id}/keys
Content-Type: application/json

{
  "label": "vscode-continue-alice",
  "rate_limit_rpm": 300,
  "model_entitlements": []
}
```

- `rate_limit_rpm: 300` — well above the global default; adjust to taste.
- `model_entitlements: []` — empty means unrestricted (all models this
  user's roles are otherwise entitled to). Scope it to a specific list
  (e.g. `["claude-sonnet-4-5", "qwen2.5:3b"]`) if you want to restrict which
  models this key can reach.

**Response (`201`) — the raw key is shown exactly once, here:**

```json
{
  "key_id": "...",
  "key_prefix": "sk_live_ab12",
  "label": "vscode-continue-alice",
  "status": "active",
  "rate_limit_rpm": 300,
  "model_entitlements": [],
  "raw_key": "sk_live_ab12....................."
}
```

Copy `raw_key` now — it is hashed at rest and cannot be retrieved again
(the developer would need a new key issued if it's lost).

---

## 3. Continue.dev configuration

Install the **Continue** extension in VS Code, then edit its `config.yaml`
(Continue's config UI → "Open config file", or `~/.continue/config.yaml`):

```yaml
name: on-prem-coding-assistant
version: 0.0.1
schema: v1

models:
  - name: On-Prem Coding Assistant
    provider: openai
    model: claude-sonnet-4-5
    apiBase: http://<your-server-host>:8080/v1
    apiKey: sk_live_ab12.....................
    roles:
      - chat
      - edit
      - apply

  # Optional second entry pointed at the free local model instead — cheaper,
  # but a 3B model follows the text-based tool-call format far less
  # reliably (see §5). Useful for quick chat questions, not recommended for
  # Agent mode yet.
  - name: On-Prem Coding Assistant (local, cheap)
    provider: openai
    model: qwen2.5:3b
    apiBase: http://<your-server-host>:8080/v1
    apiKey: sk_live_ab12.....................
    roles:
      - chat
```

Notes:
- `provider: openai` + `apiBase` pointed at `api_gateway` is what makes this
  a standard OpenAI-compatible client call — no Continue plugin/extension
  code is needed on top of what already ships.
- `apiKey` is sent by Continue as `Authorization: Bearer <apiKey>` — this is
  exactly the header `api_gateway/middleware/auth.py` now accepts (added
  specifically so standard SDKs/tools don't need to special-case a custom
  header; the original `X-Api-Key` header keeps working unchanged for
  every other existing client).
- Deliberately **do not** set `capabilities: [tool_use]` on this model
  entry. Leaving it unset is what makes Continue use its system-message-tools
  fallback for Agent mode instead of native tool-calling — the platform
  doesn't implement the native `tools`/`tool_calls` schema yet (see §5).
- Model choice: `claude-sonnet-4-5` is recommended as the primary entry —
  it has a real ~200K-token context window (now accurately reflected in the
  Model Registry, see `models.json`), enough to hold multiple real files of
  context during an Agent-mode task, at the trade-off of a per-token cloud
  API cost. `qwen2.5:3b` is free/local but has a much smaller ~32K context
  window and, being a small model, follows the fallback's text-based tool
  format noticeably less reliably — expect it to work for straightforward
  chat, not for complex multi-step Agent-mode tasks.

For a shared/team rollout, point `apiBase` at your reverse-proxy host
(`docs/DEPLOYMENT.md`'s nginx setup) rather than a raw service port, and
give each developer their own key from §2 rather than sharing one.

---

## 4. Verifying the connection

Before opening VS Code, confirm the key and endpoint work with a plain curl
call — this isolates "is the platform reachable and does auth work" from
"is Continue configured correctly":

```bash
curl -s http://<your-server-host>:8080/v1/chat/completions \
  -H "Authorization: Bearer sk_live_ab12....................." \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-sonnet-4-5",
    "messages": [{"role": "user", "content": "Say hello in one sentence."}]
  }'
```

Expect a normal `200` OpenAI-shaped chat completion response. If this
works, open VS Code, select "On-Prem Coding Assistant" as the active model
in Continue's chat panel, and try:

1. A plain chat question — confirms the basic request path.
2. An Agent-mode task that reads a real file in your workspace and proposes
   an edit — confirms the tool-call fallback loop works end-to-end.

Then confirm the request actually went through the full governed pipeline
by pulling the audit trail for that request:

```
GET /portal/audit/events?limit=5
```

(or, if you have the specific `request_id` from a response header/log,
`GET /portal/audit/requests/{request_id}`) — you should see the same
`security_layer`/`intelligent_router` stage events as any other chat
request, not a shortcut path.

---

## 5. Known limitations / Phase 2

This phase intentionally ships full agentic capability via Continue's
fallback rather than building native tool-calling, to avoid a multi-service
schema change (`api_gateway`, the IMF, `intelligent_router`,
`inference_adapter`'s Ollama/Anthropic translation) until/unless it proves
necessary. Trade-offs to expect while on the fallback:

- **Text-based tool parsing is less reliable than native tool-calling**,
  especially against small local models — a model can occasionally emit a
  malformed tool-call block, or narrate a tool call in prose without using
  the expected format, which Continue then fails to parse. This is
  materially better with `claude-sonnet-4-5` than with `qwen2.5:3b`.
- **No inline "ghost text" autocomplete** — out of scope for this phase (its
  sub-300ms latency requirement doesn't fit the governed pipeline's
  per-hop overhead); Continue is configured for chat/agent panel use only.
- Every Agent-mode round-trip is a separate `/v1/chat/completions` call
  through the full pipeline (auth → rate limit → injection scan → content
  safety → PII mask → classify → route → infer → PII mask → audit) — expect
  noticeably higher latency per tool step than a raw model API call would
  have, by design (this is the governance trade-off, not a bug).

If fallback quality turns out to be insufficient in practice, the next step
is native tool-calling, which requires (not started in this phase):

- `api_gateway/schemas/openai.py`: add `tools`/`tool_choice` to
  `OpenAIChatRequest`; add `name`/`tool_calls`/`tool_call_id` to
  `OpenAIMessage`; add real `tool_calls` + `finish_reason="tool_calls"` to
  `OpenAIChatResponse`.
- Mirror those same fields into **every** service's own copy of the IMF
  Pydantic models on the request's path (`api_gateway`, `security_layer`,
  `intelligent_router`, `inference_adapter`) — a field missing from any one
  service's schema is silently stripped by FastAPI at that service's
  inbound-parse boundary and never reaches the next hop (see `CLAUDE.md`'s
  IMF section for this general hazard).
- `inference_adapter/services/imf_mapper.py`: translate the unified `tools`
  array into Ollama's format and into Anthropic's `tool_use`/`tool_result`
  content-block format separately — these are structurally different.
- Confirm `security_layer`'s PII/injection scanning handles `tool_calls`
  arguments and `role="tool"` message content correctly.
- Only then set `capabilities: [tool_use]` in Continue's config to switch
  it from the fallback to native mode.
