"""DCF data-contract regressions; exercise actual collection and calculation."""
import json
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd
import pytest

from src.services.dcf_valuation_service import (
    DCFValuationService, _ScenarioInput, _compute_dcf, _normalize_growth, _statement_fcf, _to_yf_symbol,
)


def snapshot(**overrides):
    return {
        "currentPrice": 10.0, "regularMarketTime": datetime.now(timezone.utc).timestamp(),
        "marketCap": 1_000.0, "sharesOutstanding": 100.0, "freeCashflow": 100.0,
        "totalCash": 80.0, "totalDebt": 20.0, "revenueGrowth": 0.0,
        "currency": "USD", "financialCurrency": "USD", "quoteType": "EQUITY",
        "sector": "Industrials", **overrides,
    }


def annual(value=100.0, days_ago=150):
    return pd.DataFrame(
        {pd.Timestamp(datetime.now(timezone.utc).date() - timedelta(days=days_ago)): [value]},
        index=["Free Cash Flow"],
    )


def run(info=None, cashflow=None, service=None):
    ticker = SimpleNamespace(info=snapshot() if info is None else info,
                             cashflow=annual() if cashflow is None else cashflow)
    provider = SimpleNamespace(Ticker=Mock(return_value=ticker))
    with patch.dict(sys.modules, {"yfinance": provider}):
        result = (service or DCFValuationService()).get_dcf_valuation("TEST")
    # Responses must serialize as strict JSON; NaN is never a valid missing-data marker.
    json.dumps(result, allow_nan=False)
    return result, provider


def test_real_inputs_generate_labelled_scenarios_with_correct_margin_and_terminal_share():
    result, _ = run()
    assert result["status"] == "partial"
    assert result["valuation_signal"] == "scenario_only"
    assert result["decision_eligible"] is False
    assert result["missing_fields"] == []
    assert result["source"] == "yfinance"
    assert result["financial_period_end"] != result["retrieved_at"][:10]
    assert result["assumptions"]["base_growth_hint"] == 0.0  # valid zero never replaced
    bear, base, bull = (result["scenarios"][key] for key in ("bear", "base", "bull"))
    assert 0 < bear["intrinsic_price"] < base["intrinsic_price"] < bull["intrinsic_price"]
    exact_value = (base["enterprise_value"] + 60) / 100
    assert base["intrinsic_price"] == round(exact_value, 2)
    assert base["margin_of_safety_pct"] == round((1 - 10 / exact_value) * 100, 2)
    assert base["upside_pct"] == round((exact_value / 10 - 1) * 100, 2)
    assert base["upside_pct"] != base["margin_of_safety_pct"]
    assert 0 < base["terminal_value_share_pct"] < 100


@pytest.mark.parametrize("fcf", [-100, 0, float("nan"), float("inf")])
def test_nonpositive_or_invalid_latest_fcf_never_becomes_pe_based_profit(fcf):
    result, _ = run(snapshot(trailingPE=10, freeCashflow=200), annual(fcf))
    assert result["status"] == "unavailable"
    assert result["scenarios"] == {}
    assert result["fcf_source"] != "estimated_from_market_cap_and_pe"


@pytest.mark.parametrize("field", ["totalCash", "totalDebt", "sharesOutstanding", "currency",
                                  "financialCurrency", "regularMarketTime"])
def test_missing_inputs_do_not_become_zero_or_fabricated_price(field):
    info = snapshot()
    del info[field]
    result, _ = run(info)
    assert result["status"] == "partial"
    assert result["missing_fields"]
    assert result["scenarios"] == {}


@pytest.mark.parametrize("field", ["totalCash", "totalDebt", "sharesOutstanding", "currentPrice"])
def test_nonfinite_inputs_are_missing_in_strict_json(field):
    result, _ = run(snapshot(**{field: float("inf")}))
    assert result["scenarios"] == {}
    assert result["missing_fields"]


def test_different_statement_and_price_currency_blocks_per_share_comparison():
    result, _ = run(snapshot(currency="HKD", financialCurrency="CNY"))
    assert "verified_currency_conversion" in result["missing_fields"]
    assert result["scenarios"] == {}


@pytest.mark.parametrize("info", [snapshot(sector="Financial Services"), snapshot(industry="REIT - Retail"),
                                  snapshot(quoteType="ETF")])
def test_noncorporate_or_financial_models_are_not_force_fit(info):
    result, _ = run(info)
    assert result["status"] == "unavailable"
    assert result["scenarios"] == {}


def test_unknown_period_does_not_assume_retrieval_date_is_fcf_period():
    result, _ = run(cashflow=pd.DataFrame())
    assert result["fcf0"] == 100
    assert result["financial_period_end"] is None
    assert "financial_period_end" in result["missing_fields"]
    assert result["scenarios"] == {}


def test_stale_and_future_statement_periods_are_not_current():
    for age in (600, -30):
        result, _ = run(cashflow=annual(days_ago=age))
        assert "current_financial_period" in result["missing_fields"]
        assert result["scenarios"] == {}


def test_stale_quote_cannot_be_compared_as_current():
    result, _ = run(snapshot(regularMarketTime=(datetime.now(timezone.utc) - timedelta(days=10)).timestamp()))
    assert "current_quote" in result["missing_fields"]
    assert result["scenarios"] == {}


@pytest.mark.parametrize("capex", [-25, 25])
def test_operating_cashflow_and_capex_are_same_period_and_sign_normalized(capex):
    df = pd.DataFrame({pd.Timestamp("2024-12-31"): [100, -10],
                       pd.Timestamp("2025-12-31"): [200, capex]},
                      index=["Operating Cash Flow", "Capital Expenditure"])
    result = _statement_fcf(df)
    assert result["latest"]["fcf"] == 175
    assert result["latest"]["period_end"] == "2025-12-31"
    assert len(result["history"]) == 2


