"""Large synthetic catalogs stay discoverable without unbounded model payloads."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

from deerflow.agents.middlewares.clarification_middleware import ClarificationMiddleware
from deerflow.agents.middlewares.deferred_tool_filter_middleware import DeferredToolFilterMiddleware
from deerflow.agents.thread_state import ThreadState, merge_promoted
from deerflow.config.app_config import AppConfig
from deerflow.config.skills_config import SkillsConfig
from deerflow.config.tool_config import ToolConfig
from deerflow.config.tool_search_config import ToolSearchConfig
from deerflow.skills.describe import build_skill_search_setup, get_skill_index_prompt_section
from deerflow.skills.types import Skill, SkillCategory
from deerflow.tools.builtins.clarification_tool import ask_clarification_tool
from deerflow.tools.builtins.tool_search import assemble_deferred_tools, get_deferred_tools_prompt_section
from deerflow.tools.mcp_metadata import tag_mcp_tool
from deerflow.tools.tools import get_available_tools


def _tools(count):
    def make(index):
        @tool(f"telecom_{index:03}")
        def query_site(site_id: str) -> str:
            """Read site KPIs."""
            return site_id

        return tag_mcp_tool(query_site)

    return [make(index) for index in range(count)]


def _skills(count):
    return [
        Skill(
            name=f"telecom-{index:03}",
            description=f"Site KPI workflow {index}",
            license=None,
            skill_dir=Path(f"/skills/{index}"),
            skill_file=Path(f"/skills/{index}/SKILL.md"),
            relative_path=Path(str(index)),
            category=SkillCategory.PUBLIC,
            enabled=True,
        )
        for index in range(count)
    ]


def test_setup_template_enables_bounded_discovery():
    example = yaml.safe_load((Path(__file__).parents[2] / "config.example.yaml").read_text(encoding="utf-8"))
    assert example["tool_search"]["enabled"] is True
    assert example["skills"]["deferred_discovery"] is True


@pytest.mark.parametrize("model", [SkillsConfig, ToolSearchConfig])
def test_discovery_limits_are_positive_and_configurable(model):
    config = model(max_prompt_names=7, max_search_results=2)
    assert config.max_prompt_names == 7
    assert config.max_search_results == 2
    with pytest.raises(ValueError):
        model(max_search_results=0)


def test_schema_window_reserves_room_for_search_and_automatic_routing():
    with pytest.raises(ValueError, match="max_active_tools"):
        ToolSearchConfig(max_active_tools=5, max_search_results=5, auto_promote_top_k=3)
    assert ToolSearchConfig(max_active_tools=2, max_search_results=1, auto_promote_top_k=1).max_active_tools == 2


def test_large_catalog_indexes_are_bounded_but_omitted_skills_are_searchable():
    skills = _skills(500)
    setup = build_skill_search_setup(skills, enabled=True, max_results=2)
    skill_prompt = get_skill_index_prompt_section(skill_names=setup.skill_names, max_names=7)
    tool_prompt = get_deferred_tools_prompt_section(deferred_names=frozenset(tool.name for tool in _tools(500)), max_names=7)
    assert "telecom-006" in skill_prompt and "telecom-007" not in skill_prompt
    assert "telecom_006" in tool_prompt and "telecom_007" not in tool_prompt
    assert "describe_skill" in skill_prompt and "tool_search" in tool_prompt
    result = setup.describe_skill_tool.invoke({"type": "tool_call", "id": "s1", "name": "describe_skill", "args": {"name": "select:telecom-499"}})
    assert "telecom-499" in result.update["messages"][0].content


def test_exact_selection_cannot_dump_hundreds_of_schemas_or_skill_descriptions():
    tools, setup = assemble_deferred_tools(_tools(500), enabled=True, max_results=3)
    query = "select:" + ",".join(tool.name for tool in tools if tool.name != "tool_search")
    result = setup.tool_search_tool.invoke({"type": "tool_call", "id": "t1", "name": "tool_search", "args": {"query": query}})
    assert len(result.update["promoted"]["names"]) == 3
    assert len(json.loads(result.update["messages"][0].content)) == 3
    skills = _skills(500)
    skill_setup = build_skill_search_setup(skills, enabled=True, max_results=3)
    result = skill_setup.describe_skill_tool.invoke({"type": "tool_call", "id": "s1", "name": "describe_skill", "args": {"name": "select:" + ",".join(skill.name for skill in skills)}})
    assert result.update["messages"][0].content.count("## Skill:") == 3


def test_recent_schema_window_can_rediscover_an_evicted_tool():
    tools = _tools(30)
    state = {"promoted": {"catalog_hash": "h1", "names": [tool.name for tool in tools]}}
    middleware = DeferredToolFilterMiddleware(frozenset(tool.name for tool in tools), "h1", max_active_tools=3)

    class Request:
        def __init__(self):
            self.tools, self.state = tools, state

        def override(self, **kwargs):
            return SimpleNamespace(**kwargs)

    assert [tool.name for tool in middleware._filter_tools(Request()).tools] == [tool.name for tool in tools[-3:]]
    blocked = SimpleNamespace(tool_call={"name": tools[0].name, "id": "t1"}, state=state)
    assert middleware._blocked_tool_message(blocked).status == "error"
    state["promoted"] = merge_promoted(state["promoted"], {"catalog_hash": "h1", "names": [tools[0].name]})
    assert tools[0].name in [tool.name for tool in middleware._filter_tools(Request()).tools]
    assert middleware._blocked_tool_message(blocked) is None


def test_configured_python_tool_can_defer_without_becoming_an_mcp_tool(monkeypatch):
    @tool
    def internal_kpi(site_id: str) -> str:
        """Read an internal site KPI."""
        return site_id

    config = AppConfig.model_validate({"sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider"}, "tools": [ToolConfig(name="internal_kpi", group="telecom", use="company.tools:internal_kpi", defer_loading=True)]})
    monkeypatch.setattr("deerflow.tools.tools.resolve_variable", lambda *args: internal_kpi)
    available = get_available_tools(groups=["telecom"], include_mcp=False, include_upload_tool=False, app_config=config)
    _, setup = assemble_deferred_tools(available, enabled=True, max_results=2)
    assert "internal_kpi" in setup.deferred_names
    loaded = next(item for item in available if item.name == "internal_kpi")
    assert loaded is not internal_kpi
    assert not internal_kpi.metadata
    assert not loaded.metadata.get("deerflow_mcp")
    assert loaded.invoke({"site_id": "DAL-001"}) == "DAL-001"


def test_compiled_graph_bounds_schema_binding_across_multiple_discoveries():
    candidates = _tools(30)
    tools, setup = assemble_deferred_tools(candidates, enabled=True, max_results=2)
    bound = []

    class Model(GenericFakeChatModel):
        def bind_tools(self, tools, **kwargs):
            bound.append([item.name for item in tools if item.name != "tool_search"])
            return self

    selections = ["telecom_000,telecom_001,telecom_002", "telecom_010,telecom_011", "telecom_000"]
    messages = [AIMessage(content="", tool_calls=[{"name": "tool_search", "id": str(index), "args": {"query": "select:" + selection}}]) for index, selection in enumerate(selections)]
    graph = create_agent(model=Model(messages=iter([*messages, AIMessage(content="done")])), tools=tools, middleware=[DeferredToolFilterMiddleware(setup.deferred_names, setup.catalog_hash, max_active_tools=2)], state_schema=ThreadState)
    asyncio.run(graph.ainvoke({"messages": [HumanMessage(content="Compare site KPI tools")]}))
    assert bound == [[], ["telecom_000", "telecom_001"], ["telecom_010", "telecom_011"], ["telecom_000", "telecom_011"]]


def test_telecom_clarification_stops_queries_and_continues_after_reply():
    calls = []

    @tool
    def query_kpis(metric: str, period: str) -> str:
        """Read the chosen KPI for the requested time window."""
        calls.append((metric, period))
        return "Dallas dropped-call rate: 0.5%"

    class Model(GenericFakeChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    question = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "ask_clarification",
                "id": "clarify",
                "args": {
                    "question": "Which metric and time window?",
                    "clarification_type": "ambiguous_requirement",
                    "fields": [
                        {"name": "metric", "label": "Metric", "type": "select", "required": True, "options": ["Dropped-call rate", "Throughput"]},
                        {"name": "period", "label": "Time window", "type": "text", "required": True},
                    ],
                },
            },
            {"name": "query_kpis", "id": "premature", "args": {"metric": "throughput", "period": "today"}},
        ],
    )
    query = AIMessage(content="", tool_calls=[{"name": "query_kpis", "id": "query", "args": {"metric": "dropped-call rate", "period": "last week"}}])
    graph = create_agent(
        model=Model(messages=iter([question, query, AIMessage(content="Dallas dropped-call rate was 0.5% last week.")])), tools=[ask_clarification_tool, query_kpis], middleware=[ClarificationMiddleware()], checkpointer=InMemorySaver()
    )
    config = {"configurable": {"thread_id": "telecom-clarification"}}

    async def run():
        first = await graph.ainvoke({"messages": [HumanMessage(content="Show Dallas site performance")]}, config)
        assert calls == []
        reply = next(message for message in first["messages"] if isinstance(message, ToolMessage))
        assert reply.artifact["human_input"]["input_mode"] == "form"
        return await graph.ainvoke({"messages": [HumanMessage(content="Dropped-call rate; last week")]}, config)

    result = asyncio.run(run())
    assert calls == [("dropped-call rate", "last week")]
    assert "0.5%" in result["messages"][-1].content
