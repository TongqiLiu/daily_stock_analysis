# -*- coding: utf-8 -*-
"""Regression tests for deterministic multi-strategy scoring and output gates."""

import json
from unittest.mock import MagicMock

from src.agent.llm_adapter import LLMResponse, ToolCall
from src.agent.runner import _render_multi_strategy_score_section, run_agent_loop
from src.agent.tools.analysis_tools import (
    MULTI_STRATEGY_SCORE_SPECS,
    _handle_calculate_multi_strategy_score,
    calculate_multi_strategy_score_tool,
)
from src.agent.tools.registry import ToolDefinition, ToolRegistry


def _score_rows(score: float = 70.0) -> list[dict]:
    return [
        {
            "strategy": strategy_id,
            "signal": "买入",
            "strength": "中",
            "score": score,
            "evidence_status": "complete",
            "reason": f"{display_name}证据",
        }
        for strategy_id, display_name, _ in MULTI_STRATEGY_SCORE_SPECS
    ]


def _koru_corrected_rows() -> list[dict]:
    values = {
        "bull_trend": ("买入", "中", 68, "complete", "站上短均线但长期均线仍空头"),
        "chan_theory": ("买入", "中", 72, "partial", "MACD改善，缺少完整笔段和中枢"),
        "ma_golden_cross": ("买入", "强", 80, "complete", "MA5上穿MA10并站上MA20"),
        "shrink_pullback": ("观望", "中", 50, "complete", "当前为突破而非回踩"),
        "volume_breakout": ("买入", "弱", 58, "complete", "破前高但量比仅0.77"),
        "wave_theory": ("买入", "中", 75, "partial", "三浪候选，缩量且长期均线空头"),
        "bottom_volume": ("买入", "中", 75, "partial", "日线底部换手，缺周线确认"),
        "box_oscillation": ("买入", "中", 68, "complete", "突破19.12箱体上沿"),
        "dragon_head": ("不可评估", "-", None, "missing", "缺少板块和基准对比"),
        "emotion_cycle": ("观望", "中", 50, "partial", "情绪中性，个股相对强弱证据不足"),
        "one_yang_three_yin": ("观望", "中", 50, "complete", "未触发标准形态"),
        "fear_greed_sentiment": ("观望", "中", 50, "complete", "贪恐中性，无极值"),
        "price_action": ("买入", "中", 70, "partial", "HH/HL 候选成立，日内 VWAP 缺失"),
    }
    return [
        {
            "strategy": strategy_id,
            "signal": values[strategy_id][0],
            "strength": values[strategy_id][1],
            "score": values[strategy_id][2],
            "evidence_status": values[strategy_id][3],
            "reason": values[strategy_id][4],
        }
        for strategy_id, _, _ in MULTI_STRATEGY_SCORE_SPECS
    ]


def _wulf_failure_rows() -> list[dict]:
    """Reproduce a WULF-like bearish setup with one missing evidence row."""
    values = {
        "bull_trend": ("卖出", "强", 8, "complete", "MA5<MA10<MA20空头排列"),
        "chan_theory": ("买入", "弱", 58, "partial", "MACD零轴下金叉，缺背驰段"),
        "ma_golden_cross": ("观望", "中", 50, "complete", "MACD金叉但MA5未上穿MA10"),
        "shrink_pullback": ("卖出", "中", 30, "complete", "跌破MA20，缩量阴跌"),
        "volume_breakout": ("卖出", "强", 12, "complete", "量比0.39，无放量突破"),
        "wave_theory": ("卖出", "弱", 40, "partial", "C浪下跌候选，缺周线确认"),
        "bottom_volume": ("观望", "中", 50, "partial", "缺周线底部确认"),
        "box_oscillation": ("观望", "中", 50, "complete", "箱体内弱势震荡"),
        "dragon_head": ("不可评估", "-", None, "missing", "缺板块与基准对比"),
        "emotion_cycle": ("观望", "中", 50, "partial", "缺市场与个股相对强弱"),
        "one_yang_three_yin": ("观望", "中", 50, "complete", "未触发标准形态"),
        "fear_greed_sentiment": ("买入", "中", 68, "complete", "恐慌区间逆向关注"),
        "price_action": ("卖出", "弱", 40, "partial", "LH/LL 候选，日内 VWAP 缺失"),
    }
    return [
        {
            "strategy": strategy_id,
            "signal": values[strategy_id][0],
            "strength": values[strategy_id][1],
            "score": values[strategy_id][2],
            "evidence_status": values[strategy_id][3],
            "reason": values[strategy_id][4],
        }
        for strategy_id, _, _ in MULTI_STRATEGY_SCORE_SPECS
    ]


