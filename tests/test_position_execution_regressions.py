"""Execution contracts, not a claim of trading profitability."""

import json
from datetime import date, datetime, timezone
from functools import partial
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest

from src.agent.runner import _render_multi_strategy_score_section
from src.agent.tools.analysis_tools import (
    MULTI_STRATEGY_SCORE_SPECS,
    _handle_calculate_multi_strategy_score,
    _handle_get_volume_analysis,
)
from src.core.trading_calendar import build_market_phase_context


def _score(structure):
    rows = [
        dict(strategy=key, signal="买入", strength="中", score=75,
             evidence_status="complete", reason="合成测试输入，非实盘信号")
        for key, _, _ in MULTI_STRATEGY_SCORE_SPECS
    ]
    return _handle_calculate_multi_strategy_score(
        json.dumps(rows), market_structure_json=json.dumps(structure),
    )


@pytest.mark.parametrize("structure,expected", [
    ({}, "unavailable"),
    ({"current_price": 77.6}, "partial"),
    ({"current_price": 77.6, "resistance_levels": [85.55]}, "partial"),
    ({"current_price": 77.6, "invalidation_level": 60.51}, "partial"),
    ({"current_price": 77.6, "invalidation_level": 60.51,
      "resistance_levels": [85.55]}, "wait"),
    ({"current_price": 75, "invalidation_level": 70,
      "resistance_levels": [75.37, 77.6]}, "wait"),
])
def test_high_score_cannot_bypass_add_gate(structure, expected):
    result = _score(structure)
    assert result["execution"]["status"] == expected
    assert result["actions"]["has_position"] == "hold"
    assert result["actions"]["no_position"] != "buy"
    assert result["position_plan"]["new_position_allowed"] is False
    rendered = _render_multi_strategy_score_section(result)
    assert "已有仓位管理" in rendered
    assert "缩量本身不能触发减仓" in rendered


def test_hard_invalidation_exit_survives_blocked_entry_plan():
    result = _score({"current_price": 60, "invalidation_level": 60.51,
                     "resistance_levels": [75.37]})
    assert result["actions"]["has_position"] == "sell"
    assert result["position_plan"]["existing_position_management"]["status"] == "exit"
    assert "已触发硬失效位" in _render_multi_strategy_score_section(result)


def test_rr_boundary_and_conditional_profit_references():
    # Exactly 1.5 is eligible; eligibility is not an executed trade.
    result = _score({"current_price": 100, "invalidation_level": 96,
                     "resistance_levels": [106]})
    plan = result["position_plan"]
    assert result["execution"]["reward_risk_ratio"] == 1.5
    assert plan["new_position_allowed"] is True
    assert plan["add_requires_reassessment"] is True
    assert [r["price"] for r in plan["take_profit_tranches"]] == [104, 108, None]
    assert all(r["reduce_pct"] is None for r in plan["take_profit_tranches"])
    assert "新结构止损" in plan["tranches"][1]["trigger"]


def _bars():
    dates = pd.bdate_range("2026-08-03", "2026-09-17")
    bars = pd.DataFrame({"date": dates, "close": range(100, 100 + len(dates)),
                         "volume": 1000.0})
    bars.loc[bars.index[-2], "volume"] = 2000
    bars.loc[bars.index[-1], "volume"] = 100  # unfinished session, not bearish evidence
    return bars


def _volume(bars, now, frozen=None):
    # Exercise the real exchange calendar and the real volume calculations.
    with patch("src.services.history_loader.load_history_df", return_value=(bars, "fixture")), \
         patch("src.services.history_loader.get_frozen_target_date", return_value=frozen), \
         patch("src.core.trading_calendar.build_market_phase_context",
               partial(build_market_phase_context, current_time=now)):
        return _handle_get_volume_analysis("RKLB")


def test_intraday_volume_uses_completed_session_and_prior_baseline():
    result = _volume(_bars(), datetime(2026, 9, 17, 17, 21, tzinfo=timezone.utc))
    assert result["status"] == "ok"
    assert result["data_as_of"] == "2026-09-16"
    assert result["excluded_uncompleted_or_future_bars"] == 1
    assert result["volume_ratio_vs_5d"] == 2.0
    assert result["avg_volume_5d"] == 1000


def test_postclose_volume_includes_completed_today():
    result = _volume(_bars(), datetime(2026, 9, 17, 21, tzinfo=timezone.utc))
    assert result["data_as_of"] == "2026-09-17"
    assert result["excluded_uncompleted_or_future_bars"] == 0
    assert result["avg_volume_5d"] == 1200


def test_frozen_history_does_not_consume_future_bars():
    result = _volume(_bars(), datetime(2026, 9, 17, 21, tzinfo=timezone.utc), date(2026, 9, 15))
    assert result["data_as_of"] == "2026-09-15"
    assert result["volume_ratio_vs_5d"] == 1.0
    assert result["volume_price_corr"] is None
    json.dumps(result, allow_nan=False)


def test_unknown_calendar_does_not_claim_volume_confirmation():
    phase = SimpleNamespace(phase=SimpleNamespace(value="unknown"), warnings=["calendar_error"])
    with patch("src.services.history_loader.load_history_df", return_value=(_bars(), "fixture")), \
         patch("src.core.trading_calendar.build_market_phase_context", return_value=phase):
        result = _handle_get_volume_analysis("RKLB")
    assert result["status"] == "unavailable"
    assert "volume_ratio_vs_5d" not in result


@pytest.mark.parametrize("invalid", ["missing", "malformed"])
def test_unverifiable_bar_dates_cannot_confirm_volume(invalid):
    bars = _bars()
    if invalid == "missing":
        bars = bars.drop(columns="date")
    else:
        bars["date"] = "invalid"
    result = _volume(bars, datetime(2026, 9, 17, 21, tzinfo=timezone.utc))
    assert result["status"] == "unavailable"
    assert "volume_ratio_vs_5d" not in result
