"""
intelligent_router/policy.py

Role x task_type permission matrix for the Intelligent Router (Layer 3).

The Security Layer's policy check (security_layer/policy.py) only answers
"can this identity call the platform at all" — it runs before task_type is
classified, since task classification (Stage 1) happens here in the Router,
after the Security Layer's pre-pipeline has already completed. This module
provides the finer-grained "(role, task_type) -> allow/deny" check described
in NEXT_FEATURES_PLAN.md Section 2.3, enforced in pipeline.py right after
Stage 2 (Model Selection).

Provides:
  - PolicyMatrix: dataclass holding the loaded role -> task_type -> allowed map.
  - load_policy_matrix(path): reads policy_matrix.yaml, validates it, and
    returns a PolicyMatrix on success, or None on any failure (logging a
    specific ERROR in each case) — same contract as
    model_selector.load_model_matrix.
  - check_task_permission(roles, task_type, matrix): True if ANY role in
    *roles* is permitted to perform *task_type* (same any-of semantics as
    security_layer.policy.check_policy). A role or task_type absent from the
    matrix is treated as denied for that pairing.
"""

import pathlib
from dataclasses import dataclass

import yaml

from intelligent_router.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class PolicyMatrix:
    """Holds the role -> task_type -> allowed map loaded from policy_matrix.yaml."""

    roles: dict[str, dict[str, bool]]


def load_policy_matrix(path: str) -> PolicyMatrix | None:
    """Load the policy matrix from the YAML file at *path*.

    Returns a :class:`PolicyMatrix` instance on success. Returns ``None`` —
    and logs a specific ERROR — on any of the following failure conditions:

    - ``FileNotFoundError``: the file does not exist at *path*.
    - ``yaml.YAMLError``: the file content is not valid YAML.
    - The top-level ``roles`` map is missing or empty.
    - Any other unexpected exception during reading or parsing.
    """
    try:
        raw = pathlib.Path(path).read_text(encoding="utf-8")
        data = yaml.safe_load(raw)

        if not isinstance(data, dict):
            logger.error(
                f"Policy matrix file is empty or not a YAML mapping: {path}; "
                "refusing to start"
            )
            return None

        roles = data.get("roles") or {}
        if not roles:
            logger.error(
                f"Policy matrix 'roles' map is empty: {path}; refusing to start"
            )
            return None

        return PolicyMatrix(roles=roles)

    except FileNotFoundError:
        logger.error(f"Policy matrix file not found: {path}")
    except yaml.YAMLError as exc:
        logger.error(f"Malformed policy matrix YAML: {exc}")
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Failed to read policy matrix file '{path}': {exc}")

    return None


def check_task_permission(
    roles: list[str] | None,
    task_type: str,
    matrix: PolicyMatrix,
) -> bool:
    """Return True if any role in *roles* is permitted to perform *task_type*.

    Args:
        roles:     The caller's resolved roles (from IMF user.roles), or None.
        task_type: The classified task type (e.g. 'chat', 'code').
        matrix:    The loaded :class:`PolicyMatrix`.

    A role not present in the matrix, or a task_type not present under that
    role, is treated as denied for that pairing — matching the "absence of a
    row = deny" convention used by the DB-backed role_permissions table.
    """
    if not roles:
        return False

    for role in roles:
        role_permissions = matrix.roles.get(role)
        if role_permissions and role_permissions.get(task_type):
            return True

    return False
