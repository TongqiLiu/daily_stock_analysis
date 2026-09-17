from types import SimpleNamespace

import pytest

from src.services.decision_signal_post_review_service import DecisionSignalPostReviewService


class _OutcomeService:
    def __init__(self, completed=12):
        self.completed = completed

    def get_stats(self, *, horizons=None, source_type=None):
        return {
            "total": 15,
            "completed": self.completed,
            "unable": 3,
            "hit": 7,
            "miss": 4,
            "neutral": 1,
            "hit_rate_pct": 63.64,
            "avg_stock_return_pct": 1.2,
            "avg_adverse_excursion_pct": 3.1,
            "max_adverse_excursion_pct": 8.8,
            "unable_reasons": {"missing_anchor_price": 3},
            "breakdowns": {"horizon": [{"value": "3d", "hit_rate_pct": 60}]},
            "source_type": source_type,
        }


def test_post_review_uses_deterministic_stats_without_mutating_them() -> None:
    captured = {}

    def completion(messages):
        captured["messages"] = messages
        return SimpleNamespace(content="## 结论\nT+3 胜率较弱。", provider="test", model="fixture")

    result = DecisionSignalPostReviewService(
        outcome_service=_OutcomeService(),
        completion=completion,
    ).generate(horizons=["1d", "3d"], source_type="agent")

    assert result["completed_samples"] == 12
    assert result["content"].startswith("## 结论")
    assert '"hit_rate": "hit / (hit + miss); neutral and unable excluded"' in captured["messages"][1]["content"]
    assert "不得重算或改写 hit/miss" in captured["messages"][0]["content"]
    assert '"source_type_filter": "agent"' in captured["messages"][1]["content"]


def test_post_review_requires_enough_completed_samples() -> None:
    service = DecisionSignalPostReviewService(
        outcome_service=_OutcomeService(completed=9),
        completion=lambda _: None,
    )
    with pytest.raises(ValueError, match="at least 10"):
        service.generate()


def test_post_review_accepts_generation_backend_text_result() -> None:
    response = SimpleNamespace(text="## 结论\n后置复盘完成。", provider="codex_cli", model="codex")
    result = DecisionSignalPostReviewService(
        outcome_service=_OutcomeService(),
        completion=lambda _: response,
    ).generate()

    assert result["content"].endswith("后置复盘完成。")
    assert result["provider"] == "codex_cli"
