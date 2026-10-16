"""Versioned daily-swing plans; the same prefix-only calculation powers replay.

The risk percentages below divide one user-defined campaign loss budget, never
account equity. Enablement is opt-in after independent out-of-sample acceptance.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import math
from typing import Any, Iterable

import pandas as pd

PLAN_VERSION = "daily-swing-v1"
SETUPS = ("trend_pullback", "initial_breakout", "bottom_reversal")
SETUP_LABELS = dict(zip(SETUPS, ("顺势回踩", "初次突破", "底部反转试仓")))
TRIGGERS = {
    "trend_pullback": "确认HH/HL；近3根测试MA20或确认HL的0.5ATR范围；阳线收盘超过前一根高点",
    "initial_breakout": "阳线收盘突破此前20日高点，收在当根振幅上方25%区域",
    "bottom_reversal": "低点扫破此前20日低点，阳线收回其上，收在振幅上方25%区域",
}
MIN_REWARD_RISK = 1.5
RISK_FRACTIONS = dict(zip(SETUPS, (0.25, 0.25, 0.125)))


def normalize_enabled_setups(value: str | Iterable[str]) -> list[str]:
    values = value.split(",") if isinstance(value, str) else value
    result = list(dict.fromkeys(str(item).strip() for item in values if str(item).strip()))
    unknown = set(result) - set(SETUPS)
    if unknown:
        raise ValueError(f"Unknown multi-strategy entry setups: {','.join(sorted(unknown))}")
    return result


def validate_daily_frame(df: pd.DataFrame, *, minimum: int = 120) -> pd.DataFrame:
    """Reject damaged history instead of silently repairing the evidence."""
    required = {"date", "open", "high", "low", "close"}
    if df is None or not required.issubset(df.columns) or len(df) < minimum:
        raise ValueError("insufficient_daily_history")
    frame = df.copy()
    dates = pd.to_datetime(frame["date"], errors="coerce").dt.date
    if dates.isna().any() or dates.duplicated().any() or not dates.is_monotonic_increasing:
        raise ValueError("invalid_daily_dates")
    frame["date"] = dates
    for column in ("open", "high", "low", "close"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
        if not frame[column].map(lambda x: math.isfinite(x) and x > 0).all():
            raise ValueError("invalid_daily_prices")
    if ((frame["high"] < frame[["open", "close", "low"]].max(axis=1)) |
            (frame["low"] > frame[["open", "close", "high"]].min(axis=1))).any():
        raise ValueError("invalid_daily_ohlc")
    return frame.reset_index(drop=True)


def unavailable_plan(code: str, reason: str) -> dict:
    return {
        "version": PLAN_VERSION, "stock_code": code, "timeframe": "daily",
        "status": "unavailable", "reason": reason, "new_position_allowed": False,
        "action": "watch", "validation_status": "not_evaluated",
        "risk_budget_status": "not_provided", "shares": None,
    }


def valid_important_lows(frame: pd.DataFrame, pa: dict) -> list[dict]:
    """Confirmed lows that have not subsequently been broken, on this prefix."""
    return [low for low in pa["risk_management"]["important_low_candidates"]
            if low.get("confirmed") and float(low["price"]) < float(frame.iloc[-1]["close"])
            and low.get("date") and
            float(frame.loc[pd.to_datetime(frame["date"]).dt.date >= date.fromisoformat(low["date"][:10]), "low"].min())
            >= float(low["price"]) - 1e-4]


def build_swing_trade_plan(
    df: pd.DataFrame, *, stock_code: str, source: str, adjustment: str,
    next_session: date, enabled_setups: Iterable[str] = (),
) -> dict:
    """Compute a candidate after the supplied last *completed* session.

    No price/stop/target supplied by the LLM is accepted. Callers validate the
    exchange calendar/freshness; replay passes only the history prefix.
    """
    from src.agent.tools.analysis_tools import analyze_price_action_frame

    try:
        frame = validate_daily_frame(df)
    except ValueError as exc:
        return unavailable_plan(stock_code, str(exc))
    if adjustment not in {"forward", "split_dividend_adjusted", "unadjusted_verified"}:
        return unavailable_plan(stock_code, "adjustment_unverified")
    signal_date = frame.iloc[-1]["date"]
    if next_session <= signal_date or not source:
        return unavailable_plan(stock_code, "invalid_snapshot_identity")
    pa = analyze_price_action_frame(frame, stock_code=stock_code, source=source)
    if pa.get("status") != "ok":
        return unavailable_plan(stock_code, "price_action_unavailable")
    levels = pa["levels"]
    last, prev = frame.iloc[-1], frame.iloc[-2]
    close, atr = float(last["close"]), float(levels["atr14"])
    if atr <= 0:
        return unavailable_plan(stock_code, "invalid_atr")
    bull_bar = close > float(last["open"])
    span = float(last["high"] - last["low"])
    strong_close = span > 0 and (close - float(last["low"])) / span >= 0.75
    valid_lows = valid_important_lows(frame, pa)
    selected_low = max(valid_lows, key=lambda low: low["date"], default=None)
    from src.stock_analyzer import StockTrendAnalyzer
    legacy_trend = StockTrendAnalyzer().analyze(frame, stock_code)
    swing_lows = [low for low in valid_lows if low["type"] == "structural_swing_low"]
    hl = max(swing_lows, key=lambda low: low["date"], default=None)
    support_refs = [float(levels["ma20"])] + ([float(hl["price"])] if hl else [])
    touched_support = any(abs(float(low) - ref) <= 0.5 * atr
                          for low in frame["low"].iloc[-3:] for ref in support_refs)
    candidates = []
    if (pa["context"]["state"] == "bull_trend" and selected_low and touched_support
            and bull_bar and close > float(prev["high"])):
        candidates.append("trend_pullback")
    if bull_bar and strong_close and close > float(levels["prior_20d_high"]):
        candidates.append("initial_breakout")
    if (bull_bar and strong_close and float(last["low"]) < float(levels["prior_20d_low"])
            and close > float(levels["prior_20d_low"])):
        candidates.append("bottom_reversal")
    common = {
        "version": PLAN_VERSION, "stock_code": stock_code, "timeframe": "daily",
        "source": source, "adjustment": adjustment, "signal_date": signal_date.isoformat(),
        "snapshot_hash": hashlib.sha256(frame[["date", "open", "high", "low", "close"]].to_csv(index=False).encode()).hexdigest(),
        "valid_session": next_session.isoformat(), "signal_close": close, "atr14": atr,
        "context": pa["context"]["state"], "candidates": candidates,
        "legacy_structure": {
            "current_price": close, "support_levels": legacy_trend.support_levels,
            "resistance_levels": legacy_trend.resistance_levels,
            "invalidation_level": float(selected_low["price"]) - .5 * atr if selected_low else None,
        },
        "risk_budget_status": "not_provided", "shares": None,
        "account_sizing_formula": "股数 = 本笔总风险预算 × 本阶段风险份额 ÷ (拟成交价 − 硬止损)，再受持仓/集中度/流动性上限约束；预留费用与跳空风险。",
        "event_risk_status": "unverified",
        "event_warning": "本技术计划未完成事件排查；缺事件数据不代表没有财报/公告风险。",
        "cancellation_conditions": ["仅下一交易日有效，未触发即到期", "超出最高买价即放弃，不上调追价",
                                    "结构失效或关键行情不可验证时取消"],
        "add_requires_reassessment": True,
        "add_rule": "仅首仓盈利并出现新确认结构后重算新增风险；本笔累计风险上限25%→50%→100%，未验证完整加仓收益。底部反转转为多头且确认HL前上限12.5%。",
        "risk_fraction_basis": "本笔总风险预算，不是账户资金或计划股数比例",
    }
    if not candidates:
        return {**common, "status": "not_triggered", "reason": "尚未出现三类已定义日线信号",
                "new_position_allowed": False, "action": "watch", "validation_status": "not_evaluated"}
    setup = candidates[0]
    if setup == "bottom_reversal":
        key_low = float(last["low"])
        trigger = float(levels["prior_20d_low"])
    else:
        if not selected_low:
            return {**common, **unavailable_plan(stock_code, "confirmed_structure_low_missing"), "setup_type": setup}
        key_low = float(selected_low["price"])
        trigger = float(levels["prior_20d_high"]) if setup == "initial_breakout" else float(prev["high"])
    stop = key_low - 0.5 * atr
    if stop <= 0 or close <= key_low:
        return {**common, **unavailable_plan(stock_code, "invalid_structure_stop"), "setup_type": setup}
    # Actual, previously observed highs only. Exclude the signal bar itself.
    observed_highs = [item["price"] for item in pa["structure"]["confirmed_pivot_highs"]]
    observed_highs.append(levels["prior_20d_high"])
    resistances = sorted({float(x) for x in observed_highs if float(x) > close})
    target = resistances[0] if resistances else None
    ceiling = trigger + atr if setup == "initial_breakout" else close + 0.5 * atr
    if target is not None:
        ceiling = min(ceiling, (target + MIN_REWARD_RISK * stop) / (1 + MIN_REWARD_RISK))
    elif setup != "initial_breakout":
        return {**common, **unavailable_plan(stock_code, "upside_reference_missing"), "setup_type": setup}
    floor = max(trigger, key_low)
    enabled = setup in set(enabled_setups)
    status = "armed" if enabled else "validation_pending"
    reason = "信号已成立；仅下一交易日区间内可评估试仓" if enabled else "候选，尚未通过样本外验收或未启用"
    if ceiling < floor or close > ceiling:
        status, reason = "overpriced", "信号价格已超过允许买价，放弃本次，不追价"
    return {
        **common, "setup_type": setup, "setup_label": SETUP_LABELS[setup],
        "trigger_condition": TRIGGERS[setup], "trigger_price": trigger,
        "status": status, "reason": reason,
        "validation_status": "enabled_after_acceptance" if enabled else "not_approved",
        "new_position_allowed": False, "action": "watch", "entry_low": floor,
        "entry_high": ceiling, "max_entry_price": ceiling, "entry_reference": close,
        "structure_low": key_low, "hard_stop": stop, "risk_per_share": close - stop,
        "reward_risk_ratio": (target - close) / (close - stop) if target else None,
        "target_price": target, "exit_mode": "structure_trailing",
        "target_role": "reward_reference_not_automatic_exit" if target else "no_verified_overhead_resistance",
        "risk_budget_fraction": RISK_FRACTIONS[setup],
        "take_profit_tranches": [{"stage": f"{n}R", "price": close + n * (close - stop),
                                   "reduce_pct": None, "rule": "观察位，非自动止盈"} for n in (1, 2)],
    }


def price_plan_for_entry(plan: dict, price: float, *, session: date, enabled: bool,
                         campaign_risk_budget: float | None = None) -> dict:
    """Reprice every risk field at an executable quote/open, never reuse old R."""
    result = {**plan, "shares": None, "risk_budget_status": "not_provided"}
    if not plan.get("setup_type") or not plan.get("hard_stop"):
        return result
    if not math.isfinite(price) or price <= 0:
        return {**result, "status": "unavailable", "new_position_allowed": False, "action": "watch"}
    valid_session = date.fromisoformat(plan["valid_session"])
    if price <= plan["structure_low"]:
        status, reason = "invalidated", "结构失效，取消原计划"
    elif session > valid_session:
        status, reason = "expired", "已过计划有效交易日"
    elif session < valid_session:
        status, reason = "armed", "等待下一交易日，当前仅为条件计划"
    elif plan.get("status") == "overpriced":
        status, reason = "overpriced", "原信号超价已取消，不回溯恢复原计划"
    elif price > plan["max_entry_price"]:
        status, reason = "overpriced", "超过最高买价，放弃本次"
    elif price < plan["entry_low"]:
        status, reason = "not_triggered", "价格未达到入场下限"
    elif not enabled:
        status, reason = "validation_pending", "候选，未通过验收或未启用"
    else:
        status, reason = "ready", "技术条件可试仓；预算未提供时不得编造股数"
    risk = price - plan["hard_stop"]
    allowed = status == "ready"
    result.update(status=status, reason=reason, new_position_allowed=allowed,
                  action="buy" if allowed else "avoid" if status == "invalidated" else "watch",
                  entry_reference=price, risk_per_share=risk if risk > 0 else None,
                  risk_distance_pct=risk / price * 100 if risk > 0 else None,
                  reward_risk_ratio=(plan["target_price"] - price) / risk
                  if plan.get("target_price") and risk > 0 else None,
                  take_profit_tranches=[{"stage": f"{n}R", "price": price + n * risk,
                                         "reduce_pct": None, "rule": "观察位，非自动止盈"} for n in (1, 2)] if risk > 0 else [])
    if allowed and campaign_risk_budget is not None and plan.get("risk_budget_fraction"):
        if not math.isfinite(campaign_risk_budget) or campaign_risk_budget <= 0:
            raise ValueError("invalid_campaign_risk_budget")
        result["shares"] = math.floor(campaign_risk_budget * plan["risk_budget_fraction"] / risk)
        result["risk_budget_status"] = "provided_before_fees_and_portfolio_caps"
    return result


def load_live_swing_plan(stock_code: str, *, enabled_setups: Iterable[str]) -> dict:
    """Fresh provider history only; fail closed on calendar/adjustment uncertainty."""
    from data_provider.base import normalize_stock_code
    from src.core.trading_calendar import (
        build_market_phase_context, get_market_for_stock, get_next_trading_date, get_trading_dates,
    )
    from src.services.history_loader import load_history_df
    from src.agent.tools.data_tools import _handle_get_realtime_quote

    code = normalize_stock_code(stock_code)
    phase = build_market_phase_context(market=get_market_for_stock(code))
    if phase.warnings or phase.phase.value == "unknown":
        return unavailable_plan(code, "exchange_calendar_unverified")
    expected = phase.effective_daily_bar_date
    df, source = load_history_df(code, days=365, target_date=expected, force_refresh=True)
    if df is None or df.empty or "date" not in df:
        return unavailable_plan(code, "fresh_daily_history_unavailable")
    adjustment = str(df.attrs.get("price_adjustment") or "unknown")
    completed = df.loc[pd.to_datetime(df["date"]).dt.date <= expected].copy()
    if completed.empty or pd.to_datetime(completed["date"]).dt.date.max() != expected:
        return unavailable_plan(code, "completed_daily_history_stale")
    sessions = get_trading_dates(phase.market, pd.to_datetime(completed["date"]).dt.date.min(), expected)
    if sessions is None or set(sessions.date) != set(pd.to_datetime(completed["date"]).dt.date):
        return unavailable_plan(code, "daily_calendar_gaps")
    next_session = get_next_trading_date(phase.market, expected)
    if next_session is None:
        return unavailable_plan(code, "next_session_unknown")
    plan = build_swing_trade_plan(completed, stock_code=code, source=source, adjustment=adjustment,
                                 next_session=next_session, enabled_setups=enabled_setups)
    plan["fetched_at"] = datetime.now(timezone.utc).isoformat()
    plan["market_phase"] = phase.phase.value
    plan["market_session_date"] = phase.session_date.isoformat()
    from src.core import trading_calendar
    cal = trading_calendar.xcals.get_calendar(trading_calendar.MARKET_EXCHANGE[phase.market])
    plan["valid_until"] = cal.session_close(pd.Timestamp(next_session)).isoformat()
    # A completed signal is a next-session condition, not an immediate market order.
    if phase.session_date == next_session and phase.is_market_open_now:
        quote = _handle_get_realtime_quote(code)
        if (not quote.get("error") and normalize_stock_code(str(quote.get("code", ""))) == code
                and quote.get("provider_timestamp")):
            stamp = pd.to_datetime(quote["provider_timestamp"], errors="coerce", utc=True)
            age = (datetime.now(timezone.utc) - stamp.to_pydatetime()).total_seconds() if not pd.isna(stamp) else math.inf
            if 0 <= age <= 300:
                if plan.get("hard_stop"):
                    plan = price_plan_for_entry(plan, float(quote["price"]), session=next_session,
                                                enabled=plan.get("setup_type") in set(enabled_setups))
                plan["quote_timestamp"] = quote["provider_timestamp"]
                plan["verified_quote_price"] = float(quote["price"])
                return plan
        plan.update(quote_status="unverified", quote_warning="缺少可验证时间戳的当前报价，仅展示条件计划",
                    new_position_allowed=False, action="watch")
        if plan.get("status") in {"armed", "ready"}:
            plan.update(status="quote_unverified", reason=plan["quote_warning"])
    return plan
