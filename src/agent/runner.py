# -*- coding: utf-8 -*-
"""
Shared runner — extracted LLM + tool execution loop.

Provides ``run_agent_loop``, the single authoritative implementation of the
ReAct execute-loop that was previously inlined inside ``AgentExecutor._run_loop``.
All current and future agents should delegate to this runner instead of
re-implementing the loop themselves.

Design goals:
- Keep the same observable behaviour as the original ``_run_loop``
- Accept pluggable callbacks for progress, message history, and result handling
- Remain stateless — all mutable state lives in the caller
"""

from __future__ import annotations

import json
import logging
import re
import time
import contextvars
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from src.agent.llm_adapter import LLMToolAdapter
from src.agent.dashboard_payload import sanitize_agent_dashboard_payload
from src.agent.freshness import (
    reset_fresh_market_data_required,
    set_fresh_market_data_required,
    validate_fresh_market_data,
)
from src.agent.protocols import StageFailureReason
from src.agent.stream_events import stream_event
from src.agent.tools.registry import ToolRegistry
from src.agent.tools.execution import (
    _build_tool_cache_key,
    _guard_tool_stock_scope,
    _is_non_retriable_tool_result,
    _is_stock_scoped_tool,
    _normalize_guard_stock_code,
    _normalize_tool_stock_code,
    execute_runner_tool_call,
    serialize_tool_result,
)
from src.agent.stock_scope import StockScope
from src.llm.usage import should_persist_usage_telemetry
from src.utils.data_processing import normalize_report_signal_attribution
from src.storage import persist_llm_usage as _persist_usage

logger = logging.getLogger(__name__)

__all__ = [
    "RunLoopResult",
    "parse_dashboard_json",
    "run_agent_loop",
    "serialize_tool_result",
    "try_parse_json",
    "_build_tool_cache_key",
    "_guard_tool_stock_scope",
    "_is_non_retriable_tool_result",
    "_is_stock_scoped_tool",
    "_normalize_guard_stock_code",
    "_normalize_tool_stock_code",
]

# Tool name → friendly label for progress messages
_THINKING_TOOL_LABELS: Dict[str, str] = {
    "get_realtime_quote": "行情获取",
    "get_daily_history": "K线数据获取",
    "analyze_trend": "技术指标分析",
    "get_chip_distribution": "筹码分布分析",
    "search_stock_news": "新闻搜索",
    "search_comprehensive_intel": "综合情报搜索",
    "get_market_indices": "市场概览获取",
    "get_sector_rankings": "行业板块分析",
    "get_analysis_context": "历史分析上下文",
    "get_stock_info": "基本信息获取",
    "analyze_pattern": "K线形态识别",
    "analyze_ema200_setup": "EMA200结构判断",
    "analyze_vcp_h1_h2_buy": "VCP买点判断",
    "analyze_vcp_breakout_trader": "VCP突破判断",
    "get_volume_analysis": "量能分析",
    "calculate_ma": "均线计算",
    "calculate_multi_strategy_score": "多策略确定性计分",
    "get_skill_backtest_summary": "技能回测概览",
    "get_strategy_backtest_summary": "策略回测概览",
    "get_stock_backtest_summary": "个股回测数据",
}


# ============================================================
# RunLoopResult — the output of one run_agent_loop invocation
# ============================================================

@dataclass
class RunLoopResult:
    """Output produced by :func:`run_agent_loop`."""

    success: bool = False
    content: str = ""
    tool_calls_log: List[Dict[str, Any]] = field(default_factory=list)
    total_steps: int = 0
    total_tokens: int = 0
    provider: str = ""
    models_used: List[str] = field(default_factory=list)
    error: Optional[str] = None
    failure_reason: Optional[StageFailureReason] = None
    # Raw messages list at the end of the loop (callers may want to persist)
    messages: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def model(self) -> str:
        """Comma-separated de-duplicated model names used during the run."""
        return ", ".join(dict.fromkeys(m for m in self.models_used if m))


# ============================================================
# Helpers
# ============================================================

def parse_dashboard_json(content: str) -> Optional[Dict[str, Any]]:
    """Extract and parse a Decision Dashboard JSON from agent text.

    Tries multiple strategies:
    1. Markdown code blocks (```json ... ```)
    2. Raw JSON parse
    3. ``json_repair`` library
    4. Brace-delimited substring
    """
    if not content:
        return None

    from json_repair import repair_json

    # Strategy 1: markdown code blocks
    json_blocks = re.findall(r"```(?:json)?\s*\n?(.*?)\n?```", content, re.DOTALL)
    if json_blocks:
        for block in json_blocks:
            parsed = _try_parse_json(block)
            if parsed is not None:
                return _finalize_dashboard_payload(parsed)
            parsed = _try_repair_json(block, repair_json)
            if parsed is not None:
                return _finalize_dashboard_payload(parsed)

    # Strategy 2: raw parse
    parsed = _try_parse_json(content)
    if parsed is not None:
        return _finalize_dashboard_payload(parsed)

    # Strategy 3: json_repair on full content
    parsed = _try_repair_json(content, repair_json)
    if parsed is not None:
        return _finalize_dashboard_payload(parsed)

    # Strategy 4: brace-delimited
    brace_start = content.find("{")
    brace_end = content.rfind("}")
    if brace_start >= 0 and brace_end > brace_start:
        candidate = content[brace_start : brace_end + 1]
        parsed = _try_parse_json(candidate)
        if parsed is not None:
            return _finalize_dashboard_payload(parsed)
        parsed = _try_repair_json(candidate, repair_json)
        if parsed is not None:
            return _finalize_dashboard_payload(parsed)

    logger.warning("Failed to parse dashboard JSON from agent response")
    return None


