# Requirements Document

## Introduction

The Model Registry is a lightweight, centralised metadata store for all LLM models deployed on the Enterprise On-Prem LLM Platform. It provides a REST API that the Intelligent Router and other platform services query to discover model capabilities, endpoints, and operational status — eliminating hardcoded model configuration from individual services.

For the POC phase, the registry is implemented as a FastAPI service backed by a JSON file on a PersistentVolume. It is deployed as a single Kubernetes pod via a Helm chart under `llm-platform/charts/model-registry/`. Production features (MLflow, Argo Rollouts, Vault, HPA) are deferred to Phase 2.

---

## Glossary

- **Registry**: The Model Registry service — the FastAPI application that stores and serves model metadata.
- **Model_Record**: A single JSON object describing one registered model, conforming to the Model Metadata Schema.
- **models.json**: The JSON file on the PersistentVolume that is the Registry's backing store.
- **Router**: The Intelligent Router service (Layer 3) that consumes the Registry API to build its in-memory capability matrix.
- **Status**: The operational state of a model — one of `active`, `staging`, or `retired`.
- **Task_Type**: A string identifying the kind of inference work a model can perform — one of `chat`, `code`, `reasoning`, `summarization`, `translation`, `vision`, or `embeddings`.
- **API_Key**: The static pre-shared key used for POC-level authentication on mutating endpoints.
- **STORAGE_PATH**: The environment variable that controls the file path of `models.json` inside the container (default: `/data/models.json`).
- **Capability_Matrix**: The in-memory map built by the Router from the Registry's model list, keyed by Task_Type.
- **IMF**: Internal Message Format — the canonical inter-service JSON envelope defined in the platform master contract.

---

## Requirements

### Requirement 1: Model Metadata Storage

**User Story:** As a platform operator, I want model metadata stored in a single centralised location, so that model information is not duplicated or hardcoded across services.

#### Acceptance Criteria

1. WHEN a Model_Record is created or updated, THE Registry SHALL persist the full current list of Model_Records to the file at STORAGE_PATH in valid JSON array format before returning a success response to the caller.
2. THE Registry SHALL load all Model_Records from STORAGE_PATH on startup before accepting any API requests.
3. IF STORAGE_PATH does not exist on startup, THEN THE Registry SHALL create an empty JSON array file at STORAGE_PATH and continue startup.
4. IF STORAGE_PATH is unreadable or contains malformed JSON on startup, THEN THE Registry SHALL attempt to create a new empty JSON array file at STORAGE_PATH, and if that also fails, THE Registry SHALL log a structured error message to stdout and exit with a non-zero status code.
5. IF STORAGE_PATH becomes unreadable after a successful startup, THEN THE Registry SHALL continue operating with the models already loaded in memory, log a structured error to stdout for each failed persistence operation, and not attempt to exit.
6. THE Registry SHALL enforce that each Model_Record contains the following required fields: `name`, `version`, `backend`, `endpoint`, `tasks`, `status`; IF any required field is absent, THEN THE Registry SHALL respond with HTTP 422 and a JSON error body listing each missing field.
7. THE Registry SHALL accept the following optional fields on a Model_Record: `vram_required_gb`, `max_context_length`, `fallback_model`, `registered_at`, `notes`; any field not in the required or optional set SHALL be rejected with HTTP 422.
8. THE Registry SHALL enforce that the `status` field on any Model_Record is one of the values: `active`, `staging`, `retired`; IF the value is not one of these, THEN THE Registry SHALL respond with HTTP 422 identifying the invalid value.
9. THE Registry SHALL enforce that the `tasks` field on any Model_Record is a non-empty array whose elements are drawn from: `chat`, `code`, `reasoning`, `summarization`, `translation`, `vision`, `embeddings`; IF the array is empty or contains an unrecognised element, THEN THE Registry SHALL respond with HTTP 422 identifying the invalid element.
10. THE Registry SHALL enforce that `name` is unique across all stored Model_Records; IF a POST request contains a `name` that already exists, THEN THE Registry SHALL respond with HTTP 409 and a JSON error body with a `detail` field indicating the conflict.
11. WHEN a Model_Record is created, THE Registry SHALL populate `registered_at` with the current UTC timestamp in ISO-8601 format if the caller does not supply it.
12. IF a write to STORAGE_PATH fails after a successful startup, THEN THE Registry SHALL log a structured error to stdout containing the error message and the name of the Model_Record involved, retain the last successfully persisted state on disk, and return HTTP 500 to the caller.

