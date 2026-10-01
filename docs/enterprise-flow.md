# Enterprise harness flow diagrams

These six diagrams describe the implementation in this checkout: enterprise
package **0.1.0**, extension API **0.2.5**, and configuration template **v52**.
Open the [offline diagram viewer](enterprise-flow.html) to zoom, scroll, print,
or download the SVGs without a Mermaid renderer or an internet connection.
The Mermaid blocks below are the editable sources. The
[enterprise services guide](enterprise-services.md) owns the setup and API contracts.

## Reading the diagrams

| Appearance | Meaning |
| --- | --- |
| Blue | Existing DeerFlow engine and application behavior |
| Green | Implemented enterprise extension behavior |
| Purple cylinder | Persistent state or generated artifacts |
| Gold diamond | Admission, validation, or lifecycle decision |
| Orange dashed border / dashed arrow | Company-operated service or integration to supply |
| Red | Rejection, failure, or publication block |

Arrows describe control or data flow, not an additional service deployment.
The remote broker adapter is implemented; its isolation enforcement belongs to
the approved remote service. Typesense currently receives an exported metadata
file; there is no implemented index worker or Typesense request router.

## 1. End-to-end overview

![Enterprise harness overview](assets/enterprise-flow/overview.svg)

```mermaid
flowchart TB
  accTitle: Enterprise harness overview
  accDescr: Registry deployment, authenticated agent execution, company integrations, and reviewed skill publication.
  subgraph CONTROL["Operator control plane"]
    SOURCES["Internal + external definitions<br/>Skills, agents, subagents, Python tools, MCP servers"]:::enterprise
    REG[("Authoritative registry<br/>Immutable revisions + desired activation<br/>Team memberships + audit")]:::data
    PROJECT["Project one team's active snapshot<br/>Native configs + skills + agent templates + hashes"]:::enterprise
    DEPLOY["Operator deploys verified bundle<br/>Restart Gateway / initialize embedded SDK"]:::enterprise
    SOURCES --> REG --> PROJECT --> DEPLOY
  end
  subgraph EXEC["One team runtime deployment"]
    USER["User / another application<br/>Telecom site-health question"]:::core
    INGRESS["Gateway authentication or<br/>SDK authenticated identity binding"]:::core
    ADMIT{"Verified bundle + team membership<br/>+ registered custom agent?"}:::gate
    ENGINE["DeerFlow lead agent<br/>Plan → act → observe<br/>Thread state + scoped memory + evidence"]:::core
    DISCOVER["Bounded skill / tool discovery<br/>Clarify missing inputs; select relevant context"]:::core
    GUARD["Enterprise execution admission<br/>Registry tool / MCP provenance / subagent selection<br/>Optional retry + circuit policy"]:::enterprise
    EXECUTE["Native tools, MCP calls, delegated subagents<br/>Sandbox commands use approved remote provider"]:::core
    ANSWER["Supported answer / artifacts<br/>Explain missing evidence and dependency failures"]:::core
    USER --> INGRESS --> ADMIT
    ADMIT -->|yes| ENGINE --> DISCOVER --> GUARD --> EXECUTE
    EXECUTE -->|observations| ENGINE
    ENGINE --> ANSWER
    ADMIT -->|no| DENY["Stop before model execution"]:::stop
  end
  DEPLOY --> ADMIT
  REG -->|live membership check| ADMIT
  PROJECT --> SEARCHFILE[("typesense-documents.json<br/>Derived metadata only")]:::data
  SEARCHFILE -.-> INDEX["Company Typesense index worker<br/>and reauthorized search integration<br/>Not implemented here"]:::external
  ANSWER --> CLICK["User clicks Create a skill"]:::enterprise
  CLICK --> DRAFT["Capture → parameterize → validate<br/>Independent digest-bound review<br/>Publish immutable skill revision"]:::enterprise
  DRAFT -->|publish, then separate activation| REG
  EXECUTE -.-> SERVICES["Approved warehouse / external agent adapters<br/>Company remote sandbox broker"]:::external
  classDef core fill:#edf2ff,stroke:#365c99,color:#193656;
  classDef enterprise fill:#e7f4ed,stroke:#247056,color:#173d30;
  classDef data fill:#f1eefc,stroke:#72599d,color:#40325d;
  classDef gate fill:#fff4d6,stroke:#8b671a,color:#5d4614;
  classDef external fill:#fff1e8,stroke:#9c5b2b,color:#663b21,stroke-dasharray:5 4;
  classDef stop fill:#feece8,stroke:#9a433b,color:#6e2e29;
```

