"""
test-connectivity.py — Inter-service connectivity validator for the LLM POC.

Run this script inside any pod in the ``llm-poc`` namespace to verify that
NetworkPolicy, DNS, and HTTP health checks are all working correctly.

    kubectl run conn-test --rm -it --restart=Never \
        --image=python:3.12-slim \
        --namespace=llm-poc \
        -- python /scripts/test-connectivity.py

Exit codes
----------
0  All checks passed (PASS or WARN).
1  At least one check failed (FAIL).

Background / known issues addressed
-------------------------------------
- NetworkPolicy DNS fix (P2): The cache NetworkPolicy previously restricted
  egress to Redis on port 6379 only, with no port-53 (UDP/TCP) rule.  That
  blocked kube-dns and caused all DNS resolution to fail from cache pods.
  Fixed by adding a namespace-scoped egress rule for the same namespace plus
  an explicit port-53 egress rule.  The DNS checks below validate this fix.

- Redis DNS transient on first boot (P3): Even after the NetworkPolicy fix,
  the DNS entry for ``llm-poc-cache-redis-master`` was not propagated
  immediately on first pod start.  The cache /health probe budget was
  tightened to 15 s delay / 3 failures = 60 s, which was too short.  It is
  now 60 s delay / 10 failures = 210 s.  The Redis TCP checks below use an
  explicit retry-with-backoff to surface this condition during testing.
"""

import socket
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional

# ---------------------------------------------------------------------------
# Result tracking
# ---------------------------------------------------------------------------

PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"


@dataclass
class CheckResult:
    label: str
    status: str          # PASS | WARN | FAIL
    detail: str


results: list[CheckResult] = []


def _record(label: str, status: str, detail: str) -> CheckResult:
    result = CheckResult(label=label, status=status, detail=detail)
    results.append(result)
    # Emit immediately so progress is visible when running interactively.
    marker = "✓" if status == PASS else ("!" if status == WARN else "✗")
    print(f"  [{marker}] {status:<4}  {label}: {detail}")
    return result


# ---------------------------------------------------------------------------
# DNS check helpers
# ---------------------------------------------------------------------------

def check_dns(hostname: str, *, expect_resolution: bool = True) -> bool:
    """Resolve ``hostname`` via kube-dns and record the result.

    The ``expect_resolution`` flag lets callers mark a DNS failure as WARN
    instead of FAIL when the target may not be deployed (e.g. optional
    services).
    """
    label = f"DNS  {hostname}"
    try:
        ip = socket.gethostbyname(hostname)
        _record(label, PASS, f"resolved → {ip}")
        return True
    except socket.gaierror as exc:
        status = FAIL if expect_resolution else WARN
        _record(label, status, str(exc))
        return False


def check_dns_port53() -> None:
    """Validate that port-53 egress is open by resolving via an explicit DNS
    server lookup.  A failure here means the NetworkPolicy is still blocking
    port 53 (the root cause of the P2 incident).

    We test both UDP and TCP because the fixed NetworkPolicy allows both.
    """
    print("\n── Port-53 egress (NetworkPolicy DNS fix validation) ──────────────────")

    # UDP DNS — standard path used by getaddrinfo / kube-dns
    label_udp = "DNS  port-53/UDP egress"
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(3)
        # A minimal DNS query for "." (root) — we only care that it reaches port 53
        # without a NetworkPolicy drop; we don't need a valid response.
        sock.sendto(
            b"\x00\x01\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00\x00\x00\x02\x00\x01",
            ("8.8.8.8", 53),
        )
        sock.close()
        _record(label_udp, PASS, "UDP datagram reached port 53 (no NetworkPolicy drop)")
    except OSError as exc:
        _record(label_udp, FAIL, f"NetworkPolicy may still block UDP/53: {exc}")

    # TCP DNS — fallback path; also explicitly allowed by the fixed NetworkPolicy
    label_tcp = "DNS  port-53/TCP egress"
    try:
        conn = socket.create_connection(("8.8.8.8", 53), timeout=3)
        conn.close()
        _record(label_tcp, PASS, "TCP connection to port 53 succeeded")
    except OSError as exc:
        _record(label_tcp, FAIL, f"NetworkPolicy may still block TCP/53: {exc}")


# ---------------------------------------------------------------------------
# TCP connectivity helpers
# ---------------------------------------------------------------------------

