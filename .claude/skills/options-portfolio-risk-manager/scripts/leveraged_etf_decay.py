#!/usr/bin/env python3
"""Estimate volatility drag for daily-reset leveraged ETFs."""

from __future__ import annotations

import argparse
import json
import math
from typing import Any

from common import fetch_history, fmt_money


def parse_float_list(raw: str) -> list[float]:
    return [float(part.strip()) for part in raw.split(",") if part.strip()]


def drag_factor(leverage: float, vol: float, years: float) -> float:
    return math.exp(-0.5 * leverage * (leverage - 1.0) * vol * vol * years)


def build_payload(args: argparse.Namespace) -> dict[str, Any]:
    vols = parse_float_list(args.vols)
    years = parse_float_list(args.years)
    underlying_multiples = parse_float_list(args.underlying_multiples)
    historical_vols: dict[str, float] = {}
    if args.underlying:
        history = fetch_history(args.underlying, period="2y")
        for key in ("hv20", "hv60", "hv120", "hv252"):
            if key in history:
                historical_vols[key] = round(float(history[key]), 4)

    rows = []
    breakeven_rows = []
    needed_multiple = None
    if args.current_etf_price and args.avg_cost:
        needed_multiple = args.avg_cost / args.current_etf_price
    for vol in vols:
        for year in years:
            drag = drag_factor(args.leverage, vol, year)
            rows.append(
                {
                    "vol": vol,
                    "years": year,
                    "drag_factor": drag,
                    "drag_loss_pct": 1.0 - drag,
                }
            )
            if needed_multiple:
                underlying_multiple = (needed_multiple / drag) ** (1.0 / args.leverage)
                target = (
                    underlying_multiple * args.underlying_spot
                    if args.underlying_spot
                    else None
                )
                breakeven_rows.append(
                    {
                        "vol": vol,
                        "years": year,
                        "underlying_multiple_needed": underlying_multiple,
                        "underlying_return_needed_pct": underlying_multiple - 1.0,
                        "underlying_target_price": target,
                    }
                )

    scenario_rows = []
    for vol in vols:
        for year in years:
            drag = drag_factor(args.leverage, vol, year)
            for multiple in underlying_multiples:
                etf_multiple = (multiple**args.leverage) * drag
                scenario_rows.append(
                    {
                        "vol": vol,
                        "years": year,
                        "underlying_multiple": multiple,
                        "etf_multiple": etf_multiple,
                    }
                )
    return {
        "leverage": args.leverage,
        "underlying": args.underlying,
        "historical_vols": historical_vols,
        "drag": rows,
        "scenarios": scenario_rows,
        "breakeven": breakeven_rows,
    }


def markdown(payload: dict[str, Any]) -> str:
    lines = ["## Leveraged ETF Decay"]
    if payload.get("underlying"):
        lines.append(f"- Underlying: {payload['underlying']}")
    if payload.get("historical_vols"):
        hv = payload["historical_vols"]
        lines.append(
            "- Historical vol: "
            + ", ".join(f"{key.upper()} {value * 100:.1f}%" for key, value in hv.items())
        )
    lines.append("")
    lines.append("### Volatility Drag")
    lines.append("| Vol | Years | Drag Factor | Drag Loss |")
    lines.append("|---:|---:|---:|---:|")
    for row in payload["drag"]:
        lines.append(
            "| {vol:.0%} | {years:g} | {drag:.3f}x | -{loss:.1%} |".format(
                vol=row["vol"],
                years=row["years"],
                drag=row["drag_factor"],
                loss=row["drag_loss_pct"],
            )
        )
    lines.append("")
    lines.append("### Scenario Multiples")
    lines.append("| Vol | Years | Underlying Multiple | ETF Multiple |")
    lines.append("|---:|---:|---:|---:|")
    for row in payload["scenarios"]:
        lines.append(
            "| {vol:.0%} | {years:g} | {um:.2f}x | {em:.2f}x |".format(
                vol=row["vol"],
                years=row["years"],
                um=row["underlying_multiple"],
                em=row["etf_multiple"],
            )
        )
    if payload.get("breakeven"):
        lines.append("")
        lines.append("### Breakeven Requirement")
        lines.append("| Vol | Years | Underlying Multiple Needed | Underlying Target |")
        lines.append("|---:|---:|---:|---:|")
        for row in payload["breakeven"]:
            lines.append(
                "| {vol:.0%} | {years:g} | {multiple:.2f}x | {target} |".format(
                    vol=row["vol"],
                    years=row["years"],
                    multiple=row["underlying_multiple_needed"],
                    target=fmt_money(row["underlying_target_price"])
                    if row["underlying_target_price"]
                    else "n/a",
                )
            )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--leverage", type=float, default=2.0)
    parser.add_argument("--vols", default="0.5,0.75,1.0")
    parser.add_argument("--years", default="1,2,4")
    parser.add_argument("--underlying-multiples", default="1,1.5,2,3,4")
    parser.add_argument("--underlying", help="Optional ticker for historical volatility.")
    parser.add_argument("--current-etf-price", type=float)
    parser.add_argument("--avg-cost", type=float)
    parser.add_argument("--underlying-spot", type=float)
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown")
    args = parser.parse_args()
    payload = build_payload(args)
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(markdown(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

