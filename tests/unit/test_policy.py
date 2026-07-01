"""
Unit tests for security_layer.policy.check_policy.
"""

import pytest

from security_layer.policy import check_policy


# ---------------------------------------------------------------------------
# Deny cases
# ---------------------------------------------------------------------------

def test_none_roles_returns_deny():
    assert check_policy(None) == (False, "role_check_deny")


def test_empty_list_returns_deny():
    assert check_policy([]) == (False, "role_check_deny")


def test_unknown_role_returns_deny():
    assert check_policy(["unknown"]) == (False, "role_check_deny")


def test_multiple_unknown_roles_return_deny():
    assert check_policy(["guest", "viewer", "superuser"]) == (False, "role_check_deny")


# ---------------------------------------------------------------------------
# Pass cases — individual allowed roles
# ---------------------------------------------------------------------------

def test_developer_role_returns_pass():
    assert check_policy(["developer"]) == (True, "role_check_pass")


def test_admin_role_returns_pass():
    assert check_policy(["admin"]) == (True, "role_check_pass")


def test_analyst_role_returns_pass():
    assert check_policy(["analyst"]) == (True, "role_check_pass")


# ---------------------------------------------------------------------------
# Pass case — mixed list with one valid role
# ---------------------------------------------------------------------------

def test_mixed_roles_with_one_valid_returns_pass():
    """A list containing one allowed role alongside invalid roles should pass."""
    assert check_policy(["unknown", "developer", "nope"]) == (True, "role_check_pass")


# ---------------------------------------------------------------------------
# Case-sensitivity
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("roles", [
    ["Developer"],
    ["ADMIN"],
    ["Analyst"],
])
def test_case_sensitive_mismatch_returns_deny(roles):
    """Role comparison is case-sensitive; mixed/upper-case variants must be denied."""
    assert check_policy(roles) == (False, "role_check_deny")
