# SPDX-License-Identifier: MIT
"""Command-line interface for the RustChain Bounty Concierge.

Subcommands:
    browse   -- List and filter open bounties across all repos
    bountyhub -- Read the public BountyHub catalog and export an intake shortlist
    capture-batch -- Capture a bounded issue shortlist for offline routing
    capture-recover -- Recover an interrupted batch from saved capture files
    faq      -- Ask a question about RustChain or bounties
    wallet   -- Register a wallet or check balance
    status   -- Check pending payouts for a wallet
    mine     -- PoW dual-mining helpers (Warthog/Janushash)
    engage   -- Cross-platform engagement (star repos, Dev.to stats)
    announce -- Preview or post bounty announcements
    claim    -- Show claim instructions for a specific bounty
    version  -- Print version string
"""

import argparse
import json
import math
import sys

from concierge import __version__
from concierge import config
from concierge import pow_miners
from concierge.bounty_index import aggregate, fetch_bounties, fetch_bounties_report
from concierge.reward_evidence import (
    _sanitize_single_line,
    reward_context,
    reward_filter_value,
    reward_sort_key,
    reward_summary,
)
from concierge.faq_engine import answer as faq_answer
from concierge.wallet_helper import (
    check_wallet_exists,
    get_active_miners,
    get_all_holders,
    get_balance,
    get_epoch_info,
    get_holder_stats,
    get_pending_transfers,
    register_wallet_guide,
    validate_wallet_name,
)
from concierge.payout_tracker import check_status, format_payout_status
from concierge.skill_matcher import recommend
from concierge.migration_journal import get_attempt, prepare_migration
from concierge.wallet_migration import continue_migration
from concierge.discord_bridge import (
    already_migrated,
    get_discord_balance,
    get_migration_history,
    list_discord_holders,
)

# Optional modules -- degrade gracefully if missing or broken.
try:
    from concierge.engagement import (
        star_all_ecosystem_repos,
        check_devto_articles,
        DevtoLookupError,
        saascity_upvote,
        SaaSCityError,
        SAASCITY_API_BASE,
        SAASCITY_LISTINGS,
    )
except ImportError:
    star_all_ecosystem_repos = None
    check_devto_articles = None
    DevtoLookupError = None
    saascity_upvote = None
    SaaSCityError = None
    SAASCITY_API_BASE = None
    SAASCITY_LISTINGS = {}

try:
    from concierge.announcer import format_announcement
except ImportError:
    format_announcement = None


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _print_json(obj):
    """Pretty-print a Python object as JSON."""
    print(json.dumps(obj, indent=2, default=str))


def _print_dry_run_plan(args, action, inputs, steps, unknown, *, json_output=False):
    """Render an argument-only plan, never a fabricated provider observation."""
    plan = {
        "mode": "dry-run",
        "action": action,
        "inputs": inputs,
        "steps": steps,
        "unknown": {name: None for name in unknown},
        "note": "Local plan only. No handler reads or actions performed; live checks remain required.",
    }
    if not (args.json or json_output):
        print(f"[dry-run] {action}; null values are unknown, not zero or success.")
    _print_json(plan)


def _truncate(text, width):
    """Truncate text to width, adding '...' if needed."""
    if len(text) <= width:
        return text
    return text[: width - 3] + "..."


def _print_bounty_table(bounties, show_evidence=False):
    """Print source mentions without rounding them or calling unknown zero."""
    if not bounties:
        print("No bounty rows displayed for these filters and display limit.")
        return

    # Amounts may exceed their minimum width; do not truncate exact evidence.
    w_num = 5
    w_repo = 22
    w_title = 42
    w_rtc = 32
    w_tier = 10
    w_skills = 28

    header = (
        f"{'#':<{w_num}} "
        f"{'Repo':<{w_repo}} "
        f"{'Title':<{w_title}} "
        f"{'Indexed amount':<{w_rtc}} "
        f"{'Tier':<{w_tier}} "
        f"{'Skills':<{w_skills}}"
    )
    sep = "-" * len(header)

    print(sep)
    print(header)
    print(sep)

    for b in bounties:
        repo_short = _sanitize_single_line(b["repo"].split("/")[-1])
        title = _sanitize_single_line(b["title"])
        skills_str = _sanitize_single_line(", ".join(b["skills"])) if b["skills"] else "-"
        tier = _sanitize_single_line(b["difficulty"])
        print(
            f"{b['number']:<{w_num}} "
            f"{_truncate(repo_short, w_repo):<{w_repo}} "
            f"{_truncate(title, w_title):<{w_title}} "
            f"{reward_summary(b):<{w_rtc}} "
            f"{tier:<{w_tier}} "
            f"{_truncate(skills_str, w_skills):<{w_skills}}"
        )
        if show_evidence:
            print("  Source: " + _sanitize_single_line(b.get("url") or "not supplied"))
            print("  " + reward_context(b))

    print(sep)
    print(f"Total: {len(bounties)} displayed bounty candidates")
    print(
        "Amounts are unconfirmed source mentions, possibly pools or caps, not per-claim pay. "
        "Unknown amounts are not zero. Use --evidence for excerpts and source links."
    )


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def _cmd_bounty_intake(args):
    """Delegate intake flags, output and exit status to the shared modules."""
    module_args = list(args.intake_args)
    # The catalog always emits JSON; capture modules have an explicit flag.
    if args.json and args.command in {"capture-batch", "capture-recover"}:
        module_args.insert(0, "--json")
    if args.dry_run and not any(flag in module_args for flag in ("-h", "--help")):
        if args.command == "bountyhub":
            steps = ["Read the public BountyHub catalog using the requested source bounds",
                     "Return the reader's catalog report or intake shortlist"]
            unknown = ["catalog_rows", "source_completeness", "claim_eligibility"]
        elif args.command == "capture-recover":
            steps = ["Read the saved shortlist and completed capture files",
                     "Rebuild offline supply, summary and remaining shortlist files"]
            unknown = ["captured_count", "remaining_count", "dispositions"]
        else:
            steps = ["Read the requested issue shortlist",
                     "Collect GitHub preflight evidence within the request budget",
                     "Save completed captures and an explicit remaining shortlist"]
            unknown = ["captured_count", "remaining_count", "request_count", "dispositions"]
        _print_dry_run_plan(
            args,
            f"{args.command}.preview",
            {"arguments": module_args},
            steps,
            unknown,
        )
        return

    if args.command == "bountyhub":
        from concierge.bountyhub_catalog import main as intake_main
    elif args.command == "capture-recover":
        from concierge.bounty_capture_recover import main as intake_main
    else:
        from concierge.bounty_capture_batch import main as intake_main

    sys.exit(intake_main(module_args))


