# Enterprise capability services

The installable package in [`extensions/enterprise`](../extensions/enterprise/)
adds four enterprise features around DeerFlow's existing runtime. It does not
replace the agent engine. The registry and draft workflow can also be called as
Python services; only `adapter.py`, `runtime.py`, `skill_storage.py`, and
`sandbox.py` depend on DeerFlow runtime details.

## Capabilities and ownership

| Feature | Implementation | Deployment responsibility |
| --- | --- | --- |
| Authoritative registry | Immutable revisions, activation pointers, exact dependency pins, team memberships, audit events | Register the team's existing capabilities and deploy a generated projection |
| Create a skill | Conversation action and Skill releases page; evidence receipts, parameter schema, examples, native SkillScan, independent review, immutable publication | Configure a domain validator for warehouse permissions, schema freshness, and expected behavior |
| Dependency recovery | Async tool interception, at most four attempts, total/per-attempt deadlines, shared circuit state, partial-result contract, escalation queue | Mark only genuinely idempotent operations as retryable; configure transport deadlines |
| Remote execution | Approved HTTPS broker provider, identity-bound leases, exact policy receipt, resource limits, read-only skills | Operate or integrate an approved broker that enforces these limits outside the Gateway |

SQLite supports native local development. Use PostgreSQL for shared registry,
membership, draft, audit, and circuit state across workers and deployments.
The registry owns its versioned SQL tables; it does not modify DeerFlow's
application database schema. Back up the registry independently.

Each deployment projection serves **one team**. Multiple team deployments share
one registry database. This release does not dynamically swap an entire team
configuration inside a running Gateway. Gateway users must be assigned to their
team through the registry membership action or CLI. Existing DeerFlow
authentication, RBAC, MCP credential scope, and database permissions still apply.
The middleware checks membership before the model runs and again before tools;
runtime checks grant no special exemption to administrator-owned PAT runs.

## Installation on Windows and macOS

First complete the native setup in [README](../README.md). This extension needs
the extension API **0.2.5** and its corresponding host changes in this checkout.
Its additions preserve defaults for existing observational extensions.

Windows PowerShell, from the repository root:

```powershell
$EnterpriseSource = (Resolve-Path extensions/enterprise).Path
Set-Location backend
uv run deerflow extensions install $EnterpriseSource --yes --required
```

macOS Terminal, from the repository root:

```bash
enterprise_source="$PWD/extensions/enterprise"
cd backend
uv run deerflow extensions install "$enterprise_source" --yes --required
```

The extension manager snapshots this trusted package, updates the extension
dependency group and lock, and adds its plugin declaration. Package installation
does not start services. Configure the resulting plugin record before restarting
the Gateway. No Docker or Podman is required on developers' workstations.

For a SQLite registry, set an absolute deployment-owned path whose parent exists:

```powershell
$env:ENTERPRISE_DB_URL = "sqlite:///D:/poc/deer-flow/backend/.deer-flow/enterprise.db"
```

```bash
export ENTERPRISE_DB_URL="sqlite:///$PWD/.deer-flow/enterprise.db"
```

For PostgreSQL, inject `ENTERPRISE_DB_URL` through the approved secret mechanism
with a `postgresql+psycopg://` SQLAlchemy URL. The backend already includes psycopg;
standalone package consumers can install its `postgres` extra. Do not put this
URL, database passwords, or broker tokens into registry definitions or CLI arguments.

In the operator-owned root `config.yaml`:

```yaml
plugins:
  - name: enterprise
    use: deerflow_enterprise:install
    enabled: true
    required: true
    table_prefix: enterprise_
    config:
      database_url_env: ENTERPRISE_DB_URL
      team: ran
      draft_ttl_seconds: 86400
      require_semantic_validation: true
      validation_use: company_validation:validate_recipe
```

`validation_use` is an installed, trusted Python callable. If it is absent,
the page can capture, parameterize, and scan drafts, but publication is blocked
by required semantic validation. Setting `require_semantic_validation: false`
permits structural validation alone for a development demonstration. That
setting does not prove that a query returns correct business results.

Keep `table_prefix: enterprise_` on the plugin declaration. It tells host
migration discovery that these tables belong to the extension if an operator
chooses to share a physical database; the registry still owns their lifecycle.

Restart the Gateway with the existing README command. Open **Skill releases**
in the sidebar. Administrators can register revisions, activate desired versions,
manage membership, review drafts, and inspect dependency escalations. The
conversation menu includes **Create a skill**.

## Registry definition contract

