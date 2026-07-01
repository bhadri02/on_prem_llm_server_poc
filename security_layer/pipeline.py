"""
pipeline.py — Pre- and post-generation pipeline orchestrator.

Provides:

- ``PipelineResult``: dataclass capturing the outcome of the pre-generation pipeline.
- ``run_pre_pipeline``: enforces the strict four-stage pre-generation order:
    1. Injection scan
    2. Content safety filter
    3. PII masking on request.messages
    4. Role-based policy check
- ``run_post_pipeline``: applies PII masking to response.content and returns
  the enriched IMF together with the list of detected entity types.
"""

import time
from dataclasses import dataclass

from security_layer.content_safety import check_content_safety
from security_layer.injection import scan_for_injection
from security_layer.pii import mask_messages, mask_text
from security_layer.policy import check_policy


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class PipelineResult:
    """Outcome of a pre-generation pipeline run.

    Attributes:
        blocked: ``True`` when a pipeline stage vetoed the request.
        block_reason: One of ``"injection_detected"``,
            ``"content_safety_violation"``, or ``"policy_denied"``; ``None``
            when the request passed all stages.
        block_status: HTTP status code to return to the caller (``400`` or
            ``403``); ``None`` when the request was not blocked.
        imf: The enriched IMF dict — mutated in-place by each stage.
        latency_ms: Elapsed wall-clock time for the pipeline run in
            milliseconds.
    """

    blocked: bool
    block_reason: str | None  # injection_detected | content_safety_violation | policy_denied
    block_status: int | None  # 400 or 403
    imf: dict  # enriched IMF (mutated in-place)
    latency_ms: int


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _ms(t0: float) -> int:
    """Return elapsed milliseconds since *t0* as an integer."""
    return int((time.monotonic() - t0) * 1000)


# ---------------------------------------------------------------------------
# Pre-generation pipeline
# ---------------------------------------------------------------------------


async def run_pre_pipeline(imf: dict, state) -> PipelineResult:
    """Execute the four-stage pre-generation pipeline.

    Stages execute in strict order with short-circuit semantics — a blocking
    stage returns immediately without executing subsequent stages.

    Stage ordering:
        1. **Injection scan** — sets ``governance.injection_score``; blocks
           with HTTP 400 / ``"injection_detected"`` when score is ``1.0``.
        2. **Content safety** — sets ``governance.content_safety_passed``;
           blocks with HTTP 400 / ``"content_safety_violation"`` when ``False``.
        3. **PII masking** on ``request.messages`` — replaces the messages
           list in-place; sets ``governance.pii_masked`` and
           ``governance.pii_fields_detected``.
        4. **Policy check** — appends decision to
           ``governance.policy_decisions``; blocks with HTTP 403 /
           ``"policy_denied"`` when denied.

    On a fully passing pipeline, sets ``governance.human_approval_required``
    to ``False`` and ``governance.human_approval_status`` to
    ``"not_required"`` (POC phase — human approval always bypassed).

    Args:
        imf: The inbound IMF dict.  Mutated in-place by every stage.
        state: The ``app.state`` object carrying ``patterns``, ``blocklist``,
            ``analyzer``, ``anonymizer``, and ``settings``.

    Returns:
        A :class:`PipelineResult` describing whether the request was blocked
        and the enriched IMF.
    """
    t0 = time.monotonic()

    # ------------------------------------------------------------------
    # Stage 1: Injection scan
    # ------------------------------------------------------------------
    score = scan_for_injection(imf["request"]["messages"], state.patterns)
    imf["governance"]["injection_score"] = score
    if score == 1.0:
        return PipelineResult(
            blocked=True,
            block_reason="injection_detected",
            block_status=400,
            imf=imf,
            latency_ms=_ms(t0),
        )

    # ------------------------------------------------------------------
    # Stage 2: Content safety
    # ------------------------------------------------------------------
    safe = check_content_safety(imf["request"]["messages"], state.blocklist)
    imf["governance"]["content_safety_passed"] = safe
    if not safe:
        return PipelineResult(
            blocked=True,
            block_reason="content_safety_violation",
            block_status=400,
            imf=imf,
            latency_ms=_ms(t0),
        )

    # ------------------------------------------------------------------
    # Stage 3: PII masking on request.messages
    # ------------------------------------------------------------------
    masked_messages, entities = mask_messages(
        imf["request"]["messages"],
        state.analyzer,
        state.anonymizer,
        state.settings.pii_enabled,
    )
    imf["request"]["messages"] = masked_messages
    imf["governance"]["pii_masked"] = len(entities) > 0
    imf["governance"]["pii_fields_detected"] = entities

    # ------------------------------------------------------------------
    # Stage 4: Policy check
    # ------------------------------------------------------------------
    user_block = imf.get("user") or {}
    roles = user_block.get("roles") if isinstance(user_block, dict) else None
    permitted, decision = check_policy(roles)
    imf["governance"]["policy_decisions"].append(decision)
    if not permitted:
        return PipelineResult(
            blocked=True,
            block_reason="policy_denied",
            block_status=403,
            imf=imf,
            latency_ms=_ms(t0),
        )

    # ------------------------------------------------------------------
    # POC: human approval always not required
    # ------------------------------------------------------------------
    imf["governance"]["human_approval_required"] = False
    imf["governance"]["human_approval_status"] = "not_required"

    return PipelineResult(
        blocked=False,
        block_reason=None,
        block_status=None,
        imf=imf,
        latency_ms=_ms(t0),
    )


# ---------------------------------------------------------------------------
# Post-generation pipeline
# ---------------------------------------------------------------------------


async def run_post_pipeline(imf: dict, state) -> tuple[dict, list[str]]:
    """Apply PII masking to ``response.content`` if present and non-null.

    When ``response.content`` is ``None``, empty, or absent the IMF is
    returned unchanged with an empty entity list.

    When PII entities are found, ``governance.pii_masked`` is set to ``True``
    and ``governance.pii_fields_detected`` is updated to the deduplicated
    union of previously detected fields and the new ones.

    Args:
        imf: The IMF dict from the post-generation stage.  Mutated in-place
            when PII is found.
        state: The ``app.state`` object carrying ``analyzer``, ``anonymizer``,
            and ``settings``.

    Returns:
        A 2-tuple ``(enriched_imf, detected_entity_types)`` where
        *detected_entity_types* is the list of entity types found in
        ``response.content`` (may be empty).
    """
    response = imf.get("response") or {}
    content = response.get("content") if isinstance(response, dict) else None
    if not content:
        return imf, []

    masked_content, entities = mask_text(
        content,
        state.analyzer,
        state.anonymizer,
        state.settings.pii_enabled,
    )
    imf["response"]["content"] = masked_content
    if entities:
        imf["governance"]["pii_masked"] = True
        imf["governance"]["pii_fields_detected"] = list(
            dict.fromkeys(
                list(imf["governance"].get("pii_fields_detected", []))
                + list(entities)
            )
        )

    return imf, entities
