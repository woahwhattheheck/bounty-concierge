# SPDX-License-Identifier: MIT
"""Cross-platform bounty candidate formatter and existing dispatcher.

Formatting is local and does not post, reserve work, or establish eligibility.
The existing dispatcher remains explicitly separate from preview generation.
Run ``python -m concierge.announcer --index PATH`` for offline snapshot previews.
"""
from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from decimal import Decimal
from typing import Dict, List

from concierge.readme_sync import (
    _format_int,
    _index_object,
    _invalid_constant,
    _markdown_cell,
    _markdown_link_target,
    _parse_index_timestamp,
    _single_line_text,
    _validated_bounty_rows,
    indexed_reward_label,
)

SHORT_LIMIT = 280
MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024
_DISCOVERY_NOTICE = (
    "Indexed RTC figures may describe pools, caps or estimates, not per-claim pay. "
    "Confirm live sponsor terms, assignment, our eligibility and payment route "
    "before starting work. This preview does not confirm availability or a cash value."
)


def _normalized_bounties(bounties: List[dict]) -> List[dict]:
    """Accept both the existing CLI rtc shape and the index reward_rtc shape."""
    normalized = []
    aliases = []
    for index, bounty in enumerate(bounties):
        if not isinstance(bounty, dict):
            raise ValueError(f"bounties[{index}] must be an object")
        if not isinstance(bounty.get("title"), str):
            raise ValueError(f"bounties[{index}].title must be a string")
        row = dict(bounty)
        if "reward_rtc" not in row:
            row["reward_rtc"] = row.get("rtc")
        # Use the same literal-text and numeric contract as the README, without
        # fetching or rewriting its index. Input order is deliberately retained.
        normalized.append(row)
        if "rtc" in row and "reward_rtc" in bounty:
            aliases.append((index, row))
    rows = _validated_bounty_rows(normalized)
    for index, row in aliases:
        legacy = dict(row, reward_rtc=row["rtc"])
        _validated_bounty_rows([legacy])
        left, right = row["rtc"], row["reward_rtc"]
        unequal = (left is None) != (right is None)
        if left is not None and right is not None:
            unequal = Decimal(str(left)) != Decimal(str(right))
        if unequal:
            raise ValueError(f"bounties[{index}] has conflicting rtc and reward_rtc values")
    return rows


def _announcement_amount(bounty: dict) -> str:
    """Qualified evidence text, or the legacy 'indexed N RTC' phrase."""
    if bounty.get("reward_evidence") is None:
        return f"indexed {_format_int(bounty.get('reward_rtc'))} RTC"
    return indexed_reward_label(bounty)


def _short_announcement(bounty: dict) -> str:
    """Budget the title around whole metadata; never slice a destination URL."""
    prefix = "RustChain candidate: "
    title = _single_line_text(bounty["title"]).strip()
    amount = f" | {_announcement_amount(bounty)}"
    raw_url = bounty.get("url") or ""
    url = _markdown_link_target(raw_url) if raw_url else ""
    destination = f" | {url}" if url else " | source link not supplied"
    # Prefer preserving the complete source link over repeating an oversized
    # amount. The medium and long formats still contain the complete amount.
    if len(prefix + amount + destination) > SHORT_LIMIT:
        amount = ""
    if len(prefix + destination) > SHORT_LIMIT:
        destination = " | link exceeds short-format budget; use full preview"
    room = SHORT_LIMIT - len(prefix) - len(amount) - len(destination)
    if len(title) > room:
        title = (title[:room - 3] + "...") if room >= 3 else title[:room]
    return prefix + title + amount + destination


def format_announcement(bounties: List[dict]) -> Dict[str, str]:
    """Create local short, medium and long candidate previews.

    Input rows require title and may supply url, difficulty, labels and either
    rtc (the existing CLI contract) or reward_rtc (the index contract). Missing
    amounts remain unknown. Conflicting aliases reject instead of choosing one.
    Input order is preserved; callers own their selection/ranking policy.

    The short format is at most 280 Python characters, not a guarantee of any
    provider's weighted-length policy. Oversized links are explicitly omitted
    from that format, never silently truncated; complete links remain in long.
    """
    return _format_validated_announcement(_normalized_bounties(bounties))


