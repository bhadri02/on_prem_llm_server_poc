# Swagger UI Testing Guide — Admin Portal (User + Admin)

Step-by-step walkthrough for testing every `admin_portal` feature directly
from its Swagger UI, covering both role perspectives now that real login
exists (Phase 6): **as a regular user** (chat, playground, own visibility)
and **as an admin** (user/role/model/key management, audit, Ollama sync).

**Swagger UI:** http://localhost:8084/portal/docs
**Raw OpenAPI spec:** http://localhost:8084/portal/openapi.json

This covers `admin_portal` only — it's the one surface with both role tiers.
Other services have their own default Swagger docs if you need to poke at
the raw pipeline directly: `api_gateway` → http://localhost:8080/docs,
`model_registry` → http://localhost:5001/docs, `intelligent_router` →
http://localhost:8082/docs. Not covered here.

---

## 0. Before you start

1. Stack must be running (`.\scripts\run-local.ps1`, or at minimum
   `admin_portal` + Postgres + `model_registry` + `api_gateway` +
   `intelligent_router` + `inference_adapter` + Ollama for the chat tests).
2. Open **http://localhost:8084/portal/docs** in a browser. Swagger UI's
   "Try it out" runs real `fetch()` calls from that page's origin, so the
   session cookie set by login is sent automatically on every subsequent
   call in the same browser tab — no manual header wiring needed, even
   though the cookie is httpOnly (that only blocks *JavaScript* from
   reading it, not the browser from sending it).
3. Known seeded login: **`admin` / `admin123`** (change this in a real
   deployment — see `admin_portal/config.py::SEED_ADMIN_PASSWORD`).

---

## Part A — Auth (`auth` tag)

Do this first — almost everything else 401s without it.

| # | Endpoint | Body | Expected |
|---|---|---|---|
| A1 | `GET /portal/auth/me` | — | **401** `{"detail":{"error":"unauthorized","message":"Not logged in."}}` — confirms the gate works before you've logged in. |
| A2 | `POST /portal/auth/login` | `{"username":"admin","password":"admin123"}` | **200**, body has `roles:["admin"]`. Check your browser's dev tools → Application → Cookies → a `portal_session` cookie now exists for `localhost:8084`, marked `HttpOnly`. |
| A3 | `GET /portal/auth/me` | — | **200**, same identity as A2 — proves the cookie is being sent automatically. |
| A4 | `POST /portal/auth/login` (wrong password) | `{"username":"admin","password":"wrong"}` | **401** `{"error":"invalid_credentials",...}` |
| A5 | `POST /portal/auth/login` (unknown user) | `{"username":"nobody","password":"x"}` | **401** |
| A6 | `POST /portal/auth/logout` | — | **204** no body |
| A7 | `GET /portal/auth/me` (after A6) | — | **401** — session is genuinely gone, not just client-side forgotten |

**Re-login as admin (A2) before continuing to Part B.**

---

## Part B — Admin-only features

Everything here requires the `admin` role. Stay logged in as `admin` for
this whole section.

### B1. User management (`users` tag)

| # | Endpoint | Body | Expected |
|---|---|---|---|
| B1.1 | `GET /portal/users/` | — | **200**, array including the seeded `admin` user |
| B1.2 | `POST /portal/users/` | `{"username":"e2e-dev","email":"dev@test.local","department":"Engineering","roles":["developer"],"password":"devpass123"}` | **201**, returns the new user with `user_id` — **copy this ID**, you'll need it below |
| B1.3 | `POST /portal/users/` (duplicate) | same `username` as B1.2 | **409** `{"error":"already_exists",...}` |
| B1.4 | `POST /portal/users/` (bad role) | `{"username":"e2e-bad","roles":["not-a-role"]}` | **422** `{"error":"invalid_role",...}` |
| B1.5 | `GET /portal/users/{user_id}` | user_id from B1.2 | **200**, matches |
| B1.6 | `GET /portal/users/{user_id}` (garbage id) | `user_id=does-not-exist` | **404** |
| B1.7 | `PATCH /portal/users/{user_id}` | `{"status":"inactive"}` | **200**, `status` now `inactive` |
| B1.8 | `PATCH /portal/users/{user_id}` | `{"status":"active"}` | **200**, flip it back — needed for later steps |
| B1.9 | `PATCH /portal/users/{user_id}/roles` | `{"roles":["viewer"]}` | **200**, `roles` now `["viewer"]` (full replace, not additive) |
| B1.10 | `PATCH /portal/users/{user_id}/roles` | `{"roles":["developer"]}` | **200**, put it back to `developer` for the entitlement tests below |
| B1.11 | `PATCH /portal/users/{user_id}/password` | `{"password":"newpass456"}` | **200** — note this new password, you'll log in with it in Part C |
| B1.12 | `DELETE /portal/users/{user_id}` | — | **204** — **do this LAST**, after Part C is done with this user, since it soft-deletes (sets `status=inactive`) |