def _consensus_system_prompt() -> str:
    return (
        "启用 multi_strategy_consensus。必须调用 calculate_multi_strategy_score。"
        "13 项固定总权重是 10.0。"
    )


def _market_structure() -> dict:
    return {
        "current_price": 102.0,
        "support_levels": [98.5, 100.0],
        "resistance_levels": [106.5, 108.0],
        "breakout_status": "未确认",
        "invalidation_level": 97.8,
        "evidence": "前低、MA20与20日高点",
    }


def _valid_final_report(*, wave_three: bool = False) -> str:
    strategy_lines = "\n".join(
        f"| {index} | {display_name} | 买入 | 中 | 70 | {weight} | 完整 | 有效证据 |"
        for index, (_, display_name, weight) in enumerate(MULTI_STRATEGY_SCORE_SPECS, start=1)
    )
    content = f"""### 二、子策略评估表
| # | 策略 | 信号 | 强度 | 评分 | 权重 | 证据 | 关键依据 |
|---|---|---|---|---|---|---|---|
{strategy_lines}

### 三、加权综合得分
**加权综合得分：70.0 / 100**
计算过程：700 / 10 = 70.0
证据覆盖：完整 13 / 部分 0 / 缺失 0，有效权重覆盖 100.0%
决策映射：偏多
建议仓位：中仓30-50%
"""
    if wave_three:
        content += """
### 四、浪型图与判定
第3浪候选启动，置信度：中。
```text
价格                 C：当前 20.15（2026-08-12）
                    /
       A：19.12（2026-08-04）
         \\        /
          B：16.41（2026-08-10）
         /
O：11.65（2026-07-29）
时间 ─────────────────▶
```
替代数浪：仍可能只是下跌趋势中的 B 浪反弹。
确认位：放量站稳 20.20；弱化位：跌回 19.12；硬证伪位：跌破 16.41。
第2浪回撤比例为 36.6%；量能确认仍未满足，需等待量比扩张。
情景目标：O→A 长度 7.47，1.0 倍目标为 23.88，1.618 倍目标为 28.50。
"""
    return content


def test_calculator_uses_fixed_10_weight_and_programmatic_formula() -> None:
    result = _handle_calculate_multi_strategy_score(json.dumps(_score_rows()))

    assert result["status"] == "ok"
    assert result["weighted_numerator"] == 700.0
    assert result["included_weight"] == 10.0
    assert result["configured_total_weight"] == 10.0
    assert result["weighted_score"] == 70.0
    assert result["dimension_weighted_score"] == 70.0
    assert result["formula"] == "700 / 10 = 70"
    assert result["dimension_formula"] == "70 / 1 = 70"
    assert result["decision"] == "偏多"
    assert result["position_guidance"] == "等待确认后小仓试仓，并按止损距离计算仓位"
    assert result["bias"] == {
        "code": "bullish",
        "label": "偏多",
        "score": 70.0,
    }
    assert result["actions"]["no_position"] == "watch"
    assert result["actions"]["has_position"] == "hold"
    assert result["confidence"]["level"] == "high"
    assert len(result["dimensions"]) == 5
    price_action = next(
        row for row in result["rows"] if row["strategy"] == "price_action"
    )
    assert price_action["weight"] == 0.8
    assert price_action["dimension"] == "结构"


def test_execution_gate_waits_when_a_bullish_score_has_insufficient_reward_to_risk() -> None:
    result = _handle_calculate_multi_strategy_score(
        json.dumps(_score_rows(75.0)),
        market_structure_json=json.dumps(_market_structure()),
    )

    assert result["decision"] == "强烈买入"
    assert result["execution"]["status"] == "wait"
    assert result["execution"]["reward_risk_ratio"] == 1.07
    assert result["actions"]["no_position"] == "watch"
    assert result["actions"]["no_position_label"] == "等待突破/回踩确认，当前不追入"
    assert result["actions"]["has_position"] == "add"


