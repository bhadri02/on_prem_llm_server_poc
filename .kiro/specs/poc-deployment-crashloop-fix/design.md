# POC Deployment CrashLoopBackOff Fix — Bugfix Design

## Overview

Three Kubernetes services (`cache-service`, `security-layer`, `inference-adapter`) enter a
persistent CrashLoopBackOff state due to eight interrelated defects. The root causes fall into
four independent categories:

1. **P7** — Docker Desktop's registry mirror intercepts rebuilt image pulls, delivering a stale
   492 MB manifest instead of the current 2.09 GB image.
2. **P1/P2** — The stale image does not contain the `all-MiniLM-L6-v2` sentence-transformers
   model, causing the cache-service to attempt an outbound download that fails because the
   cluster has no internet egress.
3. **P4** — The security-layer liveness probe budget (≈ 165 s) is shorter than the worst-case
   Presidio + spaCy load time (2–3 min), causing Kubernetes to kill the container before it
   becomes healthy.
4. **P8/P5** — `initJob.enabled: false` in `values-poc-local.yaml` means `llama3.2:3b` is
   never pulled into Ollama. The inference-adapter `/health` endpoint returns 503 until the
   model is present, so the readiness probe never passes.

P3 (Redis DNS NXDOMAIN on first boot) and P6 (stale pods from prior ReplicaSets) are
downstream symptoms that resolve automatically once the four root-cause categories are fixed.

The fix strategy is deliberately minimal: bypass the registry mirror for `localhost:5000`,
rebuild and re-push the correct cache-service image, increase the security-layer liveness
probe budget, switch the inference-adapter liveness probe from HTTP to TCP, and enable the
Ollama init Job. No application code changes are required.


## Glossary

- **Bug_Condition (C)**: The specific input state or configuration that triggers a crash or
  permanent failure — each of the four root-cause categories has its own C function defined
  in `bugfix.md`.
- **Property (P)**: The desired observable behavior after the fix is applied — pods reach
  `Running/Ready` state and stay there.
- **Preservation**: Existing runtime behavior of all healthy services and all non-rebuild
  image pulls must remain unchanged after each targeted fix is applied.
- **CrashLoopBackOff**: Kubernetes pod failure state where a container repeatedly exits and
  Kubernetes imposes an exponential back-off before restarting it.
- **registry-mirror**: The Docker Desktop `registry-mirror` configured in the Docker daemon,
  currently routing `localhost:5000` pulls through a mirror that caches the old manifest.
- **initJob**: A Helm `post-install,post-upgrade` Kubernetes Job defined in
  `llm-platform/charts/inference-ollama/templates/init-job.yaml` that calls
  `POST /api/pull` on the Ollama endpoint for each model in `models.preload`.
- **liveness probe budget**: The wall-clock window Kubernetes allows before concluding a
  container is dead — computed as `initialDelaySeconds + failureThreshold × periodSeconds`.
- **isBugCondition_P7**: Pseudocode function from `bugfix.md` that returns true when an image
  is rebuilt, the registry mirror is active, and the pull target is `localhost:5000`.
- **isBugCondition_P1P2**: Returns true when the running container image is < 1 GB and the
  embedding model is absent from disk.
- **isBugCondition_P4**: Returns true when `load_time_seconds > initialDelaySeconds +
  failureThreshold × periodSeconds` for the security-layer liveness probe.
- **isBugCondition_P5P8**: Returns true when `model_present_in_ollama = false`.


## Bug Details

### P7 — Registry Mirror Intercepts Rebuilt Image Pulls

The Docker Desktop daemon is configured with a `registry-mirror` entry that sits in front of
`localhost:5000`. After a new `cache-service:poc-v2` image (2.09 GB) was built and pushed,
containerd's mirror resolver served the previously cached manifest (492 MB) instead of
fetching the updated digest from the local registry. The result is that every new pod
scheduled for `localhost:5000/cache-service:poc-v2` runs the wrong image.

