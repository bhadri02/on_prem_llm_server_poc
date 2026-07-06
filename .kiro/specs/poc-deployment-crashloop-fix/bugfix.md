# Bugfix Requirements Document

## Introduction

The POC deployment on Kubernetes (Docker Desktop) enters a persistent CrashLoopBackOff state across three services — `cache-service`, `security-layer`, and `inference-adapter` — due to eight interrelated defects. The root causes span four categories: a Docker Desktop registry mirror serving stale image manifests (P7/P1), missing runtime dependencies baked into images (P2), an under-budgeted liveness probe that races against slow model loading (P4), and Ollama having no model loaded due to a skipped init Job (P8/P5). Problems P3 and P6 are downstream symptoms that self-resolve once the root causes are fixed. None of the platform's demo flows are serviceable until all CrashLoopBackOff conditions are resolved.

---

## Bug Analysis

### Current Behavior (Defect)

**P7 — Registry mirror intercepts rebuilt image pulls**

1.1 WHEN a Docker image is rebuilt and re-pushed to `localhost:5000` under an existing tag AND a pod is (re)scheduled that references that tag THEN the system pulls the stale cached manifest from `registry-mirror:1273` instead of the updated image, resulting in the wrong image version running in the container

**P1 — Cache service runs a stale undersized image**

1.2 WHEN the cache-service pod starts using the stale `localhost:5000/cache-service:poc-v2` manifest (492 MB) that was cached by the registry mirror THEN the system runs a container that does not contain the `all-MiniLM-L6-v2` sentence-transformers model

**P2 — Cache service attempts outbound download of the embedding model at runtime**

1.3 WHEN the cache-service container starts and the `all-MiniLM-L6-v2` model is not present on disk THEN the system attempts to download the model from `huggingface.co` at startup

1.4 WHEN the outbound download attempt to `huggingface.co` is made from within the Kubernetes cluster THEN the system fails with a DNS resolution error because the cluster has no outbound internet access, causing the application to never reach a started state

**P3 — Cache service Redis DNS fails on first boot**

1.5 WHEN the cache-service pod starts before Kubernetes DNS has fully propagated the `llm-poc-cache-redis` service entry THEN the system logs an NXDOMAIN error on DNS lookup of `llm-poc-cache-redis:6379`

**P4 — Security-layer liveness probe kills pod before Presidio/spaCy finishes loading**

1.6 WHEN the security-layer container starts AND the `AnalyzerEngine` (Presidio + spaCy `en_core_web_sm`) is loading (a 2–3 minute process) THEN the system keeps port 8081 closed for the entire duration of model loading

1.7 WHEN the liveness probe (`initialDelaySeconds: 30`, `failureThreshold: 10`, `periodSeconds: 15`, total budget ≈ 165 s) fires against port 8081 via TCP socket before loading completes THEN the container is killed and restarted, producing CrashLoopBackOff

**P5 — Inference adapter readiness probe permanently fails because Ollama has no model**

1.8 WHEN the inference-adapter `/health` endpoint is called AND the default model (`llama3.2:3b`) is not present in Ollama THEN the system returns HTTP 503

1.9 WHEN the readiness probe (`httpGet /health`, `initialDelaySeconds: 15`, `periodSeconds: 15`) repeatedly receives HTTP 503 THEN the adapter pod remains in `0/1 Running` state and is never marked Ready, preventing all inference traffic

**P8 — Ollama has no model because the init Job was disabled**

1.10 WHEN `initJob.enabled: false` is set in Helm values THEN the system does not run the init Job that pulls `llama3.2:3b` into Ollama, leaving Ollama running but empty

**P6 — Two stale crashlooping pods exist per affected deployment**

1.11 WHEN a Helm upgrade creates a new ReplicaSet for a deployment that is crashlooping AND the new pod never becomes Ready THEN the system retains old crashlooping pods from prior revisions alongside the new failing pod, resulting in two or more unhealthy pods per service

---

### Expected Behavior (Correct)

**P7 — Registry mirror bypass**

2.1 WHEN a Docker image is rebuilt and re-pushed to `localhost:5000` under an existing tag AND a pod is (re)scheduled THEN the system SHALL pull the image directly from `localhost:5000` without routing through `registry-mirror:1273`, ensuring the current image digest is used

