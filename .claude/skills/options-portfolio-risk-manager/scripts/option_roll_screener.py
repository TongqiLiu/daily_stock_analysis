#!/usr/bin/env python3
"""Screen roll candidates for an existing short option."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

import yfinance as yf

from common import (
    display_symbol,
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
class RollCandidate:
    symbol: str
    side: str
    current_expiry: str
    current_strike: float
    new_expiry: str
    new_strike: float
    dte: int
    close_cost: float
    open_credit: float
    net_credit: float
    new_delta: float | None
    new_prob_itm: float | None
    new_breakeven_or_exit: float
    bid: float
    ask: float
    open_interest: int
    volume: int
    spread_pct: float | None
    comment: str


def option_type_from_side(side: str) -> str:
    if side not in {"short_put", "short_call"}:
        raise ValueError("side must be short_put or short_call")
    return "put" if side == "short_put" else "call"


def screen(args: argparse.Namespace) -> dict[str, Any]:
    symbol = yahoo_symbol(args.symbol)
    as_of = parse_date(args.as_of) if args.as_of else date.today()
    option_type = option_type_from_side(args.side)
    policy = load_config(args.policy) if args.policy else {}
    policy_thresholds = policy.get("thresholds") or {}
    target_dte_min = (
        args.target_dte_min
        if args.target_dte_min is not None
        else safe_int(policy_thresholds.get("roll_dte_min"), 21)
    )
    target_dte_max = (
        args.target_dte_max
        if args.target_dte_max is not None
        else safe_int(policy_thresholds.get("roll_dte_max"), 60)
    )
    max_delta = (
        args.max_delta
        if args.max_delta is not None
        else safe_float(policy_thresholds.get("conservative_short_put_delta"), 0.18)
        or 0.18
    )
    min_open_interest = (
        args.min_open_interest
        if args.min_open_interest is not None
        else safe_int(policy_thresholds.get("min_open_interest"), 100)
    )
    max_spread_pct = (
        args.max_spread_pct
        if args.max_spread_pct is not None
        else safe_float(policy_thresholds.get("max_bid_ask_spread_pct"), 25.0)
        or 25.0
    )
    spot = args.spot or fetch_spot(symbol)
    if not spot:
        raise ValueError(f"Unable to fetch spot for {symbol}")

    current = find_option_row(symbol, args.expiry, args.strike, option_type)
    if current is None:
        raise ValueError("Current option was not found in the option chain")
    close_cost = safe_float(current.get("ask"), 0.0) or safe_float(
        current.get("lastPrice"), 0.0
    )
    if not close_cost:
        raise ValueError("Current option has no usable ask/last price")

    ticker = yf.Ticker(symbol)
    candidates: list[RollCandidate] = []
    for expiry in list(ticker.options or []):
        exp_date = parse_date(expiry)
        dte = (exp_date - as_of).days
        if dte < target_dte_min or dte > target_dte_max:
            continue
        chain = ticker.option_chain(expiry)
        table = chain.puts if option_type == "put" else chain.calls
        for _, row in table.iterrows():
            strike = safe_float(row.get("strike"))
            bid = safe_float(row.get("bid"), 0.0) or 0.0
            ask = safe_float(row.get("ask"), 0.0) or 0.0
            iv = safe_float(row.get("impliedVolatility"), 0.0) or 0.0
            if strike is None or bid <= 0 or ask <= 0:
                continue
            if option_type == "put" and strike > args.strike:
                continue
            if option_type == "call" and strike < args.strike:
                continue
            math = option_delta_prob(
                spot=spot,
                strike=strike,
                dte=max(dte, 1),
                rate=args.risk_free_rate,
                iv=iv,
                option_type=option_type,
            )
            if math.delta is None:
                continue
            abs_delta = abs(math.delta)
            if abs_delta > max_delta:
                continue
            mid = (bid + ask) / 2.0
            spread_pct = (ask - bid) / mid * 100.0 if mid else None
            oi = safe_int(row.get("openInterest"))
            vol = safe_int(row.get("volume"))
            if oi < min_open_interest:
                continue
            if spread_pct is not None and spread_pct > max_spread_pct:
                continue
            net_credit = bid - close_cost
            if option_type == "put":
                new_be = strike - bid
                comment = "lower strike" if strike < args.strike else "same strike"
            else:
                new_be = strike + bid
                comment = "higher strike" if strike > args.strike else "same strike"
            if net_credit < 0:
                comment += ", debit roll"
            else:
                comment += ", credit roll"
            candidates.append(
                RollCandidate(
                    symbol=display_symbol(symbol),
                    side=args.side,
                    current_expiry=args.expiry,
                    current_strike=float(args.strike),
                    new_expiry=expiry,
                    new_strike=float(strike),
                    dte=dte,
                    close_cost=float(close_cost),
                    open_credit=float(bid),
                    net_credit=float(net_credit),
                    new_delta=math.delta,
                    new_prob_itm=math.prob_itm,
                    new_breakeven_or_exit=float(new_be),
                    bid=float(bid),
                    ask=float(ask),
                    open_interest=oi,
                    volume=vol,
                    spread_pct=spread_pct,
                    comment=comment,
                )
            )
    candidates.sort(
        key=lambda item: (
            item.net_credit >= 0,
            -abs(item.new_delta or 0),
            item.net_credit,
        ),
        reverse=True,
    )
    return {
        "symbol": display_symbol(symbol),
        "spot": spot,
        "as_of": as_of.isoformat(),
        "current_close_cost": close_cost,
        "filters": {
            "target_dte_min": target_dte_min,
            "target_dte_max": target_dte_max,
            "max_delta": max_delta,
            "min_open_interest": min_open_interest,
            "max_spread_pct": max_spread_pct,
        },
        "candidates": [asdict(item) for item in candidates[: args.limit]],
    }


def markdown(payload: dict[str, Any]) -> str:
    lines = [
        "## Roll Screener",
        f"- Symbol: {payload['symbol']}",
        f"- Spot: {fmt_money(payload['spot'])}",
        f"- Current close cost: {fmt_money(payload['current_close_cost'])}",
        "- Filters: DTE {min_dte}-{max_dte}, max delta {max_delta}, min OI {min_oi}, max spread {max_spread}".format(
            min_dte=payload["filters"]["target_dte_min"],
            max_dte=payload["filters"]["target_dte_max"],
            max_delta=fmt_number(payload["filters"]["max_delta"], 2),
            min_oi=payload["filters"]["min_open_interest"],
            max_spread=fmt_pct(payload["filters"]["max_spread_pct"] / 100.0),
        ),
        "",
        "| New Exp | Strike | DTE | Close Cost | Open Credit | Net Credit | Delta | Prob ITM | New BE/Exit | OI | Spread | Comment |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for item in payload["candidates"]:
        lines.append(
            "| {exp} | {strike:.2f} | {dte} | {close} | {credit} | {net} | "
            "{delta} | {prob} | {be} | {oi} | {spread} | {comment} |".format(
                exp=item["new_expiry"],
                strike=item["new_strike"],
                dte=item["dte"],
                close=fmt_money(item["close_cost"]),
                credit=fmt_money(item["open_credit"]),
                net=fmt_money(item["net_credit"]),
                delta=fmt_number(item["new_delta"], 2),
                prob=fmt_pct(item["new_prob_itm"]),
                be=fmt_money(item["new_breakeven_or_exit"]),
                oi=item["open_interest"],
                spread=fmt_pct((item["spread_pct"] or 0) / 100.0),
                comment=item["comment"],
            )
        )
    if not payload["candidates"]:
        lines.append("| n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | no candidate passed filters |")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--side", choices=["short_put", "short_call"], required=True)
    parser.add_argument("--expiry", required=True)
    parser.add_argument("--strike", type=float, required=True)
    parser.add_argument("--policy")
    parser.add_argument("--as-of")
    parser.add_argument("--spot", type=float)
    parser.add_argument("--target-dte-min", type=int)
    parser.add_argument("--target-dte-max", type=int)
    parser.add_argument("--max-delta", type=float)
    parser.add_argument("--min-open-interest", type=int)
    parser.add_argument("--max-spread-pct", type=float)
    parser.add_argument("--risk-free-rate", type=float, default=0.04)
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown")
    args = parser.parse_args()
    payload = screen(args)
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(markdown(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
