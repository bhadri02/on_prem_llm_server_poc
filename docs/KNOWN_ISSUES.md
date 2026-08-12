# Known Issues & Limitations

A consolidated list of real, verified problems in the current on-prem LLM
platform — found through direct testing and code inspection, not
speculation. Organized by category. Each item notes whether it's an open
problem or something already fixed, and why it matters.

---

## 1. Performance / Hardware

### 1.1 Inference is very slow (~3 tokens/sec)
`llama3.2:3b` runs 100% CPU on this hardware (confirmed via `ollama ps`) —
no GPU acceleration at all. Real observed response times ranged from ~10
seconds for a one-word answer up to 170+ seconds for a normal chat/agent
message. This is the root cause behind several of the other issues below.

### 1.2 No GPU acceleration available
The laptop's GPU is an **Intel Iris Xe** integrated GPU — no dedicated
VRAM (borrows from system RAM), and more importantly, `ollama/ollama`'s
standard Docker image only has acceleration backends for **NVIDIA CUDA**
and **AMD ROCm**. There is no Intel iGPU/SYCL support in the image being
used, so this GPU cannot be leveraged for inference regardless of driver
setup.

### 1.3 "Model doesn't remember the conversation" — clarified
**Verified this is not a missing-history bug.** A live capture of a real
Continue.dev follow-up request showed the full conversation was actually
sent: `system (6,531 chars) → user (2 chars) → assistant (131 chars) →
user (26 chars)` — 4 real messages, correctly accumulated.

The real problem: the **system/tool-harness prompt is over 50x larger
than the actual conversation** (6,531 chars of boilerplate vs. ~159 chars
of real exchange). A 3B CPU-only model is well known to struggle with
"needle in a haystack" recall once a prompt is dominated by unrelated
boilerplate text — so it's not that context is missing, it's that the
model can't reliably use the context it received. This is a **model
capability + prompt-composition problem**, not an architecture bug on our
side (confirmed via code inspection: `normalizer.py`, `intelligent_router`,
and `inference_adapter` all pass the full message array through
unmodified, no truncation anywhere in the pipeline).

**Likely fix:** switch to a model with genuine long-context capability
(e.g. `claude-sonnet-4-5`, ~200K context) — not a backend code change.

### 1.4 No real token-by-token streaming
Even after fixing the SSE framing bug (see §5 "Fixed tonight"), nothing in
this pipeline generates tokens incrementally — Ollama and Anthropic
responses are both received in full before being sent back. Combined with
§1.1, this means a 2–3 minute wait with **zero visual feedback** during
generation, which makes the slowness feel considerably worse than it would
with real incremental streaming.

---

## 2. Agentic / Tool-Calling

### 2.1 No native tool-calling schema anywhere in the pipeline
Not just "relying on Continue's own tools" — there is **zero** `tools`/
`tool_calls` support anywhere: `api_gateway/schemas/openai.py`, the IMF
schema, `intelligent_router`, and `inference_adapter` all lack it entirely.
Any client wanting real OpenAI-style tool-calling (not just Continue's
client-side text-parsing fallback, which only works because Continue has a
"system message tools" fallback mode) cannot use this platform for that
today. Building real tool-calling support is a multi-service schema change
(documented as "Phase 2" in `docs/VSCODE_CODING_ASSISTANT.md`), deferred.

### 2.2 Small models are unreliable under Continue/Copilot's tool harnesses
Observed directly: `llama3.2:3b` generated a malformed tool-call
(`TOOL_NAME: create_new_file` missing the required `contents` argument),
hallucinated the literal placeholder `tool_name` instead of a real tool
name, and separately refused a plain "tell me a joke" request with "Sorry,
I can't assist with that" after being confused by a large, contradictory
coding-agent harness. This is a small-model quality limitation, not
something fixable in our pipeline — a larger/cloud model is expected to
handle this far more reliably.

---

## 3. Security / Governance

### 3.1 No login/auth on most admin endpoints (significant)
`admin_portal`'s `/portal/*` management routes — create users, issue API
keys, change roles, edit the RBAC policy matrix — have **zero
browser-facing authentication**. Anyone who can reach the admin portal's
port/proxy can perform all of this without logging in. Only
`/portal/keys/resolve` (service-to-service) is guarded. This is the
single biggest gap standing between this platform and real
"enterprise-ready" status.

