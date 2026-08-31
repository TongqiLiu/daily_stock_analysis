# -*- coding: utf-8 -*-
"""
Data tools — wraps DataFetcherManager methods as agent-callable tools.

Tools:
- get_realtime_quote: real-time stock quote
- get_daily_history: historical OHLCV data
- get_chip_distribution: chip distribution analysis
- get_analysis_context: historical analysis context from DB
"""

import logging
from datetime import date, datetime, timezone
from threading import Lock
from typing import Any, Dict, List, Optional, Tuple

from src.agent.tools.execution import check_tool_execution
from src.agent.freshness import is_fresh_market_data_required
from src.agent.tools.registry import ToolParameter, ToolDefinition, ToolPolicy

logger = logging.getLogger(__name__)

_MARKET_DATA_STOCK_POLICY = ToolPolicy.declared(
    read_only=True,
    side_effects=["network_read"],
    permissions=["market_data:read"],
    scope_dimensions=["stock"],
)
_MARKET_DATA_CACHE_POLICY = ToolPolicy.declared(
    read_only=True,
    side_effects=["network_read", "db_read", "db_write_cache"],
    permissions=["market_data:read"],
    scope_dimensions=["stock"],
)
_ANALYSIS_CONTEXT_POLICY = ToolPolicy.declared(
    read_only=True,
    side_effects=["db_read"],
    permissions=["analysis_context:read"],
    scope_dimensions=["stock"],
    cancellation_safe=True,
)
_PORTFOLIO_READ_POLICY = ToolPolicy.declared(
    read_only=True,
    side_effects=["db_read"],
    permissions=["portfolio:read"],
)

_fetcher_manager_singleton = None
_fetcher_manager_lock = Lock()
_DAILY_HISTORY_DEFAULT_DAYS = 60
_DAILY_HISTORY_MAX_DAYS = 365


def _get_fetcher_manager():
    """Return a module-level singleton DataFetcherManager.

    Re-creating the manager on every tool call causes Tushare re-init overhead
    (~2 s each) and prevents circuit-breaker cooldown from taking effect across
    consecutive tool calls within the same agent run.
    """
    from data_provider import DataFetcherManager
    global _fetcher_manager_singleton
    if _fetcher_manager_singleton is None:
        with _fetcher_manager_lock:
            if _fetcher_manager_singleton is None:
                _fetcher_manager_singleton = DataFetcherManager()
    return _fetcher_manager_singleton


def reset_fetcher_manager() -> None:
    """Clear the cached DataFetcherManager so runtime config reloads take effect."""
    global _fetcher_manager_singleton
    with _fetcher_manager_lock:
        _fetcher_manager_singleton = None


def _get_db():
    """Lazy import for DatabaseManager."""
    from src.storage import get_db
    return get_db()


def _normalize_history_days(days: Any) -> Tuple[int, Dict[str, Any]]:
    """Normalize LLM-provided history window and return response metadata."""
    requested_days = days
    warning = None
    try:
        if isinstance(days, bool):
            raise ValueError("bool is not a valid days value")
        effective_days = int(days)
    except (TypeError, ValueError):
        effective_days = _DAILY_HISTORY_DEFAULT_DAYS
        warning = (
            f"Invalid days value {requested_days!r}; "
            f"using default {_DAILY_HISTORY_DEFAULT_DAYS}."
        )

    if effective_days < 1:
        effective_days = 1
        warning = f"days must be >= 1; using {effective_days}."
    elif effective_days > _DAILY_HISTORY_MAX_DAYS:
        effective_days = _DAILY_HISTORY_MAX_DAYS
        warning = f"days exceeds max {_DAILY_HISTORY_MAX_DAYS}; truncated."

    metadata: Dict[str, Any] = {}
    if warning is not None:
        metadata.update(
            {
                "warning": warning,
                "requested_days": requested_days,
                "effective_days": effective_days,
            }
        )
    return effective_days, metadata


def _history_code_candidates(stock_code: str) -> Tuple[List[str], str]:
    """Return cache lookup candidates plus canonical write code."""
    from data_provider.base import canonical_stock_code, normalize_stock_code
    from src.services.stock_list_parser import ParseStatus, parse_analysis_target

    raw_code = str(stock_code or "").strip()
    target = parse_analysis_target(raw_code)
    if target.asset_type == ParseStatus.INDEX:
        # Explicit index identities keep their canonical bucket (``sh000016``
        # / ``csi930955``) so index bars never land in the colliding stock
        # bucket (Story 1.5).
        return [target.canonical_id], target.canonical_id
    normalized_code = canonical_stock_code(normalize_stock_code(raw_code))
    candidates: List[str] = []
    for candidate in (canonical_stock_code(raw_code), normalized_code):
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    return candidates, normalized_code


def _append_history_metadata(response: dict, metadata: Dict[str, Any]) -> dict:
    if metadata:
        response.update(metadata)
    return response


