# -*- coding: utf-8 -*-
"""
Analysis tools — wraps StockTrendAnalyzer as an agent-callable tool.

Tools:
- analyze_trend: comprehensive technical trend analysis
- calculate_multi_strategy_score: deterministic 12-strategy evidence and score validation
"""

import json
import logging
import math
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional

from src.agent.tools.registry import ToolParameter, ToolDefinition, ToolPolicy

logger = logging.getLogger(__name__)

_ANALYSIS_READ_POLICY = ToolPolicy.declared(
    read_only=True,
    side_effects=["network_read", "db_read"],
    permissions=["market_data:read"],
    scope_dimensions=["stock"],
)

_DETERMINISTIC_ANALYSIS_POLICY = ToolPolicy.declared(
    read_only=True,
    side_effects=[],
    permissions=[],
)


MULTI_STRATEGY_SCORE_SPECS = (
    ("bull_trend", "多头趋势", Decimal("1.0")),
    ("chan_theory", "缠论", Decimal("1.0")),
    ("ma_golden_cross", "均线金叉", Decimal("0.9")),
    ("shrink_pullback", "缩量回踩", Decimal("0.9")),
    ("volume_breakout", "放量突破", Decimal("0.8")),
    ("wave_theory", "波浪理论", Decimal("0.8")),
    ("bottom_volume", "底部放量", Decimal("0.7")),
    ("box_oscillation", "箱体震荡", Decimal("0.7")),
    ("dragon_head", "龙头策略", Decimal("0.7")),
    ("emotion_cycle", "情绪周期", Decimal("0.6")),
    ("one_yang_three_yin", "一阳夹三阴", Decimal("0.6")),
    ("fear_greed_sentiment", "贪恐情绪极值", Decimal("0.5")),
)

_MULTI_STRATEGY_TOTAL_WEIGHT = sum(
    (weight for _, _, weight in MULTI_STRATEGY_SCORE_SPECS),
    Decimal("0"),
)
_MULTI_STRATEGY_SPEC_BY_ID = {
    strategy_id: (display_name, weight)
    for strategy_id, display_name, weight in MULTI_STRATEGY_SCORE_SPECS
}
_MULTI_STRATEGY_ID_BY_NAME = {
    display_name: strategy_id
    for strategy_id, display_name, _ in MULTI_STRATEGY_SCORE_SPECS
}

_SIGNAL_ALIASES = {
    "buy": "buy",
    "买入": "buy",
    "hold": "hold",
    "观望": "hold",
    "sell": "sell",
    "卖出": "sell",
    "unavailable": "unavailable",
    "不可评估": "unavailable",
}
_SIGNAL_LABELS = {
    "buy": "买入",
    "hold": "观望",
    "sell": "卖出",
    "unavailable": "不可评估",
}
_STRENGTH_ALIASES = {
    "strong": "strong",
    "强": "strong",
    "medium": "medium",
    "中": "medium",
    "weak": "weak",
    "弱": "weak",
    "none": "none",
    "-": "none",
    "无": "none",
}
_STRENGTH_LABELS = {
    "strong": "强",
    "medium": "中",
    "weak": "弱",
    "none": "-",
}
_EVIDENCE_ALIASES = {
    "complete": "complete",
    "完整": "complete",
    "partial": "partial",
    "部分": "partial",
    "missing": "missing",
    "缺失": "missing",
}
_EVIDENCE_LABELS = {
    "complete": "完整",
    "partial": "部分",
    "missing": "缺失",
}


def _multi_strategy_score_matches_band(signal: str, strength: str, score: Decimal) -> bool:
    """Validate one score against the score bands promised by the meta-skill."""
    ranges = {
        ("buy", "strong"): (Decimal("80"), Decimal("95")),
        ("buy", "medium"): (Decimal("65"), Decimal("80")),
        ("buy", "weak"): (Decimal("55"), Decimal("65")),
        ("hold", "medium"): (Decimal("45"), Decimal("55")),
        ("sell", "weak"): (Decimal("35"), Decimal("45")),
        ("sell", "medium"): (Decimal("20"), Decimal("35")),
        ("sell", "strong"): (Decimal("5"), Decimal("20")),
    }
    bounds = ranges.get((signal, strength))
    return bool(bounds and bounds[0] <= score <= bounds[1])


def _format_decimal(value: Decimal) -> str:
    """Render a Decimal without scientific notation or insignificant zeros."""
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def _multi_strategy_decision(score: Decimal, *, leveraged: bool) -> tuple[str, str]:
    """Map a deterministic score to a decision and risk-aware position guide."""
    if score >= Decimal("75"):
        return (
            "强烈买入",
            "杠杆ETF上限20%，必须分批" if leveraged else "重仓50-80%",
        )
    if score >= Decimal("60"):
        return (
            "偏多",
            "杠杆ETF上限10-15%" if leveraged else "中仓30-50%",
        )
    if score >= Decimal("40"):
        return (
            "观望",
            "杠杆ETF观察或不超过5%" if leveraged else "轻仓不超过20%",
        )
    if score > Decimal("25"):
        return (
            "偏空",
            "杠杆ETF减至不超过5%" if leveraged else "减仓至不超过10%",
        )
    return "卖出", "清仓"


