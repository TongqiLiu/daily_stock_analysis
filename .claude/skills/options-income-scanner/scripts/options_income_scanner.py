#!/usr/bin/env python3
"""Options income scanner for covered calls and cash-secured puts.

The script intentionally uses lightweight Black-Scholes approximations and
broker-chain quotes as screening inputs. Confirm final quotes and Greeks in the
broker platform before trading.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, asdict
from datetime import date, datetime
from typing import Any, Iterable

try:
    import numpy as np
    import pandas as pd
    import yfinance as yf
except Exception as exc:  # pragma: no cover - environment guard
    print(
        "Missing dependency. Install yfinance, pandas, and numpy before running "
        f"this scanner. Original error: {exc}",
        file=sys.stderr,
    )
    sys.exit(2)


LEVERAGED_ETFS = {
    "SOXL",
    "SOXS",
    "TQQQ",
    "SQQQ",
    "UPRO",
    "SPXU",
    "YINN",
    "YANG",
    "LABU",
    "LABD",
    "FNGU",
    "FNGD",
    "TECL",
    "TECS",
}


def norm_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def yahoo_symbol(symbol: str) -> str:
    cleaned = symbol.strip().upper()
    if cleaned in {"BRK.B", "BRKB"}:
        return "BRK-B"
    if cleaned in {"BRK.A", "BRKA"}:
        return "BRK-A"
    return cleaned


def display_symbol(symbol: str) -> str:
    return symbol.replace("-", ".")


def parse_tickers(raw: str) -> list[str]:
    tickers: list[str] = []
    for part in raw.replace("\n", ",").replace(" ", ",").split(","):
        item = part.strip()
        if item:
            tickers.append(yahoo_symbol(item))
    return list(dict.fromkeys(tickers))


def safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return default
        return float(value)
    except Exception:
        return default


def option_delta_prob(
    *,
    spot: float,
    strike: float,
    dte: int,
    rate: float,
    iv: float,
    option_type: str,
) -> tuple[float | None, float | None]:
    if spot <= 0 or strike <= 0 or dte <= 0 or iv <= 0.01:
        return None, None
    t = dte / 365.0
    d1 = (math.log(spot / strike) + (rate + 0.5 * iv * iv) * t) / (iv * math.sqrt(t))
    d2 = d1 - iv * math.sqrt(t)
    if option_type == "call":
        return norm_cdf(d1), norm_cdf(d2)
    return norm_cdf(d1) - 1.0, norm_cdf(-d2)


def pct_change(series: pd.Series, periods: int) -> float | None:
    series = series.dropna()
    if len(series) <= periods:
        return None
    previous = safe_float(series.iloc[-1 - periods])
    latest = safe_float(series.iloc[-1])
    if not previous:
        return None
    return (latest / previous - 1.0) * 100.0


def analyze_history(ticker: yf.Ticker) -> dict[str, Any]:
    hist = ticker.history(period="1y", interval="1d", auto_adjust=False)
    if hist is None or hist.empty:
        return {}
    hist = hist.dropna(subset=["High", "Low", "Close"])
    close = hist["Close"]
    returns = np.log(close / close.shift(1)).dropna()
    out: dict[str, Any] = {
        "ret_1d_pct": pct_change(close, 1),
        "ret_5d_pct": pct_change(close, 5),
        "ret_20d_pct": pct_change(close, 20),
        "last_close": safe_float(close.iloc[-1]),
    }
    for window in (20, 60):
        if len(returns) >= window:
            out[f"hv{window}"] = float(returns.tail(window).std() * math.sqrt(252.0))
    tr = pd.concat(
        [
            hist["High"] - hist["Low"],
            (hist["High"] - hist["Close"].shift(1)).abs(),
            (hist["Low"] - hist["Close"].shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)
    if len(tr) >= 14:
        out["atr14"] = float(tr.rolling(14).mean().iloc[-1])
    for window in (20, 50, 200):
        if len(close) >= window:
            out[f"ma{window}"] = float(close.rolling(window).mean().iloc[-1])
    for window in (20, 60):
        if len(hist) >= window:
            out[f"low{window}"] = float(hist["Low"].tail(window).min())
            out[f"high{window}"] = float(hist["High"].tail(window).max())
    if "Volume" in hist and len(hist) >= 20:
        avg_vol = hist["Volume"].tail(20).mean()
        latest_vol = safe_float(hist["Volume"].iloc[-1])
        out["volume_ratio_20d"] = float(latest_vol / avg_vol) if avg_vol else None
    return out


def fetch_benchmark_returns() -> dict[str, float | None]:
    result: dict[str, float | None] = {}
    for symbol in ("SPY", "QQQ"):
        try:
            hist = yf.Ticker(symbol).history(period="10d", interval="1d")
            result[f"{symbol.lower()}_1d_pct"] = pct_change(hist["Close"], 1)
            result[f"{symbol.lower()}_5d_pct"] = pct_change(hist["Close"], 5)
        except Exception:
            result[f"{symbol.lower()}_1d_pct"] = None
            result[f"{symbol.lower()}_5d_pct"] = None
    return result


def earnings_dates(ticker: yf.Ticker) -> list[str]:
    try:
        calendar = ticker.calendar or {}
    except Exception:
        return []
    raw = calendar.get("Earnings Date") if isinstance(calendar, dict) else None
    values = raw if isinstance(raw, list) else [raw] if raw else []
    parsed: list[str] = []
    for item in values:
        if isinstance(item, datetime):
            parsed.append(item.date().isoformat())
        elif isinstance(item, date):
            parsed.append(item.isoformat())
        elif item:
            parsed.append(str(item))
    return parsed


def has_event_before_expiry(earnings: Iterable[str], as_of: date, expiry: date) -> bool:
    for item in earnings:
        parsed = pd.to_datetime(item, errors="coerce")
        if not pd.isna(parsed):
            event_date = parsed.date()
            if as_of <= event_date <= expiry:
                return True
    return False


@dataclass
class Candidate:
    ticker: str
    side: str
    expiration: str
    spot: float
    strike: float
    bid: float
    ask: float
    mid: float
    last: float | None
    delta: float | None
    prob_itm: float | None
    iv: float | None
    volume: int
    open_interest: int
    spread_pct: float | None
    premium_yield_pct: float
    annualized_yield_pct: float
    breakeven_or_exit: float
    buffer_or_called_return_pct: float
    score: float
    grade: str
    notes: list[str]


def grade_candidate(score: float, notes: list[str]) -> str:
    if any(note.startswith("avoid:") for note in notes):
        return "avoid"
    if score >= 5.0:
        return "candidate"
    if score >= 3.5:
        return "aggressive"
    return "weak"


def liquidity_score(open_interest: int, volume: int, spread_pct: float | None) -> float:
    score = min(1.0, math.log10(max(open_interest, 1) + 1.0) / 3.0)
    score += min(0.5, math.log10(max(volume, 1) + 1.0) / 4.0)
    if spread_pct is None:
        score -= 0.5
    elif spread_pct <= 15:
        score += 1.0
    elif spread_pct <= 30:
        score += 0.4
    else:
        score -= 0.6
    return score


def base_quality_notes(
    symbol: str,
    spot: float,
    history: dict[str, Any],
    market_cap: float | None,
    event_before_expiry: bool,
) -> tuple[float, list[str]]:
    score = 0.0
    notes: list[str] = []
    if symbol in LEVERAGED_ETFS:
        score -= 2.0
        notes.append("avoid: leveraged ETF is not a routine assignment vehicle")
    if market_cap is not None:
        if market_cap >= 50_000_000_000:
            score += 1.0
        elif market_cap >= 10_000_000_000:
            score += 0.5
        else:
            score -= 0.3
            notes.append("small/mid-cap assignment quality needs extra review")
    ma50 = safe_float(history.get("ma50"))
    ma200 = safe_float(history.get("ma200"))
    if ma50 and spot >= ma50:
        score += 0.4
    elif ma50:
        score -= 0.4
        notes.append("below 50D moving average")
    if ma200 and spot >= ma200:
        score += 0.5
    elif ma200:
        score -= 0.7
        notes.append("below 200D moving average")
    hv20 = safe_float(history.get("hv20"))
    if hv20 is not None:
        if hv20 <= 0.35:
            score += 0.4
        elif hv20 >= 0.8:
            score -= 0.5
            notes.append("very high realized volatility")
    if event_before_expiry:
        score -= 1.5
        notes.append("avoid: earnings/event before expiration")
    return score, notes


def build_candidate(
    *,
    symbol: str,
    option_row: pd.Series,
    option_type: str,
    expiration: str,
    as_of: date,
    spot: float,
    history: dict[str, Any],
    market_cap: float | None,
    event_before_expiry: bool,
    rate: float,
    target_delta: float,
) -> Candidate | None:
    bid = safe_float(option_row.get("bid"), 0.0) or 0.0
    ask = safe_float(option_row.get("ask"), 0.0) or 0.0
    last = safe_float(option_row.get("lastPrice"))
    strike = safe_float(option_row.get("strike"))
    iv = safe_float(option_row.get("impliedVolatility"))
    if not strike or bid <= 0 or ask <= 0:
        return None
    mid = (bid + ask) / 2.0
    if mid <= 0:
        return None
    expiry = date.fromisoformat(expiration)
    dte = max((expiry - as_of).days, 1)
    delta, prob_itm = option_delta_prob(
        spot=spot,
        strike=strike,
        dte=dte,
        rate=rate,
        iv=iv or 0.0,
        option_type=option_type,
    )
    if delta is None:
        return None
    open_interest = int(safe_float(option_row.get("openInterest"), 0) or 0)
    volume = int(safe_float(option_row.get("volume"), 0) or 0)
    spread_pct = (ask - bid) / mid * 100.0 if mid else None
    premium_yield_pct = mid / spot * 100.0
    annualized_yield_pct = (mid / strike) * 365.0 / dte * 100.0
    quality_score, notes = base_quality_notes(
        symbol, spot, history, market_cap, event_before_expiry
    )
    abs_delta = abs(delta)
    delta_score = max(0.0, 1.5 - abs(abs_delta - target_delta) / 0.12)
    liq_score = liquidity_score(open_interest, volume, spread_pct)
    score = quality_score + delta_score + liq_score
    if option_type == "put":
        breakeven = strike - mid
        buffer_pct = (spot - breakeven) / spot * 100.0
        low20 = safe_float(history.get("low20"))
        low60 = safe_float(history.get("low60"))
        if low20 and breakeven <= low20:
            score += 1.0
            notes.append("breakeven below 20D low/support")
        elif low20 and breakeven <= low20 * 1.03:
            score += 0.4
            notes.append("breakeven near 20D low/support")
        elif low20:
            score -= 0.6
            notes.append("breakeven above recent support")
        if low60 and breakeven <= low60:
            score += 0.4
        if abs_delta > 0.30:
            score -= 1.0
            notes.append("avoid: put delta above routine income range")
        breakeven_or_exit = breakeven
        buffer_or_called_return_pct = buffer_pct
    else:
        exit_price = strike + mid
        called_return_pct = (exit_price - spot) / spot * 100.0
        high20 = safe_float(history.get("high20"))
        if high20 and strike >= high20:
            score += 0.8
            notes.append("strike above 20D high/resistance")
        elif high20:
            score -= 0.3
            notes.append("strike below recent high; higher call-away risk")
        if abs_delta > 0.35:
            score -= 0.8
            notes.append("avoid: call delta caps too much upside for routine income")
        breakeven_or_exit = exit_price
        buffer_or_called_return_pct = called_return_pct
    if open_interest < 50:
        score -= 0.4
        notes.append("thin open interest")
    if spread_pct is not None and spread_pct > 30:
        score -= 0.8
        notes.append("wide bid/ask; use small size or skip")
    grade = grade_candidate(score, notes)
    return Candidate(
        ticker=display_symbol(symbol),
        side="sell_put" if option_type == "put" else "sell_call",
        expiration=expiration,
        spot=round(spot, 4),
        strike=round(strike, 4),
        bid=round(bid, 4),
        ask=round(ask, 4),
        mid=round(mid, 4),
        last=None if last is None else round(last, 4),
        delta=round(delta, 4) if delta is not None else None,
        prob_itm=round(prob_itm, 4) if prob_itm is not None else None,
        iv=round(iv, 4) if iv is not None else None,
        volume=volume,
        open_interest=open_interest,
        spread_pct=round(spread_pct, 2) if spread_pct is not None else None,
        premium_yield_pct=round(premium_yield_pct, 2),
        annualized_yield_pct=round(annualized_yield_pct, 2),
        breakeven_or_exit=round(breakeven_or_exit, 4),
        buffer_or_called_return_pct=round(buffer_or_called_return_pct, 2),
        score=round(score, 3),
        grade=grade,
        notes=list(dict.fromkeys(notes)),
    )


def select_expiration(options: Iterable[str], as_of: date, preferred: str | None) -> str | None:
    available = sorted(options)
    if preferred:
        return preferred if preferred in available else None
    for item in available:
        try:
            exp = date.fromisoformat(item)
        except ValueError:
            continue
        if 7 <= (exp - as_of).days <= 14:
            return item
    for item in available:
        try:
            exp = date.fromisoformat(item)
        except ValueError:
            continue
        if exp > as_of:
            return item
    return None


def scan_ticker(
    symbol: str,
    *,
    as_of: date,
    expiration: str | None,
    side: str,
    rate: float,
    min_oi: int,
    max_spread_pct: float,
) -> dict[str, Any]:
    ticker = yf.Ticker(symbol)
    fast = ticker.fast_info
    spot = safe_float(fast.get("lastPrice")) or safe_float(fast.get("previousClose"))
    if not spot:
        return {"ticker": display_symbol(symbol), "error": "missing spot price"}
    options = list(ticker.options or [])
    selected_exp = select_expiration(options, as_of, expiration)
    if not selected_exp:
        return {
            "ticker": display_symbol(symbol),
            "spot": spot,
            "error": "requested expiration not available",
            "available_expirations": options[:12],
        }
    history = analyze_history(ticker)
    market_cap = safe_float(fast.get("marketCap"))
    earnings = earnings_dates(ticker)
    event_before = has_event_before_expiry(earnings, as_of, date.fromisoformat(selected_exp))
    chain = ticker.option_chain(selected_exp)
    candidates: list[Candidate] = []
    if side in {"put", "both"}:
        for _, row in chain.puts.iterrows():
            item = build_candidate(
                symbol=symbol,
                option_row=row,
                option_type="put",
                expiration=selected_exp,
                as_of=as_of,
                spot=spot,
                history=history,
                market_cap=market_cap,
                event_before_expiry=event_before,
                rate=rate,
                target_delta=0.16,
            )
            if item and 0.05 <= abs(item.delta or 0) <= 0.32:
                candidates.append(item)
    if side in {"call", "both"}:
        for _, row in chain.calls.iterrows():
            item = build_candidate(
                symbol=symbol,
                option_row=row,
                option_type="call",
                expiration=selected_exp,
                as_of=as_of,
                spot=spot,
                history=history,
                market_cap=market_cap,
                event_before_expiry=event_before,
                rate=rate,
                target_delta=0.18,
            )
            if item and 0.05 <= abs(item.delta or 0) <= 0.38:
                candidates.append(item)
    filtered = [
        item
        for item in candidates
        if item.open_interest >= min_oi
        and (item.spread_pct is None or item.spread_pct <= max_spread_pct)
    ]
    filtered.sort(key=lambda item: item.score, reverse=True)
    return {
        "ticker": display_symbol(symbol),
        "spot": round(spot, 4),
        "expiration": selected_exp,
        "earnings_dates": earnings,
        "event_before_expiry": event_before,
        "history": {
            key: round(value, 4) if isinstance(value, float) else value
            for key, value in history.items()
        },
        "candidates": [asdict(item) for item in filtered],
    }


def format_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f}%"


def markdown_report(payload: dict[str, Any], top: int) -> str:
    lines: list[str] = []
    bench = payload.get("benchmarks", {})
    lines.append("## Market Context")
    lines.append(
        f"- SPY 1D: {format_pct(bench.get('spy_1d_pct'))}; "
        f"QQQ 1D: {format_pct(bench.get('qqq_1d_pct'))}"
    )
    lines.append("- Weekly-option warning: 0-14 DTE trades have high gamma; use smaller size.")
    lines.append("")
    lines.append("## Best Candidates")
    lines.append(
        "| Ticker | Side | Strike | Exp | Credit(mid) | Delta | Prob ITM | "
        "Breakeven/Exit | Buffer/Called Return | Score | Grade |"
    )
    lines.append("|---|---|---:|---|---:|---:|---:|---:|---:|---:|---|")
    any_rows = False
    for result in payload.get("results", []):
        for item in result.get("candidates", [])[:top]:
            any_rows = True
            lines.append(
                "| {ticker} | {side} | {strike:.2f} | {expiration} | {mid:.2f} | "
                "{delta:.2f} | {prob:.1%} | {breakeven:.2f} | {buffer:.2f}% | "
                "{score:.2f} | {grade} |".format(
                    ticker=item["ticker"],
                    side=item["side"],
                    strike=item["strike"],
                    expiration=item["expiration"],
                    mid=item["mid"],
                    delta=item["delta"] or 0.0,
                    prob=item["prob_itm"] or 0.0,
                    breakeven=item["breakeven_or_exit"],
                    buffer=item["buffer_or_called_return_pct"],
                    score=item["score"],
                    grade=item["grade"],
                )
            )
    if not any_rows:
        lines.append("| n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |")
    lines.append("")
    lines.append("## Candidate Notes")
    for result in payload.get("results", []):
        if result.get("error"):
            lines.append(f"- {result['ticker']}: {result['error']}")
            continue
        candidates = result.get("candidates", [])[:top]
        if not candidates:
            lines.append(f"- {result['ticker']}: no liquid candidate passed filters.")
            continue
        for item in candidates:
            notes = "; ".join(item.get("notes") or ["no major warning"])
            lines.append(
                f"- {item['ticker']} {item['side']} {item['strike']}: {notes}; "
                f"OI={item['open_interest']}, spread={item['spread_pct']}%."
            )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan covered call / cash-secured put candidates.")
    parser.add_argument("--tickers", required=True, help="Comma or space separated ticker list.")
    parser.add_argument("--expiration", help="Expiration date YYYY-MM-DD. Defaults to next 7-14 DTE chain.")
    parser.add_argument("--side", choices=["put", "call", "both"], default="both")
    parser.add_argument("--as-of", help="As-of date YYYY-MM-DD. Defaults to today.")
    parser.add_argument("--risk-free-rate", type=float, default=0.036, help="Annual risk-free rate.")
    parser.add_argument("--min-oi", type=int, default=50)
    parser.add_argument("--max-spread-pct", type=float, default=30.0)
    parser.add_argument("--top", type=int, default=3)
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown")
    args = parser.parse_args()

    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
    tickers = parse_tickers(args.tickers)
    payload = {
        "as_of": as_of.isoformat(),
        "requested_expiration": args.expiration,
        "side": args.side,
        "benchmarks": fetch_benchmark_returns(),
        "results": [],
    }
    for symbol in tickers:
        try:
            payload["results"].append(
                scan_ticker(
                    symbol,
                    as_of=as_of,
                    expiration=args.expiration,
                    side=args.side,
                    rate=args.risk_free_rate,
                    min_oi=args.min_oi,
                    max_spread_pct=args.max_spread_pct,
                )
            )
        except Exception as exc:
            payload["results"].append({"ticker": display_symbol(symbol), "error": str(exc)})
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(markdown_report(payload, args.top))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
