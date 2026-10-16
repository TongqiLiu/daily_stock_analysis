"""Exercise the tool boundary with unfinished and incomplete provider bars."""

from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest

from src.agent.tools.analysis_tools import (
    _handle_analyze_vcp_breakout_trader,
    _handle_analyze_vcp_h1_h2_buy,
)
from src.core.trading_calendar import get_trading_dates


HANDLERS = [_handle_analyze_vcp_breakout_trader, _handle_analyze_vcp_h1_h2_buy]


def _history():
    dates = pd.bdate_range(end="2026-06-23", periods=265)
    return pd.DataFrame([
        {"date": day, "open": 100 + i / 10, "high": 102 + i / 10,
         "low": 99 + i / 10, "close": 101 + i / 10, "volume": 1000.0}
        for i, day in enumerate(dates)
    ])


def _run(handler, frame, *, warnings=(), frozen=None, stock_code="AAPL"):
    phase = SimpleNamespace(
        phase=SimpleNamespace(value="intraday"), warnings=warnings,
        effective_daily_bar_date=date(2026, 6, 22),
    )
    with patch("src.services.history_loader.load_history_df", return_value=(frame, "provider")), \
         patch("src.core.trading_calendar.build_market_phase_context", return_value=phase), \
         patch("src.services.history_loader.get_frozen_target_date", return_value=frozen):
        return handler(stock_code)


def _session_history(market="us"):
    cutoff = date(2026, 6, 22)
    dates = get_trading_dates(market, cutoff - timedelta(days=800), cutoff)[-265:]
    return pd.DataFrame({"date": dates, "open": 100.0, "high": 102.0,
                         "low": 99.0, "close": 101.0, "volume": 1000.0})


@pytest.mark.parametrize("handler", HANDLERS)
def test_vcp_discards_unfinished_breakout_bar(handler):
    frame = _history()
    baseline = _run(handler, frame.iloc[:-1])
    frame.loc[frame.index[-1], ["high", "close", "volume"]] = [250, 249, 100000]
    actual = _run(handler, frame)
    assert actual["status"] == "ok"
    assert actual["as_of"] == "2026-06-22"
    assert actual["daily_bar_status"] == "completed"
    assert actual["excluded_unclosed_or_future_bars"] == 1
    assert actual["grade"] == baseline["grade"]
    assert actual["current_price"] == baseline["current_price"]
    assert actual["signals"] == baseline["signals"]


@pytest.mark.parametrize("handler", HANDLERS)
@pytest.mark.parametrize("bad_volume", [None, float("inf"), -1])
def test_vcp_missing_volume_is_unavailable_not_dry_volume(handler, bad_volume):
    frame = _history()
    frame.loc[frame.index[-4], "volume"] = bad_volume
    result = _run(handler, frame)
    assert result["status"] == "unavailable"
    assert "signals" not in result


@pytest.mark.parametrize("handler", HANDLERS)
def test_vcp_calendar_failure_does_not_confirm_daily_signal(handler):
    result = _run(handler, _history(), warnings=["calendar unavailable"])
    assert result["status"] == "unavailable"
    assert "grade" not in result


@pytest.mark.parametrize("handler", HANDLERS)
def test_vcp_frozen_date_excludes_later_provider_bars(handler):
    result = _run(handler, _history(), frozen=date(2026, 6, 21))
    assert result["status"] == "ok"
    # Sunday follows the Juneteenth holiday; Thursday is the last session.
    assert result["as_of"] == "2026-06-18"
    assert result["excluded_unclosed_or_future_bars"] == 3


@pytest.mark.parametrize("handler", HANDLERS)
def test_vcp_stale_history_cannot_produce_current_signal(handler):
    result = _run(handler, _history().iloc[:-3])
    assert result["status"] == "unavailable"
    assert result["expected_as_of"] == "2026-06-22"
    assert "signals" not in result


@pytest.mark.parametrize("handler", HANDLERS)
def test_vcp_conflicting_duplicate_is_rejected_in_either_order(handler):
    frame = _history().iloc[:-1].copy()
    conflict = frame.iloc[[-1]].copy()
    conflict.loc[:, ["open", "high", "low", "close"]] *= 2
    for combined in (pd.concat([frame, conflict]), pd.concat([conflict, frame])):
        assert _run(handler, combined)["status"] == "unavailable"
    identical = _run(handler, pd.concat([frame, frame.iloc[[-1]]]))
    assert identical["status"] == "ok"