**P1 — Cache service runs the correct rebuilt image**

2.2 WHEN the cache-service pod starts THEN the system SHALL run the 2.09 GB image containing the pre-downloaded `all-MiniLM-L6-v2` model (digest sha256:a488…)

**P2 — Cache service uses the model baked into the image**

2.3 WHEN the cache-service container starts THEN the system SHALL load `all-MiniLM-L6-v2` from the local image filesystem without making any outbound network call to `huggingface.co`

2.4 WHEN the cache-service starts in a cluster with no outbound internet access THEN the system SHALL reach startup-complete state successfully because no external model download is required

**P3 — Cache service tolerates transient Redis DNS failures**

2.5 WHEN the cache-service pod starts before the `llm-poc-cache-redis` DNS entry is fully propagated THEN the system SHALL log the DNS error, continue initializing, and SHALL CONTINUE TO reconnect to Redis automatically once the DNS entry resolves

**P4 — Security-layer liveness probe does not kill the pod during model loading**

2.6 WHEN the security-layer container starts AND `AnalyzerEngine` is loading THEN the system SHALL NOT kill the container before loading completes, by configuring the liveness probe with a budget that exceeds the worst-case load time (≥ 3 minutes)

2.7 WHEN the security-layer `AnalyzerEngine` finishes loading and port 8081 opens THEN the system SHALL report healthy on the liveness probe and the pod SHALL remain Running

**P5 — Inference adapter readiness probe reflects actual model availability**

2.8 WHEN `llama3.2:3b` is present in Ollama AND the adapter `/health` endpoint is called THEN the system SHALL return HTTP 200, causing the readiness probe to pass and the pod to be marked Ready

2.9 WHEN the inference-adapter liveness probe fires while the model is not yet loaded THEN the system SHALL NOT restart the container, because the liveness probe uses `tcpSocket` on port 8087 (process alive) rather than the HTTP `/health` endpoint (model status)

**P8 — Ollama has the required model loaded**

2.10 WHEN `initJob.enabled: true` is set in Helm values OR the model is pulled manually THEN the system SHALL have `llama3.2:3b` available in Ollama before the inference-adapter readiness probe is evaluated

**P6 — Stale pods clear automatically**

2.11 WHEN the root-cause bugs (P1, P4, P5) are resolved and the affected pods become Ready THEN the system SHALL terminate stale pods from prior ReplicaSets automatically via Kubernetes rolling-update cleanup

---

### Unchanged Behavior (Regression Prevention)

3.1 WHEN any service image that has NOT been rebuilt is pulled from `localhost:5000` THEN the system SHALL CONTINUE TO serve that image correctly from its existing manifest without disruption

3.2 WHEN the cache-service starts with a valid image and Redis is available THEN the system SHALL CONTINUE TO perform semantic similarity lookups against Redis using the `all-MiniLM-L6-v2` model with a similarity threshold of 0.90

3.3 WHEN the security-layer is fully started (spaCy and Presidio loaded) THEN the system SHALL CONTINUE TO detect and mask PII entities (e.g., `EMAIL_ADDRESS`, `PERSON`) in request content before forwarding to the router

3.4 WHEN the security-layer is fully started THEN the system SHALL CONTINUE TO evaluate prompt injection patterns and return HTTP 400 with a `security_block` reason for detected injection attempts

3.5 WHEN `llama3.2:3b` is loaded in Ollama and a valid inference request is sent to the adapter THEN the system SHALL CONTINUE TO forward the request to Ollama at `host.docker.internal:11434` and return the model response in IMF format

3.6 WHEN the inference-adapter receives a request with an unrecognised model name THEN the system SHALL CONTINUE TO return an appropriate error response without crashing

3.7 WHEN all services are healthy THEN the system SHALL CONTINUE TO record audit events at each layer (`request_received`, `auth_pass`, `cache_hit`/`cache_miss`, `inference_complete`, `response_sent`) in the audit store

3.8 WHEN the cache-service handles a repeat query with a cosine similarity ≥ 0.90 to a cached entry THEN the system SHALL CONTINUE TO return the cached response with `cache.lookup_hit: true` in the IMF envelope

