import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "extensions" / "enterprise"))

from deerflow_enterprise.registry import Registry
from deerflow_enterprise.resilience import DependencyFailure, FailurePolicy, Resilience


@pytest.mark.asyncio
async def test_bounded_read_retries_and_partial_escalation(tmp_path):
    resilience = Resilience(Registry(f"sqlite:///{tmp_path / 'db'}"))
    calls = []

    async def failed():
        calls.append(1)
        raise ConnectionError("credentials must not appear in escalation")

    with pytest.raises(DependencyFailure) as caught:
        await resilience.call("ran.bq", failed, FailurePolicy(attempts=2, backoff_seconds=0, idempotent=True), partial={"sites": 3})
    assert len(calls) == 2
    assert caught.value.result["partial"] == {"sites": 3}
    assert caught.value.result["escalation_id"]
    assert "credentials" not in str(caught.value.result)


@pytest.mark.asyncio
async def test_writes_never_replay_and_circuit_shared(tmp_path):
    url = f"sqlite:///{tmp_path / 'db'}"
    first, second = Resilience(Registry(url)), Resilience(Registry(url))
    calls = []

    async def failed():
        calls.append(1)
        raise TimeoutError("ambiguous write")

    policy = FailurePolicy(attempts=3, failure_threshold=1, idempotent=False)
    with pytest.raises(DependencyFailure):
        await first.call("ran.bq", failed, policy)
    with pytest.raises(DependencyFailure) as caught:
        await second.call("ran.bq", failed, policy)
    assert len(calls) == 1
    assert caught.value.result["category"] == "circuit_open"


@pytest.mark.asyncio
async def test_cancelled_probe_is_released(tmp_path):
    now = [100.0]
    resilience = Resilience(Registry(f"sqlite:///{tmp_path / 'db'}"), clock=lambda: now[0])
    policy = FailurePolicy(failure_threshold=1, cooldown_seconds=1, backoff_seconds=0)

    async def failed():
        raise ConnectionError()

    with pytest.raises(DependencyFailure):
        await resilience.call("ran.bq", failed, policy)
    now[0] += 2

    async def cancel():
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await resilience.call("ran.bq", cancel, policy)
    assert await resilience.call("ran.bq", lambda: asyncio.sleep(0, result="ok"), policy) == "ok"


@pytest.mark.asyncio
async def test_permanent_error_not_retried(tmp_path):
    resilience = Resilience(Registry(f"sqlite:///{tmp_path / 'db'}"))
    calls = []

    async def failed():
        calls.append(1)
        raise PermissionError("no access")

    with pytest.raises(PermissionError):
        await resilience.call("ran.bq", failed, FailurePolicy(idempotent=True, attempts=3))
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_escalation_team_comes_from_deployment_not_id_prefix(tmp_path):
    from sqlalchemy import select

    registry = Registry(f"sqlite:///{tmp_path / 'db'}")
    resilience = Resilience(registry, team="ran")

    async def failed():
        raise ConnectionError()

    with pytest.raises(DependencyFailure):
        await resilience.call("vendor.bigquery", failed, FailurePolicy())
    with registry.engine.connect() as db:
        event = db.execute(select(registry.events)).mappings().one()
    assert event["team"] == "ran"
