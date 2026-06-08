# -*- coding: utf-8 -*-
"""Deterministic daily VCP / bull-flag breakout checks for Agent skills."""

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


def _rolling_true_count(values: List[bool], idx: int, window: int) -> int:
    start = max(0, idx - window + 1)
    return sum(1 for item in values[start : idx + 1] if item)


def analyze_vcp_breakout_trader(
    df: pd.DataFrame,
    *,
    source: str = "unknown",
    timeframe: str = "daily",
) -> Dict[str, Any]:
    """Evaluate the latest daily bar against the VCP_BREAKOUT_TRADER rules."""
    frame = _coerce_numeric_frame(df)
    required_points = 220
    if len(frame) < required_points:
        return {
            "setup_id": "vcp_breakout_trader",
            "setup_name": "VCP / bull-flag breakout trader setup",
            "status": "insufficient_data",
            "data_points": len(frame),
            "required_data_points": required_points,
            "grade": 0,
            "verdict": "信息不足",
            "reasons": ["VCP突破交易员指标至少需要约 220 根有效日线 K 线以稳定计算 EMA200。"],
        }

    near_high_ratio = 0.88
    range_max = 0.18
    dry_volume_ratio = 0.90
    breakout_volume_ratio = 1.35
    buy_dedup_bars = 10
    valid_setup_window = 10
    failure_window = 10
    use_long_filter = True
    extension_atr_multiple = 3.50
    extension_pct = 0.18
    risk_max = 0.10

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
    frame["pivot"] = high.shift(1).rolling(20, min_periods=20).max()
    frame["pivot_short"] = high.shift(1).rolling(10, min_periods=10).max()
    frame["stop_base"] = low.shift(1).rolling(10, min_periods=10).min()
    frame["atr_app"] = (high - low).rolling(14, min_periods=14).mean()

    tbase = (
        (close > frame["ema21"])
        & (frame["ema21"] > frame["ema50"])
        & (frame["ema21"] > frame["ema21"].shift(5))
        & (frame["ema50"] > frame["ema50"].shift(5))
    )
    long_ok = (frame["ema50"] > frame["ema200"]) | (not use_long_filter)
    trend = tbase & long_ok
    leader_zone = close >= frame["high_60"] * near_high_ratio

    contract = (
        (frame["r10"] < frame["r20"] * 0.85)
        & (frame["r5"] < frame["r10"] * 0.95)
        & (frame["r10"] < range_max)
    )
    hl1 = low.rolling(5, min_periods=5).min() > low.shift(5).rolling(10, min_periods=10).min() * 0.995
    hl2 = low.rolling(10, min_periods=10).min() > low.shift(10).rolling(20, min_periods=20).min() * 0.970
    higher_low = hl1 & hl2
    dry_volume = frame["ma_volume_5"] < frame["ma_volume_20"] * dry_volume_ratio
    close_below_ema21_count = (close < frame["ema21"]).rolling(10, min_periods=10).sum()
    hold_ema21 = (close_below_ema21_count <= 3) & (low.rolling(10, min_periods=10).min() > frame["ema21"] * 0.94)

    impulse = high.rolling(45, min_periods=45).max() / low.rolling(60, min_periods=60).min() - 1 > 0.25
    flag_tight = (frame["r10"] < frame["r20"] * 0.95) & (frame["r10"] < range_max * 1.10)
    bull_flag = trend & impulse & flag_tight & hold_ema21

    vcp_setup_series = trend & leader_zone & contract & dry_volume & hold_ema21 & higher_low
    flag_setup_series = bull_flag & dry_volume & higher_low
    setup_series = (vcp_setup_series | flag_setup_series).fillna(False)
    valid_setup_series = (
        (setup_series.rolling(valid_setup_window, min_periods=1).sum() >= 1)
        & trend
        & hold_ema21
        & higher_low
    ).fillna(False)

    vcp_setup_series = vcp_setup_series.fillna(False)
    flag_setup_series = flag_setup_series.fillna(False)
    vcp_start_series = vcp_setup_series & ~vcp_setup_series.shift(1, fill_value=False)
    flag_start_series = flag_setup_series & ~flag_setup_series.shift(1, fill_value=False) & ~vcp_setup_series

    risk_ok_series = (
        (frame["stop_base"] > 0)
        & (close > frame["stop_base"])
        & (((close - frame["stop_base"]) / frame["stop_base"]) <= risk_max)
    ).fillna(False)
    extend_series = (
        (close > frame["ema21"])
        & (
            ((close - frame["ema21"]) > frame["atr_app"] * extension_atr_multiple)
            | (close > frame["ema21"] * (1 + extension_pct))
        )
    ).fillna(False)
    extend_first_series = extend_series & ~extend_series.shift(1, fill_value=False)

    fail_raw_series = (
        (valid_setup_series.rolling(failure_window, min_periods=1).sum() >= 1)
        & (close < frame["stop_base"])
        & (volume > frame["ma_volume_20"] * 0.90)
    ).fillna(False)
    fail_series = fail_raw_series & ~fail_raw_series.shift(1, fill_value=False)

    vcp_setup = vcp_setup_series.tolist()
    flag_setup = flag_setup_series.tolist()
    vcp_start = vcp_start_series.tolist()
    flag_start = flag_start_series.tolist()
    valid_setup = valid_setup_series.tolist()
    risk_ok = risk_ok_series.tolist()
    extend = extend_series.tolist()
    extend_first = extend_first_series.tolist()
    fail = fail_series.tolist()

    lc_raw = (
        valid_setup_series
        & (close > frame["ema10"])
        & (close > frame["ema21"])
        & (close > open_)
        & (high > frame["pivot_short"] * 0.985)
        & (close > frame["pivot_short"] * 0.975)
        & (close < frame["pivot"])
        & risk_ok_series
        & ~extend_series
    ).fillna(False).tolist()
    break_raw = (
        valid_setup_series
        & (close > frame["pivot"])
        & (close > open_)
        & (volume > frame["ma_volume_20"] * breakout_volume_ratio)
        & risk_ok_series
        & ~extend_series
    ).fillna(False).tolist()

    lc: List[bool] = []
    break_buy: List[bool] = []
    for idx in range(len(frame)):
        lc.append(bool(lc_raw[idx] and _previous_true_count(lc_raw, idx, buy_dedup_bars) == 0))
        break_buy.append(bool(break_raw[idx] and _previous_true_count(break_raw, idx, buy_dedup_bars) == 0))

    latest_idx = len(frame) - 1
    latest = frame.iloc[latest_idx]
    latest_reasons: List[str] = []
    missing: List[str] = []

    condition_map = {
        "趋势模板": bool(trend.iloc[latest_idx]),
        "靠近 60 日高点": bool(leader_zone.iloc[latest_idx]),
        "VCP 波动收缩": bool(contract.iloc[latest_idx]),
        "Higher Lows": bool(higher_low.iloc[latest_idx]),
        "量能收缩": bool(dry_volume.iloc[latest_idx]),
        "守住 EMA21": bool(hold_ema21.iloc[latest_idx]),
        "牛旗结构": bool(bull_flag.iloc[latest_idx]),
        "最近 10 日准备区仍有效": bool(valid_setup[latest_idx]),
        "买点风险 <= 10%": bool(risk_ok[latest_idx]),
    }
    for label, passed in condition_map.items():
        if passed:
            latest_reasons.append(label)
        else:
            missing.append(label)

    signal_labels = []
    if vcp_start[latest_idx]:
        signal_labels.append("VCP")
    if flag_start[latest_idx]:
        signal_labels.append("FLAG")
    if lc[latest_idx]:
        signal_labels.append("LC")
    if break_buy[latest_idx]:
        signal_labels.append("BUY")
    if extend_first[latest_idx]:
        signal_labels.append("EXT")
    if fail[latest_idx]:
        signal_labels.append("FAIL")

    if fail[latest_idx]:
        grade = 0
        verdict = "结构失败"
        latest_reasons.append("跌破最近结构低点，setup 失效。")
    elif break_buy[latest_idx]:
        grade = 4
        verdict = "突破 BUY"
        latest_reasons.append("放量突破 20 日枢轴，且未处于突破去重窗口。")
    elif lc[latest_idx]:
        grade = 3
        verdict = "LC 提前买点"
        latest_reasons.append("价格收回 EMA10/EMA21、贴近短枢轴且尚未突破 20 日枢轴，形成 Low-Cheat。")
    elif vcp_setup[latest_idx]:
        grade = 2
        verdict = "VCP 准备区"
        latest_reasons.append("VCP 准备区成立，但尚未触发标准突破。")
    elif flag_setup[latest_idx]:
        grade = 2
        verdict = "牛旗准备区"
        latest_reasons.append("牛旗/旗形准备区成立，但尚未触发标准突破。")
    elif valid_setup[latest_idx]:
        grade = 1
        verdict = "准备区延续"
        latest_reasons.append("最近 10 日出现过准备区，且当前趋势、EMA21 健康和 Higher Lows 仍有效。")
    else:
        grade = 0
        verdict = "不适合新开仓"

    if extend[latest_idx]:
        latest_reasons.append("价格相对 EMA21 已过度延展，新仓追入风险升高。")

    recent_signals: List[Dict[str, Any]] = []
    for idx in range(max(0, len(frame) - 80), len(frame)):
        labels = []
        if vcp_start[idx]:
            labels.append("VCP")
        if flag_start[idx]:
            labels.append("FLAG")
        if lc[idx]:
            labels.append("LC")
        if break_buy[idx]:
            labels.append("BUY")
        if extend_first[idx]:
            labels.append("EXT")
        if fail[idx]:
            labels.append("FAIL")
        if not labels:
            continue
        recent_signals.append(
            {
                "date": _date_at(frame, idx),
                "labels": labels,
                "close": _round_price(frame.iloc[idx]["close"]),
                "pivot": _round_price(frame.iloc[idx]["pivot"]),
                "pivot_short": _round_price(frame.iloc[idx]["pivot_short"]),
                "stop": _round_price(frame.iloc[idx]["stop_base"]),
            }
        )

    stop_base = latest["stop_base"]
    pivot = latest["pivot"]
    risk_pct = None
    if stop_base is not None and not pd.isna(stop_base) and float(stop_base) > 0:
        risk_pct = max((float(latest["close"]) - float(stop_base)) / float(stop_base) * 100, 0)

    return {
        "setup_id": "vcp_breakout_trader",
        "setup_name": "VCP / bull-flag breakout trader setup",
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
            "breakout_volume_ratio": breakout_volume_ratio,
        },
        "setup": {
            "trend": condition_map["趋势模板"],
            "leader_zone": condition_map["靠近 60 日高点"],
            "contract": condition_map["VCP 波动收缩"],
            "higher_low": condition_map["Higher Lows"],
            "dry_volume": condition_map["量能收缩"],
            "hold_ema21": condition_map["守住 EMA21"],
            "bull_flag": condition_map["牛旗结构"],
            "vcp_setup": bool(vcp_setup[latest_idx]),
            "flag_setup": bool(flag_setup[latest_idx]),
            "valid_setup": bool(valid_setup[latest_idx]),
        },
        "signals": {
            "vcp_start": bool(vcp_start[latest_idx]),
            "flag_start": bool(flag_start[latest_idx]),
            "lc_raw": bool(lc_raw[latest_idx]),
            "lc": bool(lc[latest_idx]),
            "break_raw": bool(break_raw[latest_idx]),
            "break_buy": bool(break_buy[latest_idx]),
            "extend": bool(extend[latest_idx]),
            "extend_first": bool(extend_first[latest_idx]),
            "fail": bool(fail[latest_idx]),
            "labels": signal_labels,
        },
        "pivot": _round_price(pivot),
        "pivot_short": _round_price(latest["pivot_short"]),
        "stop": _round_price(stop_base),
        "risk_pct_to_stop": _round_pct(risk_pct),
        "risk_ok": bool(risk_ok[latest_idx]),
        "recent_signals": recent_signals[-8:],
        "grade": grade,
        "verdict": verdict,
        "reasons": latest_reasons,
        "missing": list(dict.fromkeys(missing)),
        "risk_plan": {
            "entry_reference": _round_price(pivot if break_buy[latest_idx] or not lc[latest_idx] else latest["pivot_short"]),
            "stop_reference": _round_price(stop_base),
            "failure_condition": "跌破 STOP_BASE / 最近 higher low，或突破后放量跌回枢轴下方。",
            "extension_warning": "出现 EXT 时，新仓慎追；已有仓位按 10/21EMA 或前低跟踪。",
        },
        "parameters": {
            "near_high_ratio": near_high_ratio,
            "range_max": range_max,
            "dry_volume_ratio": dry_volume_ratio,
            "breakout_volume_ratio": breakout_volume_ratio,
            "buy_dedup_bars": buy_dedup_bars,
            "valid_setup_window": valid_setup_window,
            "failure_window": failure_window,
            "use_long_filter": use_long_filter,
            "extension_atr_multiple": extension_atr_multiple,
            "extension_pct": extension_pct,
            "risk_max": risk_max,
        },
    }
