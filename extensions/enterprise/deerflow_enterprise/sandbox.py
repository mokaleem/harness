"""Adapter for an operator-approved remote sandbox broker (protocol v1).

The broker, outside the Gateway trust domain, enforces OS/filesystem/network
limits and server-side command deadlines. TLS authenticates its policy receipt.
No local execution, container daemon, or arbitrary per-request endpoint exists.
"""

from __future__ import annotations

import base64
import os
import re
import threading
import time
from pathlib import PurePosixPath
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field

from deerflow.sandbox.sandbox import Sandbox, _validate_extra_env
from deerflow.sandbox.sandbox_provider import SandboxProvider
from deerflow.sandbox.search import GrepMatch


class SandboxPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    profile: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    cpu: float = Field(default=1.0, gt=0, le=16, allow_inf_nan=False)
    memory_mb: int = Field(default=512, ge=128, le=32768)
    disk_mb: int = Field(default=512, ge=32, le=32768)
    lifetime_seconds: int = Field(default=900, ge=30, le=3600)
    command_seconds: int = Field(default=60, ge=1, le=600)
    max_file_bytes: int = Field(default=1_000_000, ge=1024, le=4_000_000)
    max_skill_bytes: int = Field(default=16_000_000, ge=1024, le=64_000_000)
    max_skill_files: int = Field(default=2000, ge=1, le=10000)
    network_domains: list[str] = Field(default_factory=list, max_length=100)
    writable_roots: list[str] = Field(default_factory=lambda: ["/mnt/user-data"])
    readonly_roots: list[str] = Field(default_factory=lambda: ["/mnt/skills"])


class RemoteSandbox(Sandbox):
    def __init__(self, identifier, provider, expires):
        self._id, self.provider, self.expires = identifier, provider, expires

    def _path(self, path, *, write=False):
        if not isinstance(path, str) or "\\" in path or "\0" in path or any(p in (".", "..") for p in path.split("/")):
            raise PermissionError("Sandbox path traversal prohibited")
        roots = self.provider.policy.writable_roots + ([] if write else self.provider.policy.readonly_roots)
        normalized = str(PurePosixPath(path))
        if not any(normalized == r or normalized.startswith(r + "/") for r in roots):
            raise PermissionError("Path outside approved sandbox roots")
        return normalized

    def _call(self, operation, **payload):
        if time.time() >= self.expires:
            raise TimeoutError("Sandbox lease expired")
        if self.provider.get(self.id) is not self:
            raise PermissionError("Sandbox lease is released or fenced")
        return self.provider.rpc("POST", f"/v1/sandboxes/{self.id}/operations", {"operation": operation, **payload})

    def execute_command(self, command, env=None, timeout=None):
        _validate_extra_env(env)
        timeout = min(float(timeout or self.provider.policy.command_seconds), self.provider.policy.command_seconds, max(0.1, self.expires - time.time()))
        if timeout <= 0 or len(command) > 32000:
            raise ValueError("Invalid remote command")
        # Broker must return only AFTER execution terminates or its process group is killed.
        result = self._call("execute", command=command, env=env or {}, timeout_seconds=timeout)
        if result.get("running"):
            raise RuntimeError("Broker violated terminal command contract")
        return str(result.get("output", ""))

    def read_file(self, path, start_line=None, end_line=None):
        result = self._call("read", path=self._path(path), start_line=start_line, end_line=end_line)
        return str(result.get("output", ""))

    def download_file(self, path):
        result = self._call("download", path=self._path(path))
        data = base64.b64decode(result["data"], validate=True)
        if len(data) > self.provider.policy.max_file_bytes:
            raise ValueError("Remote file exceeds byte limit")
        return data

    def list_dir(self, path, max_depth=2):
        return self._call("list", path=self._path(path), max_depth=min(max_depth, 10))["items"]

    def write_file(self, path, content, append=False):
        data = content.encode("utf-8")
        self._write(self._path(path, write=True), data, append)

    def _write(self, path, data, append=False):
        if len(data) > self.provider.policy.max_file_bytes:
            raise ValueError("Remote file exceeds byte limit")
        self._call("write", path=path, data=base64.b64encode(data).decode("ascii"), append=append)

    def update_file(self, path, content):
        self._write(self._path(path, write=True), content)

    def glob(self, path, pattern, *, include_dirs=False, max_results=200):
        result = self._call("glob", path=self._path(path), pattern=pattern, include_dirs=include_dirs, max_results=min(max_results, 200))
        items = result["items"]
        for item in items:
            self._path(item)
        return items[:max_results], bool(result["truncated"]) or len(items) > max_results

    def grep(self, path, pattern, *, glob=None, literal=False, case_sensitive=False, max_results=100):
        result = self._call("grep", path=self._path(path), pattern=pattern, glob=glob, literal=literal, case_sensitive=case_sensitive, max_results=min(max_results, 100))
        items = [GrepMatch(**item) for item in result["items"]]
        for item in items:
            self._path(item.path)
        return items[:max_results], bool(result["truncated"]) or len(items) > max_results


