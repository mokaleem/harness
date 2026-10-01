import sys
from pathlib import Path

import pytest
from deerflow_extension_api import ActionContext, ExtensionPrincipal, RunPage, RunStatusView

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "extensions" / "enterprise"))

from deerflow_enterprise import install

from deerflow.extensions.registry import ExtensionRegistry


def test_install_declares_capture_evidence_and_ui(monkeypatch, tmp_path):
    monkeypatch.setenv("ENTERPRISE_TEST_DB", f"sqlite:///{tmp_path / 'db'}")
    registry = ExtensionRegistry()
    with registry.attributed_to("enterprise:install"):
        install(registry, {"database_url_env": "ENTERPRISE_TEST_DB", "team": "ran"})
    loaded = registry.build()
    plugin = loaded.plugins[0][1]
    assert plugin.namespace == "enterprise.capabilities"
    assert next(a for a in plugin.backend if a.name == "capture").requires_run_evidence
    assert plugin.frontend is not None
    assert loaded.services


@pytest.mark.asyncio
async def test_actions_enforce_authenticated_team_and_admin(monkeypatch, tmp_path):
    monkeypatch.setenv("ENTERPRISE_TEST_DB", f"sqlite:///{tmp_path / 'db'}")
    registry = ExtensionRegistry()
    with registry.attributed_to("enterprise:install"):
        install(registry, {"database_url_env": "ENTERPRISE_TEST_DB", "team": "ran"})
    plugin = registry.build().plugins[0][1]
    actions = {a.name: a.handler for a in plugin.backend}
    user = ActionContext(ExtensionPrincipal("alice", roles=("team:care",)), {})
    assert (await actions["capabilities"]({}, user))["items"] == []
    with pytest.raises(PermissionError):
        await actions["register"]({}, user)

    class Reader:
        async def list_changed_runs(self, **kwargs):
            return RunPage(items=(RunStatusView(thread_id="thread", run_id="run", status="success"),))

        async def get_run_status(self, **kwargs):
            return RunStatusView(**kwargs, status="success")

        async def list_run_events(self, **kwargs):
            from deerflow_extension_api import RunEventPage

            return RunEventPage()

    user = ActionContext(ExtensionPrincipal("alice", roles=("team:ran",)), {}, run_evidence_reader=Reader())
    result = await actions["capture"]({"thread_id": "thread"}, user)
    assert result["team"] == "ran"
    other = ActionContext(ExtensionPrincipal("bob", roles=("team:ran",)), {})
    with pytest.raises(ValueError, match="unavailable"):
        await actions["draft"]({"id": result["id"]}, other)
