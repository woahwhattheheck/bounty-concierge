#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Compare the actual availability entrypoint using four retained fake responses."""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class Response:
    headers: dict[str, str] = {}

    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return copy.deepcopy(self.data)


class Session:
    def __init__(self, text: str, association: str, changed: bool):
        issue = dict(id=7, number=7, state="open", updated_at="2026-10-04T12:30:00Z",
                     comments=1, title="Example task", body="Implement the requested change.")
        comment = dict(id=71, created_at="2026-10-04T12:00:00Z",
                       updated_at="2026-10-04T12:00:00Z", author_association=association,
                       body=text, user=dict(login="example-maintainer", type="User"))
        second = copy.deepcopy(comment)
        if changed:
            second["body"] += " Updated after the first read."
        self.responses = [issue, [comment], [second], issue]
        self.calls: list[str] = []

    def get(self, url: str, **kwargs):
        expected = "https://api.github.com/repos/example/project/issues/7"
        index = len(self.calls)
        assert index < 4, "Unexpected extra request"
        assert url == expected + ("/comments" if index in (1, 2) else "")
        self.calls.append(url)
        return Response(self.responses[index])


def blob(path: Path):
    raw = path.read_bytes()
    return hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, default=Path("concierge/bounty_availability.py"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    modules = {"before": load(args.before, "availability_before"),
               "after": load(args.after, "availability_after")}
    later = "The submission was not accepted; after additional changes and a complete implementation review, the submission is now accepted"
    cases = [
        ("later_acceptance", later, "COLLABORATOR", False, "MAINTAINER_ACCEPTANCE_SIGNAL"),
        ("overlapping_acceptance", "The submission was not accepted; after reviewing the final revision carefully, the submission is accepted", "OWNER", False, "MAINTAINER_ACCEPTANCE_SIGNAL"),
        ("later_cancellation", "The bounty was not cancelled; following the sponsor decision documented today, the bounty is cancelled", "MEMBER", False, "MAINTAINER_CANCELLED_SIGNAL"),
        ("later_award", "The award was not awarded to @example; after completing the final detailed review today, the award goes to @example", "OWNER", False, "MAINTAINER_AWARD_SIGNAL"),
        ("later_capacity", "The slots are not full; after the final application has been processed by the project team, all slots are full", "OWNER", False, "MAINTAINER_CAP_CLOSED_SIGNAL"),
        ("negated_only", "The submission was not accepted", "OWNER", False, None),
        ("no_more", "No more submissions", "OWNER", False, "MAINTAINER_CAP_CLOSED_SIGNAL"),
        ("negated_stop", "Do not stop submissions", "OWNER", False, None),
        ("quoted", "> " + later, "OWNER", False, None),
        ("fenced", "```\n" + later + "\n```", "OWNER", False, None),
        ("untrusted_author", later, "NONE", False, None),
        ("changed_generation", later, "OWNER", True, "COMMENT_GENERATION_CHANGED"),
    ]
    rows = []
    for name, text, association, changed, expected in cases:
        row = {"name": name}
        for label, module in modules.items():
            session = Session(text, association, changed)
            receipt = module.inspect_bounty_availability("example/project", 7, "synthetic-token", session=session)
            assert len(session.calls) == 4
            assert "example-maintainer" not in json.dumps(receipt)
            assert text not in json.dumps(receipt)
            row[label] = {key: receipt[key] for key in ("disposition", "dispatch", "reason_code", "signal_codes")}
            row[label]["controlled_get_calls"] = len(session.calls)
        actual = row["after"]
        if expected is None:
            assert actual["dispatch"] is True and actual["signal_codes"] == [], name
        elif changed:
            assert actual["reason_code"] == expected and not actual["dispatch"], name
        else:
            assert actual["signal_codes"] == [expected] and not actual["dispatch"], name
        if name in {"later_acceptance", "overlapping_acceptance", "later_cancellation", "later_award", "later_capacity"}:
            assert row["before"]["dispatch"] is True, name
        else:
            assert row["before"] == row["after"], name
        rows.append(row)
    result = {"before_blob": blob(args.before), "after_blob": blob(args.after),
              "python": sys.version, "passed": len(rows), "results": rows,
              "limits": "Controlled responses through the real entrypoint, not live GitHub or a full suite. Classification remains syntactic, not payout evidence."}
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(f"PASS {len(rows)} cases: five missed terminal outcomes corrected; seven controls preserved; four controlled GETs each")


if __name__ == "__main__":
    main()
