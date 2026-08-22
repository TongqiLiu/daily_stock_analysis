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

MULTI_STRATEGY_DIMENSIONS = {
    "bull_trend": "趋势",
    "ma_golden_cross": "趋势",
    "shrink_pullback": "量价",
    "volume_breakout": "量价",
    "bottom_volume": "量价",
    "chan_theory": "结构",
    "wave_theory": "结构",
    "box_oscillation": "结构",
    "one_yang_three_yin": "结构",
    "emotion_cycle": "情绪",
    "fear_greed_sentiment": "情绪",
    "dragon_head": "相对强弱",
}

_MULTI_STRATEGY_DIMENSION_ORDER = (
    "趋势",
    "量价",
    "结构",
    "情绪",
    "相对强弱",
)
_MULTI_STRATEGY_DIMENSION_WEIGHTS = {
    "趋势": Decimal("0.30"),
    "量价": Decimal("0.25"),
    "结构": Decimal("0.20"),
    "情绪": Decimal("0.10"),
    "相对强弱": Decimal("0.15"),
}
_MULTI_STRATEGY_EVIDENCE_QUALITY = {
    "complete": Decimal("1"),
    "partial": Decimal("0.5"),
    "missing": Decimal("0"),
}
_MULTI_STRATEGY_MIN_QUALITY_COVERAGE = Decimal("65.0")
_MULTI_STRATEGY_MIN_DIMENSION_COUNT = 4

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
    if (signal, strength) == ("buy", "strong"):
        return Decimal("80") <= score <= Decimal("95")
    if (signal, strength) == ("buy", "medium"):
        return Decimal("65") <= score < Decimal("80")
    if (signal, strength) == ("buy", "weak"):
        return Decimal("55") < score < Decimal("65")
    if (signal, strength) == ("hold", "medium"):
        return Decimal("45") <= score <= Decimal("55")
    if (signal, strength) == ("sell", "weak"):
        return Decimal("35") < score < Decimal("45")
    if (signal, strength) == ("sell", "medium"):
        return Decimal("20") < score <= Decimal("35")
    if (signal, strength) == ("sell", "strong"):
        return Decimal("5") <= score <= Decimal("20")
    return False


def _format_decimal(value: Decimal) -> str:
    """Render a Decimal without scientific notation or insignificant zeros."""
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def _multi_strategy_bias(score: Decimal) -> tuple[str, str]:
    """Return a direction label that is independent from the user action."""
    if score >= Decimal("75"):
        return "strong_bullish", "强势偏多"
    if score >= Decimal("60"):
        return "bullish", "偏多"
    if score >= Decimal("40"):
        return "neutral", "中性"
    if score > Decimal("25"):
        return "bearish", "偏空"
    return "strong_bearish", "强势偏空"


def _multi_strategy_actions(decision: str) -> dict[str, str]:
    """Map one aggregate decision to separate flat/holding actions."""
    if decision == "强烈买入":
        return {
            "no_position": "buy",
            "no_position_label": "满足入场条件后分批试仓",
            "has_position": "add",
            "has_position_label": "持有，确认后再加仓",
        }
    if decision == "偏多":
        return {
            "no_position": "watch",
            "no_position_label": "等待量价确认后试仓",
            "has_position": "hold",
            "has_position_label": "继续持有，暂不追高",
        }
    if decision == "观望":
        return {
            "no_position": "watch",
            "no_position_label": "继续观察",
            "has_position": "hold",
            "has_position_label": "持有并按失效位管理",
        }
    if decision == "偏空":
        return {
            "no_position": "avoid",
            "no_position_label": "暂不介入",
            "has_position": "reduce",
            "has_position_label": "反弹减仓并收紧止损",
        }
    if decision == "卖出":
        return {
            "no_position": "avoid",
            "no_position_label": "回避",
            "has_position": "sell",
            "has_position_label": "按失效位退出",
        }
    return {
        "no_position": "watch",
        "no_position_label": "证据不足，等待补齐",
        "has_position": "hold",
        "has_position_label": "不加仓，优先控制风险",
    }