def _format_validated_announcement(rows: List[dict]) -> Dict[str, str]:
    """Render rows already checked by this module's public input boundary."""
    if not rows:
        return {"short": "", "medium": "", "long": ""}

    short = _short_announcement(rows[0])
    medium_lines = ["RustChain bounty candidates (supplied snapshot):\n"]
    for bounty in rows[:5]:
        raw_url = bounty.get("url") or ""
        url = _markdown_link_target(raw_url) if raw_url else "source link not supplied"
        medium_lines.append(
            f"- {_markdown_cell(bounty['title'])} | "
            f"{_markdown_cell(_announcement_amount(bounty))} | {url}"
        )
    if len(rows) > 5:
        medium_lines.append(f"\n+{len(rows) - 5} supplied candidates in the full preview.")
    medium_lines.append("\n" + _DISCOVERY_NOTICE)

    long_lines = [
        "# RustChain Bounty Candidates\n",
        "| Title | Indexed amount | Difficulty | Source |",
        "|-------|-------------|------------|--------|",
    ]
    for bounty in rows:
        raw_url = bounty.get("url") or ""
        link = f"[source]({_markdown_link_target(raw_url)})" if raw_url else "not supplied"
        long_lines.append(
            f"| {_markdown_cell(bounty['title'])} | {_markdown_cell(_announcement_amount(bounty))} | "
            f"{_markdown_cell(bounty.get('difficulty') or 'unknown')} | {link} |"
        )
    long_lines.append("\n" + _DISCOVERY_NOTICE)
    return {"short": short, "medium": "\n".join(medium_lines), "long": "\n".join(long_lines)}


def _snapshot_count(payload: dict, key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"snapshot {key} must be a non-negative integer")
    return value


def _read_snapshot(path: str, *, parse_float=Decimal) -> dict:
    """Read one bounded ordinary file, with no collector or provider call."""
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("snapshot must be an ordinary file")
        if metadata.st_size > MAX_SNAPSHOT_BYTES:
            raise ValueError("snapshot exceeds the 16 MiB input limit")
        with os.fdopen(fd, "rb") as handle:
            fd = None
            # Read one byte beyond the observed length to detect growth without
            # reserving the entire input cap for an unchanged small snapshot.
            raw = handle.read(metadata.st_size + 1)
            if len(raw) > metadata.st_size:
                raw += handle.read(MAX_SNAPSHOT_BYTES + 1 - len(raw))
        if len(raw) > MAX_SNAPSHOT_BYTES:
            raise ValueError("snapshot exceeds the 16 MiB input limit")
    finally:
        if fd is not None:
            os.close(fd)
    return json.loads(
        raw.decode("utf-8-sig"),
        parse_float=parse_float,
        parse_constant=_invalid_constant,
        object_pairs_hook=_index_object,
    )


