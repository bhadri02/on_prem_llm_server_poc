"""
Example tests for the /models router endpoints.

Validates: Requirements 1.10, 2.1–2.3, 3.1–3.4, 4.1–4.6, 5.1–5.6, 6.1–6.4
"""

import pytest

# Fixed API key matching the settings_override fixture in conftest.py
TEST_KEY = "test-secret-key"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def model_payload(**overrides) -> dict:
    """Build a minimal valid POST /models request body."""
    data = {
        "name": "test-model",
        "version": "1.0",
        "backend": "ollama",
        "endpoint": "http://inference:11434",
        "tasks": ["chat"],
        "status": "active",
    }
    data.update(overrides)
    return data


AUTH = {"X-API-Key": TEST_KEY}

# Auth middleware checks the path exactly. The router prefix is "/models" with
# route "/", making the full FastAPI path "/models/". However, the auth
# middleware pattern checks path == "/models". Use the path without trailing
# slash so that the request reaches the middleware with the correct path before
# FastAPI's routing potentially redirects.  httpx follows redirects by default,
# so we disable redirect following on POST to observe the actual auth response.
POST_URL = "/models"


async def _post(client, payload, headers=None, follow_redirects=False):
    """POST to /models without following redirects so we observe auth directly."""
    return await client.post(
        POST_URL,
        json=payload,
        headers=headers or {},
        follow_redirects=follow_redirects,
    )


# ---------------------------------------------------------------------------
# GET /models
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_models_empty(async_client):
    """GET /models returns [] on an empty store."""
    response = await async_client.get("/models/")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.anyio
async def test_get_models_returns_all_after_registration(async_client):
    """GET /models returns all registered records."""
    await _post(async_client, model_payload(name="model-a"), AUTH, follow_redirects=True)
    await _post(async_client, model_payload(name="model-b"), AUTH, follow_redirects=True)

    response = await async_client.get("/models/")
    assert response.status_code == 200
    names = {r["name"] for r in response.json()}
    assert names == {"model-a", "model-b"}


# ---------------------------------------------------------------------------
# GET /models/{name}
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_model_by_name_200(async_client):
    """GET /models/{name} returns 200 with the correct record."""
    await _post(async_client, model_payload(), AUTH, follow_redirects=True)

    response = await async_client.get("/models/test-model")
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "test-model"
    assert body["version"] == "1.0"


@pytest.mark.anyio
async def test_get_model_by_name_404(async_client):
    """GET /models/{name} returns 404 for an unknown model."""
    response = await async_client.get("/models/nonexistent")
    assert response.status_code == 404
    assert "detail" in response.json()


@pytest.mark.anyio
async def test_get_model_by_name_case_sensitive_404(async_client):
    """GET /models/{name} is case-sensitive; wrong-case name returns 404."""
    await _post(async_client, model_payload(name="mymodel"), AUTH, follow_redirects=True)

    response = await async_client.get("/models/MyModel")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_get_model_invalid_chars_422(async_client):
    """GET /models/{name} returns 422 when name contains invalid chars."""
    # '@' is not in [a-zA-Z0-9._-]
    response = await async_client.get("/models/bad@name")
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# POST /models
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_post_model_201(async_client):
    """POST /models with valid payload and auth returns 201 with the record."""
    response = await _post(async_client, model_payload(), AUTH, follow_redirects=True)

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "test-model"
    assert body["version"] == "1.0"
    # registered_at must always be present
    assert body.get("registered_at") is not None


@pytest.mark.anyio
async def test_post_model_optional_fields_serialised_as_null(async_client):
    """POST /models — optional fields not supplied are returned as null, not omitted."""
    response = await _post(async_client, model_payload(), AUTH, follow_redirects=True)

    assert response.status_code == 201
    body = response.json()
    # Optional fields present as null
    assert "vram_required_gb" in body
    assert body["vram_required_gb"] is None
    assert "fallback_model" in body
    assert body["fallback_model"] is None


@pytest.mark.anyio
async def test_post_model_409_duplicate(async_client):
    """POST /models with a duplicate name returns 409."""
    await _post(async_client, model_payload(), AUTH, follow_redirects=True)

    response = await _post(
        async_client, model_payload(version="2.0"), AUTH, follow_redirects=True
    )
    assert response.status_code == 409
    assert "detail" in response.json()


@pytest.mark.anyio
async def test_post_model_422_missing_required_field(async_client):
    """POST /models without a required field returns 422."""
    payload = model_payload()
    del payload["name"]

    response = await _post(async_client, payload, AUTH, follow_redirects=True)
    assert response.status_code == 422


@pytest.mark.anyio
async def test_post_model_401_no_key(async_client):
    """POST /models without X-API-Key returns 401 (key checked before validation)."""
    response = await _post(async_client, model_payload())
    assert response.status_code == 401
    assert "detail" in response.json()


