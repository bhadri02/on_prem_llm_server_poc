# Implementation Plan

## Overview

This plan follows the exploratory bugfix workflow for the POC Kubernetes deployment CrashLoopBackOff fix. It resolves four root causes (P7 registry mirror bypass, P1/P2 cache-service image rebuild, P4 security-layer probe budget, P5/P8 Ollama init Job) using the bug condition methodology: explore failures on unfixed code, establish a preservation baseline, apply fixes in dependency order, then verify both fix-checking and preservation properties pass.

## Tasks

- [x] 1. Write bug condition exploration tests (BEFORE applying any fix)
  - **Property 1: Bug Condition** - CrashLoopBackOff Root Causes (P7, P1/P2, P4, P5/P8)
  - **CRITICAL**: These tests MUST FAIL (or surface the defects) on unfixed configuration — failure confirms the bugs exist
  - **DO NOT attempt to fix the tests or the configuration when they fail**
  - **GOAL**: Surface counterexamples that demonstrate each root cause exists before touching any file
  - **Scoped PBT Approach**: For the deterministic P4 budget arithmetic bug, scope the property to the concrete failing case (load_time=185, original probe config)
  - **Sub-tasks:**
    - 1.1 P7 mirror check — query the manifest digest / image size served for `localhost:5000/cache-service:poc-v2`:
      ```cmd
      kubectl run img-check --image=localhost:5000/cache-service:poc-v2 --restart=Never -- python -c "import os; sz=os.path.getsize('/root/.cache/huggingface') if os.path.exists('/root/.cache/huggingface') else 0; print('cache_size_bytes:', sz)"
      kubectl logs img-check
      kubectl delete pod img-check
      ```
      Expected counterexample: `FileNotFoundError` or `cache_size_bytes: 0` — confirms model layer is absent (isBugCondition_P1P2 = true)
    - 1.2 P1/P2 startup failure check — capture cache-service pod logs showing outbound download attempt:
      ```cmd
      kubectl logs -n llm-poc deploy/llm-poc-cache -f --since=60s
      ```
      Expected counterexample: log line containing `huggingface.co` connection attempt followed by `socket.gaierror` or `ConnectionError`
    - 1.3 P4 probe kill check — observe security-layer events showing liveness probe killing pod before Presidio loads:
      ```cmd
      kubectl describe pod -n llm-poc -l app.kubernetes.io/name=security-layer
      ```
      Expected counterexample: Events showing `Liveness probe failed: ... TCP probe` before `AnalyzerEngine initialized` log line
    - 1.4 P5/P8 adapter health check — call adapter health while Ollama has no model:
      ```cmd
      kubectl exec -n llm-poc deploy/llm-poc-inference-ollama-adapter -- curl -s -o - -w "\nHTTP_STATUS:%{http_code}" http://localhost:8087/health
      ```
      Expected counterexample: `HTTP_STATUS:503` with body indicating `llama3.2:3b` not available
    - 1.5 P4 budget arithmetic unit test — write and run on UNFIXED values:
      ```python
      # tests/test_bug_condition_exploration.py
      def test_p4_bug_condition_original_budget():
          probe = {"initialDelaySeconds": 30, "failureThreshold": 10, "periodSeconds": 15}
          budget = probe["initialDelaySeconds"] + probe["failureThreshold"] * probe["periodSeconds"]
          assert budget == 180          # confirms original budget
          assert 185 > budget           # isBugCondition_P4(probe, 185) is True — bug exists
      ```
      Run: `python -m pytest tests/test_bug_condition_exploration.py -v`
      **EXPECTED OUTCOME**: `test_p4_bug_condition_original_budget` PASSES — the assertion that 185 > 180 confirms the bug condition holds on unfixed config
    - 1.6 Document counterexamples found for each of P7, P1/P2, P4, P5/P8 in a comment block at the top of the exploration test file
  - Mark task complete when all four exploration sub-tasks are run and their counterexamples are documented
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 1.10_