def _browse_repos(args):
    """Resolve browse --repo values the same way the existing command did."""
    if not args.repo:
        return None
    repos = []
    for name in args.repo:
        if "/" not in name:
            name = f"Scottcjn/{name}"
        repos.append(name)
    return repos


def _filter_bounties(bounties, args):
    """Filter numeric RTC mentions, keeping unknown distinct from explicit zero."""
    filtered = list(bounties)
    if args.skill:
        skill_lower = args.skill.lower()
        filtered = [b for b in filtered if skill_lower in [s.lower() for s in b["skills"]]]
    if args.tier:
        tier_lower = args.tier.lower()
        filtered = [b for b in filtered if b["difficulty"] == tier_lower]
    if args.min_rtc is not None or args.max_rtc is not None:
        bounded = []
        for bounty in filtered:
            amount = reward_filter_value(bounty)
            if amount is None:
                continue
            if args.min_rtc is not None and amount < args.min_rtc:
                continue
            if args.max_rtc is not None and amount > args.max_rtc:
                continue
            bounded.append(bounty)
        filtered = bounded
    filtered.sort(key=reward_sort_key, reverse=True)
    return filtered


def _incomplete_browse_message(report):
    """Describe an incomplete read without calling it an empty queue."""
    failed = [row for row in report["repositories"] if row["status"] != "COMPLETE"]
    detail = "; ".join(f"{row['repo']}: {row['status']}" for row in failed[:6])
    if len(failed) > 6:
        detail += f"; {len(failed) - 6} more sources incomplete"
    return (
        f"PARTIAL {report.get('total_count', len(report['bounties']))} collected rows. "
        f"Source read is incomplete, so this is not an empty queue. {detail}."
    )


def _print_browse_sources(report):
    """Print per-source completion. Text only; never mixed into JSON."""
    print(f"Collection {report['started_at']} .. {report['updated_at']}")
    if report.get("rate_limited"):
        print(
            "rate_limited retry_after_seconds="
            f"{report.get('retry_after_seconds')} "
            f"rate_limit_reset_at={report.get('rate_limit_reset_at')}"
        )
    for row in report["repositories"]:
        print(
            f"source {row['repo']} status={row['status']} "
            f"pages={row['pages_fetched']} rows={row['bounty_count']} "
            f"http={row['http_status']}"
        )


def _browse_report_payload(report, filtered, displayed, limit):
    """Structured browse report. Row limit is display-only."""
    return {
        "rows": displayed,
        "displayed_count": len(displayed),
        "filtered_count": len(filtered),
        "collected_count": report["total_count"],
        "complete": report["complete"],
        "started_at": report["started_at"],
        "updated_at": report["updated_at"],
        "rate_limited": report["rate_limited"],
        "retry_after_seconds": report["retry_after_seconds"],
        "rate_limit_reset_at": report["rate_limit_reset_at"],
        "repositories": report["repositories"],
        "display_limit": limit,
        "note": (
            "complete means every requested source finished the pagination GitHub "
            "returned. It is not an atomic snapshot, eligibility, acceptance, or payment. "
            "display_limit does not describe source completeness. reward_evidence "
            "describes unconfirmed text mentions; legacy reward_rtc zero may mean unknown."
        ),
    }


def _cmd_browse_index(args, repos):
    """Filter one retained discovery file without starting the live collector."""
    from concierge.announcer import _read_snapshot, snapshot_data

    # Live browse emits ordinary JSON numbers. Preserve that representation in
    # saved reports while sharing the bounded reader and whole-input validation.
    payload = _read_snapshot(args.index, parse_float=float)
    snapshot = snapshot_data(payload)
    rows = snapshot["rows"]
    input_count = len(rows)
    for index, row in enumerate(rows):
        if not row.get("repo") or row.get("number") is None:
            raise ValueError(f"bounties[{index}] needs repo and number for browsing")
        if row.get("skills") is None:
            row["skills"] = []
        if row.get("difficulty") is None:
            row["difficulty"] = "unknown"
    if repos:
        wanted = {repo.casefold() for repo in repos}
        rows = [row for row in rows if row["repo"].casefold() in wanted]
    filtered = _filter_bounties(rows, args)
    displayed = filtered[:args.limit]
    previous = snapshot.get("selection", {})
    selection = {
        "input_count": input_count,
        "filtered_out_count": input_count - len(filtered),
        "omitted_by_limit_count": len(filtered) - len(displayed),
        "prior_filtered_out_count": (previous.get("prior_filtered_out_count", 0)
                                     + previous.get("filtered_out_count", 0)),
        "prior_omitted_by_limit_count": (previous.get("prior_omitted_by_limit_count", 0)
                                         + previous.get("omitted_by_limit_count", 0)),
    }
    source = snapshot["source"]
    source.pop("rows_selected")
    source.pop("preview_limit")
    for key in ("repositories", "rate_limited", "retry_after_seconds", "rate_limit_reset_at"):
        if key in payload:
            source[key] = payload[key]
    result = {
        "mode": "offline",
        "source": source,
        "rows": displayed,
        "filtered_count": len(filtered),
        "displayed_count": len(displayed),
        "display_limit": args.limit,
        "selection": selection,
        "filters": {"repos": repos, "skill": args.skill, "tier": args.tier,
                    "min_rtc": args.min_rtc, "max_rtc": args.max_rtc},
    }
    if args.json or args.report:
        # Never label retained rows as a fresh success-shaped live JSON list.
        print(json.dumps(result, indent=2, allow_nan=False))
    else:
        print(f"OFFLINE {source['kind']}; collection coverage: {source['coverage']}")
        print(f"Collection {source['started_at'] or 'not supplied'} .. {source['updated_at']}")
        print(
            f"Source collected {source['collected_count']} rows; retained "
            f"{source['rows_in_snapshot']}; omitted by the saved display limit "
            f"{source['omitted_from_snapshot']}."
        )
        print(
            f"Current file retained {input_count} rows; previous local filters excluded "
            f"{selection['prior_filtered_out_count']}; previous local display limits omitted "
            f"{selection['prior_omitted_by_limit_count']}."
        )
        print(source["note"])
        _print_bounty_table(displayed, show_evidence=getattr(args, "evidence", False))
        print(f"Showing {len(displayed)} of {len(filtered)} matching retained rows.")
        print(
            f"Current filters excluded {selection['filtered_out_count']} rows; "
            f"current display limit omitted {selection['omitted_by_limit_count']} matches."
        )
    if source["complete"] is False:
        sys.exit(2)


