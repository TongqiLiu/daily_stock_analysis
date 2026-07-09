#!/usr/bin/env python3
"""First-pass Bingshen volume-price scanner for A-share daily OHLCV.

Uses repository data_provider via load_history_df. Output is a filter for the
bingshen-volume-price-analysis skill, not a trading signal engine.
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional

import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _ensure_columns(df: pd.DataFrame) -> pd.DataFrame:
    frame = df.copy()
    rename = {c: c.lower() for c in frame.columns}
    frame = frame.rename(columns=rename)
    required = {"open", "high", "low", "close", "volume"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"missing columns: {sorted(missing)}")
    for col in required | {"pct_chg"}:
        if col in frame.columns:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
    if "pct_chg" not in frame.columns:
        frame["pct_chg"] = frame["close"].pct_change() * 100.0
    frame = frame.dropna(subset=["close", "volume"])
    if "date" in frame.columns:
        frame = frame.sort_values("date").reset_index(drop=True)
    else:
        frame = frame.reset_index(drop=True)
    return frame


def _volume_ratio(series: pd.Series, window: int = 5) -> pd.Series:
    ma = series.rolling(window, min_periods=1).mean().shift(1)
    ratio = series / ma
    return ratio.fillna(1.0)


def _body_pct(row: pd.Series) -> float:
    spread = float(row["high"] - row["low"])
    if spread <= 0:
        return 0.0
    body = abs(float(row["close"] - row["open"]))
    return body / spread


@dataclass
class ScanResult:
    code: str
    source: str
    close: float
    lean: str
    score: float
    signals: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    n_buy_low: Optional[float] = None
    n_buy_high: Optional[float] = None
    n_stop: Optional[float] = None
    anchor_date: Optional[str] = None


def _detect_r3_anchor(frame: pd.DataFrame) -> Optional[int]:
    if len(frame) < 60:
        return None
    high60 = frame["high"].rolling(60, min_periods=20).max()
    vol_ma5 = frame["volume"].rolling(5, min_periods=1).mean()
    for i in range(len(frame) - 1, max(len(frame) - 40, 0), -1):
        row = frame.iloc[i]
        if float(row["close"]) < float(high60.iloc[i]) * 0.85:
            continue
        if float(row["close"]) >= float(row["open"]):
            continue
        pct = float(row.get("pct_chg", 0.0))
        if pct > -3.0:
            continue
        if float(row["volume"]) < float(vol_ma5.iloc[i]) * 1.5:
            continue
        hl = float(row["high"] - row["low"])
        if hl <= 0:
            continue
        close_pos = (float(row["close"]) - float(row["low"])) / hl
        if close_pos > 0.35:
            continue
        return i
    return None


def _r1_healthy(frame: pd.DataFrame, window: int = 20) -> Optional[bool]:
    tail = frame.tail(window)
    if len(tail) < 10:
        return None
    up = tail[tail["pct_chg"] > 0]
    down = tail[tail["pct_chg"] < 0]
    if up.empty or down.empty:
        return None
    ratio = float(up["volume"].mean() / down["volume"].mean())
    if ratio >= 1.10:
        return True
    if ratio < 0.95:
        return False
    return None


def _r4_divergence(frame: pd.DataFrame) -> bool:
    tail = frame.tail(30)
    if len(tail) < 15:
        return False
    recent_high_idx = tail["high"].idxmax()
    recent_high_vol = float(tail.loc[recent_high_idx, "volume"])
    last = tail.iloc[-1]
    if float(last["high"]) >= float(tail["high"].iloc[-21:-1].max()) * 0.99:
        if float(last["volume"]) < recent_high_vol * 0.85:
            return True
    return False


def _r5_washout(frame: pd.DataFrame) -> bool:
    tail = frame.tail(20)
    if len(tail) < 15:
        return False
    first = tail.iloc[:10]
    second = tail.iloc[10:]
    down_second = second[second["pct_chg"] < 0]
    if down_second.empty:
        return False
    up_first = first[first["pct_chg"] > 0]
    if up_first.empty:
        return False
    if float(down_second["volume"].mean()) >= float(up_first["volume"].mean()) * 0.85:
        return False
    prior_low = float(first["low"].min())
    if float(second["low"].min()) < prior_low * 0.98:
        return False
    return True


def _r6_long_yin_short_vol(frame: pd.DataFrame) -> bool:
    tail = frame.tail(15)
    vr = _volume_ratio(tail["volume"])
    for i in range(len(tail) - 3):
        row = tail.iloc[i]
        if float(row.get("pct_chg", 0.0)) > -4.0:
            continue
        if float(vr.iloc[i]) >= 0.85:
            continue
        future_low = float(tail.iloc[i + 1 : i + 4]["low"].min())
        if future_low < float(row["low"]) * 0.995:
            continue
        return True
    return False


def _megmeet_pattern(frame: pd.DataFrame) -> bool:
    """Pullback context but volume not shrinking (该缩不缩)."""
    tail = frame.tail(10)
    if len(tail) < 5:
        return False
    down = tail[tail["pct_chg"] < 0]
    if len(down) < 2:
        return False
    vr = _volume_ratio(tail["volume"])
    if float(vr[tail["pct_chg"] < 0].mean()) > 1.2:
        return True
    # Latest down day spike while in pullback (麦格米特当日特征)
    last = tail.iloc[-1]
    if float(last.get("pct_chg", 0.0)) < 0 and float(tail["vr"].iloc[-1]) > 1.5:
        prior_up = tail[tail["pct_chg"] > 0]
        if not prior_up.empty and float(last["close"]) < float(prior_up["high"].max()):
            return True
    return False


def _ladder_distribution(frame: pd.DataFrame) -> bool:
    tail = frame.tail(10)
    if len(tail) < 8:
        return False
    down_days = int((tail["pct_chg"] < 0).sum())
    drop = (float(tail.iloc[-1]["close"]) - float(tail.iloc[0]["close"])) / float(tail.iloc[0]["close"])
    # sideways days: daily range / close < 3%
    sideways = 0
    for _, row in tail.iterrows():
        if float(row["close"]) <= 0:
            continue
        if (float(row["high"]) - float(row["low"])) / float(row["close"]) < 0.03:
            sideways += 1
    return drop < -0.10 and down_days >= 7 and sideways < 3


def _find_n_pattern(frame: pd.DataFrame) -> tuple[Optional[float], Optional[float], Optional[float]]:
    tail = frame.tail(40)
    vr = _volume_ratio(tail["volume"])
    for i in range(len(tail) - 9, 2, -1):
        b = tail.iloc[i]
        if float(vr.iloc[i]) < 1.5:
            continue
        if float(b["close"]) <= float(b["open"]):
            continue
        pct = float(b.get("pct_chg", 0.0))
        if pct < 3.0:
            continue
        pull = tail.iloc[i + 1 : i + 9]
        if pull.empty:
            continue
        if not (pull["pct_chg"] < 0).any():
            continue
        if float(pull["volume"].mean()) >= float(b["volume"]) * 0.75:
            continue
        low = float(pull["low"].min())
        b_low = float(b["low"])
        b_mid = (float(b["open"]) + float(b["close"])) / 2.0
        if low < b_low * 0.995 or low > b_mid * 1.02:
            continue
        return b_low, b_mid, b_low * 0.995
    return None, None, None


def analyze_code(code: str, days: int = 120) -> ScanResult:
    from src.services.history_loader import load_history_df

    df, source = load_history_df(code, days=days)
    if df is None or df.empty:
        raise ValueError(f"{code}: no history")

    frame = _ensure_columns(df)
    if len(frame) < 30:
        raise ValueError(f"{code}: insufficient bars ({len(frame)})")

    frame["vr"] = _volume_ratio(frame["volume"])
    close = float(frame.iloc[-1]["close"])
    signals: List[str] = []
    warnings: List[str] = []
    score = 0.0

    anchor_idx = _detect_r3_anchor(frame)
    if anchor_idx is not None:
        signals.append("R3:high_vol_bear_anchor")
        warnings.append("高位放量大阴线锚点")
        score -= 2.5
        anchor_date = str(frame.iloc[anchor_idx].get("date", anchor_idx))
    else:
        anchor_date = None

    r1 = _r1_healthy(frame)
    if r1 is True:
        signals.append("R1:healthy_vp")
        score += 1.0
    elif r1 is False:
        signals.append("R1:unhealthy_vp")
        score -= 0.5

    if _r4_divergence(frame):
        signals.append("R4:vol_price_divergence")
        warnings.append("缩量涨/量价背离")
        score -= 1.5

    if _r5_washout(frame):
        signals.append("R5:shrink_pullback_hold_low")
        score += 1.0

    if _r6_long_yin_short_vol(frame):
        signals.append("R6:long_yin_short_vol")
        score += 0.5

    if _megmeet_pattern(frame):
        signals.append("P2:expand_on_pullback")
        warnings.append("该缩不缩(麦格式)")
        score -= 2.0

    if _ladder_distribution(frame):
        signals.append("P3:ladder_distribution")
        warnings.append("阶梯式出货")
        score -= 2.0

    n_low, n_high, n_stop = _find_n_pattern(frame)
    if n_low is not None:
        signals.append("N:pattern_candidate")
        score += 0.5

    # lean classification
    if "R3:high_vol_bear_anchor" in signals or "P2:expand_on_pullback" in signals:
        lean = "distribution" if "P2:expand_on_pullback" in signals else "high_risk"
    elif "P3:ladder_distribution" in signals:
        lean = "distribution"
    elif "R5:shrink_pullback_hold_low" in signals and score >= 0:
        lean = "washout_watch"
    elif r1 is True and score >= 1.0:
        lean = "healthy_vp"
    elif score <= -2:
        lean = "high_risk"
    else:
        lean = "neutral_watch"

    return ScanResult(
        code=code,
        source=source,
        close=close,
        lean=lean,
        score=round(score, 2),
        signals=signals,
        warnings=warnings,
        n_buy_low=n_low,
        n_buy_high=n_high,
        n_stop=n_stop,
        anchor_date=anchor_date,
    )


def _format_markdown(results: Iterable[ScanResult]) -> str:
    lines = [
        "| 代码 | 收盘 | 倾向 | 分数 | 信号 | 警示 | N字买区 | 止损 | 锚点日 | 数据源 |",
        "|------|------|------|------|------|------|---------|------|--------|--------|",
    ]
    for r in results:
        n_zone = "—"
        if r.n_buy_low is not None and r.n_buy_high is not None:
            n_zone = f"{r.n_buy_low:.2f}~{r.n_buy_high:.2f}"
        n_stop = f"{r.n_stop:.2f}" if r.n_stop is not None else "—"
        lines.append(
            "| {code} | {close:.2f} | {lean} | {score:.1f} | {sig} | {warn} | {nz} | {ns} | {ad} | {src} |".format(
                code=r.code,
                close=r.close,
                lean=r.lean,
                score=r.score,
                sig=", ".join(r.signals) or "—",
                warn="; ".join(r.warnings) or "—",
                nz=n_zone,
                ns=n_stop,
                ad=r.anchor_date or "—",
                src=r.source,
            )
        )
    lines.append("")
    lines.append("> 扫描器输出仅供 skill 参考，须结合 rules.md 决策树与图表定性。")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Bingshen volume-price scanner")
    parser.add_argument("--codes", required=True, help="Comma-separated stock codes")
    parser.add_argument("--days", type=int, default=120, help="History trading days")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    args = parser.parse_args()

    codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    results: List[ScanResult] = []
    errors: List[str] = []

    for code in codes:
        try:
            results.append(analyze_code(code, days=args.days))
        except Exception as exc:  # noqa: BLE001 - CLI aggregates per-ticker failures
            errors.append(f"{code}: {exc}")

    if args.format == "json":
        import json

        payload = [
            {
                "code": r.code,
                "close": r.close,
                "lean": r.lean,
                "score": r.score,
                "signals": r.signals,
                "warnings": r.warnings,
                "n_buy_low": r.n_buy_low,
                "n_buy_high": r.n_buy_high,
                "n_stop": r.n_stop,
                "anchor_date": r.anchor_date,
                "source": r.source,
            }
            for r in results
        ]
        print(json.dumps({"results": payload, "errors": errors}, ensure_ascii=False, indent=2))
    else:
        if results:
            print(_format_markdown(results))
        for err in errors:
            print(f"<!-- ERROR: {err} -->", file=sys.stderr)

    return 0 if results else 1


if __name__ == "__main__":
    raise SystemExit(main())
