# -*- coding: utf-8 -*-
"""Fresh-market-data guardrails for current stock-analysis turns.

Historical context is useful for comparison, but it must not silently become
the input for a new "current trend" analysis.  This module keeps the policy
request-scoped so generic data tools and historical/backtest workflows retain
their existing cache behaviour.
"""

from __future__ import annotations

import contextvars
import json
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence


REQUIRED_FRESH_MARKET_TOOLS = ("get_realtime_quote", "get_daily_history")

_FRESH_MARKET_DATA_REQUIRED: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "fresh_market_data_required",
    default=False,
)


def set_fresh_market_data_required(required: bool = True) -> contextvars.Token:
    """Require current analysis tools to bypass reusable history caches."""
    return _FRESH_MARKET_DATA_REQUIRED.set(bool(required))


def reset_fresh_market_data_required(token: contextvars.Token) -> None:
    """Restore the previous request-level freshness policy."""
    _FRESH_MARKET_DATA_REQUIRED.reset(token)


def is_fresh_market_data_required() -> bool:
    """Return whether the current tool execution must fetch fresh market data."""
    return _FRESH_MARKET_DATA_REQUIRED.get()


@dataclass(frozen=True)
class FreshnessCheck:
    """Validation result for one Agent turn's newly returned tool messages."""

    ok: bool
    missing: List[str] = field(default_factory=list)
    observed: Dict[str, Any] = field(default_factory=dict)

    @property
    def retry_message(self) -> str:
        missing = "、".join(self.missing) if self.missing else "实时行情和日线数据"
        return (
            "【硬性数据门禁】本轮不能生成当前走势分析。"
            f"以下数据没有在本轮成功刷新：{missing}。"
            "必须重新调用 get_realtime_quote 和 get_daily_history；"
            "日线结果必须标记 refresh_mode=network、cache_hit=false，并带有 fetched_at。"
            "不得用历史对话、上次分析、get_analysis_context 或 DB 缓存替代。"
            "如果实时数据源确实不可用，请直接说明无法完成本次最新分析。"
        )


def _normalize_code(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text.endswith(".HK"):
        text = text[:-3]
    if text.startswith("HK") and text[2:].isdigit():
        text = f"HK{text[2:].zfill(5)}"
    return text


def _parse_tool_payload(message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if message.get("role") != "tool":
        return None
    content = message.get("content")
    if isinstance(content, dict):
        return content
    if not isinstance(content, str):
        return None
    try:
        payload = json.loads(content)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _latest_tool_payloads(messages: Sequence[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    payloads: Dict[str, List[Dict[str, Any]]] = {}
    for message in messages:
        tool_name = message.get("name")
        if tool_name not in REQUIRED_FRESH_MARKET_TOOLS:
            continue
        payload = _parse_tool_payload(message)
        if payload is not None:
            payloads.setdefault(tool_name, []).append(payload)
    return payloads


def _payload_code(payload: Dict[str, Any]) -> str:
    return _normalize_code(payload.get("code") or payload.get("stock_code"))


def _valid_quote(payload: Dict[str, Any]) -> bool:
    if payload.get("error"):
        return False
    price = payload.get("price")
    try:
        valid_price = price is not None and float(price) > 0
    except (TypeError, ValueError):
        valid_price = False
    return bool(payload.get("fetched_at")) and valid_price


def _valid_history(payload: Dict[str, Any]) -> bool:
    if payload.get("error"):
        return False
    data = payload.get("data")
    return (
        bool(payload.get("fetched_at"))
        and payload.get("refresh_mode") == "network"
        and payload.get("cache_hit") is False
        and isinstance(data, list)
        and bool(data)
        and bool(payload.get("latest_data_date"))
    )


def validate_fresh_market_data(
    messages: Sequence[Dict[str, Any]],
    *,
    required_stock_codes: Optional[Iterable[str]] = None,
) -> FreshnessCheck:
    """Validate only this turn's tool results, never prior conversation history."""
    payloads = _latest_tool_payloads(messages)
    required_codes = {
        _normalize_code(code)
        for code in (required_stock_codes or [])
        if _normalize_code(code)
    }

    missing: List[str] = []
    observed: Dict[str, Any] = {}
    for tool_name, validator in (
        ("get_realtime_quote", _valid_quote),
        ("get_daily_history", _valid_history),
    ):
        valid_payloads = [payload for payload in payloads.get(tool_name, []) if validator(payload)]
        if required_codes:
            valid_codes = {_payload_code(payload) for payload in valid_payloads}
            valid_codes.discard("")
            if not required_codes.issubset(valid_codes):
                missing.append(f"{tool_name}({','.join(sorted(required_codes - valid_codes))})")
        elif not valid_payloads:
            missing.append(tool_name)
        if valid_payloads:
            observed[tool_name] = {
                "codes": sorted({_payload_code(payload) for payload in valid_payloads if _payload_code(payload)}),
                "count": len(valid_payloads),
            }

    return FreshnessCheck(ok=not missing, missing=missing, observed=observed)


__all__ = [
    "FreshnessCheck",
    "REQUIRED_FRESH_MARKET_TOOLS",
    "is_fresh_market_data_required",
    "reset_fresh_market_data_required",
    "set_fresh_market_data_required",
    "validate_fresh_market_data",
]