def _finalize_dashboard_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Sanitize reserved fields before running normal dashboard normalization."""
    sanitized = sanitize_agent_dashboard_payload(payload)
    normalize_report_signal_attribution(sanitized)
    return sanitized


def try_parse_json(text: str) -> Optional[Dict[str, Any]]:
    """Best-effort JSON dict extraction from LLM text.

    Handles:
    1. Direct JSON parse
    2. Markdown code fences (```json ... ```)
    3. Brace-delimited substring
    4. ``json_repair`` fallback for slightly malformed JSON

    This is the shared utility that all agent ``post_process`` methods
    should use instead of duplicating the same logic.
    """
    if not text:
        return None

    candidates: List[str] = []
    cleaned = text.strip()
    if cleaned:
        candidates.append(cleaned)

    if cleaned.startswith("```"):
        unfenced = re.sub(r'^```(?:json)?\s*', '', cleaned)
        unfenced = re.sub(r'\s*```$', '', unfenced)
        if unfenced:
            candidates.append(unfenced.strip())

    fenced_blocks = re.findall(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    for block in fenced_blocks:
        block = block.strip()
        if block:
            candidates.append(block)

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        snippet = text[start:end + 1].strip()
        if snippet:
            candidates.append(snippet)

    seen: set[str] = set()
    unique_candidates: List[str] = []
    for candidate in candidates:
        if candidate not in seen:
            seen.add(candidate)
            unique_candidates.append(candidate)

    for candidate in unique_candidates:
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, ValueError):
            continue

    try:
        from json_repair import repair_json
    except Exception:
        repair_json = None

    if repair_json is not None:
        for candidate in unique_candidates:
            repaired = _try_repair_json(candidate, repair_json)
            if repaired is not None:
                return repaired

    return None


# Keep private alias used internally by parse_dashboard_json
_try_parse_json = try_parse_json


def _try_repair_json(text: str, repair_fn: Callable) -> Optional[Dict[str, Any]]:
    try:
        repaired = repair_fn(text)
        obj = json.loads(repaired)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


_MULTI_STRATEGY_SCORE_TOOL = "calculate_multi_strategy_score"
_WAVE_THREE_TERMS = (
    "第3浪候选",
    "第 3 浪候选",
    "第三浪候选",
    "三浪候选",
    "第3浪启动",
    "第 3 浪启动",
    "第三浪启动",
    "3浪启动",
    "第3浪已启动",
    "第 3 浪已启动",
    "第三浪已启动",
    "三浪启动",
    "三浪开启",
    "第三浪开启",
    "第3浪进行中",
    "第 3 浪进行中",
    "第三浪进行中",
    "3浪进行中",
    "第3浪中段",
    "第 3 浪中段",
    "第三浪中段",
    "第3浪初期",
    "第 3 浪初期",
    "第三浪初期",
)


def _requires_deterministic_multi_strategy_score(messages: List[Dict[str, Any]]) -> bool:
    """Detect the built-in consensus contract without changing every caller API."""
    return any(
        message.get("role") == "system"
        and _MULTI_STRATEGY_SCORE_TOOL in str(message.get("content") or "")
        and "12 项固定总权重" in str(message.get("content") or "")
        for message in messages
    )


def _requires_wave_three_diagram(messages: List[Dict[str, Any]]) -> bool:
    """Detect an active skill contract that requires explainable Wave 3 diagrams."""
    return any(
        message.get("role") == "system"
        and "浪型图与判定" in str(message.get("content") or "")
        and "替代数浪" in str(message.get("content") or "")
        and "第3浪" in str(message.get("content") or "").replace(" ", "")
        for message in messages
    )


def _latest_multi_strategy_score_payload(
    messages: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    for message in reversed(messages):
        if (
            message.get("role") != "tool"
            or message.get("name") != _MULTI_STRATEGY_SCORE_TOOL
        ):
            continue
        try:
            payload = json.loads(str(message.get("content") or ""))
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None
    return None


def _markdown_table_cell(value: Any) -> str:
    """Render an untrusted tool value safely inside one Markdown table cell."""
    if value is None:
        return "—"
    rendered = str(value).replace("\r\n", "\n").replace("\r", "\n")
    return rendered.replace("|", r"\|").replace("\n", "<br>") or "—"


def _format_multi_strategy_number(value: Any, *, decimals: Optional[int] = None) -> str:
    """Format deterministic score payload numbers without binary-float noise."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if decimals is not None:
        return f"{number:.{decimals}f}"
    return format(number, ".10g")


