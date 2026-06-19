#!/usr/bin/env python3
"""Shared utilities for the options portfolio risk scripts."""

from __future__ import annotations

import json
import math
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

try:
    import numpy as np
    import pandas as pd
    import yfinance as yf
except Exception as exc:  # pragma: no cover - environment guard
    print(
        "Missing dependency. Install yfinance, pandas, and numpy before running "
        f"this script. Original error: {exc}",
        file=sys.stderr,
    )
    sys.exit(2)


LEVERAGED_ETFS: dict[str, tuple[str | None, float]] = {
    "TQQQ": ("QQQ", 3.0),
    "SQQQ": ("QQQ", -3.0),
    "UPRO": ("SPY", 3.0),
    "SPXU": ("SPY", -3.0),
    "SOXL": ("SOXX", 3.0),
    "SOXS": ("SOXX", -3.0),
    "LABU": ("XBI", 3.0),
    "LABD": ("XBI", -3.0),
    "FNGU": (None, 3.0),
    "FNGD": (None, -3.0),
    "MSTX": ("MSTR", 2.0),
    "MSTU": ("MSTR", 2.0),
}


@dataclass
class OptionMath:
    delta: float | None
    prob_itm: float | None
    prob_touch: float | None


def norm_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        if isinstance(value, float) and math.isnan(value):
            return default
        return float(value)
    except Exception:
        return default


def safe_int(value: Any, default: int = 0) -> int:
    converted = safe_float(value)
    return default if converted is None else int(converted)


def yahoo_symbol(symbol: str) -> str:
    cleaned = symbol.strip().upper()
    if cleaned in {"BRK.B", "BRKB"}:
        return "BRK-B"
    if cleaned in {"BRK.A", "BRKA"}:
        return "BRK-A"
    return cleaned


def display_symbol(symbol: str) -> str:
    return symbol.upper().replace("-", ".")


def parse_date(value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return date.fromisoformat(str(value))


def option_delta_prob(
    *,
    spot: float,
    strike: float,
    dte: int,
    rate: float,
    iv: float,
    option_type: str,
) -> OptionMath:
    if spot <= 0 or strike <= 0 or dte <= 0 or iv <= 0.01:
        return OptionMath(None, None, None)
    t = dte / 365.0
    d1 = (math.log(spot / strike) + (rate + 0.5 * iv * iv) * t) / (
        iv * math.sqrt(t)
    )
    d2 = d1 - iv * math.sqrt(t)
    if option_type == "call":
        delta = norm_cdf(d1)
        prob_itm = norm_cdf(d2)
    else:
        delta = norm_cdf(d1) - 1.0
        prob_itm = norm_cdf(-d2)
    prob_touch = min(1.0, 2.0 * prob_itm)
    return OptionMath(delta, prob_itm, prob_touch)


def pct_change(series: "pd.Series", periods: int) -> float | None:
    series = series.dropna()
    if len(series) <= periods:
        return None
    previous = safe_float(series.iloc[-1 - periods])
    latest = safe_float(series.iloc[-1])
    if previous in (None, 0):
        return None
    return (latest / previous - 1.0) * 100.0


def fetch_history(symbol: str, period: str = "1y") -> dict[str, Any]:
    ticker = yf.Ticker(yahoo_symbol(symbol))
    hist = ticker.history(period=period, interval="1d", auto_adjust=False)
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
    for window in (20, 60, 120, 252):
        if len(returns) >= window:
            out[f"hv{window}"] = float(returns.tail(window).std() * math.sqrt(252.0))
    for window in (20, 50, 200):
        if len(close) >= window:
            out[f"ma{window}"] = float(close.rolling(window).mean().iloc[-1])
    for window in (20, 60):
        if len(hist) >= window:
            out[f"low{window}"] = float(hist["Low"].tail(window).min())
            out[f"high{window}"] = float(hist["High"].tail(window).max())
    return out


def fetch_spot(symbol: str) -> float | None:
    try:
        fast = yf.Ticker(yahoo_symbol(symbol)).fast_info
        return safe_float(fast.get("lastPrice")) or safe_float(fast.get("previousClose"))
    except Exception:
        return None


def fetch_market_cap(symbol: str) -> float | None:
    try:
        return safe_float(yf.Ticker(yahoo_symbol(symbol)).fast_info.get("marketCap"))
    except Exception:
        return None


def coerce_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)):
        try:
            return datetime.utcfromtimestamp(value).date()
        except Exception:
            return None
    if hasattr(value, "date"):
        try:
            return value.date()
        except Exception:
            pass
    text = str(value)
    if not text or text.lower() in {"nan", "none", "nat"}:
        return None
    try:
        return date.fromisoformat(text[:10])
    except Exception:
        return None


def first_date(value: Any) -> date | None:
    if isinstance(value, (list, tuple)):
        for item in value:
            parsed = coerce_date(item)
            if parsed:
                return parsed
        return None
    return coerce_date(value)


