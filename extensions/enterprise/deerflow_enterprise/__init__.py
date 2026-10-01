"""Installable enterprise services; the host owns authentication and execution."""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

from deerflow_extension_api import BackendAction, BrowserAssets, PluginContribution, extension

from .registry import Capability, Registry, admin, team_access
from .workflow import SkillDraft, Workflow


class EnterpriseService:
    def __init__(self, registry, workflow, team):
        self.registry, self.workflow, self.team = registry, workflow, team
        self.task = None

    async def start(self, deps):
        async def purge():
            while True:
                work = asyncio.create_task(asyncio.to_thread(self.workflow.purge_expired))
                try:
                    await asyncio.shield(work)
                except asyncio.CancelledError:
                    await work  # Drain the in-flight DB mutation before service teardown.
                    raise
                await asyncio.sleep(60)

        self.task = asyncio.create_task(purge(), name="enterprise-draft-expiration")

    async def stop(self):
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None
        await asyncio.to_thread(self.registry.engine.dispose)

    def handler(self, action):
        fields = {
            "status": (set(), set()),
            "capabilities": (set(), {"offset", "limit", "all_versions"}),
            "register": ({"capability"}, set()),
            "activate": ({"id", "version"}, set()),
            "deactivate": ({"id"}, set()),
            "capture": ({"thread_id"}, {"run_id"}),
            "drafts": (set(), {"offset", "limit"}),
            "draft": ({"id"}, set()),
            "parameterize": ({"id", "candidate"}, set()),
            "validate": ({"id"}, set()),
            "review": ({"id", "digest", "approve", "notes"}, set()),
            "publish": ({"id", "capability_id", "version"}, set()),
            "failures": (set(), {"offset", "limit"}),
            "membership": ({"user_id", "enabled"}, set()),
        }

        async def handle(payload, context):
            principal = await asyncio.to_thread(self.registry.principal, context.principal)
            if action in {"register", "activate", "deactivate", "review", "membership"}:
                admin(principal)
            required, optional = fields[action]
            if not required <= set(payload) or set(payload) - required - optional:
                raise ValueError("Invalid enterprise action fields")
            if action == "status":
                return {"team": self.team, "is_admin": principal.is_admin, "user_id": principal.user_id}
            if action == "membership":
                await asyncio.to_thread(self.registry.set_membership, self.team, payload["user_id"], payload["enabled"], principal)
                return {"user_id": payload["user_id"], "enabled": payload["enabled"]}
            if action == "capabilities":
                all_versions = payload.get("all_versions", False)
                if type(all_versions) is not bool:
                    raise ValueError("all_versions must be boolean")
                items = await asyncio.to_thread(self.registry.list, principal, offset=payload.get("offset", 0), limit=payload.get("limit", 100), active_only=not all_versions)
                return {"items": items}
            if action == "register":
                return await asyncio.to_thread(self.registry.register, Capability.model_validate(payload["capability"]), principal)
            if action == "activate":
                return await asyncio.to_thread(self.registry.activate, payload["id"], payload["version"], principal)
            if action == "deactivate":
                await asyncio.to_thread(self.registry.deactivate, payload["id"], principal)
                return {"id": payload["id"]}
            if action == "capture":
                team_access(self.team, principal)
                reader = context.run_evidence_reader
                if reader is None:
                    raise ValueError("Request-scoped run evidence unavailable")
                thread, run = payload["thread_id"], payload.get("run_id")
                if not isinstance(thread, str) or not 1 <= len(thread) <= 128:
                    raise ValueError("Invalid thread")
                if run is None:
                    cursor, matches = None, []
                    for _ in range(5):
                        page = await reader.list_changed_runs(cursor=cursor, limit=2000)
                        matches.extend(item for item in page.items if item.thread_id == thread)
                        if not page.has_more:
                            break
                        if page.next_cursor is None or page.next_cursor == cursor:
                            raise ValueError("Run cursor did not advance")
                        cursor = page.next_cursor
                    else:
                        raise ValueError("Run history exceeds capture search limits; supply a selected run")
                    for item in reversed(matches):
                        if await reader.get_run_status(thread_id=thread, run_id=item.run_id) is not None:
                            run = item.run_id
                            break
                if not isinstance(run, str) or not run or len(run) > 128:
                    raise ValueError("Run evidence unavailable")
                return await self.workflow.capture(reader, principal, self.team, thread, run)
            if action == "drafts":
                return {"items": await asyncio.to_thread(self.workflow.list, principal, offset=payload.get("offset", 0), limit=payload.get("limit", 50))}
            if action == "draft":
                return await asyncio.to_thread(self.workflow.get, payload["id"], principal)
            if action == "parameterize":
                return await asyncio.to_thread(self.workflow.parameterize, payload["id"], principal, SkillDraft.model_validate(payload["candidate"]))
            if action == "validate":
                return await asyncio.to_thread(self.workflow.validate, payload["id"], principal)
            if action == "review":
                return await asyncio.to_thread(self.workflow.review, payload["id"], principal, payload["digest"], payload["approve"], payload["notes"])
            if action == "publish":
                return await asyncio.to_thread(self.workflow.publish, payload["id"], principal, payload["capability_id"], payload["version"])
            if action == "failures":
                from sqlalchemy import select

                offset, limit = payload.get("offset", 0), payload.get("limit", 50)
                if type(offset) is not int or type(limit) is not int or not offset >= 0 or not 1 <= limit <= 100:
                    raise ValueError("Invalid pagination")

                def fetch():
                    table = self.registry.events
                    query = select(table).where(table.c.action == "dependency_failure")
                    if not principal.is_admin:
                        teams = [r[5:] for r in principal.roles if r.startswith("team:")]
                        query = query.where(table.c.team.in_(teams))
                    with self.registry.engine.connect() as db:
                        return {"items": [dict(row) for row in db.execute(query.order_by(table.c.at.desc()).offset(offset).limit(limit)).mappings().all()]}

                return await asyncio.to_thread(fetch)
            raise ValueError("Unknown enterprise action")

        return handle