---

### Requirement 2: List All Models

**User Story:** As the Intelligent Router, I want to retrieve the full list of registered models in a single call, so that I can build my in-memory capability matrix at startup and on each polling cycle.

#### Acceptance Criteria

1. WHEN a GET request is received at `/models`, THE Registry SHALL respond with HTTP 200, a `Content-Type: application/json` header, and a JSON array containing all stored Model_Records regardless of status.
2. WHEN no models are registered, THE Registry SHALL respond with HTTP 200, a `Content-Type: application/json` header, and an empty JSON array (`[]`).
3. THE Registry SHALL serialise each Model_Record such that all required fields (`name`, `version`, `backend`, `endpoint`, `tasks`, `status`, `registered_at`) are always present; each optional field (`vram_required_gb`, `max_context_length`, `fallback_model`, `notes`) that was not supplied at registration SHALL be serialised as `null` rather than omitted from the response object.
4. THE `/models` endpoint SHALL respond within 200 ms, measured from request receipt to complete response delivery, for a store containing up to 100 Model_Records.

---

### Requirement 3: Get Model by Name

**User Story:** As a platform service, I want to retrieve a single model's metadata by name, so that I can inspect its configuration without fetching the full list.

#### Acceptance Criteria

1. WHEN a GET request is received at `/models/{name}`, THE Registry SHALL respond with HTTP 200, a `Content-Type: application/json` header, and the complete Model_Record (all required fields present, optional unset fields serialised as `null`) whose `name` field matches the path parameter exactly.
2. IF no Model_Record exists with the given `name`, THEN THE Registry SHALL respond with HTTP 404 and a JSON error body containing a `detail` field describing the missing resource.
3. THE name lookup SHALL be case-sensitive and match the stored `name` exactly; a request whose path parameter differs only in casing from a stored name SHALL receive HTTP 404.
4. IF the `{name}` path parameter is empty or contains characters outside the set `[a-zA-Z0-9._-]`, THEN THE Registry SHALL respond with HTTP 422 and a JSON error body with a `detail` field identifying the invalid input.

---

### Requirement 4: Register a New Model

**User Story:** As a platform operator, I want to register a new model via the API, so that the Router and other services can discover it without redeploying any service.

#### Acceptance Criteria

1. WHEN a POST request is received at `/models` with a valid Model_Record body, THE Registry SHALL persist the record to STORAGE_PATH and respond with HTTP 201 and the created Model_Record.
2. IF the POST request body is missing any required field, THEN THE Registry SHALL respond with HTTP 422 and a JSON error body listing the missing fields.
3. IF the POST request body contains a `name` that already exists in the store, THEN THE Registry SHALL respond with HTTP 409 and a JSON error body with a `detail` field indicating the conflict.
4. IF the POST request body contains an invalid `status` value, THEN THE Registry SHALL respond with HTTP 422 and a JSON error body identifying the invalid value.
5. IF the POST request body contains an invalid or empty `tasks` array, THEN THE Registry SHALL respond with HTTP 422 and a JSON error body identifying the invalid value.
6. WHEN a POST request is received at `/models` without a valid API_Key in the `X-API-Key` header, THE Registry SHALL respond with HTTP 401 and a JSON error body with a `detail` field before evaluating any model field validation rules.

---

### Requirement 5: Update Model Status

**User Story:** As a platform operator, I want to update a model's status through the API, so that I can mark models as staging, active, or retired without re-registering them.

#### Acceptance Criteria

1. WHEN a PATCH request is received at `/models/{name}/status` with a body containing a valid `status` value, THE Registry SHALL update the stored Model_Record's `status` field, persist the change to STORAGE_PATH, and respond with HTTP 200 and the updated Model_Record.
2. IF no Model_Record exists with the given `name`, THEN THE Registry SHALL respond with HTTP 404 and a JSON error body with a `detail` field.
3. IF the PATCH request body contains an invalid `status` value, THEN THE Registry SHALL respond with HTTP 422 and a JSON error body identifying the invalid value.
4. WHEN a PATCH request is received at `/models/{name}/status` without a valid API_Key in the `X-API-Key` header, THE Registry SHALL respond with HTTP 401 and a JSON error body with a `detail` field before evaluating any model field validation rules.
5. THE Registry SHALL not permit modification of any field other than `status` via the PATCH `/models/{name}/status` endpoint.
6. WHILE the Registry has `REGISTRY_API_KEY` set, THE Registry SHALL return HTTP 404 for a PATCH to a non-existent model only after the API key has been successfully validated.

