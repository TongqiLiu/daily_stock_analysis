"""Regression tests for the deterministic price-action evidence helper."""

from datetime import date, timedelta
from unittest.mock import patch

import pandas as pd

from src.agent.factory import get_tool_registry
from src.agent.tools.analysis_tools import _handle_analyze_price_action


def _daily_bars(*, failed_breakout: bool = False) -> pd.DataFrame:
    rows = []
    for index in range(60):
        close = 100.0 + index * 0.5
        high = close + 1.0
        low = close - 1.0
        if index == 59 and not failed_breakout:
            close = 132.0
            high = close + 1.0
            low = close - 1.0
        if index == 59 and failed_breakout:
            high = 131.0
            close = 128.0
        rows.append({
            "open": close - 0.4,
            "high": high,
            "low": low,
            "close": close,
            "volume": 1000,
        })
    return pd.DataFrame(rows)


def _intraday_bars() -> pd.DataFrame:
    session = date.today().isoformat()
    return pd.DataFrame([
        {
            "date": f"{session} 09:{30 + index:02d}:00",
            "open": 100.0 + index * 0.2,
            "high": 100.5 + index * 0.2,
            "low": 99.8 + index * 0.2,
            "close": 100.3 + index * 0.2,
            "volume": 1000 + index * 100,
        }
        for index in range(12)
    ])


def _daily_bars_with_impulse_origin() -> pd.DataFrame:
    rows = []
    for index in range(60):
        close = 100.0 + index * 0.15
        open_price = close - 0.2
        high = close + 0.5
        low = close - 0.5
        if index == 55:
            open_price = 106.0
            low = 105.5
            high = 112.5
            close = 112.0
        elif index > 55:
            close = 112.0 + (index - 55) * 0.3
            open_price = close - 0.2
            high = close + 0.5
            low = close - 0.5
        rows.append({
            "date": f"2026-06-{index + 1:02d}",
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": 1000,
        })
    return pd.DataFrame(rows)


def test_price_action_returns_breakout_candidate_without_confirming_second_entry():
    with patch(
        "src.services.history_loader.load_history_df",
        return_value=(_daily_bars(), "test"),
    ), patch(
        "src.services.intraday_history_loader.load_intraday_history",
        return_value=(_intraday_bars(), "test_intraday"),
    ):
        result = _handle_analyze_price_action("US.TEST", days=120)

    assert result["status"] == "ok"
    assert result["signal"]["type"] == "bull_breakout_candidate"
    assert result["second_entry"]["status"] == "not_determined"
    assert result["context"]["state"] in {"bull_trend", "transition"}
    assert result["levels"]["prior_20d_high"] < result["levels"]["current_price"]
    assert result["levels"]["atr14"] > 0
    assert result["risk_management"]["intraday_vwap"]["status"] == "ok"
    assert result["risk_management"]["intraday_vwap"]["available"] is True
    assert result["risk_management"]["intraday_vwap"]["source"] == "test_intraday"
    assert result["risk_management"]["intraday_vwap"]["as_of"].endswith("09:41:00")
    assert result["risk_management"]["intraday_vwap"]["upper_2sigma"] > result["risk_management"]["intraday_vwap"]["vwap"]


def test_price_action_identifies_failed_breakout_candidate():
    with patch(
        "src.services.history_loader.load_history_df",
        return_value=(_daily_bars(failed_breakout=True), "test"),
    ), patch(
        "src.services.intraday_history_loader.load_intraday_history",
        return_value=(None, "none"),
    ):
        result = _handle_analyze_price_action("US.TEST")

    assert result["status"] == "ok"
    assert result["signal"]["type"] == "failed_bull_breakout_candidate"
    assert result["risk_management"]["intraday_vwap"]["status"] == "unavailable"
    assert result["risk_management"]["intraday_vwap"]["available"] is False
    assert any("VWAP" in gap for gap in result["data_gaps"])


def test_price_action_rejects_stale_intraday_vwap():
    stale_session = (date.today() - timedelta(days=10)).isoformat()
    stale_bars = _intraday_bars().assign(
        date=lambda frame: frame["date"].str.replace(
            date.today().isoformat(),
            stale_session,
            regex=False,
        )
    )
    with patch(
        "src.services.history_loader.load_history_df",
        return_value=(_daily_bars(), "test"),
    ), patch(
        "src.services.intraday_history_loader.load_intraday_history",
        return_value=(stale_bars, "stale_provider"),
    ):
        result = _handle_analyze_price_action("US.TEST")

    intraday_vwap = result["risk_management"]["intraday_vwap"]
    assert intraday_vwap["status"] == "unavailable"
    assert intraday_vwap["session_age_days"] == 10
    assert "过期" in intraday_vwap["reason"]


def test_price_action_returns_structure_stop_and_trend_bar_trailing_levels():
    with patch(
        "src.services.history_loader.load_history_df",
        return_value=(_daily_bars_with_impulse_origin(), "test"),
    ), patch(
        "src.services.intraday_history_loader.load_intraday_history",
        return_value=(_intraday_bars(), "test_intraday"),
    ):
        result = _handle_analyze_price_action("US.TEST")

    invalidation = result["risk_management"]["long_invalidation"]
    trend_bar = result["risk_management"]["trend_bar_trailing"]
    assert invalidation["status"] == "available"
    assert any(
        candidate["label"] == "强势突破起点"
        for candidate in result["risk_management"]["important_low_candidates"]
    )
    assert invalidation["buffer_1_0_atr"] < invalidation["buffer_0_5_atr"]
    assert invalidation["buffer_0_5_atr"] < invalidation["key_low"]["price"]
    assert trend_bar["status"] == "available"
    assert trend_bar["close"] == 112.0


def test_price_action_is_registered_for_multi_strategy():
    assert "analyze_price_action" in set(get_tool_registry().list_names())
