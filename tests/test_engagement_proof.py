# SPDX-License-Identifier: MIT
"""Focused output-safety tests for engagement proof Markdown."""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from concierge.engagement import generate_engagement_proof


def test_regular_engagement_proof_is_unchanged():
    assert generate_engagement_proof(
        "Dev.to",
        "shared article",
        "https://dev.to/example/useful",
    ) == (
        "**Engagement Proof**\n\n"
        "- **Platform:** Dev.to\n"
        "- **Action:** shared article\n"
        "- **Proof:** [https://dev.to/example/useful](https://dev.to/example/useful)\n\n"
        "Requesting payout per the bounty terms."
    )


def test_untrusted_fields_cannot_forge_markdown_lines():
    rendered = generate_engagement_proof(
        "Dev.to\n**forged**",
        "shared [post]\r\n> forged",
        "https://example.com/proof)\n- forged",
    )

    assert "- **Platform:** Dev.to\\x0a\\*\\*forged\\*\\*" in rendered
    assert "- **Action:** shared \\[post\\]\\x0d\\x0a\\> forged" in rendered
    assert "\n**forged**" not in rendered
    assert "\n> forged" not in rendered
    assert "\n- forged" not in rendered
    assert (
        "- **Proof:** [https://example.com/proof)\\x0a- forged]"
        "(https://example.com/proof%29%0A-%20forged)"
    ) in rendered


def test_link_label_delimiters_are_escaped_and_destination_is_encoded():
    rendered = generate_engagement_proof(
        "Moltbook",
        "shared _update_",
        "https://example.com/a](forged)",
    )

    assert "- **Action:** shared \\_update\\_" in rendered
    assert (
        "- **Proof:** [https://example.com/a\\](forged)]"
        "(https://example.com/a%5D%28forged%29)"
    ) in rendered


def test_printable_unicode_text_is_preserved():
    rendered = generate_engagement_proof(
        "社区",
        "共享 ✅",
        "https://example.com/proof",
    )

    assert "- **Platform:** 社区" in rendered
    assert "- **Action:** 共享 ✅" in rendered