def test_execution_gate_exits_when_price_breaks_hard_invalidation() -> None:
    structure = _market_structure() | {"current_price": 97.0}
    result = _handle_calculate_multi_strategy_score(
        json.dumps(_score_rows(75.0)),
        market_structure_json=json.dumps(structure),
    )

    assert result["execution"]["status"] == "blocked"
    assert result["actions"]["no_position"] == "avoid"
    assert result["actions"]["has_position"] == "sell"


def test_calculator_rejects_legacy_12_row_payload_without_price_action() -> None:
    rows = [
        row for row in _score_rows() if row["strategy"] != "price_action"
    ]

    result = _handle_calculate_multi_strategy_score(json.dumps(rows))

    assert result["status"] == "invalid"
    assert "price_action" in " ".join(result["issues"])
    assert any("expected 13 rows" in issue for issue in result["issues"])


def test_calculator_tool_requires_explicit_instrument_classification() -> None:
    parameter = next(
        item
        for item in calculate_multi_strategy_score_tool.parameters
        if item.name == "instrument_type"
    )

    assert parameter.required is True
    assert parameter.default is None


def test_renderer_includes_price_action_risk_controls_and_real_vwap() -> None:
    score_payload = _handle_calculate_multi_strategy_score(json.dumps(_score_rows()))
    price_action_payload = {
        "status": "ok",
        "context": {"label": "多头趋势候选", "reason": "HH/HL 且站上 MA20"},
        "signal": {
            "label": "上破近 20 日高点候选",
            "reason": "等待后续跟随",
            "signal_bar_label": "强势收盘阳线候选",
        },
        "structure": {
            "higher_highs": True,
            "higher_lows": True,
            "lower_highs": False,
            "lower_lows": False,
        },
        "levels": {
            "current_price": 110,
            "ma20": 105,
            "prior_20d_high": 109,
            "prior_20d_low": 98,
        },
        "second_entry": {
            "label": "二次入场需逐根 K 线确认",
            "reason": "尚未确认",
        },
        "risk_management": {
            "long_invalidation": {
                "status": "available",
                "key_low": {"price": 98},
                "buffer_0_5_atr": 96.5,
                "buffer_1_0_atr": 95,
                "rule": "收盘跌破关键低点失效",
            },
            "trend_bar_trailing": {
                "status": "available",
                "close": 108,
                "rule": "收盘跌破强阳 K 收盘价为候选",
            },
            "intraday_vwap": {
                "status": "ok",
                "source": "futu",
                "timeframe": "15m",
                "session": "2026-08-28",
                "as_of": "2026-08-28 15:45:00",
                "vwap": 107,
                "current_price": 109,
                "price_position": "above_vwap",
                "trailing_trigger": False,
                "upper_2sigma": 111,
                "upper_3sigma": 113,
                "trigger_rule": "15m K 线收盘跌破 VWAP 才触发",
            },
        },
    }

    rendered = _render_multi_strategy_score_section(
        score_payload,
        price_action_payload,
    )

    assert "#### 3.1 价格行为学视角（Brooks-inspired）" in rendered
    assert "重要低点 / ATR 止损" in rendered
    assert "0.5 ATR 96.5；1.0 ATR 95" in rendered
    assert "日内 VWAP 移动止盈" in rendered
    assert "futu / 15m / 2026-08-28 15:45:00" in rendered
    assert "+2σ 111；+3σ 113" in rendered
    assert "位置 above_vwap；移动止盈候选触发=False" in rendered
    assert "价格行为学已作为第 13 项进入正式评分" in rendered


