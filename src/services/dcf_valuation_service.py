# -*- coding: utf-8 -*-
"""
DCF Valuation Service
=====================

提供轻量 DCF 估值（Bull / Base / Bear 三情景），用于问股场景的“可计算估值区间”。

真实数据缺失时停止计算；数据齐全后仅提供有明确假设的敏感性演示，
不把供应商 FCF 代理和自动参数当作已核验的内在价值。
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 1800


def _to_float(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _stock_identity(stock_code: str):
    """Reuse the shared identity parser; adapt only provider US notation."""
    from src.services.stock_code_utils import resolve_daily_stock_identity

    code = (stock_code or "").strip().upper()
    explicit_us = code.startswith("US.") or code.endswith(".US")
    code = code.removeprefix("US.").removesuffix(".US")
    # The shared canonical share-class syntax uses a dot, Yahoo uses a dash.
    identity = resolve_daily_stock_identity(code.replace("-", "."))
    if explicit_us and identity is not None and identity.market != "us":
        return None
    return identity


def _detect_market(stock_code: str) -> str:
    identity = _stock_identity(stock_code)
    return identity.market if identity else "unknown"


def _to_yf_symbol(stock_code: str) -> str:
    """Map a validated shared market identity to Yahoo's spelling."""
    from src.services.stock_code_utils import _infer_cn_exchange

    identity = _stock_identity(stock_code)
    if identity is None:
        return ""
    code = identity.normalized_code
    if identity.market == "cn":
        exchange = _infer_cn_exchange(code)
        return f"{code}.{'SS' if exchange == 'SH' else exchange}"
    if identity.market == "hk":
        return f"{int(code):04d}.HK"
    if identity.market == "us":
        return code.replace(".", "-")
    return identity.refill_code


def _normalize_growth(raw: Optional[float], default: float = 0.0) -> float:
    """Yahoo growth fields are decimal ratios, not percent-unit observations."""
    return _clamp(raw if raw is not None else default, -0.15, 0.15)


def _statement_fcf(df: Any) -> Dict[str, Any]:
    """Use one dated annual column; never combine values from different years."""
    import pandas as pd

    if not isinstance(df, pd.DataFrame) or df.empty:
        return {}
    dated_columns = []
    for column in df.columns:
        # Integers (e.g. an unlabelled RangeIndex) are not financial periods.
        if isinstance(column, (int, float)):
            continue
        date = pd.to_datetime(column, errors="coerce", utc=True)
        if pd.notna(date):
            dated_columns.append((date, column))
    dated_columns.sort(key=lambda item: item[0], reverse=True)
    if not dated_columns:
        return {}

    def value_at(column: Any, names: Tuple[str, ...]) -> Optional[float]:
        for name in names:
            if name in df.index:
                result = _to_float(df.at[name, column])
                if result is not None:
                    return result
        return None

    history = []
    for period, column in dated_columns:
        fcf = value_at(column, ("Free Cash Flow", "FreeCashFlow"))
        source = "cashflow.free_cash_flow"
        if fcf is None:
            ocf = value_at(column, ("Operating Cash Flow", "OperatingCashFlow",
                                    "Net Cash From Operating Activities"))
            capex = value_at(column, ("Capital Expenditure", "CapitalExpenditure"))
            if ocf is not None and capex is not None:
                fcf = ocf - abs(capex)
                source = "cashflow.operating_cash_flow_minus_capex"
        history.append({"period_end": period.date().isoformat(), "fcf": fcf, "source": source})
    return {"latest": history[0], "history": history}


