"""Exercise Agent evidence tools against real SQLite rows, not mocked rollups."""

from contextlib import contextmanager
from datetime import date, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from src.agent.tools import backtest_tools
from src.services.backtest_service import BacktestService
from src.storage import AnalysisHistory, BacktestResult, BacktestSummary, Base


@pytest.fixture
def evidence_service(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    class TestDatabase:
        @contextmanager
        def get_session(self):
            with Session(engine) as session:
                yield session

    db = TestDatabase()
    with db.get_session() as session:
        for index, (position, status, value, window, version) in enumerate([
            ("long", "completed", 4.0, 10, "v1"),
            ("long", "completed", -8.0, 10, "v1"),
            ("cash", "completed", 0.0, 10, "v1"),
            ("long", "insufficient_data", None, 10, "v1"),
            ("long", "completed", 99.0, 5, "v1"),
            ("long", "completed", 99.0, 10, "old"),
        ], 1):
            session.add(AnalysisHistory(id=index, code="AAPL", report_type="simple"))
            session.add(BacktestResult(
                analysis_history_id=index, code="AAPL", analysis_date=date(2024, 1, index),
                eval_window_days=window, engine_version=version, eval_status=status,
                position_recommendation=position, simulated_return_pct=value,
                direction_correct=True if status == "completed" else None,
                outcome="win" if status == "completed" else None,
                evaluated_at=datetime(2024, 2, 1),
            ))
        for scope, code in [("stock", "AAPL"), ("overall", "__overall__")]:
            session.add(BacktestSummary(
                scope=scope, code=code, eval_window_days=10, engine_version="v1",
                computed_at=datetime(2024, 2, 2), total_evaluations=4, completed_count=3,
                insufficient_count=1, long_count=2, cash_count=1,
                win_count=3, loss_count=0, neutral_count=0, win_rate_pct=100.0,
                avg_simulated_return_pct=-4.0 / 3,
            ))
        session.commit()
    monkeypatch.setattr("src.services.backtest_service.get_config", lambda: SimpleNamespace(backtest_engine_version="v1"))
    service = BacktestService(db)
    monkeypatch.setattr(backtest_tools, "_get_backtest_service", lambda: service)
    yield service
    engine.dispose()


def test_default_horizon_keeps_summary_and_items_aligned(evidence_service):
    payload = backtest_tools._handle_get_stock_backtest_summary("AAPL")
    assert payload["requested_eval_window_days"] is None
    assert payload["effective_eval_window_days"] == 10
    assert payload["window_selection"] == "latest_available_summary"
    assert payload["available_eval_window_days"] == [5, 10]
    assert payload["evidence_status"] == "observational"
    assert payload["summary"]["eval_window_days"] == 10
    assert payload["total"] == 4
    assert len(payload["recent_evaluations"]) == 4
    assert {row["eval_window_days"] for row in payload["recent_evaluations"]} == {10}
    assert {row["engine_version"] for row in payload["recent_evaluations"]} == {"v1"}
    assert "insufficient_data" in {row["eval_status"] for row in payload["recent_evaluations"]}


def test_explicit_missing_horizon_is_not_silently_substituted(evidence_service):
    payload = backtest_tools._handle_get_stock_backtest_summary("AAPL", eval_window_days=30)
    assert payload["requested_eval_window_days"] == payload["effective_eval_window_days"] == 30
    assert payload["window_selection"] == "requested"
    assert payload["available_eval_window_days"] == [5, 10]
    assert payload["summary"] is None
    assert payload["evidence_status"] == "missing"
    assert payload["total"] == 0


def test_profitability_uses_completed_long_returns_and_preserves_losses(evidence_service):
    payload = backtest_tools._handle_get_stock_backtest_summary("AAPL")
    profile = payload["evidence_profile"]
    assert profile["total_evaluations"] == 4
    assert profile["completed_count"] == 3
    assert profile["insufficient_count"] == 1
    assert profile["long_return_count"] == 2
    assert profile["profitable_long_count"] == 1
    assert profile["losing_long_count"] == 1
    assert profile["profitable_long_rate_pct"] == 50.0
    assert profile["avg_long_simulated_return_pct"] == -2.0
    assert profile["avg_long_profit_pct"] == 4.0
    assert profile["avg_long_loss_pct"] == -8.0
    assert payload["summary"]["win_rate_pct"] == 100.0  # Directional labels are a different metric.
    assert payload["methodology"]["transaction_costs_and_slippage_included"] is False
    assert payload["methodology"]["attribution"] == "historical_analysis_corpus_not_individual_skill"


def test_default_without_rollup_selects_one_reported_horizon(evidence_service):
    with evidence_service.db.get_session() as session:
        session.query(BacktestSummary).delete()
        session.commit()
    payload = backtest_tools._handle_get_stock_backtest_summary("AAPL")
    assert payload["window_selection"] == "shortest_available_results"
    assert payload["effective_eval_window_days"] == 5
    assert payload["summary"] is None
    assert payload["total"] == 1
    assert payload["evidence_profile"]["long_return_count"] == 1


def test_overall_query_is_read_only_and_exposes_denominators(evidence_service):
    with evidence_service.db.get_session() as session:
        before = session.scalar(select(func.count()).select_from(BacktestResult))
    payload = backtest_tools._handle_get_overall_backtest_summary()
    assert payload["effective_eval_window_days"] == 10
    assert payload["long_count"] == 2
    assert payload["cash_count"] == 1
    assert payload["evidence_profile"]["profitable_long_rate_pct"] == 50.0
    with evidence_service.db.get_session() as session:
        assert session.scalar(select(func.count()).select_from(BacktestResult)) == before


def test_empty_stock_keeps_missing_evidence_distinct_from_zero(evidence_service):
    payload = backtest_tools._handle_get_stock_backtest_summary("MSFT")
    assert payload["evidence_status"] == "missing"
    assert payload["effective_eval_window_days"] is None
    assert payload["evidence_profile"] is None
    assert payload["summary"] is None
    assert payload["recent_evaluations"] == []


@pytest.mark.parametrize("window", [0, -1, True, "10", 1.5])
def test_tools_reject_invalid_horizons(evidence_service, window):
    assert "error" in backtest_tools._handle_get_stock_backtest_summary("AAPL", eval_window_days=window)
    assert "error" in backtest_tools._handle_get_overall_backtest_summary(eval_window_days=window)


def test_skill_evidence_remains_unsupported_even_when_stock_performs(evidence_service):
    payload = backtest_tools._handle_get_skill_backtest_summary("value_investing")
    assert payload["supported"] is False
    assert payload["evidence_status"] == "missing"
    assert "win_rate_pct" not in payload


def test_audit_database_enforces_sqlite_read_only_even_if_a_write_is_attempted(tmp_path):
    from scripts.audit_backtest_evidence import ReadOnlyDatabase

    database = tmp_path / "evidence sample.db"
    writable = create_engine(f"sqlite:///{database}")
    with writable.begin() as connection:
        connection.execute(text("CREATE TABLE probe (value INTEGER)"))
        connection.execute(text("INSERT INTO probe VALUES (7)"))
    writable.dispose()
    db = ReadOnlyDatabase(database)
    try:
        with db.get_session() as session:
            assert session.scalar(text("SELECT value FROM probe")) == 7
            with pytest.raises(OperationalError, match="readonly"):
                session.execute(text("INSERT INTO probe VALUES (8)"))
    finally:
        db.engine.dispose()