def test_renderer_includes_relative_strength_sources_windows_and_sector_gap() -> None:
    score_payload = _handle_calculate_multi_strategy_score(json.dumps(_score_rows()))
    relative_strength_payload = {
        "status": "partial",
        "as_of": "2026-08-28",
        "recommended_evidence_status": "partial",
        "sources": {
            "stock": "futu",
            "benchmark": "futu",
            "sector_benchmark": None,
        },
        "benchmark": {
            "code": "SPY",
            "label": "SPDR S&P 500 ETF（美股大盘代理）",
            "windows": {
                "5d": {
                    "stock_return_pct": 5.0,
                    "benchmark_return_pct": 2.0,
                    "excess_return_pct": 3.0,
                },
                "20d": {
                    "stock_return_pct": 12.0,
                    "benchmark_return_pct": 6.0,
                    "excess_return_pct": 6.0,
                },
                "60d": {
                    "stock_return_pct": 22.0,
                    "benchmark_return_pct": 11.0,
                    "excess_return_pct": 11.0,
                },
            },
        },
        "sector_benchmark": {
            "status": "missing",
            "reason": "未提供可验证的行业/板块基准代码",
        },
    }

    rendered = _render_multi_strategy_score_section(
        score_payload,
        relative_strength_payload=relative_strength_payload,
    )

    assert "#### 3.2 基准 / 板块相对强弱" in rendered
    assert "SPY / SPDR S&P 500 ETF（美股大盘代理）" in rendered
    assert "个股=futu；基准=futu" in rendered
    assert "| 大盘 SPY | 20d | 12.00% | 6.00% | 6.00% |" in rendered
    assert "未提供可验证的行业/板块基准代码" in rendered
    assert "龙头策略最多按部分证据评估" in rendered


def test_calculator_excludes_missing_evidence_from_both_sides() -> None:
    rows = _score_rows()
    dragon = next(row for row in rows if row["strategy"] == "dragon_head")
    dragon.update({
        "signal": "不可评估",
        "strength": "-",
        "score": None,
        "evidence_status": "missing",
        "reason": "缺少板块和基准对比数据",
    })

    result = _handle_calculate_multi_strategy_score(
        json.dumps(rows, ensure_ascii=False),
        instrument_type="leveraged_etf",
    )

    assert result["status"] == "ok"
    assert result["weighted_numerator"] == 651.0
    assert result["included_weight"] == 9.3
    assert result["weighted_score"] == 70.0
    assert result["evidence"]["complete_count"] == 12
    assert result["evidence"]["partial_count"] == 0
    assert result["evidence"]["missing_count"] == 1
    assert result["evidence"]["coverage_pct"] == 93.0
    assert result["evidence"]["quality_coverage_pct"] == 93.0
    assert result["evidence"]["available_dimension_count"] == 4


def test_fear_greed_unavailable_is_excluded_instead_of_proxy_scored() -> None:
    rows = _score_rows()
    fear_greed = next(
        row for row in rows if row["strategy"] == "fear_greed_sentiment"
    )
    fear_greed.update({
        "signal": "不可评估",
        "strength": "-",
        "score": None,
        "evidence_status": "missing",
        "reason": "贪恐工具 unavailable: auth_failed；建议检查配置",
    })

    result = _handle_calculate_multi_strategy_score(json.dumps(rows))

    assert result["status"] == "ok"
    assert result["included_weight"] == 9.5
    assert result["weighted_score"] == 70.0
    assert result["evidence"]["missing_count"] == 1
    scored_fear_greed = next(
        row for row in result["rows"] if row["strategy"] == "fear_greed_sentiment"
    )
    assert scored_fear_greed["dimension"] == "情绪"
    assert scored_fear_greed["included"] is False


def test_fear_greed_proxy_evidence_is_rejected() -> None:
    rows = _score_rows()
    fear_greed = next(
        row for row in rows if row["strategy"] == "fear_greed_sentiment"
    )
    fear_greed["reason"] = "使用 proxy_score=0 作为中性代理"

    result = _handle_calculate_multi_strategy_score(json.dumps(rows))

    assert result["status"] == "invalid"
    assert any("must be marked missing" in issue for issue in result["issues"])


def test_calculator_rejects_strength_score_and_partial_evidence_mismatches() -> None:
    rows = _score_rows()
    rows[0]["score"] = 90
    rows[1].update({"strength": "强", "evidence_status": "partial"})

    result = _handle_calculate_multi_strategy_score(json.dumps(rows, ensure_ascii=False))

    assert result["status"] == "invalid"
    assert any("score 90" in issue for issue in result["issues"])
    assert any("partial evidence cannot use strong" in issue for issue in result["issues"])


def test_calculator_forces_insufficient_decision_when_coverage_is_too_low() -> None:
    rows = _score_rows()
    for row in rows[7:]:
        row.update({
            "signal": "不可评估",
            "strength": "-",
            "score": None,
            "evidence_status": "missing",
            "reason": "关键输入缺失",
        })

    result = _handle_calculate_multi_strategy_score(json.dumps(rows, ensure_ascii=False))

    assert result["status"] == "ok"
    assert result["evidence"]["coverage_pct"] < 70.0
    assert result["decision"] == "证据不足（观望）"
    assert result["position_guidance"] == "不新增仓位，补齐证据后重评"


