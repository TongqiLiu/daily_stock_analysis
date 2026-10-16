"""OpenD outages must not trap an Agent data tool in SDK reconnect loops."""

from unittest.mock import Mock

import pytest

from data_provider import futu_connection
from data_provider.base import DataFetcherManager
from data_provider.futu_fetcher import FutuFetcher
from src.agent.tools.options_tools import (
    _get_futu_snapshot,
    _handle_get_option_strategy_analysis,
)


def test_unreachable_gateway_never_constructs_reconnecting_context(monkeypatch):
    monkeypatch.setattr(
        futu_connection.socket,
        "create_connection",
        Mock(side_effect=ConnectionRefusedError("OpenD down")),
    )
    factory = Mock()

    with pytest.raises(ConnectionError, match="not listening"):
        futu_connection.open_futu_quote_context(factory, "127.0.0.1", 11111)

    factory.assert_not_called()


def test_context_uses_async_connect_and_bounded_sync_query(monkeypatch):
    monkeypatch.setattr(futu_connection, "futu_opend_reachable", lambda *a, **k: True)
    context = Mock()
    factory = Mock(return_value=context)

    assert futu_connection.open_futu_quote_context(factory, "127.0.0.1", 11111) is context

    factory.assert_called_once_with(host="127.0.0.1", port=11111, is_async_connect=True)
    context.set_sync_query_connect_timeout.assert_called_once_with(5.0)


def test_context_closed_if_timeout_setup_fails(monkeypatch):
    monkeypatch.setattr(futu_connection, "futu_opend_reachable", lambda *a, **k: True)
    context = Mock()
    context.set_sync_query_connect_timeout.side_effect = RuntimeError("bad SDK")

    with pytest.raises(RuntimeError, match="bad SDK"):
        futu_connection.open_futu_quote_context(Mock(return_value=context), "127.0.0.1", 11111)

    context.close.assert_called_once()


def test_fetcher_skips_down_opend_and_closes_old_context(monkeypatch):
    monkeypatch.setenv("FUTU_ENABLED", "true")
    monkeypatch.setattr("data_provider.futu_fetcher.futu_opend_reachable", lambda *a, **k: False)
    fetcher = FutuFetcher()
    context = Mock()
    fetcher._ctx = context

    assert fetcher.is_available_for_request("realtime_quote") is False
    assert DataFetcherManager._is_fetcher_available(fetcher, "realtime_quote") is False
    assert fetcher._ctx is None
    assert fetcher.get_realtime_quote("HK06855") is None
    context.close.assert_called_once()


def test_fetcher_discards_context_after_sdk_connect_timeout(monkeypatch):
    monkeypatch.setenv("FUTU_ENABLED", "true")
    monkeypatch.setattr("data_provider.futu_fetcher.futu_opend_reachable", lambda *a, **k: True)
    fetcher = FutuFetcher()
    context = Mock()
    fetcher._ctx = context

    fetcher._close_on_connection_failure("Connect timeout")

    assert fetcher._ctx is None
    context.close.assert_called_once()


def test_futu_option_tools_report_unavailable_without_opening_sdk_context(monkeypatch):
    monkeypatch.setenv("FUTU_ENABLED", "true")
    monkeypatch.setattr(futu_connection, "futu_opend_reachable", lambda *a, **k: False)
    import futu

    factory = Mock()
    monkeypatch.setattr(futu, "OpenQuoteContext", factory)

    snapshot = _get_futu_snapshot("US.AAPL", None, 5)
    analysis = _handle_get_option_strategy_analysis(
        '[{"code":"US.AAPL260918C200000","action":"BUY","quantity":1}]',
        stock_code="US.AAPL",
    )

    assert snapshot["status"] == "unavailable"
    assert analysis["status"] == "unavailable"
    assert "not listening" in str(snapshot)
    assert "not listening" in str(analysis)
    factory.assert_not_called()