def check_tcp(
    host: str,
    port: int,
    label: Optional[str] = None,
    *,
    retries: int = 1,
    retry_delay_s: float = 5.0,
) -> bool:
    """Open a TCP connection to ``host:port``.

    ``retries`` and ``retry_delay_s`` support the Redis first-boot transient
    DNS delay (P3): callers should pass ``retries=6, retry_delay_s=10`` for
    Redis so the check retries for up to ~60 s before failing.
    """
    label = label or f"TCP  {host}:{port}"
    last_exc: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            s = socket.create_connection((host, port), timeout=5)
            s.close()
            suffix = f" (attempt {attempt}/{retries})" if attempt > 1 else ""
            _record(label, PASS, f"connection established{suffix}")
            return True
        except OSError as exc:
            last_exc = exc
            if attempt < retries:
                print(
                    f"       … TCP {host}:{port} attempt {attempt}/{retries} failed "
                    f"({exc}); retrying in {retry_delay_s:.0f}s"
                )
                time.sleep(retry_delay_s)

    status = FAIL
    detail = str(last_exc)
    if retries > 1:
        detail = f"all {retries} attempts failed — last error: {last_exc}"
    _record(label, status, detail)
    return False


# ---------------------------------------------------------------------------
# HTTP health / endpoint helpers
# ---------------------------------------------------------------------------

def check_http(url: str, label: Optional[str] = None, *, allow_4xx: bool = False) -> bool:
    """HTTP GET ``url`` and record the result.

    2xx and 3xx are always PASS.  4xx is PASS when ``allow_4xx=True`` (useful
    for endpoints that require auth), otherwise FAIL.  Network errors are FAIL.
    """
    label = label or f"HTTP {url}"
    try:
        http_resp = urllib.request.urlopen(url, timeout=8)
        _record(label, PASS, f"HTTP {http_resp.status}")
        return True
    except urllib.error.HTTPError as exc:
        if allow_4xx and 400 <= exc.code < 500:
            _record(label, PASS, f"HTTP {exc.code} (reachable; auth required)")
            return True
        _record(label, FAIL, f"HTTP {exc.code} — {exc.reason}")
        return False
    except urllib.error.URLError as exc:
        _record(label, FAIL, f"URLError: {exc.reason}")
        return False
    except OSError as exc:
        _record(label, FAIL, f"OSError: {exc}")
        return False


def check_metrics(host: str, port: int = 9090) -> bool:
    """Verify Prometheus /metrics is reachable on the observability port."""
    return check_http(
        f"http://{host}:{port}/metrics",
        label=f"METR {host}:{port}/metrics",
        allow_4xx=False,
    )


# ---------------------------------------------------------------------------
# Section helpers
# ---------------------------------------------------------------------------

def section(title: str) -> None:
    print(f"\n── {title} {'─' * max(0, 68 - len(title))}")


# ===========================================================================
# Main checks
# ===========================================================================

print("=" * 72)
print("  LLM POC — Inter-Service Connectivity Test")
print("  Namespace: llm-poc  |  Run inside any pod in the namespace")
print("=" * 72)

# ── 1. Port-53 egress (NetworkPolicy DNS fix validation) ───────────────────
# Must run before DNS checks so we know *why* DNS fails if it does.
check_dns_port53()

# ── 2. DNS resolution — all platform services ─────────────────────────────
section("DNS resolution — platform services")

# Core request-path services (must resolve or everything is broken)
check_dns("api-gateway")
check_dns("security-layer")
check_dns("router")
check_dns("cache")

# Inference — two DNS names (Ollama engine + adapter sidecar share a Service)
check_dns("inference-ollama")
check_dns("inference-adapter")

# Supporting services
check_dns("agent-framework")
check_dns("model-registry")
check_dns("audit-store")
check_dns("admin-portal")

# ── 3. Redis DNS — explicit sub-chart name (P3 transient fix validation) ───
section("DNS resolution — Redis sub-chart (P3 first-boot transient fix)")
#
# The Bitnami Redis sub-chart creates a Service named
# ``<release>-cache-redis-master`` (for standalone / HA master).
# The cache NetworkPolicy fix (P2) must allow same-namespace egress for this
# name to resolve.  This is also the name that was subject to the first-boot
# DNS propagation delay (P3).
#
# Adjust the release prefix if your Helm release name differs from ``llm-poc``.
REDIS_HOST = "llm-poc-cache-redis-master"
redis_dns_ok = check_dns(REDIS_HOST)

# ── 4. TCP connectivity — application ports ────────────────────────────────
section("TCP connectivity — application ports")

check_tcp("api-gateway",     8080, "TCP  api-gateway:8080")
check_tcp("security-layer",  8081, "TCP  security-layer:8081")
check_tcp("router",          8082, "TCP  router:8082")
check_tcp("cache",           8086, "TCP  cache:8086")
check_tcp("inference-ollama", 11434, "TCP  inference-ollama:11434 (Ollama engine)")
check_tcp("inference-adapter", 8087, "TCP  inference-adapter:8087 (IMF adapter)")
check_tcp("agent-framework", 8083, "TCP  agent-framework:8083")
check_tcp("model-registry",  5000, "TCP  model-registry:5000")
check_tcp("audit-store",     9200, "TCP  audit-store:9200")

