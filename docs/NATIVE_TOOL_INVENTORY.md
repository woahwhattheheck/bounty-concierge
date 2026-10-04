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