**Formal Specification (from bugfix.md):**
```
FUNCTION isBugCondition_P7(pull_event)
  INPUT: pull_event { registry: string, tag: string, mirror_active: bool, image_rebuilt: bool }
  OUTPUT: boolean
  RETURN pull_event.registry = "localhost:5000"
     AND pull_event.mirror_active = true
     AND pull_event.image_rebuilt = true
END FUNCTION
```

**Concrete Examples:**
- `docker pull localhost:5000/cache-service:poc-v2` from a pod spec → mirror returns 492 MB
  stale digest; pod runs image without `all-MiniLM-L6-v2`.
- Re-deploying with `helm upgrade` after rebuilding the image → still pulls stale manifest
  because `pullPolicy: IfNotPresent` and containerd mirror cache is not invalidated.
- `docker pull localhost:5000/redis:7-alpine` (not rebuilt) → mirror serves correct manifest;
  no bug triggered (isBugCondition_P7 = false because `image_rebuilt = false`).

### P1/P2 — Cache Service Missing Baked Embedding Model

When the stale 492 MB image runs, it does not contain the sentence-transformers model that
was pre-downloaded by the `RUN python -c "from sentence_transformers import SentenceTransformer;
SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"` layer in the current
`cache_service/Dockerfile`. On startup, `sentence_transformers` attempts to download the model
from `huggingface.co`. The cluster has no outbound internet egress, so DNS resolution fails and
the application raises an exception before FastAPI finishes loading, keeping port 8086 closed
and causing the liveness probe to fail.

**Formal Specification (from bugfix.md):**
```
FUNCTION isBugCondition_P1P2(container_start)
  INPUT: container_start { image_size_bytes: int, model_on_disk: bool }
  OUTPUT: boolean
  RETURN container_start.image_size_bytes < 1_000_000_000
     AND container_start.model_on_disk = false
END FUNCTION
```

**Concrete Examples:**
- Container starts with 492 MB image → `model_on_disk = false` → attempts
  `huggingface.co` download → `socket.gaierror: [Errno -2] Name or service not known` →
  uvicorn never binds port 8086 → liveness probe fails → CrashLoopBackOff.
- Container starts with 2.09 GB image (correct rebuild) → `model_on_disk = true` →
  `SentenceTransformer('all-MiniLM-L6-v2')` loads from `/root/.cache/...` in < 10 s →
  isBugCondition_P1P2 = false.


### P4 — Security-Layer Liveness Probe Kills Pod During Model Load

The `security-layer` Helm chart (`values.yaml`) currently configures:
```yaml
livenessProbe:
  tcpSocket:
    port: 8081
  initialDelaySeconds: 30
  periodSeconds: 15
  failureThreshold: 10
```
Effective budget = 30 + (10 × 15) = **180 s**. Presidio's `AnalyzerEngine` with spaCy
`en_core_web_sm` takes 2–3 minutes to load on the first container start. On a slow
Docker Desktop node with memory pressure the load can exceed 180 s, allowing the liveness
probe to fire and kill the container before it becomes healthy, producing CrashLoopBackOff.

**Formal Specification (from bugfix.md):**
```
FUNCTION isBugCondition_P4(probe_config, load_time_seconds)
  INPUT: probe_config { initialDelaySeconds: int, failureThreshold: int, periodSeconds: int }
         load_time_seconds: int
  OUTPUT: boolean
  budget ← probe_config.initialDelaySeconds
         + (probe_config.failureThreshold * probe_config.periodSeconds)
  RETURN load_time_seconds > budget
END FUNCTION
```

**Concrete Examples:**
- `load_time_seconds = 185`, budget = 180 → isBugCondition_P4 = true → container killed.
- `load_time_seconds = 175`, budget = 180 → isBugCondition_P4 = false → container survives.
- After fix with budget = 360 s: `load_time_seconds = 185` → isBugCondition_P4 = false.

### P5/P8 — Ollama Has No Model; Adapter Readiness Probe Permanently Fails

`values-poc-local.yaml` sets `initJob.enabled: false` under the `"inference-ollama"` sub-chart
key. This suppresses the `post-install,post-upgrade` Job in `init-job.yaml` that calls
`POST http://inference-ollama:11434/api/pull` for `llama3.2:3b`. Ollama starts successfully
but has no models. The inference-adapter's `/health` endpoint queries Ollama for model
availability and returns HTTP 503 when the model is absent. The readiness probe
(`httpGet /health`, `initialDelaySeconds: 15`, `periodSeconds: 15`) receives 503 on every
check, so the adapter pod is never marked Ready and all inference traffic is blocked.

