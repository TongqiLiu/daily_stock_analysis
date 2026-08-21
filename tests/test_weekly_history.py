from __future__ import annotations

import pandas as pd

from data_provider.futu_fetcher import _to_futu_code
from src.agent.tools.data_tools import (
    _aggregate_daily_to_weekly,
    _handle_get_weekly_history,
)


def test_futu_code_normalization_accepts_prefix_form():
    assert _to_futu_code("US.INTC") == "US.INTC"
    assert _to_futu_code("INTC") == "US.INTC"


def _daily_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"date": "2026-08-03", "open": 10, "high": 12, "low": 9, "close": 11, "volume": 100, "amount": 1000},
            {"date": "2026-08-04", "open": 11, "high": 13, "low": 10, "close": 12, "volume": 120, "amount": 1200},
            {"date": "2026-08-10", "open": 12, "high": 14, "low": 11, "close": 13, "volume": 140, "amount": 1400},
            {"date": "2026-08-11", "open": 13, "high": 15, "low": 12, "close": 14, "volume": 160, "amount": 1600},
        ]
    )


def test_daily_aggregation_preserves_weekly_ohlcv_semantics():
    weekly = _aggregate_daily_to_weekly(_daily_frame())

    assert len(weekly) == 2
    assert weekly.iloc[0]["open"] == 10
    assert weekly.iloc[0]["high"] == 13
    assert weekly.iloc[0]["low"] == 9
    assert weekly.iloc[0]["close"] == 12
    assert weekly.iloc[0]["volume"] == 220
    assert weekly.iloc[1]["open"] == 12
    assert weekly.iloc[1]["close"] == 14


def test_weekly_tool_prefers_native_futu(monkeypatch):
    class FakeFutu:
        def is_available_for_request(self, capability):
            assert capability == "weekly_data"
            return True

        def get_weekly_data(self, stock_code, weeks):
            assert stock_code == "US.INTC"
            assert weeks == 8
            return _daily_frame().iloc[[0, 2]].assign(date=["2026-08-03", "2026-08-10"])

        def close(self):
            pass

    monkeypatch.setattr("data_provider.futu_fetcher.FutuFetcher", FakeFutu)

    result = _handle_get_weekly_history("US.INTC", weeks=8)

    assert result["source"] == "FutuFetcher:weekly"
    assert result["fallback_reason"] is None
    assert result["period"] == "weekly"
    assert len(result["data"]) == 2


def test_weekly_tool_falls_back_to_daily_aggregation(monkeypatch):
    class UnavailableFutu:
        def is_available_for_request(self, capability):
            return False

        def close(self):
            pass

    monkeypatch.setattr("data_provider.futu_fetcher.FutuFetcher", UnavailableFutu)
    monkeypatch.setattr(
        "src.services.history_loader.load_history_df",
        lambda *args, **kwargs: (_daily_frame(), "db_cache"),
    )

    result = _handle_get_weekly_history("US.INTC", weeks=8)

    assert result["source"] == "daily_aggregate:db_cache"
    assert result["data_quality"] in {"complete", "partial"}
    assert len(result["data"]) == 2