Each Gateway deployment serves one team. Multiple deployments share a registry;
the running engine does not switch team configurations dynamically. The default
lead agent is an engine entry point; selected custom agents and delegated subagent
names must be in the deployed snapshot. Remote agents require an approved native
MCP/Python adapter; `origin: external` alone does not implement a protocol.

## 2. Registry, activation, deployment, and rollback

![Registry and deployment flow](assets/enterprise-flow/registry-deployment.svg)

```mermaid
flowchart TB
  accTitle: Registry activation and deployment
  accDescr: Immutable registration, dependency checks, consistent snapshot projection, deployment verification, and operator rollback.
  INPUT["Administrator submits definition<br/>UI register / operator import-json"]:::enterprise
  STRUCT["Validate identity and envelope<br/>id, version, kind, origin, team, name<br/>Bound payload; reject obvious credential literals"]:::enterprise
  EXIST{"ID + version already exists?"}:::gate
  SAME{"Same content digest?"}:::gate
  REUSE["Idempotent registration<br/>Return existing immutable revision"]:::enterprise
  REJECT["Reject mutation / identity conflict<br/>No revision overwrite"]:::stop
  STORE[("Registry transaction<br/>Immutable revision + registration audit<br/>Exact dependency pins")]:::data
  ACTIVATE["Administrator selects desired version"]:::enterprise
  PINS{"Dependency versions active in same team?<br/>No cycle or conflicting runtime name?"}:::gate
  POINTER[("Transactional activation pointer + audit<br/>Desired state only")]:::data
  BASE["Operator runtime base<br/>Model, memory, persistence, plugin and sandbox config<br/>Secrets referenced through environment"]:::enterprise
  SNAP["Read one consistent team snapshot<br/>At most 10,000 active capabilities"]:::enterprise
  ADAPTER["Version-specific DeerFlow adapter<br/>Validate native config models<br/>Replace managed capability lists"]:::enterprise
  BUNDLE[("Immutable content-addressed bundle<br/>config.yaml + extensions_config.json<br/>capabilities.json + manifest.json<br/>skills/public + home/agents<br/>typesense-documents.json")]:::data
  VERIFY{"Manifest and managed files intact?"}:::gate
  START["Set config / extensions / home paths<br/>Deploy bundle and restart runtime<br/>Required enterprise extension loads"]:::enterprise
  RUN["Pinned runtime snapshot<br/>Managed skills read-only<br/>Custom-agent API writes disabled"]:::core
  INDEX["Optional company Typesense worker<br/>Replace derived team index<br/>Reauthorize hits; resolve exact registry revision"]:::external
  ROLLBACK["Operator rollback<br/>Select compatible earlier desired revisions<br/>Project and deploy again, or redeploy prior bundle"]:::enterprise
  INPUT --> STRUCT --> EXIST
  EXIST -->|yes| SAME
  SAME -->|yes| REUSE
  SAME -->|no| REJECT
  EXIST -->|no; identity stable| STORE
  STORE --> ACTIVATE
  REUSE --> ACTIVATE
  ACTIVATE --> PINS
  PINS -->|no| REJECT
  PINS -->|yes| POINTER --> SNAP
  BASE --> ADAPTER
  SNAP --> ADAPTER --> BUNDLE --> VERIFY
  VERIFY -->|no| REJECT
  VERIFY -->|yes| START --> RUN
  BUNDLE -.-> INDEX
  RUN -->|operator initiates| ROLLBACK --> ACTIVATE
  classDef core fill:#edf2ff,stroke:#365c99,color:#193656;
  classDef enterprise fill:#e7f4ed,stroke:#247056,color:#173d30;
  classDef data fill:#f1eefc,stroke:#72599d,color:#40325d;
  classDef gate fill:#fff4d6,stroke:#8b671a,color:#5d4614;
  classDef external fill:#fff1e8,stroke:#9c5b2b,color:#663b21,stroke-dasharray:5 4;
  classDef stop fill:#feece8,stroke:#9a433b,color:#6e2e29;
```

