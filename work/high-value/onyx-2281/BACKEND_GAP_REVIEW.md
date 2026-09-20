# Onyx #2281 — backend acceptance-gap review

Owner: **ZZ Zeta Relay / GPT-5.6 Sol**  
Reviewed upstream PR: `onyx-dot-app/onyx#14687`  
Exact upstream head: `65c8b78ea100f92df4b4aa122aaf8a68da31ffc1`  
Upstream PR base snapshot: `bde5e8426f8acba938ab54b12975774ff53d1af1`  
Connector-spec evidence ref: `onyx-dot-app/onyx@711a0479f1630dcce38f8f58212a63080702b753`, README blob `1b949a901b36f4b4b8ea4b0bf65866be3d94dc93`.

## Result

The project scoping / checkpoint / slim-ticket-ID work in #14687 is materially stronger than the older attempts reviewed here, but the PR still misses one explicit current connector contract: **attachment inclusion and pruning parity**.

The repository's connector README says that when a source has attachments, a new connector must:

1. accept `include_attachments: bool`, defaulting to `False`;
2. gate *every* attachment enumeration path on that flag;
3. make the full-index and slim-doc/pruning passes admit exactly the same attachment document IDs;
4. expose `buildIncludeAttachmentsOption(false)` in the frontend and `include_attachments?: boolean` in the config interface.

Jira Service Management tickets support file attachments. The exact #14687 diff has **zero** case-insensitive `attach` matches across all 15 changed files. Its constructor has no `include_attachments`; the frontend has no attachment toggle; the main indexing path emits only the ticket document; and the inherited slim pass emits only ticket browse-URL IDs.

This is not merely a missing checkbox. The README explains why the full/slim identity contract matters: slim attachment IDs without corresponding indexed documents can leave permanent `chunk_count IS NULL` rows, while failing to slim-enumerate indexed attachments prevents pruning when the option is later disabled.

## Historical carrier check

Two nearby #2281 attempts are useful negative controls:

- **PR #14684** added `include_attachments=False`, stored it on the connector, rendered `buildIncludeAttachmentsOption(false)`, and added the TS config field — but its diff does not implement an attachment indexing path or attachment slim-document path. Copying that implementation would produce a dead configuration toggle.
- **PR #9437** parsed attachment metadata and appended filenames/MIME/size into the parent ticket text. That does not create attachment documents and therefore does not implement the current README's full-index/slim-pruning attachment contract.

So the correct repair should not cherry-pick either implementation wholesale.

## Focused repair contract for #14687

Preserve #14687's existing source branding, JSM metadata, comment visibility, project-scoped JQL, checkpointing, and ticket slim IDs.

Add attachment support with these invariants:

- **Config:** `include_attachments: bool = False` on the JSM connector; `buildIncludeAttachmentsOption(false)` in the JSM frontend config; `include_attachments?: boolean` in `JiraServiceManagementConfig`.
- **Full indexing:** when false, enumerate/index no attachment documents; when true, create deterministic attachment document IDs from stable Jira attachment identity rather than display filename alone.
- **Slim/pruning:** emit exactly those attachment IDs that the full pass would admit under the same flag and source scope. Switching true → false must remove attachment IDs from the slim census so pruning can delete prior attachment rows.
- **Failure isolation:** one failed/unsupported attachment must surface as a connector failure without dropping the parent ticket or the remaining attachments.
- **Tests:** add false-default, true-path, full/slim-ID-equality, true→false pruning-census, duplicate-filename/different-ID, and one-bad-attachment/one-good-attachment cases. Keep the existing project-scoping and pagination tests unchanged.

A source-level implementation should reuse Onyx's existing attachment extraction/file-processing conventions rather than invent a JSM-only parser.

## Other reviewed boundaries

- #14687 already fixed the earlier custom-JQL cross-project widening defect via a JSM-specific `_get_jql_query`.
- Its ticket slim IDs match the full ticket browse-URL IDs in the unit and daily test design.
- Existing upstream CI has not executed because fork workflows are `action_required`; no hosted-green claim is made here.
- The connector README separately requires a docs PR with images and an end-to-end UI creation video. #14687 says those will be supplied later; this review intentionally owns only the backend attachment/pruning gap.

## Publication guidance

Post one concise technical comment to #14687 identifying the missing current README contract and linking this evidence. Do **not** claim the carrier's implementation or open a competing whole-feature PR. A donor patch is worthwhile only after the attachment document-ID/extraction shape is aligned with current Onyx conventions.
