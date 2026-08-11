# -*- coding: utf-8 -*-
"""
Shared factory for building fully-configured AgentExecutor instances.

Centralises construction to eliminate boilerplate duplicated across
api/v1/endpoints/agent.py, bot/commands/chat.py, bot/commands/ask.py,
and src/core/pipeline.py.

Performance notes
-----------------
* ``ToolRegistry`` is built once and cached at module level — tool
  registrations are immutable after setup so the object is safe to share
  across every request.
* ``SkillManager`` is expensive to create (loads YAML files from disk).
  A prototype is built on first use and cheap ``deepcopy`` clones are
  returned for each request, preserving thread-safety (``activate()``
  mutates internal state).

Usage::

    from src.agent.factory import build_agent_executor

    executor = build_agent_executor(config, skills=["bull_trend", "shrink_pullback"])
    result   = executor.chat(message="...", session_id="...")
"""

import copy
import logging
from dataclasses import dataclass
from typing import List, Optional

from src.config import AGENT_MAX_STEPS_DEFAULT

logger = logging.getLogger(__name__)

_EXCLUSIVE_META_SKILL_ID = "multi_strategy_consensus"

# ---------------------------------------------------------------------------
# Module-level caches
# ---------------------------------------------------------------------------
_TOOL_REGISTRY = None
_SKILL_MANAGER_PROTOTYPE = None
# Sentinel used as initial value so None (i.e. no custom dir) compares as "changed"
# on the very first call, forcing a build rather than accidentally skipping it.
_SENTINEL = object()
# Track which custom_dir the prototype was built with so we can invalidate
# the cache if AGENT_SKILL_DIR changes at runtime (e.g. via config reload).
_SKILL_MANAGER_CUSTOM_DIR: object = _SENTINEL


@dataclass
class SkillPromptState:
    """Resolved skill activation + prompt fragments for analysis entrypoints."""

    skill_manager: object
    skills_to_activate: List[str]
    explicit_skill_selection: bool
    use_legacy_default_prompt: bool
    skill_instructions: str
    skill_execution_plan: str
    default_skill_policy: str
    technical_skill_policy: str


def _coerce_config_int(raw_value: object, default: int, *, field_name: str | None = None) -> int:
    """Coerce optional numeric config values to int with a fallback default.

    This protects test doubles and incomplete config objects from propagating
    mock-like values (e.g., MagicMock attributes) into strict numeric paths.

    This function is side-effect free: it only returns a parsed int fallback value
    and intentionally never mutates source config attributes.
    """

    try:
        return int(raw_value)
    except (TypeError, ValueError, OverflowError):
        if field_name:
            logger.warning(
                "[AgentFactory] Invalid value for %s: %r, fallback to default %s",
                field_name,
                raw_value,
                default,
            )
        return default


def _normalize_skill_ids(
    skill_ids: Optional[List[str]],
    *,
    available_skill_ids: set[str],
) -> tuple[List[str], List[str]]:
    """Return validated skill ids plus unknown ids, preserving input order."""
    normalized: List[str] = []
    unknown: List[str] = []

    for skill_id in skill_ids or []:
        if not isinstance(skill_id, str):
            continue
        cleaned = skill_id.strip()
        if not cleaned:
            continue
        if cleaned == "all":
            if "all" not in normalized:
                normalized.append("all")
            continue
        if cleaned in available_skill_ids:
            if cleaned not in normalized:
                normalized.append(cleaned)
            continue
        if cleaned not in unknown:
            unknown.append(cleaned)

    return normalized, unknown


def _normalize_exclusive_skill_ids(skill_ids: List[str]) -> List[str]:
    """Keep the broad consensus meta-skill exclusive from specialist skills.

    ``multi_strategy_consensus`` owns a strict 12-row report contract.  Combining
    it with specialist frameworks (for example Serenity + value investing)
    makes their required tools and output sections compete in one prompt.  When
    an older client submits that invalid combination, the explicitly selected
    specialist skills win over the broad Web default.
    """
    if _EXCLUSIVE_META_SKILL_ID not in skill_ids or len(skill_ids) == 1:
        return skill_ids

    normalized = [
        skill_id for skill_id in skill_ids
        if skill_id != _EXCLUSIVE_META_SKILL_ID
    ]
    logger.warning(
        "[AgentFactory] Dropping exclusive meta-skill %s from combined selection; specialist skills=%s",
        _EXCLUSIVE_META_SKILL_ID,
        normalized,
    )
    return normalized