The `adapter-deployment.yaml` template already uses `tcpSocket` for liveness (correctly), but
the `values.yaml` `adapter.readinessProbe` stanza still uses `httpGet /health`, which is the
correct behavior for readiness — it should only become ready when Ollama has the model.

**Formal Specification (from bugfix.md):**
```
FUNCTION isBugCondition_P5P8(health_check)
  INPUT: health_check { model_present_in_ollama: bool }
  OUTPUT: boolean
  RETURN health_check.model_present_in_ollama = false
END FUNCTION
```

**Concrete Examples:**
- `initJob.enabled: false` → `ollama list` returns empty → adapter `/health` → 503 →
  readiness probe fails indefinitely → pod stuck at `0/1 Running`.
- `initJob.enabled: true` → init Job pulls `llama3.2:3b` (~2.3 GB) → adapter `/health` →
  200 → pod becomes Ready → isBugCondition_P5P8 = false.
- Manual workaround: `kubectl exec … ollama pull llama3.2:3b` → same result.


## Expected Behavior

### Preservation Requirements

These behaviors must remain completely unchanged after every targeted fix is applied:

**Unchanged Behaviors:**
- All service images that have NOT been rebuilt continue to be pulled and served correctly
  from `localhost:5000` without disruption (requirement 3.1).
- The cache-service's semantic similarity lookup against Redis using `all-MiniLM-L6-v2` with
  a 0.90 cosine threshold continues to operate identically (requirement 3.2).
- The security-layer's PII detection (`EMAIL_ADDRESS`, `PERSON`, etc.) and prompt injection
  blocking continue to work once the service is fully started (requirements 3.3, 3.4).
- The inference-adapter's forwarding of valid requests to Ollama at
  `http://inference-ollama:11434` and return of IMF-formatted responses continues
  unchanged (requirement 3.5).
- Unrecognised model name handling in the inference-adapter continues to return an error
  without crashing (requirement 3.6).
- Audit events (`request_received`, `auth_pass`, `cache_hit`/`cache_miss`,
  `inference_complete`, `response_sent`) continue to be written at each layer (requirement 3.7).
- Cache hit behavior (`lookup_hit: true`, cosine ≥ 0.90) continues unchanged (requirement 3.8).
- Redis unavailability fallback to inference path continues unchanged (requirement 3.9).
- Prometheus `/metrics` endpoint on port 9090 continues to expose mandatory metrics for all
  affected services (requirement 3.10).

**Scope:**
All inputs and configuration that do NOT match any of the four bug condition functions
(`isBugCondition_P7`, `isBugCondition_P1P2`, `isBugCondition_P4`, `isBugCondition_P5P8`)
must be completely unaffected by the changes in this fix. This includes:
- Image pulls for services not in the rebuild set (api-gateway, router, agent-framework, etc.)
- Probe configurations for services not listed in this fix
- Application-level request/response logic in all three affected services

**Note:** The expected correct behavior for each bug condition (what the fixed system SHALL do)
is defined in the Correctness Properties section below.


## Hypothesized Root Cause

### RC-1: Docker Desktop registry-mirror caches localhost:5000 manifests

Docker Desktop's containerd daemon is configured with a `registry-mirror` entry that intercepts
pulls from `localhost:5000`. After a new image is pushed, the mirror's manifest cache is not
automatically invalidated — it continues to serve the digest it recorded at the time of the
first pull. Since `pullPolicy: IfNotPresent` is set, and the cached manifest resolves to a
layer set that is already present in the local layer store, containerd considers the image
up-to-date without fetching the new layers.

The fix is to add `localhost:5000` to the `insecure-registries` list and explicitly exclude it
from the mirror routing in Docker Desktop's `daemon.json` so containerd resolves it directly.

### RC-2: cache_service/Dockerfile RUN layer that pre-bakes the model was not present in
the image that was pushed as poc-v2 before the Dockerfile was updated

