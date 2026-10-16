# -*- coding: utf-8 -*-
"""
Backtest tools — read-only tools exposing backtest summaries to the agent.

Tools:
- get_skill_backtest_summary: skill-scoped stats when available, otherwise an explicit unsupported/info response
- get_strategy_backtest_summary: legacy alias of the overall summary tool
- get_stock_backtest_summary: backtest results for a specific stock
"""

import logging
from typing import Optional

from src.agent.tools.execution import (
    ToolExecutionCancelled,
    ToolExecutionDeadlineExceeded,
    check_tool_execution,
)
from src.agent.tools.registry import ToolParameter, ToolDefinition, ToolPolicy

logger = logging.getLogger(__name__)

_BACKTEST_READ_POLICY = ToolPolicy.declared(
    read_only=True,
    side_effects=["db_read"],
    permissions=["backtest:read"],
    scope_dimensions=["stock"],
    cancellation_safe=True,
)
_BACKTEST_GLOBAL_READ_POLICY = ToolPolicy.declared(
    read_only=True,
    side_effects=["db_read"],
    permissions=["backtest:read"],
    cancellation_safe=True,
)

_CONTROL_ERRORS = (ToolExecutionCancelled, ToolExecutionDeadlineExceeded)

_backtest_service = None


def _get_backtest_service():
    """Lazy import + singleton to avoid circular deps and repeated instantiation."""
    global _backtest_service
    if _backtest_service is None:
        from src.services.backtest_service import BacktestService
        _backtest_service = BacktestService()
    return _backtest_service


# ============================================================
# get_skill_backtest_summary / get_strategy_backtest_summary
# ============================================================

def _serialize_overall_backtest_summary(summary: dict, eval_window_days: Optional[int]) -> dict:
    """Return the public overall-summary payload exposed to the agent."""
    return {
        "scope": summary.get("scope", "overall"),
        "eval_window_days": summary.get("eval_window_days", eval_window_days),
        "total_evaluations": summary.get("total_evaluations", 0),
        "completed_count": summary.get("completed_count", 0),
        "insufficient_count": summary.get("insufficient_count", 0),
        "engine_version": summary.get("engine_version"),
        "long_count": summary.get("long_count", 0),
        "cash_count": summary.get("cash_count", 0),
        "win_count": summary.get("win_count", 0),
        "loss_count": summary.get("loss_count", 0),
        "neutral_count": summary.get("neutral_count", 0),
        "win_rate_pct": summary.get("win_rate_pct"),
        "direction_accuracy_pct": summary.get("direction_accuracy_pct"),
        "avg_stock_return_pct": summary.get("avg_stock_return_pct"),
        "avg_simulated_return_pct": summary.get("avg_simulated_return_pct"),
        "stop_loss_trigger_rate": summary.get("stop_loss_trigger_rate"),
        "take_profit_trigger_rate": summary.get("take_profit_trigger_rate"),
        "advice_breakdown": summary.get("advice_breakdown"),
        "computed_at": summary.get("computed_at"),
    }


def _window_error(eval_window_days: Optional[int]) -> Optional[dict]:
    if eval_window_days is not None and (
        isinstance(eval_window_days, bool) or not isinstance(eval_window_days, int) or eval_window_days <= 0
    ):
        return {"error": "eval_window_days must be a positive integer or omitted"}
    return None


def _backtest_evidence(svc, code: Optional[str], requested: Optional[int], summary: Optional[dict]) -> dict:
    """Resolve one horizon explicitly, and keep raw observations separate from claims."""
    windows = svc.get_available_eval_windows(code=code)
    check_tool_execution()
    effective = requested
    selection = "requested"
    if requested is None:
        if summary is not None:
            effective = summary.get("eval_window_days")
            selection = "latest_available_summary"
        else:
            effective = windows[0] if windows else None
            selection = "shortest_available_results" if windows else "no_available_results"
    profile = svc.get_evidence_profile(code=code, eval_window_days=effective) if effective is not None else None
    check_tool_execution()
    total = (profile or {}).get("total_evaluations", 0)
    completed = (profile or {}).get("completed_count", 0)
    return {
        "requested_eval_window_days": requested,
        "effective_eval_window_days": effective,
        "window_selection": selection,
        "available_eval_window_days": windows,
        "evidence_status": "observational" if completed else ("insufficient" if total else "missing"),
        "evidence_profile": profile,
        "methodology": {
            "attribution": "historical_analysis_corpus_not_individual_skill",
            "replay_validation": "persisted_results_not_recomputed_by_this_read_only_tool",
            "win_rate_pct_definition": "directional_wins_divided_by_wins_plus_losses_excluding_neutral",
            "profitable_long_rate_pct_definition": "positive_simulated_longs_divided_by_longs_with_return",
            "avg_simulated_return_pct_includes_cash": True,
            "transaction_costs_and_slippage_included": False,
            "entry_basis": "resolved_analysis_anchor_close_not_verified_executable_fill",
            "same_bar_target_order": "ambiguous_assumes_stop_loss_first",
            "limitations": [
                "Overlapping stock/date windows are correlated observations, not independent trades.",
                "No out-of-sample skill validation, portfolio equity curve, or point-in-time data audit is implied.",
                "An engine label alone does not prove old results reproduce with current code or revised market data.",
                "Short daily-bar horizons do not validate long-term value investing or intraday/options execution.",
                "Historical sample coverage and profit/loss sizes matter; hit rate alone must not raise conviction or sizing.",
            ],
        },
    }