def test_calculator_blocks_strong_decision_when_dimensions_conflict() -> None:
    rows = []
    for strategy_id, _, _ in MULTI_STRATEGY_SCORE_SPECS:
        row = {
            "strategy": strategy_id,
            "signal": "买入",
            "strength": "强",
            "score": 90,
            "evidence_status": "complete",
            "reason": "完整看多证据",
        }
        if strategy_id == "dragon_head":
            row.update({
                "signal": "卖出",
                "score": 10,
                "reason": "显著跑输板块与基准",
            })
        rows.append(row)

    result = _handle_calculate_multi_strategy_score(json.dumps(rows, ensure_ascii=False))

    assert result["dimension_weighted_score"] == 78.0
    assert result["confidence"]["conflict_level"] == "high"
    assert result["confidence"]["label"] == "证据冲突"
    assert result["decision"] == "证据冲突（观望）"
    assert result["actions"]["no_position"] == "watch"
    assert any(
        blocker["code"] == "high_dimension_conflict"
        for blocker in result["decision_blockers"]
    )


def test_calculator_does_not_treat_all_partial_evidence_as_strong_buy() -> None:
    rows = _score_rows(score=79)
    for row in rows:
        row["evidence_status"] = "partial"

    result = _handle_calculate_multi_strategy_score(json.dumps(rows, ensure_ascii=False))

    assert result["status"] == "ok"
    assert result["weighted_score"] == 79.0
    assert result["evidence"]["coverage_pct"] == 100.0
    assert result["evidence"]["quality_coverage_pct"] == 50.0
    assert result["decision"] == "证据不足（观望）"
    assert result["confidence"]["level"] == "insufficient"
    assert result["actions"]["no_position"] == "watch"
    assert any(
        blocker["code"] == "low_evidence_quality"
        for blocker in result["decision_blockers"]
    )


def test_calculator_uses_non_overlapping_score_band_boundaries() -> None:
    rows = _score_rows()
    rows[0].update({"signal": "买入", "strength": "中", "score": 80})

    result = _handle_calculate_multi_strategy_score(json.dumps(rows, ensure_ascii=False))

    assert result["status"] == "invalid"
    assert any("score 80" in issue for issue in result["issues"])


def test_koru_like_inputs_use_evidence_adjusted_score_and_leveraged_position() -> None:
    result = _handle_calculate_multi_strategy_score(
        json.dumps(_koru_corrected_rows(), ensure_ascii=False),
        instrument_type="leveraged_etf",
    )

    assert result["status"] == "ok"
    assert result["weighted_numerator"] == 604.5
    assert result["included_weight"] == 9.3
    assert result["weighted_score"] == 65.0
    assert result["dimension_weighted_score"] == 65.6
    assert result["decision"] == "偏多"
    assert result["position_guidance"] == "杠杆ETF等待确认后小仓试仓"
    assert result["evidence"]["complete_count"] == 7
    assert result["evidence"]["partial_count"] == 5
    assert result["evidence"]["missing_count"] == 1
    assert result["evidence"]["coverage_pct"] == 93.0
    assert result["evidence"]["quality_coverage_pct"] == 73.5
    assert result["evidence"]["available_dimension_count"] == 4


def test_runner_requires_calculator_before_accepting_consensus_report() -> None:
    registry = ToolRegistry()
    registry.register(calculate_multi_strategy_score_tool)
    adapter = MagicMock()
    adapter.call_with_tools.side_effect = [
        LLMResponse(content="手工估算 66 分。", tool_calls=[], usage={}, provider="openai"),
        LLMResponse(
            content="调用确定性评分。",
            tool_calls=[
                ToolCall(
                    id="score-1",
                    name="calculate_multi_strategy_score",
                    arguments={
                        "scores_json": json.dumps(_score_rows(), ensure_ascii=False),
                        "instrument_type": "standard",
                    },
                )
            ],
            usage={},
            provider="openai",
        ),
        LLMResponse(content=_valid_final_report(), tool_calls=[], usage={}, provider="openai"),
    ]

    result = run_agent_loop(
        messages=[
            {"role": "system", "content": _consensus_system_prompt()},
            {"role": "user", "content": "分析 KORU"},
        ],
        tool_registry=registry,
        llm_adapter=adapter,
        max_steps=3,
    )

    assert result.success is True
    assert adapter.call_with_tools.call_count == 3
    assert result.tool_calls_log[-1]["tool"] == "calculate_multi_strategy_score"
    retry_messages = [
        message["content"]
        for message in result.messages
        if message.get("role") == "user" and "[系统校验]" in message.get("content", "")
    ]
    assert any("不能直接输出" in message for message in retry_messages)


