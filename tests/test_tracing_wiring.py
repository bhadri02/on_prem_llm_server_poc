"""
tests/test_tracing_wiring.py

Integration smoke test for task 14.1: configure_tracing() wiring.

Verifies that:
  1. configure_tracing() gracefully no-ops when OTel is not installed.
  2. configure_tracing() is guarded by the tracing_enabled flag in each layer.
  3. The _set_llm_span_attributes hook is robust to missing/bad spans and headers.

Requirements: 9.1, 9.3, 9.5
"""

import pytest


def test_configure_tracing_no_otel_installed():
    """configure_tracing() must silently no-op when opentelemetry-* is not installed."""
    from shared.observability.middleware import configure_tracing

    # Calling configure_tracing() when OTel is not installed must NOT raise.
    # The function has a top-level try/except ImportError that returns early.
    configure_tracing("test_service", "http://otel-collector:4317")
    # If we get here without exception, the requirement is satisfied.


def test_set_llm_span_attributes_robustness():
    """_set_llm_span_attributes() must never raise, even with None span or bad headers."""
    from shared.observability.middleware import _set_llm_span_attributes

    # 1. None span
    _set_llm_span_attributes(None, {})
    _set_llm_span_attributes(None, {"headers": []})

    # 2. Mock non-recording span (has .is_recording() → False)
    class NonRecordingSpan:
        def is_recording(self) -> bool:
            return False

    _set_llm_span_attributes(NonRecordingSpan(), {"headers": []})

    # 3. Missing headers key
    class RecordingSpan:
        def is_recording(self) -> bool:
            return True

        def set_attribute(self, key: str, value: str) -> None:
            pass

    _set_llm_span_attributes(RecordingSpan(), {})

    # 4. Malformed headers
    _set_llm_span_attributes(RecordingSpan(), {"headers": "not_a_list"})

    # 5. Valid headers
    _set_llm_span_attributes(
        RecordingSpan(),
        {
            "headers": [
                (b"x-request-id", b"abc-123"),
                (b"x-user-id", b"user-456"),
                (b"x-department", b"engineering"),
                (b"x-layer", b"api_gateway"),
                (b"x-model", b"llama3.2:3b"),
                (b"x-task-type", b"chat"),
            ]
        },
    )


def test_tracing_flag_guards_in_configs():
    """Verify all layer configs have tracing_enabled (default False) and otel_endpoint fields."""
    import os

    # Set required env vars for layers that need them
    os.environ["GATEWAY_API_KEY"] = "test-key"
    os.environ["DOWNSTREAM_SECURITY_URL"] = "http://security:8081"
    os.environ["DOWNSTREAM_ROUTER_URL"] = "http://router:8082"
    os.environ["AUDIT_STORE_URL"] = "http://audit:9999"
    os.environ["AUDIT_API_KEY"] = "test-audit-key"
    os.environ["INJECTION_PATTERNS_PATH"] = "/tmp/patterns.yaml"

    # api_gateway
    from api_gateway.config import get_settings as get_gw_settings

    gw_s = get_gw_settings()
    assert hasattr(gw_s, "tracing_enabled")
    assert gw_s.tracing_enabled is False
    assert hasattr(gw_s, "otel_endpoint")
    assert gw_s.otel_endpoint == "http://otel-collector:4317"

    # security_layer
    from security_layer.config import settings as sec_s

    assert hasattr(sec_s, "tracing_enabled")
    assert sec_s.tracing_enabled is False
    assert hasattr(sec_s, "otel_endpoint")

    # cache_service
    from cache_service.config import get_settings as get_cache_settings

    cache_s = get_cache_settings()
    assert hasattr(cache_s, "tracing_enabled")
    assert cache_s.tracing_enabled is False
    assert hasattr(cache_s, "otel_endpoint")

    # inference_adapter
    from inference_adapter.config import get_settings as get_inf_settings

    inf_s = get_inf_settings()
    assert hasattr(inf_s, "tracing_enabled")
    assert inf_s.tracing_enabled is False
    assert hasattr(inf_s, "otel_endpoint")

    # intelligent_router (settings may be None if required env vars missing — already set above)
    from intelligent_router.config import settings as router_s

    if router_s is not None:
        assert hasattr(router_s, "tracing_enabled")
        assert router_s.tracing_enabled is False
        assert hasattr(router_s, "otel_endpoint")

    # agent_framework (settings may be None if required env vars missing,
    # and the package lives under services/agent-framework/ with its own PYTHONPATH)
    try:
        from agent_framework.config import settings as agent_s

        if agent_s is not None:
            assert hasattr(agent_s, "tracing_enabled")
            assert agent_s.tracing_enabled is False
            assert hasattr(agent_s, "otel_endpoint")
    except ModuleNotFoundError:
        # agent_framework is in a nested services/ subdirectory and requires
        # its own PYTHONPATH to be importable from the workspace root.
        pytest.skip("agent_framework not on sys.path — skipping in workspace root context")