---

### Requirement 6: Query Models by Task Type

**User Story:** As the Intelligent Router, I want to query models capable of a specific task, so that I can select a candidate model for a given inference request without filtering the full model list locally.

#### Acceptance Criteria

1. WHEN a GET request is received at `/models/by-task/{task_type}` with a recognised task type, THE Registry SHALL respond with HTTP 200 and a JSON array containing only the Model_Records whose `tasks` array includes the given `task_type` AND whose `status` is `active`; the `task_type` path parameter SHALL be matched case-insensitively after normalising to lowercase.
2. IF no active Model_Record supports the given `task_type`, THEN THE Registry SHALL respond with HTTP 200 and an empty JSON array.
3. IF the given `task_type` is not one of `chat`, `code`, `reasoning`, `summarization`, `translation`, `vision`, `embeddings` (after lowercase normalisation), THEN THE Registry SHALL respond with HTTP 422 and a JSON error body identifying the invalid value and listing the accepted values.
4. THE `/models/by-task/{task_type}` endpoint SHALL respond within 200 ms, measured from request receipt to complete response delivery, for a store containing up to 100 Model_Records.

---

### Requirement 7: Health Check Endpoint

**User Story:** As a Kubernetes operator, I want a dedicated health endpoint, so that the liveness and readiness probes can verify the service is operational.

#### Acceptance Criteria

1. WHEN a GET request is received at `/health` and the Registry has completed startup, THE Registry SHALL respond with HTTP 200 and the JSON body `{"status": "ok", "storage": "reachable"}`.
2. WHILE the Registry is starting up and has not yet fully loaded models.json, THE Registry SHALL respond to GET `/health` with HTTP 503 and the JSON body `{"status": "starting"}` for the entire duration of the startup period.
3. IF models.json becomes unreadable after a successful startup and a GET `/health` request is received, THE Registry SHALL respond with HTTP 200 and the JSON body `{"status": "degraded", "storage": "unreachable"}` to indicate the service is running but the backing store is unavailable.
4. THE `/health` endpoint SHALL not require an API_Key header.
5. THE `/health` endpoint SHALL respond within 50 ms when the service is not under artificial I/O load (i.e., no concurrent write operations to STORAGE_PATH are in progress).

---

### Requirement 8: API Authentication

**User Story:** As a platform security owner, I want mutating API operations protected by a static API key, so that only authorised callers can register or modify model records during the POC phase.

#### Acceptance Criteria

1. THE Registry SHALL read the expected API key value from the environment variable `REGISTRY_API_KEY` at startup.
2. IF `REGISTRY_API_KEY` is not set or is an empty string at startup, THEN THE Registry SHALL log a structured warning to stdout and disable API key enforcement, accepting all requests (POC convenience mode).
3. WHEN `REGISTRY_API_KEY` is set, THE Registry SHALL enforce API key validation on POST `/models` and PATCH `/models/{name}/status`.
4. THE Registry SHALL not enforce API key validation on any GET endpoint or the `/health` endpoint, and SHALL NOT require any form of authentication on GET requests even when API key enforcement is enabled.
5. THE Registry SHALL validate the API key by comparing the value of the `X-API-Key` request header to `REGISTRY_API_KEY` using a constant-time comparison.
6. IF the `X-API-Key` header value does not match `REGISTRY_API_KEY`, THEN THE Registry SHALL respond with HTTP 401 and SHALL NOT include the expected key value in the response body.

---

### Requirement 9: Structured Request Logging

**User Story:** As a platform operator, I want every API call logged in structured JSON format, so that I can monitor the registry's behaviour and diagnose issues using the platform's log aggregation tooling.

#### Acceptance Criteria

