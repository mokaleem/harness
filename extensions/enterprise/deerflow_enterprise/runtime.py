"""One thin middleware around the existing lead/native-subagent tool loop."""

import asyncio
import json

from deerflow_extension_api import MiddlewarePlacement, Placement
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage

from .adapter import verify_projection
from .resilience import DependencyFailure, FailurePolicy, Resilience

# Runtime protocol helpers are core mechanics rather than operator integrations.
PROTOCOL_TOOLS = frozenset({"ask_clarification", "tool_search", "describe_skill", "present_files", "view_image", "task"})


class EnterpriseMiddleware(AgentMiddleware):
    def __init__(self, registry, bundle, *, agent_name=None):
        self.bundle = bundle
        manifest = verify_projection(bundle)
        from pathlib import Path

        self.items = json.loads((Path(bundle) / "capabilities.json").read_text(encoding="utf-8"))
        self.registered_tools = {i["name"]: i for i in self.items if i["kind"] == "tool"}
        self.servers = {i["name"]: i for i in self.items if i["kind"] == "mcp_server"}
        self.subagents = {i["name"] for i in self.items if i["kind"] == "subagent"}
        self.resilience = Resilience(registry, team=manifest["team"])
        self.agent_name = agent_name

    def release_policy_parameters(self):
        from .registry import digest

        return {"registry_revision": digest(self.items)}

    def _item(self, request):
        name = request.tool_call["name"]
        from deerflow.tools.mcp_metadata import get_mcp_source, is_mcp_tool

        source = get_mcp_source(request.tool) if request.tool is not None else None
        if source or (request.tool is not None and is_mcp_tool(request.tool)):
            item = self.servers.get(source["server_name"]) if source else None
            if item is None:
                raise PermissionError("MCP source is not in this deployed registry snapshot")
            return item
        return self.registered_tools.get(name)

    def _admit_run(self, runtime):
        from deerflow_extension_api import ExtensionPrincipal

        from deerflow.runtime.user_context import get_current_user, resolve_runtime_user_id

        from .registry import team_access

        user = get_current_user()
        user_id = str(user.id) if user is not None else resolve_runtime_user_id(runtime)
        # Runtime has no admin bypass, including admin-owned PAT runs.
        principal = self.resilience.registry.principal(ExtensionPrincipal(user_id))
        team = self.items[0]["team"] if self.items else verify_projection(self.bundle)["team"]
        team_access(team, principal)
        if self.agent_name and self.agent_name not in {i["name"] for i in self.items if i["kind"] == "agent"}:
            raise PermissionError("Agent is not in the deployed registry snapshot")

    def before_agent(self, state, runtime):
        verify_projection(self.bundle)
        self._admit_run(runtime)

    async def abefore_agent(self, state, runtime):
        await asyncio.to_thread(verify_projection, self.bundle)
        await asyncio.to_thread(self._admit_run, runtime)

    def _check(self, request):
        self._admit_run(getattr(request, "runtime", None))
        name = request.tool_call["name"]
        item = self._item(request)
        if item is None and name not in PROTOCOL_TOOLS:
            raise PermissionError("Capability is not in this deployed registry snapshot")
        if name == "task":
            kind = request.tool_call.get("args", {}).get("subagent_type", "general-purpose")
            if kind not in self.subagents:
                raise PermissionError("Subagent is not in this deployed registry snapshot")
        return item

    async def awrap_tool_call(self, request, handler):
        await asyncio.to_thread(verify_projection, self.bundle)
        item = await asyncio.to_thread(self._check, request)
        if not item or "failure_policy" not in item["definition"]:
            return await handler(request)
        policy = FailurePolicy.model_validate(item["definition"]["failure_policy"])

        async def operation():
            result = await handler(request)
            # Only explicitly categorized transient tool failures are replay candidates.
            if isinstance(result, ToolMessage) and result.status == "error" and result.additional_kwargs.get("deerflow_tool_meta", {}).get("error_type") == "transient":
                raise ConnectionError("Transient dependency failure")
            return result

        try:
            return await self.resilience.call(item["id"], operation, policy)
        except DependencyFailure as exc:
            return ToolMessage(
                content=json.dumps(exc.result),
                tool_call_id=request.tool_call["id"],
                name=request.tool_call["name"],
                status="error",
                additional_kwargs={"enterprise_failure": exc.result, "deerflow_tool_meta": {"status": "error", "error_type": "transient", "recoverable_by_model": False, "recommended_next_action": "try_alternative", "source": "exception"}},
            )

    def wrap_tool_call(self, request, handler):
        # Sync callables must supply their own transport/server deadline; no unsafe thread replay.
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            verify_projection(self.bundle)
            self._check(request)
            return handler(request)
        raise RuntimeError("Use the async tool path on an event-loop thread")


class RuntimeContributor:
    def __init__(self, registry, bundle):
        self.registry, self.bundle = registry, bundle
        self.template = EnterpriseMiddleware(registry, bundle)

    def contribute_middlewares(self, app_store, ctx):
        import copy

        middleware = copy.copy(self.template)
        middleware.agent_name = ctx.agent_name if ctx.scope.name == "LEAD" else None
        return (MiddlewarePlacement(middleware, Placement.TOOL_RAW, execution=True, max_handler_calls=4),)
