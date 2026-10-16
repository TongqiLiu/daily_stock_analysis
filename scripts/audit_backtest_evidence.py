#!/usr/bin/env python3
"""Inspect persisted evidence and optionally replay local bars without writes/network.

Usage: python scripts/audit_backtest_evidence.py --database data/stock_analysis.db --replay
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, func, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from src.config import get_config  # noqa: E402
from src.core.backtest_engine import BacktestEngine, EvaluationConfig  # noqa: E402
from src.services.backtest_service import BacktestService  # noqa: E402
from src.services.stock_daily_start_resolver import resolve_stock_daily_start  # noqa: E402
from src.services.stock_daily_window_resolver import resolve_stock_daily_window  # noqa: E402
from src.storage import (  # noqa: E402
    AnalysisHistory, BacktestResult, SkillOpinionOutcomeRecord, SkillOpinionSampleRecord,
)


class ReadOnlyDatabase:
    """Avoid DatabaseManager startup migrations and enforce SQLite read-only mode."""

    def __init__(self, database: Path):
        if not database.is_file():
            raise ValueError("database must be an existing SQLite file")
        self.engine = create_engine(f"sqlite:///{database.resolve().as_uri()}?mode=ro&uri=true")

    @contextmanager
    def get_session(self):
        with Session(self.engine) as session:
            yield session


def replay(service: BacktestService, engine_version: str, neutral_band_pct: float) -> dict:
    """Apply current resolution/calculation to stored inputs; never refill bars."""
    counts, reasons, changed_fields = Counter(), Counter(), Counter()
    examples, long_returns = [], defaultdict(list)
    with service.db.get_session() as session:
        rows = session.execute(select(
            BacktestResult, AnalysisHistory.context_snapshot, AnalysisHistory.created_at,
            AnalysisHistory.operation_advice, AnalysisHistory.stop_loss, AnalysisHistory.take_profit,
        ).join(AnalysisHistory, AnalysisHistory.id == BacktestResult.analysis_history_id)
            .where(BacktestResult.engine_version == engine_version)
            .order_by(BacktestResult.id)).yield_per(100)
        for old, snapshot, created, advice, stop, take in rows:
            counts["examined"] += 1
            analysis_date = service._resolve_analysis_date(
                SimpleNamespace(context_snapshot=snapshot, created_at=created),
            )
            start = resolve_stock_daily_start(
                stock_code=old.code, context_snapshot=snapshot, analysis_date=analysis_date,
            )
            window = (
                resolve_stock_daily_window(
                    stock_repo=service.stock_repo, code_candidates=list(start.identity.code_candidates),
                    expected_start_date=start.backtest_start_date, eval_window_days=old.eval_window_days,
                ) if start.identity and start.backtest_start_date else None
            )
            if start.failure_reason:
                reasons[start.failure_reason] += 1
            if window is None:
                current = {"eval_status": "insufficient_data"}
            else:
                current = BacktestEngine.evaluate_single(
                    operation_advice=advice, analysis_date=window.start_bar.date,
                    start_price=window.start_bar.close, forward_bars=window.forward_bars,
                    stop_loss=stop, take_profit=take,
                    config=EvaluationConfig(old.eval_window_days, neutral_band_pct, engine_version),
                )
            counts[f"replayed_{current['eval_status']}"] += 1
            if current["eval_status"] == "completed" and current.get("position_recommendation") == "long":
                value = current.get("simulated_return_pct")
                if value is not None:
                    long_returns[old.eval_window_days].append(value)
            changes = []
            for field in ("eval_status", "start_price", "stock_return_pct", "simulated_return_pct",
                          "outcome", "position_recommendation"):
                if field != "eval_status" and current["eval_status"] != "completed":
                    continue
                before, after = getattr(old, field), current.get(field)
                equal = (math.isclose(before, after, rel_tol=1e-9, abs_tol=1e-8)
                         if isinstance(before, (int, float)) and isinstance(after, (int, float)) else before == after)
                if not equal:
                    changes.append(field)
                    changed_fields[field] += 1
            counts["rows_changed" if changes else "rows_matched"] += 1
            if changes and len(examples) < 10:
                examples.append({"result_id": old.id, "code": old.code, "changed_fields": changes})
    return {
        "neutral_band_pct": neutral_band_pct,
        "counts": dict(counts), "start_resolution_diagnostics": dict(reasons),
        "changed_fields": dict(changed_fields), "first_ten_mismatches": examples,
        "replayed_long_returns_by_window": [
            {"eval_window_days": window, "long_return_count": len(values),
             "avg_long_simulated_return_pct": round(sum(values) / len(values), 4)}
            for window, values in sorted(long_returns.items())
        ],
        "limitation": "Current local bars and resolver only; no point-in-time dataset, new skill signals, fees, or fills.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--replay", action="store_true", help="Replay existing signals on local bars without saving")
    args = parser.parse_args()
    db = ReadOnlyDatabase(args.database)
    try:
        service = BacktestService(db)
        config = get_config()
        engine_version = str(config.backtest_engine_version)
        with db.get_session() as session:
            sample_count = session.scalar(select(func.count()).select_from(SkillOpinionSampleRecord))
            outcome_count = session.scalar(select(func.count()).select_from(SkillOpinionOutcomeRecord))
        output = {
            "audited_at": datetime.now(timezone.utc).isoformat(),
            "read_only": True, "engine_version": engine_version,
            "skill_opinion_samples": sample_count, "skill_opinion_outcomes": outcome_count,
            "persisted_profiles": [service.get_evidence_profile(code=None, eval_window_days=window)
                                   for window in service.get_available_eval_windows()],
        }
        if args.replay:
            output["replay"] = replay(service, engine_version, config.backtest_neutral_band_pct)
        print(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False))
    finally:
        db.engine.dispose()


if __name__ == "__main__":
    main()