The `RUN python -c "from sentence_transformers import SentenceTransformer;
SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"` layer was added to
`cache_service/Dockerfile` after the initial `poc-v2` tag was pushed. The mirror cached
the pre-update manifest. Until the image is rebuilt and pushed with the model layer AND the
mirror bypass is in place, any pod pull gets the model-less image.

The fix requires both RC-1 (mirror bypass) AND a `docker build` + `docker push` with the
current `cache_service/Dockerfile`, in that order.

### RC-3: Security-layer liveness probe budget is too tight for Docker Desktop on constrained hardware

On an Intel i7-1355U with integrated graphics running Docker Desktop, the node may experience
memory pressure during the initial Helm deployment when all services start simultaneously.
spaCy model loading is single-threaded and CPU-bound; under memory pressure the OS may page
memory during the `nlp = spacy.load('en_core_web_sm')` call, extending it beyond 180 s.

The fix is to increase `initialDelaySeconds` to 120 and `failureThreshold` to 16 (budget =
120 + 16 × 15 = **360 s**), giving a 6-minute window that comfortably covers worst-case load.

### RC-4: initJob deliberately disabled in values-poc-local.yaml with a manual pull comment

The `values-poc-local.yaml` file contains an explicit comment:
```yaml
# After Ollama pod is Running, pull manually:
#   kubectl exec -n llm-poc deploy/llm-poc-inference-ollama -- ollama pull llama3.2:3b
initJob:
  enabled: false
```
This was intentional to avoid a Job failure on first deploy before Ollama was Running, but it
means every fresh `helm upgrade --install` leaves Ollama empty, and the manual pull step is
easy to forget during a demo setup. The fix is to set `initJob.enabled: true` and rely on the
Job's `backoffLimit: 2` retry logic to handle the race with Ollama's startup.


## Correctness Properties

Property 1: Bug Condition — Registry Mirror Bypass (P7)

_For any_ image pull event where `isBugCondition_P7` returns true (registry is
`localhost:5000`, mirror is active, image was rebuilt), the fixed pull mechanism SHALL pull
the image directly from `localhost:5000`, resolving to the current registry digest
(≥ 2 GB for cache-service), not the stale mirror-cached manifest.

**Validates: Requirements 2.1, 2.2**

---

Property 2: Bug Condition — Correct Image Contains Baked Model (P1/P2)

_For any_ container start event where `isBugCondition_P1P2` returns true (image < 1 GB,
model absent from disk), the fixed deployment SHALL instead run the current 2.09 GB image
where `all-MiniLM-L6-v2` is pre-downloaded on disk, resulting in `model_loaded_from_disk
= true`, `outbound_huggingface_call = false`, and `startup_complete = true`.

**Validates: Requirements 2.2, 2.3, 2.4**

---

Property 3: Bug Condition — Liveness Probe Budget Covers Worst-Case Load Time (P4)

_For any_ probe configuration where `isBugCondition_P4` returns true with
`load_time_seconds ≤ 180` (i.e., any load time the original budget would have killed), the
fixed probe configuration SHALL have a budget greater than 180 s (specifically ≥ 360 s) and
SHALL use `tcpSocket` on port 8081, so the container is NOT killed before loading completes.

**Validates: Requirements 2.6, 2.7**

---

Property 4: Bug Condition — Readiness Probe Passes Once Model Present (P5/P8)

_For any_ health check where `isBugCondition_P5P8` returns true (model absent) and then
the init Job or manual pull makes the model present, the fixed adapter `/health` endpoint
SHALL return HTTP 200, the readiness probe SHALL pass, and the pod SHALL transition to
`Ready = true`.

**Validates: Requirements 2.8, 2.9, 2.10**

---

Property 5: Preservation — Non-Rebuild Image Pulls Unaffected

_For any_ image pull event where `isBugCondition_P7` returns false (image not rebuilt, or
registry is not `localhost:5000`), the fixed pull mechanism SHALL produce exactly the same
result as the original mechanism — same manifest, same layers, same running container.

**Validates: Requirements 3.1**

---