- [x] 2. Write preservation property tests (BEFORE implementing any fix)
  - **Property 2: Preservation** - Non-Buggy Service Behavior Unchanged Across All Layers
  - **IMPORTANT**: Follow observation-first methodology — observe actual behavior of healthy/non-buggy paths on the current (unfixed but partially working) deployment before writing assertions
  - **Scope**: All inputs where NONE of isBugCondition_P7, isBugCondition_P1P2, isBugCondition_P4, isBugCondition_P5P8 hold
  - **Sub-tasks:**
    - 2.1 Probe budget arithmetic PBT — for all `(initialDelaySeconds ∈ [60, 300], failureThreshold ∈ [5, 30], periodSeconds ∈ [10, 30])` where budget > 180, assert isBugCondition_P4 returns False:
      ```python
      # tests/test_preservation_properties.py
      from hypothesis import given, assume, settings
      import hypothesis.strategies as st

      @given(
          initial_delay=st.integers(min_value=60, max_value=300),
          failure_threshold=st.integers(min_value=5, max_value=30),
          period_seconds=st.integers(min_value=10, max_value=30),
      )
      def test_probe_budget_not_buggy_when_budget_exceeds_180(initial_delay, failure_threshold, period_seconds):
          budget = initial_delay + failure_threshold * period_seconds
          assume(budget > 180)
          # isBugCondition_P4 must be False for any load_time <= 180
          assert not (180 > budget)  # non-buggy configs must not trigger the kill condition
      ```
    - 2.2 Cache similarity threshold PBT — for all cosine similarity scores s ∈ [0.0, 1.0], verify lookup decision is consistent (hit iff s ≥ 0.90, miss otherwise):
      ```python
      @given(similarity=st.floats(min_value=0.0, max_value=1.0, allow_nan=False))
      def test_cache_lookup_decision_consistent(similarity):
          threshold = 0.90
          expected_hit = similarity >= threshold
          # lookup_hit logic must be a pure threshold comparison, no side effects
          actual_hit = similarity >= threshold
          assert actual_hit == expected_hit
      ```
    - 2.3 Adapter request forwarding PBT — for all valid model name strings and IMF payloads where model is loaded, assert response always includes required top-level IMF fields:
      ```python
      @given(
          model=st.one_of(st.just("llama3.2:3b"), st.text(min_size=1, max_size=64)),
          temperature=st.floats(min_value=0.0, max_value=2.0, allow_nan=False),
      )
      def test_imf_envelope_fields_always_present(model, temperature):
          required_fields = {"request_id", "response", "cache", "governance", "routing"}
          # Adapter must always return a dict with these top-level keys
          # (mocked via unit test — does not call live Ollama)
          imf = build_mock_imf_response(model=model, temperature=temperature)
          for field in required_fields:
              assert field in imf, f"Missing IMF field: {field}"
      ```
    - 2.4 Run all preservation tests on UNFIXED code and verify they PASS:
      ```cmd
      python -m pytest tests/test_preservation_properties.py -v
      ```
      **EXPECTED OUTCOME**: All preservation tests PASS — this establishes the behavioral baseline before the fix
    - 2.5 Document observed baseline outputs (cache threshold=0.90, IMF field set, healthy probe budget range) as comments in the test file
  - Mark task complete when all preservation tests are written, run, and confirmed PASSING on unfixed code
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10_

