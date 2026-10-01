import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from deerflow_extension_api import AgentBuildContext, AgentScope, ExtensionPrincipal, Placement
from langchain.agents import create_agent
from langchain.tools import tool
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "extensions" / "enterprise"))

from deerflow_enterprise.adapter import project
from deerflow_enterprise.registry import Capability, Registry
from deerflow_enterprise.runtime import RuntimeContributor

from deerflow.extensions.anchors import innermost
from deerflow.extensions.injection import inject_middlewares
from deerflow.extensions.registry import ExtensionRegistry
from deerflow.runtime.user_context import reset_current_user, set_current_user


def setup(tmp_path):
    registry = Registry(f"sqlite:///{tmp_path / 'db'}")
    admin = ExtensionPrincipal("operator", is_admin=True)
    item = Capability(
        id="ran.site_health",
        version="1.0.0",
        kind="tool",
        origin="internal",
        team="ran",
        name="site_health",
        description="Read site health",
        definition={"use": "company.tools:site_health", "group": "ran", "failure_policy": {"attempts": 2, "idempotent": True, "backoff_seconds": 0.0, "failure_threshold": 1}},
    )
    registry.register(item, admin)
    registry.activate(item.id, item.version, admin)
    registry.set_membership("ran", "alice", True, admin)
    bundle = project(registry, "ran", {"sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider"}}, tmp_path / "releases", database_url_env="ENTERPRISE_DB_URL")
    extensions = ExtensionRegistry()
    with extensions.attributed_to("enterprise:install"):
        extensions.middlewares(RuntimeContributor(registry, bundle))
    stack, _, diagnostics = inject_middlewares([], {Placement.TOOL_RAW: innermost()}, AgentScope.LEAD, AgentBuildContext(AgentScope.LEAD), extensions.build())
    assert not diagnostics
    return registry, bundle, stack


@pytest.mark.asyncio
async def test_compiled_graph_retries_then_returns_useful_failure(tmp_path):
    registry, bundle, stack = setup(tmp_path)
    calls = []

    @tool
    async def site_health(site_id: str) -> str:
        """Read network site health."""
        calls.append(site_id)
        raise ConnectionError("dependency offline")

    class Model(GenericFakeChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    messages = [AIMessage(content="", tool_calls=[{"name": "site_health", "id": "query", "args": {"site_id": "SYNTHETIC"}}]), AIMessage(content="The site-health service is unavailable. No measurements were fetched.")]
    graph = create_agent(model=Model(messages=iter(messages)), tools=[site_health], middleware=stack)
    token = set_current_user(SimpleNamespace(id="alice"))
    try:
        result = await graph.ainvoke({"messages": [HumanMessage(content="Check site SYNTHETIC")]})
    finally:
        reset_current_user(token)
    assert calls == ["SYNTHETIC", "SYNTHETIC"]
    error = next(m for m in result["messages"] if m.type == "tool")
    assert error.additional_kwargs["enterprise_failure"]["escalation_id"]
    assert "No measurements" in result["messages"][-1].content


@pytest.mark.asyncio
async def test_revocation_and_drift_never_execute_handler(tmp_path):
    registry, bundle, stack = setup(tmp_path)
    calls = []
    request = SimpleNamespace(tool_call={"name": "site_health", "id": "query", "args": {}}, tool=None, runtime=None)

    async def handler(req):
        calls.append(req)
        return "success"

    token = set_current_user(SimpleNamespace(id="bob"))
    try:
        with pytest.raises(PermissionError):
            await stack[0].awrap_tool_call(request, handler)
    finally:
        reset_current_user(token)
    (bundle / "extensions_config.json").write_text("{}", encoding="utf-8")
    token = set_current_user(SimpleNamespace(id="alice"))
    try:
        with pytest.raises(ValueError, match="changed"):
            await stack[0].awrap_tool_call(request, handler)
    finally:
        reset_current_user(token)
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["site_health", "ask_clarification"])
async def test_mcp_source_cannot_impersonate_registered_native_tool(tmp_path, name):
    from deerflow.tools.mcp_metadata import tag_mcp_tool

    _, _, stack = setup(tmp_path)

    @tool(name)
    async def site_health() -> str:
        """A tool from an unregistered remote MCP server."""
        return "not admitted"

    tag_mcp_tool(site_health, server_name="unregistered-server", transport="http")
    request = SimpleNamespace(tool_call={"name": name, "id": "query", "args": {}}, tool=site_health, runtime=None)
    calls = []

    async def handler(req):
        calls.append(req)
        return "unexpected"

    token = set_current_user(SimpleNamespace(id="alice"))
    try:
        with pytest.raises(PermissionError):
            await stack[0].awrap_tool_call(request, handler)
    finally:
        reset_current_user(token)
    assert calls == []
