---
inclusion: manual
---

# Layer 2 — Security and Governance (POC)

> Load this file when working on the Security & Governance layer: `#02-layer-security-governance`
> **Scope:** Proof-of-Concept — demonstrate the pipeline, not production hardening.

---

## POC Goal

Show that every request passes through governance checks before reaching inference. For POC, implement lightweight rule-based checks that prove the pipeline works. Full ML classifiers and OPA are replaced with simple heuristic alternatives.

---

## Components to Build (POC Scope)

| Component | Technology | POC Simplification |
|---|---|---|
| Policy Check | Simple role/department dict lookup | No OPA; hardcoded allow-list in config |
| Prompt Injection Detector | Keyword/regex scanner | No ML model; regex pattern list |
| PII Detector & Masker | Microsoft Presidio (lite) | Run on CPU; detect EMAIL + PHONE only |
| Content Safety Filter | Keyword blocklist | No LlamaGuard; simple word filter |
| Audit Logger | Stdout JSON logging | No Elasticsearch; print audit record |
| Human Approval Workflow | **Skip for POC** | Not implemented |
| Jailbreak Classifier | Regex-based heuristic | Not a trained ML model |

---

## Processing Sequence (POC)

### Pre-Generation

```
Receive IMF from API Gateway (HTTP JSON)
  │
  ├─ [1] Keyword-based Injection Scan
  │       → Match prompt against injection pattern list
  │       → BLOCK if match found → return 400
  │
  ├─ [2] Simple Content Safety Check
  │       → Match against blocked-word list
  │       → BLOCK if match → return 400
  │
  ├─ [3] PII Detection (Presidio)
  │       → Detect EMAIL_ADDRESS, PHONE_NUMBER in messages
  │       → Mask detected entities with [REDACTED_<TYPE>]
  │       → Update IMF messages with masked version
  │
  ├─ [4] Policy Check
  │       → Verify user.roles contains at least "developer"
  │       → DENY if not → return 403
  │
  └─ Log pre-audit record to stdout
  │
  Forward enriched IMF to Router Layer
```

### Post-Generation

```
Receive IMF with response from Router
  │
  ├─ [1] PII Scan on response content (Presidio)
  │       → Mask any leaked PII in response
  │
  └─ Log post-audit record to stdout
  │
  Return enriched IMF to API Gateway
```

---

## Injection Pattern List (POC)

Store in a config file `injection_patterns.yaml`:

```yaml
patterns:
  - "ignore previous instructions"
  - "ignore all instructions"
  - "you are now"
  - "disregard your"
  - "forget your training"
  - "act as if"
  - "pretend you are"
  - "\\{\\{.*\\}\\}"       # template injection
  - "<\\?.*\\?>"            # code injection
```

Match case-insensitive across all message content fields.

---

## PII Configuration (POC)

Use Presidio `AnalyzerEngine` with default English recognizers:

```python
# Entities to detect for POC
POC_ENTITIES = ["EMAIL_ADDRESS", "PHONE_NUMBER", "PERSON"]

# Mode: mask only
MASK_CHAR = "[REDACTED_{entity_type}]"
```

Run Presidio on CPU — no GPU needed.

---

## IMF Fields This Layer Reads and Writes

**Reads:**
- `user.roles` — for policy check
- `request.messages` — for scan and PII masking

**Writes (pre-gen):**
```json
{
  "governance": {
    "pii_masked": true,
    "pii_fields_detected": ["EMAIL_ADDRESS"],
    "injection_score": 1.0,
    "content_safety_passed": true,
    "human_approval_required": false,
    "human_approval_status": "not_required",
    "policy_decisions": ["role_check_pass"]
  },
  "request": {
    "messages": "[ PII-masked version ]"
  }
}
```

**Writes (post-gen):**
```json
{
  "response": {
    "content": "[ PII-masked version of model response ]"
  }
}
```

---

## Audit Logging (POC)

Print audit records as JSON to stdout. No write to Elasticsearch.

```json
{
  "audit_id": "uuid",
  "request_id": "uuid",
  "timestamp_utc": "ISO-8601",
  "user_id": "string",
  "layer": "security",
  "event_type": "request_pre_audit | security_block | pii_masked_request | response_post_audit",
  "outcome": "pass | block",
  "pii_actions": []
}
```

---

## Helm Chart: `llm-platform/charts/security-layer/`

```yaml
# values.yaml (POC)
replicaCount: 1

image:
  repository: registry.local/security-layer
  tag: ""
  pullPolicy: IfNotPresent

service:
  type: ClusterIP
  port: 8081

env:
  LOG_LEVEL: "INFO"
  DOWNSTREAM_ROUTER_URL: "http://router:8082"
  PII_ENABLED: "true"
  INJECTION_PATTERNS_PATH: "/config/injection_patterns.yaml"

resources:
  requests:
    cpu: "200m"
    memory: "512Mi"
  limits:
    cpu: "1"
    memory: "1Gi"

autoscaling:
  enabled: false

vault:
  enabled: false
```

---

## Observability (POC)

- Structured JSON logs to stdout.
- Log fields per request: `request_id`, `injection_detected`, `pii_entities_found`, `outcome`, `latency_ms`.
- No OTel tracing for POC.

---

## POC Non-Goals (Explicitly Out of Scope)

- OPA / Rego policy engine
- LlamaGuard (self-hosted ML content moderation)
- Trained injection / jailbreak ML classifiers
- Human approval workflow and review queue
- Hallucination detector
- IP allowlist enforcement
- gRPC transport / mTLS
- Elasticsearch audit storage
