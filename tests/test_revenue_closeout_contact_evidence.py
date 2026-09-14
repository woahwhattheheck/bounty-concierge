from __future__ import annotations

from tests import legacy_revenue_closeout_contact_evidence as _legacy


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


class RevenueCloseoutContactEvidenceTests(_legacy.RevenueCloseoutContactEvidenceTests):
    pass
