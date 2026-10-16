"""Daily-swing safety contract and prefix-only execution tests."""
from datetime import timedelta
import json
import math
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd
import pytest

from src.agent.tools.analysis_tools import (
    MULTI_STRATEGY_SCORE_SPECS, _handle_calculate_multi_strategy_score,
    _normalize_multi_strategy_market_structure, _multi_strategy_execution_gate,
    analyze_price_action_frame,
)
from src.core.backtest_engine import BacktestEngine
from src.services.swing_trade_plan import (
    SETUPS, build_swing_trade_plan, normalize_enabled_setups, price_plan_for_entry,
    validate_daily_frame, load_live_swing_plan,
)
from src.services.agent_chat_decision_signal_service import AgentChatDecisionSignalService


def bars(kind="breakout"):
    rows = []
    dates = pd.bdate_range("2025-01-02", periods=140)
    for i, day in enumerate(dates):
        close = 100 + i * .15 + (0 if kind == "breakout" else 2 * math.sin(i * math.pi / 5))
        rows.append(dict(date=day.date(), open=close-.4, high=close+.6,
                         low=close-.8, close=close, volume=10000))
    prior_high = max(r["high"] for r in rows[-21:-1])
    prior_low = min(r["low"] for r in rows[-21:-1])
    if kind == "breakout":
        rows[-1].update(open=prior_high-2, low=prior_high-3,
                        close=prior_high+.3, high=prior_high+.5)
    elif kind == "reversal":
        rows[-1].update(open=prior_low-.2, low=prior_low-1,
                        close=prior_low+2, high=prior_low+2.1)
    return pd.DataFrame(rows)


def plan_for(frame, enabled=SETUPS):
    return build_swing_trade_plan(frame, stock_code="TEST", source="fixture",
                                 adjustment="forward", next_session=frame.iloc[-1]["date"] + timedelta(days=1),
                                 enabled_setups=enabled)


def test_breakout_has_no_fabricated_target_and_is_next_session_only():
    plan = plan_for(bars())
    assert plan["setup_type"] == "initial_breakout"
    assert plan["status"] == "armed"
    assert plan["target_price"] is None
    assert plan["reward_risk_ratio"] is None
    assert plan["new_position_allowed"] is False
    assert plan["shares"] is None
    assert plan["risk_budget_fraction"] == .25


def test_bottom_reversal_has_independent_stop_and_risk_cap():
    frame = bars("reversal")
    plan = plan_for(frame)
    assert plan["setup_type"] == "bottom_reversal"
    assert plan["structure_low"] == frame.iloc[-1]["low"]
    assert plan["hard_stop"] == pytest.approx(plan["structure_low"]-.5*plan["atr14"])
    assert plan["risk_budget_fraction"] == .125
    assert "12.5%" in plan["add_rule"]


def test_disabled_setup_cannot_open_even_with_good_price():
    plan = plan_for(bars(), enabled=())
    assert plan["validation_status"] == "not_approved"
    result = price_plan_for_entry(plan, plan["signal_close"], session=pd.Timestamp(plan["valid_session"]).date(), enabled=False)
    assert result["status"] == "validation_pending"
    assert not result["new_position_allowed"]


def test_entry_repricing_recomputes_r_shares_and_both_observation_levels():
    plan = plan_for(bars())
    session = pd.Timestamp(plan["valid_session"]).date()
    for price in [plan["entry_low"]+.01, plan["max_entry_price"]-.01]:
        result = price_plan_for_entry(plan, price, session=session, enabled=True, campaign_risk_budget=100)
        risk = price - plan["hard_stop"]
        assert result["action"] == "buy"
        assert result["risk_per_share"] == risk
        assert result["shares"] == math.floor(25 / risk)
        assert [x["price"] for x in result["take_profit_tranches"]] == [price+risk, price+2*risk]


@pytest.mark.parametrize("case,expected", [("early", "armed"), ("late", "expired"),
                                           ("high", "overpriced"), ("low", "invalidated")])