Property 6: Preservation — Cache Service Behavior Unchanged After Fix

_For any_ cache-service request where the service is healthy (correct image running, Redis
available), the fixed cache-service SHALL produce the same lookup result as the pre-bug
correct version: semantic similarity ≥ 0.90 returns `lookup_hit: true`, < 0.90 returns
`lookup_hit: false`, Redis unavailability falls back to inference path.

**Validates: Requirements 3.2, 3.8, 3.9**

---

Property 7: Preservation — Security-Layer Request Decisions Unchanged After Probe Fix

_For any_ security-layer request where the service is fully started (spaCy + Presidio
loaded), the fixed probe configuration SHALL not alter the security decision logic — PII
detection, injection blocking, and pass-through behavior remain identical to pre-fix behavior.

**Validates: Requirements 3.3, 3.4**

---

Property 8: Preservation — Inference Adapter Response Behavior Unchanged After Probe Fix

_For any_ inference-adapter request where `llama3.2:3b` is loaded in Ollama, the fixed
probe configuration SHALL not alter the adapter's request forwarding or response translation
logic — valid requests continue to return IMF-formatted responses, invalid model names
continue to return errors without crashing.

**Validates: Requirements 3.5, 3.6**


## Fix Implementation

### Changes Required

Assuming the root cause analysis above is correct, the following changes are required in
dependency order. Steps 1 and 2 are prerequisites for step 3; step 4 is independent.

---

#### Fix 1 — Bypass Docker Desktop Registry Mirror for localhost:5000

**File**: Docker Desktop daemon settings  
**Location**: Docker Desktop → Settings → Docker Engine (JSON editor)

**Specific Changes:**

Add `localhost:5000` to `insecure-registries` and configure a `registry-mirrors` exclusion
so containerd resolves `localhost:5000` directly without routing through the mirror.

Current `daemon.json` (conceptual):
```json
{
  "registry-mirrors": ["http://registry-mirror:1273"]
}
```

Fixed `daemon.json`:
```json
{
  "registry-mirrors": ["http://registry-mirror:1273"],
  "insecure-registries": ["localhost:5000"],
  "allow-nondistributable-artifacts": ["localhost:5000"]
}
```

Additionally, configure containerd's `hosts.toml` for `localhost:5000` to bypass the mirror.
Create or update `%USERPROFILE%\.docker\daemon.json` (Docker Desktop reads this on Windows)
OR use the Docker Desktop Settings → Docker Engine panel directly.

After applying: **restart Docker Desktop** to reload the daemon configuration.

**Verification**: `docker pull localhost:5000/cache-service:poc-v2` should resolve in
< 5 s and report the 2.09 GB image size, not 492 MB.

---

#### Fix 2 — Rebuild and Re-push cache-service:poc-v2 After Mirror Bypass

**File**: `cache_service/Dockerfile` (already correct — no code changes needed)  
**Action**: Rebuild the image now that the mirror bypass is in place so containerd
fetches fresh layers.

```cmd
REM Run from repo root (on_prem_server_poc\)
docker build -f cache_service/Dockerfile -t localhost:5000/cache-service:poc-v2 .
docker push localhost:5000/cache-service:poc-v2
```

**Expected result**: Image is 2.09 GB and contains `/root/.cache/huggingface/` with the
`all-MiniLM-L6-v2` model weights (validated by the `RUN python -c "..."` Dockerfile layer).

**Verification**: 
```cmd
docker run --rm localhost:5000/cache-service:poc-v2 \
  python -c "from sentence_transformers import SentenceTransformer; \
             m = SentenceTransformer('all-MiniLM-L6-v2'); print('OK')"
```
Should print `OK` without making any network calls.

---

#### Fix 3 — Increase Security-Layer Liveness Probe Budget

**File**: `llm-platform/charts/security-layer/values.yaml`  
**Function**: `livenessProbe` configuration block

**Current values (budget = 180 s):**
```yaml
livenessProbe:
  tcpSocket:
    port: 8081
  initialDelaySeconds: 30
  periodSeconds: 15
  timeoutSeconds: 5
  failureThreshold: 10
  successThreshold: 1
```