### B2. Per-user API keys (`users` tag, nested)

Use the `user_id` from B1.2.

| # | Endpoint | Body | Expected |
|---|---|---|---|
| B2.1 | `POST /portal/users/{user_id}/keys` | `{"label":"e2e test key","model_entitlements":["llama3.2:3b"]}` | **201**, response includes `raw_key` — **copy it now, it is never shown again**. Also copy `key_id`. |
| B2.2 | `GET /portal/users/{user_id}/keys` | — | **200**, array with the key from B2.1 — confirm `raw_key`/`key_hash` are **not** present in this listing |
| B2.3 | `PATCH /portal/users/{user_id}/keys/{key_id}/models` | `{"model_entitlements":["llama3.2:3b","claude-sonnet-5"]}` | **200**, entitlements updated |
| B2.4 | `DELETE /portal/users/{user_id}/keys/{key_id}` | — | **200**, `status:"revoked"` |
| B2.5 | `GET /portal/users/{user_id}/keys` | — | **200**, the key now shows `status:"revoked"` |

### B3. Roles & permission matrix (`roles` tag)

| # | Endpoint | Body | Expected |
|---|---|---|---|
| B3.1 | `GET /portal/roles/` | — | **200**, 4 roles: `viewer`, `analyst`, `developer`, `admin` |
| B3.2 | `GET /portal/roles/{role}/permissions` | `role=viewer` | **200**, `permissions: {}` — an **empty** object. The seed never inserts rows for `viewer` at all (absence of a row = deny), so don't expect explicit `false` values here, just nothing. |
| B3.3 | `GET /portal/roles/{role}/permissions` | `role=not-a-role` | **404** |
| B3.4 | `PATCH /portal/roles/{role}/permissions` | `role=analyst`, `{"permissions":{"code":true}}` | **200**, `code` now `true` for analyst |
| B3.5 | `GET /portal/roles/{role}/permissions` | `role=analyst` | **200**, confirms B3.4 persisted |
| B3.6 | **Cleanup** — `PATCH /portal/roles/{role}/permissions` | `role=analyst`, `{"permissions":{"code":false}}` | **200** — revert; this is a real, shared table, don't leave it dirty |

✅ This now takes effect on real enforcement, not just what the API reports —
`intelligent_router` polls `GET /portal/policy/matrix` (an internal,
key-gated endpoint) on a ~15s TTL cache
(`intelligent_router/services/policy_resolver.py`), so a change here shows
up in real request routing within that window, no Router restart needed.
Verify it yourself: with an `analyst`-role key, a "write a python function"
request gets `403 policy_denied` (analyst lacks `code` by default) — do
B3.4, wait ~15s, retry the exact same request, and it now succeeds with a
real response, no restart in between.

⚠️ Deliberately used `analyst`/`code` above, not `viewer`, for a reason
worth knowing: `security_layer` has a **separate, still fully static**
coarse gate (`ALLOWED_ROLES` in `security_layer/policy.py`, a hardcoded
Python frozenset — not backed by this table, not pollable) that rejects
`viewer` before it ever reaches this fine-grained matrix. Granting `viewer`
a task here will update what the API reports but can never take live
effect, since `viewer` is blocked one layer earlier regardless. Every other
role (`analyst`/`developer`/`admin`) passes that gate and is fully governed
by this table.

### B4. Model registry management (`models` tag, admin parts)