def _render_multi_strategy_score_section(payload: Dict[str, Any]) -> str:
    """Render the authoritative user-facing score block from a valid tool payload."""
    if payload.get("status") != "ok":
        return ""
    rows = payload.get("rows")
    evidence = payload.get("evidence")
    if not isinstance(rows, list) or not isinstance(evidence, dict):
        return ""

    lines = [
        "### 系统确定性多策略评分",
        "",
        "> 以下评分区块由系统根据 `calculate_multi_strategy_score` 的返回结果生成；如与正文中的手工复述冲突，以本区块为准。",
        "",
        "| # | 策略 | 维度 | 信号 | 强度 | 评分 | 权重 | 证据 | 关键依据 |",
        "|---|---|---|---|---|---:|---:|---|---|",
    ]
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue
        score = (
            _format_multi_strategy_number(row.get("score"))
            if row.get("included") is not False and row.get("score") is not None
            else "—"
        )
        weight = _format_multi_strategy_number(row.get("weight"), decimals=1)
        lines.append(
            "| "
            + " | ".join([
                str(index),
                _markdown_table_cell(row.get("display_name") or row.get("strategy")),
                _markdown_table_cell(row.get("dimension")),
                _markdown_table_cell(row.get("signal")),
                _markdown_table_cell(row.get("strength")),
                score,
                weight,
                _markdown_table_cell(row.get("evidence_status")),
                _markdown_table_cell(row.get("reason")),
            ])
            + " |"
        )

    expected_score = _format_multi_strategy_number(payload.get("weighted_score"), decimals=1)
    numerator = _format_multi_strategy_number(payload.get("weighted_numerator"))
    included_weight = _format_multi_strategy_number(payload.get("included_weight"))
    coverage = _format_multi_strategy_number(evidence.get("coverage_pct"), decimals=1)
    lines.extend([
        "",
        f"- **加权综合得分：{expected_score} / 100**",
        f"- 计算过程：{numerator} / {included_weight} = {expected_score}",
        "- 证据覆盖："
        f"完整 {evidence.get('complete_count', '—')} / "
        f"部分 {evidence.get('partial_count', '—')} / "
        f"缺失 {evidence.get('missing_count', '—')}，有效权重覆盖 {coverage}%",
        f"- 决策：{_markdown_table_cell(payload.get('decision'))}",
        f"- 仓位建议：{_markdown_table_cell(payload.get('position_guidance'))}",
        "- 评分性质：这是基于当前证据的综合评分，不是收益概率；同一维度内的同向策略可能存在相关性，不能按行数直接叠加信心。",
    ])
    if payload.get("instrument_type") == "leveraged_etf":
        lines.append("- 杠杆 ETF 风险：日内复位、波动损耗、隔夜跳空。")
    return "\n".join(lines)


def _append_multi_strategy_score_section(
    content: str,
    payload: Optional[Dict[str, Any]],
) -> str:
    """Replace model-authored blocks and place authoritative evidence before narrative."""
    if not isinstance(payload, dict):
        return content
    section = _render_multi_strategy_score_section(payload)
    if not section:
        return content
    start_marker = "<!-- multi-strategy-score:start -->"
    end_marker = "<!-- multi-strategy-score:end -->"
    narrative = re.sub(
        rf"\s*{re.escape(start_marker)}.*?{re.escape(end_marker)}\s*",
        "\n\n",
        content,
        flags=re.DOTALL,
    )
    narrative = narrative.replace(start_marker, "").replace(end_marker, "").rstrip()
    return f"{section}\n\n{narrative}" if narrative else section


def _wave_three_output_issues(content: str) -> List[str]:
    """Return missing explainability fields when a report claims a Wave 3 setup."""
    if not any(term in content for term in _WAVE_THREE_TERMS):
        return []

    issues: List[str] = []
    if "浪型图" not in content:
        issues.append("浪型图章节")
    code_blocks = re.findall(r"```(?:text)?\s*(.*?)```", content, re.DOTALL | re.IGNORECASE)
    chart = next(
        (block for block in code_blocks if all(marker in block for marker in ("O", "A", "B"))),
        "",
    )
    if not chart:
        issues.append("标有 O/A/B 的代码块浪型图")
    else:
        point_lines = {}
        for marker in ("O", "A", "B"):
            point_lines[marker] = next(
                (
                    line
                    for line in chart.splitlines()
                    if re.search(rf"(?:^|\s){marker}\s*[:：]", line)
                ),
                "",
            )
        current_line = next((line for line in chart.splitlines() if "当前" in line), "")
        dated_price_lines = [*point_lines.values(), current_line]
        date_pattern = re.compile(r"20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}")
        points_complete = True
        for line in dated_price_lines:
            if not line or not date_pattern.search(line):
                points_complete = False
                break
            without_date = date_pattern.sub("", line)
            without_wave_number = re.sub(r"第\s*\d+\s*浪", "", without_date)
            if not re.search(r"\d+(?:\.\d+)?", without_wave_number):
                points_complete = False
                break
        if not points_complete:
            issues.append("O/A/B/当前点的日期与价格")
    if "回撤" not in content:
        issues.append("第2浪回撤比例")
    if "量能" not in content:
        issues.append("量能确认说明")
    if "未满足" not in content and "不足" not in content:
        issues.append("未满足条件")
    if "替代" not in content:
        issues.append("替代数浪")
    if "确认位" not in content:
        issues.append("确认位")
    if "弱化位" not in content:
        issues.append("弱化位")
    if "证伪位" not in content and "失效位" not in content:
        issues.append("硬证伪位")
    if "置信度" not in content:
        issues.append("浪型置信度")
    if "1.0" not in content and "1.618" not in content:
        issues.append("浪型情景目标公式")
    return issues


