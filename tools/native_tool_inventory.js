"use strict";

// Pure inspection of the caller's current deferred-tool registry. No I/O.
// Exact namespace families observed across the supported ChatGPT connector harnesses.
// Accept the caller's actual names; do not synthesize registry entries or permissions.
const TOOL_PREFIXES = Object.freeze({
  github: Object.freeze([
    "mcp__codex_apps__github_",
    "mcp__GitHub__",
    "GitHub.",
  ]),
  slack: Object.freeze([
    "mcp__codex_apps__slack_slack_",
    "mcp__Slack__slack_",
    "Slack.slack_",
  ]),
});

const NATIVE_WRITE_ACTIONS = Object.freeze({
  github: Object.freeze([
    "create_blob", "create_tree", "create_commit", "create_branch",
    "update_ref", "create_file", "update_file", "create_pull_request",
    "merge_pull_request", "update_pull_request", "add_comment_to_issue",
    "update_issue_comment",
  ]),
  slack: Object.freeze([
    "send_message", "edit_message", "create_conversation",
  ]),
});

/** Return current observations, never inferred account or provider permission. */
function inspectToolInventory(registry, { includeNames = false } = {}) {
  if (!Array.isArray(registry)) {
    throw new TypeError("Tool registry must be an array of {name, description} entries");
  }
  const allNames = [...new Set(registry.map(entry => entry?.name)
    .filter(name => typeof name === "string"))].sort();
  const observed = new Set(allNames);
  const providers = {};
  for (const [provider, prefixes] of Object.entries(TOOL_PREFIXES)) {
    const names = allNames.filter(name => prefixes.some(candidate => name.startsWith(candidate)));
    const writes = {};
    for (const action of NATIVE_WRITE_ACTIONS[provider]) {
      const name = prefixes.map(candidate => candidate + action)
        .find(candidate => observed.has(candidate));
      writes[action] = name
        ? { state: "OBSERVED", name }
        : { state: "NOT_OBSERVED_IN_THIS_SNAPSHOT" };
    }
    providers[provider] = {
      count: names.length,
      writes,
      ...(includeNames ? { names } : {}),
    };
  }
  const discovery = allNames.filter(name =>
    /(?:^|__)(?:[^_]+__)?(?:tool_search|search_tools|list_resources)$/.test(name)
      || name === "mcp__codex_apps__plugin_management_search_plugins"
      || name === "mcp__Plugin_Management__search_plugins"
      || name === "api_tool.list_resources"
  );
  return {
    registryCount: allNames.length,
    relevantCount: providers.github.count + providers.slack.count,
    providers,
    discovery,
    authorization: "NOT_PROBED_BY_THIS_INSPECTOR",
    scope: "CURRENT_REGISTRY_SNAPSHOT_ONLY",
  };
}

/** Read only exact observed schemas requested by the caller. */
function selectToolSchemas(registry, names) {
  if (!Array.isArray(registry) || !Array.isArray(names)
      || names.some(name => typeof name !== "string")) {
    throw new TypeError("Expected a registry array and an array of exact tool names");
  }
  const entries = new Map();
  for (const entry of registry) {
    if (typeof entry?.name === "string" && !entries.has(entry.name)) {
      entries.set(entry.name, entry);
    }
  }
  return [...new Set(names)].map(name => {
    const entry = entries.get(name);
    if (!entry) return { name, state: "NOT_OBSERVED_IN_THIS_SNAPSHOT" };
    return {
      name,
      state: "OBSERVED",
      description: typeof entry.description === "string" ? entry.description : "",
    };
  });
}

// Copy the source above into a code-mode cell, or require it from Node.js.
if (typeof module !== "undefined" && module.exports) {
  module.exports = { inspectToolInventory, selectToolSchemas };
}
