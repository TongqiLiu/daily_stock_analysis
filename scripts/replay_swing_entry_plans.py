#!/usr/bin/env python3
"""Auditable 12-symbol execution-layer replay; never write production outcomes.

Fetch snapshots once (--fetch), then rerun from those frozen CSVs without network.
Acceptance thresholds are fixed in code; no automatic configuration activation.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.core.backtest_engine import BacktestEngine  # noqa: E402
from src.core.trading_calendar import get_next_trading_date, get_trading_dates  # noqa: E402
from src.services.swing_trade_plan import (  # noqa: E402
    PLAN_VERSION, SETUPS, build_swing_trade_plan, validate_daily_frame,
)

SYMBOLS = ("ORCL", "AXTI", "RKLB", "AMKR", "INTC", "MSTR", "NFLX", "SOFI", "WULF", "RXRX", "BABA", "CPNG")


def summarize(rows: list[dict]) -> dict:
    trades = sorted([row for row in rows if row.get("filled")],
                    key=lambda row: (row["entry_date"], row["stock_code"]))
    values = [row["net_r"] for row in trades]
    gains, losses = sum(max(x, 0) for x in values), -sum(min(x, 0) for x in values)
    equity = peak = drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return {
        "opportunities": len(rows), "fills": len(trades),
        "symbols_with_fills": len({row["stock_code"] for row in trades}),
        "mean_net_r": sum(values) / len(values) if values else None,
        "net_r_per_opportunity": sum(values) / len(rows) if rows else None,
        "profit_factor": gains / losses if losses else None,
        "no_losses": bool(values) and losses == 0 and gains > 0,
        "win_rate_pct": sum(x > 0 for x in values) / len(values) * 100 if values else None,
        "max_drawdown_r": drawdown,
        "drawdown_method": "synthetic equal-risk trade sequence ordered by entry_date,stock_code; not portfolio NAV",
        "reasons": dict(Counter(row["reason"] for row in rows)),
    }


def acceptance(base: dict, stress: dict, legacy: dict) -> dict:
    checks = {
        "minimum_30_fills": base["fills"] >= 30,
        "minimum_5_symbols": base["symbols_with_fills"] >= 5,
        "positive_mean_net_r": base["mean_net_r"] is not None and base["mean_net_r"] > 0,
        "profit_factor_above_one": base["no_losses"] or (base["profit_factor"] is not None and base["profit_factor"] > 1),
        "stress_mean_nonnegative": stress["mean_net_r"] is not None and stress["mean_net_r"] >= 0,
        "max_drawdown_at_most_6r": base["max_drawdown_r"] <= 6,
        "not_worse_per_opportunity": base["net_r_per_opportunity"] is not None and
        legacy["net_r_per_opportunity"] is not None and base["net_r_per_opportunity"] >= legacy["net_r_per_opportunity"],
    }
    return {"passed": all(checks.values()), "checks": checks,
            "baseline_small_sample": legacy["fills"] < 30}


def legacy_allows(plan: dict, frame: pd.DataFrame) -> bool:
    """Frozen old execution gate, on the same candidate/stop. Not old LLM scores."""
    from src.stock_analyzer import StockTrendAnalyzer
    if not plan.get("hard_stop"):
        return False
    old = StockTrendAnalyzer().analyze(frame.copy(), plan["stock_code"])
    current, stop = plan["signal_close"], plan["hard_stop"]
    above = [x for x in old.resistance_levels if x >= current]
    target = min(above) if above else min(old.resistance_levels) if old.resistance_levels else None
    if target is None or stop >= current:
        return False
    support = max(old.support_levels) if old.support_levels else None
    near_support = bool(support and abs(current-support)/support <= .03)
    near_resistance = not near_support and abs(target - current) / target <= .03
    return not near_resistance and (target - current) / (current - stop) >= 1.5


def replay(frame: pd.DataFrame, *, code: str, source: str, adjustment: str) -> list[dict]:
    rows = []
    cutoff = 120 + int((len(frame) - 120) * .7)
    busy_until = {}
    for i in range(120, len(frame) - 20):
        split = "holdout" if i >= cutoff else "development"
        if split == "development" and i + 20 >= cutoff:
            continue  # purge overlapping outcome windows at split boundary
        next_session = get_next_trading_date("us", frame.iloc[i]["date"])
        plan = build_swing_trade_plan(frame.iloc[:i + 1], stock_code=code, source=source,
                                     adjustment=adjustment, next_session=next_session, enabled_setups=SETUPS)
        if not plan.get("setup_type"):
            continue
        old_allows = legacy_allows(plan, frame.iloc[:i+1])
        for horizon in (5, 10, 20):
            for policy in ("legacy_gate", "daily_swing_v1"):
                key = (split, horizon, policy)
                context = {"stock_code": code, "signal_date": plan["signal_date"], "setup_type": plan["setup_type"],
                           "split": split, "policy": policy, "plan_version": PLAN_VERSION,
                           "snapshot_hash": plan.get("snapshot_hash"), "entry_low": plan.get("entry_low"),
                           "max_entry_price": plan.get("max_entry_price"), "hard_stop": plan.get("hard_stop"),
                           "risk_budget_fraction": plan.get("risk_budget_fraction"), "target_price": plan.get("target_price")}
                if not plan.get("hard_stop"):
                    result = {"filled": False, "net_r": 0.0, "reason": plan["reason"], "horizon": horizon}
                elif i < busy_until.get(key, -1):
                    result = {"filled": False, "net_r": 0.0, "reason": "existing_position", "horizon": horizon}
                elif policy == "legacy_gate" and not old_allows:
                    result = {"filled": False, "net_r": 0.0, "reason": "legacy_gate_wait", "horizon": horizon}
                else:
                    result = BacktestEngine.evaluate_swing_plan(plan=plan, history=frame, signal_index=i, horizon=horizon)
                    if result["filled"]:
                        busy_until[key] = result["exit_index"]
                for cost in (10, 20):
                    adjusted = dict(result)
                    if adjusted["filled"]:
                        adjusted["net_r"] = adjusted["gross_r"] - (adjusted["entry_price"] + adjusted["exit_price"]) * cost / 10000 / adjusted["initial_risk"]
                    rows.append({**context, **adjusted, "cost_bps": cost})
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fetch", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parents[1]
    protocol = {"warmup_bars": 120, "development_fraction": .7, "purge_bars": 20,
                "horizons": [5, 10, 20], "primary_horizon": 10, "cost_bps_per_side": [10, 20],
                "min_fills": 30, "min_symbols": 5, "max_drawdown_r": 6,
                "risk_fractions": {"trend_pullback": .25, "initial_breakout": .25, "bottom_reversal": .125},
                "code_sha256": {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in
                    ("scripts/replay_swing_entry_plans.py", "src/services/swing_trade_plan.py",
                     "src/core/backtest_engine.py", "src/agent/tools/analysis_tools.py")}}
    (args.output / "protocol.json").write_text(json.dumps(protocol, indent=2))
    manifest_path = args.output / "snapshots.json"
    if args.fetch:
        if manifest_path.exists():
            parser.error("snapshot already frozen; use a new output directory to refetch")
        from src.services.history_loader import load_history_df
        from src.core.trading_calendar import build_market_phase_context
        phase = build_market_phase_context(market="us")
        if phase.warnings:
            raise RuntimeError(f"calendar unavailable: {phase.warnings}")
        manifest = {}
        for code in SYMBOLS:
            print(f"FETCH {code}", flush=True)
            try:
                frame, source = load_history_df(code, days=365, force_refresh=True)
                adjustment = frame.attrs.get("price_adjustment", "unknown")
                frame = frame.loc[pd.to_datetime(frame["date"]).dt.date <= phase.effective_daily_bar_date].copy()
                path = args.output / f"{code}.csv"
                frame.to_csv(path, index=False)
                manifest[code] = {"source": source, "adjustment": adjustment,
                                  "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                  "fetched_at": datetime.now(timezone.utc).isoformat()}
            except Exception as exc:
                manifest[code] = {"error": str(exc)}
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    manifest = json.loads(manifest_path.read_text())
    audit, all_rows = {}, []
    for code in SYMBOLS:
        try:
            info = manifest[code]
            if info.get("error"):
                raise ValueError(info["error"])
            path = args.output / f"{code}.csv"
            if hashlib.sha256(path.read_bytes()).hexdigest() != info["sha256"]:
                raise ValueError("snapshot_checksum_changed")
            frame = validate_daily_frame(pd.read_csv(path), minimum=160)
            if info["adjustment"] == "unknown":
                raise ValueError("adjustment_unverified")
            expected = get_trading_dates("us", frame.iloc[0]["date"], frame.iloc[-1]["date"])
            if expected is None:
                raise ValueError("calendar_unavailable")
            missing = sorted(set(expected.date) - set(frame["date"]))
            extra = sorted(set(frame["date"]) - set(expected.date))
            if missing or extra:
                raise ValueError(f"calendar_gaps={len(missing)},non_sessions={len(extra)}")
            audit[code] = {**info, "valid": True, "bars": len(frame), "start": str(frame.iloc[0]["date"]),
                           "end": str(frame.iloc[-1]["date"]), "calendar_gaps": 0}
            print(f"REPLAY {code} {len(frame)} bars", flush=True)
            all_rows.extend(replay(frame, code=code, source=info["source"], adjustment=info["adjustment"]))
        except Exception as exc:
            audit[code] = {"valid": False, "error": str(exc)}
            print(f"UNAVAILABLE {code}: {exc}", flush=True)
    valid_count = sum(item["valid"] for item in audit.values())
    groups, approvals = {}, {}
    for setup in SETUPS:
        for split in ("development", "holdout"):
            for horizon in (5, 10, 20):
                for policy in ("legacy_gate", "daily_swing_v1"):
                    for cost in (10, 20):
                        key = f"{setup}/{split}/{horizon}/{policy}/{cost}"
                        groups[key] = summarize([row for row in all_rows if row["setup_type"] == setup and
                                                row["split"] == split and row["horizon"] == horizon and
                                                row["policy"] == policy and row["cost_bps"] == cost])
        prefix = f"{setup}/holdout/10/"
        approvals[setup] = acceptance(groups[prefix + "daily_swing_v1/10"],
                                     groups[prefix + "daily_swing_v1/20"], groups[prefix + "legacy_gate/10"])
        approvals[setup]["checks"]["minimum_10_valid_symbols"] = valid_count >= 10
        approvals[setup]["passed"] &= valid_count >= 10
    result = {"version": PLAN_VERSION, "valid_symbols": valid_count, "audit": audit,
              "acceptance": approvals, "groups": groups,
              "limitations": ["技术首仓执行层；未重建历史13项AI评分", "旧执行门控使用原MA/20日高点逻辑；注入同一PA止损和成交窗口作为控制变量", "当前自选池存在选择偏差",
                              "现取复权历史不是完整点时数据库", "费用为假设；非实盘收益或组合NAV回撤",
                              "只模拟首仓及结构跟踪，不验证金字塔加仓", "事件风险未回放，不能宣称事件已排除"]}
    (args.output / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    (args.output / "trades.json").write_text(json.dumps(all_rows, ensure_ascii=False, indent=2, allow_nan=False))
    flat = [{k: v for k, v in row.items() if k != "stop_path"} for row in all_rows]
    pd.DataFrame(flat).to_csv(args.output / "trades.csv", index=False)
    lines = ["# 日线波段首仓执行层回放", "", f"有效股票：{valid_count}/12；完整验收要求至少10只。", "",
             "| 类型 | 样本外成交 | 涉及股票 | 平均净R | 盈利因子 | 最大回撤R | 启用验收 |",
             "|---|---:|---:|---:|---:|---:|---|"]
    for setup in SETUPS:
        s = groups[f"{setup}/holdout/10/daily_swing_v1/10"]
        lines.append(f"| {setup} | {s['fills']} | {s['symbols_with_fills']} | {s['mean_net_r']} | {s['profit_factor']} | {s['max_drawdown_r']} | {approvals[setup]['passed']} |")
    lines.extend(["", "## 数据与边界", "", *[f"- {x}" for x in result["limitations"]], "",
                  "阈值冻结，不按结果调参；未来新样本到齐后重新验收。逐笔与数据审计见同目录JSON/CSV。"])
    lines.extend(["", "## 逐股数据审计与样本外10日结果", "",
                  "| 股票 | 来源 / 复权 | 日期 | 根数 | 成交 | 平均净R | 序列回撤R |", "|---|---|---|---:|---:|---:|---:|"])
    for code in SYMBOLS:
        info = audit[code]
        sample = summarize([row for row in all_rows if row["stock_code"] == code and row["split"] == "holdout"
                            and row["policy"] == "daily_swing_v1" and row["horizon"] == 10 and row["cost_bps"] == 10])
        lines.append(f"| {code} | {info.get('source', info.get('error'))} / {info.get('adjustment', '—')} | "
                     f"{info.get('start', '—')} → {info.get('end', '—')} | {info.get('bars', 0)} | "
                     f"{sample['fills']} | {sample['mean_net_r']} | {sample['max_drawdown_r']} |")
    (args.output / "report.md").write_text("\n".join(lines) + "\n")
    print(json.dumps({"valid_symbols": valid_count, "acceptance": approvals, "output": str(args.output)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