def _compact_fundamental_context(fundamental_context: dict) -> dict:
    """Reduce token footprint for tool responses while keeping key semantics."""
    if not isinstance(fundamental_context, dict):
        return {}
    blocks = (
        "valuation",
        "growth",
        "earnings",
        "institution",
        "capital_flow",
        "dragon_tiger",
        "boards",
    )
    compact = {
        "market": fundamental_context.get("market"),
        "status": fundamental_context.get("status"),
        "coverage": fundamental_context.get("coverage", {}),
    }
    for block in blocks:
        payload = fundamental_context.get(block, {})
        if isinstance(payload, dict):
            compact[block] = {
                "status": payload.get("status"),
                "data": payload.get("data", {}),
            }
        else:
            compact[block] = {"status": "failed", "data": {}}
    return compact


def _compact_portfolio_snapshot(snapshot: dict, include_positions: bool = False, top_n: int = 5) -> dict:
    """Shrink portfolio snapshot payload for default tool responses."""
    if not isinstance(snapshot, dict):
        return {}
    compact_accounts = []
    for account in snapshot.get("accounts", []) or []:
        if not isinstance(account, dict):
            continue
        positions = list(account.get("positions") or [])
        positions = sorted(
            positions,
            key=lambda item: float((item or {}).get("market_value_base") or 0.0),
            reverse=True,
        )
        account_payload = {
            "account_id": account.get("account_id"),
            "account_name": account.get("account_name"),
            "market": account.get("market"),
            "base_currency": account.get("base_currency"),
            "total_equity": account.get("total_equity"),
            "total_market_value": account.get("total_market_value"),
            "total_cash": account.get("total_cash"),
            "realized_pnl": account.get("realized_pnl"),
            "unrealized_pnl": account.get("unrealized_pnl"),
            "fx_stale": account.get("fx_stale"),
        }
        if include_positions:
            account_payload["positions"] = positions
        else:
            account_payload["position_count"] = len(positions)
            account_payload["top_positions"] = positions[:top_n]
        compact_accounts.append(account_payload)

    return {
        "as_of": snapshot.get("as_of"),
        "cost_method": snapshot.get("cost_method"),
        "currency": snapshot.get("currency"),
        "account_count": snapshot.get("account_count"),
        "total_cash": snapshot.get("total_cash"),
        "total_market_value": snapshot.get("total_market_value"),
        "total_equity": snapshot.get("total_equity"),
        "realized_pnl": snapshot.get("realized_pnl"),
        "unrealized_pnl": snapshot.get("unrealized_pnl"),
        "fx_stale": snapshot.get("fx_stale"),
        "accounts": compact_accounts,
    }


def _compact_portfolio_risk(risk: dict, top_n: int = 10) -> dict:
    """Shrink portfolio risk payload for tool responses."""
    if not isinstance(risk, dict):
        return {}
    concentration = risk.get("concentration", {}) or {}
    top_positions = list(concentration.get("top_positions") or [])
    top_positions = sorted(
        top_positions,
        key=lambda item: float((item or {}).get("weight_pct") or 0.0),
        reverse=True,
    )[:top_n]
    stop_loss = risk.get("stop_loss", {}) or {}
    stop_items = list(stop_loss.get("items") or [])
    stop_items = sorted(
        stop_items,
        key=lambda item: float((item or {}).get("loss_pct") or 0.0),
        reverse=True,
    )[:top_n]
    drawdown = risk.get("drawdown", {}) or {}
    return {
        "as_of": risk.get("as_of"),
        "currency": risk.get("currency"),
        "cost_method": risk.get("cost_method"),
        "thresholds": risk.get("thresholds", {}),
        "concentration": {
            "alert": concentration.get("alert", False),
            "top_weight_pct": concentration.get("top_weight_pct"),
            "top_positions": top_positions,
        },
        "drawdown": {
            "alert": drawdown.get("alert", False),
            "max_drawdown_pct": drawdown.get("max_drawdown_pct"),
            "current_drawdown_pct": drawdown.get("current_drawdown_pct"),
            "fx_stale": drawdown.get("fx_stale", False),
        },
        "stop_loss": {
            "near_alert": stop_loss.get("near_alert", False),
            "triggered_count": stop_loss.get("triggered_count", 0),
            "near_count": stop_loss.get("near_count", 0),
            "items": stop_items,
        },
    }


# ============================================================
# get_realtime_quote
# ============================================================

def _handle_get_realtime_quote(stock_code: str) -> dict:
    """Get real-time stock quote."""
    manager = _get_fetcher_manager()
    quote = manager.get_realtime_quote(stock_code)
    if quote is None:
        return {
            "error": f"No realtime quote available for {stock_code}",
            "retriable": False,
            "freshness_required": is_fresh_market_data_required(),
            "note": (
                "All realtime data sources unavailable (network or circuit-breaker). "
                "For a current-analysis turn, do not use historical data to replace this quote."
            ),
        }

    response = {
        "code": quote.code,
        "name": quote.name,
        "price": quote.price,
        "change_pct": quote.change_pct,
        "change_amount": quote.change_amount,
        "volume": quote.volume,
        "amount": quote.amount,
        "volume_ratio": quote.volume_ratio,
        "turnover_rate": quote.turnover_rate,
        "amplitude": quote.amplitude,
        "open": quote.open_price,
        "high": quote.high,
        "low": quote.low,
        "pre_close": quote.pre_close,
        "pe_ratio": quote.pe_ratio,
        "pb_ratio": quote.pb_ratio,
        "total_mv": quote.total_mv,
        "circ_mv": quote.circ_mv,
        "change_60d": quote.change_60d,
        "source": quote.source.value if hasattr(quote.source, 'value') else str(quote.source),
    }
    for field in (
        "fetched_at",
        "provider_timestamp",
        "is_stale",
        "stale_seconds",
        "fallback_from",
        "data_quality",
        "missing_fields",
    ):
        value = getattr(quote, field, None)
        if value is not None:
            response[field] = value
    response["freshness_status"] = (
        "fresh_request" if response.get("fetched_at") else "timestamp_unavailable"
    )
    return response


