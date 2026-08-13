# -*- coding: utf-8 -*-
"""Regression tests for deterministic multi-strategy scoring and output gates."""

import json
from unittest.mock import MagicMock

from src.agent.llm_adapter import LLMResponse, ToolCall
from src.agent.runner import run_agent_loop
from src.agent.tools.analysis_tools import (
    MULTI_STRATEGY_SCORE_SPECS,
    _handle_calculate_multi_strategy_score,
    calculate_multi_strategy_score_tool,
)
from src.agent.tools.registry import ToolRegistry


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
        "12 项固定总权重是 9.2。"
    )


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
计算过程：644 / 9.2 = 70.0
证据覆盖：完整 12 / 部分 0 / 缺失 0，有效权重覆盖 100.0%
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


def test_calculator_uses_fixed_9_2_weight_and_programmatic_formula() -> None:
    result = _handle_calculate_multi_strategy_score(json.dumps(_score_rows()))

    assert result["status"] == "ok"
    assert result["weighted_numerator"] == 644.0
    assert result["included_weight"] == 9.2
    assert result["configured_total_weight"] == 9.2
    assert result["weighted_score"] == 70.0
    assert result["formula"] == "644 / 9.2 = 70"
    assert result["decision"] == "偏多"
    assert result["position_guidance"] == "中仓30-50%"


def test_calculator_tool_requires_explicit_instrument_classification() -> None:
    parameter = next(
        item
        for item in calculate_multi_strategy_score_tool.parameters
        if item.name == "instrument_type"
    )

    assert parameter.required is True
    assert parameter.default is None


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
    assert result["weighted_numerator"] == 595.0
    assert result["included_weight"] == 8.5
    assert result["weighted_score"] == 70.0
    assert result["evidence"] == {
        "complete_count": 11,
        "partial_count": 0,
        "missing_count": 1,
        "coverage_pct": 92.4,
    }
    assert result["position_guidance"] == "杠杆ETF上限10-15%"


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


def test_koru_like_inputs_use_evidence_adjusted_score_and_leveraged_position() -> None:
    result = _handle_calculate_multi_strategy_score(
        json.dumps(_koru_corrected_rows(), ensure_ascii=False),
        instrument_type="leveraged_etf",
    )

    assert result["status"] == "ok"
    assert result["weighted_numerator"] == 548.5
    assert result["included_weight"] == 8.5
    assert result["weighted_score"] == 64.5
    assert result["decision"] == "偏多"
    assert result["position_guidance"] == "杠杆ETF上限10-15%"
    assert result["evidence"] == {
        "complete_count": 7,
        "partial_count": 4,
        "missing_count": 1,
        "coverage_pct": 92.4,
    }


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