def normalize_requested_skill_ids(config, skill_ids: List[str]) -> List[str]:
    """Normalize API-requested Skill ids with the AgentFactory catalog rules."""
    skill_manager = get_skill_manager(config)
    available_skill_ids = {
        str(getattr(skill, "name", "")).strip()
        for skill in skill_manager.list_skills()
        if str(getattr(skill, "name", "")).strip()
    }
    normalized, unknown = _normalize_skill_ids(
        skill_ids,
        available_skill_ids=available_skill_ids,
    )
    if unknown:
        logger.warning("[AgentFactory] Ignoring unknown request skill ids: %s", unknown)
    return _normalize_exclusive_skill_ids(normalized)


def _resolve_selected_skill_ids(
    *,
    requested_skills: Optional[List[str]],
    configured_skills: Optional[List[str]],
    default_skills: List[str],
    available_skill_ids: set[str],
) -> tuple[List[str], bool]:
    """Resolve active skill ids and whether they came from a valid explicit selection."""
    selection_source = None
    raw_skill_ids = None
    if requested_skills is not None:
        selection_source = "request"
        raw_skill_ids = requested_skills
    elif configured_skills is not None:
        selection_source = "config"
        raw_skill_ids = configured_skills
    else:
        return list(default_skills), False

    selected_skill_ids, unknown_skill_ids = _normalize_skill_ids(
        raw_skill_ids,
        available_skill_ids=available_skill_ids,
    )
    selected_skill_ids = _normalize_exclusive_skill_ids(selected_skill_ids)
    if unknown_skill_ids:
        logger.warning(
            "[AgentFactory] Ignoring unknown %s skill ids: %s",
            selection_source,
            unknown_skill_ids,
        )
    if selected_skill_ids:
        return selected_skill_ids, True

    if raw_skill_ids:
        logger.warning(
            "[AgentFactory] No valid %s skills remain after validation; falling back to default skills: %s",
            selection_source,
            default_skills,
        )
    return list(default_skills), False


def _build_skill_execution_plan(
    *,
    skill_catalog: List[object],
    skills_to_activate: List[str],
    use_legacy_default_prompt: bool,
) -> str:
    """Build a deterministic execution contract for active skill prompts."""
    if use_legacy_default_prompt:
        return ""

    if "all" in skills_to_activate:
        active_skills = list(skill_catalog)
    else:
        skills_by_id = {
            str(getattr(skill, "name", "")).strip(): skill
            for skill in skill_catalog
        }
        active_skills = [
            skills_by_id[skill_id]
            for skill_id in skills_to_activate
            if skill_id in skills_by_id
        ]

    if not active_skills:
        return ""

    skill_lines: List[str] = []
    all_required_tools: List[str] = []
    for index, skill in enumerate(active_skills, start=1):
        skill_id = str(getattr(skill, "name", "")).strip()
        display_name = str(getattr(skill, "display_name", "")).strip() or skill_id
        required_tools = [
            str(tool_name).strip()
            for tool_name in (getattr(skill, "required_tools", None) or [])
            if str(tool_name).strip()
        ]
        for tool_name in required_tools:
            if tool_name not in all_required_tools:
                all_required_tools.append(tool_name)
        tools_text = (
            "、".join(f"`{tool_name}`" for tool_name in required_tools)
            or "无额外专项工具"
        )
        skill_lines.append(
            f"{index}. **{display_name}** (`{skill_id}`)：{tools_text}"
        )

    skill_list_text = "\n".join(skill_lines)
    combined_tools = (
        "、".join(f"`{tool_name}`" for tool_name in all_required_tools)
        or "无"
    )
    multi_skill_section = ""
    if len(active_skills) > 1:
        multi_skill_section = """
- 这些技能是本轮都要完成的分析任务，不是备选项；不得完成第一个技能后直接结束。
- 最终输出必须能清楚识别每个技能的独立结果，并按上述顺序呈现；综合结论不能替代任一技能结果。
- 单个技能中的“严格输出”“不要额外章节”等限制只约束该技能自己的部分，不得据此删除其他已激活技能的内容。
"""

    return f"""## 激活技能执行计划（优先于通用工作流与单技能局部格式限制）

按用户选择顺序执行：
{skill_list_text}

本轮专项工具并集（去重后按声明顺序）：{combined_tools}

- 通用行情/技术/新闻流程只是最低基线；在最终回答前，还必须完成上面各技能声明的专项工具。已有可信上下文或本轮成功结果可以复用。
- 工具失败时记录具体失败与降级影响，继续完成对应技能的其余分析；不得静默跳过，也不得编造结果。{multi_skill_section}"""


