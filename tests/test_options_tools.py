"""Regression tests for the read-only option strategy data tool."""

from src.agent.tools.options_tools import (
    _handle_get_option_strategy_analysis,
    _handle_get_option_strategy_snapshot,
    _futu_option_quote_records,
    _serialize_strategy_analysis,
    _serialize_option_quote,
)


def test_strategy_analysis_serializer_preserves_provider_combo_fields():
    result = _serialize_strategy_analysis({
        "code": "AAPL260918C/P200/205",
        "name": "AAPL Bull Call Spread",
        "option_strategy": "SPREAD",
        "bid1": 2.1,
        "ask1": 2.4,
        "max_profit": 260,
        "max_loss": -240,
        "breakeven_points": [202.4],
        "prob_of_profit": 54.2,
        "delta": 0.31,
        "theta": -0.02,
    })

    assert result["option_strategy"] == "SPREAD"
    assert result["bid1"] == 2.1
    assert result["ask1"] == 2.4
    assert result["max_profit"] == 260
    assert result["breakeven_points"] == [202.4]


def test_strategy_analysis_requires_futu_when_disabled(monkeypatch):
    monkeypatch.delenv("FUTU_ENABLED", raising=False)
    result = _handle_get_option_strategy_analysis(
        '[{"code":"US.AAPL260918C200000","action":"BUY","quantity":1}]',
        stock_code="US.AAPL",
    )

    assert result["status"] == "unavailable"
    assert result["source"] == "futu"
    assert "FUTU_ENABLED" in result["reason"]


def test_option_strategy_scope_accepts_provider_us_prefix_and_ignores_greek_tokens():
    from src.agent.factory import get_tool_registry
    from src.agent.stock_scope import extract_stock_codes, StockScope
    from src.agent.tools.execution import _guard_tool_stock_scope

    assert extract_stock_codes("分析 NFLX 的 IV、OI、PCR 和 Put") == ["NFLX"]
    assert _guard_tool_stock_scope(
        get_tool_registry(),
        "get_option_strategy_analysis",
        {"stock_code": "US.NFLX", "legs_json": "[]"},
        StockScope(expected_stock_code="NFLX", allowed_stock_codes={"NFLX"}),
    ) is None


def test_futu_option_quote_adapter_uses_provider_quote_fields():
    import pandas as pd

    class FakeContext:
        def get_option_quote(self, legs):
            assert [leg.code for leg in legs] == ["US.AAPL260918C200000"]
            return 0, pd.DataFrame([{
                "price": 3.2,
                "open_interest": 1200,
                "implied_volatility": 31.0,
                "delta": 0.42,
                "gamma": 0.018,
                "theta": -0.04,
                "option_type": "CALL",
                "strike_price": 200.0,
                "expire_time": "2026-09-18",
            }])

    result = _futu_option_quote_records(
        FakeContext(),
        [{"code": "US.AAPL260918C200000"}],
    )

    assert result[0]["symbol"] == "US.AAPL260918C200000"
    assert result[0]["gamma"] == 0.018
    assert result[0]["open_interest"] == 1200
    assert result[0]["implied_volatility"] == 31


def test_option_tool_never_fabricates_when_no_provider_is_configured(monkeypatch):
    monkeypatch.delenv("LONGBRIDGE_APP_KEY", raising=False)
    monkeypatch.delenv("LONGBRIDGE_APP_SECRET", raising=False)
    monkeypatch.delenv("LONGBRIDGE_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("LONGBRIDGE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("FUTU_ENABLED", raising=False)

    result = _handle_get_option_strategy_snapshot("US.AAPL")

    assert result["status"] == "unavailable"
    assert result["source"] == "none"
    assert result["data_quality"]["greeks"] == "missing"
    assert result["data_quality"]["open_interest"] == "missing"
    assert "reason" in result and result["reason"]
    assert "gamma" not in result
    assert "pcr" not in result


def test_option_tool_rejects_unknown_source():
    result = _handle_get_option_strategy_snapshot("AAPL", source="invented")

    assert result == {"status": "invalid", "error": "source must be auto, longbridge, or futu"}


def test_option_quote_serializer_preserves_only_provider_fields():
    result = _serialize_option_quote(
        {
            "code": "US.AAPL260918C200000",
            "strike_price": 200,
            "option_type": "CALL",
            "implied_volatility": 0.31,
            "open_interest": 1200,
            "gamma": 0.018,
        }
    )

    assert result["symbol"] == "US.AAPL260918C200000"
    assert result["strike_price"] == 200
    assert result["implied_volatility"] == 0.31
    assert result["open_interest"] == 1200
    assert result["gamma"] == 0.018
    assert result["delta"] is None
    assert result["theta"] is None
