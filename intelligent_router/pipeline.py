"""
intelligent_router/pipeline.py

Six-stage routing pipeline orchestrator for the Intelligent Router (Layer 3).

Stages:
  Gate:    Governance check — blocks immediately on content_safety_passed=False/missing
  Stage 1: Task classification — always overwrites inbound task_type
  Stage 2: Model selection — raises InvalidPinnedModelError (422) / NoModelForTaskError (503)
  Stage 2b: Policy & entitlement check (Phase 2 — RBAC + per-user API keys) —
            (role, task_type) permission matrix, then model-entitlement check
            against the primary selected model. Both return 403 on denial.
  Stage 3: Health check — falls back to next model or exhausts chain (503)
  Stage 4: Cache lookup — returns 200 on HIT with valid content; treats missing content as MISS
  Stage 5: Inference dispatch — falls back on InferenceError, continues on success
  Stage 6: Cache write + audit dispatch (fire-and-forget via BackgroundTasks), returns 200

All exceptions from downstream calls are handled; the outer try/except catches anything
unhandled and returns 500 internal_error.
"""

import time
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import BackgroundTasks

from intelligent_router import metrics
from intelligent_router.audit_client import post_audit_event
from intelligent_router.cache_client import cache_lookup, cache_write
from intelligent_router.fallback_manager import FallbackState, create_fallback_state
from intelligent_router.health_checker import check_model_health
from intelligent_router.inference_client import InferenceError, call_inference
from intelligent_router.logging_config import get_logger
from intelligent_router.model_selector import (
    InvalidPinnedModelError,
    NoModelForTaskError,
    select_model,
)
from intelligent_router.policy import check_task_permission
from intelligent_router.services.policy_resolver import get_policy_matrix
from intelligent_router.task_classifier import classify_task

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class PipelineResult:
    """Returned by run_routing_pipeline for every outcome (success or error)."""

    success: bool
    status_code: int
    imf: dict
    error_code: str | None
    latency_ms: int


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _ms(t0: float) -> int:
    """Return wall-clock milliseconds elapsed since *t0* (monotonic), as int."""
    return int((time.monotonic() - t0) * 1000)


