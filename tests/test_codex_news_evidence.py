"""Only news actually delivered by an owned tool worker enters parent evidence."""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.agent.codex_tool_process import CodexToolProcessRunner
from src.agent.news_evidence import (
    activate_news_evidence_scope, get_current_news_evidence, reset_news_evidence_scope,
)
from src.agent.stock_scope import StockScope
from src.agent.tools.execution import ToolAccessContext


def _real_search(context: ToolAccessContext) -> dict:
    """Keep the production handler, evidence recorder, ToolSurface and DB write."""
    from src.agent.factory import get_tool_registry
    from src.agent.tool_surface import ToolSurface
    from src.search_service import SearchResponse, SearchResult

    response = SearchResponse(query="AAPL", provider="fixture", success=True, results=[
        SearchResult(title="Apple disclosure", snippet="Company reported results", source="fixture",
                     url="https://example.test/apple-disclosure", published_date="2026-01-01"),
    ])
    service = SimpleNamespace(is_available=True, search_stock_news=lambda *args, **kwargs: response)
    with patch("src.agent.tools.search_tools._get_search_service", return_value=service):
        return ToolSurface(get_tool_registry()).execute_tool(
            "search_stock_news", {"stock_code": "AAPL", "stock_name": "Apple"}, context,
        )


def _search_worker(_name: str, _arguments: dict, context: ToolAccessContext) -> dict:
    return _real_search(context)


def _failed_after_search_worker(_name: str, _arguments: dict, context: ToolAccessContext) -> dict:
    result = _real_search(context)
    assert result["ok"], result
    raise RuntimeError("Result failed before delivery")


def _cancel_after_search_worker(_name: str, arguments: dict, context: ToolAccessContext) -> dict:
    result = _real_search(context)
    assert result["ok"], result
    Path(arguments["marker"]).write_text(str(os.getpid()), encoding="utf-8")
    # The parent kills the owned process; no result containing news is delivered.
    time.sleep(60)
    return result


def _context(**overrides) -> ToolAccessContext:
    return ToolAccessContext(**{
        "stock_scope": StockScope(expected_stock_code="AAPL", allowed_stock_codes={"AAPL"}),
        "deadline": time.monotonic() + 45, "backend": "codex_app_server", **overrides,
    })


def test_successful_spawned_news_merges_once_into_original_thread_scope(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "success.db"))
    token = activate_news_evidence_scope()
    accumulator = get_current_news_evidence()
    # Transport constructs the runner in the request thread but dispatches tools
    # through a different thread without inheriting this ContextVar.
    runner = CodexToolProcessRunner(worker=_search_worker)
    output = {}

    def execute_from_transport_thread():
        assert get_current_news_evidence() is None
        output["result"] = runner.execute("search_stock_news", {}, _context())

    thread = threading.Thread(target=execute_from_transport_thread)
    try:
        thread.start()
        thread.join(timeout=40)
        assert not thread.is_alive()
        result = output["result"]
        assert result["ok"], result
        assert json.loads(result["result_text"])["results_count"] == 1
        assert accumulator.total == 1
        assert runner.snapshot()[0]["pid_alive_after"] is False
        assert runner.close()
        # Cleanup and telemetry reads must not count the same delivery again.
        runner.snapshot()
        assert runner.close()
        assert accumulator.total == 1
    finally:
        runner.close()
        thread.join(timeout=5)
        reset_news_evidence_scope(token)


@pytest.mark.parametrize("worker,context_overrides,error_code", [
    (_failed_after_search_worker, {}, "handler_error"),
    (_search_worker, {"max_result_bytes": 16}, "output_too_large"),
])
def test_undelivered_worker_news_never_counts_as_consumed(
    tmp_path: Path, monkeypatch, worker, context_overrides, error_code,
):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "failed.db"))
    token = activate_news_evidence_scope()
    accumulator = get_current_news_evidence()
    runner = CodexToolProcessRunner(worker=worker)
    try:
        result = runner.execute("search_stock_news", {}, _context(**context_overrides))
        assert result["error"]["code"] == error_code
        assert accumulator.total == 0
        assert runner.snapshot()[0]["pid_alive_after"] is False
    finally:
        assert runner.close()
        reset_news_evidence_scope(token)


def test_cancel_after_real_search_does_not_merge_undelivered_news(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "cancelled.db"))
    token = activate_news_evidence_scope()
    accumulator = get_current_news_evidence()
    runner = CodexToolProcessRunner(worker=_cancel_after_search_worker)
    marker = tmp_path / "search-complete.marker"
    cancel_event = threading.Event()
    output = {}
    thread = threading.Thread(target=lambda: output.setdefault("result", runner.execute(
        "search_stock_news", {"marker": str(marker)}, _context(cancel_event=cancel_event),
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
        assert accumulator.total == 0
        record = runner.snapshot()[0]
        assert record["pid_alive_after"] is False
        assert record["process_group_alive_after"] is False
    finally:
        cancel_event.set()
        assert runner.close()
        thread.join(timeout=5)
        reset_news_evidence_scope(token)