def _should_use_legacy_default_prompt(
    *,
    skills_to_activate: List[str],
    explicit_skill_selection: bool,
    skill_catalog: List[object],
) -> bool:
    """Keep the legacy prompt only for the implicit built-in bull_trend fallback."""
    if explicit_skill_selection or skills_to_activate != ["bull_trend"]:
        return False

    bull_trend_skill = next(
        (
            skill
            for skill in skill_catalog
            if str(getattr(skill, "name", "")).strip() == "bull_trend"
        ),
        None,
    )
    return getattr(bull_trend_skill, "source", None) == "builtin"


def get_tool_registry():
    """Return a cached ToolRegistry (built once, shared across requests)."""
    global _TOOL_REGISTRY
    if _TOOL_REGISTRY is not None:
        return _TOOL_REGISTRY

    from src.agent.tools.registry import ToolRegistry
    from src.agent.tools.data_tools import ALL_DATA_TOOLS
    from src.agent.tools.analysis_tools import ALL_ANALYSIS_TOOLS
    from src.agent.tools.search_tools import ALL_SEARCH_TOOLS
    from src.agent.tools.market_tools import ALL_MARKET_TOOLS
    from src.agent.tools.backtest_tools import ALL_BACKTEST_TOOLS
    from src.agent.tools.value_analysis_tools import ALL_VALUE_ANALYSIS_TOOLS

    registry = ToolRegistry()
    for tool_fn in ALL_DATA_TOOLS + ALL_ANALYSIS_TOOLS + ALL_SEARCH_TOOLS + ALL_MARKET_TOOLS + ALL_BACKTEST_TOOLS + ALL_VALUE_ANALYSIS_TOOLS:
        registry.register(tool_fn)

    _TOOL_REGISTRY = registry
    logger.info("[AgentFactory] ToolRegistry cached (%d tools)", len(registry._tools) if hasattr(registry, "_tools") else -1)
    return _TOOL_REGISTRY


def get_skill_manager(config=None):
    """Return a deepcopy-clone of the cached SkillManager prototype.

    The prototype is initialised from disk on first call; subsequent calls
    return ``copy.deepcopy(prototype)`` which is ~10× faster than re-reading
    YAML files.  Each clone is independent so ``.activate()`` calls do not
    bleed between requests.

    Cache invalidation: if ``config.agent_skill_dir`` changes at runtime
    (e.g. via the web settings reload), the prototype is rebuilt automatically.
    """
    global _SKILL_MANAGER_PROTOTYPE, _SKILL_MANAGER_CUSTOM_DIR

    if config is None:
        from src.config import get_config
        config = get_config()

    current_custom_dir = getattr(config, "agent_skill_dir", None)
    if _SKILL_MANAGER_PROTOTYPE is not None and current_custom_dir == _SKILL_MANAGER_CUSTOM_DIR:
        return copy.deepcopy(_SKILL_MANAGER_PROTOTYPE)

    from src.agent.skills.base import SkillManager

    if _SKILL_MANAGER_PROTOTYPE is not None:
        logger.info("[AgentFactory] SkillManager prototype invalidated (agent_skill_dir changed: %r -> %r)",
                    _SKILL_MANAGER_CUSTOM_DIR, current_custom_dir)

    skill_manager = SkillManager()
    skill_manager.load_builtin_skills()

    if current_custom_dir:
        try:
            skill_manager.load_custom_skills(current_custom_dir)
        except Exception as exc:
            logger.warning("[AgentFactory] Failed to load custom skills from %s: %s", current_custom_dir, exc)

    _SKILL_MANAGER_PROTOTYPE = skill_manager
    _SKILL_MANAGER_CUSTOM_DIR = current_custom_dir
    logger.info("[AgentFactory] SkillManager prototype cached (%d skills)", len(skill_manager._skills))
    return copy.deepcopy(_SKILL_MANAGER_PROTOTYPE)