def _handle_get_overall_backtest_summary(eval_window_days: Optional[int] = None) -> dict:
    """Get the overall backtest summary for the full analysis corpus."""
    if error := _window_error(eval_window_days):
        return error
    try:
        check_tool_execution()
        svc = _get_backtest_service()
        summary = svc.get_summary(scope="overall", code=None, eval_window_days=eval_window_days)
        check_tool_execution()
        evidence = _backtest_evidence(svc, None, eval_window_days, summary)
        if summary is None:
            return {"scope": "overall", "info": "No cached summary for the selected horizon.", **evidence}
        return {**_serialize_overall_backtest_summary(summary, eval_window_days), **evidence}
    except _CONTROL_ERRORS:
        raise
    except Exception:
        logger.warning("[backtest_tools] get_overall_backtest_summary error", exc_info=True)
        return {"error": "Failed to retrieve backtest summary."}


def _handle_get_skill_backtest_summary(skill_id: str = "", eval_window_days: Optional[int] = None) -> dict:
    """Get a skill-scoped backtest summary when real per-skill stats exist."""
    if not skill_id:
        return {
            "supported": False,
            "error": "skill_id is required. Use get_strategy_backtest_summary for overall metrics.",
        }
    if error := _window_error(eval_window_days):
        return error

    try:
        check_tool_execution()
        svc = _get_backtest_service()
        summary = svc.get_skill_summary(skill_id, eval_window_days=eval_window_days)
        check_tool_execution()
        if summary is None:
            return {
                "skill_id": skill_id,
                "supported": False,
                "evidence_status": "missing",
                "requested_eval_window_days": eval_window_days,
                "info": "Skill-scoped backtest summaries are not available yet.",
                "limitation": "Overall or per-stock performance cannot be attributed to this skill or used as its win rate.",
            }
        return {
            "scope": "skill",
            "skill_id": skill_id,
            "supported": True,
            "eval_window_days": summary.get("eval_window_days", eval_window_days),
            "total_evaluations": summary.get("total_evaluations", 0),
            "completed_count": summary.get("completed_count", 0),
            "win_rate": summary.get("win_rate"),
            "direction_accuracy": summary.get("direction_accuracy"),
            "avg_return": summary.get("avg_return"),
            "win_rate_pct": summary.get("win_rate_pct"),
            "direction_accuracy_pct": summary.get("direction_accuracy_pct"),
            "avg_stock_return_pct": summary.get("avg_stock_return_pct"),
            "avg_simulated_return_pct": summary.get("avg_simulated_return_pct"),
            "computed_at": summary.get("computed_at"),
        }
    except _CONTROL_ERRORS:
        raise
    except Exception:
        logger.warning("[backtest_tools] get_skill_backtest_summary error", exc_info=True)
        return {"error": "Failed to retrieve backtest summary."}


get_skill_backtest_summary_tool = ToolDefinition(
    name="get_skill_backtest_summary",
    description=(
        "Inspect backtest data for a specific skill when skill-scoped stats exist. "
        "Provide skill_id for a targeted lookup; use get_strategy_backtest_summary for overall metrics. "
        "When skill-scoped rollups are unavailable, returns an informational response instead of fabricating metrics."
    ),
    parameters=[
        ToolParameter(
            name="skill_id",
            type="string",
            description="Skill identifier, e.g. 'bull_trend'.",
            required=True,
        ),
        ToolParameter(
            name="eval_window_days",
            type="integer",
            description="Trading-day horizon. Omit when unspecified; unavailable skill evidence remains unsupported.",
            required=False,
        ),
    ],
    handler=_handle_get_skill_backtest_summary,
    category="data",
    policy=_BACKTEST_GLOBAL_READ_POLICY,
)