def test_rejects_wrong_day_and_out_of_range_price(case, expected):
    plan = plan_for(bars())
    day, price = pd.Timestamp(plan["valid_session"]).date(), plan["signal_close"]
    if case == "early": day -= timedelta(days=1)
    if case == "late": day += timedelta(days=1)
    if case == "high": price = plan["max_entry_price"] + 1
    if case == "low": price = plan["structure_low"] - .1
    result = price_plan_for_entry(plan, price, session=day, enabled=True)
    assert result["status"] == expected
    assert not result["new_position_allowed"]


def test_rejects_bad_prices_dates_and_adjustment():
    frame = bars()
    with pytest.raises(ValueError):
        validate_daily_frame(pd.concat([frame, frame.tail(1)]))
    bad = frame.copy()
    bad.loc[3, "low"] = -1
    with pytest.raises(ValueError): validate_daily_frame(bad)
    plan = build_swing_trade_plan(frame, stock_code="TEST", source="fixture", adjustment="unknown",
                                 next_session=frame.iloc[-1]["date"]+timedelta(days=1))
    assert plan["reason"] == "adjustment_unverified"
    with pytest.raises(ValueError): normalize_enabled_setups("typo")


def test_pivots_never_confirm_without_two_right_hand_bars():
    frame = bars()
    frame.loc[138, "high"] = 150
    before = analyze_price_action_frame(frame, stock_code="TEST", source="fixture")
    assert 150 not in [x["price"] for x in before["structure"]["confirmed_pivot_highs"]]
    extra = dict(frame.iloc[-1], date=frame.iloc[-1]["date"]+timedelta(days=1))
    after = analyze_price_action_frame(pd.concat([frame, pd.DataFrame([extra])]), stock_code="TEST", source="fixture")
    assert 150 in [x["price"] for x in after["structure"]["confirmed_pivot_highs"]]


def test_old_resistance_is_not_negative_upside_and_nearby_level_is_not_veto():
    structure = _normalize_multi_strategy_market_structure({"current_price":105,"support_levels":[98],
        "resistance_levels":[100],"invalidation_level":98})
    assert structure["nearest_resistance"] is None
    assert _multi_strategy_execution_gate(structure).get("reward_risk_ratio") is None
    structure = _normalize_multi_strategy_market_structure({"current_price":100,"support_levels":[],
        "resistance_levels":[102],"invalidation_level":99})
    assert _multi_strategy_execution_gate(structure)["status"] == "ready"


def test_seventy_score_and_ready_plan_cannot_disagree():
    rows = [{"strategy":sid,"signal":"买入","strength":"中","score":70,
             "evidence_status":"complete","reason":"fixture"} for sid,_,_ in MULTI_STRATEGY_SCORE_SPECS]
    result = _handle_calculate_multi_strategy_score(json.dumps(rows),market_structure_json=json.dumps({
        "current_price":100,"support_levels":[96],"resistance_levels":[112],"invalidation_level":96}))
    assert result["position_plan"]["new_position_allowed"]
    assert result["actions"]["no_position"] == "buy"


def test_gap_stop_fills_at_worse_open_and_stop_never_moves_down():
    frame = bars()
    plan = plan_for(frame)
    next_day = frame.iloc[-1]["date"]+timedelta(days=1)
    entry = plan["signal_close"]
    appended = pd.DataFrame([
        dict(date=next_day, open=entry,high=entry+.5,low=entry-.2,close=entry+.3),
        dict(date=next_day+timedelta(days=1),open=plan["hard_stop"]-2,high=entry,low=plan["hard_stop"]-3,close=plan["hard_stop"]-1),
    ])
    result = BacktestEngine.evaluate_swing_plan(plan=plan,history=pd.concat([frame,appended],ignore_index=True),
                                               signal_index=len(frame)-1,horizon=2)
    assert result["filled"] and result["reason"] == "gap_stop"
    assert result["exit_price"] == plan["hard_stop"]-2
    assert result["net_r"] < -1
    stops = [x["stop"] for x in result["stop_path"]]
    assert stops == sorted(stops)


