import sys
from pathlib import Path

import pytest
from deerflow_extension_api import ExtensionPrincipal, RunEventPage, RunEventView, RunStatusView

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "extensions" / "enterprise"))

from deerflow_enterprise.registry import Registry
from deerflow_enterprise.workflow import SkillDraft, Workflow

OWNER = ExtensionPrincipal("alice", roles=("team:ran",))
REVIEWER = ExtensionPrincipal("reviewer", is_admin=True)


class Reader:
    async def get_run_status(self, **kwargs):
        return RunStatusView(**kwargs, status="success")

    async def list_run_events(self, **kwargs):
        return RunEventPage(items=(RunEventView(seq=1, event_type="tool", content={"sql": "SELECT site_id FROM synthetic.sites", "authorization": "secret"}),))


def candidate():
    return SkillDraft(
        name="site-health",
        description="Read network site health for a selected date.",
        instructions="Ask for the date, use the approved database tool with the query in query.sql. Return site health and missing evidence.",
        files={"query.sql": "SELECT site_id FROM synthetic.sites WHERE day = @day"},
        parameters={"type": "object", "properties": {"day": {"type": "string", "format": "date"}}, "required": ["day"], "additionalProperties": False},
        examples=[{"day": "2026-09-30"}],
        tables=[{"name": "synthetic.sites", "ddl": "CREATE TABLE sites (site_id STRING, day DATE)", "digest": "a" * 64}],
    )


@pytest.mark.asyncio
async def test_capture_validate_review_publish_immutable(tmp_path):
    registry = Registry(f"sqlite:///{tmp_path / 'registry.db'}")
    workflow = Workflow(registry)
    draft = await workflow.capture(Reader(), OWNER, "ran", "thread", "run")
    assert "secret" not in str(draft["evidence"])
    edited = workflow.parameterize(draft["id"], OWNER, candidate())
    report = workflow.validate(draft["id"], OWNER)
    assert report["valid"] is True
    with pytest.raises(PermissionError):
        workflow.review(draft["id"], OWNER, report["digest"], True, "Looks good")
    with pytest.raises(ValueError, match="digest"):
        workflow.review(draft["id"], REVIEWER, "wrong", True, "Looks good")
    workflow.review(draft["id"], REVIEWER, report["digest"], True, "Validated synthetic fixture and approved table scope")
    release = workflow.publish(draft["id"], OWNER, "ran.site-health", "1.0.0")
    assert release["kind"] == "skill"
    assert registry.list(OWNER) == []  # Publishing does not enable a capability.
    assert workflow.publish(draft["id"], OWNER, "ran.site-health", "1.0.0") == release
    with pytest.raises(ValueError, match="published"):
        workflow.parameterize(edited["id"], OWNER, candidate())


@pytest.mark.asyncio
async def test_edit_invalidates_review_and_expiration(tmp_path):
    registry = Registry(f"sqlite:///{tmp_path / 'registry.db'}")
    now = [100.0]
    workflow = Workflow(registry, ttl_seconds=60, clock=lambda: now[0])
    draft = await workflow.capture(Reader(), OWNER, "ran", "thread", "run")
    workflow.parameterize(draft["id"], OWNER, candidate())
    report = workflow.validate(draft["id"], OWNER)
    workflow.review(draft["id"], REVIEWER, report["digest"], True, "Approved")
    workflow.parameterize(draft["id"], OWNER, candidate())
    with pytest.raises(ValueError, match="review"):
        workflow.publish(draft["id"], OWNER, "ran.site-health", "1.0.0")
    now[0] = 161
    with pytest.raises(ValueError, match="expired"):
        workflow.get(draft["id"], OWNER)
    assert workflow.purge_expired() == 1


@pytest.mark.asyncio
async def test_bad_parameters_and_unsafe_files_block_release(tmp_path):
    workflow = Workflow(Registry(f"sqlite:///{tmp_path / 'registry.db'}"))
    draft = await workflow.capture(Reader(), OWNER, "ran", "thread", "run")
    bad = candidate().model_copy(update={"examples": [{"day": 123}]})
    workflow.parameterize(draft["id"], OWNER, bad)
    assert workflow.validate(draft["id"], OWNER)["valid"] is False
    with pytest.raises(ValueError):
        workflow.parameterize(draft["id"], OWNER, candidate().model_copy(update={"files": {"../secret.py": "print(1)"}}))


@pytest.mark.asyncio
async def test_inaccessible_or_running_evidence_is_not_promotable(tmp_path):
    reader = Reader()

    async def missing(**kwargs):
        return None

    reader.get_run_status = missing
    workflow = Workflow(Registry(f"sqlite:///{tmp_path / 'registry.db'}"))
    with pytest.raises(ValueError, match="unavailable"):
        await workflow.capture(reader, OWNER, "ran", "thread", "run")


@pytest.mark.asyncio
async def test_enterprise_semantic_validation_required_and_receipt_bound(tmp_path):
    workflow = Workflow(Registry(f"sqlite:///{tmp_path / 'registry.db'}"), require_semantic_validation=True)
    draft = await workflow.capture(Reader(), OWNER, "ran", "thread", "run")
    workflow.parameterize(draft["id"], OWNER, candidate())
    assert workflow.validate(draft["id"], OWNER)["valid"] is False
    checks = []

    def validator(recipe, evidence, principal):
        checks.append((recipe["name"], evidence["run_id"], principal.user_id))
        return {"valid": True, "checks": ["synthetic schema and expected behavior"], "receipt": {"schema_digest": "a" * 64}}

    workflow.validator = validator
    report = workflow.validate(draft["id"], OWNER)
    assert report["valid"] is True
    assert report["semantic_validation"]["receipt_digest"]
    assert checks == [("site-health", "run", "alice")]
