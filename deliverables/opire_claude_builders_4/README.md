# Claude PR Reviewer

A small Claude Code sub-agent for bounty #4. It accepts a real GitHub pull
request URL, downloads the PR metadata and unified diff, sends the diff to the
local authenticated Claude Code CLI, validates the model response, and prints a
deterministic Markdown review.

It does not replace the repository's bounty-board README and it does not
substitute hand-written heuristics for a Claude review.

## Setup

Requirements:

- Python 3.10+
- an installed and authenticated `claude` (Claude Code) CLI
- network access to GitHub
- optional `GITHUB_TOKEN`/`GH_TOKEN` for private repositories or higher API
  limits

Install from this directory:

```bash
python -m pip install .
```

That installs the requested command:

```bash
claude-review --pr https://github.com/owner/repo/pull/123
```

To use a non-default Claude executable or model:

```bash
claude-review \
  --pr https://github.com/owner/repo/pull/123 \
  --claude-bin /path/to/claude \
  --model sonnet
```

## Output contract

`claude-review` always renders these four sections after validating Claude's
JSON response:

```markdown
## Summary
Two or three sentences.

## Identified risks
- Concrete risk.

## Improvement suggestions
- Concrete suggestion.

## Confidence
Low
```

Confidence is restricted to `Low`, `Medium`, or `High`. A malformed or missing
field fails the command rather than publishing a half-structured review.

The prompt marks the entire diff as untrusted data and tells the reviewer not to
follow instructions found inside source code, comments, strings, or patch text.

## Large diffs and GitHub authentication

The default maximum diff size is 350,000 bytes. The command fails closed rather
than silently reviewing a truncated patch. Raise the ceiling deliberately with
`--max-diff-bytes` when needed.

For private PRs:

```bash
export GITHUB_TOKEN=...
claude-review --pr https://github.com/acme/private-repo/pull/42
```

The token is sent only in the GitHub API `Authorization` header. It is not
included in the Claude prompt.

## Real-PR sample outputs

The `samples/` directory contains structured review outputs grounded in two real,
merged GitHub PR diffs:

- `mova-store-359.md` - Movalabs-crew/mova-store#359
- `mova-store-361.md` - Movalabs-crew/mova-store#361

They exercise two distinct review shapes: a CI/Rust change with policy-sensitive
risk and a small React/test change with localized maintainability risks.

## Design notes

The production path is intentionally simple: GitHub diff -> untrusted-data prompt
-> Claude Code `--print` -> strict JSON validation -> deterministic Markdown.
Claude never receives the GitHub token. Shell interpolation is not used when
invoking the Claude executable.
