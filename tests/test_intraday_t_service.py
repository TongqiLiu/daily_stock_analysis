import math
from unittest.mock import patch

import pandas as pd

from src.agent.tools.analysis_tools import _handle_analyze_intraday_t
from src.services.intraday_t_service import analyze_intraday_t


def _frame(closes: list[float], spread: float = 0.35) -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.date_range("2026-08-13 09:30", periods=len(closes), freq="3min"),
        "open": closes,
        "high": [value + spread for value in closes],
        "low": [value - spread for value in closes],
        "close": closes,
        "volume": [1000] * len(closes),
    })


def _strong_uptrend_fixture() -> pd.DataFrame:
    closes = [
        100 + index * 0.18 + math.sin(index * math.pi / 4) * 0.75
        for index in range(120)
    ]
    return _frame(closes)


def _bearish_fixture() -> pd.DataFrame:
    closes = [
        120 - index * 0.16 + math.sin(index * math.pi / 4) * 0.70
        for index in range(120)
    ]
    return _frame(closes)


def _range_high_sell_fixture() -> pd.DataFrame:
    closes = [
        100 + math.sin(index * math.pi / 5) * 1.1
        for index in range(120)
    ]
    closes.extend([
        100, 99, 98, 99, 101, 103, 104, 103, 101,
        99.5, 98.5, 99.5, 101.5, 103, 102, 101, 100.5, 100,
    ])
    return _frame(closes)


def _higher_low_reclaim_fixture() -> pd.DataFrame:
    closes = [
        100 + math.sin(index * math.pi / 5) * 0.6
        for index in range(100)
    ]
    closes.extend([
        100.4, 99.8, 99.5, 99.8, 100.4, 100.8, 100.4,
        100.0, 99.75, 99.9, 100.2, 100.55, 100.7,
    ])
    return _frame(closes, spread=0.4)


def test_intraday_t_requires_enough_three_minute_bars():
    result = analyze_intraday_t(_strong_uptrend_fixture().tail(60))

    assert result["status"] == "insufficient_data"
    assert "至少需要80根" in result["missing"][0]


def test_intraday_t_drops_still_forming_latest_bar():
    fixture = _strong_uptrend_fixture().tail(90).reset_index(drop=True)
    fixture["date"] = pd.date_range(
        "2026-08-13 07:33",
        periods=90,
        freq="3min",
    )

    result = analyze_intraday_t(
        fixture,
        analysis_time=pd.Timestamp("2026-08-13 12:01:00"),
    )

    assert result["status"] == "ok"
    assert result["received_bars"] == 90
    assert result["bars"] == 89
    assert result["dropped_unclosed_bar"] is True
    assert result["as_of"] == "2026-08-13 11:57:00"
    assert "已自动剔除" in result["risk"]["closed_bar_confirmation"]


def test_intraday_t_avoids_high_sell_in_steep_hh_hl_trend():
    result = analyze_intraday_t(_strong_uptrend_fixture())

    assert result["status"] == "ok"
    assert result["environment"]["regime"] == "strong_uptrend"
    assert result["structure"]["highs"] == "HH"
    assert result["structure"]["lows"] == "HL"
    assert result["signals"]["high_sell"]["status"] == "avoid"
    assert result["plan"]["action"] == "hold_core_reduce_t_frequency"
    assert result["plan"]["t_position"] == "10%-20%（取下限）"


def test_intraday_t_blocks_mechanical_buyback_in_bearish_structure():
    result = analyze_intraday_t(_bearish_fixture())

    assert result["environment"]["regime"] == "bearish"
    assert result["structure"]["highs"] == "LH"
    assert result["structure"]["lows"] == "LL"
    assert result["signals"]["low_buy"]["status"] == "blocked"
    assert result["plan"]["action"] == "high_sell_only"


def test_intraday_t_confirms_range_high_sell_and_atr_spacing():
    result = analyze_intraday_t(_range_high_sell_fixture())

    high_sell = result["signals"]["high_sell"]
    plan = result["plan"]
    assert result["environment"]["regime"] == "range"
    assert high_sell["status"] == "ready"
    assert high_sell["failed_second_push"] is True
    assert high_sell["pullback_between_highs"] is True
    assert high_sell["ema20_lost"] is True
    assert plan["action"] == "sell_t_position"
    assert plan["minimum_1atr_met"] is True
    assert plan["preferred_1_5atr_met"] is True
    assert plan["t_position"] == "20%-30%"
    assert plan["sell_reference"] == high_sell["confirmation_reference"]
    assert plan["sell_reference"] < high_sell["failed_high"]["price"]


def test_intraday_t_requires_support_higher_low_and_ema20_reclaim():
    result = analyze_intraday_t(_higher_low_reclaim_fixture())

    low_buy = result["signals"]["low_buy"]
    assert result["structure"]["lows"] == "HL"
    assert low_buy["status"] == "ready"
    assert low_buy["support_touched"] is True
    assert low_buy["ema20_reclaimed"] is True
    assert result["plan"]["action"] == "buyback_t_position"


def test_intraday_t_tool_loads_260_three_minute_bars():
    fixture = _range_high_sell_fixture()
    with patch(
        "src.services.intraday_history_loader.load_intraday_history",
        return_value=(fixture, "longbridge"),
    ) as loader:
        result = _handle_analyze_intraday_t("KORU")

    loader.assert_called_once_with("KORU", timeframe="3m", bars=260)
    assert result["status"] == "ok"
    assert result["code"] == "KORU"
    assert result["source"] == "longbridge"
    assert result["timeframe"] == "3m"
    assert result["requested_bars"] == 260
