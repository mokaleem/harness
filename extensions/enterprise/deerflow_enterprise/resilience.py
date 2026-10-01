"""Bounded dependency retries and database-shared, lease-fenced circuits."""

from __future__ import annotations

import asyncio
import time
import uuid

import httpx
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from .registry import Registry


class FailurePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    attempts: int = Field(default=1, ge=1, le=4)
    idempotent: bool = False
    timeout_seconds: float = Field(default=20.0, gt=0, le=120, allow_inf_nan=False)
    total_seconds: float = Field(default=30.0, gt=0, le=300, allow_inf_nan=False)
    backoff_seconds: float = Field(default=0.25, ge=0, le=10, allow_inf_nan=False)
    failure_threshold: int = Field(default=3, ge=1, le=20)
    cooldown_seconds: float = Field(default=30.0, gt=0, le=3600, allow_inf_nan=False)


class DependencyFailure(RuntimeError):
    def __init__(self, result):
        self.result = result
        super().__init__("Dependency unavailable; retain supported partial results and request operator assistance")


class Resilience:
    def __init__(self, registry: Registry, *, clock=time.time, team=None):
        self.registry, self.clock, self.team = registry, clock, team

    def _admit(self, key, policy):
        if not key or len(key) > 200:
            raise ValueError("Invalid dependency key")
        table = self.registry.circuits
        with self.registry.transaction() as db:
            row = db.execute(select(table).where(table.c.key == key)).mappings().first()
            if row is None:
                db.execute(table.insert().values(key=key, failures=0, opened_until=0, probe=None, probe_until=0))
                return "closed"
            if row["opened_until"] > self.clock() or row["probe_until"] > self.clock():
                return None
            if row["opened_until"] or row["failures"] >= policy.failure_threshold:
                token = uuid.uuid4().hex
                db.execute(table.update().where(table.c.key == key).values(probe=token, probe_until=self.clock() + policy.total_seconds + 5))
                return token
            return "closed"

    def _settle(self, key, token, policy, *, failed=False, cancelled=False):
        table = self.registry.circuits
        with self.registry.transaction() as db:
            row = db.execute(select(table).where(table.c.key == key)).mappings().one()
            # Old calls cannot settle a newer open/probe generation.
            if token != "closed" and row["probe"] != token:
                return
            if token == "closed" and (row["opened_until"] or row["probe"]):
                return
            if cancelled:
                db.execute(table.update().where(table.c.key == key).values(probe=None, probe_until=0))
                return
            failures = row["failures"] + 1 if failed else 0
            db.execute(table.update().where(table.c.key == key).values(failures=failures, probe=None, probe_until=0, opened_until=self.clock() + policy.cooldown_seconds if failures >= policy.failure_threshold else 0))

    def _escalate(self, key, category, attempts, partial):
        identifier = uuid.uuid4().hex
        team = self.team or key.split(".", 1)[0][:48]
        with self.registry.transaction() as db:
            self.registry.audit(db, "runtime", team, "dependency_failure", f"{identifier}:{key}:{category}")
        return DependencyFailure(
            {
                "status": "partial" if partial is not None else "unavailable",
                "dependency": key,
                "category": category,
                "attempts": attempts,
                "partial": partial,
                "escalation_id": identifier,
                "next_action": "Retain verified results; explain missing evidence; ask an operator to inspect dependency health. Do not invent unavailable data.",
            }
        )

    async def call(self, key, operation, policy: FailurePolicy, *, partial=None):
        token = await asyncio.to_thread(self._admit, key, policy)
        if token is None:
            raise await asyncio.to_thread(self._escalate, key, "circuit_open", 0, partial)
        attempts, failed = 0, False
        try:
            async with asyncio.timeout(policy.total_seconds):
                for number in range(policy.attempts if policy.idempotent else 1):
                    attempts += 1
                    try:
                        async with asyncio.timeout(policy.timeout_seconds):
                            value = await operation()
                    except (ConnectionError, TimeoutError, OSError, httpx.TransportError, httpx.HTTPStatusError) as exc:
                        # Permission/not-found errors are permanent even though they subclass OSError.
                        if isinstance(exc, (PermissionError, FileNotFoundError)):
                            raise
                        if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code not in {429, 502, 503, 504}:
                            raise
                        if number + 1 >= (policy.attempts if policy.idempotent else 1):
                            failed = True
                            break
                        await asyncio.sleep(min(policy.backoff_seconds * (2**number), 10))
                    else:
                        await asyncio.to_thread(self._settle, key, token, policy)
                        return value
        except asyncio.CancelledError:
            await asyncio.shield(asyncio.to_thread(self._settle, key, token, policy, cancelled=True))
            raise
        except TimeoutError:
            failed = True
        except BaseException:
            await asyncio.shield(asyncio.to_thread(self._settle, key, token, policy, cancelled=True))
            raise
        if failed:
            await asyncio.to_thread(self._settle, key, token, policy, failed=True)
            raise await asyncio.to_thread(self._escalate, key, "dependency_failure", attempts, partial)
        raise RuntimeError("Dependency operation produced no result")
