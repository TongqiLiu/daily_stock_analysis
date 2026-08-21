# -*- coding: utf-8 -*-
"""Read-only option-chain and option-quote tools for Ask Stock.

The tool deliberately returns explicit availability and provenance metadata.
It never fills missing Greeks, open interest, or prices with estimates.
"""

from __future__ import annotations

import math
import os
from datetime import datetime
from typing import Any, Optional

from src.agent.tools.registry import ToolDefinition, ToolParameter, ToolPolicy


_OPTIONS_POLICY = ToolPolicy.declared(
    read_only=True,
    side_effects=["network_read"],
    permissions=["market_data:read", "derivatives_data:read"],
    scope_dimensions=["stock"],
    cancellation_safe=True,
)


def _clean_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _finite_number(value: Any) -> Optional[float | int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return int(number) if number.is_integer() else number


def _value(item: Any, *names: str) -> Any:
    if isinstance(item, dict):
        for name in names:
            if name in item:
                return item[name]
        return None
    for name in names:
        value = getattr(item, name, None)
        if value is not None:
            return value
    return None


def _records(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, tuple):
        return [item for item in value if item is not None]
    if isinstance(value, list):
        return value
    try:
        return list(value)
    except TypeError:
        return [value]


def _serialize_option_quote(item: Any) -> dict[str, Any]:
    fields = {
        "symbol": ("symbol", "code"),
        "underlying_symbol": ("underlying_symbol", "underlying"),
        "expiry_date": ("expiry_date", "strike_time", "strike_date"),
        "strike_price": ("strike_price", "strike"),
        "direction": ("direction", "option_type"),
        "last_price": ("last_done", "last_price", "price"),
        "bid_price": ("bid_price", "bid"),
        "ask_price": ("ask_price", "ask"),
        "volume": ("volume",),
        "turnover": ("turnover",),
        "open_interest": ("open_interest",),
        "contract_multiplier": ("contract_multiplier", "contract_size"),
        "implied_volatility": ("implied_volatility", "iv"),
        "historical_volatility": ("historical_volatility", "history_volatility", "hv"),
        "delta": ("delta",),
        "gamma": ("gamma",),
        "vega": ("vega",),
        "theta": ("theta",),
        "rho": ("rho",),
        "timestamp": ("timestamp", "updated_at"),
    }
    result: dict[str, Any] = {}
    for target, source_names in fields.items():
        raw = _value(item, *source_names)
        if target in {"symbol", "underlying_symbol", "expiry_date", "direction", "timestamp"}:
            result[target] = _clean_text(raw)
        else:
            result[target] = _finite_number(raw)
    return result


def _serialize_chain_row(item: Any) -> dict[str, Any]:
    result = {
        "call_symbol": _clean_text(_value(item, "call_symbol", "call_code")),
        "put_symbol": _clean_text(_value(item, "put_symbol", "put_code")),
        "strike_price": _finite_number(_value(item, "strike_price", "strike")),
        "expiry_date": _clean_text(_value(item, "expiry_date", "strike_date", "date")),
    }
    return result


def _futu_option_quote_records(ctx: Any, chain_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fetch provider Greeks/quotes for Futu option-chain contracts.

    ``get_option_chain`` returns contract metadata only.  Futu exposes the
    live quote and Greeks through ``get_option_quote`` and requires
    ``OptionStrategyLeg`` objects, so keep that SDK-specific conversion here
    rather than leaking it into the report layer.
    """
    codes = [
        _clean_text(item.get("code"))
        for item in chain_records
        if _clean_text(item.get("code"))
    ]
    if not codes:
        return []
    if not hasattr(ctx, "get_option_quote"):
        raise RuntimeError("当前 futu-api/OpenD 未提供 get_option_quote")

    from futu import OptionStrategyLeg, RET_OK

    quotes = []
    # Futu treats multiple legs as one combo and may return one aggregate row.
    # Query one contract at a time so every quote/Greek remains attributable.
    # The OpenD endpoint is limited to 30 requests per 30 seconds.
    for code in codes[:30]:
        leg = OptionStrategyLeg()
        leg.code = code
        leg.action = "BUY"
        leg.quantity = 1.0
        ret, data = ctx.get_option_quote([leg])
        if ret != RET_OK:
            continue
        raw_quotes = data.to_dict(orient="records") if hasattr(data, "to_dict") else []
        if not raw_quotes:
            continue
        enriched = dict(raw_quotes[0])
        # The Futu quote response is keyed by the request and does not
        # include the contract code in all SDK versions.
        enriched.setdefault("code", code)
        quotes.append(_serialize_option_quote(enriched))
    return [item for item in quotes if item.get("symbol")]


def _option_unavailable(
    *, stock_code: str, source: str, reason: str, error: Optional[str] = None
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": "unavailable",
        "stock_code": stock_code,
        "source": source,
        "as_of": None,
        "data_quality": {
            "permission": "unknown",
            "freshness": "unknown",
            "chain": "missing",
            "greeks": "missing",
            "open_interest": "missing",
        },
        "reason": reason,
    }
    if error:
        result["error"] = error
    return result


def _parse_futu_strategy_legs(legs_json: str) -> tuple[list[Any], list[dict[str, Any]]]:
    """Parse a read-only strategy leg payload for Futu's combo analysis API."""
    import json

    try:
        items = json.loads(legs_json) if isinstance(legs_json, str) else legs_json
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"legs_json 不是有效 JSON：{exc}") from exc
    if not isinstance(items, list) or not items:
        raise ValueError("legs_json 必须是非空数组")
    if len(items) > 4:
        raise ValueError("Futu 组合分析最多支持 4 条期权腿")

    from futu import OptionStrategyLeg

    legs = []
    normalized = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("每条期权腿必须是对象")
        code = _clean_text(item.get("code"))
        action = _clean_text(item.get("action") or "BUY")
        if not code or "." not in code:
            raise ValueError("每条期权腿必须提供包含市场前缀的 code，例如 US.AAPL260918C200000")
        if action.upper() not in {"BUY", "SELL"}:
            raise ValueError("期权腿 action 只能是 BUY 或 SELL")
        quantity = _finite_number(item.get("quantity", 1.0))
        if quantity is None or float(quantity) <= 0:
            raise ValueError("期权腿 quantity 必须是正数")

        leg = OptionStrategyLeg()
        leg.code = code.upper()
        leg.action = action.upper()
        leg.quantity = float(quantity)
        legs.append(leg)
        normalized.append({"code": leg.code, "action": leg.action, "quantity": float(quantity)})
    return legs, normalized


def _serialize_strategy_analysis(item: Any) -> dict[str, Any]:
    """Preserve only fields returned by Futu's combo analysis endpoint."""
    result: dict[str, Any] = {}
    for field in ("code", "name", "option_strategy"):
        result[field] = _clean_text(_value(item, field))
    for field in (
        "bid1", "ask1", "max_profit", "max_loss", "prob_of_profit", "delta", "theta"
    ):
        result[field] = _finite_number(_value(item, field))
    raw_break_even = _value(item, "breakeven_points")
    if isinstance(raw_break_even, (list, tuple)):
        result["breakeven_points"] = [
            _finite_number(value) for value in raw_break_even
            if _finite_number(value) is not None
        ]
    else:
        result["breakeven_points"] = None
    return result


def _handle_get_option_strategy_analysis(
    legs_json: str,
    stock_code: Optional[str] = None,
) -> dict[str, Any]:
    """Run Futu's provider-side combo P/L analysis without placing an order."""
    if str(os.getenv("FUTU_ENABLED", "")).strip().lower() not in {"1", "true", "yes", "on"}:
        return _option_unavailable(
            stock_code=stock_code or "unknown",
            source="futu",
            reason="FUTU_ENABLED 未开启",
        )
    try:
        from futu import OpenQuoteContext, RET_OK
    except ImportError as exc:
        return _option_unavailable(
            stock_code=stock_code or "unknown",
            source="futu",
            reason="未安装 futu-api",
            error=str(exc),
        )

    ctx = None
    try:
        legs, normalized_legs = _parse_futu_strategy_legs(legs_json)
        ctx = OpenQuoteContext(
            host=os.getenv("FUTU_OPEND_HOST", "127.0.0.1"),
            port=int(os.getenv("FUTU_OPEND_PORT", "11111")),
        )
        if not hasattr(ctx, "get_option_strategy_analysis"):
            raise RuntimeError("当前 futu-api/OpenD 未提供 get_option_strategy_analysis")
        ret, data = ctx.get_option_strategy_analysis(legs)
        if ret != RET_OK:
            return _option_unavailable(
                stock_code=stock_code or "unknown",
                source="futu",
                reason="Futu 期权策略组合分析请求失败",
                error=str(data),
            )
        raw_records = data.to_dict(orient="records") if hasattr(data, "to_dict") else []
        analysis = [_serialize_strategy_analysis(item) for item in raw_records]
        if not analysis:
            return _option_unavailable(
                stock_code=stock_code or "unknown",
                source="futu",
                reason="Futu 期权策略组合分析返回为空",
            )
        return {
            "status": "ok",
            "stock_code": stock_code,
            "source": "futu",
            "as_of": datetime.now().astimezone().isoformat(),
            "legs": normalized_legs,
            "analysis": analysis,
            "data_quality": {
                "permission": "available",
                "freshness": "live_request",
                "strategy_analysis": "complete",
            },
            "reason": None,
        }
    except ValueError as exc:
        return {
            "status": "invalid",
            "stock_code": stock_code,
            "source": "futu",
            "reason": str(exc),
        }
    except Exception as exc:
        return _option_unavailable(
            stock_code=stock_code or "unknown",
            source="futu",
            reason="Futu OpenD 期权策略组合分析失败",
            error=str(exc),
        )
    finally:
        if ctx is not None:
            try:
                ctx.close()
            except Exception:
                pass


def _normalize_stock_for_longbridge(stock_code: str) -> Optional[str]:
    from data_provider.longbridge_fetcher import _to_longbridge_symbol

    return _to_longbridge_symbol(stock_code)


def _get_longbridge_snapshot(
    stock_code: str,
    expiry_date: Optional[str],
    max_contracts: int,
) -> dict[str, Any]:
    try:
        from data_provider.longbridge_fetcher import LongbridgeFetcher

        symbol = _normalize_stock_for_longbridge(stock_code)
        if not symbol:
            return _option_unavailable(
                stock_code=stock_code,
                source="longbridge",
                reason="无法将标的转换为 Longbridge 代码",
            )
        fetcher = LongbridgeFetcher()
        if not fetcher.has_configured_credentials():
            return _option_unavailable(
                stock_code=stock_code,
                source="longbridge",
                reason="未配置 Longbridge API 凭证或 OAuth token",
            )
        ctx = fetcher._get_ctx()
        if ctx is None:
            return _option_unavailable(
                stock_code=stock_code,
                source="longbridge",
                reason="Longbridge QuoteContext 未建立，可能是凭证、区域或权限问题",
            )

        expiries = [_clean_text(item) for item in _records(ctx.option_chain_expiry_date_list(symbol))]
        expiries = [item for item in expiries if item]
        if not expiry_date:
            return {
                "status": "partial" if expiries else "unavailable",
                "stock_code": stock_code,
                "source": "longbridge",
                "as_of": datetime.now().astimezone().isoformat(),
                "expiry_dates": expiries,
                "chain": [],
                "quotes": [],
                "data_quality": {
                    "permission": "available",
                    "freshness": "live_request",
                    "chain": "expiry_only",
                    "greeks": "missing",
                    "open_interest": "missing",
                },
                "reason": "未指定到期日，仅返回可用到期日列表",
            }

        chain_items = _records(ctx.option_chain_info_by_date(symbol, expiry_date))
        chain = [_serialize_chain_row(item) for item in chain_items]
        option_symbols = []
        for item in chain:
            for key in ("call_symbol", "put_symbol"):
                if item.get(key) and item[key] not in option_symbols:
                    option_symbols.append(item[key])
        option_symbols = option_symbols[: max_contracts * 2]
        quotes = [_serialize_option_quote(item) for item in _records(ctx.option_quote(option_symbols))]
        quotes = [item for item in quotes if item.get("symbol")]
        has_greeks = any(item.get("gamma") is not None or item.get("delta") is not None for item in quotes)
        has_oi = any(item.get("open_interest") is not None for item in quotes)
        status = "ok" if chain and quotes else ("partial" if chain else "unavailable")
        return {
            "status": status,
            "stock_code": stock_code,
            "source": "longbridge",
            "as_of": datetime.now().astimezone().isoformat(),
            "expiry_date": expiry_date,
            "expiry_dates": expiries,
            "chain": chain[:max_contracts],
            "quotes": quotes,
            "data_quality": {
                "permission": "available",
                "freshness": "live_request",
                "chain": "complete" if chain else "missing",
                "greeks": "available" if has_greeks else "missing",
                "open_interest": "available" if has_oi else "missing",
            },
            "reason": None if status == "ok" else "期权链或期权报价返回为空",
        }
    except Exception as exc:  # provider errors must be visible to the model
        return _option_unavailable(
            stock_code=stock_code,
            source="longbridge",
            reason="Longbridge 期权请求失败",
            error=str(exc),
        )


def _get_futu_snapshot(
    stock_code: str,
    expiry_date: Optional[str],
    max_contracts: int,
) -> dict[str, Any]:
    if str(os.getenv("FUTU_ENABLED", "")).strip().lower() not in {"1", "true", "yes", "on"}:
        return _option_unavailable(
            stock_code=stock_code,
            source="futu",
            reason="FUTU_ENABLED 未开启",
        )
    try:
        from futu import OpenQuoteContext, RET_OK
    except ImportError as exc:
        return _option_unavailable(
            stock_code=stock_code,
            source="futu",
            reason="未安装 futu-api",
            error=str(exc),
        )

    futu_code = stock_code.strip().upper()
    if "." not in futu_code:
        futu_code = f"US.{futu_code}"
    ctx = None
    try:
        ctx = OpenQuoteContext(
            host=os.getenv("FUTU_OPEND_HOST", "127.0.0.1"),
            port=int(os.getenv("FUTU_OPEND_PORT", "11111")),
        )
        if not expiry_date:
            ret, data = ctx.get_option_expiration_date(futu_code)
            if ret != RET_OK:
                return _option_unavailable(
                    stock_code=stock_code,
                    source="futu",
                    reason="Futu 到期日请求失败",
                    error=str(data),
                )
            expiry_values = []
            if hasattr(data, "to_dict"):
                expiry_values = [
                    _clean_text(item.get("strike_time") or item.get("expiration_date"))
                    for item in data.to_dict(orient="records")
                ]
            expiry_values = [item for item in expiry_values if item]
            return {
                "status": "partial" if expiry_values else "unavailable",
                "stock_code": stock_code,
                "source": "futu",
                "as_of": datetime.now().astimezone().isoformat(),
                "expiry_dates": expiry_values,
                "chain": [],
                "quotes": [],
                "data_quality": {
                    "permission": "available",
                    "freshness": "live_request",
                    "chain": "expiry_only",
                    "greeks": "missing",
                    "open_interest": "missing",
                },
                "reason": "未指定到期日，仅返回可用到期日列表",
            }

        ret, data = ctx.get_option_chain(futu_code, start=expiry_date, end=expiry_date)
        if ret != RET_OK:
            return _option_unavailable(
                stock_code=stock_code,
                source="futu",
                reason="Futu 期权链请求失败",
                error=str(data),
            )
        raw_records = data.to_dict(orient="records") if hasattr(data, "to_dict") else []
        records = raw_records[:max_contracts]
        quote_error = None
        try:
            quotes = _futu_option_quote_records(ctx, records)
        except Exception as exc:
            # A valid chain is still useful, but it must be marked partial;
            # never substitute chain metadata for missing live Greeks/OI.
            quotes = []
            quote_error = str(exc)
        has_greeks = any(item.get("gamma") is not None for item in quotes)
        has_oi = any(item.get("open_interest") is not None for item in quotes)
        has_quotes = bool(quotes)
        quote_complete = bool(records) and len(quotes) >= len(records)
        if quote_complete:
            reason = None
        elif quote_error:
            reason = quote_error
        elif len(records) > 30:
            reason = "Futu get_option_quote 每 30 秒最多请求 30 次，本次仅返回前 30 个合约报价"
        else:
            reason = "Futu 期权报价为空"
        return {
            "status": "ok" if records and quote_complete else ("partial" if records else "unavailable"),
            "stock_code": stock_code,
            "source": "futu",
            "as_of": datetime.now().astimezone().isoformat(),
            "expiry_date": expiry_date,
            "expiry_dates": [],
            "chain": records,
            "quotes": quotes,
            "data_quality": {
                "permission": "available",
                "freshness": "live_request",
                "chain": "complete" if records else "missing",
                "quotes": "complete" if quote_complete else ("partial" if has_quotes else "missing"),
                "greeks": "available" if has_greeks else "missing",
                "open_interest": "available" if has_oi else "missing",
            },
            "reason": reason,
        }
    except Exception as exc:
        return _option_unavailable(
            stock_code=stock_code,
            source="futu",
            reason="Futu OpenD 期权请求失败",
            error=str(exc),
        )
    finally:
        if ctx is not None:
            try:
                ctx.close()
            except Exception:
                pass


def _handle_get_option_strategy_snapshot(
    stock_code: str,
    expiry_date: Optional[str] = None,
    max_contracts: int = 40,
    source: str = "auto",
) -> dict[str, Any]:
    """Fetch real option chain/quotes; return explicit missing-data states."""
    try:
        max_contracts = max(1, min(int(max_contracts), 100))
    except (TypeError, ValueError):
        max_contracts = 40
    normalized_source = str(source or "auto").strip().lower()
    if normalized_source not in {"auto", "longbridge", "futu"}:
        return {"status": "invalid", "error": "source must be auto, longbridge, or futu"}

    providers = (
        ("longbridge", "futu")
        if normalized_source == "auto"
        else (normalized_source,)
    )
    results = {}
    for provider in providers:
        result = (
            _get_longbridge_snapshot(stock_code, expiry_date, max_contracts)
            if provider == "longbridge"
            else _get_futu_snapshot(stock_code, expiry_date, max_contracts)
        )
        results[provider] = result
        if result.get("status") in {"ok", "partial"}:
            return result

    reasons = [
        f"{provider}: {result.get('reason')}"
        for provider, result in results.items()
        if result.get("reason")
    ]
    return {
        "status": "unavailable",
        "stock_code": stock_code,
        "source": "none",
        "as_of": None,
        "providers_checked": list(providers),
        "data_quality": {
            "permission": "unavailable",
            "freshness": "unknown",
            "chain": "missing",
            "greeks": "missing",
            "open_interest": "missing",
        },
        "reason": "；".join(reasons) or "没有可用的期权数据源",
    }


get_option_strategy_snapshot_tool = ToolDefinition(
    name="get_option_strategy_snapshot",
    description=(
        "Fetch a real option-chain and option-quote snapshot for option strategy analysis. "
        "Returns source, timestamp, expiry dates, chain, quotes, IV/HV, volume, open interest, "
        "and any provider-supplied Greeks. Missing permissions or fields are returned explicitly "
        "as unavailable/missing; never infer or fabricate Gamma, PCR, OI, or option direction."
    ),
    parameters=[
        ToolParameter(
            name="stock_code",
            type="string",
            description="Underlying symbol, e.g. AAPL, US.AAPL, 700.HK, or HK.00700.",
        ),
        ToolParameter(
            name="expiry_date",
            type="string",
            description="Optional expiry date. Omit to return only available expiry dates.",
            required=False,
            default=None,
        ),
        ToolParameter(
            name="max_contracts",
            type="integer",
            description="Maximum strikes to return, 1-100; default 40.",
            required=False,
            default=40,
        ),
        ToolParameter(
            name="source",
            type="string",
            description="Provider preference. auto tries Longbridge then Futu.",
            required=False,
            enum=["auto", "longbridge", "futu"],
            default="auto",
        ),
    ],
    handler=_handle_get_option_strategy_snapshot,
    category="data",
    policy=_OPTIONS_POLICY,
)


get_option_strategy_analysis_tool = ToolDefinition(
    name="get_option_strategy_analysis",
    description=(
        "Run Futu OpenD's real provider-side option-combination analysis for up to four legs. "
        "Returns combination bid/ask, max profit/loss, breakeven points, probability of profit, "
        "Delta and Theta. This is read-only; it never places or prepares an order. "
        "Pass legs_json as a JSON array with code, action BUY/SELL, and quantity. "
        "Missing permissions or provider results are returned explicitly."
    ),
    parameters=[
        ToolParameter(
            name="legs_json",
            type="string",
            description=(
                'JSON array of 1-4 legs, e.g. '
                '[{"code":"US.AAPL260918C200000","action":"BUY","quantity":1}]'
            ),
        ),
        ToolParameter(
            name="stock_code",
            type="string",
            description="Optional underlying symbol used for provenance, e.g. US.AAPL.",
            required=False,
            default=None,
        ),
    ],
    handler=_handle_get_option_strategy_analysis,
    category="data",
    policy=_OPTIONS_POLICY,
)


ALL_OPTIONS_TOOLS = [get_option_strategy_snapshot_tool, get_option_strategy_analysis_tool]
