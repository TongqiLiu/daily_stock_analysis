# -*- coding: utf-8 -*-
"""Deterministic 3-minute intraday T-trading structure analysis.

The service does not predict an absolute top or bottom.  It converts the
project's intraday OHLC data into the confirmation sequence used by the
``intraday_t_trading`` skill: trend/range regime, failed second push, higher
low, EMA20 reclaim, ATR spacing, and position guardrails.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import pandas as pd


@dataclass(frozen=True)
class IntradayTParams:
    """Thresholds for the 3-minute structure checks."""

    min_bars: int = 80
    ema_fast: int = 20
    ema_slow: int = 50
    atr_period: int = 14
    slope_lookback: int = 5
    pivot_left: int = 2
    pivot_right: int = 2
    pivot_scan_bars: int = 160
    structure_tolerance_atr: float = 0.15
    support_touch_atr: float = 0.60
    flat_slope_atr: float = 0.25
    steep_slope_atr: float = 0.35


def _round_price(value: Any) -> Optional[float]:
    if value is None or pd.isna(value):
        return None
    return round(float(value), 4)


def _round_metric(value: Any) -> Optional[float]:
    if value is None or pd.isna(value):
        return None
    return round(float(value), 3)


def _coerce_frame(df: pd.DataFrame) -> pd.DataFrame:
    frame = df.copy()
    required = ["open", "high", "low", "close"]
    for column in required + ["volume"]:
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=required).reset_index(drop=True)

    if "date" in frame.columns:
        frame["_timestamp"] = pd.to_datetime(frame["date"], errors="coerce")
        if frame["_timestamp"].notna().any():
            frame = frame.sort_values("_timestamp", kind="stable").reset_index(drop=True)
    return frame


def _drop_unclosed_latest_bar(
    frame: pd.DataFrame,
    *,
    timeframe: str,
    analysis_time: Optional[pd.Timestamp],
) -> tuple[pd.DataFrame, bool]:
    """Exclude a still-forming bar when its local timestamp is available."""
    if timeframe != "3m" or "_timestamp" not in frame.columns or frame.empty:
        return frame, False
    latest_timestamp = frame.iloc[-1]["_timestamp"]
    if pd.isna(latest_timestamp):
        return frame, False

    latest_timestamp = pd.Timestamp(latest_timestamp)
    current_time = analysis_time or pd.Timestamp.now(tz=latest_timestamp.tz)
    current_time = pd.Timestamp(current_time)
    if latest_timestamp.tz is None and current_time.tz is not None:
        current_time = current_time.tz_localize(None)
    elif latest_timestamp.tz is not None and current_time.tz is None:
        current_time = current_time.tz_localize(latest_timestamp.tz)
    elif latest_timestamp.tz != current_time.tz:
        current_time = current_time.tz_convert(latest_timestamp.tz)

    if current_time < latest_timestamp + pd.Timedelta(minutes=3):
        return frame.iloc[:-1].reset_index(drop=True), True
    return frame, False


def _date_at(frame: pd.DataFrame, idx: int) -> str:
    if "date" not in frame.columns:
        return str(idx)
    return str(frame.iloc[idx].get("date"))


def _add_indicators(frame: pd.DataFrame, params: IntradayTParams) -> pd.DataFrame:
    enriched = frame.copy()
    enriched["ema20"] = enriched["close"].ewm(
        span=params.ema_fast,
        adjust=False,
    ).mean()
    enriched["ema50"] = enriched["close"].ewm(
        span=params.ema_slow,
        adjust=False,
    ).mean()

    previous_close = enriched["close"].shift(1)
    true_range = pd.concat(
        [
            enriched["high"] - enriched["low"],
            (enriched["high"] - previous_close).abs(),
            (enriched["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    enriched["atr14"] = true_range.ewm(
        alpha=1 / params.atr_period,
        adjust=False,
        min_periods=params.atr_period,
    ).mean()
    return enriched


def _collect_pivots(
    frame: pd.DataFrame,
    *,
    column: str,
    kind: str,
    params: IntradayTParams,
) -> list[dict[str, Any]]:
    start = max(params.pivot_left, len(frame) - params.pivot_scan_bars)
    end = len(frame) - params.pivot_right
    pivots: list[dict[str, Any]] = []

    for idx in range(start, end):
        value = float(frame.iloc[idx][column])
        left = frame[column].iloc[idx - params.pivot_left : idx]
        right = frame[column].iloc[idx + 1 : idx + params.pivot_right + 1]
        if kind == "high":
            confirmed = value > float(left.max()) and value >= float(right.max())
        else:
            confirmed = value < float(left.min()) and value <= float(right.min())
        if not confirmed:
            continue
        pivots.append({
            "index": idx,
            "time": _date_at(frame, idx),
            "price": _round_price(value),
        })
    return pivots


def _classify_highs(pivots: list[dict[str, Any]], tolerance: float) -> str:
    if len(pivots) < 2:
        return "insufficient"
    previous = float(pivots[-2]["price"])
    latest = float(pivots[-1]["price"])
    if latest > previous + tolerance:
        return "HH"
    if latest < previous - tolerance:
        return "LH"
    return "EQH"


def _classify_lows(pivots: list[dict[str, Any]], tolerance: float) -> str:
    if len(pivots) < 2:
        return "insufficient"
    previous = float(pivots[-2]["price"])
    latest = float(pivots[-1]["price"])
    if latest > previous + tolerance:
        return "HL"
    if latest < previous - tolerance:
        return "LL"
    return "EQL"


def _crossed_above_ema20(frame: pd.DataFrame, start_idx: int) -> bool:
    start = max(1, start_idx)
    for idx in range(start, len(frame)):
        previous = frame.iloc[idx - 1]
        current = frame.iloc[idx]
        if (
            float(previous["close"]) <= float(previous["ema20"])
            and float(current["close"]) > float(current["ema20"])
        ):
            return True
    return False


def _nearest_support(
    frame: pd.DataFrame,
    pivot_lows: list[dict[str, Any]],
    latest_low_idx: int,
    latest_low: float,
) -> dict[str, Any]:
    row = frame.iloc[latest_low_idx]
    candidates = [
        ("EMA20", float(row["ema20"])),
        ("EMA50", float(row["ema50"])),
    ]
    if len(pivot_lows) >= 2:
        candidates.append(("前低", float(pivot_lows[-2]["price"])))
    bar_low = float(row["low"])
    bar_high = float(row["high"])

    def distance_to_bar(price: float) -> float:
        if bar_low <= price <= bar_high:
            return 0.0
        return min(abs(price - bar_low), abs(price - bar_high))

    label, price = min(candidates, key=lambda item: distance_to_bar(item[1]))
    return {
        "label": label,
        "price": _round_price(price),
        "distance_to_bar": _round_price(distance_to_bar(price)),
        "distance_to_pivot_low": _round_price(abs(latest_low - price)),
    }


def _positive_spacing(
    sell_reference: Optional[float],
    buyback_reference: Optional[float],
    atr: float,
) -> tuple[Optional[float], Optional[float]]:
    if sell_reference is None or buyback_reference is None or sell_reference <= buyback_reference:
        return None, None
    spacing = sell_reference - buyback_reference
    return _round_price(spacing), _round_metric(spacing / atr)


def analyze_intraday_t(
    df: pd.DataFrame,
    *,
    source: str = "unknown",
    timeframe: str = "3m",
    params: Optional[IntradayTParams] = None,
    analysis_time: Optional[pd.Timestamp] = None,
) -> dict[str, Any]:
    """Analyze an intraday chart using confirmation-based T-trading rules."""
    params = params or IntradayTParams()
    frame = _coerce_frame(df)
    received_bars = len(frame)
    frame, dropped_unclosed_bar = _drop_unclosed_latest_bar(
        frame,
        timeframe=timeframe,
        analysis_time=analysis_time,
    )
    base = {
        "status": "ok",
        "timeframe": timeframe,
        "source": source,
        "bars": len(frame),
        "received_bars": received_bars,
        "dropped_unclosed_bar": dropped_unclosed_bar,
    }

    if timeframe != "3m":
        return {
            **base,
            "status": "invalid_timeframe",
            "error": "日内做T规则目前只支持3分钟K线",
        }
    if len(frame) < params.min_bars:
        return {
            **base,
            "status": "insufficient_data",
            "missing": [f"至少需要{params.min_bars}根有效3分钟K线，当前只有{len(frame)}根"],
        }

    frame = _add_indicators(frame, params)
    latest = frame.iloc[-1]
    atr = float(latest["atr14"])
    if pd.isna(atr) or atr <= 0:
        return {
            **base,
            "status": "insufficient_data",
            "missing": ["无法计算有效ATR14"],
        }

    pivot_highs = _collect_pivots(
        frame,
        column="high",
        kind="high",
        params=params,
    )
    pivot_lows = _collect_pivots(
        frame,
        column="low",
        kind="low",
        params=params,
    )
    tolerance = atr * params.structure_tolerance_atr
    high_structure = _classify_highs(pivot_highs, tolerance)
    low_structure = _classify_lows(pivot_lows, tolerance)

    slope_start = frame.iloc[-1 - params.slope_lookback]
    ema20_slope_atr = (
        float(latest["ema20"]) - float(slope_start["ema20"])
    ) / atr
    ema20_above_ema50 = float(latest["ema20"]) > float(latest["ema50"])
    close_above_ema20 = float(latest["close"]) > float(latest["ema20"])

    strong_uptrend = (
        ema20_slope_atr >= params.steep_slope_atr
        and ema20_above_ema50
        and high_structure == "HH"
        and low_structure == "HL"
    )
    bearish_structure = (
        ema20_slope_atr < 0
        and not ema20_above_ema50
        and (high_structure == "LH" or low_structure == "LL")
    )
    range_structure = (
        abs(ema20_slope_atr) <= params.flat_slope_atr
        and high_structure in {"LH", "EQH"}
        and low_structure in {"HL", "EQL"}
    )

    if strong_uptrend:
        regime = "strong_uptrend"
        regime_label = "强趋势，少T并优先持有"
    elif bearish_structure:
        regime = "bearish"
        regime_label = "弱势下行，只高卖不机械低接"
    elif range_structure:
        regime = "range"
        regime_label = "EMA20走平且箱体结构形成，可进入做T环境"
    else:
        regime = "transition"
        regime_label = "趋势/箱体转换中，等待结构确认"

    avoid_high_sell_reasons: list[str] = []
    if strong_uptrend:
        avoid_high_sell_reasons.extend([
            "EMA20斜率较大且位于EMA50上方",
            "最近结构保持HH/HL，频繁高卖容易卖飞",
        ])

    latest_high = pivot_highs[-1] if pivot_highs else None
    previous_high = pivot_highs[-2] if len(pivot_highs) >= 2 else None
    pullback_between_highs = False
    if previous_high and latest_high:
        pullback_between_highs = any(
            previous_high["index"] < pivot["index"] < latest_high["index"]
            for pivot in pivot_lows
        )
    ema20_lost = not close_above_ema20
    high_sell_ready = (
        high_structure == "LH"
        and pullback_between_highs
        and ema20_lost
        and not strong_uptrend
    )
    if strong_uptrend:
        high_sell_status = "avoid"
    elif high_sell_ready:
        high_sell_status = "ready"
    elif high_structure == "LH":
        high_sell_status = "watch"
    else:
        high_sell_status = "not_ready"

    latest_low = pivot_lows[-1] if pivot_lows else None
    support = None
    support_touched = False
    reclaimed = False
    if latest_low:
        latest_low_price = float(latest_low["price"])
        support = _nearest_support(
            frame,
            pivot_lows,
            int(latest_low["index"]),
            latest_low_price,
        )
        support_touched = (
            float(support["distance_to_bar"]) <= atr * params.support_touch_atr
        )
        reclaimed = close_above_ema20 and _crossed_above_ema20(
            frame,
            int(latest_low["index"]) + 1,
        )

    avoid_low_buy_reasons: list[str] = []
    if bearish_structure:
        avoid_low_buy_reasons.extend([
            "EMA20已经下弯且位于EMA50下方",
            f"最近结构为{high_structure}/{low_structure}，尚未形成低位修复",
        ])
    low_buy_ready = (
        low_structure == "HL"
        and support_touched
        and reclaimed
        and not bearish_structure
    )
    if bearish_structure:
        low_buy_status = "blocked"
    elif low_buy_ready:
        low_buy_status = "ready"
    elif low_structure == "HL" or support_touched:
        low_buy_status = "watch"
    else:
        low_buy_status = "not_ready"

    if high_sell_ready:
        # Execution follows the EMA20 loss/failed reclaim confirmation instead
        # of pretending the earlier swing high was tradable in hindsight.
        sell_reference = float(latest["ema20"])
    else:
        sell_reference = float(latest_high["price"]) if latest_high else None
    buyback_reference = float(latest_low["price"]) if latest_low else None
    spacing, spacing_atr = _positive_spacing(sell_reference, buyback_reference, atr)
    minimum_spacing_met = bool(spacing_atr is not None and spacing_atr >= 1.0)
    preferred_spacing_met = bool(spacing_atr is not None and spacing_atr >= 1.5)
    has_two_sided_plan = bool(spacing is not None)

    if strong_uptrend:
        action = "hold_core_reduce_t_frequency"
        t_position = "10%-20%（取下限）"
    elif bearish_structure:
        action = "high_sell_only" if high_sell_ready else "wait_no_low_buy"
        t_position = "10%-20%"
    elif not has_two_sided_plan or not minimum_spacing_met:
        action = "skip_t_insufficient_space"
        t_position = "0%"
    elif high_sell_ready:
        action = "sell_t_position"
        t_position = "20%-30%" if regime == "range" else "10%-20%"
    elif low_buy_ready:
        action = "buyback_t_position"
        t_position = "20%-30%" if regime == "range" else "10%-20%"
    elif regime == "range":
        action = "plan_then_wait"
        t_position = "20%-30%"
    else:
        action = "wait_for_confirmation"
        t_position = "10%-20%"

    reasons = [
        f"EMA20五根斜率={ema20_slope_atr:.2f} ATR",
        f"EMA20{'高于' if ema20_above_ema50 else '低于或等于'}EMA50",
        f"高点/低点结构={high_structure}/{low_structure}",
    ]

    return {
        **base,
        "as_of": _date_at(frame, len(frame) - 1),
        "verdict": regime_label,
        "metrics": {
            "price": _round_price(latest["close"]),
            "ema20": _round_price(latest["ema20"]),
            "ema50": _round_price(latest["ema50"]),
            "atr14": _round_price(atr),
            "ema20_slope_atr_5bars": _round_metric(ema20_slope_atr),
            "ema_gap_atr": _round_metric(
                (float(latest["ema20"]) - float(latest["ema50"])) / atr
            ),
        },
        "structure": {
            "highs": high_structure,
            "lows": low_structure,
            "recent_pivot_highs": pivot_highs[-3:],
            "recent_pivot_lows": pivot_lows[-3:],
        },
        "environment": {
            "regime": regime,
            "label": regime_label,
            "reasons": reasons,
        },
        "signals": {
            "high_sell": {
                "status": high_sell_status,
                "failed_second_push": high_structure == "LH",
                "pullback_between_highs": pullback_between_highs,
                "ema20_lost": ema20_lost,
                "failed_high": latest_high,
                "confirmation_reference": _round_price(latest["ema20"]),
                "avoid_reasons": avoid_high_sell_reasons,
            },
            "low_buy": {
                "status": low_buy_status,
                "higher_low": low_structure == "HL",
                "support": support,
                "support_touched": support_touched,
                "ema20_reclaimed": reclaimed,
                "avoid_reasons": avoid_low_buy_reasons,
            },
        },
        "plan": {
            "action": action,
            "sell_reference": _round_price(sell_reference),
            "buyback_reference": _round_price(buyback_reference),
            "spacing": spacing,
            "spacing_atr": spacing_atr,
            "has_two_sided_plan": has_two_sided_plan,
            "minimum_1atr_met": minimum_spacing_met,
            "preferred_1_5atr_met": preferred_spacing_met,
            "t_position": t_position,
            "core_position_rule": "底仓不因一次3分钟波动全部卖出",
        },
        "risk": {
            "closed_bar_confirmation": (
                "已自动剔除最新未收盘3分钟K线；高卖/低接只使用已收盘K线确认"
                if dropped_unclosed_bar
                else "高卖/低接信号只使用已收盘3分钟K线确认"
            ),
            "execution_rule": "不猜绝对顶底，只交易高位失败与低位修复确认后的中间段",
            "leveraged_etf": "杠杆ETF使用T仓区间下限，并额外考虑波动衰减、滑点和隔夜风险",
        },
    }
