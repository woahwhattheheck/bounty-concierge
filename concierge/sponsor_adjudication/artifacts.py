from __future__ import annotations

from .common import *
from .compiler import compile_manifest


def verify_report(report: Any) -> Dict[str, Any]:
    obj = _require_dict(report, where="report")
    _require_exact_keys(
        obj,
        [
            "schema_version", "program", "summary", "claim_units", "findings", "sponsor_events",
            "authority_ceiling", "sponsor_authority_binding", "report_sha256",
        ],
        where="report",
    )
    if type(obj["schema_version"]) is not int or obj["schema_version"] != OUTPUT_SCHEMA_VERSION:
        raise AdjudicationError("report schema_version mismatch")
    if obj["authority_ceiling"] != AUTHORITY_CEILING:
        raise AdjudicationError("report authority ceiling mismatch")
    digest = _require_hex64(obj["report_sha256"], where="report.report_sha256")
    core = {k: deepcopy(v) for k, v in obj.items() if k != "report_sha256"}
    expected = _sha256_obj(core)
    if digest != expected:
        raise AdjudicationError("report digest mismatch")
    summary = _require_dict(obj["summary"], where="report.summary")
    if summary.get("cash_recognized") is not False:
        raise AdjudicationError("report may not recognize cash")
    for idx, unit in enumerate(_require_list(obj["claim_units"], where="report.claim_units")):
        u = _require_dict(unit, where=f"report.claim_units[{idx}]")
        if u.get("status") not in UNIT_STATUSES or u.get("action") not in ACTIONS:
            raise AdjudicationError("report claim-unit status/action invalid")
        if u.get("cash_status") != "not_inferred":
            raise AdjudicationError("report claim-unit cash status escalated")
    for idx, row in enumerate(_require_list(obj["findings"], where="report.findings")):
        r = _require_dict(row, where=f"report.findings[{idx}]")
        if r.get("action") not in ACTIONS or r.get("cash_status") != "not_inferred":
            raise AdjudicationError("report finding authority escalated")
    return dict(obj)


def _csv_cell(value: Any) -> str:
    if value is None:
        text = ""
    elif isinstance(value, list):
        text = ";".join(str(v) for v in value)
    elif isinstance(value, bool):
        text = "true" if value else "false"
    else:
        text = str(value)
    if text[:1] in {"=", "+", "-", "@"}:
        return "'" + text
    return text


