"""Evidence -> parameterized draft -> validation -> digest-bound review -> release."""

from __future__ import annotations

import json
import re
import tempfile
import time
import uuid
from pathlib import Path, PurePosixPath

import jsonschema
import yaml
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from .registry import Capability, Registry, admin, canonical, digest, no_secrets, team_access


class SkillDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$", max_length=64)
    description: str = Field(min_length=1, max_length=1024)
    instructions: str = Field(min_length=1, max_length=24000)
    files: dict[str, str] = Field(default_factory=dict, max_length=50)
    parameters: dict
    examples: list[dict] = Field(min_length=1, max_length=30)
    tables: list[dict] = Field(default_factory=list, max_length=100)
    dependencies: dict[str, str] = Field(default_factory=dict, max_length=100)


def safe_files(files):
    for name, content in files.items():
        path = PurePosixPath(name)
        if not re.fullmatch(r"[A-Za-z0-9_./-]+", name) or path.is_absolute() or any(p in (".", "..") or p.startswith(".") for p in name.split("/")):
            raise ValueError("Unsafe package path")
        if path.name == "SKILL.md" or path.suffix not in {".md", ".sql", ".json", ".txt", ".yaml"}:
            raise ValueError("Only reviewed text/query resources are supported in promotion v1")
        if len(content.encode("utf-8")) > 64000:
            raise ValueError("File exceeds 64 KB")
    if len(canonical(files).encode("utf-8")) > 200000:
        raise ValueError("Package exceeds 200 KB")


def package(candidate: dict):
    files = dict(candidate["files"])
    frontmatter = yaml.safe_dump({"name": candidate["name"], "description": candidate["description"]}, sort_keys=True, allow_unicode=True).strip()
    files["SKILL.md"] = f"---\n{frontmatter}\n---\n\n{candidate['instructions']}\n\nValidate inputs against parameters.json. Use approved tools and current table permissions.\n"
    files["parameters.json"] = canonical(candidate["parameters"])
    files["examples.json"] = canonical(candidate["examples"])
    files["tables.json"] = canonical(candidate["tables"])
    return files