get_strategy_backtest_summary_tool = ToolDefinition(
    name="get_strategy_backtest_summary",
    description=(
        "Overall historical analysis diagnostics, not individual-skill win rates. Returns sample coverage, "
        "long-only simulated profitability, and evidence limits. Omit the horizon to select the latest available summary."
    ),
    parameters=[
        ToolParameter(
            name="eval_window_days",
            type="integer",
            description="Trading-day horizon. Omit to use an available summary; explicit horizons are never substituted.",
            required=False,
        ),
    ],
    handler=_handle_get_overall_backtest_summary,
    category="data",
    policy=_BACKTEST_GLOBAL_READ_POLICY,
)


# ============================================================
# get_stock_backtest_summary
# ============================================================

def _handle_get_stock_backtest_summary(stock_code: str, eval_window_days: Optional[int] = None, limit: int = 10) -> dict:
    """Get backtest results for a specific stock.

    Returns the summary plus recent evaluation items.
    """
    if error := _window_error(eval_window_days):
        return error
    try:
        check_tool_execution()
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            return {"error": "limit must be an integer between 1 and 100"}
        svc = _get_backtest_service()
        result = {}

        # Per-stock summary
        summary = svc.get_summary(scope="stock", code=stock_code, eval_window_days=eval_window_days)
        check_tool_execution()
        evidence = _backtest_evidence(svc, stock_code, eval_window_days, summary)
        result.update(evidence)
        result["scope"] = "stock"
        result["code"] = stock_code
        if summary:
            result["summary"] = _serialize_overall_backtest_summary(summary, evidence["effective_eval_window_days"])
            result["summary"]["code"] = summary.get("code", stock_code)
        else:
            result["summary"] = None

        # Recent evaluations
        effective = evidence["effective_eval_window_days"]
        evals = (
            svc.get_recent_evaluations(code=stock_code, eval_window_days=effective, limit=limit)
            if effective is not None else {"items": [], "total": 0}
        )
        check_tool_execution()
        items = evals.get("items", [])
        # Slim down items to essential fields
        result["recent_evaluations"] = [
            {
                "analysis_date": item.get("analysis_date"),
                "eval_status": item.get("eval_status"),
                "eval_window_days": item.get("eval_window_days"),
                "engine_version": item.get("engine_version"),
                "position_recommendation": item.get("position_recommendation"),
                "first_hit": item.get("first_hit"),
                "evaluated_at": item.get("evaluated_at"),
                "operation_advice": item.get("operation_advice"),
                "stock_return_pct": item.get("stock_return_pct"),
                "direction_correct": item.get("direction_correct"),
                "outcome": item.get("outcome"),
                "simulated_return_pct": item.get("simulated_return_pct"),
                "hit_stop_loss": item.get("hit_stop_loss"),
                "hit_take_profit": item.get("hit_take_profit"),
            }
            for item in items
        ]
        result["total"] = evals.get("total", 0)
        check_tool_execution()

        if result["summary"] is None and not result["recent_evaluations"]:
            result["info"] = f"No backtest data available for {stock_code} at the selected horizon."

        return result
    except _CONTROL_ERRORS:
        raise
    except Exception:
        logger.warning("[backtest_tools] get_stock_backtest_summary error", exc_info=True)
        return {"error": "Failed to retrieve backtest data."}


get_stock_backtest_summary_tool = ToolDefinition(
    name="get_stock_backtest_summary",
    description=(
        "Get backtest performance data for a specific stock: per-stock summary (win rate, "
        "accuracy, avg return) plus sample coverage, long-only profitability and recent evaluation statuses. "
        "Omit horizon to select available evidence. Direction accuracy is not trade win rate or skill-specific performance. "
        "Read-only, does not trigger new backtests."
    ),
    parameters=[
        ToolParameter(
            name="stock_code",
            type="string",
            description="Stock code, e.g., '600519' (A-share), 'AAPL' (US), 'hk00700' (HK)",
        ),
        ToolParameter(
            name="eval_window_days",
            type="integer",
            description="Trading-day horizon. Omit to use an available summary; explicit horizons are never substituted.",
            required=False,
        ),
        ToolParameter(
            name="limit",
            type="integer",
            description="Max number of recent evaluation records to return (default: 10)",
            required=False,
            default=10,
        ),
    ],
    handler=_handle_get_stock_backtest_summary,
    category="data",
    policy=_BACKTEST_READ_POLICY,
)


# ============================================================
# Exported tool list
# ============================================================

ALL_BACKTEST_TOOLS = [
    get_skill_backtest_summary_tool,
    get_strategy_backtest_summary_tool,
    get_stock_backtest_summary_tool,
]
