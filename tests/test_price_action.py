"""Regression tests for the deterministic price-action evidence helper."""

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


def test_price_action_returns_breakout_candidate_without_confirming_second_entry():
    with patch(
        "src.services.history_loader.load_history_df",
        return_value=(_daily_bars(), "test"),
    ):
        result = _handle_analyze_price_action("US.TEST", days=120)

    assert result["status"] == "ok"
    assert result["signal"]["type"] == "bull_breakout_candidate"
    assert result["second_entry"]["status"] == "not_determined"
    assert result["context"]["state"] in {"bull_trend", "transition"}
    assert result["levels"]["prior_20d_high"] < result["levels"]["current_price"]


def test_price_action_identifies_failed_breakout_candidate():
    with patch(
        "src.services.history_loader.load_history_df",
        return_value=(_daily_bars(failed_breakout=True), "test"),
    ):
        result = _handle_analyze_price_action("US.TEST")

    assert result["status"] == "ok"
    assert result["signal"]["type"] == "failed_bull_breakout_candidate"


def test_price_action_is_registered_for_multi_strategy():
    assert "analyze_price_action" in set(get_tool_registry().list_names())
