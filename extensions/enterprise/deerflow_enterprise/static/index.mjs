const example = {
  name: "site-health", description: "Read network site health for a selected day.",
  instructions: "Ask for the day. Use the approved database tool with query.sql. Explain missing evidence and cite table definitions.",
  files: { "query.sql": "SELECT site_id FROM approved_dataset.site_health WHERE day = @day" },
  parameters: { type: "object", properties: { day: { type: "string", format: "date" } }, required: ["day"], additionalProperties: false },
  examples: [{ day: "2026-10-01" }], tables: [], dependencies: {}
};

function mount(root, context) {
  const make = (tag, text = "", attrs = {}) => {
    const element = document.createElement(tag);
    element.textContent = text;
    for (const [name, value] of Object.entries(attrs)) element.setAttribute(name, value);
    return element;
  };
  root.append(make("link", "", { rel: "stylesheet", href: new URL("./styles.css", import.meta.url).href }));
  root.append(make("h2", "Enterprise capabilities"), make("p", "Capture evidence with Create a skill in a conversation. Parameterize the recipe, validate it, obtain independent review, then publish a version. Activation and deployment apply the release to the harness."));
  const status = make("p", "", { role: "status", "aria-live": "polite" });
  const error = make("p", "", { role: "alert" });
  const drafts = make("section"), catalog = make("section"), failures = make("section");
  root.append(status, error, drafts, catalog, failures);
  let disposed = false;
  const active = () => !disposed && !context.signal.aborted;
  const button = (parent, label, operation) => {
    const element = make("button", label, { type: "button" });
    element.addEventListener("click", async () => {
      element.disabled = true; error.textContent = "";
      try { await operation(); }
      catch { if (active()) error.textContent = "Could not complete this step. Check permissions, input, validation, and current review status."; }
      finally { if (active()) element.disabled = false; }
    }, { signal: context.signal });
    parent.append(element); return element;
  };
  const field = (parent, label, value, multiline = false) => {
    const id = `enterprise-${crypto.randomUUID()}`;
    const input = make(multiline ? "textarea" : "input", "", { id }); input.value = value;
    parent.append(make("label", label, { for: id }), input); return input;
  };
  async function load() {
    const [viewer, draftData, capabilityData, failureData] = await Promise.all([
      context.callBackend("status", {}), context.callBackend("drafts", { limit: 50 }),
      context.callBackend("capabilities", { limit: 100 }), context.callBackend("failures", { limit: 20 })
    ]);
    if (!active()) return;
    status.textContent = `Team: ${viewer.team}. Showing up to 50 drafts, 100 active capabilities, and 20 recent dependency failures.`;
    drafts.replaceChildren(make("h3", "Skill releases"));
    if (!draftData.items.length) drafts.append(make("p", "No drafts. Use Create a skill from a completed conversation."));
    for (const draft of draftData.items) {
      const card = make("article"); card.append(make("h3", `${draft.candidate?.name ?? "New recipe"} · ${draft.state}`));
      card.append(make("p", `Owner: ${draft.owner}. Evidence: ${draft.evidence.events.length} receipts. Expires: ${new Date(draft.expires * 1000).toLocaleString()}.`));
      const evidence = make("details"); evidence.append(make("summary", "Evidence references and hashes"), make("pre", JSON.stringify(draft.evidence, null, 2))); card.append(evidence);
      if (draft.state === "published") { card.append(make("pre", `${draft.release.id}@${draft.release.version}\n${draft.release.digest}`)); drafts.append(card); continue; }
      const candidate = field(card, "Recipe (parameters, example inputs, table DDL, and query resources)", JSON.stringify(draft.candidate ?? example, null, 2), true);
      button(card, "Save parameters", async () => { await context.callBackend("parameterize", { id: draft.id, candidate: JSON.parse(candidate.value) }); await load(); });
      button(card, "Validate", async () => { await context.callBackend("validate", { id: draft.id }); await load(); });
      if (draft.validation) card.append(make("pre", JSON.stringify(draft.validation, null, 2)));
      if (viewer.is_admin && viewer.user_id !== draft.owner && draft.validation?.valid) {
        const notes = field(card, "Review notes (confirm table scope, parameterization, and expected behavior)", "");
        button(card, "Approve exact revision", async () => { await context.callBackend("review", { id: draft.id, digest: draft.validation.digest, approve: true, notes: notes.value }); await load(); });
        button(card, "Request changes", async () => { await context.callBackend("review", { id: draft.id, digest: draft.validation.digest, approve: false, notes: notes.value }); await load(); });
      }
      if (draft.review) card.append(make("pre", JSON.stringify(draft.review, null, 2)));
      if (draft.review?.approved) {
        const id = field(card, "Capability ID", `${draft.team}.${draft.candidate.name}`);
        const version = field(card, "Immutable version", "1.0.0");
        button(card, "Publish version", async () => { await context.callBackend("publish", { id: draft.id, capability_id: id.value, version: version.value }); await load(); });
      }
      drafts.append(card);
    }
    catalog.replaceChildren(make("h3", "Active registry"));
    for (const item of capabilityData.items) catalog.append(make("p", `${item.kind} · ${item.origin} · ${item.id}@${item.version} — ${item.description}`));
    if (viewer.is_admin) {
      const member = field(catalog, "Authenticated user ID for team membership", "");
      button(catalog, "Add team member", async () => { await context.callBackend("membership", { user_id: member.value, enabled: true }); await load(); });
      button(catalog, "Remove team member", async () => { await context.callBackend("membership", { user_id: member.value, enabled: false }); await load(); });
      const definition = field(catalog, "Register a capability (JSON; immutable ID/version)", JSON.stringify({ id: `${viewer.team}.example`, version: "1.0.0", kind: "tool", origin: "internal", team: viewer.team, name: "example", description: "Replace with an approved capability", definition: { use: "company.tools:example", group: viewer.team }, dependencies: {} }, null, 2), true);
      button(catalog, "Register version", async () => { await context.callBackend("register", { capability: JSON.parse(definition.value) }); await load(); });
      const id = field(catalog, "Capability ID to activate", ""); const version = field(catalog, "Version", "1.0.0");
      button(catalog, "Activate desired version", async () => { await context.callBackend("activate", { id: id.value, version: version.value }); await load(); });
      button(catalog, "Deactivate", async () => { await context.callBackend("deactivate", { id: id.value }); await load(); });
    }
    failures.replaceChildren(make("h3", "Dependency escalation queue"));
    failures.append(make("pre", JSON.stringify(failureData.items, null, 2)));
  }
  button(root, "Refresh", load);
  load().catch(() => { if (active()) error.textContent = "Enterprise services unavailable. Check deployment configuration."; });
  return { dispose() { disposed = true; root.replaceChildren(); } };
}

export default {
  apiVersion: 1, module: "enterprise.v1", icon: "layers",
  surfaces: [{ id: "releases", slot: "page", title: "Enterprise capabilities", navigation: { label: "Skill releases", icon: "layers" }, mount }],
  conversationActions() {
    return { label: "Reusable skills", icon: "layers", actions: [{
      id: "create-skill", label: "Create a skill", icon: "plus", available: settings => settings.enabled === true,
      async execute(context, services) {
        await services.callBackend("capture", { thread_id: context.thread.thread_id });
        services.showMessage("Evidence captured. Open Skill releases in the sidebar to parameterize, validate, review, and publish the draft.");
      }
    }] };
  }
};
