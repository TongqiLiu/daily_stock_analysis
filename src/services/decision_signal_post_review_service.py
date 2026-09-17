# -*- coding: utf-8 -*-
"""LLM post-review over deterministic DecisionSignal outcome statistics."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any, Callable, Dict, List, Optional

from src.analyzer import GeminiAnalyzer
from src.config import get_config
from src.services.decision_signal_outcome_service import DecisionSignalOutcomeService


MIN_POST_REVIEW_COMPLETED = 10
POST_REVIEW_PROMPT_VERSION = "decision-signal-post-review-v1"


class DecisionSignalPostReviewService:
    """Generate narrative diagnostics without changing deterministic outcomes."""

    def __init__(
        self,
        *,
        outcome_service: Optional[DecisionSignalOutcomeService] = None,
        completion: Optional[Callable[[List[Dict[str, str]]], Any]] = None,
    ) -> None:
        self.outcome_service = outcome_service or DecisionSignalOutcomeService()
        self.completion = completion

    def generate(
        self,
        *,
        horizons: Optional[List[str]] = None,
        source_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        stats = self.outcome_service.get_stats(horizons=horizons, source_type=source_type)
        completed = int(stats.get("completed") or 0)
        if completed < MIN_POST_REVIEW_COMPLETED:
            raise ValueError(
                f"AI post-review requires at least {MIN_POST_REVIEW_COMPLETED} completed outcomes; got {completed}"
            )

        audit_payload = self._audit_payload(stats)
        messages = [
            {
                "role": "system",
                "content": (
                    "你是交易分析流程审计员。只依据给定的确定性后验统计做归因，"
                    "不得重算或改写 hit/miss，不得声称因果关系，不得给出实时交易建议。"
                    "重点识别周期衰减、动作偏差、数据质量和无法结算原因。"
                    "输出 Markdown，必须包含：结论、证据表、优先改进项、不可下结论项。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(audit_payload, ensure_ascii=False, sort_keys=True),
            },
        ]
        try:
            response = self.completion(messages) if self.completion else self._call_llm(messages)
        except Exception as exc:
            raise RuntimeError("configured AI backend did not return a usable post-review") from exc
        content = str(
            getattr(response, "content", "")
            or getattr(response, "text", "")
            or ""
        ).strip()
        provider = str(getattr(response, "provider", "") or "")
        if not content or provider == "error":
            raise RuntimeError("configured AI backend did not return a usable post-review")
        return {
            "content": content,
            "provider": provider or None,
            "model": str(getattr(response, "model", "") or "") or None,
            "prompt_version": POST_REVIEW_PROMPT_VERSION,
            "completed_samples": completed,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    @staticmethod
    def _audit_payload(stats: Dict[str, Any]) -> Dict[str, Any]:
        dimensions = ("horizon", "action", "source_type", "market_phase", "data_quality_level")
        breakdowns = stats.get("breakdowns") if isinstance(stats.get("breakdowns"), dict) else {}
        return {
            "prompt_version": POST_REVIEW_PROMPT_VERSION,
            "metric_contract": {
                "hit_rate": "hit / (hit + miss); neutral and unable excluded",
                "adverse_excursion": "direction-aware MAE from persisted start/high/low only",
                "warning": "descriptive association only; no causal claim",
            },
            "overall": {
                key: stats.get(key)
                for key in (
                    "total", "completed", "unable", "hit", "miss", "neutral",
                    "hit_rate_pct", "avg_stock_return_pct",
                    "avg_adverse_excursion_pct", "max_adverse_excursion_pct",
                    "unable_reasons",
                )
            },
            "source_type_filter": stats.get("source_type"),
            "breakdowns": {key: breakdowns.get(key, [])[:20] for key in dimensions},
        }

    @staticmethod
    def _call_llm(messages: List[Dict[str, str]]) -> Any:
        config = get_config()
        prompt = "\n\n".join(str(message.get("content") or "") for message in messages)
        return GeminiAnalyzer(config=config).generate_text_with_metadata(
            prompt,
            temperature=0.1,
            max_tokens=1200,
        )
