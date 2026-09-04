from __future__ import annotations

import sys
from types import SimpleNamespace

import pandas as pd

from data_provider.futu_fetcher import FutuFetcher


def _page(start_day: int, rows: int) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "time_key": f"2026-08-{start_day + index:02d} 10:00:00",
                "open": 100 + index,
                "high": 101 + index,
                "low": 99 + index,
                "close": 100.5 + index,
                "volume": 1000 + index,
            }
            for index in range(rows)
        ]
    )


def test_futu_intraday_paginates_and_returns_latest_bars(monkeypatch):
    first_page = _page(1, 3)
    second_page = _page(4, 3)
    calls = []

    class FakeContext:
        def request_history_kline(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return 0, first_page, "next-page"
            return 0, second_page, None

    fake_futu = SimpleNamespace(
        RET_OK=0,
        KLType=SimpleNamespace(K_15M="K_15M"),
        AuType=SimpleNamespace(QFQ="QFQ"),
    )
    monkeypatch.setitem(sys.modules, "futu", fake_futu)

    fetcher = FutuFetcher()
    monkeypatch.setattr(fetcher, "is_available_for_request", lambda _: True)
    monkeypatch.setattr(fetcher, "_get_ctx", lambda: FakeContext())

    result = fetcher.get_intraday_data("AAPL", timeframe="15m", bars=4)

    assert len(calls) == 2
    assert calls[0]["max_count"] == 1000
    assert "page_req_key" not in calls[0]
    assert calls[1]["page_req_key"] == "next-page"
    assert result["date"].tolist() == [
        "2026-08-03 10:00:00",
        "2026-08-04 10:00:00",
        "2026-08-05 10:00:00",
        "2026-08-06 10:00:00",
    ]
