# Local CLI plans

`browse`, `faq`, `engage`, `announce`, `mine`, `wallet migrate`, and `claim` turn `--dry-run` into an argument-only plan. These handlers return before bounty collection, FAQ lookup, engagement actions, process/service detection, node RPC, Discord/SSH/database reads, migration-history reads, transfers, debits, or miner launch. They do not inspect the selected log file or miner executable. Normal package bootstrap and authority controls remain in place; this is not an alternate runtime or a way to execute without live checks.

```sh
concierge browse --repo rustchain-bounties --skill python --limit 5 --dry-run --report
concierge faq "How do I claim a bounty?" --dry-run --json
concierge engage --star-repos --dry-run --json
concierge engage --devto --dry-run --json
concierge engage --saascity --dry-run --json
concierge announce --dry-run --json
concierge mine --pow warthog --detect-only --dry-run --json
concierge mine --pow warthog --wallet EXAMPLE_ADDRESS --dry-run --json
concierge mine --pow warthog --wallet EXAMPLE_ADDRESS --miner janusminer --dry-run --json
concierge wallet migrate --history --dry-run --json
concierge wallet migrate --list --min-balance 1 --dry-run --json
concierge wallet migrate --user EXAMPLE_DISCORD_ID --to example-wallet --dry-run --json
concierge claim --repo rustchain-bounties --issue 1 --wallet example-wallet --dry-run --json
```

Common flags also work before the command, such as `concierge --dry-run --json announce`. Successful JSON output is one object with `mode`, `action`, `inputs`, `steps`, `unknown`, and `note`. Without `--json` or browse's `--report`, a short dry-run heading precedes the same plan. Invalid required arguments produce an error instead of a success-shaped plan.

For `browse`, `--report` also selects a single JSON plan during dry-run, even without `--json`. The plan retains filters, display limit, source-page budget, and normalized repository names; row counts and source completeness remain unknown. FAQ plans retain the question and `--grok` choice without looking up or generating an answer. Engagement plans list only configured targets, with star/upvote results and article statistics unknown. When several engagement flags are supplied, the existing star-repos, devto, then saascity precedence applies.

The `claim.instructions` plan includes the normalized repository, issue number, issue URL, and supplied wallet after local wallet-name validation. Bounty availability, claim eligibility, assignment, and payment stay unknown. It neither posts a claim nor looks up the target; ordinary live claim prerequisites still apply when `--dry-run` is omitted.

Inputs come only from arguments and static configuration. `unknown` values are JSON `null`: no balance, bonus, source completeness, history, availability, verification, or successful migration is inferred. A mining command is an argv preview, not a launched process. A resolved pool preset is configuration, not a verified account or reachable server; Janusminer still targets the local node shown in its argv. Migration plans validate the target wallet name without checking that the wallet exists. History takes precedence over list, which takes precedence over user migration, just as in the live handler. `--force` is recorded as an input, not a claim of authorization or a performed migration.

`announce --dry-run` no longer fetches content. For an actual preview from a retained index or browse report, use the existing saved-snapshot formatter:

```sh
python -m concierge.announcer --index PATH --format long
```

That formatter preserves saved-source age and coverage. An old snapshot is not a live bounty check. Ordinary `announce` still fetches and formats preview text; its CLI handler does not post it. Omitting `--dry-run` from mining or migration retains the existing live validations and actions. This plan format applies to the commands above; other CLI subcommands keep their existing output contracts.