3.9 WHEN Redis becomes unavailable after a successful connection THEN the system SHALL CONTINUE TO serve cache misses by falling back to the inference path rather than crashing

3.10 WHEN Prometheus scrapes `/metrics` on port 9090 of any affected service THEN the system SHALL CONTINUE TO expose the mandatory `llm_<layer>_requests_total`, `llm_<layer>_latency_seconds`, and `llm_<layer>_errors_total` metrics

---

## Bug Condition Pseudocode

### Bug Condition Functions

```pascal
// P7 — Stale registry mirror manifest
FUNCTION isBugCondition_P7(pull_event)
  INPUT: pull_event { registry: string, tag: string, mirror_active: bool, image_rebuilt: bool }
  OUTPUT: boolean
  RETURN pull_event.registry = "localhost:5000"
     AND pull_event.mirror_active = true
     AND pull_event.image_rebuilt = true
END FUNCTION

// P1 / P2 — Cache image missing baked model
FUNCTION isBugCondition_P1P2(container_start)
  INPUT: container_start { image_size_bytes: int, model_on_disk: bool }
  OUTPUT: boolean
  RETURN container_start.image_size_bytes < 1_000_000_000   // < 1 GB indicates stale image
     AND container_start.model_on_disk = false
END FUNCTION

// P4 — Security-layer load time exceeds liveness probe budget
FUNCTION isBugCondition_P4(probe_config, load_time_seconds)
  INPUT: probe_config { initialDelaySeconds: int, failureThreshold: int, periodSeconds: int }
         load_time_seconds: int
  OUTPUT: boolean
  budget ← probe_config.initialDelaySeconds
         + (probe_config.failureThreshold * probe_config.periodSeconds)
  RETURN load_time_seconds > budget
END FUNCTION

// P5 / P8 — Adapter health returns 503 because model absent
FUNCTION isBugCondition_P5P8(health_check)
  INPUT: health_check { model_present_in_ollama: bool }
  OUTPUT: boolean
  RETURN health_check.model_present_in_ollama = false
END FUNCTION
```

### Fix-Checking Properties

```pascal
// Property: P7 Fix — direct pull bypasses mirror
FOR ALL pull WHERE isBugCondition_P7(pull) DO
  result ← pull_image'(pull)
  ASSERT result.source_registry = "localhost:5000"
     AND result.manifest_digest = current_registry_digest
     AND result.image_size_bytes >= 2_000_000_000
END FOR

// Property: P1/P2 Fix — model present at startup, no outbound call
FOR ALL start WHERE isBugCondition_P1P2(start) DO
  result ← cache_startup'(start)
  ASSERT result.model_loaded_from_disk = true
     AND result.outbound_huggingface_call = false
     AND result.startup_complete = true
END FOR

// Property: P4 Fix — liveness budget exceeds worst-case load time
FOR ALL cfg WHERE isBugCondition_P4(cfg, load_time=180) DO
  budget' ← cfg'.initialDelaySeconds
           + (cfg'.failureThreshold * cfg'.periodSeconds)
  ASSERT budget' > 180
     AND cfg'.probe_type = "tcpSocket"
END FOR

// Property: P5/P8 Fix — readiness passes once model present
FOR ALL hc WHERE isBugCondition_P5P8(hc) AFTER fix DO
  result ← adapter_health'(hc_with_model_present)
  ASSERT result.http_status = 200
     AND result.pod_ready = true
END FOR
```

### Preservation Properties

```pascal
// Preservation: services without rebuilt images are unaffected
FOR ALL pull WHERE NOT isBugCondition_P7(pull) DO
  ASSERT pull_image(pull) = pull_image'(pull)
END FOR

// Preservation: cache normal operation unchanged after fix
FOR ALL req WHERE cache_service_healthy(req) DO
  ASSERT cache_lookup(req) = cache_lookup'(req)
END FOR

// Preservation: security-layer blocking behavior unchanged after probe fix
FOR ALL req WHERE security_layer_started(req) DO
  ASSERT security_decision(req) = security_decision'(req)
END FOR

// Preservation: adapter inference behavior unchanged after probe fix
FOR ALL req WHERE model_loaded(req) DO
  ASSERT adapter_response(req) = adapter_response'(req)
END FOR
```
