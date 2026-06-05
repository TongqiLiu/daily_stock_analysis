#!/usr/bin/env python3
"""First-pass VCP / bull-flag / breakout scanner."""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from typing import Iterable

import pandas as pd

try:
    import yfinance as yf
except ImportError as exc:  # pragma: no cover
    raise SystemExit("Missing dependency: pip install yfinance pandas") from exc


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high_low = df["High"] - df["Low"]
    high_close = (df["High"] - df["Close"].shift()).abs()
    low_close = (df["Low"] - df["Close"].shift()).abs()
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return true_range.rolling(period).mean()


def local_lows(df: pd.DataFrame, lookback: int = 80, window: int = 2) -> list[tuple[pd.Timestamp, float]]:
    data = df.tail(lookback)
    lows: list[tuple[pd.Timestamp, float]] = []
    for i in range(window, len(data) - window):
        value = float(data["Low"].iloc[i])
        neighborhood = data["Low"].iloc[i - window : i + window + 1]
        if value == float(neighborhood.min()):
            lows.append((data.index[i], value))
    deduped: list[tuple[pd.Timestamp, float]] = []
    for item in lows:
        if not deduped or (item[0] - deduped[-1][0]).days > 3:
            deduped.append(item)
        elif item[1] < deduped[-1][1]:
            deduped[-1] = item
    return deduped


def pct(value: float | None) -> str:
    if value is None or math.isnan(value):
        return "n/a"
    return f"{value * 100:.1f}%"


@dataclass
class Setup:
    ticker: str
    close: float
    pattern: str
    status: str
    pivot: float
    distance_to_pivot: float
    stop: float
    risk: float
    extension_atr: float
    extension_pct: float
    volume_ratio: float
    rel20: float
    score: float
    grade: str
    notes: list[str]


def classify_grade(score: float) -> str:
    if score >= 7.5:
        return "A"
    if score >= 6.0:
        return "B"
    if score >= 4.5:
        return "C"
    return "avoid"