get_realtime_quote_tool = ToolDefinition(
    name="get_realtime_quote",
    description="Get real-time stock quote including price, change%, volume ratio, "
                "turnover rate, PE, PB, market cap. Returns live market data plus "
                "fetched_at/provider_timestamp freshness metadata.",
    parameters=[
        ToolParameter(
            name="stock_code",
            type="string",
            description="Stock code, e.g., '600519' (A-share), 'AAPL' (US), 'hk00700' (HK)",
        ),
    ],
    handler=_handle_get_realtime_quote,
    category="data",
    policy=_MARKET_DATA_STOCK_POLICY,
)


# ============================================================
# get_daily_history
# ============================================================

def _handle_get_daily_history(stock_code: str, days: int = 60) -> dict:
    """Get daily OHLCV history data."""
    effective_days, metadata = _normalize_history_days(days)
    freshness_required = is_fresh_market_data_required()

    from src.services.history_loader import load_history_df
    df, source = load_history_df(
        stock_code,
        days=effective_days,
        force_refresh=freshness_required,
    )

    if df is None or df.empty:
        return _append_history_metadata(
            {
                "error": f"No historical data available for {stock_code}",
                "freshness_required": freshness_required,
                "refresh_mode": "network" if freshness_required else "cache_or_network",
                "retriable": False if freshness_required else True,
            },
            metadata,
        )

    if source != "db_cache":
        _, normalized_code = _history_code_candidates(stock_code)
        try:
            saved_count = _get_db().save_daily_data(df, normalized_code, source)
            logger.info(
                "Agent daily history persisted for %s (source=%s, new_records=%s)",
                normalized_code,
                source,
                saved_count,
            )
        except Exception as exc:
            logger.warning(
                "Agent daily history persistence failed for %s: %s",
                normalized_code,
                exc,
            )

    # Convert DataFrame to list of dicts (last N records)
    records = df.tail(min(effective_days, len(df))).to_dict(orient="records")
    # Ensure date is string
    for r in records:
        if "date" in r:
            r["date"] = str(r["date"])

    latest_data_date = None
    if "date" in df.columns and not df["date"].empty:
        latest_data_date = str(df["date"].max())[:10]
    fetched_at = datetime.now(timezone.utc).isoformat()

    response_code = stock_code
    if source == "db_cache" and records:
        response_code = records[-1].get("code") or response_code

    return _append_history_metadata({
        "code": response_code,
        "source": source,
        "cache_hit": source == "db_cache",
        "requested_days": effective_days,
        "effective_days": effective_days,
        "actual_records": len(records),
        "partial_cache": source == "db_cache" and len(records) < effective_days,
        "total_records": len(records),
        "latest_data_date": latest_data_date,
        "fetched_at": fetched_at,
        "freshness_required": freshness_required,
        "refresh_mode": "network" if source != "db_cache" else "cache",
        "data": records,
    }, metadata)


get_daily_history_tool = ToolDefinition(
    name="get_daily_history",
    description="Get daily OHLCV (open, high, low, close, volume) historical data "
                "with MA5/MA10/MA20 indicators. Returns the last N trading days. "
                "For a current stock-analysis turn, this tool must perform a network "
                "refresh and returns refresh_mode=network, cache_hit=false, fetched_at, "
                "and latest_data_date.",
    parameters=[
        ToolParameter(
            name="stock_code",
            type="string",
            description="Stock code, e.g., '600519' (A-share), 'AAPL' (US)",
        ),
        ToolParameter(
            name="days",
            type="integer",
            description="Number of trading days to fetch (default: 60)",
            required=False,
            default=60,
        ),
    ],
    handler=_handle_get_daily_history,
    category="data",
    policy=_MARKET_DATA_CACHE_POLICY,
)


# ============================================================
# get_weekly_history
# ============================================================

def _normalize_weekly_history_weeks(weeks: Any) -> int:
    try:
        if isinstance(weeks, bool):
            raise ValueError
        return max(4, min(int(weeks), 260))
    except (TypeError, ValueError):
        return 104