@pytest.mark.parametrize("handler", HANDLERS)
@pytest.mark.parametrize("code,market,refill_code", [
    ("AAPL", "us", "AAPL"), ("AAPL.US", "us", "AAPL"),
    ("BRK.B", "us", "BRK.B"), ("600519", "cn", "600519"),
    ("sh600519", "cn", "600519"), ("SH.600519", "cn", "600519"),
    ("600519.SH", "cn", "600519"), ("HK.00700", "hk", "HK00700"),
    ("00700.HK", "hk", "HK00700"), ("hk00700", "hk", "HK00700"),
])
def test_vcp_uses_one_shared_identity_for_calendar_and_provider(handler, code, market, refill_code):
    phase = SimpleNamespace(phase=SimpleNamespace(value="intraday"), warnings=[],
                            effective_daily_bar_date=date(2026, 6, 22))
    with patch("src.services.history_loader.load_history_df", return_value=(_session_history(market), "fixture")) as load, \
         patch("src.core.trading_calendar.build_market_phase_context", return_value=phase) as calendar:
        result = handler(code)
    assert result["status"] == "ok"
    assert result["market"] == market
    assert result["normalized_code"] == refill_code
    assert result["as_of"] == "2026-06-22"
    assert load.call_args.args == (refill_code,)
    calendar.assert_called_once_with(market=market)


@pytest.mark.parametrize("handler", HANDLERS)
@pytest.mark.parametrize("code", ["US.AAPL", "SZ.600519"])
def test_vcp_does_not_invent_aliases_or_ignore_exchange_conflicts(handler, code):
    with patch("src.services.history_loader.load_history_df") as load:
        result = handler(code)
    assert result["status"] == "unavailable"
    assert "共享标的规则" in result["reason"]
    load.assert_not_called()


@pytest.mark.parametrize("handler", HANDLERS)
def test_vcp_holidays_and_weekends_cannot_pollute_price_or_volume_signals(handler):
    frame = _session_history()
    baseline = _run(handler, frame)
    # Both rows are outside NYSE sessions. Even malformed OHLCV in these rows
    # must neither create a breakout nor invalidate the real session history.
    fake_bars = pd.DataFrame({"date": pd.to_datetime(["2026-06-19", "2026-06-20"]),
                             "open": 100.0, "high": 999.0, "low": 1.0,
                             "close": 900.0, "volume": [None, 1000000.0]})
    result = _run(handler, pd.concat([frame, fake_bars]).sort_values("date"))
    assert result["status"] == "ok"
    assert result["as_of"] == "2026-06-22"
    assert result["excluded_non_session_bars"] == 2
    assert result["signals"] == baseline["signals"]
    assert result["current_price"] == baseline["current_price"]


@pytest.mark.parametrize("handler", HANDLERS)
def test_vcp_only_validates_the_last_requested_trading_sessions(handler):
    frame = _session_history()
    baseline = _run(handler, frame)
    frame.loc[frame.index[0], "volume"] = None
    old_conflict = frame.iloc[[1]].copy()
    old_conflict.loc[:, ["open", "high", "low", "close"]] *= 2
    result = _run(handler, pd.concat([old_conflict, frame]))
    assert result["status"] == "ok"
    assert result["signals"] == baseline["signals"]
    assert result["current_price"] == baseline["current_price"]


@pytest.mark.parametrize("handler", HANDLERS)
def test_vcp_identical_duplicates_do_not_replace_distinct_sessions_in_volume_window(handler):
    frame = _session_history()
    frame.loc[frame.index[-1], "volume"] = 0
    baseline = _run(handler, frame)
    result = _run(handler, pd.concat([frame] + [frame.iloc[[-1]]] * 25))
    assert result["status"] == "ok"
    assert result["signals"] == baseline["signals"]


@pytest.mark.parametrize("handler", HANDLERS)
@pytest.mark.parametrize("code,market", [("600519.SH", "cn"), ("HK.00700", "hk"), ("AAPL.US", "us")])
def test_vcp_valid_aliases_keep_historical_frozen_session_boundary(handler, code, market):
    result = _run(handler, _session_history(market), frozen=date(2026, 6, 21), stock_code=code)
    assert result["status"] == "ok"
    assert result["as_of"] == "2026-06-18"
    assert result["market"] == market


@pytest.mark.parametrize("handler", HANDLERS)
def test_vcp_missing_historical_session_calendar_remains_unavailable(handler):
    with patch("src.core.trading_calendar.get_trading_dates", return_value=None):
        result = _run(handler, _history())
    assert result["status"] == "unavailable"
    assert "历史日线的交易日" in result["reason"]
