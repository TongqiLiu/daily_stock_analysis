"""Ask-stock must retain evidence semantics after tool compaction."""
from types import SimpleNamespace
from unittest.mock import patch

from src.agent.tools.data_tools import _handle_get_stock_info


def test_stock_info_retains_provenance_and_does_not_invent_report_period():
    context = {
        "market": "us", "status": "partial", "provider": "yfinance",
        "as_of": "2026-08-20T10:00:00+00:00", "data_quality": "partial",
        "missing_fields": ["cash_flow_period"], "coverage": {"growth": "ok", "earnings": "partial"},
        "source_chain": [{"provider": "yfinance", "result": "ok"}],
        "errors": ["cashflow_timeout"],
        "growth": {"status": "ok", "data": {"roe": 18.2},
                   "source_chain": [{"provider": "yfinance.info", "result": "ok"}], "errors": []},
        "earnings": {"status": "partial", "data": {"financial_report": {"report_date": None, "currency": "USD"}},
                     "errors": ["report_period_missing"]},
    }
    manager = SimpleNamespace(get_fundamental_context=lambda code: context,
                              get_belong_boards=lambda code: [], get_stock_name=lambda code: "Test Company")
    with patch("src.agent.tools.data_tools._get_fetcher_manager", return_value=manager):
        result = _handle_get_stock_info("TEST")
    compact = result["fundamental_context"]
    assert compact["as_of"] == context["as_of"]
    assert compact["source_chain"] == context["source_chain"]
    assert compact["errors"] == ["cashflow_timeout"]
    assert compact["growth"]["source_chain"] == context["growth"]["source_chain"]
    assert compact["earnings"]["errors"] == ["report_period_missing"]
    assert compact["earnings"]["data"]["financial_report"]["report_date"] is None
    assert compact["missing_fields"] == ["cash_flow_period"]
    assert result["pe_ratio"] is None
