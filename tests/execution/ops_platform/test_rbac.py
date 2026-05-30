"""Tests for execution/ops_platform/rbac.py + enforcement.py"""

import pytest

from execution.ops_platform import audit_log, cache_bus, enforcement, rbac, workspaces
from execution.ops_platform.errors import OpsError
from execution.ops_platform.identity import IdentityContext, anonymous_identity


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(audit_log, "_AUDIT_DIR", tmp_path / "audit")
    monkeypatch.setattr(workspaces, "_WORKSPACES_DIR", tmp_path / "workspaces")
    monkeypatch.setattr(cache_bus, "_VERSION_DIR", tmp_path / "versions")
    cache_bus.reset_for_tests()
    yield


def _identity(roles, workspaces_=None, authenticated=True):
    return IdentityContext(
        user_id="u", display_name="u", auth_provider="HEADER_AUTH",
        authenticated=authenticated, roles=list(roles),
        workspace_ids=list(workspaces_ or []),
    )


def test_admin_role_has_every_permission():
    identity = _identity(["admin"])
    for p in rbac.PERMISSIONS:
        assert rbac.has_permission(identity, p)


def test_viewer_cannot_publish():
    identity = _identity(["viewer"])
    assert not rbac.has_permission(identity, "capability.publish")


def test_operator_can_execute_not_publish():
    identity = _identity(["operator"])
    assert rbac.has_permission(identity, "capability.execute")
    assert not rbac.has_permission(identity, "capability.publish")


def test_anonymous_passes_when_enforcement_off():
    identity = anonymous_identity()
    assert rbac.has_permission(identity, "capability.publish")


def test_anonymous_denied_when_enforcement_on(monkeypatch):
    monkeypatch.setenv("OPS_ENFORCE_RBAC", "true")
    identity = anonymous_identity()
    assert not rbac.has_permission(identity, "capability.publish")


def test_workspace_membership_required():
    identity = _identity(["operator"], workspaces_=["sales"])
    assert rbac.has_permission(identity, "capability.execute", workspace_id="sales")
    assert not rbac.has_permission(identity, "capability.execute", workspace_id="ops")


def test_enforce_raises_with_correlation_id():
    identity = _identity(["viewer"])
    with pytest.raises(OpsError) as exc:
        enforcement.enforce(identity, "capability.publish")
    assert exc.value.code == "ACCESS_DENIED"
    assert exc.value.correlation_id


def test_enforce_denial_audit_row_written():
    identity = _identity(["viewer"])
    try:
        enforcement.enforce(identity, "capability.publish")
    except OpsError:
        pass
    rows = audit_log.list_entries(action="enforcement.denied")
    assert rows


def test_enforce_allows_admin_regardless_of_workspace():
    identity = _identity(["admin"], workspaces_=[])
    enforcement.enforce(identity, "capability.execute", workspace_id="any")


def test_can_helper_returns_boolean():
    identity = _identity(["viewer"])
    assert enforcement.can(identity, "capability.read")
    assert not enforcement.can(identity, "capability.publish")


def test_workspace_visibility_block(monkeypatch):
    workspaces.create_workspace(workspace_id="sales", name="Sales",
                                  owner={"name": "a"}, capability_ids=["cap_a"])
    workspaces.create_workspace(workspace_id="ops", name="Ops",
                                  owner={"name": "a"}, capability_ids=["cap_b"])
    identity = _identity(["operator"], workspaces_=["ops"])
    # cap_a is owned by sales; identity is in ops — denied
    with pytest.raises(OpsError):
        enforcement.enforce(identity, "capability.execute",
                              workspace_id="ops", capability_id="cap_a")
