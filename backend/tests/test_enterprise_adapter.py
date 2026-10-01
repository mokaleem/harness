import sys
from pathlib import Path

import pytest
from deerflow_extension_api import ExtensionPrincipal

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "extensions" / "enterprise"))

from deerflow_enterprise.adapter import project, verify_projection
from deerflow_enterprise.registry import Capability, Registry

BASE = {"config_version": 51, "models": [], "sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider"}}


def test_projection_all_runtime_types_and_tamper_detection(tmp_path):
    registry = Registry(f"sqlite:///{tmp_path / 'registry.db'}")
    principal = ExtensionPrincipal("operator", is_admin=True, roles=("team:ran",))
    definitions = {
        "tool": {"use": "deerflow.sandbox.tools:read_file_tool", "group": "files"},
        "mcp_server": {"transport": "http", "url": "https://approved.example/mcp", "headers": {"Authorization": "${BQ_TOKEN}"}},
        "subagent": {"description": "Read site health", "system_prompt": "Use approved inputs", "tools": ["read_file"]},
        "agent": {"config": {"name": "ran-assistant", "skills": ["site-health"]}, "soul": "Telecom analyst"},
        "skill": {"files": {"SKILL.md": "---\nname: site-health\ndescription: Read network site health.\n---\nUse approved tables."}},
    }
    names = {"tool": "read_file", "mcp_server": "bq", "subagent": "site-agent", "agent": "ran-assistant", "skill": "site-health"}
    for kind, definition in definitions.items():
        item = Capability(id=f"ran.{kind}", version="1.0.0", kind=kind, origin="external", team="ran", name=names[kind], description="Telecom capability", definition=definition)
        registry.register(item, principal)
        registry.activate(item.id, item.version, principal)
    bundle = project(registry, "ran", BASE, tmp_path / "releases", database_url_env="ENTERPRISE_DB_URL")
    assert verify_projection(bundle)["team"] == "ran"
    import yaml

    config = yaml.safe_load((bundle / "config.yaml").read_text(encoding="utf-8"))
    assert config["tools"][0]["name"] == "read_file"
    assert config["plugins"][-1]["table_prefix"] == "enterprise_"
    assert "site-agent" in config["subagents"]["custom_agents"]
    assert (bundle / "home/agents/ran-assistant/config.yaml").exists()
    assert (bundle / "skills/public/site-health/SKILL.md").exists()
    assert project(registry, "ran", BASE, tmp_path / "releases", database_url_env="ENTERPRISE_DB_URL") == bundle
    (bundle / "skills/public/site-health/SKILL.md").write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        verify_projection(bundle)


def test_projection_does_not_merge_unregistered_tools(tmp_path):
    registry = Registry(f"sqlite:///{tmp_path / 'registry.db'}")
    bundle = project(registry, "ran", {**BASE, "tools": [{"name": "unregistered"}]}, tmp_path / "releases", database_url_env="ENTERPRISE_DB_URL")
    import yaml

    config = yaml.safe_load((bundle / "config.yaml").read_text(encoding="utf-8"))
    assert config["tools"] == []


def test_projection_rejects_unregistered_skill_files(tmp_path):
    registry = Registry(f"sqlite:///{tmp_path / 'registry.db'}")
    bundle = project(registry, "ran", BASE, tmp_path / "releases", database_url_env="ENTERPRISE_DB_URL")
    rogue = bundle / "skills/public/unregistered/SKILL.md"
    rogue.parent.mkdir(parents=True)
    rogue.write_text("Unregistered instructions", encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        verify_projection(bundle)


def test_registry_skill_storage_excludes_user_and_legacy_writers(tmp_path, monkeypatch):
    from deerflow.config.app_config import AppConfig
    from deerflow.skills.storage import get_or_new_user_skill_storage, reset_skill_storage

    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path / "home"))
    public = tmp_path / "skills/public/approved"
    custom = tmp_path / "skills/custom/unapproved"
    for path in (public, custom):
        path.mkdir(parents=True)
        (path / "SKILL.md").write_text(f"---\nname: {path.name}\ndescription: Read selected network information.\n---\nUse approved inputs.", encoding="utf-8")
    config = AppConfig.model_validate({**BASE, "skills": {"path": str(tmp_path / "skills"), "user_scoped_use": "deerflow_enterprise.skill_storage:RegistryUserSkillStorage"}})
    reset_skill_storage()
    storage = get_or_new_user_skill_storage("alice", app_config=config)
    assert [s.name for s in storage.load_skills()] == ["approved"]
    with pytest.raises(PermissionError):
        storage.write_custom_skill("approved", "SKILL.md", "tampered")
    reset_skill_storage()