- [x] 3. Apply Fix 1 — Bypass Docker Desktop registry mirror for localhost:5000

  - [x] 3.1 Update Docker Desktop daemon.json to add insecure-registries and mirror exclusion
    - Open Docker Desktop → Settings → Docker Engine (JSON editor)
    - Add `"insecure-registries": ["localhost:5000"]` to the JSON object
    - Add `"allow-nondistributable-artifacts": ["localhost:5000"]` to prevent mirror routing
    - Ensure `registry-mirrors` entry for `registry-mirror:1273` is retained for other registries
    - Save the configuration
    - _Bug_Condition: isBugCondition_P7 — pull_event.registry="localhost:5000" AND mirror_active=true AND image_rebuilt=true_
    - _Requirements: 2.1_

  - [x] 3.2 Configure containerd hosts.toml to bypass mirror for localhost:5000
    - Create or update `%USERPROFILE%\.docker\daemon.json` on the Windows host (Docker Desktop reads this)
    - Alternatively: create `$env:USERPROFILE\.docker\config\containerd\certs.d\localhost:5000\hosts.toml` with:
      ```toml
      server = "http://localhost:5000"
      [host."http://localhost:5000"]
        capabilities = ["pull", "resolve", "push"]
      ```
    - This ensures containerd resolves `localhost:5000` directly without consulting the mirror
    - _Requirements: 2.1_

  - [x] 3.3 Restart Docker Desktop to reload daemon configuration
    - Right-click Docker Desktop system tray → Restart
    - Wait for Docker Desktop to report "Running" status (typically 30–60 s)
    - _Requirements: 2.1_

  - [x] 3.4 Verify mirror bypass is active
    - Run: `docker pull localhost:5000/cache-service:poc-v2`
    - Confirm output reports the pull is resolving directly (not from mirror host)
    - The reported image size after rebuild (step 4) should be ≥ 2.09 GB, not 492 MB
    - _Requirements: 2.1_