Every definition has a stable `id`, immutable `version`, `kind`, `origin`, `team`,
runtime `name`, `description`, `definition`, and optional exact `dependencies`.
An ID cannot change its team, kind, or runtime name between versions. Duplicate
versions with identical bytes are idempotent; different bytes are rejected.
Conflicting active runtime names, missing dependency pins, cross-team pins,
dependency cycles, and removal of an active dependency are rejected.

| Kind | `definition` format |
| --- | --- |
| `skill` | `files`: package-relative UTF-8 text resources including `SKILL.md` |
| `tool` | Existing `ToolConfig` fields such as `use`, `group`, and `defer_loading` |
| `mcp_server` | Existing `McpServerConfig`, including stdio or HTTP/SSE transport and environment references |
| `agent` | `config`: native `AgentConfig`; `soul`: its `SOUL.md` text |
| `subagent` | Existing `CustomSubagentConfig`, including prompt, tool/skill selection and execution limits |

All five accept `origin: internal` or `external`. Origin records provenance; it
does not change the execution protocol. A remote external agent needs an approved
MCP/Python adapter and a native agent definition that delegates through it.
Registering an arbitrary agent URL does not implement a remote-agent protocol.

For example, an approved MCP server for telecom BigQuery access:

```json
{
  "id": "ran.bigquery", "version": "1.0.0",
  "kind": "mcp_server", "origin": "internal", "team": "ran",
  "name": "ran_bq", "description": "Read approved RAN analytics tables",
  "definition": {
    "transport": "http", "url": "https://approved-mcp.example/mcp",
    "headers": {"Authorization": "${RAN_BQ_AUTHORIZATION}"},
    "failure_policy": {
      "attempts": 1, "idempotent": false,
      "timeout_seconds": 20.0, "total_seconds": 25.0,
      "failure_threshold": 3, "cooldown_seconds": 30.0
    }
  },
  "dependencies": {}
}
```

The authorization environment value contains the complete header value.
Secret references are converted to DeerFlow's `$ENV_VAR` syntax during projection.
Obvious credential literals are rejected; this is not a comprehensive DLP scan.
Treat package content, review notes, and definitions as governed enterprise data.

## Projection and SDK usage

Activation selects desired state. Publication and activation do **not** immediately
alter running agents. The operator creates a deployment bundle and restarts against
it; the same workflow rolls back to an earlier approved immutable version.

From `backend/`, after installing the extension:

```powershell
uv run python -m deerflow_enterprise import-json D:/approved/ran-capabilities.json
uv run python -m deerflow_enterprise activate ran.bigquery 1.0.0
uv run python -m deerflow_enterprise membership --team ran --user-id AUTHENTICATED_USER_ID
uv run python -m deerflow_enterprise project --team ran --base ../config.yaml --destination D:/approved/ran-releases
```

`import-json` takes a list of definitions. Register and activate existing Python
tools, skills, agents, and subagents as well as MCP servers before deployment.
Managed capability lists are replaced by the registry snapshot. Native protocol
helpers (`ask_clarification`, discovery, artifact presentation, and delegation)
remain engine mechanics; delegated subagent names still require registration.
Other operator-installed Python extensions remain in the runtime base.

The returned directory contains:

```text
<revision>/
  config.yaml
  extensions_config.json
  manifest.json
  capabilities.json
  typesense-documents.json
  skills/public/<skill>/...
  home/agents/<agent>/{config.yaml,SOUL.md}
```

Set the Gateway environment to that bundle before starting:

```powershell
$Bundle = "D:/approved/ran-releases/REVISION_PRINTED_BY_PROJECT"
$env:DEER_FLOW_CONFIG_PATH = "$Bundle/config.yaml"
$env:DEER_FLOW_EXTENSIONS_CONFIG_PATH = "$Bundle/extensions_config.json"
$env:DEER_FLOW_HOME = "$Bundle/home"
```

```bash
bundle="/approved/ran-releases/REVISION_PRINTED_BY_PROJECT"
export DEER_FLOW_CONFIG_PATH="$bundle/config.yaml"
export DEER_FLOW_EXTENSIONS_CONFIG_PATH="$bundle/extensions_config.json"
export DEER_FLOW_HOME="$bundle/home"
```

Use a deployment-owned directory with appropriate ACLs. A generated release is
not edited in place. Managed file changes fail verification at startup and before
execution. Registry skill storage exposes published packages and rejects direct
skill installation/edit/delete and per-user enabled switches. Personal and legacy
skill folders do not augment the managed catalog. Custom-agent API writes are
disabled in generated projections; native agent templates remain discoverable.