1. THE Registry SHALL emit a JSON log entry to stdout for every HTTP request it processes.
2. THE log entry SHALL include the following fields: `method`, `path`, `status_code`, `latency_ms`.
3. THE log entry SHALL include a `timestamp` field in ISO-8601 UTC format.
4. THE log entry SHALL include a `level` field set to `INFO` for successful responses (2xx) and `ERROR` for responses with status 500 or above.
5. THE Registry SHALL read the minimum log level from the `LOG_LEVEL` environment variable and suppress log entries below that level.
6. THE Registry SHALL NOT log the value of the `X-API-Key` header in any log entry.

---

### Requirement 10: Kubernetes Deployment and Persistence

**User Story:** As a platform operator, I want the registry deployed as a Helm chart with a PersistentVolume, so that model records survive pod restarts and the deployment follows platform chart conventions.

#### Acceptance Criteria

1. THE Registry Helm chart SHALL be located at `llm-platform/charts/model-registry/` and SHALL include the following files: `Chart.yaml`, `values.yaml`, `templates/deployment.yaml`, `templates/service.yaml`, `templates/networkpolicy.yaml`, `templates/servicemonitor.yaml`, `templates/_helpers.tpl`, and `README.md`; the `hpa.yaml` template is omitted for the POC phase.
2. THE Registry Helm chart SHALL mount a PersistentVolumeClaim with `accessMode: ReadWriteOnce` and `storage: 1Gi` at `/data` inside the container; the chart SHALL not support ephemeral (non-persistent) operation, and `persistence.enabled` SHALL default to `true` with no supported `false` path.
3. THE Registry Helm chart SHALL expose the service as a `ClusterIP` on port `5000` targeting container port `5000`.
4. THE Registry Helm chart SHALL set `replicaCount: 1` and `autoscaling.enabled: false` for the POC.
5. THE Registry Helm chart SHALL configure `STORAGE_PATH` with a default value of `/data/models.json` and `LOG_LEVEL` with a default value of `INFO` from `values.yaml`; `REGISTRY_API_KEY` SHALL be injected from a Kubernetes Secret named `model-registry-secret` using the key `registry-api-key`.
6. THE Registry Helm chart SHALL define liveness and readiness probes on container port `5000` targeting `GET /health` with `initialDelaySeconds: 10`, `periodSeconds: 15`, `timeoutSeconds: 2`, and `failureThreshold: 3`.
7. THE Registry Helm chart SHALL define resource requests and limits: CPU request `100m`, CPU limit `300m`, memory request `128Mi`, memory limit `256Mi`.

---

### Requirement 11: Router Integration Contract

**User Story:** As the Intelligent Router, I want a well-defined polling contract with the registry, so that I can keep my capability matrix current without a restart and remain operational when the registry is temporarily unreachable.

#### Acceptance Criteria

1. THE Router SHALL call GET `http://model-registry:5000/models` at service startup before processing any inference requests.
2. THE Router SHALL call GET `http://model-registry:5000/models` every 60 seconds after startup to refresh its in-memory Capability_Matrix.
3. IF the Registry is unreachable during the startup poll, THEN THE Router SHALL log a structured warning and load its Capability_Matrix from a local static YAML fallback configuration.
4. IF the Registry is unreachable during a periodic refresh poll, THEN THE Router SHALL retain its current in-memory Capability_Matrix and log a structured warning with the failure details.
5. THE Registry SHALL return all active Model_Records in a single response to GET `/models` (no pagination required for POC with up to 100 records).
6. THE GET `/models` response payload SHALL be a valid JSON array that can be deserialised directly into the Router's internal model list without transformation.

---

### Requirement 12: Data Integrity on Write

**User Story:** As a platform operator, I want all writes to models.json to be atomic, so that a crash during a write does not corrupt the stored model records.

#### Acceptance Criteria

1. WHEN the Registry writes to STORAGE_PATH (on POST or PATCH), THE Registry SHALL write the full updated model list to a temporary file in the same directory as STORAGE_PATH, then rename that temporary file to STORAGE_PATH, replacing the previous file in a single filesystem operation.
2. IF the rename operation in criterion 1 fails, THEN THE Registry SHALL delete the temporary file, log a structured error to stdout containing the filesystem error message, and respond with HTTP 500 to the caller without modifying the last successfully persisted STORAGE_PATH file.
3. WHEN the Registry starts up after any sequence of write operations, THE Registry SHALL load a Model_Record list that reflects all writes for which the caller received a 2xx response and none of the writes for which the caller received a non-2xx response or no response due to a crash.