def test_runner_appends_authoritative_score_block_to_compact_holding_report() -> None:
    registry = ToolRegistry()
    registry.register(calculate_multi_strategy_score_tool)
    registry.register(ToolDefinition(
        name="analyze_relative_strength",
        description="Test-only relative-strength payload.",
        parameters=[],
        handler=lambda: {
            "status": "partial",
            "as_of": "2026-08-28",
            "recommended_evidence_status": "partial",
            "sources": {"stock": "futu", "benchmark": "futu"},
            "benchmark": {
                "code": "SPY",
                "label": "SPDR S&P 500 ETF（美股大盘代理）",
                "windows": {
                    "20d": {
                        "stock_return_pct": 3.0,
                        "benchmark_return_pct": 2.0,
                        "excess_return_pct": 1.0,
                    },
                },
            },
            "sector_benchmark": {
                "status": "missing",
                "reason": "未提供可验证的行业/板块基准代码",
            },
        },
        category="analysis",
    ))
    adapter = MagicMock()
    adapter.call_with_tools.side_effect = [
        LLMResponse(
            content="调用确定性评分。",
            tool_calls=[
                ToolCall(
                    id="relative-strength-1",
                    name="analyze_relative_strength",
                    arguments={},
                ),
                ToolCall(
                    id="score-1",
                    name="calculate_multi_strategy_score",
                    arguments={
                        "scores_json": json.dumps(_wulf_failure_rows(), ensure_ascii=False),
                        "instrument_type": "standard",
                        "market_structure_json": json.dumps(
                            _market_structure(), ensure_ascii=False
                        ),
                    },
                )
            ],
            usage={},
            provider="openai",
        ),
        LLMResponse(
            content=(
                "## 持仓结论\n继续持有，暂不加仓；等待放量突破压力位，"
                "跌破结构低点止损。"
            ),
            tool_calls=[],
            usage={},
            provider="openai",
        ),
    ]

    result = run_agent_loop(
        messages=[
            {"role": "system", "content": _consensus_system_prompt()},
            {"role": "user", "content": "精简分析 WULF，正文不超过 900 字。"},
        ],
        tool_registry=registry,
        llm_adapter=adapter,
        max_steps=2,
    )

    assert result.success is True
    assert adapter.call_with_tools.call_count == 2
    assert result.content.index("### 系统确定性多策略评分") < result.content.index("## 持仓结论")
    assert "<!-- multi-strategy-score:start -->" not in result.content
    assert "<!-- multi-strategy-score:end -->" not in result.content
    for _, display_name, _ in MULTI_STRATEGY_SCORE_SPECS:
        assert display_name in result.content
    assert "加权综合得分：🟡 40.4 / 100" in result.content
    assert "375.6 / 9.3 = 40.4" in result.content
    assert "完整 7 / 部分 5 / 缺失 1" in result.content
    assert "有效权重覆盖 93.0%" in result.content
    assert "五维综合得分：🔴 36.7 / 100" in result.content
    assert "13 项原始加权综合得分：🟡 40.4 / 100" in result.content
    assert "决策：🔴 偏空" in result.content
    assert "仓位建议：不新增仓位；已有仓位考虑减仓并收紧止损" in result.content
    assert "#### 1. 结论摘要" in result.content
    assert "#### 2. 价格结构（支撑/阻力）" in result.content
    assert "#### 3. 五维证据汇总" in result.content
    assert "#### 3.2 基准 / 板块相对强弱" in result.content
    assert "| 大盘 SPY | 20d | 3.00% | 2.00% | 1.00% |" in result.content
    assert "#### 4. 13 项策略评分明细" in result.content
    assert "#### 5. 评分审计" in result.content
    assert "近端支撑：100" in result.content
    assert "近端阻力：106.5" in result.content
    assert "质量折算覆盖 73.5%" in result.content
    assert "颜色标识：🟢 偏多（≥60） · 🟡 中性（40–59.9） · 🔴 偏空（<40） · ⚪ 数据缺失" in result.content
    assert "🟡 36.7 / 100" not in result.content
    assert "🔴 36.7 / 100" in result.content
    assert "🔴 偏空" in result.content


