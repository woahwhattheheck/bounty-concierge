# Shared GitHub cooldown status

`concierge.github_cooldown_status` reads the existing shared GitHub cooldown
and reserved-headroom state without spending a GitHub request. It is an
observability surface only: it does not sleep, retry, schedule, reserve quota,
clear a deadline, or mutate provider state.

## Usage

```bash
python -m concierge.github_cooldown_status \
  --cooldown-file "$HOME/.cache/bounty-capture/github.sqlite" \
  --json
```

`--cooldown-file` defaults to `CONCIERGE_BOUNTY_COOLDOWN`. By default the
command selects the same credential scope as `GITHUB_TOKEN`. A worker that
stores its matching credential under a different environment variable can
select that variable by name:

```bash
python -m concierge.github_cooldown_status \
  --cooldown-file "$HOME/.cache/bounty-capture/github.sqlite" \
  --token-env SPONSOR_GITHUB_TOKEN
```

The command reports provider cooldown and proactive quota reservation
separately, plus the effective remaining wait. Expired rows remain visible with
`active=false` and zero remaining seconds. A missing file reports `ABSENT`
and is not created. An existing database is opened with SQLite `mode=ro`; the
status path never runs the worker's schema-initialization statements. An empty
or incomplete database is reported as unavailable rather than initialized. If
the file disappears between the initial observation and connection, the command
fails without recreating it. Normal worker reads and deadline updates retain
their existing behavior.

Output excludes credential values, scope fingerprints, the selected environment
variable name, and the private SQLite path. Existing malformed or unavailable
state fails closed with a generic diagnostic and exit status 2.

This is local coordination evidence, not a fresh GitHub quota reading. Separate
containers that do not share the same SQLite file remain independent.

The passive connection uses Python's documented
[read-only SQLite URI mode](https://docs.python.org/3.10/library/sqlite3.html).