def _validate_multi_strategy_final_answer(
    content: str,
    messages: List[Dict[str, Any]],
) -> tuple[bool, str, str]:
    """Validate that the final report faithfully uses the deterministic score result."""
    payload = _latest_multi_strategy_score_payload(messages)
    if payload is None:
        issue = "未调用 calculate_multi_strategy_score，或工具结果无法解析"
        return False, issue, (
            "[系统校验] 多策略联合报告不能直接输出。请先调用 "
            "calculate_multi_strategy_score；工具返回 invalid 时修正全部 12 项并重试。"
        )
    if payload.get("status") != "ok":
        issue = f"评分工具未通过校验：{payload.get('issues') or payload.get('error')}"
        return False, issue, (
            "[系统校验] 确定性评分工具返回 invalid。请按工具给出的 issues 修正 12 项，"
            "重新调用 calculate_multi_strategy_score 后再输出报告。"
        )

    expected_score = f"{float(payload['weighted_score']):.1f}"
    expected_numerator = format(float(payload.get("weighted_numerator")), ".10g")
    expected_weight = format(float(payload.get("included_weight")), ".10g")
    expected_decision = str(payload.get("decision") or "")
    expected_position = str(payload.get("position_guidance") or "")
    missing: List[str] = []
    if not re.search(
        rf"加权综合得分\s*[:：]\s*(?:\*\*)?{re.escape(expected_score)}\s*/\s*100",
        content,
    ):
        missing.append(f"工具得分 {expected_score}/100")
    if not re.search(
        rf"{re.escape(expected_numerator)}(?:\.0+)?\s*(?:/|÷)\s*"
        rf"{re.escape(expected_weight)}(?:\.0+)?(?:\D|$)",
        content,
    ):
        missing.append(f"计算式 {expected_numerator}/{expected_weight}")
    if expected_decision and expected_decision not in content:
        missing.append(f"决策 {expected_decision}")
    if expected_position and expected_position not in content:
        missing.append(f"仓位建议 {expected_position}")
    rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    absent_strategy_names = [
        str(row.get("display_name") or "")
        for row in rows
        if isinstance(row, dict)
        and row.get("display_name")
        and str(row["display_name"]) not in content
    ]
    if absent_strategy_names:
        missing.append("12项策略行（缺：" + "、".join(absent_strategy_names) + "）")
    evidence = payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {}
    evidence_expectations = (
        ("完整", evidence.get("complete_count")),
        ("部分", evidence.get("partial_count")),
        ("缺失", evidence.get("missing_count")),
    )
    for label, count in evidence_expectations:
        if count is not None and not re.search(rf"{label}\s*[:：]?\s*{count}(?:\D|$)", content):
            missing.append(f"证据{label}数 {count}")
    coverage = evidence.get("coverage_pct")
    if coverage is not None:
        expected_coverage = format(float(coverage), ".10g")
        if not re.search(rf"{re.escape(expected_coverage)}(?:\.0+)?\s*%", content):
            missing.append(f"证据覆盖率 {expected_coverage}%")
    if payload.get("instrument_type") == "leveraged_etf":
        for risk_term in ("日内复位", "波动损耗", "隔夜跳空"):
            if risk_term not in content:
                missing.append(risk_term)
    wave_issues = _wave_three_output_issues(content)
    missing.extend(wave_issues)
    if not missing:
        return True, "", ""

    issue = "最终报告未忠实复制工具结果或浪型解释不完整：" + "、".join(missing)
    retry_message = (
        "[系统校验] 最终报告未通过。本次具体缺失："
        + "、".join(missing)
        + "。确定性评分表、计算式、证据覆盖、决策和仓位由系统自动附加，"
        "请不要手工重写；只需重新输出修正后的分析正文。"
    )
    if wave_issues:
        retry_message += (
            " 报告既然判断第3浪候选/启动，还必须补齐带 O/A/B/当前点日期价格的"
            "代码块浪型图、第2浪回撤比例、量能支持与未满足项、替代数浪、确认位、"
            "弱化位、硬证伪位、1.0/1.618 情景目标公式和置信度。"
        )
    return False, issue, retry_message


def _remaining_timeout_seconds(
    start_time: float,
    max_wall_clock_seconds: Optional[float],
) -> Optional[float]:
    """Return remaining wall-clock budget in seconds, or None when disabled."""
    if max_wall_clock_seconds is None or max_wall_clock_seconds <= 0:
        return None
    return max(0.0, float(max_wall_clock_seconds) - (time.time() - start_time))


def _build_timeout_result(
    *,
    start_time: float,
    max_wall_clock_seconds: float,
    step: int,
    tool_calls_log: List[Dict[str, Any]],
    total_tokens: int,
    provider_used: str,
    models_used: List[str],
    messages: List[Dict[str, Any]],
) -> RunLoopResult:
    elapsed = time.time() - start_time
    return RunLoopResult(
        success=False,
        content="",
        tool_calls_log=tool_calls_log,
        total_steps=step,
        total_tokens=total_tokens,
        provider=provider_used,
        models_used=models_used,
        error=f"Agent timed out after {elapsed:.2f}s (limit: {max_wall_clock_seconds:.2f}s)",
        failure_reason=StageFailureReason.TIMEOUT,
        messages=messages,
    )


def _build_budget_guard_result(
    *,
    start_time: float,
    step: int,
    tool_calls_log: List[Dict[str, Any]],
    total_tokens: int,
    provider_used: str,
    models_used: List[str],
    messages: List[Dict[str, Any]],
    remaining_timeout_s: float,
    min_step_budget_s: float,
) -> RunLoopResult:
    elapsed = time.time() - start_time
    return RunLoopResult(
        success=False,
        content="",
        tool_calls_log=tool_calls_log,
        total_steps=step,
        total_tokens=total_tokens,
        provider=provider_used,
        models_used=models_used,
        error=(
            "Agent step skipped due to insufficient budget: "
            f"{remaining_timeout_s:.2f}s remaining, minimum {min_step_budget_s:.1f}s required"
        ),
        failure_reason=StageFailureReason.BUDGET_SKIP,
        messages=messages,
    )


def _is_cancel_requested(cancel_event: Optional[Any]) -> bool:
    """Return whether a cooperative cancellation event has been set."""
    if cancel_event is None:
        return False
    try:
        return bool(cancel_event.is_set())
    except Exception:
        return False