def test_live_loader_excludes_incomplete_bar_and_rejects_stale_history():
    frame = bars()
    from src.core.trading_calendar import get_trading_dates
    frame["date"] = list(get_trading_dates("us", pd.Timestamp("2025-01-02").date(),
                                          pd.Timestamp("2025-12-31").date()).date[:len(frame)])
    completed = frame.iloc[-2]["date"]
    frame.attrs["price_adjustment"] = "forward"
    phase = SimpleNamespace(warnings=[],phase=SimpleNamespace(value="intraday"),market="us",
                            effective_daily_bar_date=completed,session_date=frame.iloc[-1]["date"],is_market_open_now=False)
    with patch("src.core.trading_calendar.build_market_phase_context",return_value=phase), \
         patch("src.core.trading_calendar.get_next_trading_date",return_value=frame.iloc[-1]["date"]), \
         patch("src.services.history_loader.load_history_df",return_value=(frame,"fixture")):
        plan = load_live_swing_plan("TEST",enabled_setups=SETUPS)
    assert plan["signal_date"] == completed.isoformat()
    phase.effective_daily_bar_date = frame.iloc[-1]["date"]+timedelta(days=2)
    with patch("src.core.trading_calendar.build_market_phase_context",return_value=phase), \
         patch("src.services.history_loader.load_history_df",return_value=(frame,"fixture")):
        assert load_live_swing_plan("TEST",enabled_setups=SETUPS)["reason"] == "completed_daily_history_stale"


def test_structured_watch_is_saved_without_parsing_misleading_prose_and_mismatch_rejected():
    writer = Mock()
    writer.create_signal.return_value = {"item":{"id":1}}
    service = AgentChatDecisionSignalService(writer)
    plan = plan_for(bars(), enabled=())
    scope = SimpleNamespace(expected_stock_code="TEST",allowed_stock_codes={"TEST"},mode="switch")
    kwargs = dict(content="最终结论：买入1000股",stock_scope=scope,session_id="s",user_message_id=1,
                  assistant_message_id=2,backend="litellm",model="test",decision_payload={"trade_plan":plan})
    assert service.persist_completed_turn(**kwargs)["id"] == 1
    payload = writer.create_signal.call_args.args[0]
    assert payload["action"] == "watch"
    assert payload["metadata"]["trade_plan"] == plan
    writer.reset_mock()
    kwargs["decision_payload"] = {"trade_plan":{**plan,"stock_code":"OTHER"}}
    assert service.persist_completed_turn(**kwargs) is None
    writer.create_signal.assert_not_called()


def test_repricing_clears_old_shares_when_plan_is_rejected():
    plan = plan_for(bars())
    day = pd.Timestamp(plan["valid_session"]).date()
    entered = price_plan_for_entry(plan, plan["signal_close"], session=day, enabled=True, campaign_risk_budget=100)
    rejected = price_plan_for_entry(entered, plan["max_entry_price"]+1, session=day, enabled=True)
    assert rejected["shares"] is None
    assert rejected["risk_per_share"] == rejected["entry_reference"] - rejected["hard_stop"]
    assert not rejected["new_position_allowed"]


def test_live_loader_rejects_a_calendar_gap():
    from src.core.trading_calendar import get_trading_dates
    frame = bars()
    frame["date"] = list(get_trading_dates("us", pd.Timestamp("2025-01-02").date(),
                                          pd.Timestamp("2025-12-31").date()).date[:len(frame)])
    frame.attrs["price_adjustment"] = "forward"
    phase = SimpleNamespace(warnings=[], phase=SimpleNamespace(value="postmarket"), market="us",
                            effective_daily_bar_date=frame.iloc[-1]["date"])
    with patch("src.core.trading_calendar.build_market_phase_context", return_value=phase), \
         patch("src.services.history_loader.load_history_df", return_value=(frame.drop(index=20), "fixture")):
        assert load_live_swing_plan("TEST", enabled_setups=SETUPS)["reason"] == "daily_calendar_gaps"