| # | Endpoint | Body | Expected |
|---|---|---|---|
| B4.1 | `GET /portal/models` | — | **200** (this one's login-only, not admin-only — any logged-in user can list) |
| B4.2 | `POST /portal/models` | `{"name":"swagger-test-model","version":"1.0","backend":"ollama","endpoint":"http://localhost:11434","tasks":["chat"],"status":"staging"}` | **201**, `api_key_set:false` |
| B4.3 | `POST /portal/models` (duplicate) | same `name` | **409** |
| B4.4 | `PATCH /portal/models/{name}/status` | `name=swagger-test-model`, `{"status":"active"}` | **200** |
| B4.5 | `PATCH /portal/models/{name}/status` (bad value) | `{"status":"deleted"}` | **422**, `allowed_values` includes `active/retired/staging` |
| B4.6 | `POST /portal/models` (cloud, no key) | `{"name":"swagger-cloud-test","version":"1.0","backend":"anthropic","endpoint":"https://api.anthropic.com","tasks":["chat"]}` — omit `api_key` | **201 still succeeds** (registry doesn't hard-enforce this — a documented gap; note it, don't be alarmed) |
| B4.7 | `PATCH /portal/models/{name}/api-key` | `name=swagger-cloud-test`, `{"api_key":"sk-ant-fake-for-testing"}` | **200**, `api_key_set:true` |
| B4.8 | `PATCH /portal/models/{name}/api-key` (unknown model) | `name=does-not-exist` | **404** |
| B4.9 | `POST /portal/models/sync-ollama` | `{}` | **200**, `{"pulled":null,"ollama_models":[...],"registered":[...],"already_registered":[...],"failed":{}}` — should show `llama3.2:3b` (and `swagger-test-model` if it collides — it won't, different name) already registered |
| B4.10 | `POST /portal/models/sync-ollama` (with pull) | `{"model":"llama3.2:1b"}` **only if you're OK downloading it** — otherwise skip | **200**, `pulled:"llama3.2:1b"`, takes a while |

### B5. Admin-wide API keys (`keys` tag)

| # | Endpoint | Expected |
|---|---|---|
| B5.1 | `GET /portal/keys/` | **200**, flat array of every key across every user, each with `owner_username` and `user_id` |
| B5.2 | `GET /portal/keys/resolve?key=admin123` (no `X-Portal-Internal-Key` header) | **401** — this one is service-to-service only, not session-gated; Swagger UI has no easy way to set that header by default, this call is really meant for `api_gateway` to call server-to-server. Confirm it 401s and move on. |

### B6. Audit (`audit` tag)

| # | Endpoint | Expected |
|---|---|---|
| B6.1 | `GET /portal/audit/events?limit=10` | **200**, `{"events":[...]}` sorted newest-first |
| B6.2 | `GET /portal/audit/events?limit=0` | **400** `validation_error`, limit must be 1–200 |
| B6.3 | `GET /portal/audit/events?from=not-a-date` | **400** |
| B6.4 | `GET /portal/audit/requests/{request_id}` — grab a real `request_id` from B6.1's results | **200**, all events for that one request |
| B6.5 | `GET /portal/audit/requests/{request_id}` — `request_id=not-a-uuid` | **400** |

### B7. Metrics (`metrics` tag)

| # | Endpoint | Expected |
|---|---|---|
| B7.1 | `GET /portal/metrics/summary` | **200**, `{request_rate, error_rate, cache_hit_rate, active_users}` — fields may be `null` if Prometheus has no data yet, that's not a failure |

---

## Part C — Regular-user features

**Log out (A6), then log in as the developer user created in B1.2** —
`e2e-dev` / `newpass456` (the password you set in B1.11). If you already
deleted that user (B1.12), create a fresh one first.

### C1. Confirm the role boundary

| # | Endpoint | Expected |
|---|---|---|
| C1.1 | `GET /portal/auth/me` | **200**, `roles:["developer"]` |
| C1.2 | `GET /portal/users/` | **403** `{"error":"forbidden","message":"admin role required."}` — a non-admin genuinely cannot reach admin endpoints, even logged in |
| C1.3 | `POST /portal/models` | **403**, same reason |
| C1.4 | `GET /portal/keys/` | **403** |
| C1.5 | `GET /portal/audit/events` | **403** |
| C1.6 | `GET /portal/metrics/summary` | **403** |

### C2. What a regular user CAN do

| # | Endpoint | Body | Expected |
|---|---|---|---|
| C2.1 | `GET /portal/roles/` | — | **200** — read-only role list is fine for anyone logged in |
| C2.2 | `GET /portal/roles/developer/permissions` | — | **200** |
| C2.3 | `GET /portal/models` | — | **200** — anyone logged in can browse the catalog |
| C2.4 | `GET /portal/chat/models` | — | **200**, each model annotated `entitled: true/false` based on **this user's own** key entitlements — this is the real per-user RBAC signal introduced in Phase 6 |
| C2.5 | `POST /portal/chat/completions` | `{"model":"llama3.2:3b","messages":[{"role":"user","content":"Say hello in one word."}],"temperature":0.7}` | **200**, a real model reply — dispatched using **this session's own key**, not the portal's fixed key |
| C2.6 | `POST /portal/chat/completions` (unentitled model) | pin a model NOT in this user's entitlements (check C2.4's `entitled:false` list) | **403** `{"error":"model_not_entitled","allowed_models":[...]}` — proves per-user entitlement is really enforced through the whole pipeline now, not just cosmetically in the UI |
| C2.7 | `POST /portal/playground/chat` | `{"model":"llama3.2:3b","messages":[{"role":"user","content":"hi"}],"temperature":0.7}` | **200** |

### C3. Role-based task denial

Log out, log in as a **`viewer`**-role user (create one via admin if you
don't have one: repeat B1.2 with `"roles":["viewer"]`, set a password via
B1.11).

| # | Endpoint | Expected |
|---|---|---|
| C3.1 | `POST /portal/chat/completions` (any model) | **403** `{"error":"policy_denied",...}` — but note this specific denial comes from `security_layer`'s separate, static coarse gate (`viewer` isn't in `ALLOWED_ROLES`), not the fine-grained matrix in B3 — `viewer` would be denied here even if the fine-grained matrix granted every task type |

