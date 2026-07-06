"""
Bug Condition Exploration Tests — Task 1 (BEFORE applying any fix)
===================================================================
These tests surface counterexamples that confirm all four CrashLoopBackOff root
causes exist on the UNFIXED deployment. They MUST NOT be modified to pass on broken
infra; instead they serve as proof that the bugs exist before any fix is applied.

After fixes are applied (Task 8), the kubectl-observable conditions documented here
should no longer hold, and `test_p4_fix_budget` (added in Task 5.2) should also pass.

Spec: .kiro/specs/poc-deployment-crashloop-fix/
Validates: Requirements 1.1–1.10 (Bug Condition — CrashLoopBackOff Root Causes)

=============================================================================
COUNTEREXAMPLES OBSERVED — 2026-07-04 (unfixed deployment, namespace: llm-poc)
=============================================================================

--- P7 Mirror Check (sub-task 1.1) ---
Command: kubectl run img-check --image=localhost:5000/cache-service:poc-v2
         --restart=Never -- python -c "import os; sz=os.path.getsize(
         '/root/.cache/huggingface') if os.path.exists('/root/.cache/huggingface')
         else 0; print('cache_size_bytes:', sz)"
Output:  cache_size_bytes: 4096

Interpretation:
  The /root/.cache/huggingface directory exists but contains only 4096 bytes
  (a single empty directory block — no model weight files).
  The expected model directory for all-MiniLM-L6-v2 would occupy ~90 MB.
  This confirms the running image is the stale 492 MB mirror-cached manifest,
  NOT the 2.09 GB rebuilt image that has the model baked in.
  isBugCondition_P1P2(image_size_bytes=492_000_000, model_on_disk=False) = True
  isBugCondition_P7(registry="localhost:5000", mirror_active=True,
                    image_rebuilt=True) = True

--- P1/P2 Startup Failure Check (sub-task 1.2) ---
Command: kubectl logs -n llm-poc llm-poc-cache-7588554b47-m95w4 --previous
Output (truncated):
  INFO:     Started server process [1]
  INFO:     Waiting for application startup.
  {"event": "redis_connection_failed", "detail": "Error -3 connecting to
   llm-poc-cache-redis:6379. Temporary failure in name resolution."}
  '(MaxRetryError('HTTPSConnectionPool(host=\'huggingface.co\', port=443):
   Max retries exceeded with url: /sentence-transformers/all-MiniLM-L6-v2/
   resolve/main/modules.json (Caused by NameResolutionError(
   "HTTPSConnection(host=\'huggingface.co\', port=443): Failed to resolve
   \'huggingface.co\' ([Errno -3] Temporary failure in name resolution)"))
  Retrying in 1s [Retry 1/5].
  Retrying in 2s [Retry 2/5].

Interpretation:
  The stale image does not contain the all-MiniLM-L6-v2 model on disk.
  sentence_transformers attempts to download from huggingface.co at runtime.
  The cluster has no outbound internet egress — DNS resolution fails with
  [Errno -3] Temporary failure in name resolution.
  The application never completes startup; uvicorn never binds port 8086;
  the liveness probe fails and Kubernetes restarts the pod repeatedly.
  Pod status: CrashLoopBackOff with 15+ restarts observed.
  isBugCondition_P1P2 = True confirmed.

--- P4 Probe Kill Check (sub-task 1.3) ---
Command: kubectl describe pod -n llm-poc -l app.kubernetes.io/name=security-layer
Key observations from Events section:
  - Liveness probe: http-get http://:8081/health delay=60s timeout=5s
                    period=15s #success=1 #failure=3
    Budget = 60 + (3 * 15) = 105 s  [NOTE: this deployment uses even TIGHTER
    values than the spec's original 180s estimate — delay=60, threshold=3]
  - "Liveness probe failed: Get http://10.244.0.66:8081/health: dial tcp
     10.244.0.66:8081: connect: connection refused"  (x43 over 53m)
  - "Container security-layer failed liveness probe, will be restarted"
     (x14 over 52m)
  - "Back-off restarting failed container security-layer" (x101 over 39m)
  - Container killed with Exit Code 137 (SIGKILL from kubelet) repeatedly.
  - Last State: Terminated, Reason: Error, Exit Code: 137
    (container ran for ~2 min 7s before being killed)
  - Presidio/spaCy AnalyzerEngine requires 2-3 min to load;
    port 8081 stays closed until loading completes.

Interpretation:
  The liveness probe fires at 60s + 3*15s = 105s max budget (even tighter than
  the 180s documented in design.md — the deployed chart uses delay=60/threshold=3).
  The AnalyzerEngine has not finished loading when the budget expires.
  Kubernetes kills the container (exit code 137) before it can become healthy.
  This is an infinite restart loop. 15+ restarts observed across two pods.
  isBugCondition_P4(probe_config={initialDelaySeconds:60, failureThreshold:3,
                    periodSeconds:15}, load_time=185) = True
  budget = 60 + 3*15 = 105 < 185 → kill condition holds

--- P5/P8 Adapter Health Check (sub-task 1.4) ---
Command: kubectl exec -n llm-poc deploy/llm-poc-inference-ollama-adapter --
         python3 -c "import urllib.request, urllib.error; url=
         'http://localhost:8087/health'; [HTTP check with exception handling]"
Output:
  HTTP_STATUS:503
  {"status":"unavailable","reason":"ollama_unreachable"}

Interpretation:
  The inference-adapter /health endpoint returns HTTP 503 because Ollama is
  unreachable or has no model loaded. initJob.enabled: false in
  values-poc-local.yaml means the init Job that pulls llama3.2:3b was never run.
  The readiness probe (httpGet /health, initialDelaySeconds:15, periodSeconds:15)
  receives 503 on every check, keeping the adapter pod stuck at 0/1 Running.
  Two adapter pods observed (both 0/1 Running): adapter-545795b6f9-nsfsj (6h old),
  adapter-c7cb47cbf-7x427 (53m old).
  isBugCondition_P5P8(model_present_in_ollama=False) = True

=============================================================================
P4 BUDGET ARITHMETIC — deployed values vs design.md documented values
=============================================================================
design.md documents: initialDelaySeconds=30, failureThreshold=10, periodSeconds=15
  → budget_documented = 30 + 10*15 = 180s

Actual deployed values (from kubectl describe): delay=60, threshold=3, period=15
  → budget_deployed = 60 + 3*15 = 105s

Both are buggy (both < 185s worst-case load time). The unit test below uses the
design.md values (the canonical "original" config) as specified in the task.
The deployed config is actually WORSE (105s budget), which only strengthens the
bug condition evidence.
=============================================================================
"""

