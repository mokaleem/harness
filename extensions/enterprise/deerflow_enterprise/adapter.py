"""Small version-specific projection boundary; no alternate execution engine."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path, PurePosixPath

import yaml

from .registry import Registry, canonical, digest, no_secrets


def _env_refs(value):
    if isinstance(value, str) and re.fullmatch(r"\$\{[A-Z_][A-Z0-9_]*\}", value):
        return "$" + value[2:-1]
    if isinstance(value, dict):
        return {k: _env_refs(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_env_refs(v) for v in value]
    return value


def verify_projection(root):
    root = Path(root).resolve()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    for relative, checksum in manifest["files"].items():
        pure = PurePosixPath(relative)
        if pure.is_absolute() or ".." in pure.parts:
            raise ValueError("Unsafe projection manifest")
        path = root / relative
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != checksum:
            raise ValueError("Registry projection changed; regenerate and redeploy")
    # These catalogs are managed inputs; mutable runtime state elsewhere in home
    # is intentionally outside the manifest. New instructions/templates also drift.
    for relative in ("skills/public", "home/agents"):
        directory = root / relative
        for path in directory.rglob("*"):
            if path.is_symlink() or (path.is_file() and path.relative_to(root).as_posix() not in manifest["files"]):
                raise ValueError("Registry projection changed; regenerate and redeploy")
    return manifest


def project(registry: Registry, team: str, base: dict, destination: Path, *, database_url_env: str):
    """Create a new immutable deployment bundle. Never overwrite an existing release.

    Registry activation selects desired state; deploying a projection/restarting
    applies it. Runtime mutations of managed inputs are detected by middleware.
    """
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,47}", team) or not re.fullmatch(r"[A-Z_][A-Z0-9_]*", database_url_env):
        raise ValueError("Invalid team or database environment name")
    no_secrets(base)
    items = registry.snapshot(team)
    # A stable digest includes definitions, pins and operator runtime base configuration.
    revision = digest({"protocol": 1, "team": team, "capabilities": items, "base": base, "database_url_env": database_url_env})
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / revision
    if target.exists():
        verify_projection(target)
        return target
    config = copy.deepcopy(base)
    # These lists are replaced, not merged with independent capability writers.
    config["tools"], config["tool_groups"] = [], []
    config["subagents"] = {**config.get("subagents", {}), "custom_agents": {}, "agents": {}}
    config["skills"] = {
        **config.get("skills", {}),
        "path": str(target / "skills"),
        "deferred_discovery": True,
        "use": "deerflow_enterprise.skill_storage:RegistrySkillStorage",
        "user_scoped_use": "deerflow_enterprise.skill_storage:RegistryUserSkillStorage",
    }
    config["tool_search"] = {**config.get("tool_search", {}), "enabled": True}
    config["agent_storage"] = {"backend": "file"}
    config["agents_api"] = {"enabled": False}
    operator_config = next((plugin.get("config", {}) for plugin in base.get("plugins", []) if plugin.get("use") == "deerflow_enterprise:install"), {})
    private = {key: operator_config[key] for key in ("validation_use", "require_semantic_validation", "draft_ttl_seconds") if key in operator_config}
    config["plugins"] = [plugin for plugin in config.get("plugins", []) if plugin.get("use") != "deerflow_enterprise:install"] + [
        {"name": "enterprise", "use": "deerflow_enterprise:install", "enabled": True, "required": True, "table_prefix": "enterprise_", "config": {**private, "database_url_env": database_url_env, "runtime_bundle": str(target), "team": team}}
    ]
    extensions = {"mcpServers": {}, "skills": {}}
    files = {}
    for item in items:
        name, kind = item["name"], item["kind"]
        definition = copy.deepcopy(item["definition"])
        definition.pop("failure_policy", None)
        definition.pop("provenance", None)
        if kind == "tool":
            from deerflow.config.tool_config import ToolConfig

            tool = ToolConfig.model_validate({**definition, "name": name})
            config["tools"].append(tool.model_dump())
        elif kind == "mcp_server":
            from deerflow.config.extensions_config import McpServerConfig

            mcp = McpServerConfig.model_validate({**definition, "enabled": True})
            extensions["mcpServers"][name] = _env_refs(mcp.model_dump(exclude_none=True))
        elif kind == "subagent":
            from deerflow.config.subagents_config import CustomSubagentConfig

            config["subagents"]["custom_agents"][name] = CustomSubagentConfig.model_validate(definition).model_dump()
        elif kind == "agent":
            from deerflow.config.agents_config import AGENT_NAME_PATTERN, AgentConfig

            if not AGENT_NAME_PATTERN.fullmatch(name):
                raise ValueError("Agent runtime name must contain letters, digits or hyphens")
            agent = AgentConfig.model_validate({**definition["config"], "name": name})
            files[f"home/agents/{name}/config.yaml"] = yaml.safe_dump(agent.model_dump(exclude_none=True))
            files[f"home/agents/{name}/SOUL.md"] = definition.get("soul", "")
        elif kind == "skill":
            from deerflow.skills.storage.skill_storage import SkillStorage

            SkillStorage.validate_skill_name(name)
            package = definition["files"]
            if "SKILL.md" not in package:
                raise ValueError("Skill package needs SKILL.md")
            SkillStorage.validate_skill_markdown_content(name, package["SKILL.md"])
            for relative, content in package.items():
                if not re.fullmatch(r"[A-Za-z0-9_./-]+", relative) or any(p in ("", ".", "..") for p in relative.split("/")) or relative.startswith("/"):
                    raise ValueError("Unsafe skill package path")
                files[f"skills/public/{name}/{relative}"] = content
    config["tool_groups"] = [{"name": name} for name in sorted({tool["group"] for tool in config["tools"]})]
    from deerflow.config.app_config import AppConfig

    AppConfig.model_validate(config)
    files["config.yaml"] = yaml.safe_dump(_env_refs(config), sort_keys=False, allow_unicode=True)
    files["extensions_config.json"] = canonical(extensions)
    files["capabilities.json"] = canonical(items)
    files["typesense-documents.json"] = canonical([{k: item[k] for k in ("id", "version", "kind", "origin", "team", "name", "description", "digest")} for item in items])
    manifest = {"schema_version": 1, "team": team, "revision": revision, "files": {name: hashlib.sha256(content.encode("utf-8")).hexdigest() for name, content in files.items()}}
    # Staging is retained on failures for diagnostics, never published partially.
    staging = Path(tempfile.mkdtemp(prefix=".building-", dir=destination))
    for relative, content in files.items():
        path = staging / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
    (staging / "manifest.json").write_text(canonical(manifest), encoding="utf-8", newline="\n")
    try:
        os.rename(staging, target)
    except FileExistsError:
        verify_projection(target)
    return target