def fetch_events(symbol: str) -> dict[str, date]:
    ticker = yf.Ticker(yahoo_symbol(symbol))
    events: dict[str, date] = {}
    try:
        calendar = ticker.calendar
        if isinstance(calendar, dict):
            earnings = first_date(calendar.get("Earnings Date"))
        else:
            earnings = None
            if hasattr(calendar, "loc"):
                for key in ("Earnings Date", "Earnings Date[0]"):
                    try:
                        earnings = first_date(calendar.loc[key].iloc[0])
                        if earnings:
                            break
                    except Exception:
                        continue
        if earnings:
            events["earnings_date"] = earnings
    except Exception:
        pass
    try:
        info = ticker.info
        ex_dividend = first_date(
            info.get("exDividendDate")
            or info.get("ex_dividend_date")
            or info.get("exDividend")
        )
        if ex_dividend:
            events["ex_dividend_date"] = ex_dividend
    except Exception:
        pass
    return events


def find_option_row(symbol: str, expiry: str, strike: float, option_type: str) -> Any | None:
    ticker = yf.Ticker(yahoo_symbol(symbol))
    if expiry not in list(ticker.options or []):
        return None
    chain = ticker.option_chain(expiry)
    table = chain.calls if option_type == "call" else chain.puts
    if table is None or table.empty:
        return None
    rows = table[(table["strike"] - float(strike)).abs() < 0.0001]
    if rows.empty:
        return None
    return rows.iloc[0]


def parse_scalar(raw: str) -> Any:
    value = raw.strip()
    if value == "":
        return ""
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    if value in {"null", "None", "~"}:
        return None
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    if value.startswith("[") and value.endswith("]"):
        body = value[1:-1].strip()
        if not body:
            return []
        return [parse_scalar(part.strip()) for part in body.split(",")]
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    if re.fullmatch(r"-?(\d+\.\d*|\d*\.\d+)([eE]-?\d+)?", value) or re.fullmatch(
        r"-?\d+[eE]-?\d+", value
    ):
        return float(value)
    return value


def strip_comment(line: str) -> str:
    quote: str | None = None
    for idx, char in enumerate(line):
        if char in {'"', "'"}:
            quote = None if quote == char else char if quote is None else quote
        if char == "#" and quote is None:
            return line[:idx]
    return line


def simple_yaml_load(text: str) -> Any:
    raw_lines = []
    for line in text.splitlines():
        cleaned = strip_comment(line).rstrip()
        if cleaned.strip():
            raw_lines.append(cleaned)

    def indent_of(line: str) -> int:
        return len(line) - len(line.lstrip(" "))

    def parse_block(index: int, indent: int) -> tuple[Any, int]:
        if index >= len(raw_lines):
            return {}, index
        is_list = raw_lines[index].lstrip().startswith("- ")
        if is_list:
            items = []
            while index < len(raw_lines):
                line = raw_lines[index]
                current_indent = indent_of(line)
                if current_indent < indent or not line.lstrip().startswith("- "):
                    break
                content = line.lstrip()[2:].strip()
                index += 1
                if content == "":
                    child, index = parse_block(index, current_indent + 2)
                    items.append(child)
                    continue
                if ":" in content:
                    key, raw_value = content.split(":", 1)
                    item: dict[str, Any] = {}
                    if raw_value.strip():
                        item[key.strip()] = parse_scalar(raw_value)
                    else:
                        child, index = parse_block(index, current_indent + 2)
                        item[key.strip()] = child
                    while index < len(raw_lines):
                        next_indent = indent_of(raw_lines[index])
                        if next_indent <= current_indent:
                            break
                        key_line = raw_lines[index].strip()
                        if key_line.startswith("- "):
                            break
                        child_key, child_raw = key_line.split(":", 1)
                        index += 1
                        if child_raw.strip():
                            item[child_key.strip()] = parse_scalar(child_raw)
                        else:
                            child, index = parse_block(index, next_indent + 2)
                            item[child_key.strip()] = child
                    items.append(item)
                else:
                    items.append(parse_scalar(content))
            return items, index

        mapping: dict[str, Any] = {}
        while index < len(raw_lines):
            line = raw_lines[index]
            current_indent = indent_of(line)
            if current_indent < indent or line.lstrip().startswith("- "):
                break
            content = line.strip()
            key, raw_value = content.split(":", 1)
            index += 1
            if raw_value.strip():
                mapping[key.strip()] = parse_scalar(raw_value)
            else:
                child, index = parse_block(index, current_indent + 2)
                mapping[key.strip()] = child
        return mapping, index

    parsed, _ = parse_block(0, 0)
    return parsed


def load_config(path: str | Path) -> dict[str, Any]:
    text = Path(path).read_text(encoding="utf-8")
    stripped = text.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        return json.loads(text)
    try:
        import yaml  # type: ignore

        loaded = yaml.safe_load(text)
        return loaded or {}
    except Exception:
        loaded = simple_yaml_load(text)
        if not isinstance(loaded, dict):
            raise ValueError(f"Expected mapping in {path}")
        return loaded


def fmt_money(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"${value:,.2f}"


def fmt_pct(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.{digits}f}%"


def fmt_number(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"
