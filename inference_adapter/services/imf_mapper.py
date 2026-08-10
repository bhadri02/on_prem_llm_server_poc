"""
IMFMapper — translates between IMF (Internal Message Format) documents and
the Ollama ``/api/chat`` wire format.

Provides two directions of translation:
  - ``to_ollama_request``:   IMFDocument + Settings → Ollama request dict
  - ``to_imf_response``:     IMFDocument + Ollama response dict → IMFDocument

Also exposes two pure helpers used internally and in tests:
  - ``resolve_finish_reason``
  - ``resolve_token_counts``

Validates: Requirements 7.1, 7.2, 7.4, 7.5, 7.6, 7.7, 7.8,
           2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8,
           3.1, 3.2, 3.3, 3.4, 3.5, 3.6,
           8.1, 8.2, 8.3, 8.4, 8.5
"""

from __future__ import annotations

import json
import math
import sys

from inference_adapter.config import Settings
from inference_adapter.schemas.imf import IMFDocument, IMFResponse, IMFUsage
from inference_adapter.services.anthropic_client import AnthropicInvalidResponseError
from inference_adapter.services.ollama_client import OllamaInvalidResponseError


class IMFMapper:
    """Pure-static translation layer between IMF and Ollama wire formats.

    All methods are ``@staticmethod`` — this class is a namespace, not an
    object.  Instantiation is never necessary.
    """

    # ------------------------------------------------------------------
    # IMF → Ollama
    # ------------------------------------------------------------------

    @staticmethod
    def to_ollama_request(imf: IMFDocument, settings: Settings) -> dict:
        """Translate an ``IMFDocument`` into an Ollama ``/api/chat`` payload.

        Args:
            imf:      The inbound IMF envelope.  ``routing.selected_model``
                      is used as the Ollama model name — never
                      ``request.model``.
            settings: Application settings supplying token and temperature
                      defaults/limits.

        Returns:
            A ``dict`` containing exactly four keys: ``model``, ``messages``,
            ``stream``, and ``options``.

        Side-effects:
            Writes a single JSON warning line to ``sys.stdout`` when
            ``request.max_tokens`` exceeds ``settings.max_tokens_limit``.

        Raises:
            Nothing — all edge cases are handled by clamping or defaulting.
        """
        # ---- model -------------------------------------------------------
        model = imf.routing.selected_model

        # ---- messages — plain dicts only (no Pydantic) -------------------
        messages = [
            {"role": m.role, "content": m.content}
            for m in imf.request.messages
        ]

        # ---- num_predict (max tokens) ------------------------------------
        requested_max = imf.request.max_tokens

        if not requested_max:  # None or 0
            num_predict = settings.default_max_tokens
        elif requested_max > settings.max_tokens_limit:
            num_predict = settings.max_tokens_limit
            sys.stdout.write(
                json.dumps(
                    {
                        "event": "max_tokens_clamped",
                        "requested": requested_max,
                        "clamped_to": settings.max_tokens_limit,
                    }
                )
                + "\n"
            )
        else:
            num_predict = requested_max

        # ---- temperature -------------------------------------------------
        temperature = (
            imf.request.temperature
            if imf.request.temperature is not None
            else settings.default_temperature
        )

        return {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {
                "num_predict": num_predict,
                "temperature": temperature,
            },
        }

    # ------------------------------------------------------------------
    # Ollama → IMF
    # ------------------------------------------------------------------

    @staticmethod
    def resolve_finish_reason(done_reason: str | None) -> str | None:
        """Map an Ollama ``done_reason`` string to an IMF ``finish_reason``.

        Args:
            done_reason: The ``done_reason`` field from the Ollama response,
                         or ``None`` if absent.

        Returns:
            ``"stop"``   if ``done_reason == "stop"``
            ``"length"`` if ``done_reason == "length"``
            ``None``     for every other value (including ``None``)
        """
        if done_reason == "stop":
            return "stop"
        if done_reason == "length":
            return "length"
        return None

    @staticmethod
    def resolve_token_counts(
        prompt_eval_count: int | None,
        eval_count: int | None,
    ) -> tuple[int, int, int]:
        """Convert Ollama token count fields to IMF usage counters.

        Args:
            prompt_eval_count: Ollama ``prompt_eval_count`` (prompt tokens).
                               ``None`` is treated as ``0``.
            eval_count:        Ollama ``eval_count`` (completion tokens).
                               ``None`` is treated as ``0``.

        Returns:
            A three-tuple ``(prompt_tokens, completion_tokens, total_tokens)``
            where all values are non-negative integers and
            ``total == prompt + completion``.
        """
        prompt_tokens = prompt_eval_count if prompt_eval_count is not None else 0
        completion_tokens = eval_count if eval_count is not None else 0
        total_tokens = prompt_tokens + completion_tokens
        return prompt_tokens, completion_tokens, total_tokens

    @staticmethod
    def to_imf_response(
        imf_in: IMFDocument,
        ollama_resp: dict,
        wall_clock_ms: int,
    ) -> IMFDocument:
        """Build an outbound ``IMFDocument`` from the Ollama response.

        Preserves all fields from ``imf_in`` that are not ``response``,
        ``metadata``, or ``extensions``.  Returns a **new** ``IMFDocument``
        — the input is never mutated.

        Args:
            imf_in:        The original inbound ``IMFDocument`` whose fields
                           (except ``response``, ``metadata``, ``extensions``)
                           are copied verbatim into the returned document.
            ollama_resp:   The parsed JSON dict returned by ``OllamaClient.chat()``.
            wall_clock_ms: Caller-measured wall-clock latency in milliseconds,
                           used as a fallback when ``total_duration`` is absent
                           or zero.

        Returns:
            A new ``IMFDocument`` with ``response``, ``metadata``, and
            ``extensions`` populated.

        Raises:
            OllamaInvalidResponseError: If ``ollama_resp`` is missing the
                ``"message"`` key or ``ollama_resp["message"]`` is missing
                the ``"content"`` key.
        """
        # ---- validate Ollama response structure --------------------------
        if "message" not in ollama_resp:
            raise OllamaInvalidResponseError(
                'Ollama response missing required key: "message"'
            )
        if "content" not in ollama_resp["message"]:
            raise OllamaInvalidResponseError(
                'Ollama response["message"] missing required key: "content"'
            )

        # ---- response block ----------------------------------------------
        content = ollama_resp["message"]["content"]
        finish_reason = IMFMapper.resolve_finish_reason(ollama_resp.get("done_reason"))

        prompt_tokens, completion_tokens, total_tokens = IMFMapper.resolve_token_counts(
            ollama_resp.get("prompt_eval_count"),
            ollama_resp.get("eval_count"),
        )

        response = IMFResponse(
            content=content,
            finish_reason=finish_reason,
            usage=IMFUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
            ),
        )

        # ---- latency calculation -----------------------------------------
        total_duration = ollama_resp.get("total_duration", 0)
        if total_duration > 0:
            inference_latency_ms = math.floor(total_duration / 1_000_000)
        else:
            inference_latency_ms = wall_clock_ms

        # ---- metadata (fresh dict — exactly three keys) ------------------
        metadata = {
            "inference_backend": "ollama",
            "inference_latency_ms": inference_latency_ms,
            "model_name": imf_in.routing.selected_model,
        }

        # ---- build new IMFDocument without mutating imf_in ---------------
        return imf_in.model_copy(
            update={
                "response": response,
                "metadata": metadata,
                "extensions": {},
            }
        )

    # ------------------------------------------------------------------
    # IMF → Anthropic
    # ------------------------------------------------------------------

    @staticmethod
    def to_anthropic_request(imf: IMFDocument, settings: Settings) -> dict:
        """Translate an ``IMFDocument`` into an Anthropic ``/v1/messages`` payload.

        Anthropic's wire format differs from Ollama's in two structural ways
        this mapping has to account for:
          - the system prompt is a top-level ``system`` string, not a
            ``{"role": "system", ...}`` entry inside ``messages``;
          - ``max_tokens`` is a required field, not an optional sampling knob.

        Args:
            imf:      The inbound IMF envelope. ``routing.selected_model``
                      is used as the Anthropic model name.
            settings: Application settings supplying token defaults/limits
                      (the same ``default_max_tokens`` / ``max_tokens_limit``
                      / ``default_temperature`` used for Ollama).

        Returns:
            A dict with keys ``model``, ``messages``, ``max_tokens``,
            ``temperature``, and ``system`` (only when a system message is
            present).
        """
        model = imf.routing.selected_model

        system_parts = [m.content for m in imf.request.messages if m.role == "system"]
        messages = [
            {"role": m.role, "content": m.content}
            for m in imf.request.messages
            if m.role != "system"
        ]

        requested_max = imf.request.max_tokens
        if not requested_max:
            max_tokens = settings.default_max_tokens
        elif requested_max > settings.max_tokens_limit:
            max_tokens = settings.max_tokens_limit
        else:
            max_tokens = requested_max

        temperature = (
            imf.request.temperature
            if imf.request.temperature is not None
            else settings.default_temperature
        )

        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system_parts:
            payload["system"] = "\n".join(system_parts)
        return payload

    # ------------------------------------------------------------------
    # Anthropic → IMF
    # ------------------------------------------------------------------

    @staticmethod
    def resolve_anthropic_finish_reason(stop_reason: str | None) -> str | None:
        """Map an Anthropic ``stop_reason`` string to an IMF ``finish_reason``.

        Args:
            stop_reason: The ``stop_reason`` field from the Anthropic
                         response, or ``None`` if absent.

        Returns:
            ``"stop"``   for ``"end_turn"`` or ``"stop_sequence"``
            ``"length"`` for ``"max_tokens"``
            ``None``     for every other value (including ``None`` and
                         ``"tool_use"`` — tool calls are out of scope here)
        """
        if stop_reason in ("end_turn", "stop_sequence"):
            return "stop"
        if stop_reason == "max_tokens":
            return "length"
        return None

    @staticmethod
    def to_imf_response_from_anthropic(
        imf_in: IMFDocument,
        anthropic_resp: dict,
        wall_clock_ms: int,
    ) -> IMFDocument:
        """Build an outbound ``IMFDocument`` from the Anthropic response.

        Preserves all fields from ``imf_in`` that are not ``response``,
        ``metadata``, or ``extensions``. Returns a **new** ``IMFDocument``
        — the input is never mutated. Mirrors ``to_imf_response`` (the
        Ollama equivalent).

        Args:
            imf_in:         The original inbound ``IMFDocument``.
            anthropic_resp: The parsed JSON dict returned by
                            ``AnthropicClient.messages()``.
            wall_clock_ms:  Caller-measured wall-clock latency in
                            milliseconds — Anthropic's response doesn't
                            report server-side generation time, so this is
                            the only latency figure available.

        Returns:
            A new ``IMFDocument`` with ``response``, ``metadata``, and
            ``extensions`` populated.

        Raises:
            AnthropicInvalidResponseError: If ``anthropic_resp["content"]``
                is missing, empty, or has no text block.
        """
        content_blocks = anthropic_resp.get("content")
        if not content_blocks or not isinstance(content_blocks, list):
            raise AnthropicInvalidResponseError(
                'Anthropic response missing or empty "content" array'
            )

        text_parts = [
            block.get("text", "")
            for block in content_blocks
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        if not text_parts:
            raise AnthropicInvalidResponseError(
                'Anthropic response "content" has no text block'
            )
        content = "".join(text_parts)

        finish_reason = IMFMapper.resolve_anthropic_finish_reason(
            anthropic_resp.get("stop_reason")
        )

        usage_block = anthropic_resp.get("usage") or {}
        prompt_tokens = usage_block.get("input_tokens") or 0
        completion_tokens = usage_block.get("output_tokens") or 0

        response = IMFResponse(
            content=content,
            finish_reason=finish_reason,
            usage=IMFUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
            ),
        )

        metadata = {
            "inference_backend": "anthropic",
            "inference_latency_ms": wall_clock_ms,
            "model_name": imf_in.routing.selected_model,
        }

        return imf_in.model_copy(
            update={
                "response": response,
                "metadata": metadata,
                "extensions": {},
            }
        )