@extension(api="0.2.5", name="enterprise")
def install(registry, config):
    env, team = config.get("database_url_env"), config.get("team")
    if not isinstance(env, str) or not re.fullmatch(r"[A-Z_][A-Z0-9_]*", env) or not os.environ.get(env):
        raise ValueError("Provide an operator-owned database URL environment variable")
    if not isinstance(team, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,47}", team):
        raise ValueError("Provide a deployment team")
    store = Registry(os.environ[env])
    validator = None
    if validation_use := config.get("validation_use"):
        from deerflow.reflection import resolve_variable

        validator = resolve_variable(validation_use)
        import inspect

        if not callable(validator) or inspect.iscoroutinefunction(validator):
            raise ValueError("validation_use must resolve to a synchronous bounded validation callable")
    workflow = Workflow(store, ttl_seconds=config.get("draft_ttl_seconds", 86400), validator=validator, require_semantic_validation=config.get("require_semantic_validation", True))
    service = EnterpriseService(store, workflow, team)
    actions = ("status", "capabilities", "register", "activate", "deactivate", "capture", "drafts", "draft", "parameterize", "validate", "review", "publish", "failures", "membership")
    if not registry.plugin(
        PluginContribution(
            namespace="enterprise.capabilities",
            title="Enterprise capabilities",
            description="Versioned capabilities and reviewed skill releases",
            enabled=True,
            frontend=BrowserAssets("enterprise.v1", Path(__file__).parent),
            backend=tuple(BackendAction(action, service.handler(action), requires_run_evidence=action == "capture") for action in actions),
        )
    ):
        raise RuntimeError("Enterprise workflow requires the full-stack plugin host")
    if bundle := config.get("runtime_bundle"):
        from .adapter import verify_projection
        from .runtime import RuntimeContributor

        if verify_projection(bundle)["team"] != team:
            raise ValueError("Registry runtime projection team mismatch")
        registry.middlewares(RuntimeContributor(store, bundle))
    registry.service(service)