def _render_csv(report: Mapping[str, Any]) -> bytes:
    buf = io.StringIO(newline="")
    fields = [
        "claim_unit_id", "status", "action", "finding_ids", "submission_ids", "event_ids",
        "sponsor_received", "sponsor_verified", "reward_amount", "reward_currency", "collapsed_into",
        "first_sponsor_event_at", "last_sponsor_event_at", "cash_status",
    ]
    writer = csv.DictWriter(buf, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for row in report["claim_units"]:
        writer.writerow({field: _csv_cell(row.get(field)) for field in fields})
    return buf.getvalue().encode("utf-8")


def _md(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("`", "\\`").replace("\n", " ")


def _render_markdown(report: Mapping[str, Any]) -> bytes:
    program = report["program"]
    summary = report["summary"]
    lines = [
        "# Sponsor adjudication custody report",
        "",
        f"- Program: `{_md(program['program_id'])}`",
        f"- Sponsor: {_md(program['sponsor'])}",
        f"- Findings: {summary['finding_count']}",
        f"- Submissions: {summary['submission_count']}",
        f"- Sponsor events: {summary['sponsor_event_count']}",
        f"- Claim units: {summary['claim_unit_count']}",
        "- Cash recognized: **NO**",
        "",
        "## Claim units",
        "",
        "| Claim unit | Status | Action | Findings | Reward | Cash |",
        "|---|---|---|---:|---|---|",
    ]
    for unit in report["claim_units"]:
        reward = ""
        if unit["reward_amount"] is not None:
            reward = f"{unit['reward_amount']} {unit['reward_currency']}"
        lines.append(
            f"| {_md(unit['claim_unit_id'])} | {_md(unit['status'])} | {_md(unit['action'])} | "
            f"{len(unit['finding_ids'])} | {_md(reward)} | not inferred |"
        )
    lines.extend(["", "## Finding actions", "", "| Finding | Status | Action | Unit | Submissions |", "|---|---|---|---|---:|"])
    for finding in report["findings"]:
        lines.append(
            f"| {_md(finding['finding_id'])} | {_md(finding['status'])} | {_md(finding['action'])} | "
            f"{_md(finding['canonical_claim_unit_id'])} | {finding['submission_count']} |"
        )
    lines.extend([
        "",
        "## Authority ceiling",
        "",
        "This report is evidence custody only. It does not contact a sponsor, submit a claim, mutate a provider or wallet, initiate a payout, recognize payment, or recognize accounting revenue.",
        "",
        f"Report SHA-256: `{report['report_sha256']}`",
        "",
    ])
    return "\n".join(lines).encode("utf-8")


def build_artifacts(manifest: Any) -> Dict[str, bytes]:
    report = compile_manifest(manifest)
    report_bytes = _canonical_bytes(report)
    csv_bytes = _render_csv(report)
    md_bytes = _render_markdown(report)
    manifest_digest = _sha256_obj(manifest)
    binding = report["sponsor_authority_binding"]
    authority_manifest_digest = binding["manifest_sha256"] if isinstance(binding, dict) else None
    files = {
        "adjudication.json": report_bytes,
        "claim_units.csv": csv_bytes,
        "adjudication.md": md_bytes,
    }
    receipt_core = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "kind": "sponsor_adjudication_custody_receipt",
        "manifest_sha256": manifest_digest,
        "authority_manifest_sha256": authority_manifest_digest,
        "report_sha256": report["report_sha256"],
        "files": {name: _sha256_bytes(content) for name, content in sorted(files.items())},
        "authority_ceiling": deepcopy(AUTHORITY_CEILING),
    }
    receipt = deepcopy(receipt_core)
    receipt["receipt_sha256"] = _sha256_obj(receipt_core)
    files["receipt.json"] = _canonical_bytes(receipt)
    return files


def write_artifacts(manifest: Any, out_dir: os.PathLike[str] | str) -> Dict[str, str]:
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    artifacts = build_artifacts(manifest)
    result: Dict[str, str] = {}
    for name, content in artifacts.items():
        path = root / name
        tmp = root / f".{name}.tmp"
        with open(tmp, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        result[name] = _sha256_bytes(content)
    return result


def verify_artifacts(out_dir: os.PathLike[str] | str) -> Dict[str, Any]:
    root = Path(out_dir)
    receipt = load_strict(root / "receipt.json")
    obj = _require_dict(receipt, where="receipt")
    _require_exact_keys(
        obj,
        [
            "schema_version", "kind", "manifest_sha256", "authority_manifest_sha256", "report_sha256",
            "files", "authority_ceiling", "receipt_sha256",
        ],
        where="receipt",
    )
    if type(obj["schema_version"]) is not int or obj["schema_version"] != RECEIPT_SCHEMA_VERSION:
        raise AdjudicationError("receipt schema_version mismatch")
    if obj["kind"] != "sponsor_adjudication_custody_receipt":
        raise AdjudicationError("receipt kind mismatch")
    if obj["authority_ceiling"] != AUTHORITY_CEILING:
        raise AdjudicationError("receipt authority ceiling mismatch")
    if obj["authority_manifest_sha256"] is not None:
        _require_hex64(obj["authority_manifest_sha256"], where="receipt.authority_manifest_sha256")
    expected_receipt = _sha256_obj({k: deepcopy(v) for k, v in obj.items() if k != "receipt_sha256"})
    if _require_hex64(obj["receipt_sha256"], where="receipt.receipt_sha256") != expected_receipt:
        raise AdjudicationError("receipt digest mismatch")
    file_map = _require_dict(obj["files"], where="receipt.files")
    expected_names = {"adjudication.json", "claim_units.csv", "adjudication.md"}
    if set(file_map) != expected_names:
        raise AdjudicationError("receipt file set mismatch")
    for name in sorted(expected_names):
        expected = _require_hex64(file_map[name], where=f"receipt.files.{name}")
        actual = _sha256_bytes((root / name).read_bytes())
        if actual != expected:
            raise AdjudicationError(f"artifact digest mismatch: {name}")
    report = load_strict(root / "adjudication.json")
    verified = verify_report(report)
    if verified["report_sha256"] != obj["report_sha256"]:
        raise AdjudicationError("receipt/report digest binding mismatch")
    return dict(obj)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compile or verify sponsor adjudication custody evidence")
    sub = parser.add_subparsers(dest="command", required=True)
    compile_p = sub.add_parser("compile", help="compile a strict evidence manifest")
    compile_p.add_argument("manifest")
    compile_p.add_argument("--out-dir", required=True)
    verify_p = sub.add_parser("verify", help="verify an emitted artifact directory")
    verify_p.add_argument("out_dir")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "compile":
            manifest = load_strict(args.manifest)
            hashes = write_artifacts(manifest, args.out_dir)
            print(json.dumps({"ok": True, "files": hashes}, sort_keys=True, separators=(",", ":")))
            return 0
        receipt = verify_artifacts(args.out_dir)
        print(json.dumps({"ok": True, "receipt_sha256": receipt["receipt_sha256"]}, sort_keys=True, separators=(",", ":")))
        return 0
    except (AdjudicationError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True, separators=(",", ":")))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
