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
        setup_id: 'ema5_200_setup' or 'ema_200_highlow'
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
