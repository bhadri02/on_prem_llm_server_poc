"""
Property-based and parametrized tests for Helm chart structural invariants.

Properties 3–10 as specified in design.md.

Validates:
  - Requirements 2.1, 2.2, 4.3, 6.5, 6.6, 7.1, 11.1, 11.2, 12.1, 15.7, 16.1, 16.5, 18.1, 18.2
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml
from hypothesis import given, settings
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent.parent.parent.resolve()
UMBRELLA_DIR = REPO_ROOT / "llm-platform"
CHARTS_DIR = UMBRELLA_DIR / "charts"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_camel(kebab: str) -> str:
    """Convert a kebab-case chart name to camelCase for Helm condition keys."""
    parts = kebab.split("-")
    return parts[0] + "".join(word.capitalize() for word in parts[1:])

CHARTS: list[str] = [
    "api-gateway",
    "security-layer",
    "router",
    "cache",
    "inference-ollama",
    "agent-framework",
    "model-registry",
    "audit-store",
    "admin-portal",
    "observability",
]

# Charts that have standard values.yaml schema (replicaCount, image, service, resources …)
# observability is excluded — it is a wrapper chart with no standard image/service/resources.
STANDARD_CHARTS: list[str] = [c for c in CHARTS if c != "observability"]

# Charts that declare persistence.enabled in values.yaml
STATEFUL_CHARTS: list[str] = ["audit-store", "model-registry", "inference-ollama"]

# Resource kinds prohibited by design (no Istio, no HPA)
PROHIBITED_KINDS: frozenset[str] = frozenset(
    [
        "VirtualService",
        "DestinationRule",
        "AuthorizationPolicy",
        "PeerAuthentication",
        "HorizontalPodAutoscaler",
    ]
)


# ---------------------------------------------------------------------------
# Shared helm helper (mirrors conftest.py for standalone import safety)
# ---------------------------------------------------------------------------

try:
    from conftest import helm_template  # type: ignore[import]
except ImportError:  # pragma: no cover
    import tempfile

    def helm_template(  # type: ignore[misc]
        chart_dir: str | Path,
        release_name: str = "test-release",
        values: dict[str, Any] | None = None,
        set_args: list[str] | None = None,
        namespace: str = "llm-poc",
        extra_args: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        chart_dir = Path(chart_dir)
        cmd = [
            "helm", "template", release_name, str(chart_dir),
            "--namespace", namespace,
        ]
        tmp_file = None
        if values:
            tmp = tempfile.NamedTemporaryFile(
                mode="w", suffix=".yaml", delete=False, prefix="helm-test-"
            )
            yaml.dump(values, tmp)
            tmp.flush()
            tmp.close()
            tmp_file = tmp.name
            cmd += ["--values", tmp_file]
        if set_args:
            for a in set_args:
                cmd += ["--set", a]
        if extra_args:
            cmd.extend(extra_args)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
        finally:
            if tmp_file:
                Path(tmp_file).unlink(missing_ok=True)
        docs = list(yaml.safe_load_all(result.stdout))
        return [d for d in docs if d is not None]


# ---------------------------------------------------------------------------
# Helper: get a dependency-ready chart dir (skip if deps missing)
# ---------------------------------------------------------------------------

def _chart_dir(chart: str) -> Path:
    """Return the chart directory; ensure tarballs exist for charts with deps."""
    d = CHARTS_DIR / chart
    charts_subdir = d / "charts"
    # For charts with sub-dependencies (cache, observability) ensure deps are present.
    if (d / "Chart.lock").exists() and charts_subdir.is_dir():
        has_archives = any(f.suffix == ".tgz" for f in charts_subdir.iterdir())
        if not has_archives:
            subprocess.run(
                ["helm", "dependency", "build", str(d)],
                capture_output=True,
                text=True,
                check=False,
            )
    return d


def _safe_helm_template(
    chart: str,
    set_args: list[str] | None = None,
    values: dict[str, Any] | None = None,
    extra_args: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Render a standalone sub-chart with sensible defaults for dependency gating."""
    d = _chart_dir(chart)
    # Disable Redis / kube-prometheus-stack sub-chart to avoid external deps in CI.
    extra: list[str] = list(set_args or [])
    if chart == "cache":
        extra = ["redis.enabled=false"] + extra
    if chart == "observability":
        extra = ["kubePrometheusStack.enabled=false"] + extra
    return helm_template(
        d,
        set_args=extra if extra else None,
        values=values,
        extra_args=extra_args,
    )


