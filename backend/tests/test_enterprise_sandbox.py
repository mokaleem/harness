import json
import sys
import time
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "extensions" / "enterprise"))

from deerflow_enterprise.sandbox import RemoteSandboxProvider, SandboxPolicy


def provider(monkeypatch, *, altered=False):
    monkeypatch.setenv("TEST_BROKER_TOKEN", "synthetic")
    calls = []
    policy = SandboxPolicy(profile="approved-ran", cpu=1.0, memory_mb=512, disk_mb=128, lifetime_seconds=300, command_seconds=20, network_domains=["bigquery.googleapis.com"])

    def handler(request):
        body = json.loads(request.content) if request.content else {}
        calls.append((request.url.path, body))
        if request.url.path == "/v1/sandboxes":
            enforced = policy.model_dump()
            if altered:
                enforced["memory_mb"] = 8192
            return httpx.Response(200, json={"id": "sandbox-1", "expires_at": time.time() + 300, "identity": {"user_id": body["user_id"], "thread_id": body["thread_id"]}, "enforced": enforced})
        return httpx.Response(200, json={"output": "ok", "data": "", "items": [], "truncated": False})

    result = RemoteSandboxProvider(endpoint="https://broker.example.test", approved_endpoints=["https://broker.example.test"], token_env="TEST_BROKER_TOKEN", policy=policy, transport=httpx.MockTransport(handler))
    return result, calls


def test_remote_receipt_identity_limits_and_no_local_fallback(monkeypatch):
    remote, calls = provider(monkeypatch)
    identifier = remote.acquire("thread", user_id="alice")
    assert remote.get_scoped(identifier, thread_id="thread", user_id="bob") is None
    sandbox = remote.get(identifier)
    assert sandbox.execute_command("echo ok", timeout=999) == "ok"
    assert calls[-1][1]["timeout_seconds"] <= 20
    with pytest.raises(PermissionError):
        sandbox.read_file("/etc/passwd")
    with pytest.raises(PermissionError):
        sandbox.write_file("/mnt/user-data/../secret", "bad")
    with pytest.raises(PermissionError):
        sandbox.write_file("/mnt/skills/SKILL.md", "bad")
    remote.release(identifier)
    assert remote.get(identifier) is None


def test_weakened_limits_rejected_and_lease_deleted(monkeypatch):
    remote, calls = provider(monkeypatch, altered=True)
    with pytest.raises(PermissionError, match="policy"):
        remote.acquire("thread", user_id="alice")
    assert remote.get("sandbox-1") is None
    assert calls[-1][0] == "/v1/sandboxes/sandbox-1"


def test_only_operator_approved_https_endpoint(monkeypatch):
    monkeypatch.setenv("TEST_BROKER_TOKEN", "synthetic")
    with pytest.raises(ValueError, match="approved"):
        RemoteSandboxProvider(endpoint="https://evil.example", approved_endpoints=["https://broker.example"], token_env="TEST_BROKER_TOKEN", policy=SandboxPolicy(profile="approved"))


def test_retained_handle_cannot_execute_after_ambiguous_release(monkeypatch):
    remote, calls = provider(monkeypatch)
    identifier = remote.acquire("thread", user_id="alice")
    retained = remote.get(identifier)
    original = remote.rpc

    def fail_delete(method, path, body=None):
        if method == "DELETE":
            raise httpx.ReadTimeout("ambiguous delete")
        return original(method, path, body)

    remote.rpc = fail_delete
    with pytest.raises(httpx.ReadTimeout):
        remote.release(identifier)
    before = len(calls)
    with pytest.raises(PermissionError, match="fenced"):
        retained.execute_command("echo blocked")
    assert len(calls) == before
