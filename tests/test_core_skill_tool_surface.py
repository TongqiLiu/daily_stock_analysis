"""Core skills must execute through the real owned-process cancellation boundary."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from contextlib import ExitStack
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.agent.codex_tool_process import CodexToolProcessRunner
from src.agent.stock_scope import StockScope
from src.agent.tool_surface import ToolSurface
from src.agent.tools.execution import ToolAccessContext, bind_tool_execution_context, reset_tool_execution_context
from src.agent.tools.registry import ToolPolicy


def _core_required_tools() -> set[str]:
    from src.agent.skills.base import SkillManager

    manager = SkillManager()
    manager.load_builtin_skills()
    return {
        name for skill in manager.list_skills() if skill.catalog_group == "core"
        for name in skill.required_tools
    }


def _daily_frame():
    import pandas as pd

    closes = [100 + index * 0.1 for index in range(280)]
    return pd.DataFrame({
        "date": pd.date_range(end=pd.Timestamp.today().normalize(), periods=280),
        "open": [value - 0.2 for value in closes],
        "high": [value + 1 for value in closes],
        "low": [value - 1 for value in closes],
        "close": closes, "volume": [10000] * 280,
    })


def _provider_fixtures(stack: ExitStack) -> None:
    """Replace only provider boundaries; handlers, policies and DB writes stay real."""
    from data_provider.realtime_types import UnifiedRealtimeQuote
    from src.search_service import SearchResponse, SearchResult

    frame = _daily_frame()
    response = SearchResponse(query="AAPL", provider="fixture", success=True, results=[
        SearchResult(title="Apple filing", snippet="Disclosed results", source="fixture",
                     url="https://example.test/apple-filing", published_date="2026-01-01"),
    ])
    service = SimpleNamespace(
        is_available=True, search_stock_news=lambda *a, **k: response,
        search_comprehensive_intel=lambda **k: {"latest_news": response},
        format_intel_report=lambda *a: "Company disclosure",
    )
    manager = SimpleNamespace(
        get_realtime_quote=lambda code: UnifiedRealtimeQuote(code=code, name="Apple", price=127.9),
        get_fundamental_context=lambda code: {"valuation": {"status": "ok", "data": {"pe_ratio": 25}}},
        get_belong_boards=lambda code: [], get_stock_name=lambda code: "Apple",
    )
    patches = {
        "src.services.history_loader.load_history_df": lambda *a, **k: (frame.copy(), "fixture"),
        "src.services.intraday_history_loader.load_intraday_history": lambda *a, **k: (frame.copy(), "fixture"),
        "data_provider.futu_fetcher.FutuFetcher.is_available_for_request": lambda *a, **k: False,
        "src.agent.tools.data_tools._get_fetcher_manager": lambda: manager,
        "src.agent.tools.data_tools._get_fear_greed_service": lambda: SimpleNamespace(is_available=False),
        "src.agent.tools.data_tools._get_valuation_service": lambda: SimpleNamespace(
            get_valuation_data=lambda **k: {"stock_code": "AAPL", "status": "partial"}),
        "src.agent.tools.data_tools._get_dcf_valuation_service": lambda: SimpleNamespace(
            get_dcf_valuation=lambda **k: {"stock_code": "AAPL", "status": "partial"}),
        "src.agent.tools.search_tools._get_search_service": lambda: service,
        "src.agent.tools.search_tools._fetch_futu_research": lambda *a, **k: {
            "code": 0, "data": [{"title": "Apple research", "url": "https://example.test/research"}]},
        "src.agent.tools.options_tools._get_longbridge_snapshot": lambda *a: {
            "status": "partial", "stock_code": "AAPL", "source": "fixture"},
    }
    for target, replacement in patches.items():
        stack.enter_context(patch(target, new=replacement))
    stack.enter_context(patch.dict(os.environ, {"FUTU_ENABLED": "false"}))


def _all_core_worker(_name: str, _arguments: dict, context: ToolAccessContext) -> dict:
    from src.agent.factory import get_tool_registry
    from src.agent.tools.analysis_tools import MULTI_STRATEGY_SCORE_SPECS

    scores = [{"strategy": name, "signal": "观望", "strength": "无", "score": None,
               "evidence_status": "missing", "reason": "fixture evidence missing"}
              for name, _, _ in MULTI_STRATEGY_SCORE_SPECS]
    arguments = {
        "calculate_multi_strategy_score": {"scores_json": json.dumps(scores), "instrument_type": "standard"},
        "get_option_strategy_analysis": {"stock_code": "AAPL", "legs_json": "[]"},
        "search_stock_news": {"stock_code": "AAPL", "stock_name": "Apple"},
        "search_comprehensive_intel": {"stock_code": "AAPL", "stock_name": "Apple"},
    }
    results = {}
    with ExitStack() as stack:
        _provider_fixtures(stack)
        surface = ToolSurface(get_tool_registry())
        for name in sorted(_core_required_tools()):
            result = surface.execute_tool(name, arguments.get(name, {"stock_code": "AAPL"}), context)
            results[name] = {"ok": result["ok"], "error": result.get("error"), "payload": result.get("result")}
    return {"ok": True, "tool_name": "core_fixture", "result_text": json.dumps(results), "error": None}


def _cache_write_worker(tool_name: str, arguments: dict, context: ToolAccessContext) -> dict:
    from sqlalchemy import event
    from src.agent.factory import get_tool_registry
    from src.storage import get_db

    db = get_db()
    table = "news_intel" if tool_name == "search_stock_news" else "stock_daily"

    def after_write(_connection, _cursor, statement, _params, _execution_context, _many):
        if statement.lstrip().upper().startswith("INSERT") and table in statement.lower():
            # Pause after a real INSERT, before the production transaction commits.
            Path(arguments["marker"]).write_text(str(os.getpid()), encoding="utf-8")
            time.sleep(60)

    event.listen(db._engine, "after_cursor_execute", after_write)
    with ExitStack() as stack:
        _provider_fixtures(stack)
        tool_arguments = {"stock_code": "AAPL"}
        if tool_name == "search_stock_news":
            tool_arguments["stock_name"] = "Apple"
        return ToolSurface(get_tool_registry()).execute_tool(tool_name, tool_arguments, context)


def _history_context_worker(tool_name: str, arguments: dict, context: ToolAccessContext) -> dict:
    import pandas as pd
    from src.agent.factory import get_tool_registry
    from src.agent.freshness import is_fresh_market_data_required
    from src.services.history_loader import get_frozen_target_date
    from src.storage import get_db

    cached = _daily_frame()
    cached["date"] = pd.date_range(end=arguments["seed_end"], periods=len(cached))
    get_db().save_daily_data(cached, "AAPL", "seed_cache")
    network = cached.copy()
    network["close"] += 10
    with patch("src.services.history_loader._get_fetcher_manager", return_value=SimpleNamespace(
        get_daily_data=lambda *a, **k: (network, "network_fixture"),
    )):
        result = ToolSurface(get_tool_registry()).execute_tool(
            "get_daily_history", {"stock_code": "AAPL"}, context,
        )
    result["result_text"] = json.dumps({
        "fresh": is_fresh_market_data_required(),
        "frozen": str(get_frozen_target_date()) if get_frozen_target_date() else None,
        "history": result["result"],
    })
    return result


def test_core_required_tools_are_visible_only_with_their_execution_capability():
    from src.agent.factory import get_tool_registry

    registry = get_tool_registry()
    surface = ToolSurface(registry)
    bounded = {item["name"] for item in surface.list_tools(cancellation_safe_only=True)}
    owned = {item["name"] for item in surface.list_tools(cancellation_safe_only=True, process_isolated=True)}
    assert _core_required_tools() <= owned
    assert "calculate_ma" not in bounded
    assert "get_portfolio_snapshot" not in owned
    newly_exposed = owned - bounded
    assert newly_exposed <= _core_required_tools()
    assert all(registry.resolve(name).policy.read_only for name in newly_exposed)
    assert all(not registry.resolve(name).policy.cancellation_safe for name in newly_exposed)


def test_caller_metadata_cannot_spoof_owned_process_capability():
    from src.agent.factory import get_tool_registry

    surface = ToolSurface(get_tool_registry())
    result = surface.execute_tool("calculate_ma", {"stock_code": "AAPL"}, ToolAccessContext(
        stock_scope=StockScope(expected_stock_code="AAPL", allowed_stock_codes={"AAPL"}),
        deadline=time.monotonic() + 20,
        backend="codex_app_server",
        audit_context={"process_isolated": True, "owned_tool_process_pid": os.getpid()},
    ))
    assert result["error"]["code"] == "cancellation_unsupported"
    with pytest.raises(ValueError, match="limited to reads"):
        ToolPolicy.declared(read_only=False, side_effects=["send_message"], process_cancellation_safe=True)


def test_all_core_handlers_execute_in_real_owned_process(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "core-tools.db"))
    runner = CodexToolProcessRunner(worker=_all_core_worker)
    try:
        result = runner.execute("core_fixture", {}, ToolAccessContext(
            stock_scope=StockScope(expected_stock_code="AAPL", allowed_stock_codes={"AAPL"}),
            deadline=time.monotonic() + 60, backend="codex_app_server",
        ))
    finally:
        assert runner.close()
    assert result["ok"], result
    payload = json.loads(result["result_text"])
    assert set(payload) == _core_required_tools()
    failures = {name: item for name, item in payload.items() if not item["ok"]}
    assert failures == {}
    assert payload["calculate_ma"]["payload"]["ma"]["ma5"]["value"] == 127.7
    assert payload["search_stock_news"]["payload"]["results_count"] == 1
    assert payload["get_daily_history"]["payload"]["actual_records"] > 0
    assert runner.snapshot()[0]["pid_alive_after"] is False


@pytest.mark.parametrize("tool_name,table", [
    ("get_daily_history", "stock_daily"), ("search_stock_news", "news_intel"),
])
def test_cancel_during_real_cache_transaction_rolls_back_and_releases_resources(
    tmp_path: Path, monkeypatch, tool_name: str, table: str,
):
    db_path = tmp_path / "cancel.db"
    marker = tmp_path / "insert.marker"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    runner = CodexToolProcessRunner(worker=_cache_write_worker)
    cancel_event = threading.Event()
    output = {}
    thread = threading.Thread(target=lambda: output.setdefault("result", runner.execute(
        tool_name, {"marker": str(marker)}, ToolAccessContext(
            stock_scope=StockScope(expected_stock_code="AAPL", allowed_stock_codes={"AAPL"}),
            cancel_event=cancel_event, deadline=time.monotonic() + 45,
        ),
    )))
    try:
        thread.start()
        deadline = time.monotonic() + 30
        while not marker.exists() and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert marker.exists(), output
        cancel_event.set()
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert output["result"]["error"]["code"] == "cancelled"
    finally:
        assert runner.close()
        thread.join(timeout=5)
    with sqlite3.connect(db_path, timeout=2) as verifier:
        assert verifier.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
        assert verifier.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        verifier.execute("BEGIN IMMEDIATE")
        verifier.rollback()
    record = runner.snapshot()[0]
    assert record["pid_alive_after"] is False
    assert record["process_group_alive_after"] is False


def test_search_cancellation_after_provider_return_skips_cache(monkeypatch):
    from src.agent.tools import search_tools
    from src.agent.tools.execution import ToolExecutionCancelled

    cancelled = threading.Event()
    writes = []

    def provider(*args, **kwargs):
        cancelled.set()
        return SimpleNamespace(success=True, results=[object()])

    monkeypatch.setattr(search_tools, "_get_search_service", lambda: SimpleNamespace(
        is_available=True, search_stock_news=provider,
    ))
    monkeypatch.setattr(search_tools, "_get_db", lambda: SimpleNamespace(
        save_news_intel=lambda **kwargs: writes.append(kwargs),
    ))
    token = bind_tool_execution_context(ToolAccessContext(cancel_event=cancelled))
    try:
        with pytest.raises(ToolExecutionCancelled):
            search_tools._handle_search_stock_news("AAPL", "Apple")
    finally:
        reset_tool_execution_context(token)
    assert writes == []


@pytest.mark.parametrize("fresh,frozen", [(True, None), (False, date(2025, 6, 10))])
def test_real_spawn_preserves_fresh_cache_bypass_and_frozen_history_window(
    tmp_path, monkeypatch, fresh, frozen,
):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "history-context.db"))
    runner = CodexToolProcessRunner(worker=_history_context_worker)
    output = {}
    # The explicit runtime context survives a plain dispatch thread, whose
    # ContextVars start empty, and the spawned child process after that.
    thread = threading.Thread(target=lambda: output.setdefault("result", runner.execute(
        "get_daily_history", {"seed_end": str(frozen or date.today())}, ToolAccessContext(
            stock_scope=StockScope(expected_stock_code="AAPL", allowed_stock_codes={"AAPL"}),
            require_fresh_market_data=fresh, frozen_target_date=frozen,
            deadline=time.monotonic() + 40,
        ),
    )))
    try:
        thread.start()
        thread.join(timeout=45)
        assert not thread.is_alive()
    finally:
        assert runner.close()
    assert output["result"]["ok"], output
    payload = json.loads(output["result"]["result_text"])
    assert payload["fresh"] is fresh
    assert payload["frozen"] == (str(frozen) if frozen else None)
    assert payload["history"]["cache_hit"] is (not fresh)
    assert payload["history"]["latest_data_date"] == str(frozen or date.today())
    assert payload["history"]["data"][-1]["close"] == (137.9 if fresh else 127.9)
    assert runner.snapshot()[0]["pid_alive_after"] is False