Publication and activation do not change a running agent. Deployment applies the
snapshot. Membership is checked live before the model and again before tool calls.
Registering an envelope is distinct from native configuration validation during
projection. Registry storage is SQLite locally or PostgreSQL across deployments;
DeerFlow's application checkpoints and memory remain separate responsibilities.

## 3. Prompt routing, clarification, and agent execution

![Request execution and discovery flow](assets/enterprise-flow/request-execution.svg)

```mermaid
flowchart TB
  accTitle: Prompt discovery and agent execution
  accDescr: Authenticated request admission, bounded context, clarification, tool and skill discovery, subagent delegation, and verified results.
  QUESTION["Prompt: Investigate degraded RAN site health<br/>for site_id during a date range"]:::core
  AUTH["Gateway resolves authenticated user + thread<br/>SDK caller binds authenticated user identity"]:::core
  ADMIT{"Bundle intact, team member,<br/>custom agent registered if selected?"}:::gate
  STOP["Reject run / tool admission<br/>No administrative bypass for runtime membership"]:::stop
  BUILD["Build native lead agent<br/>Selected model, prompt, middleware<br/>Scoped memory + checkpoint/thread context"]:::core
  PREVIEW["Bounded initial context<br/>Skill names / descriptions and routing hints<br/>Deferred tools omit bulk schemas"]:::core
  THINK["Model plans next step from prompt<br/>and permitted deployed capabilities"]:::core
  MISSING{"Missing essential information?"}:::gate
  INTERACTIVE{"Interactive lead-agent run?"}:::gate
  CLARIFY["ask_clarification<br/>Drop sibling tool calls; end current graph turn<br/>Show question / choices / form"]:::core
  REPLY["User supplies date / site / metric<br/>Continue with the response in a later turn"]:::core
  NONINTERACTIVE["Scheduled non-interactive run<br/>Clarification tool is excluded<br/>Return missing-input limitation or use approved defaults"]:::core
  CHOOSE{"Next action?"}:::gate
  SKILL["describe_skill searches permitted catalog<br/>Returns bounded matches / selected full instructions<br/>Load approved supporting resources as needed"]:::core
  TOOL["tool_search searches deferred tool catalog<br/>Promote bounded relevant schemas<br/>Preserve MCP source metadata"]:::core
  DELEGATE["task selects registered subagent type<br/>Native subagent gets its configured tool / skill scope<br/>Enterprise middleware also applies"]:::core
  CHECK["Reverify bundle and live team membership<br/>Admit native registry tool or registered MCP server<br/>Reject unregistered MCP provenance despite name collision"]:::enterprise
  POLICY{"Async dependency policy configured?"}:::gate
  RECOVER["Bounded recovery flow<br/>See diagram 5"]:::enterprise
  DIRECT["Invoke admitted tool once<br/>Sync path relies on transport deadline<br/>No automatic dependency policy added"]:::core
  OUTPUT["Tool observation / artifact / failure receipt<br/>Preserve earlier verified results"]:::core
  ENOUGH{"Enough evidence to answer?"}:::gate
  FINAL["Answer with supported measurements and charts<br/>State assumptions, missing evidence, and escalation ID"]:::core
  QUESTION --> AUTH --> ADMIT
  ADMIT -->|no| STOP
  ADMIT -->|yes| BUILD --> PREVIEW --> THINK --> MISSING
  MISSING -->|yes| INTERACTIVE
  INTERACTIVE -->|yes| CLARIFY --> REPLY --> THINK
  INTERACTIVE -->|no| NONINTERACTIVE --> FINAL
  MISSING -->|no| CHOOSE
  CHOOSE -->|skill instructions| SKILL --> THINK
  CHOOSE -->|find tool| TOOL --> THINK
  CHOOSE -->|delegate| DELEGATE --> CHECK
  CHOOSE -->|invoke selected tool| CHECK
  CHOOSE -->|supported answer ready| FINAL
  CHECK --> POLICY
  CHECK -->|admission fails| STOP
  POLICY -->|yes| RECOVER --> OUTPUT
  POLICY -->|no or synchronous| DIRECT --> OUTPUT
  OUTPUT --> ENOUGH
  ENOUGH -->|no; remaining permitted work| THINK
  ENOUGH -->|yes or dependency-limited result| FINAL
  classDef core fill:#edf2ff,stroke:#365c99,color:#193656;
  classDef enterprise fill:#e7f4ed,stroke:#247056,color:#173d30;
  classDef data fill:#f1eefc,stroke:#72599d,color:#40325d;
  classDef gate fill:#fff4d6,stroke:#8b671a,color:#5d4614;
  classDef external fill:#fff1e8,stroke:#9c5b2b,color:#663b21,stroke-dasharray:5 4;
  classDef stop fill:#feece8,stroke:#9a433b,color:#6e2e29;
```

