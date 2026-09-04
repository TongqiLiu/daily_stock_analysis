from __future__ import annotations

import pandas as pd

from src.services.intraday_history_loader import load_intraday_history


def _intraday_frame(rows: int = 3) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "date": f"2026-08-28 09:{30 + index * 5:02d}:00",
                "open": 100 + index,
                "high": 101 + index,
                "low": 99 + index,
                "close": 100.5 + index,
                "volume": 1000 + index * 100,
            }
            for index in range(rows)
        ]
    )


def test_intraday_loader_prefers_longbridge(monkeypatch):
    expected = _intraday_frame(4)

    class FakeLongbridge:
        def is_available_for_request(self, capability):
            assert capability == "intraday_data"
            return True

        def fetch_intraday_candlesticks(self, code, start, end, period):
            assert code == "AAPL"
            return expected

    class UnexpectedFutu:
        def __init__(self):
            raise AssertionError("Futu should not be created when Longbridge succeeds")

    monkeypatch.setattr(
        "data_provider.longbridge_fetcher.LongbridgeFetcher",
        FakeLongbridge,
    )
    monkeypatch.setattr(
        "data_provider.futu_fetcher.FutuFetcher",
        UnexpectedFutu,
    )
    monkeypatch.setattr(
        "src.services.intraday_history_loader._map_timeframe_to_longbridge_period",
        lambda timeframe: timeframe,
    )

    result, source = load_intraday_history("AAPL", timeframe="5m", bars=3)

    assert source == "longbridge"
    assert len(result) == 3
    assert result.iloc[-1]["close"] == expected.iloc[-1]["close"]


def test_intraday_loader_falls_back_to_futu_and_closes_context(monkeypatch):
    expected = _intraday_frame(3)
    state = {"closed": False}

    class UnavailableLongbridge:
        def is_available_for_request(self, capability):
            return False

    class FakeFutu:
        def is_available_for_request(self, capability):
            assert capability == "intraday_data"
            return True

        def get_intraday_data(
            self,
            stock_code,
            timeframe,
            bars,
            start_date,
            end_date,
        ):
            assert stock_code == "US.AAPL"
            assert timeframe == "15m"
            assert bars == 160
            return expected

        def close(self):
            state["closed"] = True

    monkeypatch.setattr(
        "data_provider.longbridge_fetcher.LongbridgeFetcher",
        UnavailableLongbridge,
    )
    monkeypatch.setattr("data_provider.futu_fetcher.FutuFetcher", FakeFutu)

    result, source = load_intraday_history("US.AAPL", timeframe="15m", bars=160)

    assert source == "futu"
    assert result.equals(expected)
    assert state["closed"] is True


def test_intraday_loader_returns_none_when_all_providers_unavailable(monkeypatch):
    class UnavailableProvider:
        def is_available_for_request(self, capability):
            return False

        def close(self):
            pass

    monkeypatch.setattr(
        "data_provider.longbridge_fetcher.LongbridgeFetcher",
        UnavailableProvider,
    )
    monkeypatch.setattr(
        "data_provider.futu_fetcher.FutuFetcher",
        UnavailableProvider,
    )

    result, source = load_intraday_history("AAPL", timeframe="15m", bars=160)

    assert result is None
    assert source == "none"