def _aggregate_daily_to_weekly(df):
    """Aggregate standard daily OHLCV rows into completed/partial weeks."""
    import pandas as pd

    if df is None or df.empty or "date" not in df.columns:
        return pd.DataFrame()

    work = df.copy()
    work["date"] = pd.to_datetime(work["date"], errors="coerce")
    work = work.dropna(subset=["date"]).sort_values("date")
    if work.empty:
        return pd.DataFrame()
    work = work.set_index("date")
    aggregations = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
        "amount": "sum",
    }
    available = {name: method for name, method in aggregations.items() if name in work.columns}
    weekly = work.resample("W-FRI", label="left", closed="left").agg(available)
    weekly = weekly.dropna(subset=["close"]).reset_index(names="date")
    if "pct_chg" not in weekly.columns:
        weekly["pct_chg"] = weekly["close"].pct_change() * 100
    weekly["date"] = weekly["date"].dt.strftime("%Y-%m-%d")
    return weekly


def _handle_get_weekly_history(stock_code: str, weeks: int = 104) -> dict:
    """Fetch native Futu weekly bars, falling back to daily aggregation."""
    import pandas as pd

    effective_weeks = _normalize_weekly_history_weeks(weeks)
    freshness_required = is_fresh_market_data_required()
    source = None
    fallback_reason = None
    df = None

    # Futu's provider-native K_WEEK keeps the broker's session/adjustment
    # semantics intact. It is optional and must never block other analyses.
    try:
        from data_provider.futu_fetcher import FutuFetcher

        futu = FutuFetcher()
        if futu.is_available_for_request("weekly_data"):
            df = futu.get_weekly_data(stock_code, weeks=effective_weeks)
            if df is not None and not df.empty:
                source = "FutuFetcher:weekly"
        futu.close()
    except Exception as exc:
        fallback_reason = f"Futu weekly unavailable: {type(exc).__name__}: {exc}"
        logger.info("get_weekly_history(%s): %s", stock_code, fallback_reason)

    if df is None or df.empty:
        from src.services.history_loader import load_history_df

        daily_days = effective_weeks * 7 + 30
        daily_df, daily_source = load_history_df(
            stock_code,
            days=daily_days,
            force_refresh=freshness_required,
        )
        df = _aggregate_daily_to_weekly(daily_df)
        if df is not None and not df.empty:
            source = f"daily_aggregate:{daily_source}"
        elif not fallback_reason:
            fallback_reason = "Futu weekly and daily fallback returned no data"

    if df is None or df.empty:
        return {
            "error": f"No weekly data available for {stock_code}",
            "code": stock_code,
            "period": "weekly",
            "source": source or "none",
            "weeks": effective_weeks,
            "fallback_reason": fallback_reason,
            "freshness_required": freshness_required,
        }

    records = df.tail(effective_weeks).to_dict(orient="records")
    for record in records:
        if "date" in record:
            record["date"] = str(record["date"])[:10]

    latest_date = str(records[-1].get("date") or "")[:10] if records else None
    today = date.today()
    current_week_start = today - pd.Timedelta(days=today.weekday())
    is_partial = bool(latest_date and pd.Timestamp(latest_date).date() >= current_week_start)
    return {
        "code": stock_code,
        "period": "weekly",
        "source": source or "none",
        "weeks": effective_weeks,
        "actual_records": len(records),
        "latest_data_date": latest_date,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "is_partial": is_partial,
        "data_quality": "partial" if is_partial else "complete",
        "fallback_reason": fallback_reason,
        "freshness_required": freshness_required,
        "data": records,
    }


get_weekly_history_tool = ToolDefinition(
    name="get_weekly_history",
    description="Get provider-native weekly OHLCV history when Futu OpenD is enabled. "
                "If Futu is unavailable, aggregate the available daily history and "
                "mark the fallback source explicitly. The current week is marked "
                "is_partial=true and must not be treated as a completed weekly bar.",
    parameters=[
        ToolParameter(
            name="stock_code",
            type="string",
            description="Stock code, e.g., 'US.INTC' or 'AAPL'.",
        ),
        ToolParameter(
            name="weeks",
            type="integer",
            description="Number of weekly bars, 4-260 (default: 104).",
            required=False,
            default=104,
        ),
    ],
    handler=_handle_get_weekly_history,
    category="data",
    policy=_MARKET_DATA_CACHE_POLICY,
)


# ============================================================
# get_chip_distribution
# ============================================================

def _handle_get_chip_distribution(stock_code: str) -> dict:
    """Get chip distribution data."""
    manager = _get_fetcher_manager()
    chip = manager.get_chip_distribution(stock_code)

    if chip is None:
        return {"error": f"No chip distribution data available for {stock_code}"}

    return {
        "code": chip.code,
        "date": chip.date,
        "source": chip.source,
        "profit_ratio": chip.profit_ratio,
        "avg_cost": chip.avg_cost,
        "cost_90_low": chip.cost_90_low,
        "cost_90_high": chip.cost_90_high,
        "concentration_90": chip.concentration_90,
        "cost_70_low": chip.cost_70_low,
        "cost_70_high": chip.cost_70_high,
        "concentration_70": chip.concentration_70,
    }