def _multi_strategy_dimension_label(score: Decimal) -> str:
    if score >= Decimal("60"):
        return "偏多"
    if score <= Decimal("40"):
        return "偏空"
    return "中性"


def _summarize_multi_strategy_dimensions(rows: list[dict]) -> list[dict]:
    """Aggregate correlated strategy rows once per user-facing dimension."""
    summaries: list[dict] = []
    for dimension in _MULTI_STRATEGY_DIMENSION_ORDER:
        dimension_rows = [row for row in rows if row.get("dimension") == dimension]
        included_rows = [row for row in dimension_rows if row.get("included") is True]
        configured_weight = sum(
            (Decimal(str(row.get("weight") or 0)) for row in dimension_rows),
            Decimal("0"),
        )
        included_weight = sum(
            (Decimal(str(row.get("weight") or 0)) for row in included_rows),
            Decimal("0"),
        )
        quality_weight = sum(
            (
                Decimal(str(row.get("weight") or 0))
                * Decimal(str(row.get("evidence_quality") or 0))
                for row in dimension_rows
            ),
            Decimal("0"),
        )
        counts = {
            "complete": sum(row.get("evidence_status_code") == "complete" for row in dimension_rows),
            "partial": sum(row.get("evidence_status_code") == "partial" for row in dimension_rows),
            "missing": sum(row.get("evidence_status_code") == "missing" for row in dimension_rows),
        }
        summary: dict[str, Any] = {
            "dimension": dimension,
            "available": bool(included_rows),
            "configured_weight": float(configured_weight),
            "included_weight": float(included_weight),
            "quality_coverage_pct": float(
                (quality_weight / configured_weight * Decimal("100")).quantize(
                    Decimal("0.1"), rounding=ROUND_HALF_UP
                )
            ) if configured_weight > 0 else 0.0,
            "evidence": counts,
            "conflict": len({row.get("signal_code") for row in included_rows}) > 1,
            "supporting_factor": None,
            "risk_factor": None,
        }
        if included_rows and included_weight > 0:
            numerator = sum(
                (
                    Decimal(str(row.get("score")))
                    * Decimal(str(row.get("weight") or 0))
                    for row in included_rows
                ),
                Decimal("0"),
            )
            score = (numerator / included_weight).quantize(
                Decimal("0.1"), rounding=ROUND_HALF_UP
            )
            supporting = max(included_rows, key=lambda row: float(row.get("score") or 0))
            opposing = min(included_rows, key=lambda row: float(row.get("score") or 0))
            summary.update({
                "score": float(score),
                "direction": _multi_strategy_dimension_label(score),
                "supporting_factor": (
                    supporting.get("reason") if float(supporting.get("score") or 0) > 50 else None
                ),
                "risk_factor": (
                    opposing.get("reason") if float(opposing.get("score") or 0) < 50 else None
                ),
            })
        else:
            summary.update({"score": None, "direction": "证据缺失"})
        summaries.append(summary)
    return summaries


def _multi_strategy_conflict_level(dimensions: list[dict], rows: list[dict]) -> str:
    available = [item for item in dimensions if item.get("available")]
    directions = {item.get("direction") for item in available}
    if "偏多" in directions and "偏空" in directions:
        return "high"
    if any(item.get("conflict") for item in available):
        return "medium"
    row_signals = {row.get("signal_code") for row in rows if row.get("included") is True}
    if "buy" in row_signals and "sell" in row_signals:
        return "low"
    return "none"


def _calculate_multi_strategy_dimension_score(
    dimensions: list[dict],
) -> tuple[Decimal, Decimal, Decimal]:
    """Calculate one primary direction score after de-correlating row dimensions."""
    numerator = Decimal("0")
    included_weight = Decimal("0")
    for item in dimensions:
        if not item.get("available") or item.get("score") is None:
            continue
        dimension = str(item.get("dimension") or "")
        weight = _MULTI_STRATEGY_DIMENSION_WEIGHTS.get(dimension)
        if weight is None:
            continue
        numerator += Decimal(str(item["score"])) * weight
        included_weight += weight
    if included_weight <= 0:
        return Decimal("0"), Decimal("0"), Decimal("50.0")
    score = (numerator / included_weight).quantize(
        Decimal("0.1"), rounding=ROUND_HALF_UP
    )
    return numerator, included_weight, score


