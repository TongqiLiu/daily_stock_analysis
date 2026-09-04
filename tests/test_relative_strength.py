from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pandas as pd

from src.agent.factory import get_tool_registry
from src.agent.skills.base import SkillManager
from src.agent.tools.analysis_tools import (
    _default_relative_strength_benchmark,
    _handle_analyze_relative_strength,
)


def _history(step: float, rows: int = 90) -> pd.DataFrame:
    end = pd.Timestamp(date.today()) - pd.offsets.BDay(1)
    dates = pd.bdate_range(end=end, periods=rows)
    return pd.DataFrame({
        "date": dates.strftime("%Y-%m-%d"),
        "close": [100.0 + index * step for index in range(len(dates))],
    })


def test_default_relative_strength_benchmark_is_market_specific():
    assert _default_relative_strength_benchmark("AAPL")[0] == "SPY"
    assert _default_relative_strength_benchmark("US.AAPL")[0] == "SPY"
    assert _default_relative_strength_benchmark("HK00700")[0] == "HK02800"
    assert _default_relative_strength_benchmark("00700.HK")[0] == "HK02800"
    assert _default_relative_strength_benchmark("600519")[0] == "510300"
    assert _default_relative_strength_benchmark("000001.SZ")[0] == "510300"


def test_relative_strength_uses_matched_windows_and_marks_missing_sector_partial():
    frames = {
        "AAPL": (_history(1.2), "stock_provider"),
        "SPY": (_history(0.5), "benchmark_provider"),
    }

    with patch(
        "src.services.history_loader.load_history_df",
        side_effect=lambda code, **kwargs: frames[code],
    ):
        result = _handle_analyze_relative_strength("AAPL")

    assert result["status"] == "partial"
    assert result["benchmark"]["code"] == "SPY"
    assert result["benchmark"]["direction"] == "outperforming"
    assert set(result["benchmark"]["windows"]) == {"5d", "20d", "60d"}
    assert result["benchmark"]["windows"]["20d"]["excess_return_pct"] > 0
    assert result["sector_benchmark"]["status"] == "missing"
    assert result["recommended_evidence_status"] == "partial"
    assert result["sources"] == {
        "stock": "stock_provider",
        "benchmark": "benchmark_provider",
        "sector_benchmark": None,
    }


def test_relative_strength_becomes_complete_with_verified_sector_benchmark():
    frames = {
        "AAPL": (_history(1.2), "stock_provider"),
        "SPY": (_history(0.5), "benchmark_provider"),
        "XLK": (_history(0.8), "sector_provider"),
    }

    with patch(
        "src.services.history_loader.load_history_df",
        side_effect=lambda code, **kwargs: frames[code],
    ):
        result = _handle_analyze_relative_strength(
            "AAPL",
            sector_benchmark_code="XLK",
        )

    assert result["status"] == "ok"
    assert result["sector_benchmark"]["code"] == "XLK"
    assert result["sector_benchmark"]["direction"] == "outperforming"
    assert result["recommended_evidence_status"] == "complete"


def test_relative_strength_rejects_stale_sector_benchmark():
    stale_sector = _history(0.8)
    stale_sector["date"] = (
        pd.to_datetime(stale_sector["date"]) - pd.Timedelta(days=30)
    ).dt.strftime("%Y-%m-%d")
    frames = {
        "AAPL": (_history(1.2), "stock_provider"),
        "SPY": (_history(0.5), "benchmark_provider"),
        "XLK": (stale_sector, "sector_provider"),
    }

    with patch(
        "src.services.history_loader.load_history_df",
        side_effect=lambda code, **kwargs: frames[code],
    ):
        result = _handle_analyze_relative_strength(
            "AAPL",
            sector_benchmark_code="XLK",
        )

    assert result["status"] == "partial"
    assert result["sector_benchmark"]["status"] == "unavailable"
    assert "过期" in result["sector_benchmark"]["reason"]
    assert result["recommended_evidence_status"] == "partial"


def test_relative_strength_is_registered_for_multi_strategy():
    assert "analyze_relative_strength" in set(get_tool_registry().list_names())

    manager = SkillManager()
    manager.load_builtin_strategies()
    skill = manager.get("multi_strategy_consensus")
    assert skill is not None
    assert "analyze_relative_strength" in skill.required_tools
