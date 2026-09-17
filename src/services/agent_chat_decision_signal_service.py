# -*- coding: utf-8 -*-
"""Best-effort DecisionSignal persistence for completed single-stock chat turns."""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional

from data_provider.base import normalize_stock_code
from src.core.trading_calendar import build_market_phase_context, get_market_for_stock
from src.market_phase_summary import render_market_phase_summary
from src.schemas.decision_action import localize_action_label, normalize_decision_action
from src.services.decision_signal_service import DecisionSignalService
from src.services.portfolio_service import VALID_MARKETS


logger = logging.getLogger(__name__)

_ACTIONABLE_CHAT_ACTIONS = frozenset({"buy", "add", "hold", "reduce", "sell", "avoid"})
_CONCLUSION_LINE = re.compile(r"(?:最终(?:判断|结论|计划)?|持仓结论|一句话(?:判断|结论)?|结论)\s*[：:]?\s*(.+)", re.IGNORECASE)
_HORIZON_PATTERNS = (
    (re.compile(r"(?:T\s*\+\s*10|10\s*(?:个)?交易日|10\s*日)", re.IGNORECASE), "10d"),
    (re.compile(r"(?:T\s*\+\s*5|5\s*(?:个)?交易日|5\s*日)", re.IGNORECASE), "5d"),
    (re.compile(r"(?:T\s*\+\s*3|3\s*(?:个)?交易日|3\s*日)", re.IGNORECASE), "3d"),
    (re.compile(r"(?:T\s*\+\s*1|1\s*(?:个)?交易日|1\s*日)", re.IGNORECASE), "1d"),
)


class AgentChatDecisionSignalService:
    """Persist only unambiguous single-stock decisions; never block Chat."""

    def __init__(self, signal_service: Optional[DecisionSignalService] = None) -> None:
        self.signal_service = signal_service or DecisionSignalService()

    def persist_completed_turn(
        self,
        *,
        content: str,
        stock_scope: Any,
        session_id: str,
        user_message_id: int,
        assistant_message_id: int,
        backend: str,
        model: str,
    ) -> Optional[Dict[str, Any]]:
        try:
            stock_code = self._single_stock_code(stock_scope)
            action = self._extract_action(content)
            if not stock_code or action not in _ACTIONABLE_CHAT_ACTIONS:
                return None
            market = get_market_for_stock(normalize_stock_code(stock_code))
            if market not in VALID_MARKETS:
                return None
            phase_summary = render_market_phase_summary(
                build_market_phase_context(
                    market=market,
                    trigger_source="web:agent_chat",
                ).to_dict()
            ) or {}
            horizon = self._extract_horizon(content)
            result = self.signal_service.create_signal({
                "stock_code": stock_code,
                "market": market,
                "source_type": "agent",
                "source_agent": (backend or "agent_chat")[:64],
                "trace_id": f"chat:{session_id}:{assistant_message_id}"[:64],
                "decision_profile": "balanced",
                "market_phase": phase_summary.get("phase"),
                "trigger_source": "web:agent_chat",
                "action": action,
                "action_label": localize_action_label(action, "zh"),
                "horizon": horizon,
                "reason": self._extract_reason(content),
                "data_quality_summary": {"level": "unknown"},
                "plan_quality": "minimal",
                "metadata": {
                    "profile_source": "auto_default",
                    "signal_generation_version": "agent-chat-extractor-v1",
                    "session_id": session_id,
                    "user_message_id": user_message_id,
                    "assistant_message_id": assistant_message_id,
                    "backend": backend,
                    "model": model,
                    "holding_state": "unknown",
                    "declared_horizon": horizon,
                    "market_phase_summary": phase_summary,
                },
            })
            return result.get("item") if isinstance(result, dict) else None
        except Exception as exc:
            logger.warning(
                "Agent Chat decision signal persistence skipped: session=%s assistant_message_id=%s error=%s",
                session_id,
                assistant_message_id,
                exc,
                exc_info=True,
            )
            return None

    @staticmethod
    def _single_stock_code(stock_scope: Any) -> str:
        if stock_scope is None or getattr(stock_scope, "mode", "") == "compare":
            return ""
        expected = str(getattr(stock_scope, "expected_stock_code", "") or "").strip()
        allowed = set(getattr(stock_scope, "allowed_stock_codes", set()) or set())
        if not expected or len(allowed) != 1 or expected not in allowed:
            return ""
        return expected

    @staticmethod
    def _extract_action(content: str) -> Optional[str]:
        text = str(content or "").strip()
        if not text:
            return None
        candidates = [match.group(1).strip() for match in _CONCLUSION_LINE.finditer(text)]
        for candidate in reversed(candidates):
            action = normalize_decision_action(candidate)
            if action in _ACTIONABLE_CHAT_ACTIONS:
                return action
        action = normalize_decision_action(text[-1200:])
        return action if action in _ACTIONABLE_CHAT_ACTIONS else None

    @staticmethod
    def _extract_horizon(content: str) -> str:
        tail = str(content or "")[-1600:]
        for pattern, horizon in _HORIZON_PATTERNS:
            if pattern.search(tail):
                return horizon
        return "3d"

    @staticmethod
    def _extract_reason(content: str) -> str:
        text = str(content or "").strip()
        candidates = [match.group(1).strip() for match in _CONCLUSION_LINE.finditer(text)]
        reason = candidates[-1] if candidates else text[-1000:]
        return reason[:1000]