**Fixed values (budget = 360 s):**
```yaml
livenessProbe:
  tcpSocket:
    port: 8081
  initialDelaySeconds: 120
  periodSeconds: 15
  timeoutSeconds: 5
  failureThreshold: 16
  successThreshold: 1
```

Budget calculation: 120 + (16 × 15) = **360 s (6 minutes)**. This provides a 2× safety
margin over the 3-minute worst-case Presidio/spaCy load time on constrained hardware.

The probe type remains `tcpSocket` on port 8081 (not HTTP), which is already correct —
the comment in the values file explains the rationale.

**No changes required to `readinessProbe`** — it already uses `initialDelaySeconds: 180`
which is sufficient since readiness does not kill the pod.


#### Fix 4 — Enable Ollama Init Job in values-poc-local.yaml

**File**: `llm-platform/values-poc-local.yaml`

Two locations in this file set `initJob.enabled: false` — both must be set to `true`.

**Change 1** (under `inferenceOllama:` camelCase block, line ~113):
```yaml
# Before:
  initJob:
    enabled: false

# After:
  initJob:
    enabled: true
```

**Change 2** (under `"inference-ollama":` hyphenated sub-chart key block, near bottom):
```yaml
# Before:
"inference-ollama":
  adapter:
    image:
      repository: localhost:5000/inference-adapter
      tag: poc
      pullPolicy: IfNotPresent
  initJob:
    enabled: false

# After:
"inference-ollama":
  adapter:
    image:
      repository: localhost:5000/inference-adapter
      tag: poc
      pullPolicy: IfNotPresent
  initJob:
    enabled: true
```

Remove (or update) the manual pull comment — the init Job handles it automatically.

**Note on timing**: The init Job has `backoffLimit: 2` and will retry if Ollama is not yet
ready when the Job starts. If the Job fails both retries (e.g., Ollama pod is still
starting), run the Job manually after Ollama is Running:
```cmd
kubectl delete job -n llm-poc -l app.kubernetes.io/name=inference-ollama
helm upgrade llm-poc . --namespace llm-poc \
  --values values-poc.yaml --values values-poc-local.yaml --reuse-values
```
Or pull the model directly as a fallback:
```cmd
kubectl exec -n llm-poc deploy/llm-poc-inference-ollama -- ollama pull llama3.2:3b
```

#### Fix 5 — Redeploy with helm upgrade

After applying Fixes 1–4:
```cmd
helm upgrade --install llm-poc . ^
  --namespace llm-poc ^
  --values values-poc.yaml ^
  --values values-poc-local.yaml
```

P6 (stale crashlooping pods from prior ReplicaSets) resolves automatically: once the pods
in the new ReplicaSets become Ready, Kubernetes rolling-update cleanup terminates the old pods.


## Testing Strategy

### Validation Approach

The testing strategy follows the two-phase bug condition methodology:

1. **Exploratory phase** — run tests against the UNFIXED configuration to observe failure
   modes and confirm the root cause analysis.
2. **Fix checking + preservation checking** — run tests after each fix is applied to
   confirm the bug condition no longer holds and existing behavior is unchanged.

Because the bugs are infrastructure/configuration defects (not pure application logic bugs),
the primary test mechanisms are:
- **Shell/kubectl assertions** for Kubernetes state verification (pod status, probe outcomes)
- **Python unit tests with mocks** for testing application-level behavior in isolation
  (embedding model load path, liveness probe logic, health endpoint logic)
- **Property-based tests (Hypothesis)** for preservation checking across randomized input
  ranges (probe budget arithmetic, cache similarity threshold, inference request forwarding)

---

### Exploratory Bug Condition Checking

**Goal**: Surface counterexamples that confirm the four root causes BEFORE applying fixes.
Run the following on the current (unfixed) deployment.

**Test Cases:**

1. **P7 Mirror Check**: Query containerd for the manifest digest served for
   `localhost:5000/cache-service:poc-v2`:
   ```cmd
   kubectl run img-check --image=localhost:5000/cache-service:poc-v2 --restart=Never \
     -- python -c "import os; print(os.path.getsize('/root/.cache/huggingface'))"
   kubectl logs img-check
   ```
   Expected counterexample: `FileNotFoundError` or very small directory size confirming
   the model layer is absent.