def test_recording_plan_twice_is_idempotent_and_does_not_rewrite_snapshot(monkeypatch):
    from src.storage import DatabaseManager
    from src.services.decision_signal_service import DecisionSignalService
    monkeypatch.setattr(DatabaseManager, "_instance", None)
    db = DatabaseManager(db_url="sqlite:///:memory:")
    assert str(db._engine.url) == "sqlite:///:memory:"
    writer = DecisionSignalService(db_manager=db)
    recorder = AgentChatDecisionSignalService(writer)
    plan = {**plan_for(bars(), enabled=()), "stock_code": "ORCL"}
    args = dict(content="观望", stock_scope=SimpleNamespace(expected_stock_code="ORCL", allowed_stock_codes={"ORCL"}, mode="switch"),
                session_id="test-idempotent", user_message_id=1, assistant_message_id=2, backend="test", model="test",
                decision_payload={"trade_plan": plan})
    first = recorder.persist_completed_turn(**args)
    assert first is not None
    args["decision_payload"] = {"trade_plan": {**plan, "action": "buy", "reason": "changed later"}}
    second = recorder.persist_completed_turn(**args)
    assert first["id"] == second["id"]
    assert second["metadata"]["trade_plan"]["action"] == "watch"
    assert writer.list_signals(source_type="agent")["total"] == 1


@pytest.mark.parametrize("backend_id,owns_loop", [("litellm", False), ("codex_app_server", True)])
@pytest.mark.parametrize("failure", ["cancelled", "timeout", "stock_scope_mismatch"])
def test_both_backends_do_not_record_cancel_timeout_or_wrong_stock(backend_id, owns_loop, failure):
    import threading
    from src.agent.agent_backend import AgentRunResult
    from src.agent.chat_executor import AgentChatExecutor, PreparedAgentChatTurn
    event = threading.Event()
    if failure == "cancelled": event.set()
    result = AgentRunResult(success=failure != "timeout", final_answer="模型正文", backend=backend_id,
                            error_code="timeout" if failure == "timeout" else None,
                            decision_payload={"trade_plan": {**plan_for(bars()), "stock_code": "OTHER" if failure == "stock_scope_mismatch" else "TEST"}})
    backend = SimpleNamespace(runtime_owns_loop=owns_loop, run=lambda _: result)
    executor = AgentChatExecutor(backend=backend, config=SimpleNamespace(), context_llm_adapter=None)
    turn = PreparedAgentChatTurn(message="TEST", session_id="test", baseline_len=2, run_id="run", user_message_id=1,
                                 prepared=SimpleNamespace(system_prompt="", history_messages=[], user_message="TEST",
                                                          stock_scope=SimpleNamespace(expected_stock_code="TEST")))
    with patch("src.agent.chat_executor.conversation_manager.add_message"), \
         patch.object(AgentChatDecisionSignalService, "persist_completed_turn") as record:
        output = executor.execute_turn(turn, cancel_event=event)
    assert not output.success
    assert output.error_code == failure
    record.assert_not_called()


def test_frozen_acceptance_does_not_accept_positive_but_tiny_sample():
    from scripts.replay_swing_entry_plans import acceptance
    stats = dict(fills=29, symbols_with_fills=5, mean_net_r=.5, profit_factor=2,
                 no_losses=False, max_drawdown_r=2, net_r_per_opportunity=.2)
    assert not acceptance(stats, stats, stats)["passed"]
    assert acceptance({**stats, "fills": 30}, stats, stats)["passed"]
    assert not acceptance({**stats, "fills": 30}, {**stats, "mean_net_r": -.01}, stats)["passed"]


def test_model_commentary_cannot_add_a_second_execution_plan():
    from src.agent.runner import _strip_independent_execution_commentary
    body = "### 行情\n\n公司收入增长。\n\n### 独立仓位计划\n\n空仓观望，但可以试仓100股。\n\n止损为99；1R为101。\n\n### 风险\n\n新闻来源缺失。"
    result = _strip_independent_execution_commentary(body)
    assert "收入增长" in result and "新闻来源缺失" in result
    assert "100股" not in result and "止损为99" not in result
    assert "已省略" in result