def _cmd_browse(args):
    """Handle the 'browse' subcommand."""
    repos = _browse_repos(args)
    if args.limit < 0:
        raise ValueError("--limit must be non-negative")
    if args.min_rtc is not None and args.max_rtc is not None and args.min_rtc > args.max_rtc:
        raise ValueError("--min-rtc must not exceed --max-rtc")

    if args.dry_run and getattr(args, "index", None):
        _print_dry_run_plan(
            args, "browse.offline.preview",
            {"index": args.index, "repos": repos, "skill": args.skill,
             "tier": args.tier, "min_rtc": args.min_rtc, "max_rtc": args.max_rtc,
             "limit": args.limit, "report": args.report, "evidence": args.evidence},
            ["Read retained bounty candidates", "Apply filters to retained reward mentions",
             "Display selected rows with the original source metadata"],
            ["bounties", "source_completeness", "filtered_count", "displayed_count"],
            json_output=args.report,
        )
        return

    if args.dry_run:
        _print_dry_run_plan(
            args, "browse.preview",
            {"repos": repos or list(config.REPOS), "skill": args.skill,
             "tier": args.tier, "min_rtc": args.min_rtc, "max_rtc": args.max_rtc,
             "limit": args.limit, "max_pages": args.max_pages,
             "report": args.report, "evidence": args.evidence},
            ["Fetch bounty candidates", "Apply filters to retained reward mentions",
             "Display selected rows with source-completion metadata"],
            ["bounties", "source_completeness", "filtered_count", "displayed_count"],
            json_output=args.report,
        )
        return

    if getattr(args, "index", None):
        _cmd_browse_index(args, repos)
        return

    try:
        report = fetch_bounties_report(repos=repos, max_pages=args.max_pages)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    filtered = _filter_bounties(report["bounties"], args)
    displayed = filtered[: args.limit]
    incomplete = not report["complete"]

    if args.report:
        _print_json(_browse_report_payload(report, filtered, displayed, args.limit))
        if incomplete:
            sys.exit(2)
        return

    if args.json:
        if incomplete:
            print(_incomplete_browse_message(report), file=sys.stderr)
            sys.exit(2)
        _print_json(displayed)
        return

    if incomplete:
        print("PARTIAL")
        print(_incomplete_browse_message(report))
        _print_browse_sources(report)
        if displayed:
            _print_bounty_table(displayed, show_evidence=getattr(args, "evidence", False))
        else:
            print(
                "0 displayed rows. The source read is incomplete, so this is not an empty queue."
            )
        print(
            f"Showing {len(displayed)} of {len(filtered)} filtered rows "
            f"({report['total_count']} collected). --limit is display-only."
        )
        sys.exit(2)

    print("COMPLETE")
    _print_bounty_table(displayed, show_evidence=getattr(args, "evidence", False))
    print(
        f"Collected {report['total_count']} rows; showing {len(displayed)} of "
        f"{len(filtered)} after filters. --limit does not describe source completeness."
    )
    print(f"Collection {report['started_at']} .. {report['updated_at']}")