def _build_cancelled_result(
    *,
    step: int,
    tool_calls_log: List[Dict[str, Any]],
    total_tokens: int,
    provider_used: str,
    models_used: List[str],
    messages: List[Dict[str, Any]],
) -> RunLoopResult:
    return RunLoopResult(
        success=False,
        content="",
        tool_calls_log=tool_calls_log,
        total_steps=step,
        total_tokens=total_tokens,
        provider=provider_used,
        models_used=models_used,
        error="Agent execution cancelled",
        messages=messages,
    )


# ============================================================
# Core loop
# ============================================================

def run_agent_loop(
    *,
    messages: List[Dict[str, Any]],
    tool_registry: ToolRegistry,
    llm_adapter: LLMToolAdapter,
    max_steps: int = 10,
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    thinking_labels: Optional[Dict[str, str]] = None,
    max_wall_clock_seconds: Optional[float] = None,
    tool_call_timeout_seconds: Optional[float] = None,
    stock_scope: Optional[StockScope] = None,
    emit_stage_events: bool = True,
    cancel_event: Optional[Any] = None,
    require_fresh_market_data: bool = False,
    enforce_report_contracts: bool = True,
) -> RunLoopResult:
    """Execute the ReAct LLM ↔ tool loop.

    This is the *single shared implementation* of the agent execution loop.
    Both the legacy ``AgentExecutor`` and any future multi-agent runner
    should delegate here.

    Args:
        messages: The initial message list (system + user + optional history).
                  **Mutated in-place** — tool results are appended.
        tool_registry: Registry of callable tools.
        llm_adapter: LLM backend (handles multi-provider fallback).
        max_steps: Maximum number of LLM round-trips.
        progress_callback: Optional callback receiving progress dicts.
        thinking_labels: Override map of tool_name → friendly label.
        max_wall_clock_seconds: Optional overall timeout budget for the loop.
        tool_call_timeout_seconds: Optional timeout for one parallel tool batch.
        cancel_event: Optional ``threading.Event``-compatible cancellation signal.
        emit_stage_events: Whether to emit the synthetic ``agent_loop``
            stage lifecycle. Orchestrated business stages disable this so
            ``stage_start`` / ``stage_done`` only describe real stages.
        require_fresh_market_data: Require a successful current quote and
            network-refreshed daily history before accepting a final answer.
        enforce_report_contracts: Enforce user-facing multi-strategy and Wave 3
            report contracts. Structured internal sub-agents must disable this.

    Returns:
        A :class:`RunLoopResult` with the final content, stats, and the
        (mutated) messages list.
    """
    labels = thinking_labels or _THINKING_TOOL_LABELS
    tool_decls = tool_registry.to_openai_tools()

    start_time = time.time()
    tool_calls_log: List[Dict[str, Any]] = []
    non_retriable_tool_results: Dict[str, str] = {}
    total_tokens = 0
    provider_used = ""
    models_used: List[str] = []
    freshness_token = set_fresh_market_data_required(require_fresh_market_data)
    initial_message_count = len(messages)
    last_freshness_issue = ""
    require_multi_strategy_score = (
        enforce_report_contracts
        and _requires_deterministic_multi_strategy_score(messages)
    )
    last_multi_strategy_issue = ""
    require_wave_three_diagram = (
        enforce_report_contracts
        and _requires_wave_three_diagram(messages)
    )
    last_wave_three_issue = ""
    required_stock_codes = None
    if require_fresh_market_data and stock_scope is not None:
        required_stock_codes = getattr(stock_scope, "allowed_stock_codes", None)

    # Minimum seconds needed for a meaningful LLM round-trip.  If the
    # remaining budget is positive but below this threshold, the step will
    # almost certainly timeout mid-call, wasting a billed request.  Only
    # enforced from step 2 onwards so the first step always gets a chance
    # even when the total budget is small.
    _MIN_STEP_BUDGET_S = 8.0

    def _finish(result: RunLoopResult) -> RunLoopResult:
        reset_fresh_market_data_required(freshness_token)
        if progress_callback and emit_stage_events:
            progress_callback(
                stream_event(
                    "stage_done",
                    stage="agent_loop",
                    status="completed" if result.success else "failed",
                    duration=round(time.time() - start_time, 2),
                )
            )
        return result

    if progress_callback and emit_stage_events:
        progress_callback(
            stream_event(
                "stage_start",
                stage="agent_loop",
                message="Starting agent analysis...",
            )
        )

    for step in range(max_steps):
        if _is_cancel_requested(cancel_event):
            logger.info("Agent execution cancelled before step %d", step + 1)
            return _finish(_build_cancelled_result(
                step=step,
                tool_calls_log=tool_calls_log,
                total_tokens=total_tokens,
                provider_used=provider_used,
                models_used=models_used,
                messages=messages,
            ))

        remaining_timeout = _remaining_timeout_seconds(start_time, max_wall_clock_seconds)
        timeout_exhausted = remaining_timeout is not None and remaining_timeout <= 0
        budget_guard_triggered = (
            not timeout_exhausted
            and remaining_timeout is not None
            and step > 0
            and remaining_timeout <= _MIN_STEP_BUDGET_S
        )
        if timeout_exhausted or budget_guard_triggered:
            if budget_guard_triggered:
                logger.warning(
                    "Agent budget too low for step %d (%.1fs remaining, min %.1fs)",
                    step + 1,
                    remaining_timeout,
                    _MIN_STEP_BUDGET_S,
                )
                return _finish(_build_budget_guard_result(
                    start_time=start_time,
                    step=step,
                    tool_calls_log=tool_calls_log,
                    total_tokens=total_tokens,
                    provider_used=provider_used,
                    models_used=models_used,
                    messages=messages,
                    remaining_timeout_s=remaining_timeout,
                    min_step_budget_s=_MIN_STEP_BUDGET_S,
                ))

            if remaining_timeout <= 0:
                logger.warning("Agent timed out before step %d", step + 1)
            return _finish(_build_timeout_result(
                start_time=start_time,
                max_wall_clock_seconds=float(max_wall_clock_seconds),
                step=step,
                tool_calls_log=tool_calls_log,
                total_tokens=total_tokens,
                provider_used=provider_used,
                models_used=models_used,
                messages=messages,
            ))

        logger.info("Agent step %d/%d", step + 1, max_steps)

        # --- progress: thinking ---
        if progress_callback:
            if not tool_calls_log:
                thinking_msg = "正在制定分析路径..."
            else:
                last_tool = tool_calls_log[-1].get("tool", "")
                label = labels.get(last_tool, last_tool)
                thinking_msg = f"「{label}」已完成，继续深入分析..."
            progress_callback(stream_event("thinking", step=step + 1, message=thinking_msg))

        # --- LLM call ---
        llm_call_kwargs: Dict[str, Any] = {"timeout": remaining_timeout}
        if cancel_event is not None:
            llm_call_kwargs["cancel_event"] = cancel_event
        response = llm_adapter.call_with_tools(
            messages,
            tool_decls,
            **llm_call_kwargs,
        )
        provider_used = response.provider
        total_tokens += (response.usage or {}).get("total_tokens", 0)
        m = getattr(response, "model", "") or response.provider
        if m and m != "error":
            models_used.append(m)
        model_for_usage = m or response.provider
        if model_for_usage and model_for_usage != "error" and should_persist_usage_telemetry(response.usage):
            _persist_usage(response.usage, model_for_usage, call_type="agent")

        if _is_cancel_requested(cancel_event):
            logger.info("Agent execution cancelled after LLM call at step %d", step + 1)
            return _finish(_build_cancelled_result(
                step=step + 1,
                tool_calls_log=tool_calls_log,
                total_tokens=total_tokens,
                provider_used=provider_used,
                models_used=models_used,
                messages=messages,
            ))

        remaining_timeout = _remaining_timeout_seconds(start_time, max_wall_clock_seconds)
        if remaining_timeout is not None and remaining_timeout <= 0:
            logger.warning("Agent timed out after LLM call at step %d", step + 1)
            return _finish(_build_timeout_result(
                start_time=start_time,
                max_wall_clock_seconds=float(max_wall_clock_seconds),
                step=step + 1,
                tool_calls_log=tool_calls_log,
                total_tokens=total_tokens,
                provider_used=provider_used,
                models_used=models_used,
                messages=messages,
            ))

        if response.tool_calls:
            # ---- tool execution branch ----
            logger.info(
                "Agent requesting %d tool call(s): %s",
                len(response.tool_calls),
                [tc.name for tc in response.tool_calls],
            )

            # Append assistant message (with tool_calls) to history
            assistant_msg: Dict[str, Any] = {
                "role": "assistant",
                "content": response.content,
                "_trace_provider": response.provider,
                "_trace_model": m,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "name": tc.name,
                        "arguments": tc.arguments,
                        **({"provider_specific_fields": tc.provider_specific_fields} if tc.provider_specific_fields else {}),
                        **({"thought_signature": tc.thought_signature} if tc.thought_signature is not None else {}),
                    }
                    for tc in response.tool_calls
                ],
            }
            if response.reasoning_content is not None:
                assistant_msg["reasoning_content"] = response.reasoning_content
            if response.provider_blocks:
                assistant_msg["provider_blocks"] = response.provider_blocks
            messages.append(assistant_msg)

            # Execute tools (parallel when > 1)
            effective_tool_timeout = tool_call_timeout_seconds
            if remaining_timeout is not None:
                effective_tool_timeout = min(
                    remaining_timeout,
                    tool_call_timeout_seconds if tool_call_timeout_seconds and tool_call_timeout_seconds > 0 else remaining_timeout,
                )
            tool_results = _execute_tools(
                response.tool_calls,
                tool_registry,
                step + 1,
                progress_callback,
                tool_calls_log,
                non_retriable_tool_results,
                tool_wait_timeout_seconds=effective_tool_timeout,
                stock_scope=stock_scope,
            )

            if _is_cancel_requested(cancel_event):
                logger.info("Agent execution cancelled after tool execution at step %d", step + 1)
                return _finish(_build_cancelled_result(
                    step=step + 1,
                    tool_calls_log=tool_calls_log,
                    total_tokens=total_tokens,
                    provider_used=provider_used,
                    models_used=models_used,
                    messages=messages,
                ))

            # Append tool results preserving original call order
            tc_order = {tc.id: i for i, tc in enumerate(response.tool_calls)}
            tool_results.sort(key=lambda x: tc_order.get(x["tc"].id, 0))
            for tr in tool_results:
                messages.append(
                    {
                        "role": "tool",
                        "name": tr["tc"].name,
                        "tool_call_id": tr["tc"].id,
                        "content": tr["result_str"],
                    }
                )

            remaining_timeout = _remaining_timeout_seconds(start_time, max_wall_clock_seconds)
            if remaining_timeout is not None and remaining_timeout <= 0:
                logger.warning("Agent timed out after tool execution at step %d", step + 1)
                return _finish(_build_timeout_result(
                    start_time=start_time,
                    max_wall_clock_seconds=float(max_wall_clock_seconds),
                    step=step + 1,
                    tool_calls_log=tool_calls_log,
                    total_tokens=total_tokens,
                    provider_used=provider_used,
                    models_used=models_used,
                    messages=messages,
                ))

        else:
            # ---- final answer branch ----
            final_content = response.content or ""
            is_error = response.provider == "error"

            if require_fresh_market_data and not is_error:
                freshness = validate_fresh_market_data(
                    messages[initial_message_count:],
                    required_stock_codes=required_stock_codes,
                )
                if not freshness.ok:
                    last_freshness_issue = ", ".join(freshness.missing)
                    logger.warning(
                        "Agent final answer blocked by fresh-data gate: %s",
                        last_freshness_issue,
                    )
                    if step + 1 >= max_steps:
                        return _finish(RunLoopResult(
                            success=False,
                            content="",
                            tool_calls_log=tool_calls_log,
                            total_steps=step + 1,
                            total_tokens=total_tokens,
                            provider=provider_used,
                            models_used=models_used,
                            error=(
                                "Fresh market data requirement not met: "
                                f"{last_freshness_issue}"
                            ),
                            failure_reason=StageFailureReason.STAGE_FAILURE,
                            messages=messages,
                        ))
                    messages.append({"role": "user", "content": freshness.retry_message})
                    continue

            if require_multi_strategy_score and not is_error:
                score_payload = _latest_multi_strategy_score_payload(
                    messages[initial_message_count:]
                )
                final_content = _append_multi_strategy_score_section(
                    final_content,
                    score_payload,
                )
                score_ok, score_issue, retry_message = _validate_multi_strategy_final_answer(
                    final_content,
                    messages[initial_message_count:],
                )
                if not score_ok:
                    last_multi_strategy_issue = score_issue
                    logger.warning(
                        "Agent final answer blocked by deterministic multi-strategy gate: %s",
                        score_issue,
                    )
                    messages.append({
                        "role": "assistant",
                        "content": final_content,
                        "_trace_provider": response.provider,
                        "_trace_model": m,
                    })
                    if step + 1 >= max_steps:
                        return _finish(RunLoopResult(
                            success=False,
                            content="",
                            tool_calls_log=tool_calls_log,
                            total_steps=step + 1,
                            total_tokens=total_tokens,
                            provider=provider_used,
                            models_used=models_used,
                            error=f"Multi-strategy report validation failed: {score_issue}",
                            failure_reason=StageFailureReason.STAGE_FAILURE,
                            messages=messages,
                        ))
                    messages.append({"role": "user", "content": retry_message})
                    continue

            if require_wave_three_diagram and not require_multi_strategy_score and not is_error:
                wave_issues = _wave_three_output_issues(final_content)
                if wave_issues:
                    last_wave_three_issue = "、".join(wave_issues)
                    logger.warning(
                        "Agent final answer blocked by Wave 3 explainability gate: %s",
                        last_wave_three_issue,
                    )
                    if step + 1 >= max_steps:
                        return _finish(RunLoopResult(
                            success=False,
                            content="",
                            tool_calls_log=tool_calls_log,
                            total_steps=step + 1,
                            total_tokens=total_tokens,
                            provider=provider_used,
                            models_used=models_used,
                            error=(
                                "Wave 3 report validation failed: "
                                f"{last_wave_three_issue}"
                            ),
                            failure_reason=StageFailureReason.STAGE_FAILURE,
                            messages=messages,
                        ))
                    messages.append({
                        "role": "user",
                        "content": (
                            "[系统校验] 报告判断了第3浪候选/启动，但解释不完整。请重新输出，"
                            "补齐带 O/A/B/当前点日期价格的代码块浪型图、第2浪回撤比例、"
                            "量能支持与未满足项、替代数浪、确认位、弱化位、硬证伪位、"
                            "1.0/1.618 情景目标公式和置信度。"
                        ),
                    })
                    continue

            logger.info(
                "Agent completed in %d steps (%.1fs, %d tokens)",
                step + 1,
                time.time() - start_time,
                total_tokens,
            )
            if progress_callback:
                progress_callback(stream_event("generating", step=step + 1, message="正在生成最终分析..."))

            return _finish(RunLoopResult(
                success=not is_error and bool(final_content),
                content=final_content if not is_error else "",
                tool_calls_log=tool_calls_log,
                total_steps=step + 1,
                total_tokens=total_tokens,
                provider=provider_used,
                models_used=models_used,
                error=final_content if is_error else None,
                failure_reason=(StageFailureReason.STAGE_FAILURE if is_error else None),
                messages=messages,
            ))

    # Max steps exceeded
    logger.warning("Agent hit max steps (%d)", max_steps)
    return _finish(RunLoopResult(
        success=False,
        content="",
        tool_calls_log=tool_calls_log,
        total_steps=max_steps,
        total_tokens=total_tokens,
        provider=provider_used,
        models_used=models_used,
        error=(
            f"Fresh market data requirement not met: {last_freshness_issue}"
            if require_fresh_market_data and last_freshness_issue
            else (
                f"Multi-strategy report validation failed: {last_multi_strategy_issue}"
                if require_multi_strategy_score and last_multi_strategy_issue
                else (
                    f"Wave 3 report validation failed: {last_wave_three_issue}"
                    if require_wave_three_diagram and last_wave_three_issue
                    else f"Agent exceeded max steps ({max_steps}). Try increasing AGENT_MAX_STEPS if analysis tasks are complex."
                )
            )
        ),
        failure_reason=StageFailureReason.STAGE_FAILURE,
        messages=messages,
    ))


