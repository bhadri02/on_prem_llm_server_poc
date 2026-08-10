"""
Unit tests for intelligent_router.policy — Phase 2 (role, task_type)
permission matrix loading and enforcement.

Mirrors the loading-contract tests already written for
intelligent_router.model_selector.load_model_matrix.
"""

import os

import pytest

os.environ.setdefault("MODEL_MATRIX_PATH", "/tmp/model_matrix.yaml")
os.environ.setdefault("TASK_RULES_PATH", "/tmp/task_rules.yaml")
os.environ.setdefault("AUDIT_STORE_URL", "http://audit-store:9200")

from intelligent_router.policy import (  # noqa: E402
    PolicyMatrix,
    check_task_permission,
    load_policy_matrix,
)


class TestLoadPolicyMatrix:
    def test_missing_file_returns_none(self):
        assert load_policy_matrix("/tmp/does-not-exist-policy-matrix.yaml") is None

    def test_malformed_yaml_returns_none(self, tmp_path):
        p = tmp_path / "bad.yaml"
        p.write_text("roles: [this is not a mapping")
        assert load_policy_matrix(str(p)) is None

    def test_non_mapping_top_level_returns_none(self, tmp_path):
        p = tmp_path / "list.yaml"
        p.write_text("- just\n- a\n- list\n")
        assert load_policy_matrix(str(p)) is None

    def test_empty_roles_returns_none(self, tmp_path):
        p = tmp_path / "empty.yaml"
        p.write_text("roles: {}\n")
        assert load_policy_matrix(str(p)) is None

    def test_missing_roles_key_returns_none(self, tmp_path):
        p = tmp_path / "no_roles.yaml"
        p.write_text("something_else: true\n")
        assert load_policy_matrix(str(p)) is None

    def test_valid_file_loads(self, tmp_path):
        p = tmp_path / "policy.yaml"
        p.write_text("roles:\n  developer:\n    chat: true\n    code: true\n")
        matrix = load_policy_matrix(str(p))
        assert matrix is not None
        assert matrix.roles["developer"]["chat"] is True


class TestCheckTaskPermission:
    matrix = PolicyMatrix(
        roles={
            "viewer": {"chat": False},
            "analyst": {"chat": True, "code": False},
            "developer": {"chat": True, "code": True},
        }
    )

    def test_none_roles_denied(self):
        assert check_task_permission(None, "chat", self.matrix) is False

    def test_empty_roles_denied(self):
        assert check_task_permission([], "chat", self.matrix) is False

    def test_role_not_in_matrix_denied(self):
        assert check_task_permission(["unknown-role"], "chat", self.matrix) is False

    def test_task_explicitly_denied_for_role(self):
        assert check_task_permission(["analyst"], "code", self.matrix) is False

    def test_permitted_role_and_task_allowed(self):
        assert check_task_permission(["developer"], "code", self.matrix) is True

    def test_any_of_multiple_roles_passes(self):
        # analyst can't do code, but developer can -- any-of semantics,
        # matching security_layer.policy.check_policy's existing behaviour.
        assert check_task_permission(["analyst", "developer"], "code", self.matrix) is True

    def test_task_absent_from_role_denied(self):
        assert check_task_permission(["developer"], "translation", self.matrix) is False