def _normalize_multi_strategy_market_structure(raw_structure: Any) -> dict:
    """Normalize support/resistance facts supplied from the trend tool."""
    if raw_structure in (None, ""):
        return {
            "status": "unavailable",
            "reason": "本轮评分未提交 analyze_trend 的结构化支撑/阻力数据",
        }
    try:
        structure = (
            json.loads(raw_structure)
            if isinstance(raw_structure, str)
            else raw_structure
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        return {
            "status": "invalid",
            "reason": f"market_structure_json 不是有效 JSON：{exc}",
        }
    if not isinstance(structure, dict):
        return {
            "status": "invalid",
            "reason": "market_structure_json 必须是 JSON 对象",
        }

    def positive_number(value: Any) -> Optional[float]:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) and number > 0 else None

    def levels(value: Any) -> list[float]:
        if not isinstance(value, list):
            return []
        normalized = {
            number
            for item in value
            for number in [positive_number(item)]
            if number is not None
        }
        return sorted(normalized)

    current_price = positive_number(structure.get("current_price"))
    supports = levels(structure.get("support_levels"))
    resistances = levels(structure.get("resistance_levels"))
    if current_price is None and not supports and not resistances:
        return {
            "status": "unavailable",
            "reason": "analyze_trend 未返回可用的当前价格、支撑位或阻力位",
        }

    below_support = [level for level in supports if level <= current_price] if current_price else []
    above_resistance = [level for level in resistances if level >= current_price] if current_price else []
    nearest_support = max(below_support) if below_support else (max(supports) if supports else None)
    nearest_resistance = min(above_resistance) if above_resistance else (
        min(resistances) if resistances else None
    )

    price_location = "unknown"
    if current_price and resistances and current_price > max(resistances) * 1.01:
        price_location = "breakout"
    elif current_price and supports and current_price < min(supports) * 0.99:
        price_location = "breakdown"
    elif current_price and nearest_support and (
        abs(current_price - nearest_support) / nearest_support <= 0.03
    ):
        price_location = "near_support"
    elif current_price and nearest_resistance and (
        abs(nearest_resistance - current_price) / nearest_resistance <= 0.03
    ):
        price_location = "near_resistance"
    elif nearest_support or nearest_resistance:
        price_location = "between_levels"

    def distance_pct(level: Optional[float]) -> Optional[float]:
        if level is None or current_price is None:
            return None
        return round((level - current_price) / current_price * 100, 2)

    return {
        "status": "available" if (supports or resistances) else "partial",
        "source": "analyze_trend",
        "current_price": current_price,
        "support_levels": supports,
        "resistance_levels": resistances,
        "nearest_support": nearest_support,
        "nearest_resistance": nearest_resistance,
        "nearest_support_distance_pct": distance_pct(nearest_support),
        "nearest_resistance_distance_pct": distance_pct(nearest_resistance),
        "price_location": price_location,
        "breakout_status": structure.get("breakout_status") or price_location,
        "invalidation_level": positive_number(structure.get("invalidation_level")),
        "evidence": structure.get("evidence") or "来自本轮 analyze_trend 支撑/阻力字段",
    }


def _multi_strategy_decision(score: Decimal, *, leveraged: bool) -> tuple[str, str]:
    """Map a deterministic score to a decision and risk-aware position guide."""
    if score >= Decimal("75"):
        return (
            "强烈买入",
            "杠杆ETF仅小仓分批试仓并按止损距离定仓"
            if leveraged
            else "按止损距离和单笔风险预算定仓；缺少账户参数时仅分批试仓",
        )
    if score >= Decimal("60"):
        return (
            "偏多",
            "杠杆ETF等待确认后小仓试仓"
            if leveraged
            else "等待确认后小仓试仓，并按止损距离计算仓位",
        )
    if score >= Decimal("40"):
        return (
            "观望",
            "不新增仓位；已有仓位按失效位管理",
        )
    if score > Decimal("25"):
        return (
            "偏空",
            "不新增仓位；已有仓位考虑减仓并收紧止损",
        )
    return "卖出", "空仓回避；已有仓位按失效位退出"


