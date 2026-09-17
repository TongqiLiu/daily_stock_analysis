from types import SimpleNamespace
from unittest.mock import Mock

from src.services.agent_chat_decision_signal_service import AgentChatDecisionSignalService


def _scope(code="NVDA", *, mode="switch", allowed=None):
    return SimpleNamespace(
        expected_stock_code=code,
        allowed_stock_codes=set(allowed or [code]),
        mode=mode,
    )


def test_persists_unambiguous_single_stock_conclusion() -> None:
    writer = Mock()
    writer.create_signal.return_value = {"item": {"id": 7}, "created": True}
    service = AgentChatDecisionSignalService(writer)

    item = service.persist_completed_turn(
        content="## 结论\n最终判断：买入，按 T+5 观察。",
        stock_scope=_scope(),
        session_id="session-1",
        user_message_id=11,
        assistant_message_id=12,
        backend="litellm",
        model="test-model",
    )

    assert item == {"id": 7}
    payload = writer.create_signal.call_args.args[0]
    assert payload["stock_code"] == "NVDA"
    assert payload["market"] == "us"
    assert payload["action"] == "buy"
    assert payload["horizon"] == "5d"
    assert payload["source_type"] == "agent"
    assert payload["metadata"]["assistant_message_id"] == 12


def test_skips_compare_or_ambiguous_chat_without_writing() -> None:
    writer = Mock()
    service = AgentChatDecisionSignalService(writer)

    compare = service.persist_completed_turn(
        content="最终判断：买入。",
        stock_scope=_scope("NVDA", mode="compare", allowed=["NVDA", "META"]),
        session_id="s",
        user_message_id=1,
        assistant_message_id=2,
        backend="litellm",
        model="model",
    )
    ambiguous = service.persist_completed_turn(
        content="可以买入，也可以减仓，视情况而定。",
        stock_scope=_scope(),
        session_id="s",
        user_message_id=1,
        assistant_message_id=3,
        backend="litellm",
        model="model",
    )

    assert compare is None
    assert ambiguous is None
    writer.create_signal.assert_not_called()


def test_persistence_failure_never_breaks_chat() -> None:
    writer = Mock()
    writer.create_signal.side_effect = RuntimeError("db unavailable")
    service = AgentChatDecisionSignalService(writer)

    assert service.persist_completed_turn(
        content="最终结论：继续持有。",
        stock_scope=_scope(),
        session_id="s",
        user_message_id=1,
        assistant_message_id=2,
        backend="codex_app_server",
        model="model",
    ) is None