2. **P1/P2 Startup Failure Check**: Observe cache-service pod logs immediately after pod
   creation:
   ```cmd
   kubectl logs -n llm-poc deploy/llm-poc-cache -f --since=30s
   ```
   Expected counterexample: Log line containing `huggingface.co` connection attempt followed
   by `socket.gaierror` or `requests.exceptions.ConnectionError`.

3. **P4 Probe Kill Check**: Observe security-layer events during startup:
   ```cmd
   kubectl describe pod -n llm-poc -l app.kubernetes.io/name=security-layer
   ```
   Expected counterexample: Events showing `Liveness probe failed: ... TCP probe` before
   the Presidio log line `AnalyzerEngine initialized`.

4. **P5/P8 Adapter Health Check**: Call the adapter health endpoint directly while Ollama
   has no model:
   ```cmd
   kubectl exec -n llm-poc deploy/llm-poc-inference-ollama-adapter \
     -- curl -s http://localhost:8087/health
   ```
   Expected counterexample: HTTP 503 with body indicating `llama3.2:3b` is not available.

5. **P4 Budget Arithmetic Check** (unit test on unfixed values):
   ```python
   def test_p4_bug_condition_original_budget():
       probe = {"initialDelaySeconds": 30, "failureThreshold": 10, "periodSeconds": 15}
       budget = probe["initialDelaySeconds"] + probe["failureThreshold"] * probe["periodSeconds"]
       assert budget == 180
       # Simulates worst-case load time of 185s — bug condition holds
       assert 185 > budget  # isBugCondition_P4 returns True
   ```

---

### Fix Checking

**Goal**: After each fix is applied, assert the bug condition no longer holds.

**Pseudocode (from bugfix.md):**
```
// P7 fix check
FOR ALL pull WHERE isBugCondition_P7(pull) DO
  result ← pull_image_fixed(pull)
  ASSERT result.source_registry = "localhost:5000"
     AND result.manifest_digest = current_registry_digest
     AND result.image_size_bytes >= 2_000_000_000
END FOR

// P1/P2 fix check
FOR ALL start WHERE isBugCondition_P1P2(start) DO
  result ← cache_startup_fixed(start)
  ASSERT result.model_loaded_from_disk = true
     AND result.outbound_huggingface_call = false
     AND result.startup_complete = true
END FOR

// P4 fix check
FOR ALL cfg WHERE isBugCondition_P4(cfg, load_time=180) DO
  budget_fixed ← cfg_fixed.initialDelaySeconds
               + (cfg_fixed.failureThreshold * cfg_fixed.periodSeconds)
  ASSERT budget_fixed > 180
     AND cfg_fixed.probe_type = "tcpSocket"
END FOR

// P5/P8 fix check
FOR ALL hc WHERE isBugCondition_P5P8(hc) AFTER model pulled DO
  result ← adapter_health_fixed(model_present=True)
  ASSERT result.http_status = 200
     AND result.pod_ready = true
END FOR
```

**Concrete post-fix assertions:**
- `kubectl get pods -n llm-poc` — all three previously crashlooping pods show `Running/1/1`
- Cache-service: `curl http://cache:8086/health` → 200
- Security-layer: `curl http://security-layer:8081/health` → 200 (after load completes)
- Inference-adapter: `curl http://inference-adapter:8087/health` → 200


### Preservation Checking

**Goal**: For all inputs where the bug conditions do NOT hold, the fixed system produces
identical results to the correct pre-bug behavior.

**Pseudocode (from bugfix.md):**
```
// Preservation: services without rebuilt images are unaffected
FOR ALL pull WHERE NOT isBugCondition_P7(pull) DO
  ASSERT pull_image(pull) = pull_image_fixed(pull)
END FOR

// Preservation: cache normal operation unchanged
FOR ALL req WHERE cache_service_healthy(req) DO
  ASSERT cache_lookup(req) = cache_lookup_fixed(req)
END FOR

// Preservation: security-layer blocking unchanged
FOR ALL req WHERE security_layer_started(req) DO
  ASSERT security_decision(req) = security_decision_fixed(req)
END FOR

// Preservation: adapter inference behavior unchanged
FOR ALL req WHERE model_loaded(req) DO
  ASSERT adapter_response(req) = adapter_response_fixed(req)
END FOR
```