# ============================================================
# Internal tool execution
# ============================================================

def _execute_tools(
    tool_calls,
    tool_registry: ToolRegistry,
    step: int,
    progress_callback: Optional[Callable],
    tool_calls_log: List[Dict[str, Any]],
    non_retriable_tool_results: Optional[Dict[str, str]] = None,
    tool_wait_timeout_seconds: Optional[float] = None,
    stock_scope: Optional[StockScope] = None,
) -> List[Dict[str, Any]]:
    """Execute one or more tool calls, returning ordered result dicts.

    Single tools run inline; multiple tools run in parallel threads.
    """

    def _exec_single(tc_item):
        return execute_runner_tool_call(
            tool_call=tc_item,
            tool_registry=tool_registry,
            stock_scope=stock_scope,
            non_retriable_tool_results=non_retriable_tool_results,
        )

    results: List[Dict[str, Any]] = []

    if len(tool_calls) == 1:
        tc = tool_calls[0]
        if progress_callback:
            progress_callback(stream_event("tool_start", step=step, tool=tc.name))
        timeout_triggered = False
        if tool_wait_timeout_seconds and tool_wait_timeout_seconds > 0:
            pool = ThreadPoolExecutor(max_workers=1)
            ctx = contextvars.copy_context()
            try:
                future = pool.submit(ctx.run, _exec_single, tc)
                try:
                    _, result_str, success, dur, cached, guard_result = future.result(timeout=tool_wait_timeout_seconds)
                except FuturesTimeoutError:
                    timeout_triggered = True
                    future.cancel()
                    timeout_label = f"{tool_wait_timeout_seconds:.2f}s"
                    logger.warning("Tool '%s' timed out after %s at step %d", tc.name, timeout_label, step)
                    result_str = json.dumps({
                        "error": f"Tool execution timed out after {timeout_label}",
                        "timeout": True,
                    })
                    success = False
                    dur = round(tool_wait_timeout_seconds, 2)
                    cached = False
                    guard_result = None
            finally:
                pool.shutdown(wait=not timeout_triggered, cancel_futures=timeout_triggered)
        else:
            _, result_str, success, dur, cached, guard_result = _exec_single(tc)
        if progress_callback:
            progress_callback(stream_event("tool_done", step=step, tool=tc.name, success=success, duration=dur))
        log_entry = {
            "step": step, "tool": tc.name, "arguments": tc.arguments,
            "success": success, "duration": dur, "result_length": len(result_str),
            "cached": cached,
        }
        if tool_wait_timeout_seconds and tool_wait_timeout_seconds > 0 and not success:
            try:
                if json.loads(result_str).get("timeout") is True:
                    log_entry["timeout"] = True
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        if guard_result is not None:
            log_entry.update({
                "guarded": True,
                "expected_stock_code": guard_result.get("expected_stock_code"),
                "requested_stock_code": guard_result.get("requested_stock_code"),
                "allowed_stock_codes": guard_result.get("allowed_stock_codes", []),
            })
        tool_calls_log.append(log_entry)
        results.append({"tc": tc, "result_str": result_str})
    else:
        for tc in tool_calls:
            if progress_callback:
                progress_callback(stream_event("tool_start", step=step, tool=tc.name))

        pool = ThreadPoolExecutor(max_workers=min(len(tool_calls), 5))
        timeout_triggered = False
        try:
            futures = {pool.submit(contextvars.copy_context().run, _exec_single, tc): tc for tc in tool_calls}
            pending = set(futures)
            for future in as_completed(
                futures,
                timeout=tool_wait_timeout_seconds if tool_wait_timeout_seconds and tool_wait_timeout_seconds > 0 else None,
            ):
                pending.discard(future)
                tc_item, result_str, success, dur, cached, guard_result = future.result()
                if progress_callback:
                    progress_callback(stream_event("tool_done", step=step, tool=tc_item.name, success=success, duration=dur))
                log_entry = {
                    "step": step, "tool": tc_item.name, "arguments": tc_item.arguments,
                    "success": success, "duration": dur, "result_length": len(result_str),
                    "cached": cached,
                }
                if guard_result is not None:
                    log_entry.update({
                        "guarded": True,
                        "expected_stock_code": guard_result.get("expected_stock_code"),
                        "requested_stock_code": guard_result.get("requested_stock_code"),
                        "allowed_stock_codes": guard_result.get("allowed_stock_codes", []),
                    })
                tool_calls_log.append(log_entry)
                results.append({"tc": tc_item, "result_str": result_str})
        except FuturesTimeoutError:
            timeout_triggered = True
            timeout_label = (
                f"{tool_wait_timeout_seconds:.2f}s"
                if tool_wait_timeout_seconds is not None
                else "the configured limit"
            )
            logger.warning("Tool batch timed out after %s at step %d", timeout_label, step)
            for future, tc_item in futures.items():
                if future in pending:
                    future.cancel()
                    result_str = json.dumps({
                        "error": f"Tool execution timed out after {timeout_label}",
                        "timeout": True,
                    })
                    if progress_callback:
                        progress_callback(stream_event(
                            "tool_done",
                            step=step,
                            tool=tc_item.name,
                            success=False,
                            duration=round(tool_wait_timeout_seconds or 0.0, 2),
                        ))
                    tool_calls_log.append({
                        "step": step,
                        "tool": tc_item.name,
                        "arguments": tc_item.arguments,
                        "success": False,
                        "duration": round(tool_wait_timeout_seconds or 0.0, 2),
                        "result_length": len(result_str),
                        "cached": False,
                        "timeout": True,
                    })
                    results.append({"tc": tc_item, "result_str": result_str})
        finally:
            pool.shutdown(wait=not timeout_triggered, cancel_futures=timeout_triggered)

    return results