def analyze_ticker(ticker: str, benchmark_returns: pd.Series | None) -> Setup:
    df = yf.Ticker(ticker).history(period="1y", auto_adjust=False)
    if len(df) < 80:
        raise ValueError(f"{ticker}: not enough daily history")

    df = df.dropna(subset=["Open", "High", "Low", "Close", "Volume"]).copy()
    df["EMA10"] = ema(df["Close"], 10)
    df["EMA21"] = ema(df["Close"], 21)
    df["SMA50"] = df["Close"].rolling(50).mean()
    df["SMA200"] = df["Close"].rolling(200).mean()
    df["ATR14"] = atr(df, 14)
    df["Vol20"] = df["Volume"].rolling(20).mean()

    row = df.iloc[-1]
    close = float(row["Close"])
    atr14 = float(row["ATR14"])
    ema21 = float(row["EMA21"])
    sma50 = float(row["SMA50"])
    sma200 = float(row["SMA200"]) if not pd.isna(row["SMA200"]) else float("nan")
    avg_vol20 = float(row["Vol20"])
    volume_ratio = float(row["Volume"] / avg_vol20) if avg_vol20 else float("nan")

    prior_20_high = float(df["High"].iloc[-21:-1].max())
    pivot = prior_20_high
    distance_to_pivot = (pivot - close) / close
    triggered = close > pivot and volume_ratio >= 1.4

    lows = local_lows(df)
    recent_lows = lows[-3:]
    higher_lows = len(recent_lows) >= 2 and all(
        recent_lows[i][1] > recent_lows[i - 1][1] for i in range(1, len(recent_lows))
    )
    stop = recent_lows[-1][1] if recent_lows else float(df["Low"].tail(20).min())
    risk_raw = (close - stop) / close if close else float("nan")
    below_structure_stop = risk_raw <= 0
    risk = max(risk_raw, 0.0) if not math.isnan(risk_raw) else float("nan")

    last_45 = df.tail(45)
    thirds = [last_45.iloc[:15], last_45.iloc[15:30], last_45.iloc[30:]]
    ranges = []
    for part in thirds:
        if len(part):
            ranges.append(float((part["High"].max() - part["Low"].min()) / part["Close"].iloc[-1]))
    contraction = len(ranges) == 3 and ranges[2] < ranges[1] < ranges[0]
    tight_recent = len(ranges) == 3 and ranges[2] <= min(0.12, ranges[0] * 0.65)

    impulse_45 = close / float(df["Close"].iloc[-45]) - 1
    flag_depth = (float(df["High"].tail(25).max()) - float(df["Low"].tail(25).min())) / close
    bull_flag = impulse_45 >= 0.15 and flag_depth <= max(0.18, impulse_45 * 0.55)

    vol_dry = float(df["Volume"].tail(5).mean() / avg_vol20) if avg_vol20 else float("nan")
    extension_atr = (close - ema21) / atr14 if atr14 else float("nan")
    extension_pct = (close - ema21) / ema21 if ema21 else float("nan")

    ret20 = close / float(df["Close"].iloc[-21]) - 1 if len(df) > 21 else float("nan")
    rel20 = ret20
    if benchmark_returns is not None and len(benchmark_returns) > 20:
        rel20 = ret20 - float(benchmark_returns.iloc[-1] / benchmark_returns.iloc[-21] - 1)

    above_trend = close > sma50 and (math.isnan(sma200) or close > sma200)
    near_pivot = -0.03 <= distance_to_pivot <= 0.06
    not_extended = extension_atr <= 3.0 and extension_pct <= 0.18

    score = 0.0
    notes: list[str] = []
    if above_trend:
        score += 1.5
    else:
        notes.append("below 50D/200D trend filter")
    if higher_lows:
        score += 1.5
    else:
        notes.append("higher lows not clearly confirmed")
    if contraction or tight_recent:
        score += 1.5
    else:
        notes.append("range contraction is incomplete")
    if vol_dry <= 0.85:
        score += 1.0
    else:
        notes.append("volume dry-up is weak")
    if near_pivot:
        score += 1.0
    else:
        notes.append("not close to pivot")
    if not_extended:
        score += 1.0
    else:
        notes.append("extended from 21EMA")
    if rel20 > 0:
        score += 1.0
    else:
        notes.append("20D relative strength is weak")
    if triggered:
        score += 1.0

    if below_structure_stop:
        status = "failed / below stop"
    elif triggered:
        status = "triggered breakout"
    elif near_pivot and (contraction or bull_flag or higher_lows):
        status = "watch pivot"
    else:
        status = "developing"

    if contraction and higher_lows:
        pattern = "VCP"
    elif bull_flag:
        pattern = "bull flag"
    elif higher_lows:
        pattern = "higher-low coil"
    else:
        pattern = "unconfirmed"

    if below_structure_stop:
        notes.append("price is below latest structural low")
    elif risk > 0.12:
        notes.append("structure stop is wide")
    if distance_to_pivot < -0.03:
        notes.append("already above pivot; avoid chasing unless managing")

    return Setup(
        ticker=ticker,
        close=close,
        pattern=pattern,
        status=status,
        pivot=pivot,
        distance_to_pivot=distance_to_pivot,
        stop=stop,
        risk=risk,
        extension_atr=extension_atr,
        extension_pct=extension_pct,
        volume_ratio=volume_ratio,
        rel20=rel20,
        score=score,
        grade=classify_grade(score),
        notes=notes,
    )


def load_benchmark(symbol: str | None) -> pd.Series | None:
    if not symbol:
        return None
    df = yf.Ticker(symbol).history(period="1y", auto_adjust=False)
    if len(df) < 30:
        return None
    return df["Close"].dropna()


def markdown_report(setups: Iterable[Setup]) -> str:
    rows = list(setups)
    lines = [
        "| Ticker | Close | Pattern | Status | Pivot | Dist | Stop | Risk | Ext ATR | Vol x20 | RS20 | Grade |",
        "|---|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for s in rows:
        lines.append(
            f"| {s.ticker} | {s.close:.2f} | {s.pattern} | {s.status} | "
            f"{s.pivot:.2f} | {pct(s.distance_to_pivot)} | {s.stop:.2f} | "
            f"{pct(s.risk)} | {s.extension_atr:.2f} | {s.volume_ratio:.2f} | "
            f"{pct(s.rel20)} | {s.grade} |"
        )
    lines.append("\n## Notes")
    for s in rows:
        note = "; ".join(s.notes[:5]) if s.notes else "clean setup by scanner filters"
        lines.append(f"- {s.ticker}: {note}.")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan VCP/bull-flag breakout candidates.")
    parser.add_argument("--tickers", required=True, help="Comma-separated tickers, e.g. INTC,ACLS,MCHP")
    parser.add_argument("--benchmark", default="QQQ", help="Benchmark for relative strength; default QQQ")
    parser.add_argument("--format", choices=["markdown"], default="markdown")
    args = parser.parse_args()

    benchmark = load_benchmark(args.benchmark)
    results: list[Setup] = []
    for ticker in [item.strip().upper() for item in args.tickers.split(",") if item.strip()]:
        try:
            results.append(analyze_ticker(ticker, benchmark))
        except Exception as exc:
            print(f"- {ticker}: ERROR {exc}")
    if results:
        print(markdown_report(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
