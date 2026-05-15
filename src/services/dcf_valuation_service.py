# -*- coding: utf-8 -*-
"""
DCF Valuation Service
=====================

提供轻量 DCF 估值（Bull / Base / Bear 三情景），用于问股场景的“可计算估值区间”。

设计目标：
- fail-open：返回结构化状态，不抛异常阻塞主流程
- 跨市场可用：A/H/US 统一走 yfinance 符号映射
- 参数保守：默认 5 年预测，终值永续增长率约束在折现率以下
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 1800


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _detect_market(stock_code: str) -> str:
    code = (stock_code or "").strip().upper()
    if not code:
        return "unknown"
    if code.startswith("HK") or code.endswith(".HK") or (code.isdigit() and len(code) == 5):
        return "hk"
    if code.isdigit() and len(code) == 6:
        return "cn"
    if code.replace(".", "").isalpha() and 1 <= len(code.replace(".", "")) <= 6:
        return "us"
    return "unknown"


def _to_yf_symbol(stock_code: str) -> str:
    """
    将项目常见股票代码转换为 yfinance 可识别 symbol。
    """
    code = (stock_code or "").strip().upper()
    if not code:
        return code

    if code.endswith(".US"):
        return code[:-3]
    if code.startswith("HK"):
        digits = code[2:].lstrip("0") or "0"
        return f"{digits.zfill(4)}.HK"
    if code.endswith(".HK"):
        return code

    if code.isdigit() and len(code) == 6:
        if code.startswith(("6", "9")):
            return f"{code}.SS"
        if code.startswith(("0", "2", "3")):
            return f"{code}.SZ"
        if code.startswith(("8", "4")) or code.startswith("920"):
            return f"{code}.BJ"
        return f"{code}.SZ"

    return code


def _normalize_growth(raw: Optional[float], default: float = 0.12) -> float:
    if raw is None:
        return default
    growth = raw
    # 兼容部分数据源返回百分数（例如 15 代表 15%）
    if abs(growth) > 1.0 and abs(growth) <= 100.0:
        growth = growth / 100.0
    return _clamp(growth, -0.15, 0.35)


def _extract_series_value(df: Any, candidate_names: Tuple[str, ...]) -> Optional[float]:
    """
    从财报 DataFrame 中提取候选行的“最新有效值”。
    """
    try:
        import pandas as pd  # noqa: WPS433
    except Exception:
        return None

    if not isinstance(df, pd.DataFrame) or df.empty:
        return None

    normalized_map: Dict[str, Any] = {}
    for idx in df.index:
        key = str(idx).strip().lower().replace("_", " ").replace("-", " ")
        key = " ".join(key.split())
        normalized_map[key] = idx

    for name in candidate_names:
        target = " ".join(name.strip().lower().replace("_", " ").replace("-", " ").split())
        matched = None
        if target in normalized_map:
            matched = normalized_map[target]
        else:
            for norm_name, origin in normalized_map.items():
                if target in norm_name:
                    matched = origin
                    break
        if matched is None:
            continue

        row = df.loc[matched]
        if hasattr(row, "values"):
            values = [_to_float(v) for v in list(row.values)]
        else:
            values = [_to_float(row)]
        for val in values:
            if val is not None:
                return val

    return None


@dataclass
class _ScenarioInput:
    growth_rate: float
    discount_rate: float
    terminal_growth_rate: float


def _compute_dcf(
    fcf0: float,
    years: int,
    scenario: _ScenarioInput,
) -> Dict[str, Any]:
    growth = scenario.growth_rate
    discount = scenario.discount_rate
    terminal_growth = scenario.terminal_growth_rate

    # 保证终值分母有效，避免无穷值
    if discount <= terminal_growth + 0.005:
        discount = terminal_growth + 0.005

    projected_fcf = []
    pv_sum = 0.0
    fcf = fcf0
    for year in range(1, years + 1):
        fcf = fcf * (1.0 + growth)
        pv = fcf / ((1.0 + discount) ** year)
        projected_fcf.append({"year": year, "fcf": round(fcf, 2), "pv": round(pv, 2)})
        pv_sum += pv

    terminal_fcf = fcf * (1.0 + terminal_growth)
    terminal_value = terminal_fcf / (discount - terminal_growth)
    terminal_pv = terminal_value / ((1.0 + discount) ** years)
    enterprise_value = pv_sum + terminal_pv

    return {
        "growth_rate": round(growth, 4),
        "discount_rate": round(discount, 4),
        "terminal_growth_rate": round(terminal_growth, 4),
        "pv_explicit_period": round(pv_sum, 2),
        "terminal_value": round(terminal_value, 2),
        "terminal_pv": round(terminal_pv, 2),
        "enterprise_value": round(enterprise_value, 2),
        "projected_fcf": projected_fcf,
    }


class DCFValuationService:
    """
    轻量 DCF 估值服务（跨市场，fail-open）。
    """

    def __init__(self) -> None:
        self._cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}

    def get_dcf_valuation(
        self,
        stock_code: str,
        forecast_years: int = 5,
    ) -> Dict[str, Any]:
        forecast_years = max(3, min(int(forecast_years or 5), 10))
        code = (stock_code or "").strip().upper()
        market = _detect_market(code)
        yf_symbol = _to_yf_symbol(code)
        cache_key = f"{code}|{forecast_years}"

        now = time.monotonic()
        cached = self._cache.get(cache_key)
        if cached and (now - cached[0]) < _CACHE_TTL_SECONDS:
            return cached[1]

        try:
            import yfinance as yf  # noqa: WPS433
        except Exception as exc:
            return {
                "status": "error",
                "stock_code": code,
                "market": market,
                "note": f"yfinance unavailable: {exc}",
            }

        try:
            ticker = yf.Ticker(yf_symbol)
            info = ticker.info or {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("dcf valuation info fetch failed for %s: %s", code, exc)
            return {
                "status": "error",
                "stock_code": code,
                "market": market,
                "yf_symbol": yf_symbol,
                "note": f"yfinance info fetch failed: {exc}",
            }

        current_price = _to_float(
            info.get("currentPrice")
            or info.get("regularMarketPrice")
            or info.get("previousClose")
        )
        if current_price is None:
            try:
                hist = ticker.history(period="5d")
                if hist is not None and not hist.empty:
                    current_price = _to_float(hist["Close"].iloc[-1])
            except Exception:
                current_price = None

        market_cap = _to_float(info.get("marketCap"))
        shares_outstanding = _to_float(info.get("sharesOutstanding") or info.get("impliedSharesOutstanding"))
        if shares_outstanding is None and market_cap and current_price and current_price > 0:
            shares_outstanding = market_cap / current_price

        growth_hint = _normalize_growth(
            _to_float(info.get("revenueGrowth"))
            or _to_float(info.get("earningsGrowth")),
            default=0.12,
        )
        beta = _to_float(info.get("beta"))
        base_discount = 0.10
        if beta is not None and beta > 0:
            base_discount = _clamp(0.045 + beta * 0.05, 0.07, 0.18)

        # 提取 FCF：优先 info.freeCashflow，再尝试 cashflow 表。
        fcf0 = _to_float(info.get("freeCashflow"))
        fcf_source = "info.freeCashflow" if fcf0 is not None else "unknown"
        if fcf0 is None:
            try:
                cashflow_df = ticker.cashflow
            except Exception:
                cashflow_df = None
            fcf0 = _extract_series_value(cashflow_df, ("Free Cash Flow",))
            if fcf0 is not None:
                fcf_source = "cashflow.free_cash_flow"
            else:
                op_cash = _extract_series_value(cashflow_df, ("Operating Cash Flow", "Net Cash From Operating Activities"))
                capex = _extract_series_value(cashflow_df, ("Capital Expenditure",))
                if op_cash is not None and capex is not None:
                    # yfinance 常见 capex 为负值：FCF = OCF + CapEx
                    fcf0 = op_cash + capex
                    fcf_source = "cashflow.ocf_plus_capex"

        estimated = False
        if (fcf0 is None or fcf0 <= 0) and market_cap is not None:
            trailing_pe = _to_float(info.get("trailingPE"))
            if trailing_pe is not None and trailing_pe > 0:
                # 极简 fallback：用净利润近似估算 FCF（折减系数 0.7）
                fcf0 = (market_cap / trailing_pe) * 0.7
                fcf_source = "estimated_from_market_cap_and_pe"
                estimated = True

        if fcf0 is None or fcf0 <= 0:
            result = {
                "status": "unavailable",
                "stock_code": code,
                "market": market,
                "yf_symbol": yf_symbol,
                "current_price": current_price,
                "note": "无法获取有效自由现金流（FCF<=0 或缺失），无法构建可靠 DCF。",
            }
            self._cache[cache_key] = (now, result)
            return result

        base_terminal_growth = 0.03
        scenarios = {
            "bear": _ScenarioInput(
                growth_rate=max(-0.05, growth_hint - 0.07),
                discount_rate=min(0.22, base_discount + 0.015),
                terminal_growth_rate=max(0.01, base_terminal_growth - 0.005),
            ),
            "base": _ScenarioInput(
                growth_rate=growth_hint,
                discount_rate=base_discount,
                terminal_growth_rate=base_terminal_growth,
            ),
            "bull": _ScenarioInput(
                growth_rate=min(0.45, growth_hint + 0.07),
                discount_rate=max(0.06, base_discount - 0.01),
                terminal_growth_rate=min(0.04, base_terminal_growth + 0.005),
            ),
        }

        cash = _to_float(info.get("totalCash")) or 0.0
        debt = _to_float(info.get("totalDebt")) or 0.0
        net_cash = cash - debt

        scenario_results: Dict[str, Dict[str, Any]] = {}
        for name, scenario_input in scenarios.items():
            dcf_block = _compute_dcf(fcf0, forecast_years, scenario_input)
            enterprise_value = _to_float(dcf_block.get("enterprise_value")) or 0.0
            equity_value = enterprise_value + net_cash
            intrinsic_price = None
            upside_pct = None
            if shares_outstanding and shares_outstanding > 0:
                intrinsic_price = equity_value / shares_outstanding
                if current_price and current_price > 0:
                    upside_pct = (intrinsic_price / current_price - 1.0) * 100.0
            scenario_results[name] = {
                **dcf_block,
                "equity_value": round(equity_value, 2),
                "intrinsic_price": round(intrinsic_price, 2) if intrinsic_price is not None else None,
                "upside_pct": round(upside_pct, 2) if upside_pct is not None else None,
            }

        base_upside = scenario_results["base"].get("upside_pct")
        if base_upside is None:
            valuation_signal = "insufficient_data"
        elif base_upside >= 20:
            valuation_signal = "undervalued"
        elif base_upside <= -20:
            valuation_signal = "overvalued"
        else:
            valuation_signal = "fair"

        result = {
            "status": "ok" if not estimated else "partial",
            "stock_code": code,
            "market": market,
            "yf_symbol": yf_symbol,
            "currency": info.get("currency"),
            "current_price": current_price,
            "market_cap": market_cap,
            "shares_outstanding": round(shares_outstanding, 0) if shares_outstanding else None,
            "fcf0": round(fcf0, 2),
            "fcf_source": fcf_source,
            "forecast_years": forecast_years,
            "assumptions": {
                "base_growth_hint": round(growth_hint, 4),
                "base_discount_rate": round(base_discount, 4),
                "base_terminal_growth_rate": base_terminal_growth,
                "net_cash": round(net_cash, 2),
            },
            "scenarios": scenario_results,
            "valuation_signal": valuation_signal,
            "note": (
                "部分估值输入为估算值，建议与财报口径核对。"
                if estimated
                else "估值由 yfinance 财务数据驱动，适合做区间参考。"
            ),
            "source": "yfinance",
        }
        self._cache[cache_key] = (now, result)
        return result