class Workflow:
    def __init__(self, registry: Registry, *, ttl_seconds=86400, clock=time.time, validator=None, require_semantic_validation=False):
        if not 60 <= ttl_seconds <= 604800:
            raise ValueError("Draft TTL must be between one minute and seven days")
        self.registry, self.ttl, self.clock = registry, ttl_seconds, clock
        if type(require_semantic_validation) is not bool:
            raise ValueError("require_semantic_validation must be boolean")
        self.validator, self.require_semantic_validation = validator, require_semantic_validation

    def _load(self, db, identifier, principal, *, review=False):
        table = self.registry.drafts
        row = db.execute(select(table).where(table.c.id == identifier)).mappings().first()
        if not row or (row["owner"] != principal.user_id and not principal.is_admin):
            raise ValueError("Draft unavailable")
        team_access(row["team"], principal)
        if review:
            admin(principal)
            if row["owner"] == principal.user_id:
                raise PermissionError("Independent reviewer required")
        body = json.loads(row["body"])
        if self.clock() >= row["expires"] and body["state"] != "published":
            raise ValueError("Draft expired")
        return body

    def _save(self, db, body):
        db.execute(self.registry.drafts.update().where(self.registry.drafts.c.id == body["id"]).values(body=canonical(body)))

    async def capture(self, reader, principal, team, thread_id, run_id):
        team_access(team, principal)
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,47}", team):
            raise ValueError("Invalid team")
        status = await reader.get_run_status(thread_id=thread_id, run_id=run_id)
        if status is None:
            raise ValueError("Run evidence unavailable")
        if status.status not in {"success", "completed", "error", "failed", "interrupted", "cancelled"}:
            raise ValueError("Capture requires a terminal run")
        events, after, incomplete = [], None, False
        # Capture only receipts and payload hashes, not prompts, customer rows or credentials.
        for _ in range(10):
            page = await reader.list_run_events(thread_id=thread_id, run_id=run_id, after_seq=after, limit=100)
            for event in page.items:
                events.append({"seq": event.seq, "type": event.event_type, "category": event.category, "content_digest": digest(event.content)})
            if not page.has_more:
                break
            if page.next_after_seq is None or page.next_after_seq == after:
                raise ValueError("Evidence cursor did not advance")
            after = page.next_after_seq
        else:
            incomplete = True
        body = {
            "id": uuid.uuid4().hex,
            "owner": principal.user_id,
            "team": team,
            "state": "captured",
            "expires": self.clock() + self.ttl,
            "evidence": {"thread_id": thread_id, "run_id": run_id, "status": status.status, "events": events, "incomplete": incomplete},
            "candidate": None,
            "validation": None,
            "review": None,
            "release": None,
        }
        import asyncio

        def save():
            with self.registry.transaction() as db:
                db.execute(self.registry.drafts.insert().values(id=body["id"], owner=principal.user_id, team=team, expires=body["expires"], body=canonical(body)))
                self.registry.audit(db, principal.user_id, team, "capture", body["id"])

        await asyncio.to_thread(save)
        return body

    def get(self, identifier, principal):
        with self.registry.engine.connect() as db:
            return self._load(db, identifier, principal)

    def list(self, principal, *, offset=0, limit=50):
        if not 0 <= offset or not 1 <= limit <= 100:
            raise ValueError("Invalid pagination")
        table = self.registry.drafts
        query = select(table).where(table.c.expires > self.clock())
        if not principal.is_admin:
            query = query.where(table.c.owner == principal.user_id)
        with self.registry.engine.connect() as db:
            rows = db.execute(query.order_by(table.c.id).offset(offset).limit(limit)).mappings().all()
        return [json.loads(row["body"]) for row in rows]

    def parameterize(self, identifier, principal, candidate: SkillDraft):
        candidate = SkillDraft.model_validate(candidate.model_dump())
        data = candidate.model_dump()
        safe_files(data["files"])
        if any(name in data["files"] for name in ("parameters.json", "examples.json", "tables.json")):
            raise ValueError("Generated package resource name is reserved")
        no_secrets(data)
        if len(canonical(data).encode("utf-8")) > 240000:
            raise ValueError("Draft exceeds 240 KB")
        with self.registry.transaction() as db:
            body = self._load(db, identifier, principal)
            if body["state"] == "published":
                raise ValueError("A published draft cannot change")
            body.update(state="parameterized", candidate=data, validation=None, review=None)
            self._save(db, body)
        return body

    def validate(self, identifier, principal):
        body = self.get(identifier, principal)
        if body["state"] == "published" or body["candidate"] is None:
            raise ValueError("Parameterize an unpublished draft before validation")
        candidate = body["candidate"]
        findings = []
        try:
            schema = candidate["parameters"]
            jsonschema.Draft202012Validator.check_schema(schema)
            if schema.get("type") != "object" or schema.get("additionalProperties") is not False or "$ref" in canonical(schema):
                raise ValueError("Use a closed object parameter schema without references")
            validator = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())
            for example in candidate["examples"]:
                validator.validate(example)
            for table in candidate["tables"]:
                if set(table) != {"name", "ddl", "digest"} or not re.fullmatch(r"[a-f0-9]{64}", table["digest"]):
                    raise ValueError("Each table requires its name, reviewed DDL, and schema digest")
        except (ValueError, jsonschema.ValidationError, jsonschema.SchemaError) as exc:
            findings.append({"severity": "error", "rule": "parameters", "message": str(exc)[:500]})
        # Reuse DeerFlow's native package analyzer and SkillScan; never execute candidate code.
        from deerflow.skills.review.analyzer import analyze_skill_package
        from deerflow.skills.review.readers import LocalDirectoryReader

        files = package(candidate)
        with tempfile.TemporaryDirectory(prefix="deerflow-review-") as temp:
            for name, content in files.items():
                target = Path(temp) / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            facts = analyze_skill_package(LocalDirectoryReader(Path(temp)).read())
        findings.extend({"severity": f["severity"], "rule": f["rule_id"], "message": f["message"]} for f in facts["findings"])
        if facts["analyzer_errors"] or facts["reader_errors"] or facts["completeness"]["not_assessed"]:
            findings.append({"severity": "error", "rule": "review.incomplete", "message": "Package review did not complete"})
        if body["evidence"]["incomplete"]:
            findings.append({"severity": "error", "rule": "evidence.incomplete", "message": "Run evidence exceeds capture limits"})
        semantic = {"status": "not_configured", "checks": [], "receipt_digest": None}
        if self.validator is not None:
            try:
                result = self.validator(candidate, body["evidence"], principal)
                if not isinstance(result, dict) or type(result.get("valid")) is not bool or not isinstance(result.get("checks"), list) or not all(isinstance(check, str) for check in result["checks"]):
                    raise ValueError("Invalid semantic validation contract")
                no_secrets(result)
                if len(canonical(result).encode("utf-8")) > 32000:
                    raise ValueError("Validation receipt too large")
                semantic = {"status": "passed" if result["valid"] else "failed", "checks": result["checks"], "receipt_digest": digest(result.get("receipt"))}
                if not result["valid"]:
                    findings.append({"severity": "error", "rule": "semantic.failed", "message": "Deployment validation rejected this recipe"})
            except Exception:
                semantic["status"] = "failed"
                findings.append({"severity": "error", "rule": "semantic.unavailable", "message": "Deployment validation failed or is unavailable"})
        elif self.require_semantic_validation:
            findings.append({"severity": "error", "rule": "semantic.required", "message": "Configure the deployment validator for approved table permissions, schema freshness, and expected behavior"})
        report = {
            "digest": digest(candidate),
            "valid": not any(f["severity"] in ("error", "blocker") for f in findings),
            "findings": findings,
            "checks": ["parameter fixture validation", "package structure", "native SkillScan"],
            "query_execution": "not performed",
            "semantic_validation": semantic,
        }
        with self.registry.transaction() as db:
            current = self._load(db, identifier, principal)
            if current["state"] == "published" or digest(current["candidate"]) != report["digest"]:
                raise ValueError("Draft changed during validation; validate again")
            current.update(state="validated", validation=report, review=None)
            self._save(db, current)
        return report

    def review(self, identifier, principal, checksum, approve, notes):
        if not isinstance(notes, str) or not notes.strip() or len(notes) > 4000 or type(approve) is not bool:
            raise ValueError("Provide a review decision and notes")
        with self.registry.transaction() as db:
            body = self._load(db, identifier, principal, review=True)
            if body["state"] == "published" or not body["validation"] or not body["validation"]["valid"]:
                raise ValueError("Passing validation is required for review")
            if checksum != body["validation"]["digest"] or checksum != digest(body["candidate"]):
                raise ValueError("Review digest does not match validated content")
            body.update(state="reviewed", review={"reviewer": principal.user_id, "digest": checksum, "approved": approve, "notes": notes})
            self._save(db, body)
            self.registry.audit(db, principal.user_id, body["team"], "review", f"{identifier}:{checksum}")
        return body

    def publish(self, identifier, principal, capability_id, version):
        with self.registry.transaction() as db:
            body = self._load(db, identifier, principal)
            if body["state"] == "published":
                if body["release"]["id"] != capability_id or body["release"]["version"] != version:
                    raise ValueError("Draft already published under a different version")
                return body["release"]
            review, report = body["review"], body["validation"]
            if not review or not review["approved"] or not report or not report["valid"] or review["digest"] != digest(body["candidate"]):
                raise ValueError("Passing validation and independent review required")
            candidate = body["candidate"]
            release = self.registry._register(
                db,
                Capability(
                    id=capability_id,
                    version=version,
                    kind="skill",
                    origin="internal",
                    team=body["team"],
                    name=candidate["name"],
                    description=candidate["description"],
                    dependencies=candidate["dependencies"],
                    definition={"files": package(candidate), "provenance": {"draft": identifier, "evidence_digest": digest(body["evidence"]), "review": review, "validation": report}},
                ),
                principal.user_id,
            )
            body.update(state="published", release=release)
            self._save(db, body)
            self.registry.audit(db, principal.user_id, body["team"], "publish", f"{capability_id}@{version}")
        return release

    def purge_expired(self):
        # Release/provenance remain in the immutable registry; temporary evidence is removed.
        with self.registry.transaction() as db:
            result = db.execute(self.registry.drafts.delete().where(self.registry.drafts.c.expires <= self.clock()))
            return result.rowcount
