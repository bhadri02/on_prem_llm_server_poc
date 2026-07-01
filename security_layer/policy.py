"""
policy.py — Role-based policy check for the Security & Governance Layer.

Provides:

- ``ALLOWED_ROLES``: the frozenset of role strings that satisfy the policy
  check (``developer``, ``analyst``, ``admin``).
- ``check_policy``: per-request check that returns a pass/deny tuple based on
  whether the caller's roles list contains at least one allowed role.
"""

# The static set of roles that are permitted to call the platform.
# Comparison is case-sensitive: "Developer" is NOT the same as "developer".
ALLOWED_ROLES: frozenset[str] = frozenset({"developer", "analyst", "admin"})


def check_policy(roles: list[str] | None) -> tuple[bool, str]:
    """Check whether the caller holds at least one allowed role.

    Returns:
        ``(True, "role_check_pass")`` if any value in *roles* is a member of
        :data:`ALLOWED_ROLES`.  ``(False, "role_check_deny")`` in all other
        cases: *roles* is ``None``, *roles* is an empty list, or no element of
        *roles* matches an allowed role.

    The comparison is **case-sensitive**: ``"Developer"`` will not match the
    allowed role ``"developer"``.

    Args:
        roles: The list of role strings from the inbound IMF ``user.roles``
            field, or ``None`` if the field is absent.
    """
    if not roles:
        return (False, "role_check_deny")

    if any(role in ALLOWED_ROLES for role in roles):
        return (True, "role_check_pass")

    return (False, "role_check_deny")