def _utc_now() -> str:
    """Return the current UTC time as an ISO-8601 string ending in 'Z'."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _build_routing_audit(imf: dict, outcome: str, latency_ms: int) -> dict:
    """Build an audit event dict for a routing decision (success or error).

    Args:
        imf:        The current IMF (used for request_id and routing fields).
        outcome:    "pass" for success, "error" for failure.
        latency_ms: Wall-clock latency in milliseconds.

    Returns:
        Audit event dict conforming to the routing_decision audit schema.
    """
    event_type = "inference_complete" if outcome == "pass" else "inference_start"
    return {
        "request_id": imf.get("request_id"),
        "layer": "router",
        "event_type": event_type,
        "outcome": outcome,
        "model_used": imf.get("routing", {}).get("selected_model"),
        "latency_ms": latency_ms,
        "timestamp_utc": _utc_now(),
    }


def _build_fallback_audit(imf: dict, fallback: FallbackState, outcome: str, latency_ms: int) -> dict:
    """Build an audit event dict for a fallback event.

    Args:
        imf:        The current IMF.
        fallback:   Current FallbackState (used to determine the failed model).
        outcome:    Always "fallback".
        latency_ms: Wall-clock latency in milliseconds.

    Returns:
        Audit event dict conforming to the fallback audit schema.
    """
    # The model that just failed is one position behind the current index
    # because advance() has already been called when we reach here.
    failed_index = fallback.current_index - 1
    if failed_index >= 0 and failed_index < len(fallback.chain):
        failed_model = fallback.chain[failed_index]
    else:
        failed_model = imf.get("routing", {}).get("selected_model")

    return {
        "request_id": imf.get("request_id"),
        "layer": "router",
        "event_type": "inference_start",
        "outcome": outcome,
        "model_used": failed_model,
        "fallback_level": fallback.fallback_level,
        "latency_ms": latency_ms,
        "timestamp_utc": _utc_now(),
    }


def _build_policy_denied_audit(imf: dict, latency_ms: int) -> dict:
    """Build an audit event dict for a Stage 2b task-permission denial."""
    return {
        "request_id": imf.get("request_id"),
        "layer": "router",
        "event_type": "policy_denied",
        "outcome": "block",
        "model_used": imf.get("routing", {}).get("selected_model"),
        "latency_ms": latency_ms,
        "timestamp_utc": _utc_now(),
    }


def _build_entitlement_denied_audit(imf: dict, latency_ms: int) -> dict:
    """Build an audit event dict for a Stage 2b model-entitlement denial."""
    return {
        "request_id": imf.get("request_id"),
        "layer": "router",
        "event_type": "model_not_entitled",
        "outcome": "block",
        "model_used": imf.get("routing", {}).get("selected_model"),
        "latency_ms": latency_ms,
        "timestamp_utc": _utc_now(),
    }


def _build_cache_hit_audit(imf: dict, latency_ms: int) -> dict:
    """Build an audit event dict for a cache hit.

    Args:
        imf:        The current IMF.
        latency_ms: Wall-clock latency in milliseconds.

    Returns:
        Audit event dict conforming to the cache_hit audit schema.
    """
    return {
        "request_id": imf.get("request_id"),
        "layer": "router",
        "event_type": "cache_hit",
        "outcome": "pass",
        "model_used": imf.get("routing", {}).get("selected_model"),
        "latency_ms": latency_ms,
        "timestamp_utc": _utc_now(),
    }


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


async def run_routing_pipeline(
    imf: dict,
    state,
    background_tasks: BackgroundTasks,
) -> PipelineResult:
    """Orchestrate the six-stage routing pipeline.

    Args:
        imf:              The incoming IMF dict (mutated in place).
        state:            FastAPI app.state — provides settings, classifier_rules,
                          model_matrix, and http_client.
        background_tasks: FastAPI BackgroundTasks for fire-and-forget dispatches.

    Returns:
        A PipelineResult describing the outcome.
    """
    t0 = time.monotonic()
    request_id = imf.get("request_id", "unknown")

    try:
        # -----------------------------------------------------------------------
        # Governance gate — MUST be checked before any downstream calls
        # -----------------------------------------------------------------------
        if not imf.get("governance", {}).get("content_safety_passed"):
            return PipelineResult(
                success=False,
                status_code=400,
                imf=imf,
                error_code="governance_check_failed",
                latency_ms=_ms(t0),
            )

        # -----------------------------------------------------------------------
        # Stage 1: Task Classification — always overwrites inbound task_type
        # -----------------------------------------------------------------------
        task_type = classify_task(
            imf["request"]["messages"],
            state.classifier_rules,
        )
        imf["request"]["task_type"] = task_type

        # -----------------------------------------------------------------------
        # Stage 2: Model Selection
        # -----------------------------------------------------------------------
        routing_mode = (imf.get("routing") or {}).get("routing_mode") or "auto"
        pinned_model = (imf.get("request") or {}).get("model")

        try:
            selected_model, effective_mode = select_model(
                task_type,
                routing_mode,
                pinned_model,
                state.model_matrix,
            )
        except InvalidPinnedModelError:
            return PipelineResult(
                success=False,
                status_code=422,
                imf=imf,
                error_code="invalid_pinned_model",
                latency_ms=_ms(t0),
            )
        except NoModelForTaskError:
            return PipelineResult(
                success=False,
                status_code=503,
                imf=imf,
                error_code="no_model_for_task",
                latency_ms=_ms(t0),
            )

        # Initialise routing fields on the IMF
        if "routing" not in imf or imf["routing"] is None:
            imf["routing"] = {}
        imf["routing"]["routing_mode"] = effective_mode
        imf["routing"]["fallback_level"] = 0

        # Initialise cache block if missing
        if "cache" not in imf or imf["cache"] is None:
            imf["cache"] = {}

        # -----------------------------------------------------------------------
        # Stage 2b: Policy & Entitlement Check (Phase 2 — RBAC + per-user API keys)
        #
        # Checked once against the primary selected model, not re-checked per
        # fallback candidate — a denial here means "this identity may never
        # make this call", independent of which backend would have served it.
        # -----------------------------------------------------------------------
        user_block = imf.get("user") or {}
        roles = user_block.get("roles") if isinstance(user_block, dict) else None

        # TTL-cached live fetch from admin_portal (falls back to the static
        # YAML-loaded matrix on any failure) — this is what makes
        # PATCH /portal/roles/{role}/permissions take effect on real
        # enforcement without a policy_matrix.yaml edit + Router restart.
        policy_matrix = await get_policy_matrix(state)

        if not check_task_permission(roles, task_type, policy_matrix):
            background_tasks.add_task(
                post_audit_event,
                _build_policy_denied_audit(imf, _ms(t0)),
                state.settings.audit_store_url,
                state.http_client,
                state.settings.audit_api_key,
            )
            return PipelineResult(
                success=False,
                status_code=403,
                imf=imf,
                error_code="policy_denied",
                latency_ms=_ms(t0),
            )

        model_entitlements = user_block.get("model_entitlements") if isinstance(user_block, dict) else None
        if model_entitlements and selected_model not in model_entitlements:
            background_tasks.add_task(
                post_audit_event,
                _build_entitlement_denied_audit(imf, _ms(t0)),
                state.settings.audit_store_url,
                state.http_client,
                state.settings.audit_api_key,
            )
            return PipelineResult(
                success=False,
                status_code=403,
                imf=imf,
                error_code="model_not_entitled",
                latency_ms=_ms(t0),
            )

        # Build the fallback state from the primary selected model
        fallback = create_fallback_state(selected_model, state.model_matrix)

        # -----------------------------------------------------------------------
        # Stages 3–6: Fallback loop
        # -----------------------------------------------------------------------
        while True:
            current_model = fallback.selected_model
            imf["routing"]["selected_model"] = current_model

            # -------------------------------------------------------------------
            # Stage 3: Health Check
            #
            # Cloud-backend models (backend != "ollama") skip the live network
            # probe entirely and are assumed healthy — there's no cheap,
            # unauthenticated reachability check for a paid external API, and
            # probing on every routing decision would mean burning a real
            # request against the provider just to check liveness. Real
            # failures still surface at Stage 5 (Inference Dispatch) and
            # trigger the normal fallback path.
            #
            # `routing.backend` is also stamped onto the IMF here — this is
            # how the Inference Adapter learns which client to dispatch
            # through (Ollama vs a cloud provider) without an extra
            # per-request lookup against the Model Registry for the common
            # (Ollama) case. Never carries a secret — just the backend name.
            # -------------------------------------------------------------------
            model_entry = state.model_matrix.models.get(current_model)
            backend = (model_entry.backend if model_entry else "ollama") or "ollama"
            imf["routing"]["backend"] = backend

            if backend == "ollama":
                health_url = model_entry.health_url if model_entry else ""
                healthy = await check_model_health(
                    health_url,
                    state.http_client,
                    state.settings.health_check_timeout_seconds,
                )
            else:
                healthy = True

            if not healthy:
                metrics.fallbacks_total.labels(
                    task_type=task_type,
                    reason="health_check_failed",
                ).inc()

                next_model = fallback.advance()
                imf["routing"]["fallback_level"] = fallback.fallback_level

                if next_model is not None:
                    # Log and audit the fallback event, then retry with next model
                    logger.info(
                        "routing_fallback",
                        extra={
                            "extra_fields": {
                                "request_id": request_id,
                                "failed_model": current_model,
                                "fallback_level": fallback.fallback_level,
                                "reason": "health_check_failed",
                            }
                        },
                    )
                    background_tasks.add_task(
                        post_audit_event,
                        _build_fallback_audit(imf, fallback, "fallback", _ms(t0)),
                        state.settings.audit_store_url,
                        state.http_client,
                        state.settings.audit_api_key,
                    )
                    continue
                else:
                    # Chain exhausted — no healthy model available
                    background_tasks.add_task(
                        post_audit_event,
                        _build_routing_audit(imf, "error", _ms(t0)),
                        state.settings.audit_store_url,
                        state.http_client,
                        state.settings.audit_api_key,
                    )
                    return PipelineResult(
                        success=False,
                        status_code=503,
                        imf=imf,
                        error_code="all_backends_exhausted",
                        latency_ms=_ms(t0),
                    )

            # -------------------------------------------------------------------
            # Stage 4: Cache Lookup
            # -------------------------------------------------------------------
            cache_response = await cache_lookup(
                imf["request"]["messages"],
                current_model,
                task_type,
                request_id,
                state.settings.cache_url,
                state.http_client,
                full_imf=imf,
            )

            lookup_hit = bool(cache_response.get("hit"))
            imf["cache"]["lookup_hit"] = lookup_hit
            imf["cache"]["cache_key"] = cache_response.get("cache_key")

            if lookup_hit:
                resp_block = cache_response.get("response") or {}
                if resp_block.get("content"):
                    # Valid cache HIT — populate response and return immediately
                    imf["response"] = {
                        "content": resp_block.get("content"),
                        "finish_reason": resp_block.get("finish_reason"),
                        "usage": resp_block.get("usage") or {
                            "prompt_tokens": 0,
                            "completion_tokens": 0,
                            "total_tokens": 0,
                        },
                    }
                    metrics.cache_hits_total.labels(
                        task_type=task_type,
                        model=current_model,
                    ).inc()
                    background_tasks.add_task(
                        post_audit_event,
                        _build_cache_hit_audit(imf, _ms(t0)),
                        state.settings.audit_store_url,
                        state.http_client,
                        state.settings.audit_api_key,
                    )
                    return PipelineResult(
                        success=True,
                        status_code=200,
                        imf=imf,
                        error_code=None,
                        latency_ms=_ms(t0),
                    )
                else:
                    # HIT reported but response.content is missing/null — treat as MISS
                    imf["cache"]["lookup_hit"] = False

            # -------------------------------------------------------------------
            # Stage 5: Inference Dispatch
            # -------------------------------------------------------------------
            try:
                result_imf = await call_inference(
                    imf,
                    state.settings.inference_adapter_url,
                    request_id,
                    state.settings.inference_timeout_seconds,
                    state.http_client,
                )
            except InferenceError as exc:
                metrics.fallbacks_total.labels(
                    task_type=task_type,
                    reason="inference_error",
                ).inc()

                next_model = fallback.advance()
                imf["routing"]["fallback_level"] = fallback.fallback_level

                logger.warning(
                    "inference_error_fallback",
                    extra={
                        "extra_fields": {
                            "request_id": request_id,
                            "failed_model": current_model,
                            "reason": exc.reason,
                            "fallback_level": fallback.fallback_level,
                        }
                    },
                )

                background_tasks.add_task(
                    post_audit_event,
                    _build_fallback_audit(imf, fallback, "fallback", _ms(t0)),
                    state.settings.audit_store_url,
                    state.http_client,
                    state.settings.audit_api_key,
                )

                if next_model is not None:
                    continue
                else:
                    return PipelineResult(
                        success=False,
                        status_code=503,
                        imf=imf,
                        error_code="all_backends_exhausted",
                        latency_ms=_ms(t0),
                    )

            # -------------------------------------------------------------------
            # Stage 6: Cache Write + Routing Audit (fire-and-forget)
            # -------------------------------------------------------------------
            # Merge the response block from the inference result
            if "response" in result_imf:
                imf["response"] = result_imf["response"]

            # Only write to cache when the lookup was a MISS
            if not imf["cache"].get("lookup_hit"):
                background_tasks.add_task(
                    cache_write,
                    imf["request"]["messages"],
                    current_model,
                    task_type,
                    result_imf,
                    state.settings.cache_url,
                    state.http_client,
                )

            background_tasks.add_task(
                post_audit_event,
                _build_routing_audit(imf, "pass", _ms(t0)),
                state.settings.audit_store_url,
                state.http_client,
                state.settings.audit_api_key,
            )

            return PipelineResult(
                success=True,
                status_code=200,
                imf=imf,
                error_code=None,
                latency_ms=_ms(t0),
            )

    except Exception as exc:
        # -----------------------------------------------------------------------
        # Unhandled exception guard — always return 500 internal_error
        # -----------------------------------------------------------------------
        logger.error(
            "pipeline_internal_error",
            extra={
                "extra_fields": {
                    "request_id": request_id,
                    "error": str(exc),
                    "timestamp_utc": _utc_now(),
                }
            },
        )
        return PipelineResult(
            success=False,
            status_code=500,
            imf=imf,
            error_code="internal_error",
            latency_ms=_ms(t0),
        )