@pytest.mark.anyio
async def test_post_model_401_wrong_key(async_client):
    """POST /models with wrong X-API-Key returns 401."""
    response = await _post(
        async_client, model_payload(), {"X-API-Key": "wrong-key"}
    )
    assert response.status_code == 401


@pytest.mark.anyio
async def test_post_model_401_before_body_validation(async_client):
    """Auth check happens before Pydantic validation — 401 even for invalid body."""
    # Body is intentionally invalid (missing required fields) but key is wrong
    response = await _post(
        async_client, {"bad": "body"}, {"X-API-Key": "wrong-key"}
    )
    # Should be 401, not 422 — auth fires before body validation
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# PATCH /models/{name}/status
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_patch_status_200(async_client):
    """PATCH /models/{name}/status with valid status and auth returns 200."""
    await _post(async_client, model_payload(), AUTH, follow_redirects=True)

    response = await async_client.patch(
        "/models/test-model/status",
        json={"status": "staging"},
        headers=AUTH,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "staging"


@pytest.mark.anyio
async def test_patch_status_only_changes_status(async_client):
    """PATCH /models/{name}/status leaves all other fields unchanged."""
    payload = model_payload(
        version="2.5",
        vram_required_gb=8.0,
        notes="do not change me",
    )
    create_resp = await _post(async_client, payload, AUTH, follow_redirects=True)
    original = create_resp.json()

    patch_resp = await async_client.patch(
        "/models/test-model/status",
        json={"status": "retired"},
        headers=AUTH,
    )
    updated = patch_resp.json()

    assert updated["status"] == "retired"
    # Every field other than status must be identical
    for field in ("name", "version", "backend", "endpoint", "tasks",
                  "vram_required_gb", "max_context_length", "fallback_model",
                  "registered_at", "notes"):
        assert updated[field] == original[field], f"Field '{field}' changed unexpectedly"


@pytest.mark.anyio
async def test_patch_status_404_unknown(async_client):
    """PATCH to a non-existent model with valid key returns 404."""
    response = await async_client.patch(
        "/models/no-such-model/status",
        json={"status": "retired"},
        headers=AUTH,
    )
    assert response.status_code == 404


@pytest.mark.anyio
async def test_patch_status_401_no_key(async_client):
    """PATCH /models/{name}/status without auth header returns 401."""
    await _post(async_client, model_payload(), AUTH, follow_redirects=True)

    response = await async_client.patch(
        "/models/test-model/status",
        json={"status": "staging"},
    )
    assert response.status_code == 401


@pytest.mark.anyio
async def test_patch_status_401_wrong_key(async_client):
    """PATCH /models/{name}/status with wrong key returns 401."""
    await _post(async_client, model_payload(), AUTH, follow_redirects=True)

    response = await async_client.patch(
        "/models/test-model/status",
        json={"status": "staging"},
        headers={"X-API-Key": "bad-key"},
    )
    assert response.status_code == 401


@pytest.mark.anyio
async def test_patch_status_404_after_key_validated(async_client):
    """Req 5.6: 404 for non-existent model only returned AFTER key is validated.
    A valid key + non-existent model must yield 404, not 401."""
    response = await async_client.patch(
        "/models/definitely-not-there/status",
        json={"status": "active"},
        headers=AUTH,
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /models/by-task/{task_type}
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_by_task_returns_active_only(async_client):
    """GET /models/by-task/{task_type} returns only active models for that task."""
    await _post(
        async_client,
        model_payload(name="active-chat", status="active", tasks=["chat"]),
        AUTH,
        follow_redirects=True,
    )
    await _post(
        async_client,
        model_payload(name="staging-chat", status="staging", tasks=["chat"]),
        AUTH,
        follow_redirects=True,
    )

    response = await async_client.get("/models/by-task/chat")
    assert response.status_code == 200
    results = response.json()
    names = [r["name"] for r in results]
    assert "active-chat" in names
    assert "staging-chat" not in names


@pytest.mark.anyio
async def test_get_by_task_empty_when_no_match(async_client):
    """GET /models/by-task/{task_type} returns [] when no active model matches."""
    response = await async_client.get("/models/by-task/vision")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.anyio
async def test_get_by_task_422_invalid_task(async_client):
    """GET /models/by-task/{task_type} returns 422 for an unrecognised task type."""
    response = await async_client.get("/models/by-task/invalid-task-type")
    assert response.status_code == 422


@pytest.mark.anyio
async def test_by_task_route_not_treated_as_model_name(async_client):
    """GET /models/by-task/chat must be handled by the by-task route,
    not the {name} route; response is a list."""
    response = await async_client.get("/models/by-task/chat")
    assert response.status_code == 200
    assert isinstance(response.json(), list)