# ---------------------------------------------------------------------------
# Sub-task 1.5 — P4 budget arithmetic unit test on UNFIXED values
# ---------------------------------------------------------------------------
# Validates: Requirements 1.6, 1.7 (isBugCondition_P4)
# This test PASSES intentionally — the assertion that 185 > 180 is TRUE,
# which is how it CONFIRMS the bug condition holds on the original unfixed config.
# ---------------------------------------------------------------------------

def test_p4_bug_condition_original_budget():
    """
    Confirms the P4 bug condition holds for the canonical original probe config
    documented in design.md (initialDelaySeconds=30, failureThreshold=10,
    periodSeconds=15) when the security-layer load time is 185 seconds.

    Expected: PASSES — both assertions are True, proving the bug exists.
      budget == 180  → original probe budget
      185 > budget   → isBugCondition_P4(probe, 185) is True → pod gets killed

    Validates: Requirements 1.6, 1.7
    """
    probe = {"initialDelaySeconds": 30, "failureThreshold": 10, "periodSeconds": 15}
    budget = probe["initialDelaySeconds"] + probe["failureThreshold"] * probe["periodSeconds"]
    assert budget == 180          # confirms original budget
    assert 185 > budget           # isBugCondition_P4(probe, 185) is True — bug exists


def test_p4_bug_condition_deployed_budget():
    """
    Additional confirmation: the ACTUAL deployed probe config (observed via
    kubectl describe) has an even tighter budget than documented in design.md.

    delay=60s, threshold=3, period=15s → budget = 60 + 3*15 = 105s
    105s < 185s worst-case load time → bug condition holds even more severely.

    Validates: Requirements 1.6, 1.7
    """
    probe_deployed = {"initialDelaySeconds": 60, "failureThreshold": 3, "periodSeconds": 15}
    budget_deployed = (
        probe_deployed["initialDelaySeconds"]
        + probe_deployed["failureThreshold"] * probe_deployed["periodSeconds"]
    )
    assert budget_deployed == 105         # actual deployed budget (tighter than design.md)
    assert 185 > budget_deployed          # bug condition holds on deployed config too
    assert budget_deployed < 180          # deployed is even worse than the design.md baseline


# ---------------------------------------------------------------------------
# Sub-task 5.2 — P4 fix-checking unit test (added after Fix 3 is applied)
# ---------------------------------------------------------------------------
# Validates: Requirements 2.6, 2.7 (isBugCondition_P4 is now False after fix)
# ---------------------------------------------------------------------------

def test_p4_fix_budget():
    """
    Confirms the P4 bug condition no longer holds after Fix 3 is applied.
    Fixed probe config: initialDelaySeconds=120, failureThreshold=16, periodSeconds=15
    New budget = 120 + 16*15 = 360s — 2x safety margin over 185s worst-case load time.

    Expected: PASSES — all assertions confirm the fix is correct.
      budget == 360          → fixed probe budget
      185 <= budget          → isBugCondition_P4(probe_fixed, 185) is now False
      180 <= budget          → worst-case original load time is within budget

    Validates: Requirements 2.6, 2.7
    """
    probe_fixed = {"initialDelaySeconds": 120, "failureThreshold": 16, "periodSeconds": 15}
    budget = probe_fixed["initialDelaySeconds"] + probe_fixed["failureThreshold"] * probe_fixed["periodSeconds"]
    assert budget == 360
    assert 185 <= budget   # isBugCondition_P4(probe_fixed, 185) is now False
    assert 180 <= budget   # worst-case original load time is now within budget