The deployed catalogs drive discovery today; Typesense is not in this request path.
Template defaults bound prompt name previews to **50 per catalog**, discovery matches to **5**,
and active deferred tool schemas to **20**. Operators can configure these limits.
Exact skill selection and subsequent resource reads are distinct from catalog
search. Clarification is existing DeerFlow behavior, not an enterprise UI action.

## 4. Create a skill: evidence to immutable release

![Evidence capture and skill publication flow](assets/enterprise-flow/skill-publication.svg)

```mermaid
flowchart TB
  accTitle: Evidence to reviewed immutable skill
  accDescr: Terminal-run capture, expiring drafts, explicit parameterization, validation, independent review, immutable publication, and TTL cleanup.
  FINISH["Conversation reaches a terminal run<br/>Success, failure, interruption, or cancellation"]:::core
  CLICK["User clicks Create a skill"]:::enterprise
  AUTH["Host authorizes plugin action<br/>Request-scoped RunEvidenceReader<br/>Owner-visible evidence; no global reader fallback"]:::core
  TERMINAL{"Visible terminal run available?"}:::gate
  REJECT["Reject capture / publication<br/>Unavailable, running, expired, or unauthorized"]:::stop
  CAPTURE[("Expiring draft: captured<br/>Owner + team + thread/run references<br/>Event receipts and content hashes only")]:::data
  EDIT["User writes reusable recipe<br/>Instructions + SQL/text resources<br/>JSON Schema parameters + example inputs<br/>Reviewed table DDL / digests + dependency pins"]:::enterprise
  PARAM[("Draft: parameterized<br/>Invalidate previous validation and review")]:::data
  STRUCT["Structural validation<br/>Closed parameter schema + valid examples<br/>Path / resource limits + table reference shape<br/>Native package analyzer and SkillScan"]:::enterprise
  DOMAIN["Operator-configured telecom validator<br/>Schema freshness, permissions, approved joins<br/>BQ dry runs / cost bounds / expected behavior<br/>Company integration required"]:::external
  VALID{"All required checks pass?<br/>Evidence complete; draft unchanged?"}:::gate
  FIX["Validation blocked<br/>Show findings; revise recipe<br/>Configure unavailable required validator"]:::stop
  REPORT[("Draft: validated<br/>Exact candidate digest + scan findings<br/>Semantic receipt digest")]:::data
  REVIEW["Different authenticated administrator<br/>Reviews exact validated digest + notes"]:::enterprise
  APPROVE{"Independent review approves<br/>same current digest?"}:::gate
  REVISE["Review rejected / stale digest<br/>Revise, revalidate, and review again"]:::stop
  REVIEWED[("Draft: reviewed<br/>Reviewer identity + digest + decision")]:::data
  PUBLISH["Owner / permitted administrator publishes<br/>Check validation, review, expiry, and identity<br/>Atomic registration + draft publication + audit"]:::enterprise
  RELEASE[("Immutable skill ID + version<br/>Package bytes + exact dependencies<br/>Evidence digest + validation/review provenance")]:::data
  DEPLOY["Separate administrator activation<br/>Project and deploy a new bundle<br/>See diagram 2"]:::enterprise
  PURGE["TTL cleanup, every minute while service runs<br/>Default draft lifetime: 24 hours<br/>Temporary draft removed; immutable release retained"]:::enterprise
  FINISH --> CLICK --> AUTH --> TERMINAL
  TERMINAL -->|no| REJECT
  TERMINAL -->|yes| CAPTURE --> EDIT --> PARAM --> STRUCT --> DOMAIN --> VALID
  VALID -->|no| FIX --> EDIT
  VALID -->|yes| REPORT --> REVIEW --> APPROVE
  APPROVE -->|no| REVISE --> EDIT
  APPROVE -->|yes| REVIEWED --> PUBLISH --> RELEASE --> DEPLOY
  REPORT -->|recipe edited| EDIT
  REVIEWED -->|recipe edited| EDIT
  CAPTURE -->|expiry| PURGE
  RELEASE -->|temporary draft expires| PURGE
  classDef core fill:#edf2ff,stroke:#365c99,color:#193656;
  classDef enterprise fill:#e7f4ed,stroke:#247056,color:#173d30;
  classDef data fill:#f1eefc,stroke:#72599d,color:#40325d;
  classDef gate fill:#fff4d6,stroke:#8b671a,color:#5d4614;
  classDef external fill:#fff1e8,stroke:#9c5b2b,color:#663b21,stroke-dasharray:5 4;
  classDef stop fill:#feece8,stroke:#9a433b,color:#6e2e29;
```

