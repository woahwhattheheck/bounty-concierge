from __future__ import annotations

from tests import legacy_revenue_closeout as _legacy


_original_get = _legacy.FakeSession.get


def _get_with_inline_default(self, url, *, headers=None, params=None, timeout=None):
    try:
        return _original_get(self, url, headers=headers, params=params, timeout=timeout)
    except AssertionError:
        if (
            "/pulls/" in url
            and url.endswith("/comments")
            and params == {"per_page": 100, "page": 1}
        ):
            return _legacy.FakeResponse([])
        raise


_legacy.FakeSession.get = _get_with_inline_default


class RevenueCloseoutTests(_legacy.RevenueCloseoutTests):
    def test_duplicate_items_reject_before_second_scan(self):
        session = _legacy.FakeSession(_legacy.routes())
        with self.assertRaisesRegex(_legacy.RevenueCloseoutInputError, "duplicate closeout item"):
            _legacy.build_closeout_queue([_legacy.item(), _legacy.item()], session=session)
        self.assertEqual(len(session.calls), 4)
