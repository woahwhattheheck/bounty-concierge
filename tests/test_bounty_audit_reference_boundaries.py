import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from concierge import bounty_audit


def _pr(text: str) -> dict[str, str]:
    return {"title": text, "body": ""}


def test_references_issue_rejects_identifier_suffixed_lookalikes():
    lookalikes = (
        "Use brand color #42a5f5",
        "Discuss acme/widget#42abc in prose",
        "See https://github.com/acme/widget/issues/42abc",
        "Token #42_legacy is not an issue reference",
    )

    for text in lookalikes:
        assert not bounty_audit.references_issue(_pr(text), "acme/widget", 42), text


def test_references_issue_preserves_valid_reference_boundaries():
    references = (
        "Fix #42",
        "Fixes #42.",
        "Tracks acme/widget#42",
        "See https://github.com/acme/widget/issues/42",
        "See https://github.com/acme/widget/issues/42#issuecomment-123",
        "See https://github.com/acme/widget/issues/42/comments",
    )

    for text in references:
        assert bounty_audit.references_issue(_pr(text), "acme/widget", 42), text