def _cmd_faq(args):
    """Handle the 'faq' subcommand."""
    question = " ".join(args.question)
    if not question.strip():
        print("Please provide a question.", file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        _print_dry_run_plan(
            args, "faq.lookup", {"question": question, "use_grok": args.grok},
            ["Look up the question in the FAQ",
             "Use Grok for unanswered questions only when requested"],
            ["answer", "source", "confidence"],
        )
        return

    result = faq_answer(question, use_grok=args.grok)

    if args.json:
        _print_json(result)
    else:
        source_label = f"[{result['source']}]"
        conf = f"(confidence: {result['confidence']:.0%})"
        print(f"{source_label} {conf}")
        print()
        print(result["answer"])


def _cmd_wallet(args):
    """Handle the 'wallet' subcommand."""
    action = args.wallet_action

    if not action:
        print("Usage: concierge wallet {register,balance,holders,stats,miners,migrate}",
              file=sys.stderr)
        print("Run 'concierge wallet --help' for details.", file=sys.stderr)
        sys.exit(1)

    if action == "register":
        name = args.name
        valid, msg = validate_wallet_name(name)
        if not valid:
            print(f"Error: {msg}", file=sys.stderr)
            sys.exit(1)
        if args.dry_run:
            _print_dry_run_plan(
                args, "wallet.register.instructions", {"wallet_name": name},
                ["Show local wallet registration instructions"],
                ["registration_status"],
            )
            return
        guide = register_wallet_guide(name)
        if args.json:
            _print_json({"wallet_name": name, "guide": guide})
        else:
            print(guide)

    elif action == "balance":
        name = args.name
        if args.dry_run:
            _print_dry_run_plan(
                args, "wallet.balance", {"wallet": name},
                ["Read the wallet balance from the node"], ["balance_rtc"],
            )
            return
        result = get_balance(name)
        if isinstance(result, dict) and "error" in result:
            if args.json:
                _print_json(result)
            print(f"Error: {result['error']}", file=sys.stderr)
            sys.exit(1)
        if args.json:
            _print_json(result)
        else:
            print(f"Wallet:  {name}")
            if "balance_rtc" in result:
                print(f"Balance: {result['balance_rtc']:.6f} RTC")
            elif "amount_i64" in result:
                print(f"Balance: {result['amount_i64'] / 1_000_000:.6f} RTC")
            else:
                for k, v in result.items():
                    print(f"  {k}: {v}")

    elif action == "holders":
        minimum = getattr(args, "min_balance", None)
        if minimum is not None and not math.isfinite(minimum):
            raise ValueError("--min-balance must be finite")
        limit = getattr(args, "limit", 50)
        if limit < 0:
            raise ValueError("--limit must be non-negative")
        if args.dry_run:
            _print_dry_run_plan(
                args, "wallet.holders",
                {"category": getattr(args, "category", None),
                 "min_balance_rtc": minimum, "limit": limit},
                ["Read wallet holders from the node (requires RC_ADMIN_KEY)",
                 "Apply holder filters and display limit"],
                ["holders"],
            )
            return
        holders = get_all_holders()
        if isinstance(holders, dict) and "error" in holders:
            print("Error: %s" % holders["error"], file=sys.stderr)
            sys.exit(1)
        # Filter
        cat_filter = getattr(args, "category", None)
        if cat_filter:
            holders = [h for h in holders if h["category"] == cat_filter]
        if minimum is not None:
            holders = [h for h in holders if h["amount_rtc"] >= minimum]
        holders = holders[:limit]
        if args.json:
            _print_json(holders)
        else:
            print("%-4s %-45s %12s  %s" % ("#", "Wallet", "Balance RTC", "Category"))
            print("-" * 75)
            for i, h in enumerate(holders, 1):
                print("%-4d %-45s %12.2f  %s" % (
                    i, h["miner_id"][:45], h["amount_rtc"], h["category"]))
            print("-" * 75)
            print("Showing %d wallets" % len(holders))

    elif action == "stats":
        if args.dry_run:
            _print_dry_run_plan(
                args, "wallet.stats", {},
                ["Read wallet holders from the node (requires RC_ADMIN_KEY)",
                 "Compute wallet statistics"],
                ["stats"],
            )
            return
        stats = get_holder_stats()
        if isinstance(stats, dict) and "error" in stats:
            print("Error: %s" % stats["error"], file=sys.stderr)
            sys.exit(1)
        if args.json:
            _print_json(stats)
        else:
            print("=== RustChain Wallet Statistics ===")
            print()
            print("Total wallets:        %d" % stats["total_wallets"])
            print("With balance:         %d" % stats["wallets_with_balance"])
            print("Empty:                %d" % stats["empty_wallets"])
            print("Total RTC:            {:,.2f}".format(stats["total_rtc"]))
            print()
            print("--- By Category ---")
            for cat, info in sorted(stats["categories"].items(),
                                    key=lambda x: x[1]["rtc"], reverse=True):
                pct = info["rtc"] / stats["total_rtc"] * 100 if stats["total_rtc"] else 0
                print("  %-15s %4d wallets  %12.2f RTC  (%5.1f%%)" % (
                    cat, info["count"], info["rtc"], pct))
            print()
            print("--- User Distribution (excl. founder/platform) ---")
            for tier, info in stats["distribution"].items():
                print("  %-20s %4d holders  %12.2f RTC" % (
                    tier, info["count"], info["rtc"]))

    elif action == "miners":
        if args.dry_run:
            _print_dry_run_plan(
                args, "wallet.miners", {},
                ["Read active miners and epoch information from the node"],
                ["miners", "epoch"],
            )
            return
        miners = get_active_miners()
        if isinstance(miners, dict) and "error" in miners:
            print("Error: %s" % miners["error"], file=sys.stderr)
            sys.exit(1)
        epoch = get_epoch_info()
        if args.json:
            _print_json({"epoch": epoch, "miners": miners})
        else:
            if not isinstance(epoch, dict) or "error" in epoch:
                print("Epoch info unavailable")
            else:
                print("Epoch %s | Slot %s | Enrolled: %s | Pot: %s RTC" % (
                    epoch.get("epoch", "?"), epoch.get("slot", "?"),
                    epoch.get("enrolled_miners", "?"), epoch.get("epoch_pot", "?")))
            print()
            print("%-4s %-40s %-15s %s" % ("#", "Miner", "Architecture", "Multiplier"))
            print("-" * 70)
            for i, m in enumerate(miners, 1):
                print("%-4d %-40s %-15s %sx" % (
                    i, m["miner"][:40], m.get("device_arch", "?"),
                    m.get("antiquity_multiplier", "?")))
            print("-" * 70)
            print("%d active miners" % len(miners))

    elif action == "migrate":
        _cmd_wallet_migrate(args)

    else:
        print("Unknown wallet action: %s" % action, file=sys.stderr)
        sys.exit(1)


def _cmd_wallet_migrate(args):
    """Handle the 'wallet migrate' subcommand."""
    from concierge import config as _cfg

    # Select the same mode as the live handler, before any retained or remote read.
    if args.dry_run:
        if args.history:
            _print_dry_run_plan(args, "wallet.migrate.history", {},
                                ["Read retained migration history"], ["history"])
        elif getattr(args, "list", False):
            minimum = getattr(args, "min_balance", 0.1)
            if not math.isfinite(minimum) or minimum < 0:
                raise ValueError("--min-balance must be finite and non-negative")
            _print_dry_run_plan(
                args, "wallet.migrate.list", {"min_balance_rtc": minimum},
                ["Query Discord holders", "Read prior migration records"],
                ["holders", "prior_migrations"],
            )
        elif args.resume:
            if not args.user or args.force:
                raise ValueError("--resume requires --user and cannot be combined with --force")
            _print_dry_run_plan(
                args, "wallet.migrate.resume", {"user": args.user},
                ["Load the exact retained migration attempt",
                 "Recover or check its existing transfer with the same idempotency key",
                 "After confirmation, reconcile its idempotent Discord debit"],
                ["attempt", "transfer_status", "debit_status"],
            )
        else:
            if not args.user or not args.to_wallet:
                raise ValueError("--user and --to are required for migration")
            valid, msg = validate_wallet_name(args.to_wallet)
            if not valid:
                raise ValueError("Invalid target wallet name: " + msg)
            steps = [] if args.force else ["Check the prior migration record"]
            steps += [
                "Read Discord balance and require at least 0.1 RTC",
                "Persist one migration attempt and transfer idempotency key",
                "Request the credit and retain its transaction receipt",
                "Require confirmed transfer status before the idempotent Discord debit",
                "Retain progress for --resume and record completion",
            ]
            _print_dry_run_plan(
                args, "wallet.migrate.user",
                {"user": args.user, "to_wallet": args.to_wallet,
                 "source_wallet": _cfg.MIGRATION_SOURCE_WALLET, "force": args.force},
                steps, ["prior_migration", "balance_rtc", "amount_rtc", "outcome"],
            )
        return

    # --- history ---
    if args.history:
        history = get_migration_history()
        if args.json:
            _print_json(history)
        elif not history:
            print("No migrations recorded yet.")
        else:
            print("%-4s %-20s %-25s %10s  %-10s  %s" % (
                "#", "Discord ID", "Target Wallet", "RTC", "Status", "Date"))
            print("-" * 100)
            for i, m in enumerate(history, 1):
                print("%-4d %-20s %-25s %10.2f  %-10s  %s" % (
                    i, m["discord_user_id"], m["target_wallet"],
                    m["amount_rtc"], m["status"], m["created_at"]))
        return

    # --- list ---
    if getattr(args, "list", False):
        min_bal = getattr(args, "min_balance", 0.1)
        holders = list_discord_holders(min_balance=min_bal)
        if isinstance(holders, dict) and "error" in holders:
            print("Error: %s" % holders["error"], file=sys.stderr)
            sys.exit(1)
        if args.json:
            _print_json(holders)
        elif not holders:
            print("No Discord holders found with balance >= %.2f RTC" % min_bal)
        else:
            total = sum(h["balance"] for h in holders)
            print("=== Discord Economy Holders (>= %.2f RTC) ===" % min_bal)
            print()
            print("%-4s %-22s %10s %12s %12s  %s" % (
                "#", "Discord User ID", "Balance", "Earned", "Spent", "Migrated?"))
            print("-" * 90)
            for i, h in enumerate(holders, 1):
                migrated = "YES" if already_migrated(h["user_id"]) else "-"
                print("%-4d %-22s %10.4f %12.4f %12.4f  %s" % (
                    i, h["user_id"], h["balance"],
                    h.get("total_earned", 0), h.get("total_spent", 0),
                    migrated))
            print("-" * 90)
            print("%d holders  |  %.4f RTC total" % (len(holders), total))
        return

    # --- migrate or resume a specific user ---
    user_id = args.user
    if not user_id:
        raise ValueError("--user is required for migration or --resume")
    if args.resume:
        if args.force:
            raise ValueError("--resume cannot be combined with --force")
        attempt = get_attempt(user_id)
        if attempt is None:
            raise ValueError(
                "No resumable attempt exists for this user. Legacy unresolved records "
                "require reconciliation of their existing transfer."
            )
        if args.to_wallet is not None and args.to_wallet != attempt["target_wallet"]:
            raise ValueError("--to does not match the retained migration target")
    else:
        if not args.to_wallet:
            raise ValueError("--to is required for a new migration")
        valid, msg = validate_wallet_name(args.to_wallet)
        if not valid:
            raise ValueError("Invalid target wallet name: " + msg)
        if not args.force and already_migrated(user_id):
            raise ValueError(
                "Migration already recorded; use --resume for a retained attempt. "
                "--force only starts another migration after completion."
            )
        discord_info = get_discord_balance(user_id)
        if not isinstance(discord_info, dict) or "error" in discord_info:
            detail = discord_info.get("error") if isinstance(discord_info, dict) else "Malformed Discord balance"
            raise ValueError(detail)
        if str(discord_info.get("user_id")) != str(user_id):
            raise ValueError("Discord balance does not match the requested user")
        attempt = prepare_migration(
            user_id, _cfg.MIGRATION_SOURCE_WALLET, args.to_wallet,
            discord_info.get("balance"), _cfg.RUSTCHAIN_NODE_URL, force=args.force,
        )

    attempt, message, exit_code = continue_migration(attempt)
    if args.json:
        _print_json({"migration": attempt, "message": message})
    else:
        print(message)
        print("Attempt: %s | State: %s" % (attempt["attempt_key"], attempt["status"]))
        if attempt["tx_hash"]:
            print("Transfer: %s" % attempt["tx_hash"])
        if exit_code:
            print("Resume: concierge wallet migrate --user %s --resume" % user_id)
    if exit_code:
        sys.exit(exit_code)


def _cmd_status(args):
    """Handle the 'status' subcommand."""
    wallet = args.wallet
    if not wallet:
        print("Error: --wallet is required for status checks.", file=sys.stderr)
        sys.exit(1)

    valid, msg = validate_wallet_name(wallet)
    if not valid:
        print(f"Error: {msg}", file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        print(f"[dry-run] Would check payout status for wallet: {wallet}")
        return

    pending, history = check_status(wallet)

    if args.json:
        _print_json({"wallet": wallet, "pending": pending, "history": history})
    else:
        print(f"Payout status for: {wallet}")
        print()
        print(format_payout_status(pending, history))


def _cmd_engage(args):
    """Handle the 'engage' subcommand."""
    if args.star_repos:
        if star_all_ecosystem_repos is None:
            print(
                "Error: engagement module is not available.",
                file=sys.stderr,
            )
            sys.exit(1)

        if args.dry_run:
            _print_dry_run_plan(
                args, "engage.star_repos", {"repos": list(config.REPOS)},
                ["Star the configured repositories with the existing GitHub account"],
                ["star_results"],
            )
            return

        token = config.GITHUB_TOKEN
        if not token:
            print(
                "Error: GITHUB_TOKEN environment variable is required to star repos.",
                file=sys.stderr,
            )
            sys.exit(1)

        results = star_all_ecosystem_repos(token)
        if args.json:
            _print_json(results)
        else:
            for repo, ok in results.items():
                status = "starred" if ok else "FAILED"
                print(f"  {repo}: {status}")

    elif args.devto:
        if check_devto_articles is None:
            print(
                "Error: engagement module is not available.",
                file=sys.stderr,
            )
            sys.exit(1)

        if args.dry_run:
            _print_dry_run_plan(
                args, "engage.devto", {}, ["Fetch Dev.to article statistics"],
                ["articles", "statistics"],
            )
            return

        api_key = config.DEVTO_API_KEY
        if not api_key:
            print(
                "Error: DEVTO_API_KEY environment variable is required.",
                file=sys.stderr,
            )
            sys.exit(1)

        try:
            articles = check_devto_articles(api_key)
        except DevtoLookupError as exc:
            if args.json:
                _print_json({"error": "devto_lookup_failed", "detail": str(exc)})
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
        if args.json:
            _print_json(articles)
        else:
            if not articles:
                print("No Dev.to articles found.")
            else:
                print("Dev.to Articles:")
                for a in articles:
                    print(
                        f"  {a['title']}"
                        f"  views={a['page_views']}"
                        f"  reactions={a['positive_reactions']}"
                    )
                    print(f"    {a['url']}")

    elif args.saascity:
        if saascity_upvote is None:
            print(
                "Error: engagement module is not available.",
                file=sys.stderr,
            )
            sys.exit(1)

        if args.dry_run:
            _print_dry_run_plan(
                args, "engage.saascity",
                {"listings": dict(SAASCITY_LISTINGS), "api_base": SAASCITY_API_BASE},
                ["Upvote the configured SaaSCity listings with the existing provider account"],
                ["upvote_results"],
            )
            return

        api_key = config.SAASCITY_KEY
        if not api_key:
            print(
                "Error: SAASCITY_KEY environment variable is required to upvote on SaaSCity.",
                file=sys.stderr,
            )
            sys.exit(1)

        try:
            results = saascity_upvote(api_key=api_key)
        except SaaSCityError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)

        if args.json:
            _print_json(results)
        else:
            for listing, ok in results.items():
                status = "upvoted" if ok else "FAILED"
                print(f"  {listing}: {status}")

    else:
        print(
            "Specify an engagement action: --star-repos, --devto, or --saascity",
            file=sys.stderr,
        )
        sys.exit(1)


def _cmd_mine(args):
    """Handle the 'mine' subcommand."""
    if args.pow != "warthog":
        print("Error: only --pow warthog is supported at this time.", file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        if args.detect_only:
            _print_dry_run_plan(
                args, "mine.detect", {"pow": args.pow},
                ["Inspect processes, services, and screen sessions"],
                ["detection", "bonus"],
            )
            return
        if not args.wallet:
            raise ValueError("--wallet is required unless --detect-only is used")
        # These helpers only resolve static configuration and build argv. Their
        # 'verified' flag is not evidence that a pool or account was contacted.
        pool = pow_miners.resolve_pool_endpoint(args.pool, args.pool_url)
        if not pool.get("verified"):
            raise ValueError(pool.get("error", "Failed to resolve pool"))
        if args.miner == "bzminer":
            command = pow_miners.build_bzminer_command(
                wallet=args.wallet, pool_url=pool["endpoint"],
                miner_path=args.miner_path or "bzminer",
            )
        else:
            command = pow_miners.build_janusminer_command(
                wallet=args.wallet, miner_path=args.miner_path or "janusminer-ubuntu22",
            )
        _print_dry_run_plan(
            args, "mine.start",
            {"pow": args.pow, "miner": args.miner, "wallet": args.wallet,
             "command": command, "log_file": args.log_file,
             "pool": {"name": pool["pool"], "endpoint": pool["endpoint"],
                      "source": pool["source"]}},
            ["Detect existing miners", "Validate pool configuration and query node RPC",
             "Start the selected miner and capture logs"],
            ["detection", "pool_account_verification", "node_rpc_verification",
             "bonus", "executable_available"],
        )
        return

    detection = pow_miners.detect_pow_processes()

    if args.detect_only:
        bonus = pow_miners.calculate_bonus_multiplier(
            managed_subprocess_running=False,
            external_miner_detected=detection.get("external_miner_detected", False),
            pool_account_verified=False,
            node_rpc_verified=False,
        )
        payload = {"mode": "detect-only", "detection": detection, "bonus": bonus}
        if args.json:
            _print_json(payload)
        else:
            print("PoW detection summary:")
            print(pow_miners.summarize_for_console(payload))
        return

    if not args.wallet:
        print("Error: --wallet is required unless --detect-only is used.", file=sys.stderr)
        sys.exit(1)

    pool_result = pow_miners.resolve_pool_endpoint(args.pool, args.pool_url)
    if not pool_result.get("verified"):
        print(f"Error: {pool_result.get('error', 'Failed to resolve pool')}", file=sys.stderr)
        sys.exit(1)

    pool_proof = pow_miners.verify_pool_account(args.wallet, args.pool, args.pool_url)
    node_proof = pow_miners.query_node_rpc(args.wallet)

    command = None
    if args.miner == "bzminer":
        command = pow_miners.build_bzminer_command(
            wallet=args.wallet,
            pool_url=pool_result["endpoint"],
            miner_path=args.miner_path or "bzminer",
        )
    else:
        command = pow_miners.build_janusminer_command(
            wallet=args.wallet,
            miner_path=args.miner_path or "janusminer-ubuntu22",
        )

    result = {
        "command": command,
        "pool_proof": pool_proof,
        "node_rpc_proof": node_proof,
    }
    managed = None
    try:
        managed = pow_miners.start_managed_miner(command, log_path=args.log_file)
        result.update(pid=managed.process.pid, log_path=managed.log_path)
        if not args.json:
            print(f"Started {args.miner} (pid={managed.process.pid})")
            print(f"Pool: {pool_result['endpoint']}")
            print(f"Wallet: {args.wallet}")
            print(f"Logs: {managed.log_path}")
            bonus = pow_miners.calculate_bonus_multiplier(
                managed_subprocess_running=managed.process.poll() is None,
                external_miner_detected=detection.get("external_miner_detected", False),
                pool_account_verified=pool_proof.get("verified", False),
                node_rpc_verified=node_proof.get("verified", False),
            )
            print()
            print("Verification summary:")
            print(pow_miners.summarize_for_console(
                {
                    "pool_proof": pool_proof,
                    "node_rpc_proof": node_proof,
                    "bonus": bonus,
                }
            ))
            print()
            print("Press Ctrl+C to stop mining.")

        returncode = managed.process.wait()
        # Popen uses negative return codes for signals; shells use 128 + signal.
        exit_code = returncode if returncode >= 0 else 128 - returncode
        result.update(
            status="exited" if returncode == 0 else "failed",
            returncode=returncode,
            exit_code=exit_code,
            bonus=pow_miners.calculate_bonus_multiplier(
                managed_subprocess_running=False,
                external_miner_detected=detection.get("external_miner_detected", False),
                pool_account_verified=pool_proof.get("verified", False),
                node_rpc_verified=node_proof.get("verified", False),
            ),
        )
        if args.json:
            _print_json(result)
        else:
            print(f"Miner exited with code {returncode}")
        sys.exit(exit_code)
    except KeyboardInterrupt:
        result.update(status="interrupted", exit_code=130)
        if managed is not None:
            stop_info = pow_miners.stop_managed_miner(managed)
            result.update(status="stopped", returncode=managed.process.returncode,
                          stop_info=stop_info)
            if not args.json:
                print("Stopping miner...")
                print(pow_miners.summarize_for_console(stop_info))
        else:
            print("Interrupted.", file=sys.stderr)
        if args.json:
            _print_json(result)
        sys.exit(130)
    except Exception as exc:
        if args.json:
            _print_json(dict(result, status="failed", error=str(exc), exit_code=1))
        print(f"Error: failed to start mining: {exc}", file=sys.stderr)
        sys.exit(1)


def _cmd_announce(args):
    """Handle the 'announce' subcommand."""
    if args.dry_run:
        _print_dry_run_plan(
            args, "announce.preview", {"repos": list(config.REPOS)},
            ["Fetch bounty candidates", "Sort retained reward evidence",
             "Format short, medium, and long previews; this handler does not post",
             "For saved content use: python -m concierge.announcer --index PATH --format long"],
            ["bounties", "source_completeness", "announcement_content"],
        )
        return
    if format_announcement is None:
        print(
            "Error: announcer module is not available.",
            file=sys.stderr,
        )
        sys.exit(1)

    bounties = fetch_bounties()
    bounties.sort(key=reward_sort_key, reverse=True)

    # The formatter accepts native index rows. Keep evidence, excerpts and
    # additional mentions rather than dropping them in a legacy rtc adapter.
    content = format_announcement(bounties)

    if args.json:
        _print_json(content)
    else:
        print("--- Short (Twitter) ---")
        print(content.get("short", ""))
        print()
        print("--- Medium (4claw / AgentChan) ---")
        print(content.get("medium", ""))
        print()
        print("--- Long (Moltbook / Dev.to) ---")
        print(content.get("long", ""))


def _cmd_claim(args):
    """Handle the 'claim' subcommand."""
    repo = args.repo
    if "/" not in repo:
        repo = f"Scottcjn/{repo}"
    issue_num = args.issue
    wallet = args.wallet

    if wallet is not None:
        valid, msg = validate_wallet_name(wallet)
        if not valid:
            print(f"Error: {msg}", file=sys.stderr)
            sys.exit(1)

    issue_url = f"https://github.com/{repo}/issues/{issue_num}"

    if args.dry_run:
        _print_dry_run_plan(
            args, "claim.instructions",
            {"repo": repo, "issue": issue_num, "wallet": wallet, "url": issue_url},
            ["Validate live claim prerequisites",
             "Show claim instructions for the supplied issue and wallet; this handler does not post"],
            ["bounty_availability", "claim_eligibility", "assignment", "payment"],
        )
        return

    steps = [
        "Read the sponsor's current claim, submission, and payout requirements "
        f"at {issue_url}",
        "Complete the sponsor's required claim/application and payment setup "
        "with the intended claimant account.",
        "Supply the wallet only where the sponsor's payment process requests it.",
        "Submit the required contribution or update its existing PR, referencing "
        f"#{issue_num} and following the sponsor's submission process.",
        "Follow the sponsor's review, award, and payout process; a merge alone "
        "does not confirm an award or payment.",
    ]

    if args.json:
        _print_json({
            "action": "claim",
            "status": "instructions_only",
            "claim_submitted": False,
            "provider_mutation_executed": False,
            "repo": repo,
            "issue": issue_num,
            "wallet": wallet,
            "url": issue_url,
            "assignment": None,
            "payment": None,
            "next_steps": steps,
        })
    else:
        print(f"Claim instructions for issue #{issue_num}")
        print(f"Repository: {repo}")
        print(f"Issue URL:  {issue_url}")
        if wallet is not None:
            print(f"Wallet:     {wallet}")
        print(
            "Instructions only; this command has not submitted a claim "
            "or changed provider state."
        )
        print("Assignment and payment are unconfirmed.")
        print()
        print("Next steps:")
        for number, step in enumerate(steps, 1):
            print(f"  {number}. {step}")


def _cmd_version(args):
    """Handle the 'version' subcommand."""
    if args.json:
        _print_json({"version": __version__})
    else:
        print(f"bounty-concierge {__version__}")


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def _add_common_flags(parser):
    """Add --dry-run and --json flags to a parser or subparser."""
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Preview actions without making network calls (plan details: docs/DRY_RUN.md)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Output results as JSON",
    )


def _rtc_filter_bound(text):
    """Parse a finite non-negative bound before starting a live source read."""
    try:
        value = float(text)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("RTC bound must be a finite non-negative number") from exc
    if not math.isfinite(value) or value < 0:
        raise argparse.ArgumentTypeError("RTC bound must be a finite non-negative number")
    return value


def _build_parser():
    """Build and return the top-level argument parser."""
    parser = argparse.ArgumentParser(
        prog="concierge",
        description="RustChain Bounty Concierge -- CLI for bounty hunters",
    )
    _add_common_flags(parser)
    # Subparsers must not replace common flags supplied before their command.
    parser.set_defaults(dry_run=False, json=False)

    sub = parser.add_subparsers(dest="command", help="Available commands")

    # Each intake module owns its flags and help; the installed entry point
    # forwards them instead of keeping another parser in sync.
    for command, description in (
        ("bountyhub", "Read the public BountyHub catalog and export an intake shortlist"),
        ("capture-batch", "Capture a bounded issue shortlist for offline routing"),
        ("capture-recover", "Recover an interrupted batch from saved capture files"),
    ):
        delegated = sub.add_parser(command, add_help=False, help=description)
        _add_common_flags(delegated)

    # --- browse ---
    p_browse = sub.add_parser("browse", help="List and filter open bounties")
    _add_common_flags(p_browse)
    p_browse.add_argument("--index", help="Filter a retained index or live/offline browse report without network access")
    p_browse.add_argument("--repo", nargs="+", help="Filter by repo (short name or owner/repo)")
    p_browse.add_argument("--skill", help="Filter by required skill")
    p_browse.add_argument("--tier", choices=["micro", "standard", "major", "critical"],
                          help="Filter by difficulty tier")
    p_browse.add_argument("--min-rtc", type=_rtc_filter_bound, help="Minimum indexed RTC mention; excludes unknown amounts, not a payment filter")
    p_browse.add_argument("--max-rtc", type=_rtc_filter_bound, help="Maximum indexed RTC mention; excludes unknown amounts, not a payment filter")
    p_browse.add_argument("--evidence", action="store_true", default=False,
                          help="Show retained reward excerpts, other mentions and source links in text output")
    p_browse.add_argument("--limit", type=int, default=20, help="Max displayed rows (default: 20). Does not change source completeness.")
    p_browse.add_argument(
        "--max-pages",
        type=int,
        default=100,
        help="Page bound passed to the collector (1-1000, default: 100)",
    )
    p_browse.add_argument(
        "--report",
        action="store_true",
        default=False,
        help="Print one JSON object with rows and source-completion metadata",
    )

    # --- faq ---
    p_faq = sub.add_parser("faq", help="Ask a question about RustChain or bounties")
    _add_common_flags(p_faq)
    p_faq.add_argument("question", nargs="+", help="Your question")
    p_faq.add_argument("--grok", action="store_true", default=False,
                       help="Use Grok AI for unanswered questions (requires GROK_API_KEY)")

    # --- wallet ---
    p_wallet = sub.add_parser("wallet", help="Wallet operations")
    _add_common_flags(p_wallet)
    wallet_sub = p_wallet.add_subparsers(dest="wallet_action", help="Wallet action")

    p_w_register = wallet_sub.add_parser("register", help="Get wallet registration instructions")
    _add_common_flags(p_w_register)
    p_w_register.add_argument("name", help="Desired wallet name")

    p_w_balance = wallet_sub.add_parser("balance", help="Check wallet RTC balance")
    _add_common_flags(p_w_balance)
    p_w_balance.add_argument("name", help="Wallet or miner ID")

    p_w_holders = wallet_sub.add_parser("holders", help="List all wallet holders")
    _add_common_flags(p_w_holders)
    p_w_holders.add_argument("--category", choices=["named", "founder", "platform", "auto-hash", "redteam"],
                             help="Filter by wallet category")
    p_w_holders.add_argument("--min-balance", type=float, help="Minimum RTC balance")
    p_w_holders.add_argument("--limit", type=int, default=50, help="Max results (default: 50)")

    p_w_stats = wallet_sub.add_parser("stats", help="Wallet holder statistics")
    _add_common_flags(p_w_stats)

    p_w_miners = wallet_sub.add_parser("miners", help="List active attesting miners")
    _add_common_flags(p_w_miners)

    p_w_migrate = wallet_sub.add_parser(
        "migrate", help="Migrate Discord economy balances to on-chain wallets")
    _add_common_flags(p_w_migrate)
    p_w_migrate.add_argument("--list", action="store_true",
                             help="List Discord holders eligible for migration")
    p_w_migrate.add_argument("--user", help="Discord user ID to migrate")
    p_w_migrate.add_argument("--to", dest="to_wallet", help="Target on-chain wallet name")
    p_w_migrate.add_argument("--history", action="store_true",
                             help="Show migration history")
    p_w_migrate.add_argument("--force", action="store_true",
                             help="Start another migration only after the prior one completed")
    p_w_migrate.add_argument("--resume", action="store_true",
                             help="Continue the retained transfer and debit without a new credit")
    p_w_migrate.add_argument("--min-balance", type=float, default=0.1,
                             help="Minimum balance for --list (default: 0.1)")

    # --- status ---
    p_status = sub.add_parser("status", help="Check pending payouts for a wallet")
    _add_common_flags(p_status)
    p_status.add_argument("--wallet", required=True, help="Wallet or miner ID")

    # --- mine ---
    p_mine = sub.add_parser("mine", help="PoW dual-mining helpers for Warthog")
    _add_common_flags(p_mine)
    p_mine.add_argument("--pow", required=True, choices=["warthog"],
                        help="PoW algorithm/network (currently: warthog)")
    p_mine.add_argument("--wallet", help="Wallet/miner address used for mining")
    p_mine.add_argument("--pool", default="woolypooly",
                        choices=["woolypooly", "cedric-crispin", "herominers", "accpool"],
                        help="Known pool preset (default: woolypooly)")
    p_mine.add_argument("--pool-url", help="Explicit pool URL override")
    p_mine.add_argument("--miner", default="bzminer", choices=["bzminer", "janusminer"],
                        help="Miner engine to launch (default: bzminer)")
    p_mine.add_argument("--miner-path", help="Path to miner binary")
    p_mine.add_argument("--detect-only", action="store_true",
                        help="Only detect external miners/services/screen sessions")
    p_mine.add_argument("--log-file", default="warthog_miner.log",
                        help="Log file for managed miner output")

    # --- engage ---
    p_engage = sub.add_parser("engage", help="Cross-platform engagement actions")
    _add_common_flags(p_engage)
    p_engage.add_argument("--star-repos", action="store_true", default=False,
                          help="Star all RustChain ecosystem repos on GitHub")
    p_engage.add_argument("--devto", action="store_true", default=False,
                          help="Check Dev.to article stats")
    p_engage.add_argument("--saascity", action="store_true", default=False,
                          help="Upvote RustChain and BoTTube on SaaSCity "
                               "(requires SAASCITY_KEY)")

    # --- announce ---
    p_announce = sub.add_parser("announce", help="Preview or post bounty announcements")
    _add_common_flags(p_announce)

    # --- claim ---
    p_claim = sub.add_parser("claim", help="Show claim instructions for a bounty")
    _add_common_flags(p_claim)
    p_claim.add_argument("--issue", type=int, required=True, help="Issue number")
    p_claim.add_argument("--repo", default="Scottcjn/rustchain-bounties",
                         help="Repository (default: Scottcjn/rustchain-bounties)")
    p_claim.add_argument(
        "--wallet", help="Optional RustChain wallet name, when required by the sponsor"
    )

    # --- version ---
    p_version = sub.add_parser("version", help="Show version")
    _add_common_flags(p_version)

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    """Main CLI entry point."""
    parser = _build_parser()
    args, remaining = parser.parse_known_args()
    if args.command in {"bountyhub", "capture-batch", "capture-recover"}:
        args.intake_args = remaining
    elif remaining:
        parser.error("unrecognized arguments: " + " ".join(remaining))

    if not args.command:
        parser.print_help()
        sys.exit(0)

    dispatch = {
        "browse": _cmd_browse,
        "bountyhub": _cmd_bounty_intake,
        "capture-batch": _cmd_bounty_intake,
        "capture-recover": _cmd_bounty_intake,
        "faq": _cmd_faq,
        "wallet": _cmd_wallet,
        "status": _cmd_status,
        "mine": _cmd_mine,
        "engage": _cmd_engage,
        "announce": _cmd_announce,
        "claim": _cmd_claim,
        "version": _cmd_version,
    }

    handler = dispatch.get(args.command)
    if handler:
        try:
            handler(args)
        except KeyboardInterrupt:
            print("\nInterrupted.", file=sys.stderr)
            sys.exit(130)
        except Exception as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
    else:
        parser.print_help()
        sys.exit(1)