def _handle_calculate_multi_strategy_score(
    scores_json: str,
    instrument_type: str = "standard",
    market_structure_json: Optional[str] = None,
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
    quality_adjusted_weight = Decimal("0")

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
                "dimension": MULTI_STRATEGY_DIMENSIONS[strategy_id],
                "signal": "不可评估",
                "signal_code": "unavailable",
                "strength": "-",
                "strength_code": "none",
                "score": None,
                "weight": float(weight),
                "included": False,
                "evidence_status": "缺失",
                "evidence_status_code": "missing",
                "evidence_quality": 0.0,
                "reason": reason,
            })
            continue

        if (
            strategy_id == "fear_greed_sentiment"
            and any(
                marker in reason.lower()
                for marker in ("proxy_score", "代理", "not_configured", "unavailable", "不可用")
            )
        ):
            issues.append(
                "fear_greed_sentiment: unavailable/proxy evidence must be marked missing "
                "and excluded from the denominator"
            )
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
        evidence_quality = _MULTI_STRATEGY_EVIDENCE_QUALITY[evidence_status]
        quality_adjusted_weight += weight * evidence_quality
        normalized_rows.append({
            "strategy": strategy_id,
            "display_name": display_name,
            "dimension": MULTI_STRATEGY_DIMENSIONS[strategy_id],
            "signal": _SIGNAL_LABELS[signal],
            "signal_code": signal,
            "strength": _STRENGTH_LABELS[strength],
            "strength_code": strength,
            "score": float(score),
            "weight": float(weight),
            "included": True,
            "weighted_value": float(weighted_value),
            "evidence_status": _EVIDENCE_LABELS[evidence_status],
            "evidence_status_code": evidence_status,
            "evidence_quality": float(evidence_quality),
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

    market_structure = _normalize_multi_strategy_market_structure(market_structure_json)
    if market_structure.get("status") == "invalid":
        return {
            "status": "invalid",
            "error": "market structure validation failed",
            "issues": [market_structure.get("reason") or "invalid market structure"],
        }
    weighted_score = (weighted_numerator / included_weight).quantize(
        Decimal("0.1"),
        rounding=ROUND_HALF_UP,
    )
    evidence_coverage = (
        included_weight / _MULTI_STRATEGY_TOTAL_WEIGHT * Decimal("100")
    ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    quality_coverage = (
        quality_adjusted_weight / _MULTI_STRATEGY_TOTAL_WEIGHT * Decimal("100")
    ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    dimension_summaries = _summarize_multi_strategy_dimensions(normalized_rows)
    available_dimension_count = sum(
        bool(item.get("available")) for item in dimension_summaries
    )
    conflict_level = _multi_strategy_conflict_level(
        dimension_summaries,
        normalized_rows,
    )
    (
        dimension_weighted_numerator,
        included_dimension_weight,
        dimension_weighted_score,
    ) = _calculate_multi_strategy_dimension_score(dimension_summaries)
    leveraged = normalized_instrument_type == "leveraged_etf"
    decision, position_guidance = _multi_strategy_decision(
        dimension_weighted_score,
        leveraged=leveraged,
    )
    decision_blockers: list[dict[str, str]] = []
    if evidence_coverage < Decimal("70.0") or complete_count + partial_count < 8:
        decision_blockers.append({
            "code": "low_evaluation_coverage",
            "message": "可评估策略或有效权重覆盖不足",
        })
    if quality_coverage < _MULTI_STRATEGY_MIN_QUALITY_COVERAGE:
        decision_blockers.append({
            "code": "low_evidence_quality",
            "message": (
                "完整证据不足；部分证据按 0.5 质量系数折算后低于 "
                f"{_format_decimal(_MULTI_STRATEGY_MIN_QUALITY_COVERAGE)}%"
            ),
        })
    if available_dimension_count < _MULTI_STRATEGY_MIN_DIMENSION_COUNT:
        decision_blockers.append({
            "code": "low_dimension_coverage",
            "message": (
                f"五维证据仅覆盖 {available_dimension_count} 维，"
                f"至少需要 {_MULTI_STRATEGY_MIN_DIMENSION_COUNT} 维"
            ),
        })
    if decision_blockers:
        decision = "证据不足（观望）"
        position_guidance = "不新增仓位，补齐证据后重评"

    if decision_blockers:
        confidence_level = "insufficient"
        confidence_label = "证据不足"
    elif quality_coverage >= Decimal("85.0") and conflict_level in {"none", "low"}:
        confidence_level = "high"
        confidence_label = "高"
    elif quality_coverage >= _MULTI_STRATEGY_MIN_QUALITY_COVERAGE and conflict_level != "high":
        confidence_level = "medium"
        confidence_label = "中"
    else:
        confidence_level = "low"
        confidence_label = "低"

    supporting_factors = sorted(
        (
            {
                "strategy": row["strategy"],
                "display_name": row["display_name"],
                "dimension": row["dimension"],
                "score": row["score"],
                "reason": row["reason"],
            }
            for row in normalized_rows
            if row.get("included") is True and float(row.get("score") or 0) > 55
        ),
        key=lambda item: float(item["score"]),
        reverse=True,
    )[:3]
    contradicting_factors = sorted(
        (
            {
                "strategy": row["strategy"],
                "display_name": row["display_name"],
                "dimension": row["dimension"],
                "score": row["score"],
                "reason": row["reason"],
            }
            for row in normalized_rows
            if row.get("included") is True and float(row.get("score") or 100) < 45
        ),
        key=lambda item: float(item["score"]),
    )[:3]
    bias_code, bias_label = _multi_strategy_bias(dimension_weighted_score)
    actions = _multi_strategy_actions(decision)

    return {
        "status": "ok",
        "schema_version": "2.0",
        "instrument_type": normalized_instrument_type,
        "market_structure": market_structure,
        "rows": normalized_rows,
        "dimensions": dimension_summaries,
        "weighted_numerator": float(weighted_numerator),
        "included_weight": float(included_weight),
        "configured_total_weight": float(_MULTI_STRATEGY_TOTAL_WEIGHT),
        "weighted_score": float(weighted_score),
        "dimension_weighted_numerator": float(dimension_weighted_numerator),
        "included_dimension_weight": float(included_dimension_weight),
        "configured_dimension_weight": float(sum(_MULTI_STRATEGY_DIMENSION_WEIGHTS.values())),
        "dimension_weighted_score": float(dimension_weighted_score),
        "bias": {
            "code": bias_code,
            "label": bias_label,
            "score": float(dimension_weighted_score),
        },
        "decision": decision,
        "actions": actions,
        "position_guidance": position_guidance,
        "confidence": {
            "level": confidence_level,
            "label": confidence_label,
            "conflict_level": conflict_level,
        },
        "supporting_factors": supporting_factors,
        "contradicting_factors": contradicting_factors,
        "decision_blockers": decision_blockers,
        "evidence": {
            "complete_count": complete_count,
            "partial_count": partial_count,
            "missing_count": missing_count,
            "evaluated_count": complete_count + partial_count,
            "coverage_pct": float(evidence_coverage),
            "quality_coverage_pct": float(quality_coverage),
            "available_dimension_count": available_dimension_count,
            "dimension_count": len(_MULTI_STRATEGY_DIMENSION_ORDER),
        },
        "formula": (
            f"{_format_decimal(weighted_numerator)} / {_format_decimal(included_weight)} "
            f"= {_format_decimal(weighted_score)}"
        ),
        "dimension_formula": (
            f"{_format_decimal(dimension_weighted_numerator)} / "
            f"{_format_decimal(included_dimension_weight)} = "
            f"{_format_decimal(dimension_weighted_score)}"
        ),
    }


calculate_multi_strategy_score_tool = ToolDefinition(
    name="calculate_multi_strategy_score",
    description=(
        "Validate all 12 multi-strategy rows and calculate the weighted score, evidence coverage, "
        "quality-adjusted decision gate, five-dimension summaries, separate flat/holding actions, "
        "and risk-aware position guidance deterministically. Pass scores_json as a JSON array in "
        "canonical strategy order; each row needs strategy, signal, strength, score, "
        "evidence_status, and reason. Missing evidence must use signal='不可评估', strength='-', "
        "and score=null. Pass market_structure_json from analyze_trend when available; it adds "
        "support/resistance levels to the authoritative report. Use instrument_type='leveraged_etf' "
        "for leveraged ETFs."
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
        ToolParameter(
            name="market_structure_json",
            type="string",
            description=(
                "Optional JSON object copied from analyze_trend, containing current_price, "
                "support_levels, resistance_levels, and optional breakout_status/invalidation_level. "
                "Do not invent levels when analyze_trend did not return them."
            ),
            required=False,
            default=None,
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


def _handle_analyze_price_action(stock_code: str, days: int = 120) -> dict:
    """Extract a conservative, Brooks-inspired price-action context from daily bars.

    This is a deterministic evidence helper, not a claim that a discretionary
    price-action setup has been confirmed.  In particular, second entries are
    intentionally left for the model/user to confirm bar by bar.
    """
    from src.services.history_loader import load_history_df

    if not (stock_code and str(stock_code).strip()):
        return {"status": "error", "code": "missing_stock_code"}

    try:
        requested_days = min(max(int(days or 120), 60), 365)
    except (TypeError, ValueError):
        requested_days = 120

    try:
        df, source = load_history_df(stock_code, days=requested_days)
    except Exception as exc:
        logger.warning("Price action history load failed for %s: %s", stock_code, exc)
        return {
            "status": "unavailable",
            "code": "history_load_failed",
            "reason": str(exc),
        }

    if df is None or df.empty:
        return {
            "status": "unavailable",
            "code": "history_unavailable",
            "reason": f"No daily OHLCV history available for {stock_code}",
        }

    required_columns = {"open", "high", "low", "close"}
    missing_columns = sorted(required_columns - set(df.columns))
    if missing_columns:
        return {
            "status": "unavailable",
            "code": "missing_ohlc_columns",
            "missing_columns": missing_columns,
        }

    df = df.tail(requested_days).copy().reset_index(drop=True)
    if len(df) < 30:
        return {
            "status": "partial",
            "code": "insufficient_history",
            "bars": len(df),
            "reason": "价格行为学至少需要约 30 根日线，当前样本不足。",
        }

    try:
        opens = [float(value) for value in df["open"]]
        highs = [float(value) for value in df["high"]]
        lows = [float(value) for value in df["low"]]
        closes = [float(value) for value in df["close"]]
    except (TypeError, ValueError):
        return {"status": "unavailable", "code": "non_numeric_ohlc"}

    current = closes[-1]
    ma20 = sum(closes[-20:]) / 20
    prior_high = max(highs[-21:-1])
    prior_low = min(lows[-21:-1])
    recent_high = max(highs[-20:])
    recent_low = min(lows[-20:])
    range_pct = (recent_high - recent_low) / current * 100 if current else 0.0
    slope_pct = (closes[-1] - closes[-21]) / closes[-21] * 100 if closes[-21] else 0.0

    # Use five-bar pivots as a compact, reproducible proxy for swing structure.
    pivot_highs = [highs[i] for i in range(2, len(highs) - 2)
                   if highs[i] >= max(highs[i - 2:i + 3])]
    pivot_lows = [lows[i] for i in range(2, len(lows) - 2)
                  if lows[i] <= min(lows[i - 2:i + 3])]
    recent_pivot_highs = pivot_highs[-4:]
    recent_pivot_lows = pivot_lows[-4:]
    higher_highs = len(recent_pivot_highs) >= 2 and recent_pivot_highs[-1] > recent_pivot_highs[-2]
    higher_lows = len(recent_pivot_lows) >= 2 and recent_pivot_lows[-1] > recent_pivot_lows[-2]
    lower_highs = len(recent_pivot_highs) >= 2 and recent_pivot_highs[-1] < recent_pivot_highs[-2]
    lower_lows = len(recent_pivot_lows) >= 2 and recent_pivot_lows[-1] < recent_pivot_lows[-2]

    if higher_highs and higher_lows and current >= ma20:
        context_state = "bull_trend"
        context_label = "多头趋势候选"
        context_reason = "近端摆动高点与低点同步抬高，且收盘位于 MA20 上方。"
    elif lower_highs and lower_lows and current <= ma20:
        context_state = "bear_trend"
        context_label = "空头趋势候选"
        context_reason = "近端摆动高点与低点同步降低，且收盘位于 MA20 下方。"
    elif range_pct < 20 and abs(slope_pct) < 8:
        context_state = "trading_range"
        context_label = "交易区间候选"
        context_reason = "近 20 日波动范围和方向斜率都有限，突破更需要后续跟随或失败确认。"
    else:
        context_state = "transition"
        context_label = "趋势/区间转换候选"
        context_reason = "摆动结构与方向条件未形成同向组合，暂不把单根 K 线当作趋势确认。"

    last_range = max(highs[-1] - lows[-1], 0.0)
    body_ratio = abs(closes[-1] - opens[-1]) / last_range if last_range else 0.0
    close_position = (closes[-1] - lows[-1]) / last_range if last_range else 0.5
    if body_ratio >= 0.6 and close_position >= 0.75:
        signal_bar = "strong_bull_close"
        signal_bar_label = "强势收盘阳线候选"
    elif body_ratio >= 0.6 and close_position <= 0.25:
        signal_bar = "strong_bear_close"
        signal_bar_label = "强势收盘阴线候选"
    elif body_ratio < 0.35:
        signal_bar = "small_body"
        signal_bar_label = "小实体/犹豫 K 线"
    else:
        signal_bar = "ordinary"
        signal_bar_label = "普通信号 K 线"

    if current > prior_high:
        signal_type = "bull_breakout_candidate"
        signal_label = "上破近 20 日高点候选"
        signal_reason = "收盘高于前 20 个交易日高点；仍需量能与后续跟随确认。"
    elif highs[-1] > prior_high and current <= prior_high:
        signal_type = "failed_bull_breakout_candidate"
        signal_label = "多头突破失败候选"
        signal_reason = "盘中越过前 20 个交易日高点但收盘收回其下，需观察下一根 K 线。"
    elif current < prior_low:
        signal_type = "bear_breakdown_candidate"
        signal_label = "下破近 20 日低点候选"
        signal_reason = "收盘低于前 20 个交易日低点；仍需后续跟随确认。"
    elif lows[-1] < prior_low and current >= prior_low:
        signal_type = "failed_bear_breakdown_candidate"
        signal_label = "空头破位失败候选"
        signal_reason = "盘中跌破前 20 个交易日低点但收盘收回其上，需观察下一根 K 线。"
    else:
        signal_type = "inside_context"
        signal_label = "区间内/未形成突破候选"
        signal_reason = "当前收盘仍在前 20 个交易日高低点之间。"

    return {
        "status": "ok",
        "code": stock_code,
        "source": source,
        "timeframe": "daily",
        "bars": len(df),
        "context": {
            "state": context_state,
            "label": context_label,
            "reason": context_reason,
            "slope_pct_20d": round(slope_pct, 2),
            "range_pct_20d": round(range_pct, 2),
        },
        "signal": {
            "type": signal_type,
            "label": signal_label,
            "reason": signal_reason,
            "signal_bar": signal_bar,
            "signal_bar_label": signal_bar_label,
            "body_ratio": round(body_ratio, 3),
        },
        "structure": {
            "higher_highs": higher_highs,
            "higher_lows": higher_lows,
            "lower_highs": lower_highs,
            "lower_lows": lower_lows,
        },
        "levels": {
            "current_price": round(current, 4),
            "ma20": round(ma20, 4),
            "prior_20d_high": round(prior_high, 4),
            "prior_20d_low": round(prior_low, 4),
            "recent_range_high": round(recent_high, 4),
            "recent_range_low": round(recent_low, 4),
        },
        "second_entry": {
            "status": "not_determined",
            "label": "二次入场需逐根 K 线确认",
            "reason": "本工具不凭单次形态确认 Brooks 二次入场；需要后续信号 K、入场 K 与失效位。",
        },
        "limitations": [
            "这是 Brooks-inspired 的确定性候选识别，不是官方课程认证或收益概率。",
            "突破/失败突破需要后续跟随或失败确认，不能仅凭一根 K 线追单。",
            "未使用盘中逐笔、订单流或主观图表标注；样本不足时应降低证据等级。",
        ],
    }


analyze_price_action_tool = ToolDefinition(
    name="analyze_price_action",
    description=(
        "Analyze daily price action using a conservative Brooks-inspired lens: "
        "trend versus trading range, swing structure, breakout/failure candidates, "
        "signal-bar context, and explicit second-entry limitations."
    ),
    parameters=[
        ToolParameter(
            name="stock_code",
            type="string",
            description="Stock code, e.g., 'AAPL' or '600519'.",
        ),
        ToolParameter(
            name="days",
            type="integer",
            description="Number of recent daily bars (60-365, default 120).",
            required=False,
            default=120,
        ),
    ],
    handler=_handle_analyze_price_action,
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


def _handle_analyze_intraday_t(
    stock_code: str,
    timeframe: str = "3m",
    bars: int = 260,
) -> dict:
    """Run deterministic intraday T-trading structure checks."""
    from src.services.intraday_history_loader import load_intraday_history
    from src.services.intraday_t_service import analyze_intraday_t

    if not (stock_code and str(stock_code).strip()):
        return {"error": "stock_code is required"}
    if timeframe != "3m":
        return {
            "error": "Intraday T-trading analysis currently supports 3m only",
            "code": stock_code,
            "timeframe": timeframe,
        }
    try:
        requested_bars = max(int(bars or 260), 80)
    except (TypeError, ValueError):
        requested_bars = 260

    try:
        df, source = load_intraday_history(
            stock_code,
            timeframe="3m",
            bars=requested_bars,
        )
        if df is None or df.empty:
            return {
                "error": f"No 3-minute data available for intraday T analysis on {stock_code}",
                "code": stock_code,
                "timeframe": "3m",
            }
        result = analyze_intraday_t(df, source=source, timeframe="3m")
        result["code"] = stock_code
        result["requested_bars"] = requested_bars
        return result
    except Exception as exc:
        return {
            "error": f"Intraday T analysis failed: {str(exc)}",
            "code": stock_code,
            "timeframe": "3m",
        }


analyze_intraday_t_tool = ToolDefinition(
    name="analyze_intraday_t",
    description=(
        "Deterministically analyze a 3-minute chart for intraday T-trading. "
        "Calculates EMA20, EMA50, Wilder ATR14, confirmed HH/HL/LH/LL pivots, "
        "trend-versus-range regime, failed second-push high-sell confirmation, "
        "support plus higher-low plus EMA20-reclaim buyback confirmation, "
        "1-1.5 ATR spacing, and T-position guardrails."
    ),
    parameters=[
        ToolParameter(
            name="stock_code",
            type="string",
            description="Stock code, e.g., 'MSTX', 'OKLL', 'CONL', or 'KORU'.",
        ),
        ToolParameter(
            name="timeframe",
            type="string",
            description="Chart timeframe. This rule set is fixed to 3-minute bars.",
            required=False,
            enum=["3m"],
            default="3m",
        ),
        ToolParameter(
            name="bars",
            type="integer",
            description="Number of recent 3-minute bars to fetch (260 recommended; 80+ required).",
            required=False,
            default=260,
        ),
    ],
    handler=_handle_analyze_intraday_t,
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
    analyze_price_action_tool,
    calculate_multi_strategy_score_tool,
    analyze_ema200_setup_tool,
    analyze_intraday_t_tool,
    analyze_vcp_h1_h2_buy_tool,
    analyze_vcp_breakout_trader_tool,
]