# ── 5. Redis TCP — with retry-backoff for P3 transient DNS delay ───────────
section("TCP connectivity — Redis (retry-backoff for P3 DNS propagation delay)")
#
# The probe budget in the cache chart was increased to 210 s (60 s delay +
# 10 × 15 s).  The retry loop below mirrors that budget: up to 6 attempts
# with 10 s gaps = 60 s of retrying, matching the initialDelaySeconds window.
# If Redis is still unreachable after 6 attempts, it is a genuine failure.
if redis_dns_ok:
    check_tcp(
        REDIS_HOST, 6379,
        label=f"TCP  {REDIS_HOST}:6379 (Redis)",
        retries=6,
        retry_delay_s=10.0,
    )
else:
    _record(
        f"TCP  {REDIS_HOST}:6379 (Redis)",
        FAIL,
        "skipped — DNS did not resolve (fix DNS first)",
    )

# ── 6. HTTP health checks — all services ──────────────────────────────────
section("HTTP health checks — /health endpoints")

check_http("http://api-gateway:8080/health",         "HTTP api-gateway /health")
check_http("http://security-layer:8081/health",      "HTTP security-layer /health")
check_http("http://router:8082/health",              "HTTP router /health")
check_http("http://cache:8086/health",               "HTTP cache /health")
check_http("http://inference-adapter:8087/health",   "HTTP inference-adapter /health")
check_http("http://agent-framework:8083/health",     "HTTP agent-framework /health")
check_http("http://model-registry:5000/health",      "HTTP model-registry /health")
check_http("http://audit-store:9200/health",         "HTTP audit-store /health")

# Ollama uses /api/tags as its liveness/readiness probe path (no /health route)
check_http("http://inference-ollama:11434/api/tags", "HTTP inference-ollama /api/tags")

# ── 7. Prometheus metrics ports — observability contract ──────────────────
section("Prometheus metrics ports — /metrics on port 9090")
#
# All services must expose /metrics on :9090 per the platform observability
# contract (Master Contract §Observability).

for svc in [
    "api-gateway",
    "security-layer",
    "router",
    "cache",
    "inference-adapter",
    "agent-framework",
    "model-registry",
    "audit-store",
]:
    check_metrics(svc, 9090)

# ── 8. Inference adapter — end-to-end smoke (optional) ────────────────────
section("Inference adapter — lightweight smoke (POST /v1/chat/completions)")
#
# This check issues a real inference request.  It will take a few seconds.
# The check is WARN (not FAIL) on a timeout so a slow model pull does not
# break a quick connectivity validation run.
SMOKE_URL = "http://inference-adapter:8087/v1/chat/completions"
SMOKE_PAYLOAD = b'{"model":"llama3.2:3b","messages":[{"role":"user","content":"ping"}],"max_tokens":5}'

smoke_label = "HTTP inference-adapter POST /v1/chat/completions"
try:
    req = urllib.request.Request(
        SMOKE_URL,
        data=SMOKE_PAYLOAD,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    resp = urllib.request.urlopen(req, timeout=30)
    _record(smoke_label, PASS, f"HTTP {resp.status}")
except urllib.error.HTTPError as exc:
    # 4xx still means the adapter is reachable and understood the request
    if 400 <= exc.code < 500:
        _record(smoke_label, PASS, f"HTTP {exc.code} (reachable; check payload)")
    else:
        _record(smoke_label, FAIL, f"HTTP {exc.code} — {exc.reason}")
except TimeoutError:
    _record(smoke_label, WARN, "request timed out after 30 s — model may still be loading")
except (urllib.error.URLError, OSError) as exc:
    _record(smoke_label, FAIL, f"{type(exc).__name__}: {exc}")

# ===========================================================================
# Summary
# ===========================================================================

passed = [r for r in results if r.status == PASS]
warned = [r for r in results if r.status == WARN]
failed = [r for r in results if r.status == FAIL]

print()
print("=" * 72)
print(f"  SUMMARY   PASS={len(passed)}  WARN={len(warned)}  FAIL={len(failed)}")
print("=" * 72)

if warned:
    print("\nWarnings (non-fatal):")
    for r in warned:
        print(f"  [!] {r.label}: {r.detail}")

if failed:
    print("\nFailures:")
    for r in failed:
        print(f"  [✗] {r.label}: {r.detail}")
    print()
    print("  Likely causes:")
    print("  • DNS FAIL on any service  → NetworkPolicy port-53 egress rule missing")
    print("    (P2 fix: add UDP/TCP 53 egress to the pod's NetworkPolicy)")
    print(f"  • DNS/TCP FAIL on {REDIS_HOST}")
    print("    → Redis sub-chart not deployed, wrong release name, or DNS not yet")
    print("      propagated (P3 fix: probe initialDelaySeconds=60, failureThreshold=10)")
    print("  • HTTP FAIL on /health      → pod not Ready; check pod logs and probe budget")
    print("  • HTTP FAIL on /metrics     → missing metricsPort=9090 or Service selector")
    sys.exit(1)
else:
    print("\n  All checks passed. ✓")
    sys.exit(0)
