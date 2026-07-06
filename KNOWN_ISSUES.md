# Known Issues — On-Prem LLM Platform POC
**Purpose:** Errors observed during local demo. Fix later.  
**Format:** Each issue has a severity, what you'll see, why it happens, and workaround.

---

## Severity Legend
- 🔴 **BLOCKER** — Demo feature completely broken
- 🟠 **SIGNIFICANT** — Feature partially broken, workaround exists
- 🟡 **MINOR** — Cosmetic / edge case, demo still works

---

## Issues

---

### ISSUE-001 — Metrics summary returns 502 (Prometheus not running locally)
**Severity:** 🟡 MINOR  
**Demo step affected:** Demo 10 (Admin Portal → `/metrics/summary`)  
**What you see:**
```json
{"error": "upstream_unavailable", "upstream": "prometheus"}
```
HTTP 502

**Why:** `GET http://localhost:8084/metrics/summary` queries a Prometheus instance at `http://localhost:9090`. Prometheus is only deployed in the K8s stack, not as part of `run-local.ps1`. There is no local Prometheus process.

**Impact:** All other Admin Portal endpoints (`/audit/events`, `/models`, `/playground/chat`, `/portal/health`) work fine. Only the metrics summary is affected.

**Workaround:** Skip `/metrics/summary` in local demo. Use the raw `/metrics` endpoints on each service instead (see Demo 12 in DEMO_PRESENTATION.md). In K8s deployment, Prometheus is deployed and this works correctly.

**Fix needed:** Add a `docker-compose.local.yml` entry for Prometheus that scrapes all local service ports. Ports to scrape: 8080, 8081, 8082, 8083, 8084, 8086, 8087, 9090, 9091, 9092, 9200, 5001.

---

### ISSUE-002 — Policy block demo (Demo 8) cannot be triggered via API Gateway — user block is hardcoded
**Severity:** 🟠 SIGNIFICANT  
**Demo step affected:** Demo 8 (Policy Block)  
**What you see:** Sending `"user": {"roles": ["intern"]}` in the request body has NO effect. The policy block cannot be triggered via `POST /v1/chat/completions`.

**Why:** `api_gateway/services/normalizer.py::build_imf()` **hardcodes** the user block:
```python
user=IMFUser(
    user_id="poc-user",
    department="poc",
    roles=["developer"],   # ← always "developer", ignores request body
    auth_method="api_key",
)
```
The OpenAI-compatible request body doesn't include a `user` field at all — it's an internal IMF concept. The gateway builds the IMF from scratch, always setting `roles=["developer"]`. Since `developer` is a valid role, the policy stage always passes.

**Impact:** The policy enforcement code in `security_layer/policy.py` is implemented correctly and works when called with a different role in the IMF directly. But it cannot be triggered from the public API without changing the normalizer.

**Fix needed (DEMO WORKAROUND):** To demo policy blocking live, call the **Security Layer directly** (bypass gateway):
```powershell
# Build a minimal IMF with an invalid role and POST directly to security layer
curl -X POST http://localhost:8081/security/check `
  -H "Content-Type: application/json" `
  -d "{\"request_id\": \"11111111-1111-4111-a111-111111111111\", \"trace_id\": \"11111111-1111-4111-a111-111111111111\", \"span_id\": \"\", \"timestamp_utc\": \"2026-07-05T00:00:00Z\", \"user\": {\"user_id\": \"baduser\", \"department\": \"unknown\", \"roles\": [\"intern\"], \"auth_method\": \"api_key\"}, \"request\": {\"messages\": [{\"role\": \"user\", \"content\": \"Hello\"}], \"model\": \"llama3.2:3b\", \"task_type\": \"chat\", \"stream\": false, \"max_tokens\": 100, \"temperature\": 0.7}, \"governance\": {\"injection_score\": 0.0, \"content_safety_passed\": true, \"pii_masked\": false, \"pii_fields_detected\": [], \"policy_decisions\": []}, \"routing\": {}, \"cache\": {}, \"response\": {}, \"metadata\": {}, \"extensions\": {}}"
```
Expected: HTTP 403 `{"error": "policy_denied"}`

**Code fix needed:** Update `normalizer.py` to read `user_id` and `roles` from the request body if provided, with `developer` as the default.

---

### ISSUE-003 — Agent Framework requires IMF body with `extensions.agentic: true` — not OpenAI format
**Severity:** 🟠 SIGNIFICANT  
**Demo step affected:** Demo 11 (Agent Framework)  
**What you see:** Sending a simple OpenAI-format `messages` body to `/agent/run` returns HTTP 400 or 422.

**Why:** `POST /agent/run` expects a **full IMF body** (not OpenAI format) AND requires `extensions.agentic: true`. Without that flag it returns:
```json
{"error": "validation_error", "field": "extensions.agentic", "message": "extensions.agentic must be true to invoke the agent"}
```

**Correct curl for agent demo:**
```powershell
curl -X POST http://localhost:8083/agent/run `
  -H "Content-Type: application/json" `
  -d "{\"request_id\": \"22222222-2222-4222-a222-222222222222\", \"trace_id\": \"22222222-2222-4222-a222-222222222222\", \"span_id\": \"\", \"timestamp_utc\": \"2026-07-05T00:00:00Z\", \"user\": {\"user_id\": \"poc-user\", \"department\": \"poc\", \"roles\": [\"developer\"], \"auth_method\": \"api_key\"}, \"request\": {\"messages\": [{\"role\": \"user\", \"content\": \"What is 15% of 2340?\"}], \"model\": \"llama3.2:3b\", \"task_type\": \"chat\", \"stream\": false, \"max_tokens\": 512, \"temperature\": 0.7}, \"governance\": {\"injection_score\": 0.0, \"content_safety_passed\": true, \"pii_masked\": false, \"pii_fields_detected\": [], \"policy_decisions\": []}, \"routing\": {}, \"cache\": {}, \"response\": {}, \"metadata\": {}, \"extensions\": {\"agentic\": true}}"
```