### 3.2 `viewer` role is permanently hardcoded out of the platform
`security_layer`'s coarse gate (`ALLOWED_ROLES`) is a Python frozenset in
code, not backed by any database table — unlike the rest of the RBAC
policy matrix, which is now live-editable via the admin UI with no
restart needed. Changing what `viewer` can do requires a code change and a
`security_layer` restart, full stop.

### 3.3 Content-safety blocklist can false-positive on legitimate content
`security_layer/content_safety.py`'s `BLOCKLIST` includes words like
"hack" and "exploit" that appear routinely in legitimate security-related
code, comments, or conversation. Known, documented, intentionally left
as a judgment call rather than fixed.

---

## 4. Data Architecture

### 4.1 `model_matrix.yaml` vs. Model Registry — two disconnected sources of truth
Registering a new model through the admin UI (`POST /portal/models`) does
**not** make it routable. The Router dispatches strictly from the static
`model_matrix.yaml` file loaded once at startup — a matching hand-edit and
a Router restart are required separately. (This is the same *shape* of bug
already fixed for the policy matrix — see §5 — but not yet fixed here.)

### 4.2 Audit Store and Model Registry are SQLite / a JSON file
Not a real production database — no replication, no formal backup
process. Fine at current scale, but a real durability/scale concern if
this is positioned as an enterprise deployment.

### 4.3 Chat UI has no conversation persistence
Restarting `admin_portal` or reloading the Portal UI's Chat view loses all
prior turns — there's no database row backing chat history. (Distinct
from the Continue/Copilot context issue in §1.3 — this is specifically the
built-in Portal UI chat.)

---

## 5. Deployment

### 5.1 Kubernetes/Helm charts are stale and would crash-loop as-is
They predate the RBAC/Postgres/policy-matrix/governance work documented
elsewhere — missing required env vars, no Postgres dependency in the chart
tree, no `portal_ui` chart at all. `docs/DEPLOYMENT.md`'s Docker Compose
path (what's actually running) is the only currently-working deployment
path.

### 5.2 Governance dashboard's *rate* metrics need a live Prometheus server
`GET /portal/metrics/summary`'s rate fields are `null` whenever Prometheus
isn't reachable (the current setup has no Prometheus running). The
separate audit-trail-based governance summary built this session doesn't
have this dependency, so this is a partial gap, not a total one.

---

## Fixed this session (for context — no longer problems)

These were real, verified bugs found and fixed during this session's work,
listed here only so they aren't mistaken for still-open issues:

- **Rate limiting was in-memory and global-only** — now per-API-key,
  Redis-backed (correct across multiple replicas), with an admin UI to set
  and change each key's limit live.
- **`api_gateway`'s own audit events (auth_fail, auth_pass, rate_limited,
  request_received, response_sent) never reached the durable Audit Store**
  — only existed in local stdout, invisible to the Governance dashboard.
  Now POSTed to `audit_store` like every other layer, correctly correlated
  by `request_id`.
- **Streaming (`stream: true`) silently returned an empty body** due to a
  premature-connection-close bug, and even when working would have leaked
  internal IMF fields (including `user.key_id`) to the client. Fixed to
  return a spec-compliant single-chunk SSE response.
- **`OpenAIMessage.content` only accepted a plain string**, rejecting the
  multipart content-parts array format real OpenAI clients (Continue,
  Copilot) send once conversation history builds up — this was the actual
  cause of an earlier `400 Bad Request` in Continue.
- **Injection-pattern regexes false-positived on ordinary source code**
  (Jinja/Vue/Go template `{{ }}` syntax, any PHP file's opening tag) —
  tightened to require actual instruction-like keywords.
- **Task misclassification + cache collisions caused by GitHub Copilot
  Chat's agent-mode harness**: a near-constant `<context>`/
  `<reminderinstructions>` wrapper block was being treated as "the current
  turn" for both cache-key derivation and task-type classification,
  causing completely different questions to return the same cached answer
  and to be misclassified as `task_type="code"` regardless of topic. Both
  fixed to exclude known harness-wrapper content.
- **`Authorization: Bearer <key>` wasn't accepted**, only the platform's
  original custom `X-Api-Key` header — added so standard OpenAI-compatible
  clients/SDKs work without special-casing.
