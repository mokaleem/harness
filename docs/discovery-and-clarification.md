# Bounded discovery and clarification

The setup template enables deferred discovery for skills and tools. Standard
Gateway lead, bootstrap, embedded client, and native subagent assembly limits
catalog previews, discovery responses, and active deferred schemas.

## Configuration

Merge these sections into root `config.yaml`, preserving existing model,
sandbox, tools, and skill paths:

```yaml
tool_search:
  enabled: true
  auto_promote_top_k: 3
  max_prompt_names: 50
  max_search_results: 5
  max_active_tools: 20

skills:
  container_path: /mnt/skills
  deferred_discovery: true
  max_prompt_names: 50
  max_search_results: 5
```

Fresh setups inherit this from template version 51. Existing files are not
rewritten automatically; omitted enablement flags retain the previous disabled
defaults in the Python models. Restart your local Gateway after changing
configuration. Embedded clients must rebuild with `reset_agent()` or a new client.

| Setting | Default | Accepted values | Effect |
| --- | --- | --- | --- |
| `max_prompt_names` | 50 | 1–500 | Caps names per catalog preview; also caps MCP routing hints. |
| `max_search_results` | 5 | 1–20 | Caps discovery results, including exact `select:` requests. Ranked searches currently return at most five matches. |
| `tool_search.max_active_tools` | 20 | 1–100 | Caps recently promoted deferred schemas bound per model call. |
| `tool_search.auto_promote_top_k` | 3 | Clamped to 1–5 | Caps MCP schemas promoted automatically from routing metadata. |

`max_active_tools` must cover `max_search_results + auto_promote_top_k`, reserving
room for the last explicit search alongside automatic routing. Skill and tool
limits are independent.

## Discovery flow

1. The agent receives an alphabetically sorted, bounded catalog preview. This
   preview is not relevance ranking. A notice explains that omitted names remain
   searchable.
2. `describe_skill` searches all permitted enabled skills and returns matching
   descriptions and locations. The agent reads relevant `SKILL.md` instructions
   on demand. Metadata discovery alone does not activate tool policy or secrets.
3. `tool_search` searches deferred tools and promotes matching schemas. Responses
   remain JSON schema arrays for compatibility with active-skill policy filtering.
   Its description tells the model the limit and to narrow queries for more results.
4. A model call receives at most `max_active_tools` promoted deferred schemas.
   Searching an older tool refreshes its recency; older schemas leave the active
   window and can be rediscovered. Catalog hashes invalidate stale promotions.
5. Existing authorization and skill-policy checks still apply. Discovery does
   not grant additional permissions.

MCP tools from internal and external servers participate automatically when
tool search is enabled. Python tools remain eager unless configured to opt in:

```yaml
tools:
  - name: query_site_kpis
    group: telecom
    use: company.telecom_tools:query_site_kpis
    defer_loading: true
```

The module above is a placeholder for your installed trusted tool provider.
Deferral clones the tool without changing its source attribution or execution
implementation. Disabling tool search restores eager schemas.

Standalone catalogs and discovery builders retain uncapped exact selection
unless supplied `max_results`. Standard assembly supplies the configuration.
Direct graph integrations must pass these limits and install
`DeferredToolFilterMiddleware(max_active_tools=...)` themselves.

These limits count entries, not total tokens. Core eager tools, individual
schemas, selected skill bodies, and history still consume context. Existing
tool-output budgeting and summarization address results and history. This change
uses local discovery; it does not add Typesense or an enterprise registry.

## Clarification already exists

Interactive lead agents expose `ask_clarification`, include clarification
instructions in their prompt, and install `ClarificationMiddleware`. The frontend
already renders Human Input Cards for free text, choices, and structured forms.

For **“Show Dallas site performance”**, the agent can request the KPI and reporting
period instead of inventing them. Once the model requests clarification:

1. Sibling tool calls in that response are dropped, preventing a premature query.
2. A Human Input Card plus readable text fallback is returned.
3. The current graph invocation ends; the user's reply starts the next invocation
   on the same thread. Gateway persistence retains the conversation. Direct
   embedded graphs need a checkpointer for state across turns.

The model decides when clarification is needed. The middleware enforces the pause
after that decision; it does not guarantee detection of every ambiguous prompt.
Validate your chosen model with representative telecom prompts.

Scheduled, autonomous, webhook, and native subagent runs do not wait for a
synchronous human. Their interaction policies handle assumptions or blocking
conditions; a subagent reports missing information to its lead.

## Verification

Offline tests use synthetic catalogs of 500 skills and 500 tools. Compiled graphs
verify schema rotation and rediscovery, plus a telecom clarification form that
blocks a query and continues after a reply. No model provider or database is called.

From `backend/`, in PowerShell or a macOS terminal:

```powershell
uv run --no-sync python -m pytest tests/test_bounded_discovery.py tests/test_clarification_middleware.py tests/test_deferred_promotion_integration.py -q
```