The sample recipe does not automatically reproduce the last answer. Users enter
the logic and table references explicitly. Capture stores hashes rather than raw
customer rows or complete prompts, and is bounded to **1,000 evidence events**.
The plugin requires semantic validation by default; a missing validator blocks
publication. A standalone `Workflow` can deliberately allow structural-only
validation for development. Candidate scripts/queries are not executed by the
structural validator. Telecom identifier mappings must be approved by the domain
validator rather than inferred from matching column names.

## 5. Bounded retries, circuit breakers, and partial results

![Dependency recovery flow](assets/enterprise-flow/dependency-recovery.svg)

```mermaid
flowchart TB
  accTitle: Bounded dependency recovery
  accDescr: Shared circuit admission, one leased recovery probe, idempotent retries, permanent errors, cancellation, safe partial results, and escalation.
  CALL["Admitted async call with failure_policy<br/>Native tool ID or MCP-server capability ID"]:::enterprise
  CIRCUIT[("Shared registry circuit row<br/>Failure count, open-until, probe token, probe lease")]:::data
  OPEN{"Cooldown or another probe still active?"}:::gate
  PROBE{"Previously open / failure threshold reached?"}:::gate
  LEASE["Atomically acquire one half-open probe token<br/>Lease: total call budget + 5 seconds"]:::enterprise
  TRY["Invoke original admitted request<br/>Per-attempt timeout within total async deadline<br/>At most policy attempts; writes get one attempt"]:::enterprise
  RESULT{"Outcome?"}:::gate
  CLASSIFY{"Failure transient?<br/>Transport / connection / timeout<br/>HTTP 429, 502, 503, 504 or tagged transient error"}:::gate
  RETRY{"Explicitly idempotent AND<br/>attempt and total budgets remain?"}:::gate
  BACKOFF["Exponential backoff<br/>Delay capped at 10 seconds<br/>Maximum 4 attempts"]:::enterprise
  SETTLE["Settle failed operation<br/>Increment failures; open at threshold<br/>Stale calls cannot settle a newer probe"]:::enterprise
  SUCCESS["Settle success for current generation<br/>Reset failures / close circuit<br/>Return result to agent loop"]:::enterprise
  CANCEL["Cancellation propagates<br/>Release owned half-open probe<br/>No automatic replay"]:::core
  PERMANENT["Permission / missing resource / validation<br/>or other permanent error propagates<br/>No dependency-policy retry"]:::stop
  AUDIT[("Durable team-scoped escalation event<br/>Dependency ID + category + escalation ID<br/>No exception message or credential payload")]:::data
  PARTIAL["Return safe dependency failure ToolMessage<br/>Keep earlier verified context<br/>Standalone API can carry explicit partial result"]:::enterprise
  ANSWER["Agent explains supported results + gaps<br/>Suggest permitted alternative / operator assistance<br/>Do not invent unavailable measurements"]:::core
  QUEUE["Operator sees escalation queue<br/>Skill releases page<br/>No automatic Slack/email/page delivery"]:::enterprise
  CALL --> CIRCUIT --> OPEN
  OPEN -->|yes; no dependency dispatch| AUDIT
  OPEN -->|no| PROBE
  PROBE -->|yes| LEASE --> TRY
  PROBE -->|closed| TRY
  TRY --> RESULT
  RESULT -->|success| SUCCESS
  RESULT -->|cancelled| CANCEL
  RESULT -->|failure| CLASSIFY
  CLASSIFY -->|no| PERMANENT
  CLASSIFY -->|yes| RETRY
  RETRY -->|yes| BACKOFF --> TRY
  RETRY -->|no or total deadline exhausted| SETTLE --> AUDIT
  AUDIT --> PARTIAL --> ANSWER
  AUDIT --> QUEUE
  classDef core fill:#edf2ff,stroke:#365c99,color:#193656;
  classDef enterprise fill:#e7f4ed,stroke:#247056,color:#173d30;
  classDef data fill:#f1eefc,stroke:#72599d,color:#40325d;
  classDef gate fill:#fff4d6,stroke:#8b671a,color:#5d4614;
  classDef external fill:#fff1e8,stroke:#9c5b2b,color:#663b21,stroke-dasharray:5 4;
  classDef stop fill:#feece8,stroke:#9a433b,color:#6e2e29;
```