def resolve_skill_prompt_state(config=None, skills: Optional[List[str]] = None) -> SkillPromptState:
    """Resolve active skills and prompt fragments for analyzer / agent entrypoints."""
    if config is None:
        from src.config import get_config
        config = get_config()

    from src.agent.skills.defaults import (
        get_default_active_skill_ids,
        get_default_technical_skill_policy,
        get_default_trading_skill_policy,
    )

    skill_manager = get_skill_manager(config)
    skill_catalog = list(skill_manager.list_skills())
    available_skill_ids = {
        str(getattr(skill, "name", "")).strip()
        for skill in skill_catalog
        if str(getattr(skill, "name", "")).strip()
    }
    configured_skills = getattr(config, "agent_skills", None)
    if configured_skills == []:
        configured_skills = None
    default_skills = get_default_active_skill_ids(
        skill_catalog,
        available_skill_ids=available_skill_ids or None,
    )
    skills_to_activate, explicit_skill_selection = _resolve_selected_skill_ids(
        requested_skills=skills,
        configured_skills=configured_skills,
        default_skills=default_skills,
        available_skill_ids=available_skill_ids,
    )

    use_legacy_default_prompt = _should_use_legacy_default_prompt(
        skills_to_activate=skills_to_activate,
        explicit_skill_selection=explicit_skill_selection,
        skill_catalog=skill_catalog,
    )

    skill_execution_plan = _build_skill_execution_plan(
        skill_catalog=skill_catalog,
        skills_to_activate=skills_to_activate,
        use_legacy_default_prompt=use_legacy_default_prompt,
    )

    skill_manager.activate(skills_to_activate)
    logger.info("[AgentFactory] Activated skills: %s", skills_to_activate)

    return SkillPromptState(
        skill_manager=skill_manager,
        skills_to_activate=skills_to_activate,
        explicit_skill_selection=explicit_skill_selection,
        use_legacy_default_prompt=use_legacy_default_prompt,
        skill_instructions=skill_manager.get_skill_instructions(),
        skill_execution_plan=skill_execution_plan,
        default_skill_policy=get_default_trading_skill_policy(
            explicit_skill_selection=not use_legacy_default_prompt,
        ),
        technical_skill_policy=get_default_technical_skill_policy(
            explicit_skill_selection=not use_legacy_default_prompt,
        ),
    )


def build_agent_executor(config=None, skills: Optional[List[str]] = None):
    """Build and return a configured AgentExecutor (or future orchestrator).

    When ``AGENT_ARCH=multi``, this returns an orchestrator that manages
    multiple specialised agents. Otherwise it returns the legacy single-agent
    executor.

    Args:
        config: Application config object.  When *None*, ``get_config()`` is
                called automatically.
        skills: Skill ids to activate.  When *None* falls back to
                ``config.agent_skills``; if that is also empty falls back to
                the central default skill set.

    Returns:
        A ready-to-call :class:`src.agent.executor.AgentExecutor` instance.
    """
    if config is None:
        from src.config import get_config
        config = get_config()

    arch = getattr(config, "agent_arch", "single")

    from src.agent.llm_adapter import LLMToolAdapter

    registry = get_tool_registry()
    prompt_state = resolve_skill_prompt_state(config, skills=skills)
    skill_manager = prompt_state.skill_manager
    logger.info(
        "[AgentFactory] Resolved skill prompt state: skills=%s (arch=%s, explicit=%s, legacy_default_prompt=%s)",
        prompt_state.skills_to_activate,
        arch,
        prompt_state.explicit_skill_selection,
        prompt_state.use_legacy_default_prompt,
    )

    llm_adapter = LLMToolAdapter(config)

    if arch == "multi":
        return _build_orchestrator(
            config,
            registry,
            llm_adapter,
            skill_manager,
            skill_execution_plan=prompt_state.skill_execution_plan,
            technical_skill_policy=prompt_state.technical_skill_policy,
        )

    from src.agent.executor import AgentExecutor
    # Intentionally do not mutate config routing fields here. We only coerce
    # execution params (max_steps/timeout_seconds) from config values; provider,
    # model, base URL and channel routes stay unchanged and are consumed by
    # downstream adapter logic as-is.
    return AgentExecutor(
        tool_registry=registry,
        llm_adapter=llm_adapter,
        skill_instructions=prompt_state.skill_instructions,
        skill_execution_plan=prompt_state.skill_execution_plan,
        default_skill_policy=prompt_state.default_skill_policy,
        use_legacy_default_prompt=prompt_state.use_legacy_default_prompt,
        max_steps=_coerce_config_int(
            getattr(config, "agent_max_steps", AGENT_MAX_STEPS_DEFAULT),
            AGENT_MAX_STEPS_DEFAULT,
            field_name="agent_max_steps",
        ),
        timeout_seconds=_coerce_config_int(
            getattr(config, "agent_orchestrator_timeout_s", 0),
            0,
            field_name="agent_orchestrator_timeout_s",
        ),
    )