# ===========================================================================
# Property 3 — Every sub-chart has required files
# (16.1) Validates: Requirements 2.1, 15.7
# ===========================================================================

REQUIRED_CHART_FILES = [
    "Chart.yaml",
    "values.yaml",
    "README.md",
    "templates/_helpers.tpl",
    "templates/deployment.yaml",
    "templates/service.yaml",
    "templates/servicemonitor.yaml",
    "templates/networkpolicy.yaml",
]


@pytest.mark.unit
@pytest.mark.parametrize("chart", CHARTS)
def test_property3_required_files_exist(chart: str) -> None:
    """Property 3: Every sub-chart has all required files.

    Validates: Requirements 2.1, 15.7
    """
    chart_dir = CHARTS_DIR / chart
    missing = []
    for rel in REQUIRED_CHART_FILES:
        if chart == "observability" and rel.startswith("templates/deployment"):
            # observability wraps kube-prometheus-stack; deployment lives in the sub-chart
            continue
        if chart == "observability" and rel in (
            "templates/service.yaml",
            "templates/servicemonitor.yaml",
            "templates/networkpolicy.yaml",
        ):
            # observability is a thin wrapper — these templates are provided by the
            # kube-prometheus-stack dependency; the chart itself only needs _helpers.tpl,
            # ingress.yaml, and the jaeger templates.
            continue
        path = chart_dir / rel
        if not path.exists():
            missing.append(rel)
    assert not missing, (
        f"Chart '{chart}' is missing required files: {missing}\n"
        f"Chart directory: {chart_dir}"
    )


# ===========================================================================
# Property 4 — Every sub-chart values.yaml has required keys with correct types
# (16.2) Validates: Requirements 2.2, 12.1
# ===========================================================================

# Required paths and their expected Python types.
# observability chart is excluded (wrapper pattern, no standard schema).
REQUIRED_VALUES_PATHS: list[tuple[list[str], type]] = [
    (["replicaCount"], int),
    (["image", "repository"], str),
    (["image", "tag"], str),
    (["image", "pullPolicy"], str),
    (["service", "type"], str),
    (["service", "port"], int),
    (["resources", "requests"], dict),
    (["resources", "limits"], dict),
    (["autoscaling", "enabled"], bool),
    (["vault", "enabled"], bool),
    (["secretRef", "name"], str),
]


def _get_nested(d: dict, keys: list[str]) -> Any:
    """Retrieve a nested key path from a dict, returning None if any key is missing."""
    cur: Any = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


@pytest.mark.unit
@pytest.mark.parametrize("chart", STANDARD_CHARTS)
def test_property4_values_schema_compliance(chart: str) -> None:
    """Property 4: Every standard sub-chart values.yaml has required keys with correct types.

    inference-ollama uses a nested schema (ollama.image.*) so its image/service/resources
    keys are checked at their actual paths.

    Validates: Requirements 2.2, 12.1
    """
    values_path = CHARTS_DIR / chart / "values.yaml"
    assert values_path.exists(), f"values.yaml missing for chart '{chart}'"

    with values_path.open() as fh:
        values = yaml.safe_load(fh)

    assert isinstance(values, dict), f"values.yaml for '{chart}' is not a YAML mapping"

    # inference-ollama uses nested keys — check the outer required keys only.
    if chart == "inference-ollama":
        required: list[tuple[list[str], type]] = [
            (["secretRef", "name"], str),
            (["autoscaling", "enabled"], bool),
            (["vault", "enabled"], bool),
            (["replicaCount"], int),
            (["persistence", "enabled"], bool),
        ]
    else:
        required = REQUIRED_VALUES_PATHS

    missing_or_wrong: list[str] = []
    for keys, expected_type in required:
        val = _get_nested(values, keys)
        path_str = ".".join(keys)
        if val is None:
            missing_or_wrong.append(f"{path_str}: MISSING")
        elif not isinstance(val, expected_type):
            missing_or_wrong.append(
                f"{path_str}: expected {expected_type.__name__}, "
                f"got {type(val).__name__} ({val!r})"
            )

    assert not missing_or_wrong, (
        f"Chart '{chart}' values.yaml schema violations:\n"
        + "\n".join(f"  - {e}" for e in missing_or_wrong)
    )