Embedded applications use the same generated configuration with `DeerFlowClient`,
and bind their authenticated identity before running. The registry and workflow
remain ordinary Python APIs (`Registry`, `Capability`, `Workflow`, `SkillDraft`).
The plugin UI requires a Gateway host; it is not required by those Python services.

## Typesense

`typesense-documents.json` is a derived, credential-free metadata projection:
ID, version, kind, origin, team, name, description, and registry digest. Your indexer
can replace the team collection from this snapshot and query it for discovery.
Reauthorize a search hit and resolve its exact version through the registry before
execution. Index freshness never grants permission or changes active versions.
This package does not connect to a Typesense server or implement an index worker.

## Create a skill and telecom validation

1. Finish a conversation, such as analysis of missing site-health measurements.
2. Click **Create a skill**. The server reads the caller's visible terminal run.
   It stores event receipts and content hashes, not wholesale prompts, subscriber
   rows, tokens, or raw tool output. The original run remains the evidence source.
3. In **Skill releases**, explicitly write reusable instructions, query resources,
   table DDL references/digests, JSON Schema parameters, and example inputs. The
   initial recipe is a sample, not an automatic conversion of the last answer.
4. Validate. Parameter examples are checked, package structure is reviewed with
   DeerFlow's analyzer and native SkillScan, and the deployment validator runs.
5. A different authenticated administrator reviews that exact content digest.
   Changes invalidate validation and review. Rejection prevents publication.
6. Publish an immutable version. This atomically stores package bytes, dependency
   pins, validation/review provenance, and the evidence digest in the registry.
7. An administrator activates the desired version and deploys a projection.

Capture is bounded to 10,000 run-history entries and 1,000 evidence events.
Incomplete evidence prevents validation. Drafts expire after one day by default
and are purged every minute while the Gateway extension service is running.
Published package/provenance remain in the registry after temporary evidence is
purged. Repeating publication for the same draft/ID/version is idempotent.
Promotion v1 accepts text/query resources; it does not execute uploaded scripts.

The trusted validation callable receives `(candidate, evidence, principal)`:

```python
def validate_recipe(candidate, evidence, principal):
    # Use a bounded, approved warehouse adapter scoped to principal.user_id.
    # Check current DDL digests, required columns, supported joins, dry runs,
    # bytes billed limits, and synthetic expected-behavior fixtures.
    # Do not interpolate user inputs into SQL; use driver query parameters.
    return {
        "valid": True,  # Only after the checks have actually passed.
        "checks": ["schema freshness", "permission scope", "query dry run"],
        "receipt": {"validation_id": "VALIDATOR_RECEIPT_ID"},
    }
```

Implement this adapter against your approved catalog/query service. It must impose
its own transport deadline, return bounded non-secret receipts, and reject unknown
tables/joins. Failure, an invalid result, or a missing required validator blocks
publication. The validation report hashes the semantic receipt into release
provenance. Structural scanning itself executes no SQL and makes no claims about
warehouse data accuracy. BigQuery connection and schema discovery are not built
into this package.

For telecom queries, parameterize a date range and `site_id`; do not embed live
MDNs in examples. Validate relationships among `site_id`, `enodeb_id`, `gnodeb_id`,
and `fuze_site_id` against an approved semantic mapping. Similar column names
alone do not prove a correct join or compatible grain.

## Failure policy

Tool and MCP-server definitions may contain `failure_policy`. Server policy covers
its tools and shares a circuit under the capability ID. Keep mixed read/write
servers non-retryable; use separate registrations/adapters when a read-only tool
needs a different policy. No automatic retry policy is assigned to every tool.

Only explicitly idempotent calls retry, with at most four attempts, exponential
backoff capped at ten seconds, and per-attempt plus total async deadlines.
Connection/transport/timeouts and HTTP 429/502/503/504 are transient. Permission,
missing-resource, validation, and other HTTP errors are not replayed. Cancellation
propagates and releases the half-open probe. A database-shared probe lease admits
one recovery attempt after cooldown; stale completions cannot settle a newer probe.

Failure produces a model-visible dependency/category/attempt receipt and a durable
escalation ID. The agent retains earlier verified results and explains the missing
evidence. Standalone `Resilience.call(..., partial=...)` additionally carries an
explicit caller-provided partial result. The queue is visible in Skill releases;
this feature does not send email, Slack, or pager messages.