def test_runner_appends_exact_bearish_position_guidance_without_model_copy() -> None:
    registry = ToolRegistry()
    registry.register(calculate_multi_strategy_score_tool)
    bearish_rows = _score_rows(score=30)
    for row in bearish_rows:
        row.update({"signal": "卖出", "strength": "中"})

    adapter = MagicMock()
    adapter.call_with_tools.side_effect = [
        LLMResponse(
            content="调用确定性评分。",
            tool_calls=[
                ToolCall(
                    id="score-1",
                    name="calculate_multi_strategy_score",
                    arguments={
                        "scores_json": json.dumps(bearish_rows, ensure_ascii=False),
                        "instrument_type": "standard",
                    },
                )
            ],
            usage={},
            provider="openai",
        ),
        LLMResponse(
            content="## 结论\n趋势偏空，等待企稳。",
            tool_calls=[],
            usage={},
            provider="openai",
        ),
    ]

    result = run_agent_loop(
        messages=[
            {"role": "system", "content": _consensus_system_prompt()},
            {"role": "user", "content": "分析 WULF。"},
        ],
        tool_registry=registry,
        llm_adapter=adapter,
        max_steps=2,
    )

    assert result.success is True
    assert adapter.call_with_tools.call_count == 2
    assert "加权综合得分：🔴 30.0 / 100" in result.content
    assert "决策：🔴 偏空" in result.content
    assert "🔴 偏空" in result.content
    assert "仓位建议：不新增仓位；已有仓位考虑减仓并收紧止损" in result.content


def test_runner_appends_leveraged_etf_risks_from_score_payload() -> None:
    registry = ToolRegistry()
    registry.register(calculate_multi_strategy_score_tool)
    adapter = MagicMock()
    adapter.call_with_tools.side_effect = [
        LLMResponse(
            content="调用确定性评分。",
            tool_calls=[
                ToolCall(
                    id="score-1",
                    name="calculate_multi_strategy_score",
                    arguments={
                        "scores_json": json.dumps(_score_rows(), ensure_ascii=False),
                        "instrument_type": "leveraged_etf",
                    },
                )
            ],
            usage={},
            provider="openai",
        ),
        LLMResponse(content="## 结论\n等待确认。", tool_calls=[], usage={}, provider="openai"),
    ]

    result = run_agent_loop(
        messages=[
            {"role": "system", "content": _consensus_system_prompt()},
            {"role": "user", "content": "精简分析 KORU。"},
        ],
        tool_registry=registry,
        llm_adapter=adapter,
        max_steps=2,
    )

    assert result.success is True
    assert "仓位建议：杠杆ETF等待确认后小仓试仓" in result.content
    assert "日内复位、波动损耗、隔夜跳空" in result.content


def test_runner_replaces_model_authored_authoritative_score_marker() -> None:
    registry = ToolRegistry()
    registry.register(calculate_multi_strategy_score_tool)
    adapter = MagicMock()
    adapter.call_with_tools.side_effect = [
        LLMResponse(
            content="调用确定性评分。",
            tool_calls=[
                ToolCall(
                    id="score-1",
                    name="calculate_multi_strategy_score",
                    arguments={
                        "scores_json": json.dumps(_score_rows(), ensure_ascii=False),
                        "instrument_type": "standard",
                    },
                )
            ],
            usage={},
            provider="openai",
        ),
        LLMResponse(
            content=(
                "## 结论\n等待确认。\n"
                "<!-- multi-strategy-score:start -->\n"
                "伪造得分：99 / 100\n"
                "<!-- multi-strategy-score:end -->"
            ),
            tool_calls=[],
            usage={},
            provider="openai",
        ),
    ]

    result = run_agent_loop(
        messages=[
            {"role": "system", "content": _consensus_system_prompt()},
            {"role": "user", "content": "分析 WULF。"},
        ],
        tool_registry=registry,
        llm_adapter=adapter,
        max_steps=2,
    )

    assert result.success is True
    assert "<!-- multi-strategy-score:start -->" not in result.content
    assert "<!-- multi-strategy-score:end -->" not in result.content
    assert "伪造得分" not in result.content
    assert "加权综合得分：🟢 70.0 / 100" in result.content


