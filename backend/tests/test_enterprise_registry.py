"""Enterprise extension contract tests; no live services or customer records."""

import sys
from pathlib import Path

import pytest
from deerflow_extension_api import ExtensionPrincipal

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "extensions" / "enterprise"))

from deerflow_enterprise.registry import Capability, Registry


@pytest.fixture
def store(tmp_path):
    return Registry(f"sqlite:///{tmp_path / 'enterprise.db'}")


ADMIN = ExtensionPrincipal("operator", is_admin=True)
RAN = ExtensionPrincipal("alice", roles=("team:ran",))
CARE = ExtensionPrincipal("bob", roles=("team:care",))


def capability(**changes):
    return Capability.model_validate(
        {
            "id": "ran.site-health",
            "version": "1.0.0",
            "kind": "tool",
            "origin": "internal",
            "team": "ran",
            "name": "site_health",
            "description": "Read site health",
            "definition": {"use": "company.tools:site_health", "group": "ran"},
            **changes,
        }
    )


def test_immutable_revisions_activation_and_team_visibility(store):
    item = store.register(capability(), ADMIN)
    assert store.list(RAN) == []
    store.activate(item["id"], item["version"], ADMIN)
    assert store.list(RAN)[0]["digest"] == item["digest"]
    assert store.list(CARE) == []
    with pytest.raises(ValueError, match="immutable"):
        store.register(capability(description="changed"), ADMIN)
    assert store.register(capability(), ADMIN)["digest"] == item["digest"]
    with pytest.raises(PermissionError):
        store.activate(item["id"], item["version"], RAN)
    store.deactivate(item["id"], ADMIN)
    assert store.list(RAN) == []


@pytest.mark.parametrize("kind", ["skill", "tool", "mcp_server", "agent", "subagent"])
@pytest.mark.parametrize("origin", ["internal", "external"])
def test_all_kinds_and_origins(store, kind, origin):
    store.register(capability(kind=kind, origin=origin), ADMIN)
    store.activate("ran.site-health", "1.0.0", ADMIN)
    assert store.list(RAN)[0]["origin"] == origin


def test_dependency_pins_and_secret_literals_rejected(store):
    with pytest.raises(ValueError, match="secret"):
        store.register(capability(definition={"token": "real-token"}), ADMIN)
    store.register(capability(dependencies={"ran.missing": "1.0.0"}), ADMIN)
    with pytest.raises(ValueError, match="dependency"):
        store.activate("ran.site-health", "1.0.0", ADMIN)


def test_conflicting_runtime_names_rejected(store):
    first = capability()
    store.register(first, ADMIN)
    store.activate(first.id, first.version, ADMIN)
    store.register(capability(id="ran.other"), ADMIN)
    with pytest.raises(ValueError, match="name"):
        store.activate("ran.other", "1.0.0", ADMIN)


def test_memberships_bridge_gateway_system_roles_without_body_identity(store):
    user = ExtensionPrincipal("alice", roles=("user",))
    store.register(capability(), ADMIN)
    store.activate("ran.site-health", "1.0.0", ADMIN)
    assert store.list(user) == []
    with pytest.raises(PermissionError):
        store.set_membership("ran", "alice", True, user)
    store.set_membership("ran", "alice", True, ADMIN)
    assert store.list(store.principal(user))[0]["team"] == "ran"
    store.set_membership("ran", "alice", False, ADMIN)
    assert store.list(store.principal(user)) == []