def snapshot_data(payload: dict) -> dict:
    """Validate retained rows and coverage once for local snapshot consumers.

    A complete collection and a display subset are separate facts. All coverage
    and timestamps are declarations in the supplied file, not authentication.
    Input ordering and the existing formatter's return shape remain unchanged.
    """
    if not isinstance(payload, dict):
        raise ValueError("supply an index object or browse --report, not a bare row list")
    if ("bounties" in payload) == ("rows" in payload):
        raise ValueError("snapshot must contain exactly one of bounties or rows")
    is_report = "rows" in payload
    rows = payload["rows" if is_report else "bounties"]
    if not isinstance(rows, list):
        raise ValueError("snapshot rows must be a list")
    # Validate the whole supplied set, not merely the rows selected for preview.
    rows = _normalized_bounties(rows)
    offline = payload.get("mode") == "offline"
    metadata = payload.get("source") if offline else payload
    if not isinstance(metadata, dict):
        raise ValueError("offline snapshot source must be an object")
    selection = None
    if offline:
        if not is_report or metadata.get("kind") not in {"browse_report", "cached_index"}:
            raise ValueError("offline snapshot must retain its original source kind and rows")
        collected = _snapshot_count(metadata, "collected_count")
        filtered = _snapshot_count(metadata, "filtered_count")
        retained = _snapshot_count(metadata, "rows_in_snapshot")
        omitted = _snapshot_count(metadata, "omitted_from_snapshot")
        matching = _snapshot_count(payload, "filtered_count")
        displayed = _snapshot_count(payload, "displayed_count")
        limit = _snapshot_count(payload, "display_limit")
        if (not collected >= filtered >= retained or filtered - retained != omitted
                or displayed != len(rows) or displayed != min(matching, limit)):
            raise ValueError("offline snapshot row counts are inconsistent")
        # The first shipped offline envelope had no selection counters. It can
        # only represent one local stage, whose counts follow from its source.
        selection = payload.get("selection")
        if selection is None:
            selection = {"input_count": retained,
                         "filtered_out_count": retained - matching,
                         "omitted_by_limit_count": matching - displayed,
                         "prior_filtered_out_count": 0,
                         "prior_omitted_by_limit_count": 0}
        if not isinstance(selection, dict):
            raise ValueError("offline snapshot selection must be an object")
        selection = {key: _snapshot_count(selection, key) for key in (
            "input_count", "filtered_out_count", "omitted_by_limit_count",
            "prior_filtered_out_count", "prior_omitted_by_limit_count")}
        if (selection["input_count"] + selection["prior_filtered_out_count"]
                + selection["prior_omitted_by_limit_count"] != retained
                or selection["input_count"] - selection["filtered_out_count"] != matching
                or matching - selection["omitted_by_limit_count"] != displayed):
            raise ValueError("offline snapshot selection counts are inconsistent")
    elif is_report:
        collected = _snapshot_count(payload, "collected_count")
        filtered = _snapshot_count(payload, "filtered_count")
        displayed = _snapshot_count(payload, "displayed_count")
        if displayed != len(rows) or not collected >= filtered >= displayed:
            raise ValueError("browse snapshot row counts are inconsistent")
        if not isinstance(payload.get("complete"), bool):
            raise ValueError("browse snapshot complete must be a boolean")
        retained = displayed
    else:
        collected = _snapshot_count(payload, "total_count")
        if collected != len(rows):
            raise ValueError("index total_count must match its row list")
        filtered = retained = displayed = len(rows)

    complete = metadata.get("complete")
    if complete is not None and not isinstance(complete, bool):
        raise ValueError("snapshot complete must be a boolean when supplied")
    if offline and metadata["kind"] == "browse_report" and not isinstance(complete, bool):
        raise ValueError("offline browse source complete must be a boolean")
    updated_at = metadata.get("updated_at")
    _parse_index_timestamp(updated_at)
    started_at = metadata.get("started_at")
    if started_at is not None:
        started = _parse_index_timestamp(started_at)
        if started > _parse_index_timestamp(updated_at):
            raise ValueError("snapshot collection interval ends before it starts")
    coverage = "reported_complete" if complete is True else "partial" if complete is False else "unspecified"
    if offline and metadata.get("coverage") != coverage:
        raise ValueError("offline snapshot coverage disagrees with its source")
    result = {
        "source": {
            "kind": metadata["kind"] if offline else "browse_report" if is_report else "cached_index",
            "coverage": coverage,
            "complete": complete,
            "started_at": started_at,
            "updated_at": updated_at,
            "collected_count": collected,
            "filtered_count": filtered,
            "rows_in_snapshot": retained,
            "rows_selected": len(rows),
            "omitted_from_snapshot": filtered - retained,
            "preview_limit": None,
            "note": (
                "Retained file only; no live read, freshness check, source authentication, "
                "claim, payment or posting. Reported collection coverage is not display "
                "coverage. Zero selected rows do not establish an empty live queue."
            ),
        },
        "rows": rows,
    }
    if offline:
        for key in ("repositories", "rate_limited", "retry_after_seconds", "rate_limit_reset_at"):
            if key in metadata:
                result["source"][key] = metadata[key]
        result["selection"] = selection
    return result


