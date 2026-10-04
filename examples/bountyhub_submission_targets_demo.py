# SPDX-License-Identifier: MIT
"""Exercise the existing BountyHub catalog -> batch parser without provider reads.

Run from the repository root:
    PYTHONPATH=. python examples/bountyhub_submission_targets_demo.py

For actual work, retain a JSON map whose keys are original owner/repo#number
identities and whose values are the existing four-field submission_target
records: repository, source_url, source_content_sha256, instruction_excerpt.
Use the source content and excerpt from the sponsor's actual issue instructions:

    python -m concierge.bountyhub_catalog targets catalog.json \
        --submission-target-map delivery-targets.json > shortlist.json
    python -m concierge.bounty_capture_batch shortlist.json \
        --output-dir ./new-capture-directory --json

This demo uses the real retained catalog and a clearly synthetic delivery record.
It proves plumbing only; its record is not sponsor evidence or a live work claim.
"""

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import hashlib
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import requests

from concierge.bounty_capture_batch import _shortlist
from concierge.bountyhub_catalog import main, select_targets


def run() -> dict:
    root = Path(__file__).resolve().parents[1]
    snapshot = root / "work/supply/bountyhub/2026-10-04-catalog-56f3.json"
    raw = snapshot.read_bytes()
    report = json.loads(raw)
    original = deepcopy(report)
    excerpt = "Synthetic offline example: deliver the implementation to fluxerapp/fluxer."
    record = {
        "repository": "fluxerapp/fluxer",
        "source_url": "https://github.com/fluxerapp/fluxer-meta/issues/5",
        "source_content_sha256": hashlib.sha256(excerpt.encode()).hexdigest(),
        "instruction_excerpt": excerpt,
    }
    target_map = {"FluxerApp/Fluxer-Meta#5": record}
    with patch.object(requests.sessions.Session, "request",
                      side_effect=AssertionError("offline example attempted a provider read")) as network:
        baseline = select_targets(report, "50.00")
        mapped = select_targets(report, "50.00", submission_targets=target_map)
        expected = deepcopy(baseline)
        fluxer = next(row for row in expected["targets"]
                      if row["repo"].casefold() == "fluxerapp/fluxer-meta" and row["number"] == 5)
        fluxer["submission_target"] = record
        if mapped != expected or report != original:
            raise AssertionError("mapping changed selection, provenance or the input catalog")

        with TemporaryDirectory() as temporary:
            mapping_path = Path(temporary) / "delivery-targets.json"
            mapping_path.write_text(json.dumps(target_map), encoding="utf-8")
            stdout, stderr = StringIO(), StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = main(["targets", str(snapshot), "--min-funded-usd", "50.00",
                             "--submission-target-map", str(mapping_path)])
            if code != 0 or "mapped_submission_targets=1" not in stderr.getvalue():
                raise AssertionError("mapped export did not complete with its mapping count")
            candidates, duplicates = _shortlist(json.loads(stdout.getvalue()))
            received = next(row for row in candidates if row["repo"] == "fluxerapp/fluxer-meta")
            if received["number"] != 5 or received["submission_target"] != record or duplicates:
                raise AssertionError("batch parser lost the original issue or delivery record")

        for invalid in (
            {"fluxerapp/fluxer-meta#3": record},
            {"fluxerapp/fluxer-meta#05": record},
            {**target_map, "fluxerapp/fluxer-meta#5": record},
        ):
            try:
                select_targets(report, "50.00", submission_targets=invalid)
            except ValueError:
                continue
            raise AssertionError("ambiguous or mismatched source record was accepted")
        if select_targets(report, "1000.00", submission_targets=target_map) != select_targets(report, "1000.00"):
            raise AssertionError("an unused map entry changed funding selection")

    return {
        "status": "PASS",
        "catalog_sha256": hashlib.sha256(raw).hexdigest(),
        "catalog_listing_count": len(report["listings"]),
        "selected_issues": len(candidates),
        "mapped_source_issue": "fluxerapp/fluxer-meta#5",
        "submission_repository": record["repository"],
        "mapping_record": "synthetic offline example, not sponsor evidence",
        "listing_associations_preserved": mapped["listing_ids_by_issue"] == baseline["listing_ids_by_issue"],
        "provider_requests": network.call_count,
    }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, sort_keys=True))