---

## Part D — Public endpoints (no login needed either way)

Quick sanity check these still work without any session:

| # | Endpoint | Expected |
|---|---|---|
| D1 | `GET /portal/health` | **200** |
| D2 | `GET /portal/config` | **200**, `{"grafana_url": "..."}` |

---

## Cleanup checklist

Things you mutated above that are worth resetting if this is a shared/demo
environment, not a throwaway:

- [ ] B3.6 — reverted `analyst`'s `code` permission back to `false`
- [ ] B1.12 — deactivated the `e2e-dev` test user (or leave it — it's inert)
- [ ] B4.2/B4.6 — `swagger-test-model` / `swagger-cloud-test` are now in the
      Model Registry catalog permanently (no delete endpoint exists — see
      `docs/FRONTEND_INTEGRATION.md` §2.4). Harmless (they're not in
      `model_matrix.yaml`, so nothing can actually route to them), but if
      you want a clean catalog, edit `models.json` directly.
- [ ] Any test API keys created in B2 are revoked, not deleted (there's no
      delete-key endpoint by design — revoked keys stay for audit history).

---

## Known-gap reminders while testing

These are **expected**, documented behaviors, not bugs to report:

1. Registering a model (B4.2) does not take live effect on real request
   routing — `model_matrix.yaml` is still a static file loaded once at
   Router startup (unlike role permissions, below, this one has not been
   made dynamic). Editing role permissions (B3.4), by contrast, now DOES
   take live effect within ~15s via `intelligent_router`'s TTL-cached poll
   of `GET /portal/policy/matrix` — no Router restart needed. The one
   exception is `viewer`, which is blocked by `security_layer`'s separate,
   still-static coarse role gate regardless of what's granted here.
2. `POST /portal/chat/completions` requires a pinned `model` — there's no
   "let the Router auto-pick" path through this proxy.
3. `/portal/keys/resolve` (B5.2) is intentionally not reachable via a
   session — it's the one endpoint still gated by the old service-to-service
   `X-Portal-Internal-Key` mechanism, used only by `api_gateway`.
4. Chat has no server-side conversation history — every
   `/portal/chat/completions` call is stateless; Swagger UI won't show any
   "previous messages" state between calls.

See `docs/FRONTEND_INTEGRATION.md` and `docs/END_TO_END_TESTING.md` for the
fuller architecture writeups these gaps come from.