Gateway and async SDK tool paths use the policy. Synchronous embedded Python tools
are not safely killable or replayable by thread cancellation: that path checks
registry admission and performs one call, relying on the tool's transport deadline.
For generic tool deadlines use the async path; for code/process limits use the
remote sandbox's server-enforced deadline.

## Approved remote sandbox broker

Select the provider in the operator runtime base before projecting:

```yaml
sandbox:
  use: deerflow_enterprise.sandbox:RemoteSandboxProvider
  enterprise_remote:
    endpoint: https://approved-sandbox.example
    approved_endpoints: [https://approved-sandbox.example]
    token_env: ENTERPRISE_SANDBOX_TOKEN
    policy:
      profile: approved-ran
      cpu: 1.0
      memory_mb: 512
      disk_mb: 512
      lifetime_seconds: 900
      command_seconds: 60
      network_domains: [bigquery.googleapis.com]
```

The broker must implement these operations:

| Endpoint | Required behavior |
| --- | --- |
| `POST /v1/sandboxes` | Accept protocol 1, server-bound user/thread identity, and the complete policy. Return `id`, numeric Unix `expires_at`, identical `identity`, and identical `enforced` policy |
| `POST /v1/sandboxes/{id}/operations` | Execute/read/download/list/write/glob/grep in the identity-bound isolated environment; return bounded JSON |
| `POST /v1/sandboxes/{id}/skills` | Privileged atomic replacement of the base64 file map under the read-only skills mount |
| `DELETE /v1/sandboxes/{id}` | Terminate and remove the lease idempotently |

An execution request has `operation: execute`, `command`, `env`, and
`timeout_seconds`; the response includes `output` and cannot report ongoing work.
Read returns `output`; download returns base64 `data`; list returns string `items`;
glob/grep return `items` plus `truncated` (grep entries have `path`, `line_number`,
`line`). Write receives base64 `data` and `append`. File arguments use virtual paths.

The server must enforce CPU, memory, disk, lifetime, process-group termination at
the command deadline, writable `/mnt/user-data`, read-only `/mnt/skills`, symlink
containment, and deny-by-default network egress with the exact approved domains.
Empty domains mean isolated networking. Block access to host files, cloud metadata,
internal control planes, and other identities. Default individual file size is
1 MB; skill transfer is bounded to 16 MB and 2,000 files. The full default-expanded
policy is part of the receipt and must match exactly.

The adapter verifies the approved HTTPS origin, rejects redirects, checks identity
and exact policy receipt, clips command timeouts to policy/lease limits, bounds
responses and file transfer, and rejects path traversal and writes to skill roots.
It never falls back to local shell execution or replays an ambiguous RPC outcome.
A refused policy receipt triggers lease cleanup. Failed deletion fences the lease
until deletion is retried or its server TTL expires. `get` remains an in-memory
lookup while lifecycle RPCs run outside its metadata lock.

No broker is provisioned or certified by this change. Approval is an operator
trust decision and enforcement requires the remote service. This adapter is not
a new OS isolation implementation. Existing DeerFlow remote providers also remain
available for deployments that approve their own provider-specific controls.

## Upstream compatibility and verification

Keep the enterprise package independently versioned. The host patch adds three
small contracts: optional request-scoped evidence on `BackendAction`/`ActionContext`,
trusted execution middleware with a bounded downstream budget, and an operator
selected per-user skill storage implementation. Extension API 0.2.5 preserves
default observational behavior. It forwards the originally admitted tool request;
execution mode cannot substitute a new request after authorization.

When syncing upstream, run the enterprise contracts plus extension isolation,
injection, assembly identity, plugin authorization, skill storage, and harness
boundary tests. No copied agent loop or application imports exist in the package.

The adapter deliberately uses host-specific `deerflow.*` configuration models,
MCP provenance and user context, skill storage/provider interfaces, reflection,
and native package review. Those imports need compatibility testing when syncing
upstream; the extension API alone does not guarantee them. Pin and qualify host
releases alongside the separately versioned enterprise package.

```powershell
Set-Location backend
uv run python -m pytest tests/test_enterprise_registry.py tests/test_enterprise_workflow.py tests/test_enterprise_resilience.py tests/test_enterprise_sandbox.py tests/test_enterprise_adapter.py tests/test_enterprise_plugin.py tests/test_enterprise_runtime.py -q
```

Current validation uses synthetic data and mock remote transports. The browser
workflow was exercised against synthetic local services. PostgreSQL contention,
production BigQuery validation, a real Typesense indexer, and your approved remote
broker require deployment integration tests with those services.