get_chip_distribution_tool = ToolDefinition(
    name="get_chip_distribution",
    description="Get chip distribution analysis for a stock. Returns profit ratio, "
                "average cost, chip concentration at 90% and 70% levels. "
                "Useful for judging support/resistance and holding structure.",
    parameters=[
        ToolParameter(
            name="stock_code",
            type="string",
            description="A-share stock code, e.g., '600519'",
        ),
    ],
    handler=_handle_get_chip_distribution,
    category="data",
    policy=_MARKET_DATA_STOCK_POLICY,
)


# ============================================================
# get_analysis_context
# ============================================================

def _handle_get_analysis_context(stock_code: str) -> dict:
    """Get stored analysis context from database."""
    check_tool_execution()
    db = _get_db()
    context = db.get_analysis_context(stock_code)
    check_tool_execution()

    if context is None:
        return {"error": f"No analysis context in DB for {stock_code}"}

    # Return safely serializable version (remove raw_data to save tokens)
    safe_context = {}
    for k, v in context.items():
        check_tool_execution()
        if k == "raw_data":
            safe_context["has_raw_data"] = True
            safe_context["raw_data_count"] = len(v) if isinstance(v, list) else 0
        else:
            safe_context[k] = v

    return safe_context


get_analysis_context_tool = ToolDefinition(
    name="get_analysis_context",
    description="Get historical analysis context from the database for a stock. "
                "Returns today's and yesterday's OHLCV data, MA alignment status, "
                "volume and price changes. Provides the technical data foundation.",
    parameters=[
        ToolParameter(
            name="stock_code",
            type="string",
            description="Stock code, e.g., '600519'",
        ),
    ],
    handler=_handle_get_analysis_context,
    category="data",
    policy=_ANALYSIS_CONTEXT_POLICY,
)


# ============================================================
# get_stock_info
# ============================================================

def _handle_get_stock_info(stock_code: str) -> dict:
    """Get stock fundamental information through unified fundamental context."""
    manager = _get_fetcher_manager()
    try:
        fundamental_context = manager.get_fundamental_context(stock_code)
    except Exception as e:
        logger.warning(f"get_stock_info via fundamental pipeline failed for {stock_code}: {e}")
        fundamental_context = manager.build_failed_fundamental_context(stock_code, str(e))

    compact_context = _compact_fundamental_context(fundamental_context)
    valuation = compact_context.get("valuation", {}).get("data", {})
    sector_rankings = compact_context.get("boards", {}).get("data", {})
    belong_boards = manager.get_belong_boards(stock_code)

    stock_name = stock_code.upper()
    try:
        stock_name = manager.get_stock_name(stock_code) or stock_name
    except Exception:
        pass

    return {
        "code": stock_code.upper(),
        "name": stock_name,
        "pe_ratio": valuation.get("pe_ratio"),
        "pb_ratio": valuation.get("pb_ratio"),
        "total_mv": valuation.get("total_mv"),
        "circ_mv": valuation.get("circ_mv"),
        "fundamental_context": compact_context,
        "belong_boards": belong_boards,
        # Compatibility alias for existing callers; prefer belong_boards.
        # Planned for future deprecation in a major version.
        "boards": belong_boards,
        "sector_rankings": sector_rankings,
    }


get_stock_info_tool = ToolDefinition(
    name="get_stock_info",
    description="Get stock fundamental information: valuation, growth, earnings, institution flow, "
                "stock sector membership (belong_boards; boards is compatibility alias) and "
                "sector rankings. Returns a compact fundamental_context to reduce token usage.",
    parameters=[
        ToolParameter(
            name="stock_code",
            type="string",
            description="Stock code: A-share '600519', US 'AAPL', HK '00700'",
        ),
    ],
    handler=_handle_get_stock_info,
    category="data",
    policy=_MARKET_DATA_STOCK_POLICY,
)


# ============================================================
# get_portfolio_snapshot
# ============================================================

def _handle_get_portfolio_snapshot(
    account_id: Optional[int] = None,
    cost_method: str = "fifo",
    include_positions: bool = False,
    include_risk: bool = True,
    as_of: Optional[str] = None,
) -> dict:
    """Get compact portfolio snapshot for account-aware suggestions."""
    method = (cost_method or "fifo").strip().lower()
    if method not in {"fifo", "avg"}:
        return {"error": "cost_method must be fifo or avg"}

    as_of_date = None
    if as_of:
        try:
            as_of_date = date.fromisoformat(str(as_of).strip())
        except ValueError:
            return {"error": "as_of must be YYYY-MM-DD"}

    try:
        from src.services.portfolio_service import PortfolioService
        from src.services.portfolio_risk_service import PortfolioRiskService
    except Exception as exc:
        logger.warning("get_portfolio_snapshot unavailable: %s", exc)
        return {"status": "not_supported", "error": f"portfolio module unavailable: {exc}"}

    try:
        portfolio_service = PortfolioService()
        snapshot = portfolio_service.get_portfolio_snapshot(
            account_id=account_id,
            as_of=as_of_date,
            cost_method=method,
        )
        result = {
            "status": "ok",
            "snapshot": _compact_portfolio_snapshot(snapshot, include_positions=bool(include_positions)),
        }
        if include_risk:
            try:
                risk_service = PortfolioRiskService(portfolio_service=portfolio_service)
                risk = risk_service.get_risk_report(
                    account_id=account_id,
                    as_of=as_of_date,
                    cost_method=method,
                )
                result["risk"] = {"status": "ok", **_compact_portfolio_risk(risk)}
            except Exception as risk_exc:
                logger.warning("get_portfolio_snapshot risk block failed: %s", risk_exc)
                result["risk"] = {"status": "failed", "error": str(risk_exc)}
        return result
    except Exception as exc:
        logger.warning("get_portfolio_snapshot failed: %s", exc)
        return {"status": "failed", "error": f"failed to fetch portfolio snapshot: {exc}"}


