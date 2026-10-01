"""Authoritative, immutable definitions; runtime and search are projections."""

from __future__ import annotations

import hashlib
import json
import re
import time
from contextlib import contextmanager
from typing import Literal

from deerflow_extension_api import ExtensionPrincipal
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Column, Float, Integer, MetaData, String, Table, Text, create_engine, select


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def admin(principal: ExtensionPrincipal):
    if not principal.user_id or not principal.is_admin:
        raise PermissionError("Administrator required")


def team_access(team: str, principal: ExtensionPrincipal):
    if not principal.user_id or (not principal.is_admin and f"team:{team}" not in principal.roles):
        raise PermissionError("Team membership required")


def no_secrets(value):
    """Reject obvious credential literals; this is not a universal DLP detector."""
    if isinstance(value, dict):
        for key, item in value.items():
            if re.search(r"(?:password|secret|token|api[_-]?key|credential)$", key, re.I):
                if not isinstance(item, str) or not re.fullmatch(r"\$(?:\{[A-Z_][A-Z0-9_]*\}|[A-Z_][A-Z0-9_]*)", item):
                    raise ValueError("Use an environment reference for secret fields")
            no_secrets(item)
    elif isinstance(value, list):
        for item in value:
            no_secrets(item)
    elif isinstance(value, str):
        if re.search(r"-----BEGIN .*PRIVATE KEY|(?:https?|postgres(?:ql)?|oracle)://[^\s/]+:[^\s/]+@|^Bearer\s+(?!\$)\S+", value, re.I):
            raise ValueError("Embedded secret material is prohibited")


class Capability(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,95}$")
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9.-]+)?$", max_length=60)
    kind: Literal["skill", "agent", "subagent", "tool", "mcp_server"]
    origin: Literal["internal", "external"]
    team: str = Field(pattern=r"^[a-z][a-z0-9-]{0,47}$")
    name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,95}$")
    description: str = Field(min_length=1, max_length=2000)
    definition: dict = Field(default_factory=dict)
    dependencies: dict[str, str] = Field(default_factory=dict, max_length=100)


