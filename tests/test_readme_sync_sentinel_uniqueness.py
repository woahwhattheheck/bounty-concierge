# SPDX-License-Identifier: MIT
"""README sync must have one unambiguous generated-content boundary."""

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from concierge import readme_sync as rs


def _pair(body: str = "old") -> str:
    return f"{rs.START_MARKER}\n{body}\n{rs.END_MARKER}"


def test_duplicate_complete_sentinel_pairs_are_rejected():
    readme = f"intro\n{_pair('first')}\nmiddle\n{_pair('second')}\noutro"

    with pytest.raises(ValueError, match="exactly one"):
        rs.update_readme(readme, "replacement")

    assert "first" in readme and "second" in readme


@pytest.mark.parametrize("extra", [rs.START_MARKER, rs.END_MARKER])
def test_stray_extra_sentinel_is_rejected(extra):
    readme = f"intro\n{_pair()}\n{extra}\noutro"

    with pytest.raises(ValueError, match="exactly one"):
        rs.update_readme(readme, "replacement")


def test_single_reversed_pair_is_rejected():
    readme = f"intro\n{rs.END_MARKER}\nold\n{rs.START_MARKER}\noutro"

    with pytest.raises(ValueError, match="out of order"):
        rs.update_readme(readme, "replacement")


@pytest.mark.parametrize("marker", [rs.START_MARKER, rs.END_MARKER])
def test_generated_section_cannot_inject_sentinel(marker):
    readme = f"intro\n{_pair()}\noutro"

    with pytest.raises(ValueError, match="Generated README section"):
        rs.update_readme(readme, f"replacement\n{marker}\ncontent")