get_portfolio_snapshot_tool = ToolDefinition(
    name="get_portfolio_snapshot",
    description="Get portfolio snapshot summary and optional risk blocks. "
                "Default returns compact summary for lower token usage; "
                "set include_positions=true to include full position details.",
    parameters=[
        ToolParameter(
            name="account_id",
            type="integer",
            description="Optional account id; omit to use all active accounts.",
            required=False,
            default=None,
        ),
        ToolParameter(
            name="cost_method",
            type="string",
            description="Cost method: fifo or avg (default: fifo).",
            required=False,
            default="fifo",
            enum=["fifo", "avg"],
        ),
        ToolParameter(
            name="include_positions",
            type="boolean",
            description="Whether to include full positions in snapshot output (default: false).",
            required=False,
            default=False,
        ),
        ToolParameter(
            name="include_risk",
            type="boolean",
            description="Whether to include risk summary block (default: true).",
            required=False,
            default=True,
        ),
        ToolParameter(
            name="as_of",
            type="string",
            description="Optional snapshot date in YYYY-MM-DD format (default: today).",
            required=False,
            default=None,
        ),
    ],
    handler=_handle_get_portfolio_snapshot,
    category="data",
    policy=_PORTFOLIO_READ_POLICY,
)


# ============================================================
# Export all data tools
# ============================================================

ALL_DATA_TOOLS = [
    get_realtime_quote_tool,
    get_daily_history_tool,
    get_weekly_history_tool,
    get_chip_distribution_tool,
    get_analysis_context_tool,
    get_stock_info_tool,
    get_portfolio_snapshot_tool,
]


# ============================================================
# get_capital_flow
# ============================================================

def _handle_get_capital_flow(stock_code: str) -> dict:
    """Get main-force capital flow data for a stock."""
    manager = _get_fetcher_manager()
    try:
        ctx = manager.get_capital_flow_context(stock_code)
    except Exception as exc:
        logger.warning("get_capital_flow failed for %s: %s", stock_code, exc)
        return {
            "stock_code": stock_code,
            "status": "error",
            "error": f"capital flow fetch failed: {exc}",
        }

    status = ctx.get("status", "not_supported")
    if status == "not_supported":
        return {
            "stock_code": stock_code,
            "status": "not_supported",
            "note": "Capital flow data is only available for A-share stocks (not ETFs/indices).",
        }

    data = ctx.get("data", {})
    stock_flow = data.get("stock_flow") or {}
    sector_rankings = data.get("sector_rankings") or {}
    errors = ctx.get("errors") or []

    return {
        "stock_code": stock_code,
        "status": status,
        "main_net_inflow": stock_flow.get("main_net_inflow"),
        "inflow_5d": stock_flow.get("inflow_5d"),
        "inflow_10d": stock_flow.get("inflow_10d"),
        "sector_rankings": {
            "top_inflow_sectors": sector_rankings.get("top", [])[:3],
            "top_outflow_sectors": sector_rankings.get("bottom", [])[:3],
        },
        "errors": errors,
    }


get_capital_flow_tool = ToolDefinition(
    name="get_capital_flow",
    description=(
        "Get main-force (主力) capital flow data for an A-share stock. "
        "Returns today's net inflow, 5-day and 10-day cumulative inflows, "
        "and top sector-level capital flow rankings. "
        "Only supported for A-share individual stocks (not ETFs, indices, HK, or US stocks)."
    ),
    parameters=[
        ToolParameter(
            name="stock_code",
            type="string",
            description="A-share stock code, e.g., '600519'",
        ),
    ],
    handler=_handle_get_capital_flow,
    category="data",
    policy=_MARKET_DATA_STOCK_POLICY,
)


ALL_DATA_TOOLS.append(get_capital_flow_tool)


# ============================================================
# get_fear_greed_index (szdt.tech)
# ============================================================

_fear_greed_service_singleton = None
_fear_greed_service_lock = Lock()


def _get_fear_greed_service():
    """Return a module-level singleton FearGreedService, reusing in-process cache."""
    global _fear_greed_service_singleton
    if _fear_greed_service_singleton is None:
        with _fear_greed_service_lock:
            if _fear_greed_service_singleton is None:
                from src.config import get_config
                from src.services.fear_greed_service import FearGreedService

                cfg = get_config()
                token = getattr(cfg, "szdt_auth_token", None)
                _fear_greed_service_singleton = FearGreedService(auth_token=token)
    return _fear_greed_service_singleton