Default policy: **1 attempt**, **20 seconds per attempt**, **30 seconds total**,
**3 failed operations before opening**, and **30 seconds cooldown**. No policy is
automatically attached to every tool. MCP-server policy covers its exposed tools;
keep mixed read/write servers non-retryable. Synchronous embedded execution checks
admission and invokes once, relying on the tool's own transport deadline. Async
cancellation cannot guarantee termination of blocking code already running in a
worker thread; remote process deadlines must be enforced by the broker.

## 6. Approved remote sandbox lifecycle and enforcement

![Remote sandbox lifecycle](assets/enterprise-flow/remote-sandbox.svg)

```mermaid
flowchart TB
  accTitle: Approved remote sandbox lifecycle
  accDescr: Approved HTTPS configuration, owner-bound leases, exact policy receipts, remote enforcement, terminal output, and release fencing.
  CONFIG["Operator selects RemoteSandboxProvider<br/>Exact approved HTTPS origin + token environment<br/>CPU, memory, disk, lifetime, command, network policy"]:::enterprise
  VALID{"Endpoint approved, token present,<br/>fixed roots and exact domains valid?"}:::gate
  BLOCK["Reject provider / lease / operation<br/>No local-shell fallback; no ambiguous RPC replay"]:::stop
  OWNER["Acquire for authenticated user + thread"]:::core
  REUSE{"Same owner has live unfenced lease?"}:::gate
  CREATE["POST /v1/sandboxes<br/>Protocol 1 + user/thread + full policy"]:::enterprise
  BROKER["Company-operated approved HTTPS broker<br/>Creates isolated worker and enforces policy<br/>Infrastructure integration required"]:::external
  RECEIPT{"Safe unique lease ID, correct identity,<br/>valid expiry, identical enforced policy?"}:::gate
  CLEANUP["Reject receipt and request lease cleanup<br/>Never delete a tracked foreign lease on ID collision"]:::enterprise
  TRACK[("Provider in-memory owner map<br/>Lease ID + user/thread + expiry + fenced state")]:::data
  SKILLS["Scoped skill upload<br/>Reject symlinks; bound files and bytes<br/>POST /v1/sandboxes/id/skills"]:::enterprise
  MOUNT["Broker atomically replaces<br/>read-only /mnt/skills mount"]:::external
  OP["Execute / read / write / download / search<br/>Check tracked live lease and approved paths<br/>Bound transfer; clip command timeout to remaining TTL"]:::enterprise
  ENFORCE["Remote worker enforces OS controls<br/>CPU / memory / disk caps; filesystem containment<br/>Writable /mnt/user-data, read-only /mnt/skills<br/>Approved egress only; block metadata/control planes<br/>Kill process group at command deadline"]:::external
  TERMINAL{"Command terminated and response bounded?"}:::gate
  OBSERVE["Return terminal output / file / search result<br/>Agent loop observes supported evidence"]:::core
  RELEASE["Release marks lease fenced first<br/>DELETE /v1/sandboxes/id outside metadata lock"]:::enterprise
  DELETED{"Delete confirmed?"}:::gate
  FORGET["Remove owner map + lease entry"]:::enterprise
  FENCE["Keep lease fenced on ambiguous failure<br/>Retained handles cannot dispatch<br/>Operator deletion retry / broker TTL expiry"]:::enterprise
  CONFIG --> VALID
  VALID -->|no| BLOCK
  VALID -->|yes| OWNER --> REUSE
  REUSE -->|yes| TRACK
  REUSE -->|no| CREATE -.-> BROKER
  BROKER -.->|policy receipt| RECEIPT
  RECEIPT -->|no| CLEANUP --> BLOCK
  RECEIPT -->|yes| TRACK --> SKILLS -.-> MOUNT
  MOUNT -.-> OP -.-> ENFORCE
  ENFORCE -.-> TERMINAL
  TERMINAL -->|no| BLOCK
  TERMINAL -->|yes| OBSERVE
  OBSERVE -->|more admitted operations| OP
  OBSERVE -->|lifecycle cleanup| RELEASE --> DELETED
  DELETED -->|yes| FORGET
  DELETED -->|no| FENCE
  classDef core fill:#edf2ff,stroke:#365c99,color:#193656;
  classDef enterprise fill:#e7f4ed,stroke:#247056,color:#173d30;
  classDef data fill:#f1eefc,stroke:#72599d,color:#40325d;
  classDef gate fill:#fff4d6,stroke:#8b671a,color:#5d4614;
  classDef external fill:#fff1e8,stroke:#9c5b2b,color:#663b21,stroke-dasharray:5 4;
  classDef stop fill:#feece8,stroke:#9a433b,color:#6e2e29;
```