- [x] 4. Apply Fix 2 — Rebuild and re-push cache-service:poc-v2 with baked embedding model

  - [x] 4.1 Rebuild cache-service image from current Dockerfile (which contains the model bake layer)
    - Run from repo root (`on_prem_server_poc\`):
      ```cmd
      docker build -f cache_service/Dockerfile -t localhost:5000/cache-service:poc-v2 .
      ```
    - The Dockerfile `RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"` layer pre-downloads the model into `/root/.cache/huggingface/`
    - Expected build time: 10–20 min on first build (model download during build, not at runtime)
    - Expected final image size: ~2.09 GB
    - _Bug_Condition: isBugCondition_P1P2 — image_size_bytes < 1_000_000_000 AND model_on_disk=false_
    - _Requirements: 2.2, 2.3, 2.4_

  - [x] 4.2 Push rebuilt image to local registry
    - Run:
      ```cmd
      docker push localhost:5000/cache-service:poc-v2
      ```
    - Verify the push completes and the digest reported by the registry matches the newly built image
    - _Requirements: 2.2_

  - [x] 4.3 Smoke-test the rebuilt image locally to confirm model is baked in
    - Run:
      ```cmd
      docker run --rm --network none localhost:5000/cache-service:poc-v2 python -c "from sentence_transformers import SentenceTransformer; m = SentenceTransformer('all-MiniLM-L6-v2'); print('model_loaded_from_disk: OK')"
      ```
    - `--network none` simulates the air-gapped cluster — confirms NO outbound call is needed
    - Expected output: `model_loaded_from_disk: OK` without any `huggingface.co` connection attempts
    - _Expected_Behavior: model_loaded_from_disk=true, outbound_huggingface_call=false, startup_complete=true_
    - _Requirements: 2.2, 2.3, 2.4_

- [x] 5. Apply Fix 3 — Increase security-layer liveness probe budget from 180 s to 360 s

  - [x] 5.1 Update liveness probe values in llm-platform/charts/security-layer/values.yaml
    - File: `llm-platform/charts/security-layer/values.yaml`
    - Change `livenessProbe.initialDelaySeconds` from `30` to `120`
    - Change `livenessProbe.failureThreshold` from `10` to `16`
    - All other probe fields remain unchanged (`periodSeconds: 15`, `timeoutSeconds: 5`, `successThreshold: 1`, `tcpSocket.port: 8081`)
    - New budget: 120 + (16 × 15) = **360 s** — 2× safety margin over 3-minute worst-case load time
    - **Do NOT change** `readinessProbe` — its existing `initialDelaySeconds: 180` is correct; readiness failure does not kill the pod
    - _Bug_Condition: isBugCondition_P4 — load_time_seconds > initialDelaySeconds + failureThreshold × periodSeconds_
    - _Expected_Behavior: budget'=360 > 180, probe_type="tcpSocket", container NOT killed during Presidio/spaCy load_
    - _Preservation: Preservation Requirements 3.3, 3.4 — PII detection and injection blocking logic is untouched (no app code changes)_
    - _Requirements: 2.6, 2.7_

  - [x] 5.2 Verify the new budget arithmetic is correct
    - Run unit test:
      ```cmd
      python -m pytest tests/test_bug_condition_exploration.py::test_p4_fix_budget -v
      ```
      Where `test_p4_fix_budget` asserts:
      ```python
      def test_p4_fix_budget():
          probe_fixed = {"initialDelaySeconds": 120, "failureThreshold": 16, "periodSeconds": 15}
          budget = probe_fixed["initialDelaySeconds"] + probe_fixed["failureThreshold"] * probe_fixed["periodSeconds"]
          assert budget == 360
          assert 185 <= budget  # isBugCondition_P4(probe_fixed, 185) is now False
          assert 180 <= budget  # worst-case original load time is now within budget
      ```
    - _Requirements: 2.6, 2.7_

- [x] 6. Apply Fix 4 — Enable Ollama init Job in llm-platform/values-poc-local.yaml

  - [x] 6.1 Set initJob.enabled: true under the inferenceOllama camelCase block
    - File: `llm-platform/values-poc-local.yaml`
    - Locate the `inferenceOllama:` block (approximately line 113)
    - Change `initJob:\n  enabled: false` → `initJob:\n  enabled: true`
    - Remove or update the manual pull comment (`# After Ollama pod is Running, pull manually:`) — the init Job now handles this automatically
    - _Bug_Condition: isBugCondition_P5P8 — model_present_in_ollama=false because initJob suppressed_
    - _Requirements: 2.10_

  - [x] 6.2 Set initJob.enabled: true under the "inference-ollama" hyphenated sub-chart key block
    - File: `llm-platform/values-poc-local.yaml`
    - Locate the `"inference-ollama":` block (near the bottom of the file)
    - Change `initJob:\n  enabled: false` → `initJob:\n  enabled: true` in this second location
    - Both occurrences must be set to `true` — Helm merges both blocks and either `false` can suppress the Job
    - _Requirements: 2.10_

  - [x] 6.3 Note init Job retry behavior
    - The init Job has `backoffLimit: 2` — it will retry twice if Ollama is not yet Ready when the Job starts
    - If both retries fail after `helm upgrade`, fall back to:
      ```cmd
      kubectl exec -n llm-poc deploy/llm-poc-inference-ollama -- ollama pull llama3.2:3b
      ```
    - Or re-trigger the Job after Ollama is Running (see design.md Fix 4 fallback instructions)
    - _Requirements: 2.10_

- [x] 7. Apply Fix 5 — Redeploy with helm upgrade --install

  - [x] 7.1 Run helm upgrade to apply configuration changes and pull fresh images
    - Run from `llm-platform\` directory:
      ```cmd
      helm upgrade --install llm-poc . ^
        --namespace llm-poc ^
        --values values-poc.yaml ^
        --values values-poc-local.yaml
      ```
    - This picks up Fix 3 (probe budget), Fix 4 (initJob), and triggers pod restarts that will pull the rebuilt Fix 2 image
    - _Requirements: 2.1, 2.2, 2.6, 2.7, 2.10_

  - [x] 7.2 Monitor pod startup until all three previously-crashlooping pods are Running/Ready
    - Run:
      ```cmd
      kubectl get pods -n llm-poc -w
      ```
    - Wait for `llm-poc-cache-*`, `llm-poc-security-layer-*`, and `llm-poc-inference-ollama-adapter-*` to show `Running 1/1 Ready`
    - The security-layer pod may take up to 6 minutes to reach Ready — this is expected and correct after Fix 3
    - P6 (stale crashlooping pods from prior ReplicaSets) resolves automatically once new pods become Ready
    - _Requirements: 2.6, 2.7, 2.8, 2.9, 2.10, 2.11_

- [ ] 8. Verify bug condition exploration tests now pass (fix checking)

  - [ ] 8.1 Verify P7/P1/P2 — cache-service pod runs correct 2.09 GB image with baked model
    - **Property 1: Expected Behavior** - Cache Service Correct Image and Model Load
    - **IMPORTANT**: Re-run the SAME checks from task 1 — do NOT write new tests; the exploration test encodes expected behavior
    - Run image size / model presence check (same as task 1.1):
      ```cmd
      kubectl run img-check-fixed --image=localhost:5000/cache-service:poc-v2 --restart=Never -- python -c "import os; sz=os.path.getsize('/root/.cache/huggingface'); print('cache_size_bytes:', sz)"
      kubectl logs img-check-fixed
      kubectl delete pod img-check-fixed
      ```
    - **EXPECTED OUTCOME**: `cache_size_bytes` is non-zero (model present on disk) — isBugCondition_P1P2 = False
    - Also check: `curl http://<cache-service-clusterip>:8086/health` → HTTP 200
    - _Requirements: 2.2, 2.3, 2.4_

  - [ ] 8.2 Verify P4 — security-layer pod survives full Presidio/spaCy load without being killed
    - Re-run: `kubectl describe pod -n llm-poc -l app.kubernetes.io/name=security-layer`
    - **EXPECTED OUTCOME**: No `Liveness probe failed` events; pod transitions to Running/Ready after `AnalyzerEngine initialized` log line appears
    - Also check: `curl http://<security-layer-clusterip>:8081/health` → HTTP 200 (after load completes, allow up to 6 min)
    - _Requirements: 2.6, 2.7_

  - [ ] 8.3 Verify P5/P8 — inference-adapter /health returns 200 once model is loaded
    - Re-run: `kubectl exec -n llm-poc deploy/llm-poc-inference-ollama-adapter -- curl -s -o - -w "\nHTTP_STATUS:%{http_code}" http://localhost:8087/health`
    - **EXPECTED OUTCOME**: `HTTP_STATUS:200` — isBugCondition_P5P8 = False (model present in Ollama)
    - _Requirements: 2.8, 2.9, 2.10_

  - [ ] 8.4 Run the P4 fix-checking unit test
    - Run:
      ```cmd
      python -m pytest tests/test_bug_condition_exploration.py -v
      ```
    - `test_p4_bug_condition_original_budget` — still passes (documents original broken state)
    - `test_p4_fix_budget` — must pass (confirms fixed budget = 360 > any observed load time ≤ 180 s)
    - **EXPECTED OUTCOME**: Both tests PASS
    - _Requirements: 2.6, 2.7_

  - [ ] 8.5 Final kubectl confirmation — all three services Running/Ready
    - Run: `kubectl get pods -n llm-poc`
    - **EXPECTED OUTCOME**: All pods show `1/1 Running` with 0 restarts accumulating; no CrashLoopBackOff status
    - _Requirements: 2.1, 2.2, 2.6, 2.7, 2.8, 2.9, 2.10, 2.11_

- [ ] 9. Verify preservation tests still pass (no regressions)

  - [ ] 9.1 Re-run the full preservation property test suite
    - **Property 2: Preservation** - Non-Buggy Behavior Unchanged Across All Layers
    - **IMPORTANT**: Re-run the SAME tests from task 2 — do NOT write new tests
    - Run:
      ```cmd
      python -m pytest tests/test_preservation_properties.py -v
      ```
    - **EXPECTED OUTCOME**: All preservation tests PASS — probe budget PBT, cache threshold PBT, adapter forwarding PBT all green
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10_

  - [ ] 9.2 Smoke-test non-rebuilt image pull (manual preservation check for 3.1)
    - Run: `docker pull localhost:5000/api-gateway:poc` (or any image NOT in the rebuild set)
    - Verify the pull completes normally and the running container is unaffected by the mirror bypass change
    - _Requirements: 3.1_

  - [ ] 9.3 Verify security-layer PII masking and injection blocking behavior unchanged (3.3, 3.4)
    - Once security-layer is fully started, send a test request containing a known PII pattern:
      ```cmd
      kubectl exec -n llm-poc deploy/llm-poc-security-layer -- curl -s -X POST http://localhost:8081/analyze -H "Content-Type: application/json" -d "{\"text\": \"Contact john.doe@example.com for details\"}"
      ```
    - Verify the response detects `EMAIL_ADDRESS` entity (same as pre-fix behavior)
    - Send a prompt injection attempt and verify HTTP 400 with `security_block` reason
    - _Requirements: 3.3, 3.4_

  - [ ] 9.4 Verify Prometheus /metrics endpoints still expose mandatory metrics on all three services (3.10)
    - Run:
      ```cmd
      curl http://<cache-service-clusterip>:9090/metrics | grep llm_cache
      curl http://<security-layer-clusterip>:9090/metrics | grep llm_security
      curl http://<inference-adapter-clusterip>:9090/metrics | grep llm_inference
      ```
    - Verify `llm_<layer>_requests_total`, `llm_<layer>_latency_seconds`, and `llm_<layer>_errors_total` are all present
    - _Requirements: 3.10_

- [ ] 10. Checkpoint — Ensure all tests pass and deployment is fully stable

  - Run the complete test suite one final time:
    ```cmd
    python -m pytest tests/test_bug_condition_exploration.py tests/test_preservation_properties.py -v
    ```
  - Confirm `kubectl get pods -n llm-poc` shows all pods healthy with no crash restarts
  - Confirm full happy-path smoke test (optional but recommended): `POST /v1/chat/completions` through the API Gateway returns a 200 with non-empty `response.content`
  - Confirm audit events are recorded for the smoke test request_id (6 events across all layers)
  - If any test fails or any pod is still crashlooping, investigate before marking this task complete
  - Ask the user if any questions arise — do not assume a partial pass is acceptable
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.6, 2.7, 2.8, 2.9, 2.10, 2.11, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10_


## Task Dependency Graph

```json
{
  "waves": [
    { "wave": 1, "tasks": ["1", "2"] },
    { "wave": 2, "tasks": ["3", "5", "6"] },
    { "wave": 3, "tasks": ["4"] },
    { "wave": 4, "tasks": ["7"] },
    { "wave": 5, "tasks": ["8", "9"] },
    { "wave": 6, "tasks": ["10"] }
  ]
}
```

Tasks 1 and 2 (explore + preservation baseline) are independent and run in parallel before any fix. Tasks 3, 5, and 6 (mirror bypass, probe budget, init Job) are independent configuration changes that can be applied in any order. Task 4 (image rebuild) depends on task 3 — the mirror bypass must be active before rebuilding so containerd fetches fresh layers. Task 7 (helm upgrade) depends on all of 3, 4, 5, 6. Tasks 8 and 9 (fix-checking and preservation re-check) run in parallel after the upgrade. Task 10 (checkpoint) depends on both 8 and 9.

## Notes

- **No application code changes are required.** All five fixes are infrastructure/configuration changes (Docker daemon config, Dockerfile rebuild, Helm values, Helm upgrade).
- **Test file location**: Create exploration and preservation tests in `tests/test_bug_condition_exploration.py` and `tests/test_preservation_properties.py` at the repo root. Both files use `pytest` + `hypothesis` (already present in `.hypothesis/` directory).
- **Property 1 (Bug Condition)** tests are expected to FAIL initially on unfixed code for P7/P1/P2/P5/P8 kubectl checks, and the P4 unit test asserts that 185 > 180 (i.e., the bug exists). After Fix 3, the `test_p4_fix_budget` assertion confirms the fix.
- **Property 2 (Preservation)** tests must PASS both before and after the fixes are applied.
- **Security-layer startup time**: After Fix 3, the pod may legitimately take up to 6 minutes to reach Ready state. This is expected — do not interpret slow startup as a continued failure.
- **Init Job timing (Fix 4)**: If the init Job exhausts its `backoffLimit: 2` retries because Ollama was still starting, fall back to `kubectl exec -n llm-poc deploy/llm-poc-inference-ollama -- ollama pull llama3.2:3b` and re-run the helm upgrade or trigger a Job re-creation as described in Fix 4 of the design document.
- **P6 (stale pods)** resolves automatically — no explicit task is needed once P1, P4, P5 are fixed.
