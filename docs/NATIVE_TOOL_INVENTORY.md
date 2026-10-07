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
- `additionalWrites` lists exact observed generic write tools, including the connected GitHub Token Connection repository REST writer and comment writer. These routes remain separate from the native primitive map. Read their actual schemas with `selectToolSchemas`, probe the connected account and target permission, and preserve the publication route and provider outcome before using one. An entry records tool visibility, not authentication, authorization or a successful write.
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

## Scoped GitHub issue and PR searches

Check that search results actually match the requested selectors. On the
observed October 4 binding, `github_search_issues` returned plain issue URLs
and null state/timestamps for an `is:pr` query. Those results can supply leads;
they do not establish that the PR, state, visibility or ordering qualifiers
were applied. The approved native `github_fetch` search route returned the
GitHub JSON with PR markers, timestamps and explicit search coverage:

```js
const query = "is:pr is:open is:public author:woahwhattheheck review:changes_requested";
const response = await tools.mcp__codex_apps__github_fetch({
  url: "https://api.github.com/search/issues?q=" + encodeURIComponent(query)
    + "&sort=updated&order=desc&per_page=10"
});
store("github-pr-search-response", response);
const page = JSON.parse(response.structuredContent.content);
text({
  total_count: page.total_count,
  incomplete_results: page.incomplete_results,
  items: page.items.map(item => ({
    url: item.html_url,
    is_pr: Boolean(item.pull_request),
    state: item.state,
    updated_at: item.updated_at
  }))
});
```

Use the actual owner, repository and work selectors. Preserve the full response,
and inspect an error response before parsing it as a search page. This exact
example was executed successfully; its then-empty result is not a lasting
claim that no review work exists. `incomplete_results: false` does not make
the first page exhaustive when `total_count` exceeds the returned item count.
Continue with the same selectors and the next page when more coverage is needed.

Connected searches can include private repositories. When visibility matters,
include `is:public` as appropriate and read the target repository's current
`visibility` before changing attribution or publishing its content. Internal
attribution in a private record is not automatically an outward-publication
violation. Refresh the exact issue or PR before claiming work or editing a body;
search results can lag subsequent edits. The two search interfaces do not
provide separate quota: honor the existing provider cooldown instead of
switching routes to repeat a rate-limited request.

## Native Git publication without shell execution

A shell `409 environment_offline` result describes that execution environment;
it does not establish that connected GitHub reads or writes are unavailable.
When the native tools still work, use their actual schemas directly. Keep the
complete desired UTF-8 file contents and their original source responses in
code-mode state or an existing retrievable source packet. A local path, patch
summary, hash, or commit label cannot reconstruct missing file bytes. Recover
the exact source before writing if those strings are no longer available.

### Read the parent commit's actual tree

On the observed binding, `github_fetch_commit` normalizes commit metadata and
can omit the tree SHA. The approved `github_fetch` Git-data route returns the
provider JSON unchanged inside `structuredContent.content`:

```js
const repository = "OWNER/REPOSITORY";
const branchPath = "EXISTING/BRANCH".split("/").map(encodeURIComponent).join("/");
const refResponse = await tools.mcp__codex_apps__github_fetch({
  url: "https://api.github.com/repos/" + repository + "/git/ref/heads/" + branchPath
});
const parent = JSON.parse(refResponse.structuredContent.content).object.sha;
const commitResponse = await tools.mcp__codex_apps__github_fetch({
  url: "https://api.github.com/repos/" + repository + "/git/commits/" + parent
});
const baseTree = JSON.parse(commitResponse.structuredContent.content).tree.sha;
store("publication-base", { repository, parent, baseTree });
```

Replace the repository and existing branch with the actual target. A commit SHA
and its tree SHA are distinct objects; pass the returned `tree.sha` as the base
tree. For example, the [raw commit read](https://api.github.com/repos/woahwhattheheck/bounty-concierge/git/commits/0d77d025582531f74c2c6f1803fdd7efb4209bb6)
performed for this guide returned tree `22448cad0e53da0938a1e7009c3d04c77ab7e9b6`.
That is a historical observation, not the current branch head.

### Publish complete file contents

The full tool names below have prefix `mcp__codex_apps__github_`. Replace
uppercase values with actual target data and returned SHAs; `FULL_TEXT` means
the complete retained string, not a filesystem path. These are argument
objects, not a new client or automatic retry loop.

| Tool suffix | Supported arguments |
| --- | --- |
| `create_blob` | `{"repository_full_name":"OWNER/REPOSITORY","content":"FULL_TEXT","encoding":"utf-8"}` |
| `create_tree` | `{"repository_full_name":"OWNER/REPOSITORY","base_tree_sha":"BASE_TREE_SHA","tree_elements":[{"path":"docs/example.md","mode":"100644","type":"blob","sha":"BLOB_SHA"}]}` |
| `create_commit` | `{"repository_full_name":"OWNER/REPOSITORY","message":"Describe the source change","parent_sha":"PARENT_COMMIT_SHA","tree_sha":"NEW_TREE_SHA"}` |
| `update_ref` | `{"repository_full_name":"OWNER/REPOSITORY","branch_name":"EXISTING/BRANCH","sha":"NEW_COMMIT_SHA","expected_sha":"PARENT_COMMIT_SHA","force":false}` |

Create a blob for each complete file, collect their actual returned SHAs into
one tree, then create one commit with the observed parent. Supplying
`base_tree_sha` preserves paths outside those entries. Preserve the existing
file modes; `100644` above is a regular non-executable text file. These object
writes do not publish a branch change until `update_ref` succeeds.

Read the current ref and affected file preimages before advancing it. If another
writer changed the branch, compose the intended edits onto that current source
and tree. Do not replace a newer file with an old complete string. The current native
`update_ref` schema exposes optional `expected_sha` and rejects a mismatched
expected head. Inspect the actual schema in this harness, supply the observed
parent as `expected_sha` when supported, and keep `force:false`. By contrast,
a raw REST `PATCH /git/refs/heads/<branch>` with only `{sha, force:false}`
requires a fast-forward but does not provide expected-head compare-and-swap.
Retain a rejected or uncertain write's actual result and reconcile the branch
before another attempt.

For one existing UTF-8 file, the shorter Contents route is sufficient: read it
with `fetch_file`, then call `update_file` with
`{"repository_full_name":"OWNER/REPOSITORY","path":"docs/example.md","branch":"EXISTING/BRANCH","sha":"CURRENT_FILE_BLOB_SHA","content":"FULL_TEXT","message":"Describe the source change"}`.
The wrapper encodes the text; do not pre-encode it. Its `sha` is the current
file blob, not a commit or tree. Keep same-path updates sequential. The separate
`create_file` action accepts a new path on an existing branch.

Read the resulting branch or original PR head and the affected files at the
returned commit. Compare the complete content or expected blob SHAs and retain
the actual publication result. A local executor failure supplies no compilation
or runtime result; carry forward existing source-bound validation without
claiming a new run. Native tool availability also does not remove repository
permissions, publication decisions, response-size/UTF-8 limits on
`github_fetch`, or provider cooldowns. Keep existing author, branch, PR and
operation custody when continuing the same work.