def test_server_plan_does_not_inherit_model_supplied_holding_exit():
    plan = plan_for(bars(), enabled=())
    rows = [{"strategy":sid,"signal":"买入","strength":"中","score":70,
             "evidence_status":"complete","reason":"fixture"} for sid,_,_ in MULTI_STRATEGY_SCORE_SPECS]
    with patch("src.services.swing_trade_plan.load_live_swing_plan", return_value=plan), \
         patch("src.config.get_config", return_value=SimpleNamespace(multi_strategy_enabled_setups=["initial_breakout"])):
        result = _handle_calculate_multi_strategy_score(json.dumps(rows), stock_code="TEST",
            market_structure_json=json.dumps({"current_price":100,"invalidation_level":999,"resistance_levels":[120]}))
    assert result["actions"]["has_position"] != "sell"
    assert result["position_plan"]["existing_position_management"]["invalidation_level"] == plan["hard_stop"]
    assert result["market_structure"]["invalidation_level"] == plan["hard_stop"]


def test_longbridge_timestamp_uses_sdk_host_local_semantics_without_faking_missing():
    from datetime import datetime, timezone
    from data_provider.longbridge_fetcher import _quote_timestamp_iso
    epoch = 1750000000
    local_naive = datetime.fromtimestamp(epoch)
    assert _quote_timestamp_iso(local_naive) == datetime.fromtimestamp(epoch, timezone.utc).isoformat()
    assert _quote_timestamp_iso(None) is None
    assert _quote_timestamp_iso("unknown") is None


def test_pa_entrypoint_ignores_unfinished_breakout_and_vwap_does_not_remove_daily_evidence():
    from src.agent.tools.analysis_tools import _handle_analyze_price_action
    frame = bars()
    phase = SimpleNamespace(warnings=[], phase=SimpleNamespace(value="intraday"), effective_daily_bar_date=frame.iloc[-2]["date"])
    with patch("src.services.history_loader.load_history_df",return_value=(frame,"fixture")), \
         patch("src.core.trading_calendar.build_market_phase_context",return_value=phase), \
         patch("src.agent.tools.analysis_tools._calculate_price_action_vwap",return_value={"status":"unavailable"}):
        result = _handle_analyze_price_action("TEST")
    assert result["completed_daily_bar_date"] == str(frame.iloc[-2]["date"])
    assert result["excluded_incomplete_bars"] == 1
    assert result["levels"]["current_price"] == frame.iloc[-2]["close"]
    assert result["status"] == "ok"


@pytest.mark.parametrize("has_current", [False, True])
def test_litellm_payload_capture_never_reuses_previous_turn(has_current):
    from src.agent.agent_backend import AgentRunRequest, LiteLLMAgentBackend
    from src.agent.tools.registry import ToolRegistry
    old = {"status": "ok", "trade_plan": {"stock_code": "OLD"}}
    new = {"status": "ok", "trade_plan": {"stock_code": "NEW"}}
    history = [{"role": "tool", "name": "calculate_multi_strategy_score", "content": json.dumps(old)}]
    messages = [{"role": "system"}, *history, {"role": "user"}]
    if has_current:
        messages.append({"role": "tool", "name": "calculate_multi_strategy_score", "content": json.dumps(new)})
    loop = SimpleNamespace(success=True, total_tokens=0, content="text", tool_calls_log=[], model="test",
                           provider="test", error=None, messages=messages, total_steps=1)
    request = AgentRunRequest(system_prompt="", history_messages=history, user_message="NEW", session_id="s",
                              stock_scope=None, max_steps=3, max_wall_clock_seconds=10)
    with patch("src.agent.agent_backend.run_agent_loop", return_value=loop):
        result = LiteLLMAgentBackend(ToolRegistry(), Mock()).run(request)
    assert result.decision_payload == (new if has_current else None)
