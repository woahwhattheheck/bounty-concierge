# Native GitHub and Slack tool inventory

Inspect the **current** deferred-tool registry without printing every plugin's description or calling a provider. This helper is for the code-mode `ALL_TOOLS` shape whose names begin with `mcp__codex_apps__github_` and `mcp__codex_apps__slack_slack_`.

## Use in a code-mode cell

Read [the inspector](../tools/native_tool_inventory.js) once and place its source in the cell before these calls:

```js
text(inspectToolInventory(ALL_TOOLS));
```

For a complete exact-name inventory, including read actions:

```js
text(inspectToolInventory(ALL_TOOLS, { includeNames: true }));
```

Read just the schemas needed for the next operation:

```js
text(selectToolSchemas(ALL_TOOLS, [
  "mcp__codex_apps__github_create_tree",
  "mcp__codex_apps__slack_slack_send_message"
]));
```

The functions also work in Node.js with `require("./tools/native_tool_inventory.js")` and an array of registry entries. They perform no I/O; the caller decides when to inspect or print.

## What the result means

- `OBSERVED` means the exact tool name appears now.
- `NOT_OBSERVED_IN_THIS_SNAPSHOT` does **not** mean the provider lacks the capability. Repeat discovery alongside useful work and use any discovered plugin/tool discovery mechanism.
- The writer map includes Git objects, branches, files, PR creation/update/merge, issue comments, Slack sends/edits, and conversation creation.
- Counts are observations, not a 127-tool permission gate. Future larger or smaller registries still report their observed names.
- Authentication, installation scope, repository permission, provider policy and operation outcomes remain separate facts. The inspector always reports `NOT_PROBED_BY_THIS_INSPECTOR`; a registry entry cannot establish authorization.
- The inspector does not cache capabilities, invoke writes, retry failures, create schedules, or change sessions. A subsequent call examines the newly supplied registry.
- A differently named harness requires its actual prefixes to be adapted deliberately. Do not infer absence from unmatched naming conventions.

Before a capability claim, use the observed harmless profile/workspace and target-permission reads as appropriate. After an operation fails, retain its exact provider error and reconcile whether a write occurred before deciding to retry. A discovery failure is not an operation failure.

## Measured output cost

One execution used the actual current registry on October 4, 2026. The baseline repeated a broad `github|slack|discover|tool.?search|list_resources|plugin` match over each name **and description**, then printed each matched name and its first 500 description characters. The inspector instead selected the exact current GitHub/Slack namespaces.

| Result | Entries | Serialized UTF-8 bytes |
| --- | ---: | ---: |
| Broad description match | 1160 | 659,255 |
| Inspector summary and writer map | 127 relevant tools | 1,598 |
| Inspector with all exact names | 127 relevant tools | 7,593 |

The current snapshot contained 1181 total tools, 89 GitHub tools and 38 Slack tools. All 15 tracked writer actions were observed. The summary used **99.76% fewer serialized bytes** than the broad baseline. Exact schema selection returned only the requested `github_create_tree` schema. No provider calls were made by this execution.

These are measured output sizes for this registry, not token counts, wall-clock speedups, provider-rate savings, authentication evidence, or a claim that another session exposes the same tools. Raw numeric receipt: [results.json](../work/throughput/native-inventory-20261004/results.json).

## Scoped Slack intake

For a new work search on the observed native binding, supply the supported `query` argument alongside `keywords` and `filters`, and request matched messages without surrounding-thread expansion:

```js
const response = await tools.mcp__codex_apps__slack_slack_search_public_and_private({
  query: '"PAYD630" after:2026-10-03',
  keywords: ["PAYD630"],
  filters: "after:2026-10-03",
  include_context: false,
  response_format: "detailed",
  sort: "timestamp",
  sort_dir: "desc",
  limit: 6
});
store("payd630-intake-response", response);
```

The date illustrates an October 4 intake using the previous calendar day in the workspace's timezone. Use the actual subject and intended date range; older ownership may require a wider range. Keep an existing cursor chain's original arguments unchanged.

On this binding, a keywords-only call rendered a blank search-query heading and returned unrelated current messages. Supplying the explicit query returned the named subject. This observation was already recorded in [the existing search-use thread](https://tokenjunkielabs.slack.com/archives/C0BU51F1PL3/p1791096886427149) and reproduced during this intake. A heading alone does not prove that every selector was applied: inspect the actual matching messages and current source before inferring ownership. An empty same-day search also does not establish an unclaimed scope; see [the existing date correction](https://tokenjunkielabs.slack.com/archives/C0BU51F1PL3/p1791106180715819).

Retain the complete response and print a bounded view. The existing Commons reader, `host/connected_slack_pages.cjs`, provides `collectSlackPages` and `projectSlackSearchResults`; its usage guide is `host/CONNECTED_SLACK_PAGES.md`. It defaults fresh searches to `include_context: false` and can project an already-retained response without another provider call. Use that implementation rather than creating another collector or replaying a search just to read fewer characters.

This is a usage correction for observed search scoping and response volume. It changes no provider access, claim ownership, cooldown policy or publication permission.
