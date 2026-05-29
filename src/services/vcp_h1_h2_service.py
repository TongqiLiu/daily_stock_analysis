# -*- coding: utf-8 -*-
"""Deterministic daily VCP + H1/H2 buy setup checks for Agent skills."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd


def _round_price(value: Any) -> Optional[float]:
    if value is None or pd.isna(value):
        return None
    return round(float(value), 4)


def _round_pct(value: Any) -> Optional[float]:
    if value is None or pd.isna(value):
        return None
    return round(float(value), 2)


def _coerce_numeric_frame(df: pd.DataFrame) -> pd.DataFrame:
    frame = df.copy()
    required = ["open", "high", "low", "close"]
    for column in required + ["volume"]:
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=required).reset_index(drop=True)
    if "volume" not in frame.columns:
        frame["volume"] = 0
    frame["volume"] = pd.to_numeric(frame["volume"], errors="coerce").fillna(0)
    return frame


def _date_at(frame: pd.DataFrame, idx: int) -> str:
    if "date" not in frame.columns:
        return str(idx)
    return str(frame.iloc[idx].get("date"))


def _previous_true_count(values: List[bool], idx: int, window: int) -> int:
    start = max(0, idx - window)
    return sum(1 for item in values[start:idx] if item)


def _bars_since_true(values: List[bool], idx: int) -> Optional[int]:
    for cursor in range(idx, -1, -1):
        if values[cursor]:
            return idx - cursor
    return None


def analyze_vcp_h1_h2_buy(
    df: pd.DataFrame,
    *,
    source: str = "unknown",
    timeframe: str = "daily",
) -> Dict[str, Any]:
    """Evaluate the latest daily bar against the VCP_H1_H2_BUY rules."""
    frame = _coerce_numeric_frame(df)
    required_points = 220
    if len(frame) < required_points:
        return {
            "setup_id": "vcp_h1_h2_buy",
            "setup_name": "VCP H1/H2 buy setup",
            "status": "insufficient_data",
            "data_points": len(frame),
            "required_data_points": required_points,
            "grade": 0,
            "verdict": "信息不足",
            "reasons": ["VCP H1/H2 至少需要约 220 根有效日线 K 线以稳定计算 EMA200。"],
        }

    near_high_ratio = 0.85
    range_max = 0.20
    buy_dedup_bars = 10
    use_long_filter = True

    close = frame["close"]
    high = frame["high"]
    low = frame["low"]
    open_ = frame["open"]
    volume = frame["volume"]

    frame["ema10"] = close.ewm(span=10, adjust=False).mean()
    frame["ema21"] = close.ewm(span=21, adjust=False).mean()
    frame["ema50"] = close.ewm(span=50, adjust=False).mean()
    frame["ema200"] = close.ewm(span=200, adjust=False).mean()
    frame["ma_close_20"] = close.rolling(20, min_periods=20).mean()
    frame["ma_close_10"] = close.rolling(10, min_periods=10).mean()
    frame["ma_close_5"] = close.rolling(5, min_periods=5).mean()
    frame["ma_volume_20"] = volume.rolling(20, min_periods=20).mean()
    frame["ma_volume_5"] = volume.rolling(5, min_periods=5).mean()
    frame["high_60"] = high.rolling(60, min_periods=60).max()
    frame["r20"] = (high.rolling(20, min_periods=20).max() - low.rolling(20, min_periods=20).min()) / frame["ma_close_20"]
    frame["r10"] = (high.rolling(10, min_periods=10).max() - low.rolling(10, min_periods=10).min()) / frame["ma_close_10"]
    frame["r5"] = (high.rolling(5, min_periods=5).max() - low.rolling(5, min_periods=5).min()) / frame["ma_close_5"]
    frame["pivot"] = high.shift(1).rolling(10, min_periods=10).max()

    tbase = (
        (close > frame["ema21"])
        & (frame["ema21"] > frame["ema50"])
        & (frame["ema21"] > frame["ema21"].shift(5))
        & (frame["ema50"] > frame["ema50"].shift(5))
    )
    trend = tbase & ((frame["ema50"] > frame["ema200"]) | (not use_long_filter))
    near_high = close >= frame["high_60"] * near_high_ratio
    contract = (
        (frame["r10"] < frame["r20"] * 0.80)
        & (frame["r5"] < frame["r10"] * 0.90)
        & (frame["r10"] < range_max)
    )
    dry_volume = frame["ma_volume_5"] < frame["ma_volume_20"] * 0.92
    close_below_ema21_count = (close < frame["ema21"]).rolling(10, min_periods=10).sum()
    hold_ema = (close_below_ema21_count <= 2) & (low.rolling(10, min_periods=10).min() > frame["ema21"] * 0.97)
    setup = (trend & near_high & contract & dry_volume & hold_ema).fillna(False).tolist()
    valid_setup = (pd.Series(setup).rolling(5, min_periods=1).sum() >= 1).tolist()
    break_buy = (
        pd.Series(valid_setup)
        & (close > frame["pivot"])
        & (close > open_)
        & (volume > frame["ma_volume_20"] * 1.10)
    ).fillna(False).tolist()
    up_trigger = (
        (high > high.shift(1))
        & (close > close.shift(1))
        & (close > open_)
    ).fillna(False).tolist()

    h1: List[bool] = []
    h2: List[bool] = []
    buy_raw: List[bool] = []
    buy: List[bool] = []
    for idx in range(len(frame)):
        current_h1 = bool(
            valid_setup[idx]
            and up_trigger[idx]
            and idx >= 2
            and float(high.iloc[idx - 1]) < float(high.iloc[idx - 2])
            and float(low.iloc[idx]) > float(frame["ema21"].iloc[idx]) * 0.97
        )
        h1.append(current_h1)

        bars_since_h1 = _bars_since_true(h1, idx)
        current_h2 = bool(
            valid_setup[idx]
            and up_trigger[idx]
            and bars_since_h1 is not None
            and bars_since_h1 >= 2
            and bars_since_h1 <= 6
            and float(low.iloc[idx]) > float(frame["ema21"].iloc[idx]) * 0.97
        )
        h2.append(current_h2)

        current_buy_raw = bool(break_buy[idx] or current_h1 or current_h2)
        buy_raw.append(current_buy_raw)
        buy.append(current_buy_raw and _previous_true_count(buy_raw, idx, buy_dedup_bars) == 0)

    latest_idx = len(frame) - 1
    latest = frame.iloc[latest_idx]
    latest_reasons: List[str] = []
    missing: List[str] = []

    condition_map = {
        "长期趋势模板": bool(trend.iloc[latest_idx]) if hasattr(trend, "iloc") else bool(trend[latest_idx]),
        "接近 60 日高位": bool(near_high.iloc[latest_idx]),
        "波动逐级收缩": bool(contract.iloc[latest_idx]),
        "量能持续收缩": bool(dry_volume.iloc[latest_idx]),
        "回踩 EMA21 健康": bool(hold_ema.iloc[latest_idx]),
        "最近 5 日出现 VCP 准备区": bool(valid_setup[latest_idx]),
    }
    for label, passed in condition_map.items():
        if passed:
            latest_reasons.append(label)
        else:
            missing.append(label)

    signal_labels = []
    if break_buy[latest_idx]:
        signal_labels.append("BREAK")
    if h1[latest_idx]:
        signal_labels.append("H1")
    if h2[latest_idx]:
        signal_labels.append("H2")

    if buy[latest_idx]:
        grade = 3
        verdict = "BUY"
        latest_reasons.append("突破 / H1 / H2 买点触发，且未处于 BUY 去重窗口。")
    elif buy_raw[latest_idx]:
        grade = 2
        verdict = "买点触发但处于去重窗口"
        latest_reasons.append("BREAK / H1 / H2 原始买点触发，但最近去重窗口内已有 BUY。")
    elif valid_setup[latest_idx]:
        grade = 1
        verdict = "VCP 准备区"
        latest_reasons.append("VCP 准备区有效，但尚未触发 BREAK / H1 / H2 买点。")
    else:
        grade = 0
        verdict = "不适合新开仓"

    recent_signals: List[Dict[str, Any]] = []
    for idx in range(max(0, len(frame) - 80), len(frame)):
        labels = []
        if break_buy[idx]:
            labels.append("BREAK")
        if h1[idx]:
            labels.append("H1")
        if h2[idx]:
            labels.append("H2")
        if buy[idx]:
            labels.append("BUY")
        if not labels:
            continue
        recent_signals.append(
            {
                "date": _date_at(frame, idx),
                "labels": labels,
                "close": _round_price(frame.iloc[idx]["close"]),
                "pivot": _round_price(frame.iloc[idx]["pivot"]),
                "stop": _round_price(frame.iloc[idx]["ema21"] * 0.97),
            }
        )

    return {
        "setup_id": "vcp_h1_h2_buy",
        "setup_name": "VCP H1/H2 buy setup",
        "status": "ok",
        "timeframe": timeframe,
        "source": source,
        "data_points": len(frame),
        "as_of": _date_at(frame, latest_idx),
        "current_price": _round_price(latest["close"]),
        "ema": {
            "ema10": _round_price(latest["ema10"]),
            "ema21": _round_price(latest["ema21"]),
            "ema50": _round_price(latest["ema50"]),
            "ema200": _round_price(latest["ema200"]),
        },
        "range": {
            "r20_pct": _round_pct(latest["r20"] * 100),
            "r10_pct": _round_pct(latest["r10"] * 100),
            "r5_pct": _round_pct(latest["r5"] * 100),
            "rmax_pct": _round_pct(range_max * 100),
        },
        "volume": {
            "ma5": _round_price(latest["ma_volume_5"]),
            "ma20": _round_price(latest["ma_volume_20"]),
            "dry_volume": bool(dry_volume.iloc[latest_idx]),
        },
        "setup": {
            "trend": condition_map["长期趋势模板"],
            "near_high": condition_map["接近 60 日高位"],
            "contract": condition_map["波动逐级收缩"],
            "dry_volume": condition_map["量能持续收缩"],
            "hold_ema": condition_map["回踩 EMA21 健康"],
            "setup": bool(setup[latest_idx]),
            "valid_setup": bool(valid_setup[latest_idx]),
        },
        "signals": {
            "break_buy": bool(break_buy[latest_idx]),
            "h1": bool(h1[latest_idx]),
            "h2": bool(h2[latest_idx]),
            "buy_raw": bool(buy_raw[latest_idx]),
            "buy": bool(buy[latest_idx]),
            "labels": signal_labels,
        },
        "pivot": _round_price(latest["pivot"]),
        "stop": _round_price(latest["ema21"] * 0.97),
        "recent_signals": recent_signals[-5:],
        "grade": grade,
        "verdict": verdict,
        "reasons": latest_reasons,
        "missing": list(dict.fromkeys(missing)),
        "risk_plan": {
            "stop_reference": _round_price(latest["ema21"] * 0.97),
            "pivot_reference": _round_price(latest["pivot"]),
            "failure_condition": "跌破 EMA21*0.97 或枢轴下方支撑失效；分批/止盈/持有规则按个人交易计划执行。",
        },
        "parameters": {
            "near_high_ratio": near_high_ratio,
            "range_max": range_max,
            "buy_dedup_bars": buy_dedup_bars,
            "use_long_filter": use_long_filter,
        },
    }
