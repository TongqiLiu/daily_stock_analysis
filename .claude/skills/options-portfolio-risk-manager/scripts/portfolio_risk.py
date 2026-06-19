#!/usr/bin/env python3
"""Portfolio-level risk report for stock, ETF, leveraged ETF, and options positions."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

from common import (
    LEVERAGED_ETFS,
    display_symbol,
    fetch_events,
    fetch_history,
    fetch_market_cap,
    fetch_spot,
    find_option_row,
    fmt_money,
    fmt_number,
    fmt_pct,
    load_config,
    option_delta_prob,
    parse_date,
    safe_float,
    safe_int,
    yahoo_symbol,
)


@dataclass
class PositionRisk:
    symbol: str
    position_type: str
    quantity: float
    spot: float | None
    mark: float | None
    delta_shares: float | None
    dte: int | None
    strike: float | None
    expiry: str | None
    breakeven: float | None
    assignment_cash: float
    prob_itm: float | None
    prob_touch: float | None
    iv: float | None
    open_interest: int | None
    spread_pct: float | None
    risk_level: str
    action: str
    notes: list[str]
    underlying: str | None = None
    max_loss: float | None = None
    event_flags: list[str] = field(default_factory=list)


@dataclass
class UnderlyingExposure:
    underlying: str
    spot: float | None
    stock_shares: float = 0.0
    option_delta_shares: float = 0.0
    leveraged_delta_shares: float = 0.0
    net_delta_shares: float = 0.0
    assignment_cash: float = 0.0
    defined_risk_max_loss: float = 0.0
    call_away_shares: float = 0.0
    risk_level: str = "low"
    notes: list[str] = field(default_factory=list)


def thresholds(policy: dict[str, Any]) -> dict[str, Any]:
    defaults = {
        "max_weekly_dte": 14,
        "conservative_short_put_delta": 0.18,
        "warning_short_put_delta": 0.25,
        "danger_short_put_delta": 0.35,
        "min_open_interest": 100,
        "max_bid_ask_spread_pct": 20,
        "min_market_cap_for_assignment": 10_000_000_000,
        "high_hv_warning": 0.80,
        "event_risk_dte": 14,
        "long_option_exit_dte": 14,
        "long_option_urgent_dte": 7,
    }
    defaults.update(policy.get("thresholds") or {})
    return defaults


def watchlist_item(policy: dict[str, Any], symbol: str) -> dict[str, Any]:
    watchlist = policy.get("watchlist") or {}
    for key in (display_symbol(symbol), yahoo_symbol(symbol), str(symbol).upper()):
        item = watchlist.get(key)
        if isinstance(item, dict):
            return item
    return {}


def watchlist_quality(policy: dict[str, Any], symbol: str) -> str | None:
    return watchlist_item(policy, symbol).get("assignment_quality")


def watchlist_max_shares(policy: dict[str, Any], symbol: str) -> float | None:
    return safe_float(watchlist_item(policy, symbol).get("max_shares"))


def trend_notes(
    symbol: str,
    spot: float | None,
    history: dict[str, Any],
    high_hv_warning: float = 0.80,
) -> tuple[int, list[str]]:
    score = 0
    notes: list[str] = []
    if not spot:
        return score, ["missing spot"]
    ma50 = safe_float(history.get("ma50"))
    ma200 = safe_float(history.get("ma200"))
    ret20 = safe_float(history.get("ret_20d_pct"))
    hv20 = safe_float(history.get("hv20"))
    if ma50 and spot >= ma50:
        score += 1
    elif ma50:
        score -= 1
        notes.append("below 50D")
    if ma200 and spot >= ma200:
        score += 1
    elif ma200:
        score -= 1
        notes.append("below 200D")
    if ret20 is not None and ret20 < -10:
        score -= 1
        notes.append("20D downtrend")
    if hv20 is not None and hv20 > high_hv_warning:
        score -= 1
        notes.append("HV20 above policy threshold")
    return score, notes


def risk_level(score: int, critical: bool = False) -> str:
    if critical:
        return "high"
    if score <= -3:
        return "high"
    if score <= -1:
        return "medium"
    return "low"


def raise_level(current: str, target: str) -> str:
    order = {"unknown": -1, "low": 0, "medium": 1, "high": 2}
    return target if order.get(target, 0) > order.get(current, 0) else current


def is_between(value: date | None, start: date, end: date) -> bool:
    return value is not None and start <= value <= end


def option_event_flags(symbol: str, as_of: date, expiry: str) -> list[str]:
    expiry_date = parse_date(expiry)
    events = fetch_events(symbol)
    flags: list[str] = []
    earnings = events.get("earnings_date")
    ex_dividend = events.get("ex_dividend_date")
    if is_between(earnings, as_of, expiry_date):
        flags.append(f"earnings {earnings.isoformat()}")
    if is_between(ex_dividend, as_of, expiry_date):
        flags.append(f"ex-div {ex_dividend.isoformat()}")
    return flags


def apply_event_risk(
    *,
    notes: list[str],
    event_flags: list[str],
    dte: int,
    thresholds_: dict[str, Any],
    is_short: bool,
    option_type: str,
) -> tuple[int, bool]:
    if not event_flags:
        return 0, False
    notes.extend(f"crosses {flag}" for flag in event_flags)
    near_event = dte <= safe_int(thresholds_.get("event_risk_dte"), 14)
    if not is_short or not near_event:
        return 0, False
    if any(flag.startswith("earnings ") for flag in event_flags):
        notes.append("near-DTE short option crosses earnings")
        return -2, True
    if option_type == "call" and any(flag.startswith("ex-div ") for flag in event_flags):
        notes.append("near-DTE short call crosses ex-dividend")
        return -1, True
    return 0, False


def apply_watchlist_limits(risks: list[PositionRisk], policy: dict[str, Any]) -> None:
    short_put_shares: dict[str, float] = {}
    for item in risks:
        if item.position_type == "short_put" and item.strike:
            short_put_shares[item.symbol] = short_put_shares.get(item.symbol, 0.0) + (
                item.assignment_cash / item.strike
            )

    for symbol, shares in short_put_shares.items():
        max_shares = watchlist_max_shares(policy, symbol)
        if max_shares is None or max_shares <= 0 or shares <= max_shares:
            continue
        severity = "high" if shares >= max_shares * 2 else "medium"
        note = f"potential assigned shares {shares:.0f} above max_shares {max_shares:.0f}"
        for item in risks:
            if item.symbol != symbol or item.position_type != "short_put":
                continue
            if note not in item.notes:
                item.notes.append(note)
            item.risk_level = raise_level(item.risk_level, severity)
            if severity == "high":
                item.action = "reduce contracts or close/roll; exceeds max assignment size"
            elif item.action == "monitor":
                item.action = "reduce size or prepare roll; exceeds preferred assignment size"


def analyze_option(
    position: dict[str, Any],
    policy: dict[str, Any],
    as_of: date,
    rate: float,
) -> PositionRisk:
    symbol = yahoo_symbol(str(position["symbol"]))
    display = display_symbol(symbol)
    pos_type = str(position["type"]).lower()
    option_type = "put" if "put" in pos_type else "call"
    is_short = pos_type.startswith("short") or pos_type == "covered_call"
    contracts = safe_int(position.get("contracts"), 1)
    strike = float(position["strike"])
    expiry = str(position["expiry"])
    premium = safe_float(position.get("premium"), 0.0) or 0.0
    spot = fetch_spot(symbol)
    history = fetch_history(symbol)
    market_cap = fetch_market_cap(symbol)
    t = thresholds(policy)
    dte = max((parse_date(expiry) - as_of).days, 0)
    event_flags = option_event_flags(symbol, as_of, expiry)
    row = find_option_row(symbol, expiry, strike, option_type)
    bid = ask = mid = iv = None
    oi = None
    spread_pct = None
    if row is not None:
        bid = safe_float(row.get("bid"), 0.0) or 0.0
        ask = safe_float(row.get("ask"), 0.0) or 0.0
        iv = safe_float(row.get("impliedVolatility"))
        oi = safe_int(row.get("openInterest"))
        if bid and ask:
            mid = (bid + ask) / 2.0
            spread_pct = (ask - bid) / mid * 100.0 if mid else None
        else:
            mid = safe_float(row.get("lastPrice"))
    math = option_delta_prob(
        spot=spot or 0.0,
        strike=strike,
        dte=max(dte, 1),
        rate=rate,
        iv=iv or safe_float(history.get("hv60"), 0.0) or 0.0,
        option_type=option_type,
    )
    delta = math.delta
    signed_delta = None
    if delta is not None:
        signed_delta = delta * contracts * 100.0
        if is_short:
            signed_delta *= -1.0
    assignment_cash = 0.0
    breakeven = None
    notes: list[str] = []
    critical = False
    score = 0
    trend_score, trend_flags = trend_notes(
        symbol,
        spot,
        history,
        safe_float(t.get("high_hv_warning"), 0.80) or 0.80,
    )
    score += trend_score
    notes.extend(trend_flags)
    quality = watchlist_quality(policy, symbol)
    if quality and is_short and option_type == "put":
        notes.append(f"assignment quality: {quality}")
    elif quality:
        notes.append(f"underlying quality: {quality}")
    elif market_cap is not None and market_cap < t["min_market_cap_for_assignment"]:
        score -= 1
        notes.append("market cap below assignment threshold")
    event_score, event_critical = apply_event_risk(
        notes=notes,
        event_flags=event_flags,
        dte=dte,
        thresholds_=t,
        is_short=is_short,
        option_type=option_type,
    )
    score += event_score
    critical = critical or event_critical
    if not is_short:
        breakeven = strike + premium if option_type == "call" else strike - premium
        is_otm = False
        if spot is not None:
            is_otm = spot < strike if option_type == "call" else spot > strike
        if is_otm:
            score -= 1
            notes.append("long option is OTM")
        if dte <= safe_int(t.get("long_option_exit_dte"), 14):
            score -= 1
            notes.append("inside long-option exit window")
        if is_otm and dte <= safe_int(t.get("long_option_urgent_dte"), 7):
            score -= 1
            critical = True
            notes.append("urgent theta risk")
        if premium and mid is not None:
            notes.append(f"option mark P/L {(mid / premium - 1.0) * 100:.1f}%")
        level = risk_level(score, critical=critical)
        if level == "high":
            action = "decide exit, roll, or salvage; time decay risk is urgent"
        elif level == "medium":
            action = "monitor theta; set exit or roll trigger"
        else:
            action = "monitor optionality"
        return PositionRisk(
            symbol=display,
            position_type=pos_type,
            quantity=float(contracts),
            spot=spot,
            mark=mid,
            delta_shares=signed_delta,
            dte=dte,
            strike=strike,
            expiry=expiry,
            breakeven=breakeven,
            assignment_cash=0.0,
            prob_itm=math.prob_itm,
            prob_touch=math.prob_touch,
            iv=iv,
            open_interest=oi,
            spread_pct=spread_pct,
            risk_level=level,
            action=action,
            notes=list(dict.fromkeys(notes)),
            underlying=display,
            event_flags=event_flags,
        )
    if option_type == "put" and is_short:
        assignment_cash = strike * 100.0 * contracts
        breakeven = strike - premium
        if abs(delta or 0) > t["conservative_short_put_delta"]:
            score -= 1
            notes.append("above conservative short-put delta")
        if abs(delta or 0) > t["warning_short_put_delta"]:
            score -= 1
            notes.append("above warning short-put delta")
        if abs(delta or 0) > t["danger_short_put_delta"]:
            score -= 1
            critical = True
            notes.append("above danger short-put delta")
        if dte <= t["max_weekly_dte"] and abs(delta or 0) > t["conservative_short_put_delta"]:
            score -= 1
            critical = True
            notes.append("weekly gamma risk")
        low20 = safe_float(history.get("low20"))
        if low20 and breakeven > low20:
            score -= 1
            notes.append("breakeven above 20D low")
    elif option_type == "call" and is_short:
        breakeven = strike + premium
        call_min = safe_float(t.get("covered_call_min_delta"), 0.12) or 0.12
        call_max = safe_float(t.get("covered_call_max_delta"), 0.30) or 0.30
        if abs(delta or 0) > call_max:
            score -= 1
            notes.append("call delta above covered-call max")
        elif delta is not None and abs(delta) < call_min:
            notes.append("low call delta; confirm premium justifies cap")
    if oi is not None and oi < t["min_open_interest"]:
        score -= 1
        notes.append("thin open interest")
    if spread_pct is not None and spread_pct > t["max_bid_ask_spread_pct"]:
        score -= 1
        notes.append("wide bid/ask")
    level = risk_level(score, critical=critical)
    if level == "high" and option_type == "put" and is_short:
        action = "roll down/out or close if assignment is not acceptable"
    elif level == "medium" and option_type == "put" and is_short:
        action = "monitor; prepare lower-delta roll"
    elif option_type == "call" and is_short:
        action = "keep only if willing to sell shares at strike"
    else:
        action = "monitor"
    return PositionRisk(
        symbol=display,
        position_type=pos_type,
        quantity=float(contracts),
        spot=spot,
        mark=mid,
        delta_shares=signed_delta,
        dte=dte,
        strike=strike,
        expiry=expiry,
        breakeven=breakeven,
        assignment_cash=assignment_cash,
        prob_itm=math.prob_itm,
        prob_touch=math.prob_touch,
        iv=iv,
        open_interest=oi,
        spread_pct=spread_pct,
        risk_level=level,
        action=action,
        notes=list(dict.fromkeys(notes)),
        underlying=display,
        event_flags=event_flags,
    )


def row_metrics(row: Any | None) -> tuple[float | None, int | None, float | None]:
    if row is None:
        return None, None, None
    bid = safe_float(row.get("bid"), 0.0) or 0.0
    ask = safe_float(row.get("ask"), 0.0) or 0.0
    if bid and ask:
        mid = (bid + ask) / 2.0
        spread_pct = (ask - bid) / mid * 100.0 if mid else None
    else:
        mid = safe_float(row.get("lastPrice"))
        spread_pct = None
    return mid, safe_int(row.get("openInterest")), spread_pct


def row_iv(row: Any | None, fallback: float | None) -> float:
    if row is None:
        return fallback or 0.0
    return safe_float(row.get("impliedVolatility"), fallback) or fallback or 0.0


def analyze_spread(
    position: dict[str, Any],
    policy: dict[str, Any],
    as_of: date,
    rate: float,
) -> PositionRisk:
    symbol = yahoo_symbol(str(position["symbol"]))
    display = display_symbol(symbol)
    pos_type = str(position["type"]).lower()
    option_type = "put" if "put" in pos_type else "call"
    contracts = safe_int(position.get("contracts"), 1)
    short_strike = safe_float(position.get("short_strike") or position.get("strike"))
    long_strike = safe_float(position.get("long_strike"))
    if short_strike is None or long_strike is None:
        raise ValueError(f"{pos_type} requires short_strike and long_strike")
    expiry = str(position["expiry"])
    net_credit = safe_float(
        position.get("net_credit")
        if position.get("net_credit") is not None
        else position.get("credit", position.get("premium")),
        0.0,
    ) or 0.0
    spot = fetch_spot(symbol)
    history = fetch_history(symbol)
    t = thresholds(policy)
    dte = max((parse_date(expiry) - as_of).days, 0)
    event_flags = option_event_flags(symbol, as_of, expiry)
    short_row = find_option_row(symbol, expiry, short_strike, option_type)
    long_row = find_option_row(symbol, expiry, long_strike, option_type)
    short_mid, short_oi, short_spread = row_metrics(short_row)
    long_mid, long_oi, long_spread = row_metrics(long_row)
    fallback_iv = safe_float(history.get("hv60"), 0.0) or 0.0
    short_math = option_delta_prob(
        spot=spot or 0.0,
        strike=short_strike,
        dte=max(dte, 1),
        rate=rate,
        iv=row_iv(short_row, fallback_iv),
        option_type=option_type,
    )
    long_math = option_delta_prob(
        spot=spot or 0.0,
        strike=long_strike,
        dte=max(dte, 1),
        rate=rate,
        iv=row_iv(long_row, fallback_iv),
        option_type=option_type,
    )
    delta_shares = None
    if short_math.delta is not None and long_math.delta is not None:
        delta_shares = (-short_math.delta + long_math.delta) * contracts * 100.0
    width = abs(short_strike - long_strike)
    max_loss = max((width - net_credit) * contracts * 100.0, 0.0)
    max_profit = max(net_credit * contracts * 100.0, 0.0)
    breakeven = short_strike - net_credit if option_type == "put" else short_strike + net_credit
    mark = None
    if short_mid is not None and long_mid is not None:
        mark = short_mid - long_mid
    spread_pct_candidates = [value for value in (short_spread, long_spread) if value is not None]
    spread_pct = max(spread_pct_candidates) if spread_pct_candidates else None

    notes: list[str] = [
        f"defined-risk {option_type} spread",
        f"max loss {fmt_money(max_loss)}",
        f"max profit {fmt_money(max_profit)}",
        "assignment cash not used for defined-risk spread",
    ]
    score = 0
    critical = False
    trend_score, trend_flags = trend_notes(
        symbol,
        spot,
        history,
        safe_float(t.get("high_hv_warning"), 0.80) or 0.80,
    )
    score += trend_score
    notes.extend(trend_flags)
    event_score, event_critical = apply_event_risk(
        notes=notes,
        event_flags=event_flags,
        dte=dte,
        thresholds_=t,
        is_short=True,
        option_type=option_type,
    )
    score += event_score
    critical = critical or event_critical
    short_abs_delta = abs(short_math.delta or 0.0)
    if short_abs_delta > t["warning_short_put_delta"]:
        score -= 1
        notes.append("short leg above warning delta")
    if short_abs_delta > t["danger_short_put_delta"]:
        score -= 1
        critical = True
        notes.append("short leg above danger delta")
    if dte <= t["max_weekly_dte"] and short_abs_delta > t["conservative_short_put_delta"]:
        score -= 1
        notes.append("weekly gamma risk on short leg")
    if spot is not None:
        if option_type == "put" and spot < short_strike:
            critical = True
            notes.append("short put leg is ITM")
        if option_type == "call" and spot > short_strike:
            critical = True
            notes.append("short call leg is ITM")
    min_oi = min(value for value in (short_oi, long_oi) if value is not None) if (
        short_oi is not None or long_oi is not None
    ) else None
    if min_oi is not None and min_oi < t["min_open_interest"]:
        score -= 1
        notes.append("thin open interest on a spread leg")
    if spread_pct is not None and spread_pct > t["max_bid_ask_spread_pct"]:
        score -= 1
        notes.append("wide bid/ask on a spread leg")
    level = risk_level(score, critical=critical)
    if level == "high":
        action = "manage defined risk; close/roll if short leg remains threatened"
    elif level == "medium":
        action = "monitor short leg and max-loss threshold"
    else:
        action = "monitor defined-risk spread"
    return PositionRisk(
        symbol=display,
        position_type=pos_type,
        quantity=float(contracts),
        spot=spot,
        mark=mark,
        delta_shares=delta_shares,
        dte=dte,
        strike=short_strike,
        expiry=expiry,
        breakeven=breakeven,
        assignment_cash=0.0,
        prob_itm=short_math.prob_itm,
        prob_touch=short_math.prob_touch,
        iv=row_iv(short_row, fallback_iv) or None,
        open_interest=min_oi,
        spread_pct=spread_pct,
        risk_level=level,
        action=action,
        notes=list(dict.fromkeys(notes)),
        underlying=display,
        max_loss=max_loss,
        event_flags=event_flags,
    )


def analyze_stock(position: dict[str, Any], policy: dict[str, Any]) -> PositionRisk:
    symbol = yahoo_symbol(str(position["symbol"]))
    display = display_symbol(symbol)
    pos_type = str(position["type"]).lower()
    shares = safe_float(position.get("shares"), 0.0) or 0.0
    avg_cost = safe_float(position.get("avg_cost"))
    spot = fetch_spot(symbol)
    history = fetch_history(symbol)
    t = thresholds(policy)
    score, notes = trend_notes(
        symbol,
        spot,
        history,
        safe_float(t.get("high_hv_warning"), 0.80) or 0.80,
    )
    leverage = safe_float(position.get("leverage"))
    underlying = position.get("underlying")
    if pos_type == "leveraged_etf" or symbol in LEVERAGED_ETFS:
        if leverage is None:
            underlying_default, leverage_default = LEVERAGED_ETFS.get(symbol, (None, 1.0))
            leverage = leverage_default
            underlying = underlying or underlying_default
        score -= 1
        notes.append(f"daily reset leveraged ETF {leverage:g}x")
        if underlying:
            notes.append(f"underlying: {underlying}")
    else:
        leverage = 1.0
    mark = spot * shares if spot is not None else None
    delta_shares = shares
    if pos_type == "leveraged_etf" or symbol in LEVERAGED_ETFS:
        underlying_spot = fetch_spot(str(underlying)) if underlying else None
        if spot is not None and underlying_spot:
            delta_shares = shares * spot * (leverage or 1.0) / underlying_spot
            notes.append(f"underlying-equivalent shares {delta_shares:.1f}")
        else:
            delta_shares = shares * (leverage or 1.0)
            notes.append("delta shares use ETF-share proxy; missing underlying spot")
    if avg_cost and spot:
        pnl_pct = spot / avg_cost - 1.0
        notes.append(f"position P/L {pnl_pct * 100:.1f}%")
    level = risk_level(score)
    action = "monitor"
    if pos_type == "leveraged_etf" and level != "low":
        action = "reduce or cap exposure if trend remains broken"
    return PositionRisk(
        symbol=display,
        position_type=pos_type,
        quantity=shares,
        spot=spot,
        mark=mark,
        delta_shares=delta_shares,
        dte=None,
        strike=None,
        expiry=None,
        breakeven=avg_cost,
        assignment_cash=0.0,
        prob_itm=None,
        prob_touch=None,
        iv=None,
        open_interest=None,
        spread_pct=None,
        risk_level=level,
        action=action,
        notes=list(dict.fromkeys(notes)),
        underlying=display_symbol(str(underlying)) if underlying and pos_type == "leveraged_etf" else display,
    )


def apply_covered_call_pairing(risks: list[PositionRisk]) -> None:
    available_shares: dict[str, float] = {}
    for item in risks:
        if item.position_type in {"stock", "etf"}:
            available_shares[item.symbol] = available_shares.get(item.symbol, 0.0) + item.quantity

    for item in risks:
        if item.position_type not in {"short_call", "covered_call"}:
            continue
        required = item.quantity * 100.0
        available = available_shares.get(item.symbol, 0.0)
        covered = min(available, required)
        if covered > 0:
            item.notes.append(f"covered-call pairing: {covered:.0f}/{required:.0f} shares covered")
            available_shares[item.symbol] = max(available - covered, 0.0)
        if covered < required:
            uncovered = required - covered
            item.notes.append(f"uncovered short-call shares {uncovered:.0f}")
            item.risk_level = "high"
            item.action = "close, buy shares, or convert to defined risk; naked call exposure"
            continue
        if item.prob_itm is not None and item.prob_itm >= 0.50:
            item.risk_level = raise_level(item.risk_level, "medium")
            item.action = "accept call-away or roll before strike if shares should be kept"
        elif item.action == "monitor":
            item.action = "covered call; keep only if willing to cap upside"
        item.notes = list(dict.fromkeys(item.notes))


def aggregate_underlyings(risks: list[PositionRisk]) -> list[UnderlyingExposure]:
    exposures: dict[str, UnderlyingExposure] = {}

    def get_row(symbol: str, spot: float | None) -> UnderlyingExposure:
        display = display_symbol(symbol)
        if display not in exposures:
            exposures[display] = UnderlyingExposure(
                underlying=display,
                spot=spot if spot is not None else fetch_spot(display),
            )
        elif exposures[display].spot is None and spot is not None:
            exposures[display].spot = spot
        return exposures[display]

    for item in risks:
        underlying = item.underlying or item.symbol
        row = get_row(underlying, item.spot if display_symbol(underlying) == item.symbol else None)
        row.risk_level = raise_level(row.risk_level, item.risk_level)
        if item.position_type in {"stock", "etf"}:
            row.stock_shares += item.quantity
        elif item.position_type == "leveraged_etf":
            row.leveraged_delta_shares += item.delta_shares or 0.0
            row.notes.append(f"{item.symbol} leveraged ETF exposure")
        else:
            row.option_delta_shares += item.delta_shares or 0.0
            row.assignment_cash += item.assignment_cash
            row.defined_risk_max_loss += item.max_loss or 0.0
            if item.position_type in {"short_call", "covered_call"}:
                row.call_away_shares += item.quantity * 100.0
        for flag in item.event_flags:
            row.notes.append(flag)
    for row in exposures.values():
        row.net_delta_shares = (
            row.stock_shares + row.option_delta_shares + row.leveraged_delta_shares
        )
        row.notes = list(dict.fromkeys(row.notes))
    return sorted(
        exposures.values(),
        key=lambda item: (
            {"high": 2, "medium": 1, "low": 0, "unknown": -1}.get(item.risk_level, 0),
            abs(item.net_delta_shares),
            item.assignment_cash + item.defined_risk_max_loss,
        ),
        reverse=True,
    )


def analyze_portfolio(args: argparse.Namespace) -> dict[str, Any]:
    positions_config = load_config(args.positions)
    policy = load_config(args.policy) if args.policy else {}
    as_of = parse_date(args.as_of) if args.as_of else date.today()
    risks: list[PositionRisk] = []
    for position in positions_config.get("positions", []):
        pos_type = str(position.get("type", "")).lower()
        if pos_type in {"short_put", "short_call", "long_put", "long_call", "covered_call"}:
            risks.append(analyze_option(position, policy, as_of, args.risk_free_rate))
        elif pos_type in {"short_put_spread", "short_call_spread"}:
            risks.append(analyze_spread(position, policy, as_of, args.risk_free_rate))
        elif pos_type in {"stock", "etf", "leveraged_etf"}:
            risks.append(analyze_stock(position, policy))
        else:
            risks.append(
                PositionRisk(
                    symbol=display_symbol(str(position.get("symbol", "UNKNOWN"))),
                    position_type=pos_type or "unknown",
                    quantity=0,
                    spot=None,
                    mark=None,
                    delta_shares=None,
                    dte=None,
                    strike=None,
                    expiry=None,
                    breakeven=None,
                    assignment_cash=0,
                    prob_itm=None,
                    prob_touch=None,
                    iv=None,
                    open_interest=None,
                    spread_pct=None,
                    risk_level="unknown",
                    action="unsupported position type",
                    notes=[],
                )
            )
    total_assignment_cash = sum(item.assignment_cash for item in risks)
    near_assignment_cash = sum(
        item.assignment_cash for item in risks if item.dte is not None and item.dte <= 14
    )
    apply_watchlist_limits(risks, policy)
    apply_covered_call_pairing(risks)
    underlyings = aggregate_underlyings(risks)
    high_risks = [item for item in risks if item.risk_level == "high"]
    return {
        "as_of": as_of.isoformat(),
        "total_assignment_cash": total_assignment_cash,
        "near_assignment_cash": near_assignment_cash,
        "positions": [asdict(item) for item in risks],
        "underlyings": [asdict(item) for item in underlyings],
        "top_actions": [asdict(item) for item in high_risks[:3]],
    }


def fmt_list(values: list[str] | None) -> str:
    return ", ".join(values or []) if values else "n/a"


def markdown(payload: dict[str, Any]) -> str:
    lines = [
        "## Portfolio Read",
        f"- As of: {payload['as_of']}",
        f"- Total assignment cash: {fmt_money(payload['total_assignment_cash'])}",
        f"- Assignment cash within 14 DTE: {fmt_money(payload['near_assignment_cash'])}",
    ]
    if payload["top_actions"]:
        first = payload["top_actions"][0]
        lines.append(
            f"- Top action: {first['symbol']} {first['position_type']} - {first['action']}"
        )
    else:
        lines.append("- Top action: no high-risk position flagged by current thresholds")
    lines.extend(
        [
            "",
            "## Position Table",
            "| Symbol | Position | Spot | Mark | Delta Shares | DTE | Strike | Breakeven | Assignment Cash | Max Loss | Prob ITM | Events | Risk | Action |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|",
        ]
    )
    for item in payload["positions"]:
        lines.append(
            "| {symbol} | {ptype} | {spot} | {mark} | {delta} | {dte} | {strike} | "
            "{be} | {cash} | {max_loss} | {prob} | {events} | {risk} | {action} |".format(
                symbol=item["symbol"],
                ptype=item["position_type"],
                spot=fmt_money(item["spot"]),
                mark=fmt_money(item["mark"]),
                delta=fmt_number(item["delta_shares"], 1),
                dte=item["dte"] if item["dte"] is not None else "n/a",
                strike=fmt_money(item["strike"]),
                be=fmt_money(item["breakeven"]),
                cash=fmt_money(item["assignment_cash"]),
                max_loss=fmt_money(item.get("max_loss")),
                prob=fmt_pct(item["prob_itm"]),
                events=fmt_list(item.get("event_flags")),
                risk=item["risk_level"],
                action=item["action"],
            )
        )
    if payload.get("underlyings"):
        lines.extend(
            [
                "",
                "## Underlying Exposure",
                "| Underlying | Spot | Stock Shares | Option Delta Sh | Leveraged Eq Sh | Net Delta Sh | Assignment Cash | Defined-Risk Max Loss | Call-Away Sh | Risk | Notes |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
            ]
        )
        for item in payload["underlyings"]:
            lines.append(
                "| {underlying} | {spot} | {stock} | {option_delta} | {leveraged_delta} | "
                "{net_delta} | {cash} | {max_loss} | {call_away} | {risk} | {notes} |".format(
                    underlying=item["underlying"],
                    spot=fmt_money(item["spot"]),
                    stock=fmt_number(item["stock_shares"], 1),
                    option_delta=fmt_number(item["option_delta_shares"], 1),
                    leveraged_delta=fmt_number(item["leveraged_delta_shares"], 1),
                    net_delta=fmt_number(item["net_delta_shares"], 1),
                    cash=fmt_money(item["assignment_cash"]),
                    max_loss=fmt_money(item["defined_risk_max_loss"]),
                    call_away=fmt_number(item["call_away_shares"], 1),
                    risk=item["risk_level"],
                    notes=fmt_list(item["notes"]),
                )
            )
    lines.extend(["", "## Follow-up Checks"])
    followups: list[str] = []
    for item in payload["positions"]:
        position_type = item["position_type"]
        if (
            position_type in {"short_put", "short_call", "covered_call"}
            and item["risk_level"] == "high"
            and item["expiry"]
            and item["strike"] is not None
        ):
            roll_type = "short_call" if position_type == "covered_call" else position_type
            followups.append(
                "Run roll screener for {symbol} {ptype} {expiry} {strike} before "
                "deciding to roll.".format(
                    symbol=item["symbol"],
                    ptype=roll_type,
                    expiry=item["expiry"],
                    strike=fmt_money(item["strike"]),
                )
            )
        if position_type in {"short_put_spread", "short_call_spread"} and item["risk_level"] == "high":
            followups.append(
                "Review defined-risk spread {symbol} {ptype} {expiry} short leg {strike}; "
                "compare close cost with max loss.".format(
                    symbol=item["symbol"],
                    ptype=position_type,
                    expiry=item["expiry"],
                    strike=fmt_money(item["strike"]),
                )
            )
        if position_type in {"long_put", "long_call"} and item["risk_level"] == "high":
            followups.append(
                f"Decide exit/roll for {item['symbol']} {position_type} {item['expiry']} before theta decay dominates."
            )
        if position_type == "leveraged_etf":
            followups.append(
                f"Run leveraged ETF decay for {item['symbol']} if the holding period is longer than a trade."
            )
    if followups:
        for check in dict.fromkeys(followups):
            lines.append(f"- {check}")
    else:
        lines.append("- No roll or decay follow-up required by current flags.")
    lines.append("")
    lines.append("## Notes")
    for item in payload["positions"]:
        notes = "; ".join(item["notes"]) if item["notes"] else "no major note"
        lines.append(f"- {item['symbol']} {item['position_type']}: {notes}.")
    lines.extend(
        [
            "",
            "## Disclaimer",
            "- For educational analysis only, not financial advice. Confirm prices, Greeks, liquidity, and corporate events in the broker platform before trading.",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--positions", required=True)
    parser.add_argument("--policy")
    parser.add_argument("--as-of")
    parser.add_argument("--risk-free-rate", type=float, default=0.04)
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown")
    args = parser.parse_args()
    payload = analyze_portfolio(args)
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(markdown(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