Defaults: **1 CPU**, **512 MB memory**, **512 MB disk**, **900-second lease**,
**60-second command deadline**, **1 MB per file**, **16 MB / 2,000 files per skill
projection**. Empty network domains mean isolated networking. The client checks
receipts and paths; only the approved remote service can enforce OS isolation.
The adapter does not provision a broker or certify an infrastructure provider.
Python tools, plugins, and stdio MCP processes still run in their configured host
environment; selecting a sandbox provider does not sandbox all Gateway code.

## Implementation map and maintenance

| Diagram | Source of truth |
| --- | --- |
| Registry and deployment | [registry.py](../extensions/enterprise/deerflow_enterprise/registry.py), [adapter.py](../extensions/enterprise/deerflow_enterprise/adapter.py), [operator CLI](../extensions/enterprise/deerflow_enterprise/__main__.py) |
| Request execution | [enterprise middleware](../extensions/enterprise/deerflow_enterprise/runtime.py), [lead agent](../backend/packages/harness/deerflow/agents/lead_agent/agent.py), [tool discovery](../backend/packages/harness/deerflow/tools/builtins/tool_search.py), [skill discovery](../backend/packages/harness/deerflow/skills/describe.py), [clarification](../backend/packages/harness/deerflow/agents/middlewares/clarification_middleware.py) |
| Skill publication | [workflow.py](../extensions/enterprise/deerflow_enterprise/workflow.py), [plugin/service](../extensions/enterprise/deerflow_enterprise/__init__.py), [browser action](../extensions/enterprise/deerflow_enterprise/static/index.mjs) |
| Recovery | [resilience.py](../extensions/enterprise/deerflow_enterprise/resilience.py) |
| Sandbox | [sandbox.py](../extensions/enterprise/deerflow_enterprise/sandbox.py) |

Keep this Markdown and its rendered SVGs synchronized when behavior changes.
[render_enterprise_flow.mjs](../scripts/render_enterprise_flow.mjs) rebuilds the
SVGs from these Mermaid blocks using a development-only renderer; the HTML viewer
and exported diagrams have no runtime dependencies. Follow the regeneration
instructions in that script. Do not hand-edit generated SVG node labels.

The enterprise package remains separately versioned. Upstream updates require
qualification of the extension API and host-specific config, review, provenance,
skill-storage, and sandbox interfaces; these diagrams do not promise a permanent
API guarantee. See [compatibility and verification](enterprise-services.md#upstream-compatibility-and-verification).
