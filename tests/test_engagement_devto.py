# SPDX-License-Identifier: MIT
"""Focused resilience tests for Dev.to engagement stats."""

import pathlib
import sys
from unittest.mock import MagicMock, patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from concierge.engagement import check_devto_articles


def _response(payload=None, *, json_error=None):
    response = MagicMock()
    response.raise_for_status.return_value = None
    if json_error is not None:
        response.json.side_effect = json_error
    else:
        response.json.return_value = payload
    return response


@patch("concierge.engagement.requests.get")
def test_malformed_json_fails_closed(mock_get):
    mock_get.return_value = _response(json_error=ValueError("malformed JSON"))

    assert check_devto_articles("test-key") == []


@patch("concierge.engagement.requests.get")
def test_non_list_json_fails_closed(mock_get):
    mock_get.return_value = _response({"title": "not an article list"})

    assert check_devto_articles("test-key") == []


@patch("concierge.engagement.requests.get")
def test_invalid_list_entries_are_skipped(mock_get):
    mock_get.return_value = _response(
        [
            {
                "title": "Useful article",
                "url": "https://dev.to/example/useful",
                "page_views_count": 12,
                "positive_reactions_count": 3,
            },
            "not-a-record",
            None,
        ]
    )

    assert check_devto_articles("test-key") == [
        {
            "title": "Useful article",
            "url": "https://dev.to/example/useful",
            "page_views": 12,
            "positive_reactions": 3,
        }
    ]