def test_missing_latest_capex_does_not_borrow_previous_year():
    df = pd.DataFrame({pd.Timestamp("2024-12-31"): [100, -10],
                       pd.Timestamp("2025-12-31"): [200, None]},
                      index=["Operating Cash Flow", "Capital Expenditure"])
    assert _statement_fcf(df)["latest"]["fcf"] is None


def test_cache_reuses_original_timestamps_and_original_evidence():
    service = DCFValuationService()
    first, _ = run(service=service)
    second, provider = run(snapshot(currentPrice=50), service=service)
    provider.Ticker.assert_not_called()
    assert second["retrieved_at"] == first["retrieved_at"]
    assert second["quote_as_of"] == first["quote_as_of"]
    assert second["current_price"] == first["current_price"]


def test_growth_is_decimal_and_missing_growth_is_explicit_zero_assumption():
    assert _normalize_growth(1.2) == 0.15  # Yahoo 1.2 = 120%, never 1.2%.
    info = snapshot()
    del info["revenueGrowth"]
    result, _ = run(info)
    assert result["assumptions"]["growth_source"] == "zero_growth_placeholder"
    assert result["assumptions"]["base_growth_hint"] == 0


def test_discounted_cashflow_formula_against_closed_form_zero_growth():
    block = _compute_dcf(100, 5, _ScenarioInput(0.0, 0.1, 0.0))
    assert block["enterprise_value"] == pytest.approx(1000)
    assert block["pv_explicit_period"] + block["terminal_pv"] == pytest.approx(1000, abs=0.01)


@pytest.mark.parametrize("scenario", [_ScenarioInput(0.1, 0.02, 0.03),
                                      _ScenarioInput(float("nan"), 0.1, 0.02)])
def test_invalid_model_parameters_are_rejected_not_silently_adjusted(scenario):
    with pytest.raises(ValueError):
        _compute_dcf(100, 5, scenario)


def test_hk_numeric_and_us_share_classes_map_to_provider_symbols():
    assert _to_yf_symbol("00700") == "0700.HK"
    assert _to_yf_symbol("BRK.B") == "BRK-B"


@pytest.mark.parametrize("code, expected_market, expected_symbol", [
    ("SH.600519", "cn", "600519.SS"), ("SH600519", "cn", "600519.SS"),
    ("600519.SH", "cn", "600519.SS"), ("600519.SS", "cn", "600519.SS"),
    ("SZ.000001", "cn", "000001.SZ"), ("000001.SZ", "cn", "000001.SZ"),
    ("HK.00700", "hk", "0700.HK"), ("hk00700", "hk", "0700.HK"),
    ("00700.HK", "hk", "0700.HK"), ("US.AAPL", "us", "AAPL"),
    ("AAPL.US", "us", "AAPL"), ("US.BRK.B", "us", "BRK-B"),
    ("BRK-B", "us", "BRK-B"), ("BRK.B.US", "us", "BRK-B"),
    ("BJ.920748", "cn", "920748.BJ"), ("920748", "cn", "920748.BJ"),
    ("605499", "cn", "605499.SS"),
])
def test_dcf_resolves_provider_aliases_with_shared_market_identity(code, expected_market, expected_symbol):
    from src.services.dcf_valuation_service import _detect_market
    assert _detect_market(code) == expected_market
    assert _to_yf_symbol(code) == expected_symbol


def test_dcf_alias_is_applied_at_actual_provider_boundary():
    ticker = SimpleNamespace(info=snapshot(), cashflow=annual())
    provider = SimpleNamespace(Ticker=Mock(return_value=ticker))
    with patch.dict(sys.modules, {"yfinance": provider}):
        result = DCFValuationService().get_dcf_valuation("US.AAPL")
    provider.Ticker.assert_called_once_with("AAPL")
    assert result["market"] == "us"
    assert result["yf_symbol"] == "AAPL"
    assert result["scenarios"]


def test_ambiguous_or_conflicting_market_identity_never_guesses_provider_ticker():
    provider = SimpleNamespace(Ticker=Mock())
    with patch.dict(sys.modules, {"yfinance": provider}):
        for code in ("US.600519", "SH.000001", "HK.UNKNOWN", "not a ticker"):
            result = DCFValuationService().get_dcf_valuation(code)
            assert result["status"] == "unavailable"
    provider.Ticker.assert_not_called()


def test_negative_fcf_preserves_known_capital_structure_and_actual_period():
    statements = annual(-100)
    result, _ = run(snapshot(totalCash=80, totalDebt=20), statements)
    assert result["status"] == "unavailable"
    assert result["scenarios"] == {}
    assert result["total_cash"] == 80
    assert result["total_debt"] == 20
    assert result["shares_outstanding"] == 100
    assert result["financial_period_end"] == statements.columns[0].date().isoformat()
    assert result["capital_structure_source"] == "yfinance.info"
    assert result["capital_structure_period"] == "provider_snapshot_period_unverified"
    assert result["missing_fields"] == []


def test_negative_fcf_keeps_actual_debt_gap_distinct_from_known_cash():
    info = snapshot(totalCash=80)
    del info["totalDebt"]
    result, _ = run(info, annual(-100))
    assert result["status"] == "unavailable"
    assert result["total_cash"] == 80
    assert result["total_debt"] is None
    assert result["missing_fields"] == ["total_debt"]