def build_agent_chat_executor(config=None, skills: Optional[List[str]] = None):
    """Build the backend-neutral executor used only by Agent Chat endpoints."""
    if config is None:
        from src.config import get_config

        config = get_config()

    from src.agent.agent_backend import (
        AgentBackendConfigError,
        LiteLLMAgentBackend,
        resolve_agent_backend_id,
    )
    from src.agent.chat_executor import AgentChatExecutor

    backend_id = resolve_agent_backend_id(config)
    arch = str(getattr(config, "agent_arch", "single") or "single").strip().lower()
    if backend_id == "codex_app_server" and arch != "single":
        raise AgentBackendConfigError(
            "unsupported_agent_arch",
            "Codex Agent currently supports single-agent Chat only",
        )
    if backend_id == "litellm" and arch == "multi":
        return build_agent_executor(config, skills=skills)

    registry = get_tool_registry()
    prompt_state = resolve_skill_prompt_state(config, skills=skills)
    if backend_id == "litellm":
        from src.agent.llm_adapter import LLMToolAdapter

        context_llm_adapter = LLMToolAdapter(config)
        backend = LiteLLMAgentBackend(registry, context_llm_adapter)
    else:
        from src.agent.codex_agent_backend import CodexAgentBackend
        from src.agent.tool_surface import ToolSurface

        context_llm_adapter = None
        backend = CodexAgentBackend(ToolSurface(registry), config)

    return AgentChatExecutor(
        backend=backend,
        config=config,
        context_llm_adapter=context_llm_adapter,
        skill_instructions=prompt_state.skill_instructions,
        skill_execution_plan=getattr(prompt_state, "skill_execution_plan", ""),
        default_skill_policy=prompt_state.default_skill_policy,
        use_legacy_default_prompt=prompt_state.use_legacy_default_prompt,
        max_steps=_coerce_config_int(
            getattr(config, "agent_max_steps", AGENT_MAX_STEPS_DEFAULT),
            AGENT_MAX_STEPS_DEFAULT,
            field_name="agent_max_steps",
        ),
        timeout_seconds=_coerce_config_int(
            getattr(config, "agent_orchestrator_timeout_s", 0),
            0,
            field_name="agent_orchestrator_timeout_s",
        ),
    )


def _build_orchestrator(
    config,
    registry,
    llm_adapter,
    skill_manager,
    *,
    skill_execution_plan: str = "",
    technical_skill_policy: str = "",
):
    """Build and return an :class:`AgentOrchestrator` (multi-agent mode).

    The orchestrator presents the same ``run()`` / ``chat()`` interface as
    :class:`AgentExecutor` so callers need no changes.
    """
    from src.agent.orchestrator import AgentOrchestrator

    mode = getattr(config, "agent_orchestrator_mode", "standard")
    logger.info("[AgentFactory] Building AgentOrchestrator (mode=%s)", mode)

    skill_instructions = skill_manager.get_skill_instructions()
    if skill_execution_plan:
        skill_instructions = f"{skill_execution_plan}\n\n{skill_instructions}"

    return AgentOrchestrator(
        tool_registry=registry,
        llm_adapter=llm_adapter,
        skill_instructions=skill_instructions,
        technical_skill_policy=technical_skill_policy,
        max_steps=_coerce_config_int(
            getattr(config, "agent_max_steps", AGENT_MAX_STEPS_DEFAULT),
            AGENT_MAX_STEPS_DEFAULT,
            field_name="agent_max_steps",
        ),
        mode=mode,
        skill_manager=skill_manager,
        config=config,
    )


# Keep legacy alias so any external callers using the old name still work.
build_executor = build_agent_executor