class Registry:
    """SQLite locally, PostgreSQL shared. No host migration or app imports."""

    def __init__(self, url: str):
        if not url.startswith(("sqlite:///", "postgresql+psycopg://")):
            raise ValueError("Use a SQLite file or synchronous PostgreSQL psycopg URL")
        self.engine = create_engine(url, pool_pre_ping=True)
        self.meta = MetaData()
        self.lock = Table("enterprise_lock_v1", self.meta, Column("id", Integer, primary_key=True))
        self.revisions = Table(
            "enterprise_revisions_v1",
            self.meta,
            Column("id", String(96), primary_key=True),
            Column("version", String(60), primary_key=True),
            Column("team", String(48), nullable=False),
            Column("kind", String(16), nullable=False),
            Column("name", String(96), nullable=False),
            Column("digest", String(64), nullable=False),
            Column("body", Text, nullable=False),
        )
        self.active = Table("enterprise_active_v1", self.meta, Column("id", String(96), primary_key=True), Column("version", String(60), nullable=False))
        self.drafts = Table(
            "enterprise_drafts_v1",
            self.meta,
            Column("id", String(64), primary_key=True),
            Column("owner", String(256), nullable=False),
            Column("team", String(48), nullable=False),
            Column("expires", Float, nullable=False),
            Column("body", Text, nullable=False),
        )
        self.circuits = Table(
            "enterprise_circuits_v1",
            self.meta,
            Column("key", String(200), primary_key=True),
            Column("failures", Integer, nullable=False),
            Column("opened_until", Float, nullable=False),
            Column("probe", String(64)),
            Column("probe_until", Float, nullable=False),
        )
        self.events = Table(
            "enterprise_audit_v1",
            self.meta,
            Column("id", String(64), primary_key=True),
            Column("at", Float, nullable=False),
            Column("actor", String(256), nullable=False),
            Column("team", String(48), nullable=False),
            Column("action", String(40), nullable=False),
            Column("subject", String(200), nullable=False),
        )
        self.members = Table("enterprise_members_v1", self.meta, Column("team", String(48), primary_key=True), Column("user_id", String(256), primary_key=True))
        self.meta.create_all(self.engine)
        # Concurrent bootstrap is idempotent on both supported databases.
        with self.engine.begin() as db:
            from sqlalchemy import text

            db.execute(text("INSERT INTO enterprise_lock_v1 (id) VALUES (1) ON CONFLICT (id) DO NOTHING"))

    @contextmanager
    def transaction(self):
        with self.engine.connect() as db:
            if self.engine.dialect.name == "sqlite":
                db.exec_driver_sql("BEGIN IMMEDIATE")
            else:
                db.begin()
                db.execute(select(self.lock).where(self.lock.c.id == 1).with_for_update()).one()
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def audit(self, db, actor, team, action, subject):
        import uuid

        db.execute(self.events.insert().values(id=uuid.uuid4().hex, at=time.time(), actor=actor, team=team, action=action, subject=subject))

    def _register(self, db, capability: Capability, actor: str):
        body = capability.model_dump()
        no_secrets(body)
        encoded = canonical(body)
        if len(encoded.encode("utf-8")) > 512_000:
            raise ValueError("Capability exceeds 512 KB")
        old = db.execute(select(self.revisions).where(self.revisions.c.id == capability.id, self.revisions.c.version == capability.version)).mappings().first()
        checksum = digest(body)
        if old:
            if old["digest"] != checksum:
                raise ValueError("Capability version is immutable")
        else:
            identity = db.execute(select(self.revisions).where(self.revisions.c.id == capability.id)).mappings().first()
            if identity and any(identity[key] != body[key] for key in ("team", "kind", "name")):
                raise ValueError("Capability identity fields cannot change across versions")
            db.execute(self.revisions.insert().values(id=capability.id, version=capability.version, team=capability.team, kind=capability.kind, name=capability.name, digest=checksum, body=encoded))
            self.audit(db, actor, capability.team, "register", f"{capability.id}@{capability.version}")
        return {**body, "digest": checksum}

    def register(self, capability: Capability, principal: ExtensionPrincipal):
        admin(principal)
        with self.transaction() as db:
            return self._register(db, capability, principal.user_id)

    def get(self, identifier: str, version: str, principal: ExtensionPrincipal):
        with self.engine.connect() as db:
            row = db.execute(select(self.revisions).where(self.revisions.c.id == identifier, self.revisions.c.version == version)).mappings().first()
        if row is None:
            raise ValueError("Capability unavailable")
        team_access(row["team"], principal)
        return {**json.loads(row["body"]), "digest": row["digest"]}

    def principal(self, principal: ExtensionPrincipal):
        from dataclasses import replace

        with self.engine.connect() as db:
            teams = db.execute(select(self.members.c.team).where(self.members.c.user_id == principal.user_id)).scalars().all()
        return replace(principal, roles=tuple(sorted(set(principal.roles) | {f"team:{team}" for team in teams})))

    def set_membership(self, team, user_id, enabled, principal):
        admin(principal)
        if not isinstance(team, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,47}", team) or not isinstance(user_id, str) or not user_id.strip() or len(user_id) > 256 or type(enabled) is not bool:
            raise ValueError("Invalid team membership")
        with self.transaction() as db:
            db.execute(self.members.delete().where(self.members.c.team == team, self.members.c.user_id == user_id))
            if enabled:
                db.execute(self.members.insert().values(team=team, user_id=user_id))
            self.audit(db, principal.user_id, team, "membership", user_id)

    def list(self, principal: ExtensionPrincipal, *, offset: int = 0, limit: int = 100, active_only: bool = True):
        if not principal.user_id:
            raise PermissionError("Authenticated account required")
        if not 0 <= offset or not 1 <= limit <= 200:
            raise ValueError("Invalid pagination")
        query = select(self.revisions)
        if active_only:
            query = query.join(self.active, (self.active.c.id == self.revisions.c.id) & (self.active.c.version == self.revisions.c.version))
        if not principal.is_admin:
            teams = [r[5:] for r in principal.roles if r.startswith("team:")]
            query = query.where(self.revisions.c.team.in_(teams))
        with self.engine.connect() as db:
            rows = db.execute(query.order_by(self.revisions.c.id, self.revisions.c.version).offset(offset).limit(limit)).mappings().all()
        return [{**json.loads(row["body"]), "digest": row["digest"]} for row in rows]

    def activate(self, identifier: str, version: str, principal: ExtensionPrincipal):
        admin(principal)
        item = self.get(identifier, version, principal)
        with self.transaction() as db:
            rows = db.execute(select(self.revisions).join(self.active, (self.active.c.id == self.revisions.c.id) & (self.active.c.version == self.revisions.c.version))).mappings().all()
            selected = {r["id"]: json.loads(r["body"]) for r in rows if r["id"] != identifier}
            selected[identifier] = item
            for candidate in selected.values():
                for dep, pin in candidate["dependencies"].items():
                    target = selected.get(dep)
                    if target is None or target["version"] != pin or target["team"] != candidate["team"]:
                        raise ValueError("Active dependency version/team does not match its pin")
            remaining = {key: len(value["dependencies"]) for key, value in selected.items()}
            dependents = {key: [] for key in selected}
            for key, value in selected.items():
                for dep in value["dependencies"]:
                    dependents[dep].append(key)
            ready = [key for key, count in remaining.items() if count == 0]
            resolved = 0
            while ready:
                key = ready.pop()
                resolved += 1
                for dependent in dependents[key]:
                    remaining[dependent] -= 1
                    if remaining[dependent] == 0:
                        ready.append(dependent)
            if resolved != len(selected):
                raise ValueError("Capability dependency cycle")
            if sum(c["kind"] == item["kind"] and c["name"] == item["name"] and c["team"] == item["team"] for c in selected.values()) > 1:
                raise ValueError("Conflicting runtime name in team")
            db.execute(self.active.delete().where(self.active.c.id == identifier))
            db.execute(self.active.insert().values(id=identifier, version=version))
            self.audit(db, principal.user_id, item["team"], "activate", f"{identifier}@{version}")
        return item

    def snapshot(self, team: str):
        # One SQL statement gives one consistent activation snapshot across all rows.
        query = select(self.revisions).join(self.active, (self.active.c.id == self.revisions.c.id) & (self.active.c.version == self.revisions.c.version)).where(self.revisions.c.team == team).order_by(self.revisions.c.id).limit(10001)
        with self.engine.connect() as db:
            rows = db.execute(query).mappings().all()
        if len(rows) > 10000:
            raise ValueError("Team projection exceeds 10,000 capabilities")
        return [{**json.loads(row["body"]), "digest": row["digest"]} for row in rows]

    def deactivate(self, identifier: str, principal: ExtensionPrincipal):
        admin(principal)
        with self.transaction() as db:
            rows = db.execute(select(self.revisions).join(self.active, (self.active.c.id == self.revisions.c.id) & (self.active.c.version == self.revisions.c.version))).mappings().all()
            for row in rows:
                if identifier in json.loads(row["body"])["dependencies"] and row["id"] != identifier:
                    raise ValueError("Active capability depends on this dependency")
            db.execute(self.active.delete().where(self.active.c.id == identifier))
            self.audit(db, principal.user_id, "system", "deactivate", identifier)