**Route confirmed:** `POST /agent/run` (verified from agent.py router).

---

### ISSUE-004 — Slow first response (CPU inference — expected behaviour, not a bug)
**Severity:** 🟡 MINOR (known limitation)  
**Demo step affected:** Demo 1, 3, 4, 7, 8, 11  
**What you see:** First LLM response takes 5–30 seconds depending on prompt length.

**Why:** `llama3.2:3b` is running on CPU via Ollama on an Intel i7 with no GPU acceleration. This is expected for the POC. The machine has Intel Iris Xe integrated graphics which cannot be used for LLM inference.

**Impact on demo:** Slows down any demo step that actually hits Ollama. Cache hit demos (Demo 6) are fast. Security block demos (Demo 3) never hit Ollama so are fast.

**Workaround:** 
1. Use short prompts — "What is Kubernetes in 2 sentences?" not "Explain Kubernetes in detail with examples."
2. Set expectations with team lead upfront: "First call is slow because CPU inference, second call is cached."
3. Pre-warm the cache before the demo by running Demo 1 query in advance.

---

### ISSUE-005 — Admin Portal `/portal/config` endpoint may not be implemented
**Severity:** 🟡 MINOR  
**Demo step affected:** Admin Portal config viewer  
**What you see:** HTTP 404 or 405 on `GET http://localhost:8084/config`

**Why:** The admin portal has a `routers/config.py` file but it may expose config about the portal's own settings, not a live platform-wide config view.

**Workaround:** Skip the config endpoint in the demo. Use audit, models, and playground endpoints instead — all confirmed implemented.

---

### ISSUE-006 — Audit events from security layer may be missing if audit_client has wrong signature
**Severity:** 🟡 MINOR  
**Demo step affected:** Demo 5 (Audit Trail) — security layer events  
**What you see:** Audit events from the security layer might show fewer events than expected. Router events will still appear.

**Why:** `security_layer/audit_client.py::post_audit_event` takes `(event, url, api_key)` — 3 args. The call in `pre_check.py` also passes 3 args. This is correctly matched. However if any code path was recently changed, the background task may fail silently (fire-and-forget means errors are only logged as WARNING, never raised).

**Status:** Believed fixed — signatures match. Logging will show any failures.

**Workaround:** The router audit events are independent and will still be present. The full trace is visible even if the security layer event is missing.

---

### ISSUE-007 — `spaCy en_core_web_sm` model must be downloaded before security layer starts
**Severity:** 🔴 BLOCKER (if not installed)  
**Demo step affected:** All demos — security layer won't start without it  
**What you see:** Security layer window crashes at startup with:
```
OSError: [E050] Can't find model 'en_core_web_sm'. It doesn't seem to be a Python package or a valid path to a data directory.
```

**Fix (run once):**
```powershell
python -m spacy download en_core_web_sm
```

**Status:** Should be done as part of one-time setup. If the security layer health check returns 503 or the window crashes, run this command and restart that service:
```powershell
.\scripts\run-local.ps1 -Service security_layer
```

---

### ISSUE-008 — Redis not running causes cache service to fail at startup
**Severity:** 🟡 MINOR (N/A — Redis installed locally)  
**Demo step affected:** Demo 6 (Cache), and all requests that pass through the router  
**What you see:** Cache service window crashes or `curl http://localhost:8086/health` returns connection refused.

**Status:** Redis is installed locally on port 6379. Just ensure it's running before starting services:
```powershell
redis-cli ping   # should return PONG
redis-server     # start it if not running
```

---

### ISSUE-009 — Rate limiting behaviour in Demo not explicitly shown
**Severity:** 🟡 MINOR  
**Demo step affected:** Rate limiting is implemented in `api_gateway/middleware/rate_limit.py` but not shown in the demo script.

**Note for team lead:** Rate limiting is implemented but to trigger it you'd need to send many requests in rapid succession. Threshold values depend on the config. Not worth triggering live as it complicates demo flow.

**Future action:** Add a PowerShell loop demo that sends 20+ requests quickly to show 429 responses.

---

### ISSUE-010 — Template injection pattern `{{...}}` demo command needs escaping
**Severity:** 🟡 MINOR  
**Demo step affected:** Demo 3 (Injection Block) — regex pattern variant  
**What you see:** If you try to demo the template injection pattern `{{ some payload }}`, the curly braces may cause issues in PowerShell string interpolation.

**Workaround:** Stick to the plain keyword pattern demo (`"Ignore previous instructions"`). That is reliable and clear for a live demo.

---

## Issues Added During Demo

> Add any new errors discovered live below this line:

| # | Timestamp | Description | Severity | Steps to Reproduce |
|---|---|---|---|---|
|   |           |             |          |                    |

---

## Fixed Issues

| Issue | Fixed In | Fix Description |
|---|---|---|
| model_registry port 5000 vs 5001 | Previous session | `model_registry/main.py` `__main__` port changed to 5001 |
| agent_framework PYTHONPATH missing | Previous session | `run-local.ps1` now sets `PYTHONPATH=$ROOT` for agent_framework |
| METRICS_PORT collision (3 services all trying :9091) | Previous session | Per-service MetricsPort in `run-local.ps1`: inference=9090, cache=9091, agent=9092 |
| Demo curl URLs using port 80 (nginx) | Previous session | All demo docs updated to use `:8080` |
| `-Service` filter error message broken | Previous session | `$allNames` captured before filter |