def format_snapshot(payload: dict, limit: int = 10) -> dict:
    """Preview an existing index or browse report without upgrading its claims."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 0 <= limit <= 1000:
        raise ValueError("preview limit must be between 0 and 1000")
    snapshot = snapshot_data(payload)
    selected = snapshot["rows"][:limit]
    source = snapshot["source"]
    source["rows_selected"] = len(selected)
    source["preview_limit"] = limit
    result = {"source": source, "previews": _format_validated_announcement(selected)}
    if "selection" in snapshot:
        source["rows_in_file"] = len(snapshot["rows"])
        result["selection"] = snapshot["selection"]
    return result


def main(argv=None) -> int:
    """Offline preview entrypoint. Never invokes the publication dispatcher."""
    parser = argparse.ArgumentParser(description="Preview one retained bounty snapshot without network access")
    parser.add_argument("--index", required=True, help="Saved index JSON or live/offline concierge browse report JSON")
    parser.add_argument("--format", choices=("json", "short", "medium", "long"), default="json")
    parser.add_argument("--limit", type=int, default=10, help="Select the first 0-1000 rows in retained order (default: 10)")
    args = parser.parse_args(argv)
    if not 0 <= args.limit <= 1000:
        parser.error("--limit must be between 0 and 1000")
    try:
        result = format_snapshot(_read_snapshot(args.index), limit=args.limit)
    except (OSError, UnicodeError, ValueError, TypeError, RecursionError) as exc:
        print("error: " + _single_line_text(exc), file=sys.stderr)
        return 1
    source = result["source"]
    if args.format == "json":
        print(json.dumps(result, indent=2, ensure_ascii=True))
    else:
        print(f"Retained snapshot: {source['kind']}; collection coverage: {source['coverage']}")
        print(f"Updated: {source['updated_at']}; started: {source['started_at'] or 'not supplied'}")
        print(
            f"Collected: {source['collected_count']}; filtered: {source['filtered_count']}; "
            f"rows in file: {source.get('rows_in_file', source['rows_in_snapshot'])}; "
            f"selected: {source['rows_selected']}"
        )
        if "selection" in result:
            selection = result["selection"]
            print(
                f"Original snapshot retained {source['rows_in_snapshot']} rows, with "
                f"{source['omitted_from_snapshot']} source display omissions; local filters "
                f"excluded {selection['prior_filtered_out_count'] + selection['filtered_out_count']}; "
                f"local display limits omitted "
                f"{selection['prior_omitted_by_limit_count'] + selection['omitted_by_limit_count']}."
            )
        print(source["note"])
        print()
        print(result["previews"][args.format] or "No candidate rows selected.")
    return 2 if source["complete"] is False else 0


def post_announcement(platform: str, content: str, platform_config: dict) -> dict:
    """Post content through an explicitly requested existing platform handler.

    Returns {"ok": bool, "url": str, "error": str}. Formatting alone never
    invokes this function. The caller retains publication authorization.
    """
    try:
        handler = _PLATFORM_HANDLERS.get(platform)
        if handler is None:
            return {"ok": False, "url": "", "error": f"Unknown platform: {platform}"}
        return handler(content, platform_config)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "url": "", "error": str(exc)}


def _post_moltbook(content: str, cfg: dict) -> dict:
    import requests

    api_key = cfg.get("api_key", "")
    submolt = cfg.get("submolt", "rustchain")
    title = cfg.get("title", "Open RustChain Bounties")
    resp = requests.post(
        "https://www.moltbook.com/api/v1/posts",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"content": content, "title": title, "submolt_name": submolt},
        timeout=30,
    )
    data = resp.json() if resp.ok else {}
    url = data.get("url", "")
    return {"ok": resp.ok, "url": url, "error": "" if resp.ok else resp.text}


def _post_stub(content: str, cfg: dict) -> dict:
    """Placeholder for platforms not yet wired up."""
    return {"ok": False, "url": "", "error": "Platform handler not yet implemented."}


_PLATFORM_HANDLERS = {
    "moltbook": _post_moltbook,
    "4claw": _post_stub,
    "agentchan": _post_stub,
    "devto": _post_stub,
    "twitter": _post_stub,
}


if __name__ == "__main__":
    raise SystemExit(main())
