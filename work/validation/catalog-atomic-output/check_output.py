"""Focused offline acceptance for the existing catalog CLI's --output option."""
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from concierge import bountyhub_catalog as catalog


class Response:
    status_code = 200
    headers = {}

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload

    def close(self):
        pass


class CatalogTransport:
    """Only the public page boundary is controlled; real catalog code executes."""
    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def get(self, url, **kwargs):
        assert url == catalog.API
        return Response({"data": [{
            "id": "11111111-1111-1111-1111-111111111111",
            "repositoryFullName": "example/project", "issueNumber": 1,
            "htmlURL": "https://github.com/example/project/issues/1",
            "title": "Synthetic retained-checkpoint fixture", "issueState": "open",
            "assignmentType": "OPEN", "assignee": None,
            "claimed": False, "retracted": False, "solved": False,
            "isFrozen": False, "totalAmount": "50.00",
        }], "hasNextPage": False})


def invoke(args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = catalog.main(args)
    return code, out.getvalue(), err.getvalue()


def main():
    checks = []
    with TemporaryDirectory() as directory, patch(
        "requests.sessions.Session.request", side_effect=AssertionError("network forbidden")
    ):
        root = Path(directory)
        snapshot = root / "catalog.json"
        # The real collector creates a bounded partial report, including a pending
        # detail, without network or a funding/claim/payment assertion.
        with patch.object(catalog.requests, "Session", CatalogTransport):
            code, out, err = invoke([
                "collect", "--max-pages", "1", "--max-details", "0",
                "--min-funded-usd", "15.00", "--output", str(snapshot),
            ])
        assert code == 2 and out == "" and "PARTIAL" in err
        original = snapshot.read_bytes()
        retained = json.loads(original)
        assert retained["listing_count"] == 1 and retained["requests_made"] == 1
        checks.append("collect preserves partial report and exit 2")

        code, out, err = invoke([
            "resume", str(snapshot), "--max-details", "0", "--output", str(snapshot),
        ])
        resumed = json.loads(snapshot.read_bytes())
        assert code == 2 and out == "" and "PARTIAL" in err
        assert resumed["resume"]["source_sha256"] == hashlib.sha256(json.dumps(
            retained, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()).hexdigest()
        assert resumed["resume"]["requests_made"] == 0
        assert resumed["completed_at"] == retained["completed_at"]
        assert resumed["listings"] == retained["listings"]
        checks.append("same-path resume reads old checkpoint and preserves observation")

        code, default_out, default_err = invoke(["targets", str(snapshot)])
        target = root / "targets.json"
        file_code, out, file_err = invoke(["targets", str(snapshot), "--output", str(target)])
        assert (file_code, out, file_err) == (code, "", default_err)
        assert target.read_bytes() == default_out.encode()
        assert json.loads(default_out) == {"candidates": []}
        checks.append("targets output bytes, diagnostics and exit match stdout")

        before = snapshot.read_bytes()
        for operation in ("fsync", "replace"):
            with patch.object(catalog.os, operation, side_effect=OSError("injected write failure")):
                code, out, _ = invoke([
                    "resume", str(snapshot), "--max-details", "0", "--output", str(snapshot),
                ])
            assert code == 2 and out == "" and snapshot.read_bytes() == before
            assert list(root.glob(".*.tmp")) == []
            checks.append(f"{operation} failure preserves old checkpoint and cleans stage")

        with patch.object(catalog.os, "fsync", side_effect=KeyboardInterrupt):
            try:
                invoke(["resume", str(snapshot), "--max-details", "0", "--output", str(snapshot)])
            except KeyboardInterrupt:
                pass
            else:
                raise AssertionError("interrupt was swallowed")
        assert snapshot.read_bytes() == before and list(root.glob(".*.tmp")) == []
        checks.append("interrupt before replacement preserves old checkpoint")

        invalid = root / "invalid.json"
        invalid.write_text("not JSON")
        assert invoke(["resume", str(invalid), "--output", str(snapshot)])[0] == 2
        assert snapshot.read_bytes() == before
        checks.append("invalid input does not replace existing output")

        code, out, _ = invoke(["targets", str(snapshot), "--output", str(root / "missing" / "out.json")])
        assert code == 2 and out == "" and snapshot.read_bytes() == before
        checks.append("missing output parent returns failure without source mutation")

        complete = dict(resumed, complete=True, details_complete=True, listings=[], listing_count=0)
        # A valid zero-listing complete capture exercises exit 0 with real selector.
        complete["shortlist"] = catalog.select_targets(complete, "15.00")
        snapshot.write_text(json.dumps(complete))
        code, out, _ = invoke(["targets", str(snapshot), "--output", str(target)])
        assert code == 0 and out == "" and json.loads(target.read_bytes()) == {"candidates": []}
        checks.append("complete target export retains exit 0")

        exact = {"unicode": "café", "nested": [1, None, False]}
        with redirect_stdout(io.StringIO()) as stdout:
            catalog._emit_json(exact, None)
        catalog._emit_json(exact, target)
        assert target.read_bytes() == stdout.getvalue().encode()
        assert target.read_bytes().endswith(b"\n")
        checks.append("writer matches legacy JSON encoding and trailing newline")
    for check in checks:
        print("PASS", check)
    print(f"{len(checks)} focused checks passed; no live provider requests")


if __name__ == "__main__":
    main()