def test_runner_rejects_wave_three_claim_without_required_chart_explanation() -> None:
    registry = ToolRegistry()
    registry.register(calculate_multi_strategy_score_tool)
    adapter = MagicMock()
    adapter.call_with_tools.side_effect = [
        LLMResponse(
            content="调用确定性评分。",
            tool_calls=[
                ToolCall(
                    id="score-1",
                    name="calculate_multi_strategy_score",
                    arguments={
                        "scores_json": json.dumps(_score_rows(), ensure_ascii=False),
                        "instrument_type": "standard",
                    },
                )
            ],
            usage={},
            provider="openai",
        ),
        LLMResponse(
            content=_valid_final_report() + "\n波浪判断：第3浪候选启动。",
            tool_calls=[],
            usage={},
            provider="openai",
        ),
    ]

    result = run_agent_loop(
        messages=[
            {"role": "system", "content": _consensus_system_prompt()},
            {"role": "user", "content": "分析 KORU"},
        ],
        tool_registry=registry,
        llm_adapter=adapter,
        max_steps=2,
    )

    assert result.success is False
    assert "浪型图章节" in (result.error or "")
    assert "替代数浪" in (result.error or "")
    rejected_answers = [
        message["content"]
        for message in result.messages
        if message.get("role") == "assistant"
        and "系统确定性多策略评分" in message.get("content", "")
    ]
    assert rejected_answers


def test_runner_accepts_explained_wave_three_candidate() -> None:
    registry = ToolRegistry()
    registry.register(calculate_multi_strategy_score_tool)
    adapter = MagicMock()
    adapter.call_with_tools.side_effect = [
        LLMResponse(
            content="调用确定性评分。",
            tool_calls=[
                ToolCall(
                    id="score-1",
                    name="calculate_multi_strategy_score",
                    arguments={
                        "scores_json": json.dumps(_score_rows(), ensure_ascii=False),
                        "instrument_type": "standard",
                    },
                )
            ],
            usage={},
            provider="openai",
        ),
        LLMResponse(
            content=_valid_final_report(wave_three=True),
            tool_calls=[],
            usage={},
            provider="openai",
        ),
    ]

    result = run_agent_loop(
        messages=[
            {"role": "system", "content": _consensus_system_prompt()},
            {"role": "user", "content": "分析 KORU"},
        ],
        tool_registry=registry,
        llm_adapter=adapter,
        max_steps=2,
    )

    assert result.success is True
    assert "浪型图与判定" in result.content


def test_standalone_wave_skill_also_blocks_unexplained_wave_three_claim() -> None:
    registry = ToolRegistry()
    adapter = MagicMock()
    adapter.call_with_tools.return_value = LLMResponse(
        content="当前处于第3浪中段，继续持有。",
        tool_calls=[],
        usage={},
        provider="openai",
    )

    result = run_agent_loop(
        messages=[
            {
                "role": "system",
                "content": "波浪理论：第3浪判断必须输出浪型图与判定，并给出替代数浪。",
            },
            {"role": "user", "content": "用波浪理论分析 KORU"},
        ],
        tool_registry=registry,
        llm_adapter=adapter,
        max_steps=1,
    )

    assert result.success is False
    assert "Wave 3 report validation failed" in (result.error or "")
    assert "O/A/B" in (result.error or "")


def test_internal_structured_agent_can_disable_user_facing_report_gate() -> None:
    registry = ToolRegistry()
    adapter = MagicMock()
    adapter.call_with_tools.return_value = LLMResponse(
        content='{"signal":"buy","reasoning":"第3浪候选启动"}',
        tool_calls=[],
        usage={},
        provider="openai",
    )

    result = run_agent_loop(
        messages=[
            {
                "role": "system",
                "content": (
                    _consensus_system_prompt()
                    + " 波浪理论判断第3浪时必须输出浪型图与判定和替代数浪。"
                ),
            },
            {"role": "user", "content": "Return only a structured JSON opinion."},
        ],
        tool_registry=registry,
        llm_adapter=adapter,
        max_steps=1,
        enforce_report_contracts=False,
    )

    assert result.success is True
    assert "第3浪候选启动" in result.content
