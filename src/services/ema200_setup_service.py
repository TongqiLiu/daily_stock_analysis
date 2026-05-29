# -*- coding: utf-8 -*-
"""Deterministic EMA200 setup checks for Agent skills.

The service supports multiple timeframes:
- 5m: Pine Script v2/v3 original design (HTF=4H for bias)
- daily: Adapted version for swing trading (HTF=weekly for bias)

HTF (Higher TimeFrame) EMA200 is used as a trend filter to avoid counter-trend setups.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import pandas as pd


@dataclass(frozen=True)
class EMA200SetupParams:
    """Tunable thresholds mirroring the TradingView/Futu setup variants.

    Default values are for 5m timeframe (Pine Script original).
    For daily timeframe, use smaller windows via from_timeframe().
    """

    test_window: int = 8
    entry_window: int = 24
    cooldown_window: int = 24  # NEW: prevent duplicate signals
    confirm_pct: float = 0.0015  # 0.15% (adjusted from Futu indicator)
    near_ema_pct: float = 0.003  # 0.3% (adjusted from Futu indicator)
    double_bottom_tol: float = 0.0025
    pivot_left: int = 2
    pivot_right: int = 2
    stable_bars: int = 2
    resistance_lookback: int = 50
    min_rr: float = 1.0
    stop_buffer_pct: float = 0.0005

    # NEW: ORB (Opening Range Breakout) parameters
    orb_window: int = 6  # First 6 bars of trading day
    orb_volume_multiple: float = 1.5  # Volume > 1.5x MA(20)
    orb_lookback: int = 78  # ~1 trading day in 5m bars

    @classmethod
    def from_timeframe(cls, timeframe: str) -> "EMA200SetupParams":
        """Return timeframe-appropriate parameters."""
        if timeframe in ("5m", "15m", "30m", "1H"):
            # Intraday: use Futu indicator parameters
            return cls()
        elif timeframe in ("4H", "daily", "1D"):
            # Daily/4H: adapt to longer cycles
            return cls(
                test_window=3,
                entry_window=20,
                cooldown_window=20,
                pivot_left=3,
                pivot_right=3,
                stable_bars=2,
                resistance_lookback=50,
                # ORB not applicable to daily timeframe
                orb_window=0,
            )
        else:
            # Unknown: default to conservative daily-style params
            return cls(test_window=3, entry_window=20, cooldown_window=20, orb_window=0)


_SETUP_NAMES = {
    "ema5_200_setup": "EMA200 reclaim candidate",
    "ema_200_highlow": "EMA200 higher-low / double-bottom setup",
    "spy_orb_ema200": "SPY ORB + EMA200 trend setup",
}


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
    return frame


def _date_at(frame: pd.DataFrame, idx: int) -> str:
    if "date" not in frame.columns:
        return str(idx)
    return str(frame.iloc[idx].get("date"))


def _relation_to_ema(close: float, ema: float, confirm_pct: float) -> str:
    if close > ema * (1 + confirm_pct):
        return "上方"
    if close < ema * (1 - confirm_pct):
        return "下方"
    return "附近"


def _find_latest_touch(frame: pd.DataFrame, lookback: int, near_pct: float) -> Optional[int]:
    """Find most recent bar where price touched EMA200 within NEAR range.

    Futu indicator logic: LOW <= EMA200*(1+NEAR) AND HIGH >= EMA200*(1-NEAR)
    """
    start = max(0, len(frame) - lookback)
    for idx in range(len(frame) - 1, start - 1, -1):
        row = frame.iloc[idx]
        ema200 = float(row["ema200"])
        low = float(row["low"])
        high = float(row["high"])

        # Touch range: within NEAR% of EMA200
        if low <= ema200 * (1 + near_pct) and high >= ema200 * (1 - near_pct):
            return idx
    return None


def _detect_orb_signals(
    frame: pd.DataFrame,
    params: EMA200SetupParams,
) -> Dict[str, Any]:
    """Detect ORB (Opening Range Breakout) volume spikes.

    Futu indicator logic:
    ORBDN := INORB AND CLOSE<OPEN AND BIGVOL AND LOW<=E200*(1+NEAR)
    - INORB: within first ORB_WINDOW bars of trading day
    - CLOSE<OPEN: bearish bar
    - BIGVOL: volume > MA(20) * VOLM
    - LOW touches EMA200 area
    """
    if params.orb_window <= 0 or "date" not in frame.columns:
        return {"has_orb": False, "orb_bars": []}

    orb_bars = []

    # Calculate volume MA(20)
    frame["vol_ma20"] = frame["volume"].rolling(window=20, min_periods=1).mean()

    # Identify day boundaries
    frame["date_str"] = pd.to_datetime(frame["date"], errors="coerce").dt.date.astype(str)
    frame["new_day"] = frame["date_str"] != frame["date_str"].shift(1)
    frame["day_bar_num"] = frame.groupby((frame["new_day"].cumsum())).cumcount() + 1

    for idx in range(len(frame)):
        row = frame.iloc[idx]

        # Check ORB conditions
        in_orb = row["day_bar_num"] <= params.orb_window
        is_bearish = row["close"] < row["open"]
        is_big_vol = row["volume"] > row["vol_ma20"] * params.orb_volume_multiple
        near_ema = row["low"] <= row["ema200"] * (1 + params.near_ema_pct)

        if in_orb and is_bearish and is_big_vol and near_ema:
            orb_bars.append({
                "idx": idx,
                "date": _date_at(frame, idx),
                "close": _round_price(row["close"]),
                "volume_ratio": round(row["volume"] / row["vol_ma20"], 2),
            })

    return {
        "has_orb": len(orb_bars) > 0,
        "orb_bars": orb_bars[-3:],  # Keep last 3 for context
        "orb_count": len(orb_bars),
    }


def _is_pivot_low(frame: pd.DataFrame, idx: int, left: int, right: int) -> bool:
    if idx - left < 0 or idx + right >= len(frame):
        return False
    window = frame["low"].iloc[idx - left : idx + right + 1]
    return float(frame.iloc[idx]["low"]) <= float(window.min())


def _collect_structure(
    frame: pd.DataFrame,
    *,
    touch_idx: int,
    params: EMA200SetupParams,
) -> Dict[str, Any]:
    """Return confirmed EMA-area pivot structure by looking backwards from current bar.

    Matches Futu indicator logic: find recent pivots near EMA200 in the lookback window,
    then check if the most recent two form a higher low or double bottom.
    """
    first_low: Optional[float] = None
    first_idx: Optional[int] = None
    second_low: Optional[float] = None
    second_idx: Optional[int] = None
    structure_type = "none"
    active_stop: Optional[float] = None
    all_pivots: List[tuple[int, float]] = []  # (idx, low) pairs

    # Look backwards from current bar for pivots near EMA200
    current_idx = len(frame) - 1
    start_idx = max(0, current_idx - params.entry_window)

    for idx in range(start_idx, current_idx + 1):
        if not _is_pivot_low(frame, idx, params.pivot_left, params.pivot_right):
            continue
        pivot_low = float(frame.iloc[idx]["low"])
        pivot_ema = float(frame.iloc[idx]["ema200"])
        if pivot_ema <= 0:
            continue
        near_ema_pct = abs(pivot_low - pivot_ema) / pivot_ema
        if near_ema_pct > params.near_ema_pct:
            continue

        all_pivots.append((idx, pivot_low))

    # Process pivots in chronological order to find structure
    pivots_for_output: List[Dict[str, Any]] = []
    for idx, pivot_low in all_pivots:
        pivot_ema = float(frame.iloc[idx]["ema200"])
        near_ema_pct = abs(pivot_low - pivot_ema) / pivot_ema
        pivots_for_output.append(
            {
                "date": _date_at(frame, idx),
                "low": _round_price(pivot_low),
                "ema200": _round_price(pivot_ema),
                "distance_to_ema_pct": _round_pct(near_ema_pct * 100),
            }
        )

        if first_low is None:
            first_low = pivot_low
            first_idx = idx
            continue

        if first_idx is None or idx <= first_idx:
            continue

        diff_pct = (pivot_low - first_low) / first_low if first_low else 0
        is_higher_low = pivot_low > first_low * (1 + params.double_bottom_tol)
        is_double_bottom = abs(diff_pct) <= params.double_bottom_tol

        if is_higher_low:
            second_low = pivot_low
            second_idx = idx
            structure_type = "higher_low"
            active_stop = second_low
        elif is_double_bottom:
            second_low = pivot_low
            second_idx = idx
            structure_type = "double_bottom"
            active_stop = min(first_low, second_low)
        else:
            # Lower low resets the base
            first_low = pivot_low
            first_idx = idx
            second_low = None
            second_idx = None
            structure_type = "lower_low_reset"
            active_stop = None

    return {
        "has_structure": structure_type in {"higher_low", "double_bottom"},
        "structure_type": structure_type,
        "stop": _round_price(active_stop),
        "first_low": None if first_low is None else {
            "date": _date_at(frame, first_idx or 0),
            "low": _round_price(first_low),
        },
        "second_low": None if second_low is None else {
            "date": _date_at(frame, second_idx or 0),
            "low": _round_price(second_low),
        },
        "pivots": pivots_for_output[-5:],
    }


def _base_payload(
    *,
    setup_id: str,
    frame: pd.DataFrame,
    params: EMA200SetupParams,
    source: str,
    timeframe: str,
    htf_ema200: Optional[float],
) -> Dict[str, Any]:
    latest = frame.iloc[-1]
    close = float(latest["close"])
    ema200 = float(latest["ema200"])
    dist_pct = (close - ema200) / ema200 * 100 if ema200 else None

    payload = {
        "setup_id": setup_id,
        "setup_name": _SETUP_NAMES.get(setup_id, setup_id),
        "timeframe": timeframe,
        "source": source,
        "data_points": len(frame),
        "as_of": _date_at(frame, len(frame) - 1),
        "current_price": _round_price(close),
        "ema200": _round_price(ema200),
        "price_vs_ema200": _relation_to_ema(close, ema200, params.confirm_pct) if ema200 else "无法确认",
        "distance_to_ema200_pct": _round_pct(dist_pct),
    }

    # HTF bias information
    if htf_ema200 is not None:
        htf_dist_pct = (close - htf_ema200) / htf_ema200 * 100 if htf_ema200 else None
        payload["htf_ema200"] = _round_price(htf_ema200)
        payload["htf_bias"] = "多头" if close > htf_ema200 else "空头"
        payload["distance_to_htf_ema200_pct"] = _round_pct(htf_dist_pct)
    else:
        payload["htf_ema200"] = None
        payload["htf_bias"] = "未获取"

    return payload


def _date_key_at(frame: pd.DataFrame, idx: int) -> str:
    if "date" not in frame.columns:
        return "session"
    raw_value = str(frame.iloc[idx].get("date") or "").strip()
    parsed = pd.to_datetime(raw_value, errors="coerce")
    if not pd.isna(parsed):
        return parsed.date().isoformat()
    return raw_value.split()[0] if raw_value else "session"


def _previous_true_count(values: List[bool], idx: int, window: int) -> int:
    start = max(0, idx - window)
    return sum(1 for item in values[start:idx] if item)


def _analyze_spy_orb_ema200_setup(
    df: pd.DataFrame,
    *,
    setup_id: str,
    source: str,
    timeframe: str,
) -> Dict[str, Any]:
    """Evaluate the SPY 5m RTH ORB + EMA200 trend setup."""
    frame = _coerce_numeric_frame(df)
    required_points = 220
    if len(frame) < required_points:
        return {
            "setup_id": setup_id,
            "setup_name": _SETUP_NAMES[setup_id],
            "status": "insufficient_data",
            "data_points": len(frame),
            "required_data_points": required_points,
            "grade": 0,
            "verdict": "信息不足",
            "reasons": ["ORB + EMA200 setup 至少需要约 220 根有效 5m K 线以稳定计算 EMA200。"],
        }

    overext_multiple = 2.0
    average_distance_window = 50
    orb_bars = 12
    orb_buffer_pct = 0.03
    require_break = True
    eod_bar = 77
    session_bars = 78
    dedup_bars = 12
    enable_countertrend = False

    frame["ema200"] = frame["close"].ewm(span=200, adjust=False).mean()
    n = len(frame)
    day_bar = [0] * n
    orb_high: List[Optional[float]] = [None] * n
    orb_low: List[Optional[float]] = [None] * n
    orb_mid: List[Optional[float]] = [None] * n
    brk_up_latched = [False] * n
    brk_dn_latched = [False] * n

    session_indices: Dict[str, List[int]] = {}
    for idx in range(n):
        session_indices.setdefault(_date_key_at(frame, idx), []).append(idx)

    for indices in session_indices.values():
        if len(indices) < orb_bars:
            for pos, idx in enumerate(indices, start=1):
                day_bar[idx] = pos
            continue

        first_orb = frame.iloc[indices[:orb_bars]]
        fixed_orb_high = float(first_orb["high"].max())
        fixed_orb_low = float(first_orb["low"].min())
        fixed_orb_mid = (fixed_orb_high + fixed_orb_low) / 2
        seen_up = False
        seen_dn = False

        for pos, idx in enumerate(indices, start=1):
            day_bar[idx] = pos
            if pos <= orb_bars:
                continue

            orb_high[idx] = fixed_orb_high
            orb_low[idx] = fixed_orb_low
            orb_mid[idx] = fixed_orb_mid
            close = float(frame.iloc[idx]["close"])
            if close > fixed_orb_high:
                seen_up = True
            if close < fixed_orb_low:
                seen_dn = True
            brk_up_latched[idx] = seen_up
            brk_dn_latched[idx] = seen_dn

    is_complete = [bar > orb_bars for bar in day_bar]
    in_session = [bar <= session_bars for bar in day_bar]
    is_eod = [bar >= eod_bar for bar in day_bar]
    dist_pct: List[Optional[float]] = [None] * n
    avg_dist_pct: List[Optional[float]] = [None] * n
    overextended = [False] * n
    ema_bull = [False] * n
    ema_bear = [False] * n
    orb_bull = [False] * n
    orb_bear = [False] * n
    orb_neutral = [False] * n
    can_trade = [False] * n
    long_raw = [False] * n
    short_raw = [False] * n
    counter_long_raw = [False] * n
    counter_short_raw = [False] * n

    for idx in range(n):
        row = frame.iloc[idx]
        close = float(row["close"])
        ema200 = float(row["ema200"])
        dist = abs(close - ema200) / ema200 * 100 if ema200 else None
        dist_pct[idx] = dist
        if dist is not None:
            start = max(0, idx - average_distance_window + 1)
            window_values = [value for value in dist_pct[start : idx + 1] if value is not None]
            avg_dist = sum(window_values) / len(window_values) if window_values else None
            avg_dist_pct[idx] = avg_dist
            overextended[idx] = bool(avg_dist is not None and dist > avg_dist * overext_multiple)
        ema_bull[idx] = close > ema200
        ema_bear[idx] = close < ema200

        mid = orb_mid[idx]
        if is_complete[idx] and mid is not None:
            buffer_amount = mid * orb_buffer_pct / 100
            orb_bull[idx] = close > mid + buffer_amount
            orb_bear[idx] = close < mid - buffer_amount
            orb_neutral[idx] = not orb_bull[idx] and not orb_bear[idx]

        can_trade[idx] = (
            is_complete[idx]
            and in_session[idx]
            and not is_eod[idx]
            and not overextended[idx]
            and not orb_neutral[idx]
        )
        long_ok = brk_up_latched[idx] or not require_break
        short_ok = brk_dn_latched[idx] or not require_break
        long_raw[idx] = can_trade[idx] and ema_bull[idx] and orb_bull[idx] and long_ok
        short_raw[idx] = can_trade[idx] and ema_bear[idx] and orb_bear[idx] and short_ok
        counter_long_raw[idx] = can_trade[idx] and enable_countertrend and ema_bull[idx] and orb_bear[idx]
        counter_short_raw[idx] = can_trade[idx] and enable_countertrend and ema_bear[idx] and orb_bull[idx]

    long_entry = [
        bool(raw and _previous_true_count(long_raw, idx, dedup_bars) == 0)
        for idx, raw in enumerate(long_raw)
    ]
    short_entry = [
        bool(raw and _previous_true_count(short_raw, idx, dedup_bars) == 0)
        for idx, raw in enumerate(short_raw)
    ]
    counter_long_entry = [
        bool(raw and _previous_true_count(counter_long_raw, idx, dedup_bars) == 0)
        for idx, raw in enumerate(counter_long_raw)
    ]
    counter_short_entry = [
        bool(raw and _previous_true_count(counter_short_raw, idx, dedup_bars) == 0)
        for idx, raw in enumerate(counter_short_raw)
    ]

    def _signal_at(idx: int) -> Optional[str]:
        if long_entry[idx]:
            return "long"
        if short_entry[idx]:
            return "short"
        if counter_long_entry[idx]:
            return "counter_long"
        if counter_short_entry[idx]:
            return "counter_short"
        return None

    recent_signals: List[Dict[str, Any]] = []
    for idx in range(max(0, n - 60), n):
        signal = _signal_at(idx)
        if signal is None:
            continue
        recent_signals.append(
            {
                "type": signal,
                "label": {
                    "long": "多",
                    "short": "空",
                    "counter_long": "逆多",
                    "counter_short": "逆空",
                }[signal],
                "date": _date_at(frame, idx),
                "day_bar": day_bar[idx],
                "close": _round_price(frame.iloc[idx]["close"]),
                "ema200": _round_price(frame.iloc[idx]["ema200"]),
                "orb_high": _round_price(orb_high[idx]),
                "orb_low": _round_price(orb_low[idx]),
            }
        )

    latest_idx = n - 1
    latest = frame.iloc[latest_idx]
    latest_signal = _signal_at(latest_idx)
    close = float(latest["close"])
    ema200 = float(latest["ema200"])
    long_stop = None
    short_stop = None
    if orb_low[latest_idx] is not None:
        long_stop = max(float(orb_low[latest_idx]), ema200)
    if orb_high[latest_idx] is not None:
        short_stop = min(float(orb_high[latest_idx]), ema200)

    missing: List[str] = []
    reasons: List[str] = []
    if not is_complete[latest_idx]:
        missing.append("等待开盘 ORB 区间完成")
    if not in_session[latest_idx] or is_eod[latest_idx]:
        missing.append("不在允许新仓的 RTH 窗口")
    if overextended[latest_idx]:
        missing.append(f"距离 EMA200 超过近 {average_distance_window} 根平均乖离的 {overext_multiple:.1f} 倍")
    if orb_neutral[latest_idx]:
        missing.append("价格仍在 ORB 中线缓冲带内")
    if require_break and not (brk_up_latched[latest_idx] or brk_dn_latched[latest_idx]):
        missing.append("当天尚未收盘突破 ORB 高/低")

    if latest_signal == "long":
        grade = 3
        verdict = "顺势多头信号"
        reasons.append("价格在 EMA200 上方、ORB 中线上方，并已收盘突破 ORB 高点。")
    elif latest_signal == "short":
        grade = 3
        verdict = "顺势空头信号"
        reasons.append("价格在 EMA200 下方、ORB 中线下方，并已收盘跌破 ORB 低点。")
    elif long_raw[latest_idx] or short_raw[latest_idx]:
        grade = 2
        verdict = "条件满足但处于去重窗口"
        reasons.append("顺势条件仍成立，但同方向信号在去重窗口内已出现。")
    elif can_trade[latest_idx]:
        grade = 1
        verdict = "只适合观察"
        reasons.append("处于可交易窗口，但 EMA200 / ORB / 突破条件尚未同时满足。")
    else:
        grade = 0
        verdict = "不适合新开仓"
        reasons.append("当前未通过 ORB + EMA200 可交易过滤。")

    return {
        "setup_id": setup_id,
        "setup_name": _SETUP_NAMES[setup_id],
        "status": "ok",
        "timeframe": timeframe,
        "source": source,
        "data_points": len(frame),
        "as_of": _date_at(frame, latest_idx),
        "current_price": _round_price(close),
        "ema200": _round_price(ema200),
        "price_vs_ema200": "上方" if ema_bull[latest_idx] else "下方" if ema_bear[latest_idx] else "附近",
        "distance_to_ema200_pct": _round_pct(dist_pct[latest_idx]),
        "average_distance_to_ema200_pct": _round_pct(avg_dist_pct[latest_idx]),
        "day_bar": day_bar[latest_idx],
        "orb": {
            "bars": orb_bars,
            "high": _round_price(orb_high[latest_idx]),
            "low": _round_price(orb_low[latest_idx]),
            "mid": _round_price(orb_mid[latest_idx]),
            "buffer_pct": orb_buffer_pct,
            "bias": "多头" if orb_bull[latest_idx] else "空头" if orb_bear[latest_idx] else "中性",
            "break_up_seen": brk_up_latched[latest_idx],
            "break_down_seen": brk_dn_latched[latest_idx],
        },
        "filters": {
            "orb_complete": is_complete[latest_idx],
            "in_session": in_session[latest_idx],
            "is_eod": is_eod[latest_idx],
            "overextended": overextended[latest_idx],
            "can_trade": can_trade[latest_idx],
            "require_orb_break": require_break,
            "dedup_bars": dedup_bars,
            "countertrend_enabled": enable_countertrend,
        },
        "latest_signal": latest_signal,
        "recent_signals": recent_signals[-5:],
        "grade": grade,
        "verdict": verdict,
        "reasons": reasons,
        "missing": list(dict.fromkeys(missing)),
        "risk_plan": {
            "long_stop_reference": _round_price(long_stop),
            "short_stop_reference": _round_price(short_stop),
            "target_rule": "顺势信号按约 2R 手动止盈；逆势信号默认关闭。",
            "failure_condition": "跌破/突破对应止损参考、ORB/EMA200 偏向翻转，或接近 15:50 ET 按纪律平仓。",
            "notes": [
                "该指标只输出信号和画线，不内建自动止损、止盈、最少持有或 EOD 平仓。",
                "设计前提是 5m 常规时段 RTH；包含盘前盘后时 ORB bar 计数会失真。",
            ],
        },
        "parameters": {
            "overextension_multiple": overext_multiple,
            "average_distance_window": average_distance_window,
            "orb_bars": orb_bars,
            "orb_buffer_pct": orb_buffer_pct,
            "eod_bar": eod_bar,
            "session_bars": session_bars,
            "dedup_bars": dedup_bars,
        },
    }


def analyze_ema200_setup(
    df: pd.DataFrame,
    *,
    setup_id: str = "ema_200_highlow",
    source: str = "unknown",
    timeframe: str = "daily",
    htf_df: pd.DataFrame | None = None,
    params: EMA200SetupParams | None = None,
) -> Dict[str, Any]:
    """Analyze the latest bar against EMA200 setup rules.

    Args:
        df: OHLCV DataFrame (chart timeframe)
        setup_id: 'ema5_200_setup', 'ema_200_highlow', or 'spy_orb_ema200'
        source: Data source name
        timeframe: Chart timeframe ('5m', 'daily', etc.)
        htf_df: Optional higher-timeframe OHLCV for HTF EMA200 bias filter
        params: Optional parameter overrides

    Returns a JSON-serializable structure with a conservative 0-3 grade:
    0 not suitable, 1 observe, 2 candidate, 3 executable structure.
    """
    params = params or EMA200SetupParams.from_timeframe(timeframe)
    setup_id = (setup_id or "ema_200_highlow").strip()
    if setup_id not in _SETUP_NAMES:
        return {
            "setup_id": setup_id,
            "status": "error",
            "error": f"Unsupported setup_id: {setup_id}",
            "supported_setup_ids": sorted(_SETUP_NAMES),
        }

    if setup_id == "spy_orb_ema200":
        return _analyze_spy_orb_ema200_setup(
            df,
            setup_id=setup_id,
            source=source,
            timeframe=timeframe,
        )

    frame = _coerce_numeric_frame(df)
    if len(frame) < 220:
        return {
            "setup_id": setup_id,
            "setup_name": _SETUP_NAMES[setup_id],
            "status": "insufficient_data",
            "data_points": len(frame),
            "required_data_points": 220,
            "grade": 0,
            "verdict": "信息不足",
            "reasons": ["EMA200 setup 至少需要约 220 根有效 K 线以稳定计算 EMA200。"],
        }

    frame["ema200"] = frame["close"].ewm(span=200, adjust=False).mean()

    # Calculate HTF EMA200 if htf_df provided
    htf_ema200 = None
    if htf_df is not None and not htf_df.empty:
        htf_frame = _coerce_numeric_frame(htf_df)
        if len(htf_frame) >= 220:
            htf_frame["ema200"] = htf_frame["close"].ewm(span=200, adjust=False).mean()
            htf_ema200 = float(htf_frame.iloc[-1]["ema200"])

    payload = _base_payload(
        setup_id=setup_id,
        frame=frame,
        params=params,
        source=source,
        timeframe=timeframe,
        htf_ema200=htf_ema200,
    )

    latest = frame.iloc[-1]
    close = float(latest["close"])
    ema200 = float(latest["ema200"])

    # HTF bias filter: only proceed if HTF is bullish (or HTF not available)
    if htf_ema200 is not None and close < htf_ema200:
        payload.update(
            {
                "status": "htf_bearish",
                "grade": 0,
                "verdict": "不适合进场",
                "reasons": [
                    f"价格 {_round_price(close)} 低于 HTF EMA200 {_round_price(htf_ema200)}，"
                    "HTF 趋势为空头，跳过多头 setup。"
                ],
                "missing": ["HTF 多头趋势确认"],
            }
        )
        return payload

    # Detect ORB signals (for 5m timeframe)
    orb_info = _detect_orb_signals(frame, params)

    touch_idx = _find_latest_touch(frame, params.entry_window + 1, params.near_ema_pct)

    missing: List[str] = []
    reasons: List[str] = []

    if touch_idx is None:
        payload.update(
            {
                "status": "no_setup",
                "grade": 0,
                "verdict": "不适合进场",
                "latest_touch": None,
                "stage1": {"satisfied": False, "label": "未触碰 EMA200"},
                "stage2": {"satisfied": False, "label": "未评估"},
                "reasons": ["最近观察窗口内没有看到价格触碰 EMA200。"],
                "missing": ["没有 EMA200 回踩/触碰结构"],
            }
        )
        return payload

    bars_since_touch = len(frame) - 1 - touch_idx
    test_end_idx = min(len(frame) - 1, touch_idx + params.test_window)
    test_frame = frame.iloc[touch_idx + 1 : test_end_idx + 1]
    candidate_hits = test_frame.index[
        test_frame["close"] > test_frame["ema200"] * (1 + params.confirm_pct)
    ].tolist()
    fail_hits = test_frame.index[
        test_frame["close"] < test_frame["ema200"] * (1 - params.confirm_pct)
    ].tolist()
    first_candidate_idx = candidate_hits[0] if candidate_hits else None
    first_fail_idx = fail_hits[0] if fail_hits else None
    failed_before_candidate = bool(
        first_fail_idx is not None
        and (first_candidate_idx is None or first_fail_idx < first_candidate_idx)
    )
    reclaimed = close > ema200 * (1 + params.confirm_pct)
    failed_now = close < ema200 * (1 - params.confirm_pct)
    candidate_seen = first_candidate_idx is not None and not failed_before_candidate
    candidate_active = candidate_seen and bars_since_touch <= params.entry_window and not failed_now
    observe = bars_since_touch <= params.test_window and not candidate_seen and not failed_now and not failed_before_candidate

    payload["latest_touch"] = {
        "date": _date_at(frame, touch_idx),
        "bars_since_touch": bars_since_touch,
        "touch_price_close": _round_price(frame.iloc[touch_idx]["close"]),
        "ema200_at_touch": _round_price(frame.iloc[touch_idx]["ema200"]),
    }

    if candidate_active:
        reasons.append("价格在触碰 EMA200 后的测试窗口内重新站上 EMA200 buffer。")
    elif observe:
        reasons.append("价格仍处在 EMA200 触碰后的观察窗口，但尚未形成明确 reclaim。")
        missing.append("EMA200 reclaim 尚未确认")
    elif failed_now or failed_before_candidate:
        reasons.append("价格在测试窗口内跌破 EMA200 buffer，基础 setup 失败。")
    else:
        reasons.append("触碰 EMA200 后已超过基础测试窗口，原始候选信号超时。")
        missing.append("候选窗口内 reclaim")

    payload["candidate"] = None if first_candidate_idx is None else {
        "date": _date_at(frame, first_candidate_idx),
        "bars_since_candidate": len(frame) - 1 - first_candidate_idx,
        "candidate_close": _round_price(frame.iloc[first_candidate_idx]["close"]),
    }

    if setup_id == "ema5_200_setup":
        grade = 2 if candidate_active else 1 if observe else 0
        verdict = "可以小仓尝试 / candidate" if grade == 2 else "只适合观察" if grade == 1 else "不适合进场"
        payload.update(
            {
                "status": "ok",
                "grade": grade,
                "verdict": verdict,
                "stage1": {
                    "satisfied": candidate_active,
                    "label": "EMA200 touch + reclaim candidate" if candidate_active else "观察/失败/超时",
                },
                "stage2": {
                    "satisfied": False,
                    "label": "此 skill 不评估 HL/双底正式入场结构",
                },
                "reasons": reasons,
                "missing": missing,
                "risk_plan": {
                    "stop_reference": "未由本基础 skill 确认；需另行确认 higher low / 双底低点。",
                    "one_r_target": None,
                    "runner_target": None,
                    "failure_condition": "收盘重新跌破 EMA200 buffer 或后续形成 lower low。",
                },
            }
        )
        return payload

    structure = _collect_structure(frame, touch_idx=touch_idx, params=params)
    stop = structure.get("stop")
    stable_window = frame.tail(params.stable_bars)
    stable_near_ema = bool(
        not stable_window.empty
        and (stable_window["close"] / stable_window["ema200"]).min() >= 1 - params.confirm_pct
    )
    resistance_slice = frame["high"].iloc[max(0, len(frame) - params.resistance_lookback - 1) : len(frame) - 1]
    recent_resistance = float(resistance_slice.max()) if not resistance_slice.empty else None
    risk = close - float(stop) if stop is not None else None
    reward = recent_resistance - close if recent_resistance is not None else None
    rr = reward / risk if risk and risk > 0 and reward is not None else None
    has_1r_space = rr is not None and rr >= params.min_rr
    structure_fail = bool(stop is not None and close < float(stop) * (1 - params.stop_buffer_pct))
    formal_entry = bool(candidate_active and structure.get("has_structure") and reclaimed and stable_near_ema and has_1r_space and not structure_fail)

    # A+ signal: formal entry with ORB volume spike (Futu indicator enhancement)
    has_aplus = formal_entry and orb_info["has_orb"]

    if not structure.get("has_structure"):
        missing.append("EMA200 附近的 higher low / 双底结构")
    if stop is None:
        missing.append("明确结构止损线")
    if not stable_near_ema:
        missing.append("EMA200 附近企稳收盘")
    if rr is None:
        missing.append("可量化的 1R 空间")
    elif not has_1r_space:
        missing.append(f"到最近阻力不足 {params.min_rr:.2f}R")

    if formal_entry:
        grade = 3
        verdict = "可以执行" + (" (A+)" if has_aplus else "")
        reasons.append("已满足候选 + EMA200 附近 HL/双底 + 企稳 + 至少 1R 空间。")
        if has_aplus:
            reasons.append(f"A+增强：检测到 {orb_info['orb_count']} 次 ORB 放量下杀，setup 质量更高。")
    elif candidate_active:
        grade = 2
        verdict = "可以小仓尝试 / candidate"
        reasons.append("基础候选成立，但正式入场结构尚未完全满足。")
    elif observe and not structure_fail:
        grade = 1
        verdict = "只适合观察"
    else:
        grade = 0
        verdict = "不适合进场"
        if structure_fail:
            reasons.append("价格已跌破结构止损参考位，setup 失败。")

    payload.update(
        {
            "status": "ok",
            "grade": grade,
            "verdict": verdict,
            "stage1": {
                "satisfied": candidate_active,
                "label": "EMA200 touch + reclaim candidate" if candidate_active else "观察/失败/超时",
            },
            "stage2": {
                "satisfied": formal_entry,
                "label": "HL/双底正式入场结构" if formal_entry else "正式结构未完成",
            },
            "structure": structure,
            "orb": orb_info,  # NEW: ORB volume signals
            "has_aplus": has_aplus,  # NEW: A+ quality indicator
            "risk_reward": {
                "stop_reference": stop,
                "risk_per_share": _round_price(risk),
                "recent_resistance": _round_price(recent_resistance),
                "reward_to_resistance": _round_price(reward),
                "rr_to_resistance": None if rr is None else round(float(rr), 2),
                "one_r_target": _round_price(close + risk) if risk and risk > 0 else None,
                "runner_target": _round_price(recent_resistance),
                "has_min_rr": has_1r_space,
                "failure_condition": (
                    f"收盘跌破结构止损参考 {stop}" if stop is not None else "跌破 EMA200 或形成 lower low"
                ),
            },
            "reasons": reasons,
            "missing": list(dict.fromkeys(missing)),
        }
    )
    return payload