def reset_fear_greed_service() -> None:
    """Clear the cached FearGreedService so runtime config reloads take effect."""
    global _fear_greed_service_singleton
    with _fear_greed_service_lock:
        _fear_greed_service_singleton = None


def _classify_fear_greed_unavailable(reason: Optional[str]) -> Tuple[str, str, str]:
    """Classify szdt unavailability reason into stable machine-readable buckets."""
    text = (reason or "").strip()
    lower_text = text.lower()

    if not text:
        return (
            "unknown",
            "No data returned from szdt.tech.",
            "Fear/greed data source unavailable; using neutral proxy score 0.",
        )

    if "查询股票个数额度已用完" in text or "手动删减已查询股票" in text:
        return (
            "binding_quota_exhausted",
            text,
            "szdt account stock-binding quota exhausted; remove old bound symbols, then retry.",
        )

    if "无权限" in text or "forbidden" in lower_text or "unauthorized" in lower_text:
        return (
            "auth_failed",
            text,
            "SZDT_AUTH_TOKEN invalid/expired or permission denied.",
        )

    if "timeout" in lower_text:
        return (
            "timeout",
            text,
            "szdt request timed out; retry later.",
        )

    if "invalid stock code format" in lower_text:
        return (
            "invalid_symbol",
            text,
            "Stock code format not supported by szdt mapping.",
        )

    if "http 429" in lower_text or "too many requests" in lower_text:
        return (
            "rate_limited",
            text,
            "szdt rate-limited the request; retry later.",
        )

    return (
        "api_error",
        text,
        "szdt returned an upstream error; fallback to proxy sentiment.",
    )


def _handle_get_fear_greed_index(stock_code: str) -> dict:
    """Fetch stock-level fear/greed index via szdt.tech."""
    svc = _get_fear_greed_service()
    if not svc.is_available:
        return {
            "stock_code": stock_code,
            "status": "not_configured",
            "note": "SZDT_AUTH_TOKEN not set; fear/greed index unavailable.",
        }

    try:
        ctx = svc.get_fear_greed_context(stock_code)
        score_pair = svc.get_score(stock_code)
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_fear_greed_index failed for %s: %s", stock_code, exc)
        return {
            "stock_code": stock_code,
            "status": "error",
            "error": f"fear/greed fetch failed: {exc}",
        }

    if score_pair is None:
        raw_reason = svc.get_last_error(stock_code)
        reason_code, reason_detail, suggested_action = _classify_fear_greed_unavailable(raw_reason)
        return {
            "stock_code": stock_code,
            "status": "unavailable",
            "reason_code": reason_code,
            "reason_detail": reason_detail,
            "suggested_action": suggested_action,
            "proxy_score": 0.0,
            "proxy_label": "中性(代理)",
            "note": (
                "Fear/greed source unavailable. Use proxy_score=0 (neutral) in strategy scoring "
                "instead of marking this strategy as not applicable."
            ),
        }

    score, label = score_pair
    return {
        "stock_code": stock_code,
        "status": "ok",
        "score": score,
        "label": label,
        "score_range": "approx -100 ~ 100; negative = panic, positive = greedy",
        "context_text": ctx or "",
        "interpretation": (
            "Use as a contrarian signal: extreme greed (>60) suggests caution at highs; "
            "extreme panic (<-60) suggests potential accumulation zones. "
            "Do not rely on this alone."
        ),
    }


get_fear_greed_index_tool = ToolDefinition(
    name="get_fear_greed_index",
    description=(
        "Get the stock-level Fear/Greed index (szdt.tech) for a stock. "
        "Returns a sentiment score in the approximate range -100..100 and a Chinese label "
        "(极度恐慌/恐慌/中性/贪婪/极度贪婪). Supports A-share (SH/SZ), Hong Kong (HK), and US tickers. "
        "Useful as a contrarian sentiment signal at market extremes. "
        "When szdt.tech token is missing or stock-binding quota is exhausted, returns status='not_configured'/'unavailable' "
        "with an explanatory note rather than failing."
    ),
    parameters=[
        ToolParameter(
            name="stock_code",
            type="string",
            description="Stock code, e.g., '600519' (A-share), 'HK00700' (HK), 'AAPL' (US).",
        ),
    ],
    handler=_handle_get_fear_greed_index,
    category="data",
    policy=_MARKET_DATA_STOCK_POLICY,
)


ALL_DATA_TOOLS.append(get_fear_greed_index_tool)


# ============================================================
# get_valuation_percentile (akshare baidu + yfinance.info)
# ============================================================

_valuation_service_singleton = None
_valuation_service_lock = Lock()
_dcf_valuation_service_singleton = None
_dcf_valuation_service_lock = Lock()


def _get_valuation_service():
    """Return a module-level singleton ValuationPercentileService."""
    global _valuation_service_singleton
    if _valuation_service_singleton is None:
        with _valuation_service_lock:
            if _valuation_service_singleton is None:
                from src.services.valuation_percentile_service import (
                    ValuationPercentileService,
                )

                _valuation_service_singleton = ValuationPercentileService()
    return _valuation_service_singleton