def _timestamp(value: Any) -> Optional[str]:
    number = _to_float(value)
    if number is None or number <= 0:
        return None
    try:
        return datetime.fromtimestamp(number, timezone.utc).isoformat()
    except (ValueError, OverflowError, OSError):
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

    if any(_to_float(value) is None for value in (fcf0, growth, discount, terminal_growth)):
        raise ValueError("DCF inputs must be finite numbers")
    if fcf0 <= 0 or years < 1 or growth <= -1 or not discount > terminal_growth > -1:
        raise ValueError("DCF requires positive FCF and discount rate above terminal growth")

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
        metadata = {
            "stock_code": code, "market": market, "yf_symbol": yf_symbol,
            "source": "yfinance", "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "as_of": None, "decision_eligible": False,
        }

        if market == "unknown" or not yf_symbol:
            return {**metadata, "status": "unavailable",
                    "note": "股票身份或市场无法确认，不能猜测 Yahoo 代码进行估值。"}
        now = time.monotonic()
        cached = self._cache.get(cache_key)
        if cached and (now - cached[0]) < _CACHE_TTL_SECONDS:
            return cached[1]

        try:
            import yfinance as yf  # noqa: WPS433
        except Exception as exc:
            return {
                **metadata,
                "status": "error",
                "stock_code": code,
                "market": market,
                "note": f"yfinance unavailable: {exc}",
            }

        try:
            ticker = yf.Ticker(yf_symbol)
            info = ticker.info or {}
            if not isinstance(info, dict):
                raise ValueError("yfinance info must be a mapping")
        except Exception as exc:  # noqa: BLE001
            logger.warning("dcf valuation info fetch failed for %s: %s", code, exc)
            return {
                **metadata,
                "status": "error",
                "stock_code": code,
                "market": market,
                "yf_symbol": yf_symbol,
                "note": f"yfinance info fetch failed: {exc}",
            }

        retrieved_at = datetime.now(timezone.utc)
        current_price = _to_float(info.get("currentPrice"))
        price_source = "info.currentPrice"
        if current_price is None or current_price <= 0:
            current_price = _to_float(info.get("regularMarketPrice"))
            price_source = "info.regularMarketPrice"
        if current_price is not None and current_price <= 0:
            current_price = None
        quote_as_of = _timestamp(info.get("regularMarketTime"))
        currency = str(info.get("currency") or "").upper() or None
        financial_currency = str(info.get("financialCurrency") or "").upper() or None
        shares = _to_float(info.get("sharesOutstanding"))
        market_cap = _to_float(info.get("marketCap"))
        cash = _to_float(info.get("totalCash"))
        debt = _to_float(info.get("totalDebt"))
        warnings = [
            "自动三情景为敏感性分析，增长率/折现率/永续增长率不是公司指引或市场共识。",
            "供应商 FCF 未调节利息、净借款及股权激励，简化现金流代理不等于经财报核验的 FCFF/FCFE。",
            "现金/债务/股数来自供应商快照，具体报告期和 ADR 股数口径需另行核验。",
        ]
        try:
            statement = _statement_fcf(ticker.cashflow)
        except Exception as exc:
            logger.info("DCF annual cashflow unavailable for %s: %s", code, type(exc).__name__)
            statement = {}
        latest = statement.get("latest", {})
        fcf0 = latest.get("fcf")
        fcf_source = latest.get("source")
        financial_period_end = latest.get("period_end")
        fcf_period_type = "annual"
        # A dated latest statement with missing FCF stays missing. An older year
        # or a positive profit estimate must not overwrite a missing/negative FCF.
        if not latest:
            fcf0 = _to_float(info.get("freeCashflow"))
            fcf_source = "info.freeCashflow" if fcf0 is not None else None
            fcf_period_type = "provider_aggregate_period_unverified"

        result = {
            "status": "partial",
            "stock_code": code,
            "market": market,
            "yf_symbol": yf_symbol,
            "source": "yfinance",
            "retrieved_at": retrieved_at.isoformat(),
            "as_of": quote_as_of,
            "quote_as_of": quote_as_of,
            "price_source": price_source if current_price is not None else None,
            "financial_period_end": financial_period_end,
            "financial_period_type": fcf_period_type,
            "currency": currency,
            "financial_currency": financial_currency,
            "current_price": current_price,
            "market_cap": market_cap,
            "shares_outstanding": shares,
            "total_cash": cash,
            "total_debt": debt,
            "capital_structure_source": "yfinance.info",
            "capital_structure_period": "provider_snapshot_period_unverified",
            "fcf0": fcf0,
            "fcf_source": fcf_source,
            "fcf_history": statement.get("history", []),
            "forecast_years": forecast_years,
            "valuation_signal": "insufficient_data",
            "decision_eligible": False,
            "model_basis": "simplified_fcf_proxy_sensitivity",
            "missing_fields": [],
            "warnings": warnings,
            "scenarios": {},
        }
        missing = result["missing_fields"]
        if fcf0 is None:
            missing.append("free_cash_flow")
        if not financial_period_end:
            missing.append("financial_period_end")
        elif (retrieved_at.date() - datetime.fromisoformat(financial_period_end).date()).days not in range(0, 551):
            missing.append("current_financial_period")
        if currency is None:
            missing.append("quote_currency")
        if financial_currency is None:
            missing.append("financial_currency")
        if cash is None or cash < 0:
            missing.append("total_cash")
        if debt is None or debt < 0:
            missing.append("total_debt")
        if shares is None or shares <= 0:
            missing.append("shares_outstanding")
        if current_price is None:
            missing.append("current_price")
        if quote_as_of is None:
            missing.append("quote_as_of")
        elif not 0 <= (retrieved_at - datetime.fromisoformat(quote_as_of)).total_seconds() <= 8 * 86400:
            # Weekends/holidays are valid; stale observations are not a current comparison.
            missing.append("current_quote")

        quote_type = str(info.get("quoteType") or "").upper()
        sector = str(info.get("sector") or "").lower()
        industry = str(info.get("industry") or "").lower()
        unsupported = (quote_type and quote_type != "EQUITY") or sector == "financial services" or "reit" in industry
        if unsupported:
            result["status"] = "unavailable"
            result["note"] = "银行/保险/REIT/基金不适用此普通企业 FCF 模型，需用行业专属估值。"
        elif fcf0 is None or fcf0 <= 0:
            result["status"] = "unavailable"
            result["note"] = "自由现金流缺失或非正，不以 PE 推算利润替代；需另建可核验的转正路径。"
        elif missing or currency != financial_currency:
            if currency and financial_currency and currency != financial_currency:
                missing.append("verified_currency_conversion")
            result["note"] = "估值关键口径未齐全，暂停每股价格和高低估判断；参见 missing_fields。"
        else:
            raw_growth = _to_float(info.get("revenueGrowth"))
            growth_hint = _normalize_growth(raw_growth)
            base_discount = 0.10
            base_terminal_growth = 0.025
            scenarios = {
                "bear": _ScenarioInput(growth_hint - 0.05, base_discount + 0.02, 0.015),
                "base": _ScenarioInput(growth_hint, base_discount, base_terminal_growth),
                "bull": _ScenarioInput(growth_hint + 0.05, base_discount - 0.01, 0.03),
            }
            net_cash = cash - debt
            result["assumptions"] = {
                "assumption_status": "illustrative_unvalidated",
                "growth_source": "revenueGrowth_proxy_capped" if raw_growth is not None else "zero_growth_placeholder",
                "observed_revenue_growth": raw_growth,
                "base_growth_hint": growth_hint,
                "base_discount_rate": base_discount,
                "base_terminal_growth_rate": base_terminal_growth,
                "net_cash": net_cash,
                "net_cash_period": "provider_snapshot_period_unverified",
                "share_count_basis": "info.sharesOutstanding_unverified_dilution_or_adr_basis",
            }
            for name, scenario_input in scenarios.items():
                dcf_block = _compute_dcf(fcf0, forecast_years, scenario_input)
                equity_value = dcf_block["enterprise_value"] + net_cash
                intrinsic_price = max(equity_value, 0.0) / shares
                upside_pct = (intrinsic_price / current_price - 1.0) * 100.0
                # A price discount to value differs from upside from today's price.
                margin = (1.0 - current_price / intrinsic_price) * 100.0 if intrinsic_price > 0 else None
                result["scenarios"][name] = {
                    **dcf_block,
                    "equity_value": round(equity_value, 2),
                    "intrinsic_price": round(intrinsic_price, 2),
                    "upside_pct": round(upside_pct, 2),
                    "margin_of_safety_pct": round(margin, 2) if margin is not None else None,
                    "terminal_value_share_pct": round(dcf_block["terminal_pv"] / dcf_block["enterprise_value"] * 100, 2),
                }
            result["valuation_signal"] = "scenario_only"
            result["note"] = "真实现金流输入的自动敏感性区间；尚未核验现金流口径、资本结构和预测，不能单独认定内在价值或生成买入指令。"
        self._cache[cache_key] = (now, result)
        return result