def _handle_calculate_multi_strategy_score(
    scores_json: str,
    instrument_type: str = "standard",
) -> dict:
    """Validate 12 strategy rows and calculate their weighted score deterministically."""
    try:
        raw_scores: Any = json.loads(scores_json) if isinstance(scores_json, str) else scores_json
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        return {
            "status": "invalid",
            "error": "scores_json must be a valid JSON array",
            "detail": str(exc),
        }

    if not isinstance(raw_scores, list):
        return {
            "status": "invalid",
            "error": "scores_json must decode to a JSON array",
        }

    normalized_instrument_type = str(instrument_type or "standard").strip().lower()
    if normalized_instrument_type not in {"standard", "leveraged_etf"}:
        return {
            "status": "invalid",
            "error": "instrument_type must be standard or leveraged_etf",
        }

    entries_by_id: dict[str, dict] = {}
    issues: list[str] = []
    for index, item in enumerate(raw_scores, start=1):
        if not isinstance(item, dict):
            issues.append(f"row {index}: expected an object")
            continue
        raw_strategy = str(item.get("strategy") or item.get("strategy_id") or "").strip()
        strategy_id = (
            raw_strategy
            if raw_strategy in _MULTI_STRATEGY_SPEC_BY_ID
            else _MULTI_STRATEGY_ID_BY_NAME.get(raw_strategy, "")
        )
        if not strategy_id:
            issues.append(f"row {index}: unknown strategy {raw_strategy!r}")
            continue
        if strategy_id in entries_by_id:
            issues.append(f"row {index}: duplicate strategy {strategy_id}")
            continue
        entries_by_id[strategy_id] = item

    expected_ids = [strategy_id for strategy_id, _, _ in MULTI_STRATEGY_SCORE_SPECS]
    missing_ids = [strategy_id for strategy_id in expected_ids if strategy_id not in entries_by_id]
    if missing_ids:
        issues.append(f"missing strategies: {', '.join(missing_ids)}")
    if len(raw_scores) != len(expected_ids):
        issues.append(f"expected 12 rows, received {len(raw_scores)}")

    normalized_rows: list[dict] = []
    weighted_numerator = Decimal("0")
    included_weight = Decimal("0")
    complete_count = 0
    partial_count = 0
    missing_count = 0

    for strategy_id, display_name, weight in MULTI_STRATEGY_SCORE_SPECS:
        item = entries_by_id.get(strategy_id)
        if item is None:
            continue
        evidence_status = _EVIDENCE_ALIASES.get(
            str(item.get("evidence_status") or "").strip().lower()
        )
        if evidence_status is None:
            issues.append(
                f"{strategy_id}: evidence_status must be complete, partial, or missing"
            )
            continue

        signal = _SIGNAL_ALIASES.get(str(item.get("signal") or "").strip().lower())
        strength = _STRENGTH_ALIASES.get(str(item.get("strength") or "").strip().lower())
        reason = str(item.get("reason") or item.get("evidence") or "").strip()
        if not reason:
            issues.append(f"{strategy_id}: reason is required")

        if evidence_status == "missing":
            missing_count += 1
            if signal != "unavailable" or strength != "none":
                issues.append(
                    f"{strategy_id}: missing evidence must use signal=不可评估 and strength=-"
                )
            if item.get("score") not in (None, "", "-"):
                issues.append(f"{strategy_id}: missing evidence must not provide a numeric score")
            normalized_rows.append({
                "strategy": strategy_id,
                "display_name": display_name,
                "signal": "不可评估",
                "strength": "-",
                "score": None,
                "weight": float(weight),
                "included": False,
                "evidence_status": "缺失",
                "reason": reason,
            })
            continue

        if evidence_status == "complete":
            complete_count += 1
        else:
            partial_count += 1
        if signal not in {"buy", "hold", "sell"}:
            issues.append(f"{strategy_id}: invalid signal")
            continue
        if strength not in {"strong", "medium", "weak"}:
            issues.append(f"{strategy_id}: invalid strength")
            continue
        if evidence_status == "partial" and strength == "strong":
            issues.append(f"{strategy_id}: partial evidence cannot use strong strength")
            continue
        try:
            raw_score = item.get("score")
            if isinstance(raw_score, bool):
                raise ValueError("boolean score")
            score = Decimal(str(raw_score))
            if not math.isfinite(float(score)):
                raise ValueError("non-finite score")
        except (TypeError, ValueError, ArithmeticError, OverflowError):
            issues.append(f"{strategy_id}: score must be a finite number")
            continue
        if not _multi_strategy_score_matches_band(signal, strength, score):
            issues.append(
                f"{strategy_id}: score {score} does not match "
                f"{_SIGNAL_LABELS[signal]}/{_STRENGTH_LABELS[strength]} band"
            )
            continue

        weighted_value = score * weight
        weighted_numerator += weighted_value
        included_weight += weight
        normalized_rows.append({
            "strategy": strategy_id,
            "display_name": display_name,
            "signal": _SIGNAL_LABELS[signal],
            "strength": _STRENGTH_LABELS[strength],
            "score": float(score),
            "weight": float(weight),
            "included": True,
            "weighted_value": float(weighted_value),
            "evidence_status": _EVIDENCE_LABELS[evidence_status],
            "reason": reason,
        })

    if issues:
        return {
            "status": "invalid",
            "error": "multi-strategy score validation failed",
            "issues": issues,
            "expected_strategy_order": expected_ids,
            "configured_total_weight": float(_MULTI_STRATEGY_TOTAL_WEIGHT),
        }
    if included_weight <= 0:
        return {
            "status": "invalid",
            "error": "no strategy has evaluable evidence",
        }

    weighted_score = (weighted_numerator / included_weight).quantize(
        Decimal("0.1"),
        rounding=ROUND_HALF_UP,
    )
    evidence_coverage = (
        included_weight / _MULTI_STRATEGY_TOTAL_WEIGHT * Decimal("100")
    ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    leveraged = normalized_instrument_type == "leveraged_etf"
    decision, position_guidance = _multi_strategy_decision(weighted_score, leveraged=leveraged)
    if evidence_coverage < Decimal("70.0") or complete_count + partial_count < 8:
        decision = "证据不足（观望）"
        position_guidance = "不新增仓位，补齐证据后重评"

    return {
        "status": "ok",
        "instrument_type": normalized_instrument_type,
        "rows": normalized_rows,
        "weighted_numerator": float(weighted_numerator),
        "included_weight": float(included_weight),
        "configured_total_weight": float(_MULTI_STRATEGY_TOTAL_WEIGHT),
        "weighted_score": float(weighted_score),
        "decision": decision,
        "position_guidance": position_guidance,
        "evidence": {
            "complete_count": complete_count,
            "partial_count": partial_count,
            "missing_count": missing_count,
            "coverage_pct": float(evidence_coverage),
        },
        "formula": (
            f"{_format_decimal(weighted_numerator)} / {_format_decimal(included_weight)} "
            f"= {_format_decimal(weighted_score)}"
        ),
    }


calculate_multi_strategy_score_tool = ToolDefinition(
    name="calculate_multi_strategy_score",
    description=(
        "Validate all 12 multi-strategy rows and calculate the weighted score, evidence coverage, "
        "decision, and risk-aware position guidance deterministically. Pass scores_json as a JSON "
        "array in canonical strategy order; each row needs strategy, signal, strength, score, "
        "evidence_status, and reason. Missing evidence must use signal='不可评估', strength='-', "
        "and score=null. Use instrument_type='leveraged_etf' for leveraged ETFs."
    ),
    parameters=[
        ToolParameter(
            name="scores_json",
            type="string",
            description=(
                "JSON array of exactly 12 rows. Example row: "
                "{\"strategy\":\"bull_trend\",\"signal\":\"买入\",\"strength\":\"中\","
                "\"score\":72,\"evidence_status\":\"complete\",\"reason\":\"MA5>MA10\"}."
            ),
        ),
        ToolParameter(
            name="instrument_type",
            type="string",
            description=(
                "Required explicit classification: standard for stocks/ordinary ETFs; "
                "leveraged_etf for 2x/3x leveraged ETFs such as KORU."
            ),
            enum=["standard", "leveraged_etf"],
        ),
    ],
    handler=_handle_calculate_multi_strategy_score,
    category="analysis",
    policy=_DETERMINISTIC_ANALYSIS_POLICY,
)


def _fetch_trend_data(stock_code: str):
    """Fetch historical OHLCV (DataFrame) for trend analysis. DB first, then DataFetcher fallback."""
    from src.services.history_loader import load_history_df

    df, _ = load_history_df(stock_code, days=60)
    return df


def _handle_analyze_trend(stock_code: str) -> dict:
    """Run technical trend analysis on a stock."""
    from src.stock_analyzer import StockTrendAnalyzer

    if not (stock_code and str(stock_code).strip()):
        return {"error": "stock_code is required"}

    df = _fetch_trend_data(stock_code)
    if df is None or df.empty:
        return {"error": f"No historical data available for trend analysis on {stock_code}"}

    if len(df) < 20:
        return {"error": f"Insufficient data for trend analysis on {stock_code} (need >= 20 days)"}

    analyzer = StockTrendAnalyzer()
    try:
        result = analyzer.analyze(df, stock_code)
    except Exception:
        logger.warning("analyze_trend(%s): Trend analysis failed", stock_code, exc_info=True)
        return {"error": f"Trend analysis failed for {stock_code}"}

    return {
        "code": result.code,
        "trend_status": result.trend_status.value,
        "ma_alignment": result.ma_alignment,
        "trend_strength": result.trend_strength,
        "ma5": result.ma5,
        "ma10": result.ma10,
        "ma20": result.ma20,
        "ma60": result.ma60,
        "current_price": result.current_price,
        "bias_ma5": round(result.bias_ma5, 2),
        "bias_ma10": round(result.bias_ma10, 2),
        "bias_ma20": round(result.bias_ma20, 2),
        "volume_status": result.volume_status.value,
        "volume_ratio_5d": round(result.volume_ratio_5d, 2),
        "volume_trend": result.volume_trend,
        "support_ma5": result.support_ma5,
        "support_ma10": result.support_ma10,
        "resistance_levels": result.resistance_levels,
        "support_levels": result.support_levels,
        "macd_dif": round(result.macd_dif, 4),
        "macd_dea": round(result.macd_dea, 4),
        "macd_bar": round(result.macd_bar, 4),
        "macd_status": result.macd_status.value,
        "macd_signal": result.macd_signal,
        "rsi_6": round(result.rsi_6, 2),
        "rsi_12": round(result.rsi_12, 2),
        "rsi_24": round(result.rsi_24, 2),
        "rsi_status": result.rsi_status.value,
        "rsi_signal": result.rsi_signal,
        "buy_signal": result.buy_signal.value,
        "signal_score": result.signal_score,
        "signal_reasons": result.signal_reasons,
        "risk_factors": result.risk_factors,
    }


analyze_trend_tool = ToolDefinition(
    name="analyze_trend",
    description="Run comprehensive technical trend analysis on a stock. "
                "Fetches historical data from database or data source. "
                "Returns MA alignment, bias rates, MACD status, RSI levels, "
                "volume analysis, support/resistance levels, and a buy/sell signal "
                "with a score (0-100).",
    parameters=[
        ToolParameter(
            name="stock_code",
            type="string",
            description="Stock code to analyze, e.g., '600519'",
        ),
    ],
    handler=_handle_analyze_trend,
    category="analysis",
    policy=_ANALYSIS_READ_POLICY,
)


# ============================================================
# calculate_ma — flexible moving average calculator
# ============================================================

def _handle_calculate_ma(stock_code: str, periods: Optional[str] = None, days: int = 120) -> dict:
    """Calculate moving averages for arbitrary periods from historical K-line data."""
    from src.services.history_loader import load_history_df

    df, source = load_history_df(stock_code, days=days)

    if df is None or df.empty:
        return {"error": f"No historical data for {stock_code}"}

    # Parse requested periods (default: 5,10,20,30,60,120,250)
    default_periods = [5, 10, 20, 30, 60, 120, 250]
    if periods:
        try:
            requested = [int(p.strip()) for p in periods.split(",") if p.strip().isdigit()]
            period_list = sorted(set(requested)) if requested else default_periods
        except Exception:
            period_list = default_periods
    else:
        period_list = default_periods

    close = df["close"]
    current_price = float(close.iloc[-1])
    result: dict = {
        "code": stock_code,
        "source": source,
        "current_price": round(current_price, 2),
        "data_points": len(df),
        "ma": {},
    }

    for period in period_list:
        if len(close) < period:
            result["ma"][f"ma{period}"] = None
            continue
        ma_val = float(close.rolling(window=period).mean().iloc[-1])
        bias = round((current_price - ma_val) / ma_val * 100, 2) if ma_val else None
        result["ma"][f"ma{period}"] = {
            "value": round(ma_val, 2),
            "bias_pct": bias,
            "price_above": current_price > ma_val,
        }

    # Summary: how many MAs is the price above?
    ma_values = [v for v in result["ma"].values() if v is not None]
    above_count = sum(1 for v in ma_values if v["price_above"])
    result["above_ma_count"] = above_count
    result["total_ma_count"] = len(ma_values)
    result["ma_alignment"] = (
        "多头排列" if above_count == len(ma_values)
        else "空头排列" if above_count == 0
        else f"混合({above_count}/{len(ma_values)}条均线上方)"
    )
    return result


calculate_ma_tool = ToolDefinition(
    name="calculate_ma",
    description="Calculate moving averages (MA5/10/20/30/60/120/250 or custom periods) "
                "for a stock. Returns each MA value, price bias %, and whether price "
                "is above each MA. Also returns overall MA alignment (多头/空头/混合).",
    parameters=[
        ToolParameter(
            name="stock_code",
            type="string",
            description="Stock code, e.g., '600519'",
        ),
        ToolParameter(
            name="periods",
            type="string",
            description="Comma-separated MA periods to calculate (default: '5,10,20,30,60,120,250'). "
                        "E.g., '5,10,20,60'",
            required=False,
            default="5,10,20,30,60,120,250",
        ),
        ToolParameter(
            name="days",
            type="integer",
            description="Number of trading days to fetch history for (default: 120)",
            required=False,
            default=120,
        ),
    ],
    handler=_handle_calculate_ma,
    category="analysis",
    policy=_ANALYSIS_READ_POLICY,
)


# ============================================================
# get_volume_analysis — volume-price relationship analysis
# ============================================================

def _handle_get_volume_analysis(stock_code: str, days: int = 30) -> dict:
    """Analyse volume-price patterns over recent trading days."""
    from src.services.history_loader import load_history_df
    import pandas as pd

    df, source = load_history_df(stock_code, days=max(days + 20, 60))

    if df is None or df.empty:
        return {"error": f"No historical data for {stock_code}"}

    df = df.tail(days).copy()
    if len(df) < 5:
        return {"error": f"Insufficient data for volume analysis (got {len(df)} days, need >= 5)"}

    close = df["close"]
    volume = df["volume"]

    # Average volumes
    avg_vol_5 = float(volume.tail(5).mean())
    avg_vol_10 = float(volume.tail(10).mean())
    avg_vol_20 = float(volume.tail(20).mean()) if len(df) >= 20 else avg_vol_10
    latest_vol = float(volume.iloc[-1])
    vol_ratio_5d = round(latest_vol / avg_vol_5, 2) if avg_vol_5 > 0 else None
    vol_ratio_20d = round(latest_vol / avg_vol_20, 2) if avg_vol_20 > 0 else None

    # Price direction for each day
    price_up = close.diff() > 0  # True = up day

    # Volume-price correlation (last N days)
    try:
        import numpy as np
        vp_corr = float(pd.Series(volume.values, dtype=float).corr(pd.Series(close.values, dtype=float)))
        vp_corr = round(vp_corr, 3)
    except Exception:
        vp_corr = None

    # Detect shrinking volume on up days (bearish divergence) vs expanding on up days (healthy)
    up_days = df[price_up]
    down_days = df[~price_up]
    avg_up_vol = float(up_days["volume"].mean()) if len(up_days) > 0 else 0
    avg_down_vol = float(down_days["volume"].mean()) if len(down_days) > 0 else 0

    # Volume trend: compare last 5 days vs prior 5 days
    if len(volume) >= 10:
        recent_5_avg = float(volume.tail(5).mean())
        prior_5_avg = float(volume.iloc[-10:-5].mean())
        vol_trend_pct = round((recent_5_avg - prior_5_avg) / prior_5_avg * 100, 1) if prior_5_avg > 0 else 0
        vol_trend = "放量" if vol_trend_pct > 20 else "缩量" if vol_trend_pct < -20 else "量能平稳"
    else:
        vol_trend_pct = 0
        vol_trend = "数据不足"

    # High-volume days (> 2x 20d avg)
    high_vol_days = int((volume > avg_vol_20 * 2).sum()) if avg_vol_20 > 0 else 0

    # Volume-price pattern interpretation
    pattern = "未知"
    if avg_up_vol > avg_down_vol * 1.3:
        pattern = "量价配合良好（上涨放量、下跌缩量）"
    elif avg_down_vol > avg_up_vol * 1.3:
        pattern = "量价背离（下跌放量、上涨缩量，偏空）"
    elif vol_ratio_5d and vol_ratio_5d > 1.5:
        pattern = "近期明显放量"
    elif vol_ratio_5d and vol_ratio_5d < 0.6:
        pattern = "近期明显缩量"
    else:
        pattern = "量价关系中性"

    return {
        "code": stock_code,
        "source": source,
        "period_days": len(df),
        "latest_volume": latest_vol,
        "avg_volume_5d": round(avg_vol_5, 0),
        "avg_volume_20d": round(avg_vol_20, 0),
        "volume_ratio_vs_5d": vol_ratio_5d,
        "volume_ratio_vs_20d": vol_ratio_20d,
        "avg_up_day_volume": round(avg_up_vol, 0),
        "avg_down_day_volume": round(avg_down_vol, 0),
        "volume_trend": vol_trend,
        "volume_trend_pct": vol_trend_pct,
        "high_volume_days": high_vol_days,
        "volume_price_corr": vp_corr,
        "pattern": pattern,
    }


get_volume_analysis_tool = ToolDefinition(
    name="get_volume_analysis",
    description="Analyse volume-price relationship for a stock. Returns volume ratios, "
                "average volume on up vs down days, volume trend (expanding/shrinking), "
                "and pattern interpretation (量价配合/背离). Useful for confirming trend "
                "strength and detecting distribution or accumulation phases.",
    parameters=[
        ToolParameter(
            name="stock_code",
            type="string",
            description="Stock code, e.g., '600519'",
        ),
        ToolParameter(
            name="days",
            type="integer",
            description="Number of recent trading days to analyse (default: 30)",
            required=False,
            default=30,
        ),
    ],
    handler=_handle_get_volume_analysis,
    category="analysis",
    policy=_ANALYSIS_READ_POLICY,
)


# ============================================================
# analyze_pattern — candlestick / chart pattern recognition
# ============================================================

def _handle_analyze_pattern(stock_code: str, days: int = 60) -> dict:
    """Detect common candlestick and chart patterns in recent price history."""
    from src.services.history_loader import load_history_df

    df, source = load_history_df(stock_code, days=max(days, 120))

    if df is None or df.empty:
        return {"error": f"No historical data for {stock_code}"}

    df = df.tail(days).copy().reset_index(drop=True)
    if len(df) < 10:
        return {"error": f"Insufficient data for pattern analysis (got {len(df)} days, need >= 10)"}

    o = df["open"].values
    h = df["high"].values
    l = df["low"].values   # noqa: E741
    c = df["close"].values
    v = df["volume"].values if "volume" in df.columns else None

    patterns_detected = []
    n = len(c)

    # ---- Helpers ----
    def body(i):
        return abs(c[i] - o[i])

    def upper_shadow(i):
        return h[i] - max(c[i], o[i])

    def lower_shadow(i):
        return min(c[i], o[i]) - l[i]

    def is_bullish(i):
        return c[i] > o[i]

    def is_bearish(i):
        return c[i] < o[i]

    avg_body = sum(body(i) for i in range(n)) / n if n > 0 else 1

    # --- Single-candle patterns (last 3 days) ---
    for i in range(max(0, n - 3), n):
        bd = body(i)
        us = upper_shadow(i)
        ls = lower_shadow(i)

        # Doji
        if bd < avg_body * 0.1 and (us + ls) > bd * 3:
            patterns_detected.append({
                "pattern": "十字星 (Doji)", "type": "reversal_signal",
                "day_offset": -(n - 1 - i),
                "strength": "弱", "desc": "多空平衡，可能变盘信号"
            })

        # Hammer / Hanging Man
        if ls > body(i) * 2 and us < body(i) * 0.5:
            label = "锤子线 (Hammer)" if i == 0 or c[i] >= c[i - 1] else "上吊线 (Hanging Man)"
            patterns_detected.append({
                "pattern": label, "type": "reversal_signal",
                "day_offset": -(n - 1 - i),
                "strength": "中", "desc": "下影线长，潜在支撑/反转"
            })

        # Shooting Star / Inverted Hammer
        if us > body(i) * 2 and ls < body(i) * 0.5:
            label = "流星线 (Shooting Star)" if is_bearish(i) else "倒锤子"
            patterns_detected.append({
                "pattern": label, "type": "bearish_signal",
                "day_offset": -(n - 1 - i),
                "strength": "中", "desc": "上影线长，潜在压力/反转"
            })

        # Big bullish / bearish candle
        if bd > avg_body * 2.5:
            label = "大阳线" if is_bullish(i) else "大阴线"
            t = "bullish" if is_bullish(i) else "bearish"
            patterns_detected.append({
                "pattern": label, "type": t,
                "day_offset": -(n - 1 - i),
                "strength": "强", "desc": "实体大，方向明确"
            })

    # --- Multi-candle patterns (use last 10 days) ---
    if n >= 3:
        i = n - 1
        # Morning Star (早晨之星) — bottom reversal
        if (is_bearish(i - 2) and body(i - 2) > avg_body * 1.5
                and body(i - 1) < avg_body * 0.4
                and is_bullish(i) and body(i) > avg_body * 1.5
                and c[i] > (o[i - 2] + c[i - 2]) / 2):
            patterns_detected.append({
                "pattern": "早晨之星 (Morning Star)", "type": "bullish_reversal",
                "day_offset": -2, "strength": "强", "desc": "三根K线底部反转形态"
            })

        # Evening Star (黄昏之星) — top reversal
        if (is_bullish(i - 2) and body(i - 2) > avg_body * 1.5
                and body(i - 1) < avg_body * 0.4
                and is_bearish(i) and body(i) > avg_body * 1.5
                and c[i] < (o[i - 2] + c[i - 2]) / 2):
            patterns_detected.append({
                "pattern": "黄昏之星 (Evening Star)", "type": "bearish_reversal",
                "day_offset": -2, "strength": "强", "desc": "三根K线顶部反转形态"
            })

        # Engulfing (吞没形态)
        if (is_bullish(i) and is_bearish(i - 1)
                and o[i] < c[i - 1] and c[i] > o[i - 1]):
            patterns_detected.append({
                "pattern": "看涨吞没 (Bullish Engulfing)", "type": "bullish_reversal",
                "day_offset": -1, "strength": "强", "desc": "阳线完全覆盖前一阴线"
            })
        elif (is_bearish(i) and is_bullish(i - 1)
              and o[i] > c[i - 1] and c[i] < o[i - 1]):
            patterns_detected.append({
                "pattern": "看跌吞没 (Bearish Engulfing)", "type": "bearish_reversal",
                "day_offset": -1, "strength": "强", "desc": "阴线完全覆盖前一阳线"
            })

    # --- Chart patterns over the window ---
    # Double bottom detection (简化版: 两个相近低点 + 中间高点)
    recent_lows_idx = sorted(range(n), key=lambda i: l[i])[:5]
    if len(recent_lows_idx) >= 2:
        lo1, lo2 = sorted(recent_lows_idx[:2])
        if lo2 - lo1 >= 5 and abs(l[lo1] - l[lo2]) / max(l[lo1], l[lo2]) < 0.03:
            mid_high = max(h[lo1:lo2 + 1])
            if mid_high > l[lo1] * 1.03:
                patterns_detected.append({
                    "pattern": "双底 (Double Bottom)", "type": "bullish_reversal",
                    "day_offset": -(n - 1 - lo2),
                    "strength": "强", "desc": "两个相近低点，W型底部形态"
                })

    # Upward breakout: closes above 20d high (excluding last day itself)
    if n >= 21:
        high_20d = max(h[n - 21:n - 1])
        if c[-1] > high_20d and (v is None or v[-1] > sum(v[n - 6:n - 1]) / 5 * 1.5):
            patterns_detected.append({
                "pattern": "放量突破20日高点", "type": "bullish_breakout",
                "day_offset": 0, "strength": "强", "desc": "收盘突破近20日最高，量能配合"
            })

    # Price in consolidation box (box oscillation)
    if n >= 10:
        recent_high = max(h[n - 10:])
        recent_low = min(l[n - 10:])
        box_range_pct = (recent_high - recent_low) / recent_low * 100 if recent_low > 0 else 0
        if box_range_pct < 8:
            patterns_detected.append({
                "pattern": "箱体震荡", "type": "consolidation",
                "day_offset": 0, "strength": "中",
                "desc": f"近10日波幅 {box_range_pct:.1f}%，价格在区间内震荡"
            })

    # Deduplicate by pattern name, keep most recent
    seen = set()
    unique_patterns = []
    for p in reversed(patterns_detected):
        if p["pattern"] not in seen:
            seen.add(p["pattern"])
            unique_patterns.append(p)
    unique_patterns = list(reversed(unique_patterns))

    return {
        "code": stock_code,
        "source": source,
        "period_days": len(df),
        "current_price": round(float(c[-1]), 2),
        "patterns_count": len(unique_patterns),
        "patterns": unique_patterns,
        "summary": (
            "未发现明显形态" if not unique_patterns
            else "、".join(p["pattern"] for p in unique_patterns)
        ),
    }


analyze_pattern_tool = ToolDefinition(
    name="analyze_pattern",
    description="Detect candlestick and chart patterns in recent price history. "
                "Identifies: Doji, Hammer, Shooting Star, Morning/Evening Star, Engulfing, "
                "Double Bottom, upward breakout, box oscillation, and more. "
                "Returns pattern list with type (bullish/bearish/reversal) and strength.",
    parameters=[
        ToolParameter(
            name="stock_code",
            type="string",
            description="Stock code, e.g., '600519'",
        ),
        ToolParameter(
            name="days",
            type="integer",
            description="Number of recent trading days to scan (default: 60)",
            required=False,
            default=60,
        ),
    ],
    handler=_handle_analyze_pattern,
    category="analysis",
    policy=_ANALYSIS_READ_POLICY,
)


def _handle_analyze_ema200_setup(
    stock_code: str,
    setup_id: str = "ema_200_highlow",
    timeframe: str = "5m",
    days: int = 300,
) -> dict:
    """Run deterministic EMA200 setup checks on intraday or daily OHLCV history.

    Args:
        stock_code: Stock code
        setup_id: 'ema5_200_setup', 'ema_200_highlow', or 'spy_orb_ema200'
        timeframe: Chart timeframe ('5m', '15m', '30m', '1H', '4H', 'daily')
        days: Number of days history (for daily) or bars (for intraday)
    """
    from src.services.ema200_setup_service import analyze_ema200_setup

    if not (stock_code and str(stock_code).strip()):
        return {"error": "stock_code is required"}

    setup_id = (setup_id or "ema_200_highlow").strip()

    # Determine HTF timeframe based on chart timeframe
    htf_timeframe_map = {
        "5m": "4H",
        "15m": "4H",
        "30m": "4H",
        "1H": "daily",
        "4H": "daily",
        "daily": None,  # No HTF for daily (or could use weekly if available)
    }
    htf_timeframe = None if setup_id == "spy_orb_ema200" else htf_timeframe_map.get(timeframe, None)

    try:
        requested_bars = max(int(days or 300), 300)

        # Load chart timeframe data
        if timeframe == "daily":
            from src.services.history_loader import load_history_df
            df, source = load_history_df(stock_code, days=requested_bars)
        else:
            from src.services.intraday_history_loader import load_intraday_history
            df, source = load_intraday_history(
                stock_code,
                timeframe=timeframe,
                bars=requested_bars,
            )

        if df is None or df.empty:
            return {
                "error": f"No historical data available for EMA200 setup analysis on {stock_code}",
                "timeframe": timeframe,
            }

        # Load HTF data for bias filter (if applicable)
        htf_df = None
        if htf_timeframe:
            if htf_timeframe == "daily":
                from src.services.history_loader import load_history_df
                htf_df, _ = load_history_df(stock_code, days=260)
            else:
                from src.services.intraday_history_loader import load_intraday_history
                htf_df, _ = load_intraday_history(
                    stock_code,
                    timeframe=htf_timeframe,
                    bars=300,
                )

        result = analyze_ema200_setup(
            df,
            setup_id=setup_id,
            source=source,
            timeframe=timeframe,
            htf_df=htf_df,
        )
        result["code"] = stock_code
        result["requested_bars"] = requested_bars
        return result

    except Exception as e:
        return {
            "error": f"EMA200 setup analysis failed: {str(e)}",
            "code": stock_code,
            "timeframe": timeframe,
        }


analyze_ema200_setup_tool = ToolDefinition(
    name="analyze_ema200_setup",
    description=(
        "Deterministically evaluate EMA200 intraday/daily setup rules on OHLCV history. "
        "Supports setup_id='ema5_200_setup' for EMA200 touch/reclaim candidate checks and "
        "setup_id='ema_200_highlow' for candidate + higher-low/double-bottom + stop/1R checks. "
        "Also supports setup_id='spy_orb_ema200' for SPY-style 5m RTH ORB + EMA200 trend signals. "
        "Includes HTF (Higher TimeFrame) EMA200 bias filter: "
        "5m chart uses 4H EMA200, 1H chart uses daily EMA200 for trend confirmation. "
        "The legacy reclaim/high-low setup only returns long setups when HTF trend is bullish."
    ),
    parameters=[
        ToolParameter(
            name="stock_code",
            type="string",
            description="Stock code, e.g., '600519', 'AAPL', or 'HK00700'.",
        ),
        ToolParameter(
            name="setup_id",
            type="string",
            description="EMA200 setup id to evaluate.",
            required=False,
            enum=["ema5_200_setup", "ema_200_highlow", "spy_orb_ema200"],
            default="ema_200_highlow",
        ),
        ToolParameter(
            name="timeframe",
            type="string",
            description="Chart timeframe for entry signals.",
            required=False,
            enum=["5m", "15m", "30m", "1H", "4H", "daily"],
            default="5m",
        ),
        ToolParameter(
            name="days",
            type="integer",
            description="Number of bars to fetch (300+ recommended for EMA200 calculation).",
            required=False,
            default=300,
        ),
    ],
    handler=_handle_analyze_ema200_setup,
    category="analysis",
    policy=_ANALYSIS_READ_POLICY,
)


def _handle_analyze_vcp_h1_h2_buy(stock_code: str, days: int = 260) -> dict:
    """Run deterministic daily VCP + H1/H2 buy setup checks."""
    from src.services.history_loader import load_history_df
    from src.services.vcp_h1_h2_service import analyze_vcp_h1_h2_buy

    if not (stock_code and str(stock_code).strip()):
        return {"error": "stock_code is required"}

    try:
        requested_days = max(int(days or 260), 220)
    except (TypeError, ValueError):
        requested_days = 260

    try:
        df, source = load_history_df(stock_code, days=requested_days)
        if df is None or df.empty:
            return {
                "error": f"No historical data available for VCP H1/H2 analysis on {stock_code}",
                "timeframe": "daily",
            }
        result = analyze_vcp_h1_h2_buy(df, source=source, timeframe="daily")
        result["code"] = stock_code
        result["requested_days"] = requested_days
        return result
    except Exception as e:
        return {
            "error": f"VCP H1/H2 analysis failed: {str(e)}",
            "code": stock_code,
            "timeframe": "daily",
        }


analyze_vcp_h1_h2_buy_tool = ToolDefinition(
    name="analyze_vcp_h1_h2_buy",
    description=(
        "Deterministically evaluate the daily VCP_H1_H2_BUY rules: Minervini-style trend template, "
        "near-60-day-high filter, volatility contraction, dry volume, EMA21 hold, pivot breakout, H1/H2, "
        "and BUY de-duplication."
    ),
    parameters=[
        ToolParameter(
            name="stock_code",
            type="string",
            description="Stock code, e.g., '600519', 'AAPL', or 'HK00700'.",
        ),
        ToolParameter(
            name="days",
            type="integer",
            description="Number of daily bars to fetch (260 recommended; 220+ required).",
            required=False,
            default=260,
        ),
    ],
    handler=_handle_analyze_vcp_h1_h2_buy,
    category="analysis",
    policy=_ANALYSIS_READ_POLICY,
)


def _handle_analyze_vcp_breakout_trader(stock_code: str, days: int = 260) -> dict:
    """Run deterministic daily VCP / bull-flag breakout trader setup checks."""
    from src.services.history_loader import load_history_df
    from src.services.vcp_breakout_trader_service import analyze_vcp_breakout_trader

    if not (stock_code and str(stock_code).strip()):
        return {"error": "stock_code is required"}

    try:
        requested_days = max(int(days or 260), 220)
    except (TypeError, ValueError):
        requested_days = 260

    try:
        df, source = load_history_df(stock_code, days=requested_days)
        if df is None or df.empty:
            return {
                "error": f"No historical data available for VCP breakout trader analysis on {stock_code}",
                "timeframe": "daily",
            }
        result = analyze_vcp_breakout_trader(df, source=source, timeframe="daily")
        result["code"] = stock_code
        result["requested_days"] = requested_days
        return result
    except Exception as e:
        return {
            "error": f"VCP breakout trader analysis failed: {str(e)}",
            "code": stock_code,
            "timeframe": "daily",
        }


analyze_vcp_breakout_trader_tool = ToolDefinition(
    name="analyze_vcp_breakout_trader",
    description=(
        "Deterministically evaluate the daily VCP_BREAKOUT_TRADER rules: VCP setup, bull-flag setup, "
        "higher lows, dry volume, 10-day valid setup, EMA10/21 Low-Cheat before the 20-day pivot, "
        "20-day pivot breakout, 10% stop-risk filter, extension warning, and structural failure."
    ),
    parameters=[
        ToolParameter(
            name="stock_code",
            type="string",
            description="Stock code, e.g., '600519', 'AAPL', or 'HK00700'.",
        ),
        ToolParameter(
            name="days",
            type="integer",
            description="Number of daily bars to fetch (260 recommended; 220+ required).",
            required=False,
            default=260,
        ),
    ],
    handler=_handle_analyze_vcp_breakout_trader,
    category="analysis",
    policy=_ANALYSIS_READ_POLICY,
)


ALL_ANALYSIS_TOOLS = [
    analyze_trend_tool,
    calculate_ma_tool,
    get_volume_analysis_tool,
    analyze_pattern_tool,
    calculate_multi_strategy_score_tool,
    analyze_ema200_setup_tool,
    analyze_vcp_h1_h2_buy_tool,
    analyze_vcp_breakout_trader_tool,
]