def reset_valuation_service() -> None:
    """Clear the cached ValuationPercentileService so runtime reloads take effect."""
    global _valuation_service_singleton
    with _valuation_service_lock:
        _valuation_service_singleton = None


def _handle_get_valuation_percentile(
    stock_code: str,
    metric: str = "pe",
    lookback_years: int = 5,
) -> dict:
    """Fetch valuation history percentile (PE/PB/PS) for a stock."""
    svc = _get_valuation_service()
    try:
        result = svc.get_valuation_data(
            stock_code=stock_code,
            metric=metric,
            lookback_years=lookback_years,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "get_valuation_percentile failed for %s/%s: %s", stock_code, metric, exc
        )
        return {
            "stock_code": stock_code,
            "metric": metric,
            "status": "error",
            "error": f"valuation percentile fetch failed: {exc}",
        }

    if result is None:
        return {
            "stock_code": stock_code,
            "metric": metric,
            "status": "unavailable",
            "note": "Service returned no data.",
        }
    return result


get_valuation_percentile_tool = ToolDefinition(
    name="get_valuation_percentile",
    description=(
        "Get historical valuation percentile (PE / PB / PS) for a stock. "
        "Returns current value, percentile rank (0-100), 5-bucket distribution, "
        "and a Chinese rating (极低估/偏低估/合理/偏高估/极高估). "
        "Useful as the core 'is it cheap?' signal for long-term value investing. "
        "A-share: full ~5-year daily history via akshare/baidu. "
        "US: current value only (status='partial', historical percentile not available). "
        "HK: not supported yet (status='unavailable'). "
        "All failures return a dict with status field, never raises."
    ),
    parameters=[
        ToolParameter(
            name="stock_code",
            type="string",
            description="Stock code, e.g., '600519' (A-share), 'AAPL' (US), 'HK00700' (HK).",
        ),
        ToolParameter(
            name="metric",
            type="string",
            description="Valuation metric: pe (default) / pb / ps.",
            required=False,
            enum=["pe", "pb", "ps"],
            default="pe",
        ),
        ToolParameter(
            name="lookback_years",
            type="integer",
            description="History window in years (1, 3, 5, 10). Default 5.",
            required=False,
            default=5,
        ),
    ],
    handler=_handle_get_valuation_percentile,
    category="data",
    policy=_MARKET_DATA_CACHE_POLICY,
)


ALL_DATA_TOOLS.append(get_valuation_percentile_tool)


# ============================================================
# get_dcf_valuation (lightweight bull/base/bear DCF)
# ============================================================

def _get_dcf_valuation_service():
    """Return a module-level singleton DCFValuationService."""
    global _dcf_valuation_service_singleton
    if _dcf_valuation_service_singleton is None:
        with _dcf_valuation_service_lock:
            if _dcf_valuation_service_singleton is None:
                from src.services.dcf_valuation_service import DCFValuationService

                _dcf_valuation_service_singleton = DCFValuationService()
    return _dcf_valuation_service_singleton


def reset_dcf_valuation_service() -> None:
    """Clear cached DCFValuationService instance."""
    global _dcf_valuation_service_singleton
    with _dcf_valuation_service_lock:
        _dcf_valuation_service_singleton = None


def _handle_get_dcf_valuation(
    stock_code: str,
    forecast_years: int = 5,
) -> dict:
    """Fetch lightweight DCF valuation scenarios for a stock."""
    svc = _get_dcf_valuation_service()
    try:
        result = svc.get_dcf_valuation(
            stock_code=stock_code,
            forecast_years=forecast_years,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_dcf_valuation failed for %s: %s", stock_code, exc)
        return {
            "stock_code": stock_code,
            "status": "error",
            "error": f"dcf valuation failed: {exc}",
        }

    if result is None:
        return {
            "stock_code": stock_code,
            "status": "unavailable",
            "note": "DCF valuation service returned no data.",
        }
    return result


get_dcf_valuation_tool = ToolDefinition(
    name="get_dcf_valuation",
    description=(
        "Run a lightweight DCF valuation and return bull/base/bear intrinsic value scenarios. "
        "Outputs forecast assumptions, enterprise/equity value, intrinsic price per share, "
        "and upside/downside percentage vs current price. "
        "Designed for long-term valuation analysis and AI-cycle growth stock screening. "
        "Returns status='partial'/'unavailable' when key financial inputs are incomplete."
    ),
    parameters=[
        ToolParameter(
            name="stock_code",
            type="string",
            description="Stock code, e.g., '600519' (A-share), 'NVDA' (US), 'HK00700' (HK).",
        ),
        ToolParameter(
            name="forecast_years",
            type="integer",
            description="Explicit forecast horizon in years (3-10, default: 5).",
            required=False,
            default=5,
        ),
    ],
    handler=_handle_get_dcf_valuation,
    category="data",
    policy=_MARKET_DATA_CACHE_POLICY,
)


ALL_DATA_TOOLS.append(get_dcf_valuation_tool)
