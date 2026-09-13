import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from concierge import bounty_index


def _bounty(title):
    return {
        "title": title,
        "reward_rtc": 10,
        "number": 7,
        "repo": "owner/repo",
        "skills": ["python"],
        "difficulty": "standard",
    }


def test_format_markdown_escapes_title_pipe_without_adding_a_cell():
    rendered = bounty_index.format_markdown([_bounty("Fix parser | preserve row")])

    assert "Fix parser \\| preserve row" in rendered
    assert len(rendered.splitlines()) == 3


def test_format_markdown_flattens_title_line_breaks_and_preserves_backslash_pipe():
    rendered = bounty_index.format_markdown([_bounty("First\r\nSecond \\| literal")])

    assert len(rendered.splitlines()) == 3
    assert "First Second \\\\\\| literal" in rendered