Property-based testing is the primary mechanism for preservation checking because:
- It generates hundreds of test cases across the valid input space automatically.
- The probe budget and cache threshold logic are arithmetic properties that are easy to
  express as generators + assertions.
- It catches edge cases near the boundary (e.g., load_time == budget, similarity == 0.90).

**Test Cases:**

1. **Probe Budget Arithmetic PBT** (preserves non-buggy probe configs):
   Generate random `(initialDelaySeconds, failureThreshold, periodSeconds)` tuples where
   budget > 180 and assert they satisfy `NOT isBugCondition_P4`. Validates the fix formula
   generalises correctly.

2. **Cache Similarity Threshold PBT** (preserves lookup behavior across scores):
   Generate random `(query_embedding, cached_embeddings, threshold)` inputs where
   `isBugCondition_P1P2 = false` and assert the lookup result is unchanged.

3. **Adapter Request Forwarding PBT** (preserves response behavior):
   Generate random valid IMF request payloads and assert the adapter's forwarding logic
   returns the same IMF-formatted response structure, independent of the probe configuration.

4. **Non-rebuilt Image Pull Preservation** (manual smoke test):
   After fix, pull an image that was not rebuilt (e.g., `localhost:5000/api-gateway:poc`)
   and verify it runs the same container as before.

---

### Unit Tests

- **P4 budget arithmetic**: Parameterised tests covering original config (budget = 180),
  fixed config (budget = 360), and boundary cases (load_time = budget, load_time = budget ± 1).
- **P1/P2 model load path**: Mock `sentence_transformers.SentenceTransformer` to assert it
  is called with a local cache path, not an outbound download URL, when model is on disk.
- **P5 health endpoint logic**: Mock `httpx.get(ollama_url/api/tags)` to return empty model
  list → assert `/health` returns 503; mock to return `llama3.2:3b` → assert 200.
- **Security-layer decision isolation**: Assert PII masking and injection block logic produce
  identical outputs before and after the probe config change (no code changes expected).
- **Cache Redis fallback**: Assert that a `redis.exceptions.ConnectionError` during lookup
  results in `lookup_hit: false` and falls through to the inference path, not a 500 error.

### Property-Based Tests

- **Probe budget property**: For all `(initialDelaySeconds ∈ [60, 300], failureThreshold ∈
  [5, 30], periodSeconds ∈ [10, 30])` where budget > 180, verify `isBugCondition_P4` returns
  false (validates the fix formula is correct for the full range).
- **Cache similarity preservation**: For all cosine similarity scores `s ∈ [0.0, 1.0]`, verify
  that the lookup decision (`hit` if s ≥ 0.90, `miss` otherwise) is unchanged by the image fix.
- **Adapter model name handling**: For all model name strings (valid, empty, unknown),
  verify the adapter's response classification is unchanged after the probe fix.
- **IMF envelope preservation**: For all valid IMF request structures, verify the adapter
  returns a response with all required top-level IMF fields intact.

### Integration Tests

- **Full happy-path flow after all fixes**: Send a `POST /v1/chat/completions` through the
  full pipeline (API Gateway → Security Layer → Router → Cache miss → Inference Adapter →
  Ollama) and verify a 200 response with a non-empty `response.content` field.
- **Cache hit after first request**: Send the same request twice; assert the second response
  has `cache.lookup_hit: true` and a lower latency.
- **Security block preserved**: Send a prompt injection attempt and verify HTTP 400 with
  `security_block` reason — confirms security-layer behavior is unchanged after probe fix.
- **Audit trail completeness**: After the happy-path flow, query the audit store and verify
  6 audit events are recorded for the request_id with events at each layer.
- **Metrics continuity**: Scrape `/metrics` on port 9090 from all three previously
  crashlooping services after fixes and verify the mandatory metrics counters are present
  and incrementing.