class RemoteSandboxProvider(SandboxProvider):
    supports_agent_skill_isolation = True
    needs_upload_permission_adjustment = False

    def __init__(self, *, endpoint=None, approved_endpoints=None, token_env=None, policy=None, transport=None, **kwargs):
        if endpoint is None:
            from deerflow.config import get_app_config

            config = get_app_config().sandbox.model_extra.get("enterprise_remote", {})
            endpoint, approved_endpoints, token_env = config.get("endpoint"), config.get("approved_endpoints"), config.get("token_env")
            policy = SandboxPolicy.model_validate(config.get("policy", {}))
        parts = urlsplit(endpoint or "")
        if endpoint not in (approved_endpoints or []) or parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment or parts.path not in ("", "/"):
            raise ValueError("Sandbox endpoint must be operator-approved HTTPS origin")
        self.policy = policy if isinstance(policy, SandboxPolicy) else SandboxPolicy.model_validate(policy)
        for domain in self.policy.network_domains:
            if not re.fullmatch(r"[a-z0-9]+(?:[.-][a-z0-9]+)+", domain):
                raise ValueError("Use exact approved DNS domains")
        if self.policy.writable_roots != ["/mnt/user-data"] or self.policy.readonly_roots != ["/mnt/skills"]:
            raise ValueError("Protocol v1 requires fixed data and read-only skill roots")
        token = os.environ.get(token_env or "")
        if not token:
            raise ValueError("Approved sandbox broker token environment variable is required")
        self.client = httpx.Client(base_url=endpoint, headers={"Authorization": f"Bearer {token}"}, timeout=self.policy.command_seconds + 10, follow_redirects=False, transport=transport)
        self._lock = threading.RLock()
        self._lifecycle = threading.Lock()
        self._sandboxes, self._owners = {}, {}
        self._releasing = set()

    def rpc(self, method, path, body=None):
        # No HTTP replay: an execute/create timeout has an ambiguous remote outcome.
        with self.client.stream(method, path, json=body) as response:
            if response.is_redirect:
                raise PermissionError("Sandbox broker redirects are prohibited")
            response.raise_for_status()
            chunks, size = [], 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > self.policy.max_file_bytes * 2 + 64000:
                    raise ValueError("Sandbox response exceeds byte limit")
                chunks.append(chunk)
            import json

            return json.loads(b"".join(chunks))

    def acquire(self, thread_id=None, *, user_id=None):
        if not thread_id or not user_id:
            raise PermissionError("Remote sandboxes require thread and user identity")
        with self._lifecycle:
            with self._lock:
                for identifier, owner in self._owners.items():
                    sandbox = self._sandboxes[identifier]
                    if identifier not in self._releasing and owner == (user_id, thread_id) and sandbox.expires > time.time():
                        return identifier
            receipt = self.rpc("POST", "/v1/sandboxes", {"protocol": 1, "user_id": user_id, "thread_id": thread_id, "policy": self.policy.model_dump()})
            identifier = receipt.get("id", "")
            if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", identifier):
                raise PermissionError("Invalid sandbox lease identifier")
            try:
                expires = receipt["expires_at"]
                if (
                    receipt.get("identity") != {"user_id": user_id, "thread_id": thread_id}
                    or receipt.get("enforced") != self.policy.model_dump()
                    or not isinstance(expires, (int, float))
                    or not time.time() < expires <= time.time() + self.policy.lifetime_seconds + 5
                ):
                    raise PermissionError("Remote sandbox policy receipt does not match approved policy")
                if identifier in self._sandboxes:
                    raise PermissionError("Broker returned an already-owned sandbox")
            except BaseException:
                # An existing foreign lease must never be deleted on collision.
                if identifier not in self._sandboxes:
                    self.rpc("DELETE", f"/v1/sandboxes/{identifier}")
                raise
            with self._lock:
                self._sandboxes[identifier] = RemoteSandbox(identifier, self, expires)
                self._owners[identifier] = (user_id, thread_id)
            return identifier

    def get(self, sandbox_id):
        with self._lock:
            sandbox = self._sandboxes.get(sandbox_id)
            return sandbox if sandbox and sandbox_id not in self._releasing and sandbox.expires > time.time() else None

    def get_scoped(self, sandbox_id, *, thread_id, user_id):
        with self._lock:
            return self.get(sandbox_id) if self._owners.get(sandbox_id) == (user_id, thread_id) else None

    def release(self, sandbox_id):
        with self._lifecycle:
            with self._lock:
                if sandbox_id not in self._sandboxes:
                    return
                self._releasing.add(sandbox_id)
            # A failed/ambiguous delete stays fenced until retry or the broker TTL.
            try:
                self.rpc("DELETE", f"/v1/sandboxes/{sandbox_id}")
            except BaseException:
                raise
            with self._lock:
                self._sandboxes.pop(sandbox_id)
                self._owners.pop(sandbox_id)
                self._releasing.discard(sandbox_id)

    def sync_agent_skills(self, sandbox_id, *, thread_id, user_id, projection):
        sandbox = self.get_scoped(sandbox_id, thread_id=thread_id, user_id=user_id)
        if sandbox is None:
            raise PermissionError("Sandbox owner mismatch")
        files, total = {}, 0
        for category in ("public", "custom", "legacy", "integrations"):
            root = getattr(projection, category)
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if path.is_symlink():
                    raise PermissionError("Skill projection symlinks prohibited")
                if path.is_file():
                    data = path.read_bytes()
                    total += len(data)
                    if len(data) > self.policy.max_file_bytes or total > self.policy.max_skill_bytes or len(files) >= self.policy.max_skill_files:
                        raise ValueError("Skill projection exceeds approved upload limits")
                    files[f"/mnt/skills/{category}/{path.relative_to(root).as_posix()}"] = base64.b64encode(data).decode("ascii")
        # Privileged broker endpoint atomically replaces the read-only skill mount.
        self.rpc("POST", f"/v1/sandboxes/{sandbox_id}/skills", {"files": files})

    def sandbox_network_mode(self):
        return "allowlist" if self.policy.network_domains else "isolated"

    def shutdown(self):
        for identifier in tuple(self._sandboxes):
            self.release(identifier)
        self.client.close()