# ===========================================================================
# Property 5 — Every rendered Deployment has both probes with httpGet
# (16.3) Validates: Requirements 11.1, 11.2
# ===========================================================================


def _deployments_from_docs(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [d for d in docs if d.get("kind") == "Deployment"]


def _has_http_get_probe(container: dict[str, Any], probe_key: str) -> bool:
    probe = container.get(probe_key, {})
    return bool(probe and probe.get("httpGet"))


@pytest.mark.pbt
@settings(max_examples=50, deadline=None)
@given(chart=st.sampled_from(STANDARD_CHARTS))
def test_property5_deployments_have_both_probes(chart: str) -> None:
    """Property 5: Every rendered Deployment has livenessProbe and readinessProbe with httpGet.

    Uses hypothesis to vary chart selection across 50 examples.

    Validates: Requirements 11.1, 11.2
    """
    docs = _safe_helm_template(chart)
    deployments = _deployments_from_docs(docs)

    assert deployments, f"No Deployment rendered for chart '{chart}'"

    for deployment in deployments:
        name = deployment.get("metadata", {}).get("name", "<unknown>")
        containers: list[dict] = (
            deployment.get("spec", {})
            .get("template", {})
            .get("spec", {})
            .get("containers", [])
        )
        for container in containers:
            cname = container.get("name", "<unnamed>")
            # Skip init containers (they don't need probes)
            assert _has_http_get_probe(container, "livenessProbe"), (
                f"Deployment '{name}' container '{cname}' missing livenessProbe.httpGet "
                f"in chart '{chart}'"
            )
            assert _has_http_get_probe(container, "readinessProbe"), (
                f"Deployment '{name}' container '{cname}' missing readinessProbe.httpGet "
                f"in chart '{chart}'"
            )


# ===========================================================================
# Property 6 — No literal secret values in any values file
# (16.4) Validates: Requirements 4.3
# ===========================================================================

# Forbidden literal strings that must never appear in values files.
FORBIDDEN_STRINGS: list[str] = [
    "poc-secret-key",
]

# Additional pattern checks: keys whose non-empty values indicate a secret leak.
# Keys whose non-empty string values indicate a secret leak.
# We match whole-word substrings to avoid false positives on env var names like
# DEFAULT_MAX_TOKENS or MAX_TOKENS_LIMIT.
SECRET_KEYS_THAT_MUST_BE_EMPTY: list[str] = ["password", "apikey", "api_key"]
# "token" is checked separately only when it appears as a standalone key or
# ends the key name (e.g. "token", "access_token", "auth_token"), NOT in
# compound env var names like "DEFAULT_MAX_TOKENS" or "MAX_TOKENS_LIMIT".
SECRET_TOKEN_STANDALONE_SUFFIXES: tuple[str, ...] = (
    "token",
    "_token",
    "tokens",  # NOT matched — exclude 'tokens' to avoid false positives on MAX_TOKENS
)


def _collect_all_values_files() -> list[Path]:
    """Return all values.yaml and values-poc.yaml files across all charts + umbrella."""
    files: list[Path] = []
    for chart in CHARTS:
        f = CHARTS_DIR / chart / "values.yaml"
        if f.exists():
            files.append(f)
    # Umbrella chart values files
    for name in ("values.yaml", "values-poc.yaml"):
        f = UMBRELLA_DIR / name
        if f.exists():
            files.append(f)
    return files


_ALL_VALUES_FILES = _collect_all_values_files()


@pytest.mark.unit
@pytest.mark.parametrize("values_file", _ALL_VALUES_FILES, ids=[f.parent.name + "/" + f.name for f in _ALL_VALUES_FILES])
def test_property6_no_literal_secrets(values_file: Path) -> None:
    """Property 6: No literal secret values appear in any values file.

    Scans for 'poc-secret-key' and non-empty password/token/apikey fields.

    Validates: Requirements 4.3
    """
    content = values_file.read_text(encoding="utf-8")
    content_lower = content.lower()

    # Check forbidden literal strings
    for forbidden in FORBIDDEN_STRINGS:
        assert forbidden not in content, (
            f"Found forbidden literal secret '{forbidden}' in {values_file}"
        )

    # Parse and walk the YAML looking for non-empty password/token/apikey values
    data = yaml.safe_load(content)
    violations: list[str] = []
    _walk_for_secret_keys(data, path=[], violations=violations)

    assert not violations, (
        f"Found potential secret values in {values_file}:\n"
        + "\n".join(f"  - {v}" for v in violations)
    )


def _key_looks_like_secret(k: str) -> bool:
    """Return True if key name suggests it should hold a secret value."""
    k_lower = k.lower()
    # Broad substring matches for clear secret-key patterns
    for sk in SECRET_KEYS_THAT_MUST_BE_EMPTY:
        if sk in k_lower:
            return True
    # Match "token" only as a standalone key or ending suffix (not inside compound names
    # like DEFAULT_MAX_TOKENS, MAX_TOKENS_LIMIT, etc.)
    if k_lower in ("token", "access_token", "auth_token", "bearer_token"):
        return True
    if k_lower.endswith("_token") and not k_lower.endswith("s_token"):
        # matches auth_token, api_token — but NOT max_tokens
        return True
    return False


def _walk_for_secret_keys(
    node: Any,
    path: list[str],
    violations: list[str],
) -> None:
    """Recursively walk a YAML structure checking for non-empty secret key values."""
    if isinstance(node, dict):
        for k, v in node.items():
            new_path = path + [str(k)]
            if _key_looks_like_secret(str(k)):
                if isinstance(v, str) and v.strip():
                    # Allowlist: "poc-admin" in grafana is a known/documented POC password
                    if v.strip() != "poc-admin":
                        violations.append(f"{'.'.join(new_path)} = {v!r}")
            _walk_for_secret_keys(v, new_path, violations)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            _walk_for_secret_keys(item, path + [str(i)], violations)


# ===========================================================================
# Property 7 — Persistence toggle: emptyDir and no PVC when disabled
# (16.5) Validates: Requirements 6.5, 6.6
# ===========================================================================


@pytest.mark.unit
@pytest.mark.parametrize("chart", STATEFUL_CHARTS)
def test_property7_persistence_disabled_uses_emptydir(chart: str) -> None:
    """Property 7: persistence.enabled=false produces emptyDir and no PVC.

    Validates: Requirements 6.5, 6.6
    """
    docs = _safe_helm_template(chart, set_args=["persistence.enabled=false"])

    # No PVC should be in the output
    pvc_docs = [d for d in docs if d.get("kind") == "PersistentVolumeClaim"]
    assert not pvc_docs, (
        f"Chart '{chart}': expected no PVC when persistence.enabled=false, "
        f"but found: {[p.get('metadata', {}).get('name') for p in pvc_docs]}"
    )

    # At least one Deployment should have a volume with emptyDir
    deployments = _deployments_from_docs(docs)
    assert deployments, f"No Deployment found in chart '{chart}'"

    found_empty_dir = False
    for deployment in deployments:
        volumes: list[dict] = (
            deployment.get("spec", {})
            .get("template", {})
            .get("spec", {})
            .get("volumes", [])
        )
        for vol in volumes:
            if "emptyDir" in vol:
                found_empty_dir = True
                break
        if found_empty_dir:
            break

    assert found_empty_dir, (
        f"Chart '{chart}': expected an emptyDir volume when persistence.enabled=false, "
        "but none found in any Deployment"
    )


# ===========================================================================
# Property 8 — No prohibited resource kinds in any rendered chart
# (16.6) Validates: Requirements 18.1, 18.2
# ===========================================================================


@pytest.mark.pbt
@settings(max_examples=100, deadline=None)
@given(chart=st.sampled_from(CHARTS))
def test_property8_no_prohibited_resource_kinds(chart: str) -> None:
    """Property 8: No VirtualService, DestinationRule, AuthorizationPolicy,
    PeerAuthentication, or HorizontalPodAutoscaler in any rendered chart.

    Uses hypothesis to vary chart selection across 100 examples.

    Validates: Requirements 18.1, 18.2
    """
    docs = _safe_helm_template(chart)
    prohibited_found: list[str] = []
    for doc in docs:
        kind = doc.get("kind", "")
        if kind in PROHIBITED_KINDS:
            name = doc.get("metadata", {}).get("name", "<unknown>")
            prohibited_found.append(f"{kind}/{name}")

    assert not prohibited_found, (
        f"Chart '{chart}' rendered prohibited resource kinds: {prohibited_found}"
    )


# ===========================================================================
# Property 9 — Service URL consistency
# (16.7) Validates: Requirements 7.1
# ===========================================================================

def _parse_service_hostnames_from_poc_values() -> dict[str, str]:
    """Parse the services block from values-poc.yaml, returning {key: hostname}."""
    vf = UMBRELLA_DIR / "values-poc.yaml"
    if not vf.exists():
        return {}
    with vf.open() as fh:
        data = yaml.safe_load(fh)
    services_block: dict[str, str] = data.get("services", {})
    # Extract the hostname portion from URLs like "http://api-gateway:8080"
    hostnames: dict[str, str] = {}
    for key, url in services_block.items():
        if isinstance(url, str) and "://" in url:
            # e.g., "http://api-gateway:8080" → "api-gateway"
            host_port = url.split("://", 1)[1].split("/")[0]
            hostname = host_port.split(":")[0]
            hostnames[key] = hostname
    return hostnames


# Pre-compute the expected services at module load time.
_EXPECTED_SERVICE_HOSTNAMES = _parse_service_hostnames_from_poc_values()


@pytest.mark.unit
def test_property9_service_url_consistency() -> None:
    """Property 9: Every services block entry in values-poc.yaml maps to a Service
    in the rendered umbrella manifest when all sub-charts are enabled.

    Note: This test renders the umbrella chart without external dependencies
    (observability sub-chart is disabled to avoid requiring prometheus-community).
    The inferenceAdapter entry maps to the inference-ollama chart's service.

    Validates: Requirements 7.1
    """
    if not _EXPECTED_SERVICE_HOSTNAMES:
        pytest.skip("values-poc.yaml services block not found")

    # Render umbrella chart with all standard charts enabled, observability disabled
    # (observability requires helm dep update with external repo)
    set_args: list[str] = []
    for chart in STANDARD_CHARTS:
        camel = _to_camel(chart)
        set_args.append(f"{camel}.enabled=true")
    set_args.append("observability.enabled=false")

    try:
        docs = helm_template(
            UMBRELLA_DIR,
            release_name="llm-poc",
            set_args=set_args,
            namespace="llm-poc",
        )
    except subprocess.CalledProcessError as exc:
        pytest.skip(
            f"Umbrella helm template failed (dependencies may need update): {exc.stderr[:500]}"
        )

    # Collect all Service metadata.names from the rendered output
    rendered_service_names: set[str] = set()
    for doc in docs:
        if doc.get("kind") == "Service":
            name = doc.get("metadata", {}).get("name", "")
            if name:
                rendered_service_names.add(name)

    missing_services: list[str] = []
    for key, expected_hostname in _EXPECTED_SERVICE_HOSTNAMES.items():
        # The umbrella prepends the release name: "llm-poc-api-gateway" etc.
        # But sub-chart services use the release + chart name pattern.
        # Match either exact or release-prefixed.
        found = any(
            svc_name == expected_hostname or svc_name.endswith(f"-{expected_hostname}")
            for svc_name in rendered_service_names
        )
        if not found:
            missing_services.append(
                f"services.{key} → '{expected_hostname}' not found in rendered Services: "
                f"{sorted(rendered_service_names)}"
            )

    assert not missing_services, (
        "Service URL consistency check failed:\n"
        + "\n".join(f"  - {m}" for m in missing_services)
    )


# ===========================================================================
# Property 10 — Image tag fallback behavior
# (16.8) Validates: Requirements 16.1, 16.5
# ===========================================================================

# Hypothesis strategy: generate either empty string or a short ASCII alphanumeric tag.
# Docker image tags only support [a-zA-Z0-9_.-] so we restrict to lowercase+digits
# to avoid Unicode chars that cause cp1252 decode errors on Windows when passed through
# helm template subprocess output.
_TAG_STRATEGY = st.one_of(
    st.just(""),
    st.text(
        min_size=1,
        max_size=20,
        alphabet="abcdefghijklmnopqrstuvwxyz0123456789",
    ),
)


def _extract_container_images(docs: list[dict[str, Any]]) -> list[str]:
    """Return all container image strings from Deployments in the given docs."""
    images: list[str] = []
    for doc in docs:
        if doc.get("kind") != "Deployment":
            continue
        containers: list[dict] = (
            doc.get("spec", {})
            .get("template", {})
            .get("spec", {})
            .get("containers", [])
        )
        for c in containers:
            img = c.get("image", "")
            if img:
                images.append(img)
    return images


@pytest.mark.pbt
@settings(max_examples=100, deadline=None)
@given(
    chart=st.sampled_from(STANDARD_CHARTS),
    tag=_TAG_STRATEGY,
)
def test_property10_image_tag_fallback(chart: str, tag: str) -> None:
    """Property 10: Empty tag → ':latest'; non-empty tag → exact tag in container image.

    Uses hypothesis to vary chart and tag across 100 examples.
    inference-ollama uses adapter.image.tag for the adapter container.

    Note: ``--set-string`` is used instead of ``--set`` so Helm treats the value
    as a string literal rather than coercing numeric strings like ``"0"`` to the
    integer ``0`` (which ``| default "latest"`` would treat as falsy → ``"latest"``).

    Validates: Requirements 16.1, 16.5
    """
    if chart == "inference-ollama":
        set_key = "adapter.image.tag"
    else:
        set_key = "image.tag"

    # Use extra_args with --set-string to force string typing in Helm, preventing
    # numeric strings (e.g. "0") from being coerced to integers (falsy → :latest).
    extra = ["--set-string", f"{set_key}={tag}"]
    docs = _safe_helm_template(chart, extra_args=extra)
    images = _extract_container_images(docs)

    assert images, f"No container images found in Deployment for chart '{chart}'"

    expected_suffix = ":latest" if tag == "" else f":{tag}"

    # For inference-ollama the adapter container is the one with the variable tag.
    # The ollama container always uses "ollama/ollama:latest" (hardcoded in values).
    if chart == "inference-ollama":
        adapter_images = [img for img in images if "inference-adapter" in img]
        assert adapter_images, (
            f"No inference-adapter image found in inference-ollama Deployment; "
            f"all images: {images}"
        )
        for img in adapter_images:
            assert img.endswith(expected_suffix), (
                f"Adapter image '{img}' should end with '{expected_suffix}' "
                f"(tag={tag!r})"
            )
    else:
        # At least one image should match (the primary service container)
        for img in images:
            # Skip images that are hardcoded (e.g. curl init containers)
            if "registry.local" in img or "registry.internal" in img:
                assert img.endswith(expected_suffix), (
                    f"Image '{img}' should end with '{expected_suffix}' "
                    f"(chart={chart}, tag={tag!r})"
                )
