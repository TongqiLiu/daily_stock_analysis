#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


AUTO_PE_MIN_BEAR_EPS = 0.25
AUTO_PE_MIN_BASE_EPS = 0.50
NEAR_BASE_BAND_PCT = 5.0
SPECULATIVE_EV_SALES_MULTIPLE = 30.0
TARGET_DIVERGENCE_WARNING_PCT = 30.0
TARGET_DIVERGENCE_MAJOR_PCT = 50.0
DEVELOPMENT_POLICY_LTM_IMPLIED = "ltm_implied_multiple_sensitivity"
DEVELOPMENT_LTM_MULTIPLE_FACTORS = {
    "bear": 0.425,
    "base": 0.85,
    "bull": 1.50,
}
MODEL_CONFIDENCE_HIGH = "high"
MODEL_CONFIDENCE_MEDIUM = "medium"
MODEL_CONFIDENCE_LOW = "low"
MODEL_USE_INDEPENDENT_SIGNAL = "independent_fair_value_signal"
MODEL_USE_DIRECTIONAL_WITH_WARNINGS = "directional_fair_value_with_warnings"
MODEL_USE_DIAGNOSTIC_ONLY = "diagnostic_or_watchlist_only"
MODEL_USE_MARKET_SENSITIVITY_ONLY = "market_implied_sensitivity_only"
MODEL_USE_MIXED_BASIS_REQUIRES_SPLIT = "mixed_basis_requires_split"
MODEL_USE_LOW_CONFIDENCE_NO_STANDALONE_SIGNAL = "low_confidence_no_standalone_signal"
HIGH_SEVERITY_RISK_FLAGS = {
    "cyclical_peak_eps_risk",
    "current_revenue_cannot_explain_market_cap",
    "reverse_valuation_primary",
    "pre_revenue_or_story_stock",
    "uncontracted_pipeline",
    "dilution_or_financing_risk",
    "customer_concentration_risk",
    "regulatory_or_approval_binary_risk",
    "business_model_transition_risk",
}
MEDIUM_SEVERITY_RISK_FLAGS = {
    "margin_path_unproven",
    "execution_risk",
    "early_project_execution_risk",
    "hardware_rerating_or_margin_path_required",
    "short_public_history",
    "commodity_cycle_risk",
    "policy_sensitive_demand",
    "high_multiple_duration_risk",
}
VALID_MODES = {"auto", "pe", "ev_sales", "development_ev_sales"}
VALUATION_BASIS_INDEPENDENT = "independent_fair_value"
VALUATION_BASIS_MARKET_IMPLIED = "market_implied_sensitivity"
VALUATION_BASIS_HYBRID = "hybrid"
VALUATION_BASIS_MARKET_SENTIMENT = "market_sentiment_sensitivity"
VALID_VALUATION_BASIS_TYPES = {
    VALUATION_BASIS_INDEPENDENT,
    VALUATION_BASIS_MARKET_IMPLIED,
    VALUATION_BASIS_HYBRID,
}
VALID_FAMILY_BASIS_TYPES = VALID_VALUATION_BASIS_TYPES | {VALUATION_BASIS_MARKET_SENTIMENT}
CUSTOM_FRAMEWORK = "custom_framework"
MULTIPLE_FRAMEWORKS = {
    "traditional_memory_cycle_pe": {
        "mode": "pe",
        "multiples": {"bear": 5.0, "base": 7.0, "bull": 9.0},
        "basisType": VALUATION_BASIS_INDEPENDENT,
    },
    "ai_memory_upcycle_pe": {
        "mode": "pe",
        "multiples": {"bear": 8.0, "base": 10.0, "bull": 12.0},
        "basisType": VALUATION_BASIS_INDEPENDENT,
    },
    "cyclical_semiconductor_pe": {
        "mode": "pe",
        "multiples": {"bear": 6.0, "base": 8.0, "bull": 10.0},
        "basisType": VALUATION_BASIS_INDEPENDENT,
    },
    "mature_semiconductor_pe": {
        "mode": "pe",
        "multiples": {"bear": 18.0, "base": 25.0, "bull": 32.0},
        "basisType": VALUATION_BASIS_INDEPENDENT,
    },
    "premium_ai_platform_pe": {
        "mode": "pe",
        "multiples": {"bear": 20.0, "base": 25.0, "bull": 30.0},
        "basisType": VALUATION_BASIS_INDEPENDENT,
    },
    "mega_cap_ai_cloud_pe": {
        "mode": "pe",
        "multiples": {"bear": 20.0, "base": 28.0, "bull": 36.0},
        "basisType": VALUATION_BASIS_INDEPENDENT,
    },
    "mature_quality_software_pe": {
        "mode": "pe",
        "multiples": {"bear": 18.0, "base": 23.0, "bull": 28.0},
        "basisType": VALUATION_BASIS_INDEPENDENT,
    },
    "consumer_platform_pe": {
        "mode": "pe",
        "multiples": {"bear": 20.0, "base": 25.0, "bull": 30.0},
        "basisType": VALUATION_BASIS_INDEPENDENT,
    },
    "mega_cap_ad_platform_pe": {
        "mode": "pe",
        "multiples": {"bear": 18.0, "base": 23.0, "bull": 28.0},
        "basisType": VALUATION_BASIS_INDEPENDENT,
    },
    "premium_semiconductor_ip_pe": {
        "mode": "pe",
        "multiples": {"bear": 45.0, "base": 65.0, "bull": 85.0},
        "basisType": VALUATION_BASIS_INDEPENDENT,
    },
    "high_growth_profitable_pe": {
        "mode": "pe",
        "multiples": {"bear": 35.0, "base": 50.0, "bull": 70.0},
        "basisType": VALUATION_BASIS_INDEPENDENT,
    },
    "financial_hybrid_pe": {
        "mode": "pe",
        "multiples": {"bear": 12.0, "base": 18.0, "bull": 24.0},
        "basisType": VALUATION_BASIS_INDEPENDENT,
    },
    "payment_fintech_pe": {
        "mode": "pe",
        "multiples": {"bear": 18.0, "base": 25.0, "bull": 32.0},
        "basisType": VALUATION_BASIS_INDEPENDENT,
    },
    "premium_fintech_platform_pe": {
        "mode": "pe",
        "multiples": {"bear": 25.0, "base": 35.0, "bull": 45.0},
        "basisType": VALUATION_BASIS_INDEPENDENT,
    },
    "cyclical_midcycle_pe": {
        "mode": "pe",
        "multiples": {"bear": 6.0, "base": 8.0, "bull": 10.0},
        "basisType": VALUATION_BASIS_INDEPENDENT,
    },
    "networking_platform_pe": {
        "mode": "pe",
        "multiples": {"bear": 25.0, "base": 35.0, "bull": 45.0},
        "basisType": VALUATION_BASIS_INDEPENDENT,
    },
    "optical_component_cycle_pe": {
        "mode": "pe",
        "multiples": {"bear": 18.0, "base": 25.0, "bull": 32.0},
        "basisType": VALUATION_BASIS_INDEPENDENT,
    },
    "ai_power_profit_pe": {
        "mode": "pe",
        "multiples": {"bear": 16.0, "base": 22.0, "bull": 28.0},
        "basisType": VALUATION_BASIS_INDEPENDENT,
    },
    "ai_power_growth_pe": {
        "mode": "pe",
        "multiples": {"bear": 25.0, "base": 35.0, "bull": 45.0},
        "basisType": VALUATION_BASIS_INDEPENDENT,
    },
    "defensive_consumer_pe": {
        "mode": "pe",
        "multiples": {"bear": 18.0, "base": 22.0, "bull": 26.0},
        "basisType": VALUATION_BASIS_INDEPENDENT,
    },
    "defensive_healthcare_pe": {
        "mode": "pe",
        "multiples": {"bear": 14.0, "base": 17.0, "bull": 20.0},
        "basisType": VALUATION_BASIS_INDEPENDENT,
    },
    "defensive_restaurant_pe": {
        "mode": "pe",
        "multiples": {"bear": 20.0, "base": 24.0, "bull": 28.0},
        "basisType": VALUATION_BASIS_INDEPENDENT,
    },
    "regulated_utility_pe": {
        "mode": "pe",
        "multiples": {"bear": 18.0, "base": 22.0, "bull": 26.0},
        "basisType": VALUATION_BASIS_INDEPENDENT,
    },
    "scaled_growth_ev_sales": {
        "mode": "ev_sales",
        "multiples": {"bear": 3.0, "base": 5.0, "bull": 8.0},
        "basisType": VALUATION_BASIS_INDEPENDENT,
    },
    "storage_integrator_ev_sales": {
        "mode": "ev_sales",
        "multiples": {"bear": 0.5, "base": 0.8, "bull": 1.2},
        "basisType": VALUATION_BASIS_INDEPENDENT,
    },
    "energy_storage_scaled_ev_sales": {
        "mode": "ev_sales",
        "multiples": {"bear": 0.8, "base": 1.2, "bull": 1.8},
        "basisType": VALUATION_BASIS_INDEPENDENT,
    },
    "ai_data_center_power_storage_ev_sales": {
        "mode": "ev_sales",
        "multiples": {"bear": 1.2, "base": 2.0, "bull": 3.0},
        "basisType": VALUATION_BASIS_INDEPENDENT,
    },
    "ai_chip_hardware_ev_sales": {
        "mode": "ev_sales",
        "multiples": {"bear": 10.0, "base": 15.0, "bull": 25.0},
        "basisType": VALUATION_BASIS_INDEPENDENT,
    },
    "ai_infra_growth_ev_sales": {
        "mode": "ev_sales",
        "multiples": {"bear": 25.0, "base": 40.0, "bull": 60.0},
        "basisType": VALUATION_BASIS_INDEPENDENT,
    },
    "post_ipo_scarcity_premium_ev_sales": {
        "mode": "ev_sales",
        "multiples": {"bear": 60.0, "base": 85.0, "bull": 120.0},
        "basisType": VALUATION_BASIS_INDEPENDENT,
    },
    "current_implied_base_revenue_sensitivity": {
        "mode": "ev_sales",
        "factorBasis": "current_implied_ev_sales",
        "factors": {"bear": 0.7, "base": 1.0, "bull": 1.4},
        "basisType": VALUATION_BASIS_MARKET_IMPLIED,
    },
    "space_defense_scaled_ev_sales": {
        "mode": "ev_sales",
        "multiples": {"bear": 5.0, "base": 8.0, "bull": 12.0},
        "basisType": VALUATION_BASIS_INDEPENDENT,
    },
    "early_project_ramp_ev_sales": {
        "mode": "ev_sales",
        "multiples": {"bear": 15.0, "base": 25.0, "bull": 35.0},
        "basisType": VALUATION_BASIS_INDEPENDENT,
    },
    "ai_power_scaled_ev_sales": {
        "mode": "ev_sales",
        "multiples": {"bear": 12.0, "base": 20.0, "bull": 30.0},
        "basisType": VALUATION_BASIS_INDEPENDENT,
    },
    "development_market_implied_ev_sales": {
        "mode": "development_ev_sales",
        "factorBasis": "current_implied_ev_sales",
        "factors": {"bear": 0.425, "base": 0.85, "bull": 1.50},
        "basisType": VALUATION_BASIS_MARKET_IMPLIED,
    },
}
VALID_MULTIPLE_FRAMEWORKS = set(MULTIPLE_FRAMEWORKS) | {CUSTOM_FRAMEWORK}
STAGE_MULTIPLE_SENSITIVITY_FRAMEWORKS = {
    "stage_2_rapid_growth_pe": {
        "stageLabel": "Stage 2 - Rapid Growth",
        "multiples": {"bear": 29.2, "base": 36.5, "bull": 45.8},
    },
    "stage_3_maturity_pe": {
        "stageLabel": "Stage 3 - Maturity",
        "multiples": {"bear": 20.3, "base": 24.0, "bull": 28.9},
    },
    "stage_5_recovery_turnaround_pe": {
        "stageLabel": "Stage 5 - Recovery / Turnaround",
        "multiples": {"bear": 27.3, "base": 34.5, "bull": 42.7},
    },
}
MIN_HISTORY_TRADING_DAYS = 60
TIMESTAMPED_SNAPSHOT_RE = re.compile(r".+-\d{4}-\d{2}-\d{2}-\d{4}\.json$")


@dataclass
class Scenario:
    label: str
    eps: float | None
    revenue: float | None
    multiple: float | None


def fail(message: str) -> None:
    print(json.dumps({"error": message}, ensure_ascii=False, indent=2), file=sys.stderr)
    raise SystemExit(1)


def load_payload() -> dict[str, Any]:
    if len(sys.argv) > 2:
        fail("Usage: calculate_snapshot.py [snapshot.json]")

    try:
        if len(sys.argv) == 2:
            return json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
        return json.load(sys.stdin)
    except FileNotFoundError as exc:
        fail(f"Snapshot file not found: {exc.filename}")
    except json.JSONDecodeError as exc:
        fail(f"Invalid JSON: {exc}")


def snapshot_path_warning() -> str | None:
    if len(sys.argv) != 2:
        return None

    path = Path(sys.argv[1])
    if "valuation-snapshots" not in path.parts:
        return None
    if TIMESTAMPED_SNAPSHOT_RE.fullmatch(path.name):
        return None
    return (
        "Snapshot path is under valuation-snapshots but filename lacks an intraday timestamp; "
        "for current analyses, build a fresh snapshot from live/datestamped sources and save as "
        "symbol-YYYY-MM-DD-HHMM.json. Treat untimestamped files as historical references only."
    )


def require_number(container: dict[str, Any], key: str) -> float:
    value = container.get(key)
    if not isinstance(value, (int, float)):
        fail(f"Missing numeric field: {key}")
    return float(value)


def optional_number(container: dict[str, Any], key: str) -> float | None:
    value = container.get(key)
    if value is None:
        return None
    if not isinstance(value, (int, float)):
        fail(f"Field must be numeric when provided: {key}")
    return float(value)


def optional_string(container: dict[str, Any], key: str) -> str | None:
    value = container.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        fail(f"Field must be a non-empty string when provided: {key}")
    return value.strip()


def optional_bool(container: dict[str, Any], key: str) -> bool | None:
    value = container.get(key)
    if value is None:
        return None
    if not isinstance(value, bool):
        fail(f"Field must be boolean when provided: {key}")
    return value


def optional_number_list(container: dict[str, Any], key: str) -> list[float] | None:
    value = container.get(key)
    if value is None:
        return None
    if not isinstance(value, list):
        fail(f"Field must be an array of numbers when provided: {key}")

    numbers: list[float] = []
    for index, item in enumerate(value):
        if not isinstance(item, (int, float)):
            fail(f"Field {key}[{index}] must be numeric")
        numbers.append(float(item))
    return numbers


def optional_string_list(container: dict[str, Any], key: str) -> list[str]:
    value = container.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        fail(f"Field must be an array of strings when provided: {key}")

    strings: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            fail(f"Field {key}[{index}] must be a non-empty string")
        strings.append(item.strip())
    return strings


def require_string(container: dict[str, Any], key: str) -> str:
    value = container.get(key)
    if not isinstance(value, str) or not value.strip():
        fail(f"Missing string field: {key}")
    return value.strip()


def parse_scenario(name: str, payload: dict[str, Any]) -> Scenario:
    if not isinstance(payload, dict):
        fail(f"Scenario {name} must be an object")

    label = payload.get("label")
    if not isinstance(label, str) or not label.strip():
        label = name

    return Scenario(
        label=label.strip(),
        eps=optional_number(payload, "eps"),
        revenue=optional_number(payload, "revenue"),
        multiple=optional_number(payload, "multiple"),
    )


def apply_multiple_framework(
    scenarios: dict[str, Scenario],
    framework_name: str | None,
    current_price: float,
    net_debt: float | None,
    diluted_shares_outstanding: float | None,
) -> tuple[dict[str, Scenario], list[str]]:
    warnings: list[str] = []
    if framework_name is None or framework_name == CUSTOM_FRAMEWORK:
        if framework_name == CUSTOM_FRAMEWORK:
            warnings.append("multipleFramework=custom_framework: multiples are not standardized and may not reproduce across agents")
        return scenarios, warnings

    framework = MULTIPLE_FRAMEWORKS.get(framework_name)
    if framework is None:
        valid = ", ".join(sorted(VALID_MULTIPLE_FRAMEWORKS))
        fail(f"multipleFramework must be one of: {valid}")

    if framework.get("factorBasis") == "current_implied_ev_sales":
        base_revenue = scenarios["base"].revenue
        if base_revenue is None or base_revenue <= 0:
            fail(f"multipleFramework={framework_name} requires a positive base revenue")
        if net_debt is None or diluted_shares_outstanding is None or diluted_shares_outstanding <= 0:
            fail(f"multipleFramework={framework_name} requires netDebt and dilutedSharesOutstanding")
        current_enterprise_value = current_price * diluted_shares_outstanding + net_debt
        if current_enterprise_value <= 0:
            fail(f"multipleFramework={framework_name} requires positive current enterprise value")
        implied_multiple = current_enterprise_value / base_revenue
        framework_multiples = {
            key: implied_multiple * factor
            for key, factor in framework["factors"].items()
        }
    else:
        framework_multiples = framework["multiples"]

    patched: dict[str, Scenario] = {}
    for key, scenario in scenarios.items():
        multiple = scenario.multiple
        expected_multiple = framework_multiples[key]
        if multiple is not None and abs(multiple - expected_multiple) > 1e-9:
            warnings.append(
                f"Scenario {key} multiple {multiple:g} overrides framework {framework_name} value {expected_multiple:g}"
            )
        patched[key] = Scenario(
            label=scenario.label,
            eps=scenario.eps,
            revenue=scenario.revenue,
            multiple=multiple if multiple is not None else expected_multiple,
        )

    return patched, warnings


def build_ltm_implied_scenarios(input: dict[str, float]) -> dict[str, Scenario]:
    ltm_revenue = input["ltm_revenue"]
    current_price = input["current_price"]
    net_debt = input["net_debt"]
    diluted_shares_outstanding = input["diluted_shares_outstanding"]
    current_equity_value = current_price * diluted_shares_outstanding
    current_enterprise_value = current_equity_value + net_debt

    if ltm_revenue <= 0:
        fail("ltmRevenue must be positive when using developmentScenarioPolicy")
    if current_enterprise_value <= 0:
        fail("Current enterprise value must be positive when using developmentScenarioPolicy")

    implied_multiple = current_enterprise_value / ltm_revenue
    return {
        key: Scenario(
            label=f"LTM revenue / {key} implied-multiple sensitivity",
            eps=None,
            revenue=ltm_revenue,
            multiple=implied_multiple * factor,
        )
        for key, factor in DEVELOPMENT_LTM_MULTIPLE_FACTORS.items()
    }


def validate_multiple_framework(
    framework_name: str | None,
    requested_mode: str,
    valuation_basis_type: str,
) -> list[str]:
    warnings: list[str] = []
    if framework_name is None:
        return warnings
    if framework_name not in VALID_MULTIPLE_FRAMEWORKS:
        valid = ", ".join(sorted(VALID_MULTIPLE_FRAMEWORKS))
        fail(f"multipleFramework must be one of: {valid}")
    if framework_name == CUSTOM_FRAMEWORK:
        return warnings

    framework = MULTIPLE_FRAMEWORKS[framework_name]
    framework_mode = framework["mode"]
    if requested_mode != "auto" and requested_mode != framework_mode:
        warnings.append(
            f"multipleFramework={framework_name} is for {framework_mode}, but valuationMode={requested_mode} was requested"
        )

    framework_basis_type = framework["basisType"]
    if valuation_basis_type != framework_basis_type:
        warnings.append(
            f"valuationBasisType={valuation_basis_type} differs from framework {framework_name} basis {framework_basis_type}"
        )

    return warnings


def percent_delta(target: float, current: float) -> float:
    return ((target / current) - 1.0) * 100.0


def classify_valuation(current: float, bear: float, base: float, bull: float) -> str:
    if current < bear:
        return "deep_value"
    if abs(percent_delta(base, current)) <= NEAR_BASE_BAND_PCT:
        return "near_base"
    if current < base:
        return "buy"
    if current <= bull:
        return "hold"
    return "expensive"


def classify_analyst_overlay(current: float, average: float | None) -> str | None:
    if average is None:
        return None
    upside = percent_delta(average, current)
    if upside >= 10.0:
        return "bullish"
    if upside <= -10.0:
        return "cautious"
    return "mixed"


def classify_ratio_band(value: float | None, bullish_max: float, neutral_max: float) -> str | None:
    if value is None:
        return None
    if value < bullish_max:
        return "bullish"
    if value <= neutral_max:
        return "neutral"
    return "bearish"


def options_overlay(volume_ratio: float | None, oi_ratio: float | None) -> str | None:
    volume_view = classify_ratio_band(volume_ratio, bullish_max=0.7, neutral_max=1.2)
    oi_view = classify_ratio_band(oi_ratio, bullish_max=0.85, neutral_max=1.15)

    if volume_view is None and oi_view is None:
        return None

    views = [view for view in [volume_view, oi_view] if view is not None]
    if all(view == "bullish" for view in views):
        return "bullish"
    if all(view == "bearish" for view in views):
        return "cautious"
    if "bearish" in views and "bullish" in views:
        return "mixed"
    if "neutral" in views:
        return "mixed"
    return views[0]


def normalize_risk_flag(flag: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", flag.strip().lower()).strip("_")


def append_quality_warning(
    warnings: list[dict[str, str]],
    existing_codes: set[str],
    code: str,
    severity: str,
    message: str,
) -> None:
    if code in existing_codes:
        return
    existing_codes.add(code)
    warnings.append({"code": code, "severity": severity, "message": message})


def analyst_target_divergence_pct(base_fair_value: float, average_target: float | None) -> float | None:
    if average_target is None or average_target <= 0:
        return None
    return abs(percent_delta(base_fair_value, average_target))


def build_quality_warnings(
    *,
    selected_mode: str,
    valuation_basis_type: str,
    multiple_framework: str | None,
    fair_base: float,
    average_target: float | None,
    historical_sanity_check: dict[str, Any] | None,
    risk_flags: list[str],
    reverse_valuation: dict[str, Any] | None,
) -> list[dict[str, str]]:
    quality_warnings: list[dict[str, str]] = []
    codes: set[str] = set()

    if valuation_basis_type == VALUATION_BASIS_MARKET_IMPLIED:
        append_quality_warning(
            quality_warnings,
            codes,
            "basis_not_independent",
            "high",
            "Valuation basis is market-implied; use as sensitivity only, not an independent fair-value signal.",
        )
    elif valuation_basis_type == VALUATION_BASIS_HYBRID:
        append_quality_warning(
            quality_warnings,
            codes,
            "hybrid_basis",
            "medium",
            "Valuation basis mixes independent and market-implied assumptions; split them before assigning a rating.",
        )

    if selected_mode == "development_ev_sales":
        append_quality_warning(
            quality_warnings,
            codes,
            "development_ev_sales_high_sensitivity",
            "medium",
            "Development EV/Sales depends on future revenue timing, dilution, and project execution; treat the band as assumption-sensitive.",
        )
    elif selected_mode == "ev_sales":
        append_quality_warning(
            quality_warnings,
            codes,
            "ev_sales_margin_bridge_required",
            "medium",
            "EV/Sales needs a margin and free-cash-flow bridge before it can be treated like a mature earnings valuation.",
        )

    if multiple_framework == CUSTOM_FRAMEWORK:
        append_quality_warning(
            quality_warnings,
            codes,
            "custom_framework_reproducibility",
            "medium",
            "multipleFramework=custom_framework: explain the peer/history source and expect lower reproducibility.",
        )

    divergence = analyst_target_divergence_pct(fair_base, average_target)
    if divergence is None:
        append_quality_warning(
            quality_warnings,
            codes,
            "missing_analyst_target_overlay",
            "medium",
            "Analyst target overlay is missing; external expectation sanity check is unavailable.",
        )
    elif divergence >= TARGET_DIVERGENCE_MAJOR_PCT:
        append_quality_warning(
            quality_warnings,
            codes,
            "major_analyst_target_divergence",
            "high",
            f"Base fair value differs from the analyst average target by {divergence:.1f}%, above the {TARGET_DIVERGENCE_MAJOR_PCT:.0f}% major-divergence threshold.",
        )
    elif divergence >= TARGET_DIVERGENCE_WARNING_PCT:
        append_quality_warning(
            quality_warnings,
            codes,
            "analyst_target_divergence",
            "medium",
            f"Base fair value differs from the analyst average target by {divergence:.1f}%, above the {TARGET_DIVERGENCE_WARNING_PCT:.0f}% review threshold.",
        )

    if historical_sanity_check is not None:
        history_signal = historical_sanity_check.get("signal")
        if history_signal == "model_disconnected_from_history":
            append_quality_warning(
                quality_warnings,
                codes,
                "history_disconnected",
                "high",
                "Model bear/bull range does not overlap at least one historical trading range; inspect whether inputs or history are stale.",
            )
        elif history_signal == "history_may_be_stale":
            append_quality_warning(
                quality_warnings,
                codes,
                "history_may_be_stale",
                "medium",
                "Historical trading range is marked stale; do not use it as a strong anchor.",
            )
        elif history_signal == "short_history":
            append_quality_warning(
                quality_warnings,
                codes,
                "short_history",
                "medium",
                "Trading history is short; historical sanity check has low evidentiary weight.",
            )

    if reverse_valuation is not None:
        append_quality_warning(
            quality_warnings,
            codes,
            "reverse_valuation_required",
            "medium",
            "Reverse valuation is present; explain the revenue or multiple required by current price before giving a conclusion.",
        )

    for raw_flag in risk_flags:
        flag = normalize_risk_flag(raw_flag)
        if not flag:
            continue
        if flag in HIGH_SEVERITY_RISK_FLAGS:
            severity = "high"
        elif flag in MEDIUM_SEVERITY_RISK_FLAGS:
            severity = "medium"
        else:
            severity = "medium"
        append_quality_warning(
            quality_warnings,
            codes,
            f"risk_flag_{flag}",
            severity,
            f"Snapshot risk flag: {flag}.",
        )

    return quality_warnings


def infer_model_confidence(
    *,
    selected_mode: str,
    valuation_basis_type: str,
    multiple_framework: str | None,
    quality_warnings: list[dict[str, str]],
) -> tuple[str, int]:
    score = 100

    if selected_mode == "development_ev_sales":
        score -= 15
    elif selected_mode == "ev_sales":
        score -= 8

    if valuation_basis_type == VALUATION_BASIS_MARKET_IMPLIED:
        score -= 35
    elif valuation_basis_type == VALUATION_BASIS_HYBRID:
        score -= 20

    if multiple_framework == CUSTOM_FRAMEWORK:
        score -= 10

    for warning in quality_warnings:
        severity = warning["severity"]
        if severity == "high":
            score -= 25
        elif severity == "medium":
            score -= 10
        else:
            score -= 3

    score = max(0, min(100, score))
    if score >= 75:
        return MODEL_CONFIDENCE_HIGH, score
    if score >= 50:
        return MODEL_CONFIDENCE_MEDIUM, score
    return MODEL_CONFIDENCE_LOW, score


def infer_model_use(valuation_basis_type: str, model_confidence: str) -> str:
    if valuation_basis_type == VALUATION_BASIS_MARKET_IMPLIED:
        return MODEL_USE_MARKET_SENSITIVITY_ONLY
    if valuation_basis_type == VALUATION_BASIS_HYBRID:
        return MODEL_USE_MIXED_BASIS_REQUIRES_SPLIT
    if model_confidence == MODEL_CONFIDENCE_HIGH:
        return MODEL_USE_INDEPENDENT_SIGNAL
    if model_confidence == MODEL_CONFIDENCE_MEDIUM:
        return MODEL_USE_DIRECTIONAL_WITH_WARNINGS
    return MODEL_USE_DIAGNOSTIC_ONLY


def guardrail_valuation_signal(valuation_signal: str, valuation_basis_type: str, model_confidence: str) -> str:
    if valuation_basis_type == VALUATION_BASIS_MARKET_IMPLIED:
        return "not_independent"
    if valuation_basis_type == VALUATION_BASIS_HYBRID:
        return "mixed_basis_requires_split"
    if model_confidence == MODEL_CONFIDENCE_LOW:
        return MODEL_USE_LOW_CONFIDENCE_NO_STANDALONE_SIGNAL
    return valuation_signal


def looks_market_implied(text: str | None) -> bool:
    if text is None:
        return False
    normalized = text.lower()
    implied_markers = [
        "current implied",
        "market implied",
        "implied forward",
        "implied future",
        "implied ev/sales",
        "implied multiple",
        "当前隐含",
        "市场隐含",
        "隐含倍数",
    ]
    return any(marker in normalized for marker in implied_markers)


def infer_valuation_basis_type(
    explicit_basis_type: str | None,
    valuation_basis: str | None,
    development_scenario_policy: str | None,
) -> tuple[str, list[str]]:
    warnings: list[str] = []

    if explicit_basis_type is not None:
        if explicit_basis_type not in VALID_VALUATION_BASIS_TYPES:
            valid = ", ".join(sorted(VALID_VALUATION_BASIS_TYPES))
            fail(f"valuationBasisType must be one of: {valid}")
        if explicit_basis_type == VALUATION_BASIS_INDEPENDENT and looks_market_implied(valuation_basis):
            warnings.append(
                "valuationBasisType=independent_fair_value conflicts with valuationBasis text that looks market-implied"
            )
        return explicit_basis_type, warnings

    if development_scenario_policy == DEVELOPMENT_POLICY_LTM_IMPLIED:
        return VALUATION_BASIS_MARKET_IMPLIED, warnings

    if looks_market_implied(valuation_basis):
        return VALUATION_BASIS_MARKET_IMPLIED, warnings

    return VALUATION_BASIS_INDEPENDENT, warnings


def round2(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value, 2)


def classify_point_vs_range(value: float, low: float, high: float) -> str:
    if value < low:
        return "below_range"
    if value > high:
        return "above_range"
    return "inside_range"


def classify_model_history_overlap(bear: float, bull: float, low: float, high: float) -> str:
    if bull < low or bear > high:
        return "no_overlap"
    if bear <= low and bull >= high:
        return "covers_full_range"
    return "partial_overlap"


def parse_historical_price_ranges(payload: Any) -> dict[str, dict[str, Any]]:
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        fail("historicalPriceRanges must be an object when provided")

    ranges: dict[str, dict[str, Any]] = {}
    for horizon, range_payload in payload.items():
        if not isinstance(horizon, str) or not horizon.strip():
            fail("historicalPriceRanges keys must be non-empty strings")
        if not isinstance(range_payload, dict):
            fail(f"historicalPriceRanges.{horizon} must be an object")

        low = require_number(range_payload, "low")
        high = require_number(range_payload, "high")
        if low <= 0:
            fail(f"historicalPriceRanges.{horizon}.low must be positive")
        if high < low:
            fail(f"historicalPriceRanges.{horizon}.high must be >= low")

        median = optional_number(range_payload, "median")
        if median is not None and not (low <= median <= high):
            fail(f"historicalPriceRanges.{horizon}.median must be between low and high")

        trading_days = optional_number(range_payload, "tradingDays")
        history_may_be_stale = optional_bool(range_payload, "historyMayBeStale") or False

        ranges[horizon.strip()] = {
            "low": low,
            "median": median,
            "high": high,
            "startDate": optional_string(range_payload, "startDate"),
            "endDate": optional_string(range_payload, "endDate"),
            "tradingDays": trading_days,
            "historyMayBeStale": history_may_be_stale,
            "notes": optional_string(range_payload, "notes"),
        }

    return ranges


def build_historical_sanity_check(
    historical_ranges: dict[str, dict[str, Any]],
    fair_bear: float,
    fair_base: float,
    fair_bull: float,
) -> dict[str, Any] | None:
    if not historical_ranges:
        return None

    range_results: dict[str, Any] = {}
    any_no_overlap = False
    any_stale = False
    any_short_history = False

    for horizon, price_range in historical_ranges.items():
        low = price_range["low"]
        high = price_range["high"]
        median = price_range["median"]
        history_may_be_stale = price_range["historyMayBeStale"]
        trading_days = price_range["tradingDays"]

        warnings: list[str] = []
        if history_may_be_stale:
            any_stale = True
            warnings.append("History marked stale; use only as context, not an anchor")
        if trading_days is not None and trading_days < MIN_HISTORY_TRADING_DAYS:
            any_short_history = True
            warnings.append("Short trading history; sanity check is noisy")

        overlap = classify_model_history_overlap(fair_bear, fair_bull, low, high)
        if overlap == "no_overlap":
            any_no_overlap = True
            warnings.append("Model range does not overlap this historical trading range")

        range_results[horizon] = {
            "startDate": price_range["startDate"],
            "endDate": price_range["endDate"],
            "tradingDays": round2(trading_days),
            "historyMayBeStale": history_may_be_stale,
            "low": round2(low),
            "median": round2(median),
            "high": round2(high),
            "modelRangeOverlap": overlap,
            "bearPosition": classify_point_vs_range(fair_bear, low, high),
            "basePosition": classify_point_vs_range(fair_base, low, high),
            "bullPosition": classify_point_vs_range(fair_bull, low, high),
            "bearVsHistoricalLowPct": round2(percent_delta(fair_bear, low)),
            "baseVsHistoricalMedianPct": round2(percent_delta(fair_base, median)) if median is not None else None,
            "bullVsHistoricalHighPct": round2(percent_delta(fair_bull, high)),
            "notes": price_range["notes"],
            "warnings": warnings,
        }

    if any_stale:
        signal = "history_may_be_stale"
    elif any_no_overlap:
        signal = "model_disconnected_from_history"
    elif any_short_history:
        signal = "short_history"
    else:
        signal = "passes_basic_sanity"

    return {
        "signal": signal,
        "note": "Historical ranges are a sanity check only; they do not change fair values or valuationBand",
        "ranges": range_results,
    }


def has_pe_inputs(scenarios: dict[str, Scenario]) -> bool:
    return all(scenario.eps is not None and scenario.multiple is not None for scenario in scenarios.values())


def has_ev_sales_inputs(
    scenarios: dict[str, Scenario],
    net_debt: float | None,
    diluted_shares_outstanding: float | None,
) -> bool:
    return (
        net_debt is not None
        and diluted_shares_outstanding is not None
        and diluted_shares_outstanding > 0
        and all(scenario.revenue is not None and scenario.multiple is not None for scenario in scenarios.values())
    )


def pe_is_stable_enough(scenarios: dict[str, Scenario]) -> bool:
    bear = scenarios["bear"]
    base = scenarios["base"]
    bull = scenarios["bull"]
    assert bear.eps is not None and base.eps is not None and bull.eps is not None
    return (
        bear.eps > 0
        and base.eps > 0
        and bull.eps > 0
        and bear.eps >= AUTO_PE_MIN_BEAR_EPS
        and base.eps >= AUTO_PE_MIN_BASE_EPS
    )


def choose_mode(
    requested_mode: str,
    scenarios: dict[str, Scenario],
    net_debt: float | None,
    diluted_shares_outstanding: float | None,
    development_stage: bool,
) -> tuple[str, list[str]]:
    warnings: list[str] = []
    pe_available = has_pe_inputs(scenarios)
    ev_sales_available = has_ev_sales_inputs(scenarios, net_debt, diluted_shares_outstanding)

    if requested_mode == "pe":
        if not pe_available:
            fail("valuationMode=pe requires eps and multiple in all scenarios")
        if not pe_is_stable_enough(scenarios):
            warnings.append("valuationMode=pe was forced even though forward EPS looks weak for a clean PE anchor")
        return "pe", warnings

    if requested_mode == "ev_sales":
        if not ev_sales_available:
            fail("valuationMode=ev_sales requires revenue, multiple, netDebt, and dilutedSharesOutstanding")
        return "ev_sales", warnings

    if requested_mode == "development_ev_sales":
        if not ev_sales_available:
            fail("valuationMode=development_ev_sales requires revenue, multiple, netDebt, and dilutedSharesOutstanding")
        warnings.append("Using development_ev_sales: fair values rely on future revenue and highly assumption-sensitive multiples")
        return "development_ev_sales", warnings

    if ev_sales_available:
        base_multiple = scenarios["base"].multiple
        if development_stage or (base_multiple is not None and base_multiple >= SPECULATIVE_EV_SALES_MULTIPLE):
            if development_stage:
                warnings.append("Auto-selected development_ev_sales because developmentStage=true takes precedence over PE inputs")
            elif pe_available:
                warnings.append("Auto-switched to development_ev_sales because EV/Sales multiple is speculative")
            else:
                warnings.append("Auto-selected development_ev_sales because PE inputs were incomplete and EV/Sales multiple is speculative")
            return "development_ev_sales", warnings

    if pe_available and pe_is_stable_enough(scenarios):
        return "pe", warnings

    if ev_sales_available:
        if pe_available:
            warnings.append("Auto-switched to ev_sales because forward EPS looks too weak or unstable for a PE model")
        else:
            warnings.append("Used ev_sales because PE inputs were incomplete")
        return "ev_sales", warnings

    if pe_available:
        fail("Auto mode rejected PE because forward EPS looks too weak, and EV/Sales inputs are missing")

    fail("Insufficient inputs for auto mode: provide PE inputs or EV/Sales inputs")


def build_pe_scenarios(scenarios: dict[str, Scenario], current_price: float) -> tuple[dict[str, Any], list[float]]:
    result: dict[str, Any] = {}
    fair_values: list[float] = []

    for key in ["bear", "base", "bull"]:
        scenario = scenarios[key]
        assert scenario.eps is not None and scenario.multiple is not None
        fair_value = scenario.eps * scenario.multiple
        fair_values.append(fair_value)
        result[key] = {
            "label": scenario.label,
            "eps": round2(scenario.eps),
            "multiple": round2(scenario.multiple),
            "fairValue": round2(fair_value),
            "vsCurrentPct": round2(percent_delta(fair_value, current_price)),
        }

    return result, fair_values


def build_stage_multiple_sensitivity(
    payload: Any,
    scenarios: dict[str, Scenario],
    current_price: float,
) -> dict[str, Any] | None:
    if payload is None:
        return None
    if not isinstance(payload, dict):
        fail("stageMultipleSensitivity must be an object when provided")

    framework_name = optional_string(payload, "framework") or optional_string(payload, "stageFramework")
    if framework_name is None:
        valid = ", ".join(sorted(STAGE_MULTIPLE_SENSITIVITY_FRAMEWORKS))
        fail(f"stageMultipleSensitivity requires framework. Valid values: {valid}")
    framework = STAGE_MULTIPLE_SENSITIVITY_FRAMEWORKS.get(framework_name)
    if framework is None:
        valid = ", ".join(sorted(STAGE_MULTIPLE_SENSITIVITY_FRAMEWORKS))
        fail(f"stageMultipleSensitivity.framework must be one of: {valid}")

    eps_payload = payload.get("eps")
    if eps_payload is not None and not isinstance(eps_payload, dict):
        fail("stageMultipleSensitivity.eps must be an object when provided")
    labels_payload = payload.get("labels")
    if labels_payload is not None and not isinstance(labels_payload, dict):
        fail("stageMultipleSensitivity.labels must be an object when provided")

    warnings: list[str] = []
    provided_eps_keys = []
    if isinstance(eps_payload, dict):
        provided_eps_keys = [key for key in ["bear", "base", "bull"] if key in eps_payload]
        if provided_eps_keys and len(provided_eps_keys) != 3:
            warnings.append("Partial EPS override; missing sensitivity EPS values fall back to main scenarios")

    result: dict[str, Any] = {}
    fair_values: list[float] = []
    for key in ["bear", "base", "bull"]:
        scenario = scenarios[key]
        eps = optional_number(eps_payload, key) if isinstance(eps_payload, dict) else None
        eps_source = "stageMultipleSensitivity.eps" if eps is not None else "main_scenarios"
        if eps is None:
            eps = scenario.eps
        if eps is None:
            fail(f"stageMultipleSensitivity requires EPS for {key}; provide stageMultipleSensitivity.eps.{key}")
        if eps <= 0:
            fail(f"stageMultipleSensitivity.eps.{key} must be positive")

        label = None
        if isinstance(labels_payload, dict) and key in labels_payload:
            label_value = labels_payload[key]
            if not isinstance(label_value, str) or not label_value.strip():
                fail(f"stageMultipleSensitivity.labels.{key} must be a non-empty string")
            label = label_value.strip()
        if label is None:
            label = scenario.label if eps_source == "main_scenarios" else key

        multiple = framework["multiples"][key]
        fair_value = eps * multiple
        fair_values.append(fair_value)
        result[key] = {
            "label": label,
            "eps": round2(eps),
            "epsSource": eps_source,
            "multiple": round2(multiple),
            "fairValue": round2(fair_value),
            "vsCurrentPct": round2(percent_delta(fair_value, current_price)),
        }

    ordered = fair_values[0] <= fair_values[1] <= fair_values[2]
    if not ordered:
        warnings.append("Sensitivity fair values are not ordered bear <= base <= bull; no band classification was assigned")
    if provided_eps_keys:
        warnings.append("Stage multiple sensitivity uses separate EPS inputs; disclose source date and fiscal-year differences")

    return {
        "framework": framework_name,
        "stageLabel": framework["stageLabel"],
        "basisType": "market_sentiment_sensitivity",
        "source": optional_string(payload, "source"),
        "epsSource": optional_string(payload, "epsSource"),
        "note": "Stage multiple sensitivity explains what the price could look like if the market applies generic stage PE multiples; it does not change valuationBand",
        "scenarios": result,
        "sensitivityBand": classify_valuation(current_price, fair_values[0], fair_values[1], fair_values[2]) if ordered else None,
        "warnings": warnings,
    }


def framework_multiple_map(
    framework_name: str,
    scenarios: dict[str, Scenario],
    current_price: float,
    net_debt: float | None,
    diluted_shares_outstanding: float | None,
) -> dict[str, float]:
    framework = MULTIPLE_FRAMEWORKS.get(framework_name)
    if framework is None:
        valid = ", ".join(sorted(VALID_MULTIPLE_FRAMEWORKS))
        fail(f"framework must be one of: {valid}")

    if framework.get("factorBasis") == "current_implied_ev_sales":
        base_revenue = scenarios["base"].revenue
        if base_revenue is None or base_revenue <= 0:
            fail(f"framework={framework_name} requires a positive base revenue")
        if net_debt is None or diluted_shares_outstanding is None or diluted_shares_outstanding <= 0:
            fail(f"framework={framework_name} requires netDebt and dilutedSharesOutstanding")
        current_enterprise_value = current_price * diluted_shares_outstanding + net_debt
        if current_enterprise_value <= 0:
            fail(f"framework={framework_name} requires positive current enterprise value")
        implied_multiple = current_enterprise_value / base_revenue
        return {
            key: implied_multiple * factor
            for key, factor in framework["factors"].items()
        }

    return framework["multiples"]


def parse_multiple_map(payload: Any, field_name: str) -> dict[str, float]:
    if not isinstance(payload, dict):
        fail(f"{field_name} must be an object with bear/base/bull numbers")

    multiples: dict[str, float] = {}
    for key in ["bear", "base", "bull"]:
        value = payload.get(key)
        if not isinstance(value, (int, float)):
            fail(f"{field_name}.{key} must be numeric")
        if value <= 0:
            fail(f"{field_name}.{key} must be positive")
        multiples[key] = float(value)
    return multiples


def build_valuation_families(
    payload: Any,
    scenarios: dict[str, Scenario],
    selected_mode: str,
    current_price: float,
    net_debt: float | None,
    diluted_shares_outstanding: float | None,
) -> list[dict[str, Any]] | None:
    if payload is None:
        return None
    if not isinstance(payload, list):
        fail("valuationFamilies must be an array when provided")

    families: list[dict[str, Any]] = []
    for index, family_payload in enumerate(payload):
        if not isinstance(family_payload, dict):
            fail(f"valuationFamilies[{index}] must be an object")

        classification = optional_string(family_payload, "classification") or optional_string(family_payload, "name")
        framework_name = optional_string(family_payload, "framework")
        if framework_name is None and classification in MULTIPLE_FRAMEWORKS:
            framework_name = classification
        if classification is None:
            classification = framework_name
        if classification is None:
            fail(f"valuationFamilies[{index}] requires classification/name or framework")

        framework = MULTIPLE_FRAMEWORKS.get(framework_name) if framework_name is not None else None
        requested_family_mode = optional_string(family_payload, "mode")
        if requested_family_mode is not None and requested_family_mode not in {"pe", "ev_sales", "development_ev_sales"}:
            fail(f"valuationFamilies[{index}].mode must be pe, ev_sales, or development_ev_sales")
        family_mode = requested_family_mode or (framework["mode"] if framework is not None else selected_mode)
        if family_mode == "development_ev_sales":
            calculation_mode = "ev_sales"
        else:
            calculation_mode = family_mode

        explicit_basis_type = optional_string(family_payload, "basisType")
        if explicit_basis_type is not None and explicit_basis_type not in VALID_FAMILY_BASIS_TYPES:
            valid = ", ".join(sorted(VALID_FAMILY_BASIS_TYPES))
            fail(f"valuationFamilies[{index}].basisType must be one of: {valid}")
        family_basis_type = explicit_basis_type or (framework["basisType"] if framework is not None else VALUATION_BASIS_INDEPENDENT)

        if family_payload.get("multiples") is not None:
            multiples = parse_multiple_map(family_payload.get("multiples"), f"valuationFamilies[{index}].multiples")
        elif framework_name is not None:
            multiples = framework_multiple_map(
                framework_name=framework_name,
                scenarios=scenarios,
                current_price=current_price,
                net_debt=net_debt,
                diluted_shares_outstanding=diluted_shares_outstanding,
            )
        else:
            fail(f"valuationFamilies[{index}] requires either framework or multiples")

        family_scenarios = {
            key: Scenario(
                label=scenarios[key].label,
                eps=scenarios[key].eps,
                revenue=scenarios[key].revenue,
                multiple=multiples[key],
            )
            for key in ["bear", "base", "bull"]
        }

        if calculation_mode == "pe":
            if not all(family_scenarios[key].eps is not None for key in ["bear", "base", "bull"]):
                fail(f"valuationFamilies[{index}] mode=pe requires EPS in main scenarios")
            scenario_results, fair_values = build_pe_scenarios(family_scenarios, current_price)
        else:
            if net_debt is None or diluted_shares_outstanding is None or diluted_shares_outstanding <= 0:
                fail(f"valuationFamilies[{index}] mode={family_mode} requires netDebt and dilutedSharesOutstanding")
            if not all(family_scenarios[key].revenue is not None for key in ["bear", "base", "bull"]):
                fail(f"valuationFamilies[{index}] mode={family_mode} requires revenue in main scenarios")
            scenario_results, fair_values = build_ev_sales_scenarios(
                scenarios=family_scenarios,
                current_price=current_price,
                net_debt=net_debt,
                diluted_shares_outstanding=diluted_shares_outstanding,
            )

        warnings: list[str] = []
        ordered = fair_values[0] <= fair_values[1] <= fair_values[2]
        if not ordered:
            warnings.append("Family fair values are not ordered bear <= base <= bull; no band classification was assigned")

        families.append(
            {
                "classification": classification,
                "framework": framework_name,
                "role": optional_string(family_payload, "role"),
                "mode": family_mode,
                "basisType": family_basis_type,
                "applicability": optional_string(family_payload, "applicability") or optional_string(family_payload, "logic"),
                "explanation": optional_string(family_payload, "explanation"),
                "multiples": {key: round2(multiples[key]) for key in ["bear", "base", "bull"]},
                "scenarios": scenario_results,
                "priceBand": classify_valuation(current_price, fair_values[0], fair_values[1], fair_values[2]) if ordered else None,
                "note": "Valuation-family table compares business-classification outcomes; it does not override the main valuationSignal unless explicitly chosen as the main model",
                "warnings": warnings,
            }
        )

    return families


def build_ev_sales_scenarios(
    scenarios: dict[str, Scenario],
    current_price: float,
    net_debt: float,
    diluted_shares_outstanding: float,
) -> tuple[dict[str, Any], list[float]]:
    result: dict[str, Any] = {}
    fair_values: list[float] = []

    for key in ["bear", "base", "bull"]:
        scenario = scenarios[key]
        assert scenario.revenue is not None and scenario.multiple is not None
        fair_enterprise_value = scenario.revenue * scenario.multiple
        fair_equity_value = fair_enterprise_value - net_debt
        fair_price = fair_equity_value / diluted_shares_outstanding
        fair_values.append(fair_price)
        result[key] = {
            "label": scenario.label,
            "revenue": round2(scenario.revenue),
            "multiple": round2(scenario.multiple),
            "fairEnterpriseValue": round2(fair_enterprise_value),
            "fairEquityValue": round2(fair_equity_value),
            "fairValue": round2(fair_price),
            "vsCurrentPct": round2(percent_delta(fair_price, current_price)),
        }

    return result, fair_values


def build_reverse_valuation(
    payload: Any,
    current_price: float,
    net_debt: float | None,
    diluted_shares_outstanding: float | None,
    base_revenue: float | None,
) -> dict[str, Any] | None:
    if payload is None:
        return None
    if not isinstance(payload, dict):
        fail("reverseValuation must be an object when provided")
    if net_debt is None or diluted_shares_outstanding is None or diluted_shares_outstanding <= 0:
        fail("reverseValuation requires netDebt and dilutedSharesOutstanding")

    revenue_anchor = optional_number(payload, "revenueAnchor")
    if revenue_anchor is None:
        revenue_anchor = base_revenue
    if revenue_anchor is None or revenue_anchor <= 0:
        fail("reverseValuation requires a positive revenueAnchor or base scenario revenue")

    target_prices = optional_number_list(payload, "targetPrices") or []
    multiples = optional_number_list(payload, "multiples") or []
    revenue_cases = optional_number_list(payload, "revenueCases") or []

    if not target_prices and not multiples and not revenue_cases:
        fail("reverseValuation requires at least one of targetPrices, multiples, or revenueCases")
    if (target_prices or revenue_cases) and not multiples:
        fail("reverseValuation targetPrices or revenueCases require multiples")

    current_enterprise_value = current_price * diluted_shares_outstanding + net_debt
    if current_enterprise_value <= 0:
        fail("reverseValuation requires positive current enterprise value")

    target_results: dict[str, Any] = {}
    for target_price in target_prices:
        if target_price <= 0:
            fail("reverseValuation.targetPrices values must be positive")
        target_enterprise_value = target_price * diluted_shares_outstanding + net_debt
        if target_enterprise_value <= 0:
            fail("reverseValuation target price implies non-positive enterprise value")
        target_results[str(round2(target_price))] = {
            "targetEnterpriseValue": round2(target_enterprise_value),
            "impliedEvSalesAtRevenueAnchor": round2(target_enterprise_value / revenue_anchor),
            "requiredRevenueByMultiple": {
                f"{multiple:g}x": round2(target_enterprise_value / multiple)
                for multiple in multiples
                if multiple > 0
            },
        }

    current_price_required_revenue = {
        f"{multiple:g}x": round2(current_enterprise_value / multiple)
        for multiple in multiples
        if multiple > 0
    }

    price_grid: dict[str, Any] = {}
    for revenue in revenue_cases:
        if revenue <= 0:
            fail("reverseValuation.revenueCases values must be positive")
        price_grid[str(round2(revenue))] = {
            f"{multiple:g}x": round2((revenue * multiple - net_debt) / diluted_shares_outstanding)
            for multiple in multiples
            if multiple > 0
        }

    return {
        "revenueAnchor": round2(revenue_anchor),
        "currentEnterpriseValue": round2(current_enterprise_value),
        "currentImpliedEvSales": round2(current_enterprise_value / revenue_anchor),
        "currentPriceRequiredRevenueByMultiple": current_price_required_revenue or None,
        "targetPrices": target_results or None,
        "priceByRevenueAndMultiple": price_grid or None,
        "note": "Reverse valuation explains what revenue or EV/Sales the price implies; it is not a fair-value signal by itself",
    }


def main() -> None:
    path_warning = snapshot_path_warning()
    payload = load_payload()

    symbol = require_string(payload, "symbol")
    as_of = require_string(payload, "asOf")
    current_price = require_number(payload, "currentPrice")
    if current_price <= 0:
        fail("currentPrice must be positive")

    requested_mode = payload.get("valuationMode", "auto")
    if not isinstance(requested_mode, str) or requested_mode not in VALID_MODES:
        fail("valuationMode must be one of: auto, pe, ev_sales, development_ev_sales")

    development_stage = optional_bool(payload, "developmentStage") or False
    forecast_fiscal_year = optional_string(payload, "forecastFiscalYear")
    valuation_basis = optional_string(payload, "valuationBasis")
    multiple_framework = optional_string(payload, "multipleFramework")
    explicit_valuation_basis_type = optional_string(payload, "valuationBasisType")
    development_scenario_policy = optional_string(payload, "developmentScenarioPolicy")
    if development_scenario_policy is not None and development_scenario_policy != DEVELOPMENT_POLICY_LTM_IMPLIED:
        fail(f"developmentScenarioPolicy must be {DEVELOPMENT_POLICY_LTM_IMPLIED}")

    valuation_basis_type, basis_warnings = infer_valuation_basis_type(
        explicit_basis_type=explicit_valuation_basis_type,
        valuation_basis=valuation_basis,
        development_scenario_policy=development_scenario_policy,
    )
    if (
        explicit_valuation_basis_type is None
        and multiple_framework is not None
        and multiple_framework in MULTIPLE_FRAMEWORKS
    ):
        framework_basis_type = MULTIPLE_FRAMEWORKS[multiple_framework]["basisType"]
        if (
            valuation_basis_type == VALUATION_BASIS_MARKET_IMPLIED
            and framework_basis_type == VALUATION_BASIS_INDEPENDENT
        ):
            basis_warnings.append(
                "valuationBasis text looks market-implied; framework basisType was not used to override it as independent_fair_value"
            )
        else:
            valuation_basis_type = framework_basis_type
    framework_warnings = validate_multiple_framework(
        framework_name=multiple_framework,
        requested_mode=requested_mode,
        valuation_basis_type=valuation_basis_type,
    )

    net_debt = optional_number(payload, "netDebt")
    diluted_shares_outstanding = optional_number(payload, "dilutedSharesOutstanding")
    ltm_revenue = optional_number(payload, "ltmRevenue")
    historical_ranges = parse_historical_price_ranges(payload.get("historicalPriceRanges"))
    risk_flags = optional_string_list(payload, "riskFlags")

    scenarios_payload = payload.get("scenarios")
    apply_framework_warnings: list[str] = []
    if development_scenario_policy == DEVELOPMENT_POLICY_LTM_IMPLIED:
        if ltm_revenue is None or net_debt is None or diluted_shares_outstanding is None:
            fail("developmentScenarioPolicy requires ltmRevenue, netDebt, and dilutedSharesOutstanding")
        if multiple_framework is not None and multiple_framework != "development_market_implied_ev_sales":
            framework_warnings.append("multipleFramework is ignored when developmentScenarioPolicy builds scenarios")
        scenarios = build_ltm_implied_scenarios(
            {
                "ltm_revenue": ltm_revenue,
                "current_price": current_price,
                "net_debt": net_debt,
                "diluted_shares_outstanding": diluted_shares_outstanding,
            }
        )
    else:
        if not isinstance(scenarios_payload, dict):
            fail("Missing object field: scenarios")
        scenarios = {
            "bear": parse_scenario("bear", scenarios_payload.get("bear")),
            "base": parse_scenario("base", scenarios_payload.get("base")),
            "bull": parse_scenario("bull", scenarios_payload.get("bull")),
        }
        scenarios, apply_framework_warnings = apply_multiple_framework(
            scenarios=scenarios,
            framework_name=multiple_framework,
            current_price=current_price,
            net_debt=net_debt,
            diluted_shares_outstanding=diluted_shares_outstanding,
        )

    selected_mode, warnings = choose_mode(
        requested_mode=requested_mode,
        scenarios=scenarios,
        net_debt=net_debt,
        diluted_shares_outstanding=diluted_shares_outstanding,
        development_stage=development_stage,
    )
    warnings = basis_warnings + framework_warnings + apply_framework_warnings + warnings
    if path_warning:
        warnings.insert(0, path_warning)

    if valuation_basis_type == VALUATION_BASIS_MARKET_IMPLIED:
        warnings.append(
            "Market-implied sensitivity is not an independent fair-value signal; do not read the base case as proof that current price is reasonable"
        )
    elif valuation_basis_type == VALUATION_BASIS_HYBRID:
        warnings.append(
            "Hybrid valuation basis mixes independent and market-implied assumptions; separate them in the written report before assigning a valuation signal"
        )

    if selected_mode == "pe":
        scenario_results, fair_values = build_pe_scenarios(scenarios, current_price)
    else:
        assert net_debt is not None and diluted_shares_outstanding is not None
        scenario_results, fair_values = build_ev_sales_scenarios(
            scenarios=scenarios,
            current_price=current_price,
            net_debt=net_debt,
            diluted_shares_outstanding=diluted_shares_outstanding,
        )

    fair_bear, fair_base, fair_bull = fair_values
    if not (fair_bear <= fair_base <= fair_bull):
        fail("Fair values must be ordered bear <= base <= bull")
    price_band = classify_valuation(current_price, fair_bear, fair_base, fair_bull)

    if valuation_basis_type == VALUATION_BASIS_INDEPENDENT:
        valuation_band = price_band
        sensitivity_band = None
        valuation_signal = price_band
    elif valuation_basis_type == VALUATION_BASIS_MARKET_IMPLIED:
        valuation_band = None
        sensitivity_band = price_band
        valuation_signal = "not_independent"
    else:
        valuation_band = None
        sensitivity_band = price_band
        valuation_signal = "mixed_basis"

    historical_sanity_check = build_historical_sanity_check(
        historical_ranges=historical_ranges,
        fair_bear=fair_bear,
        fair_base=fair_base,
        fair_bull=fair_bull,
    )
    base_revenue = scenarios["base"].revenue
    reverse_valuation = build_reverse_valuation(
        payload=payload.get("reverseValuation"),
        current_price=current_price,
        net_debt=net_debt,
        diluted_shares_outstanding=diluted_shares_outstanding,
        base_revenue=base_revenue,
    )
    stage_multiple_sensitivity = build_stage_multiple_sensitivity(
        payload=payload.get("stageMultipleSensitivity"),
        scenarios=scenarios,
        current_price=current_price,
    )
    if stage_multiple_sensitivity is not None:
        warnings.extend(
            f"stageMultipleSensitivity: {warning}"
            for warning in stage_multiple_sensitivity["warnings"]
        )
    valuation_families = build_valuation_families(
        payload=payload.get("valuationFamilies"),
        scenarios=scenarios,
        selected_mode=selected_mode,
        current_price=current_price,
        net_debt=net_debt,
        diluted_shares_outstanding=diluted_shares_outstanding,
    )

    analyst_targets = payload.get("analystTargets") if isinstance(payload.get("analystTargets"), dict) else {}
    average_target = require_number(analyst_targets, "average") if "average" in analyst_targets else None
    high_target = require_number(analyst_targets, "high") if "high" in analyst_targets else None
    low_target = require_number(analyst_targets, "low") if "low" in analyst_targets else None

    options = payload.get("options") if isinstance(payload.get("options"), dict) else {}
    put_call_volume = require_number(options, "putCallVolumeRatio") if "putCallVolumeRatio" in options else None
    put_call_oi = require_number(options, "putCallOiRatio") if "putCallOiRatio" in options else None

    quality_warnings = build_quality_warnings(
        selected_mode=selected_mode,
        valuation_basis_type=valuation_basis_type,
        multiple_framework=multiple_framework,
        fair_base=fair_base,
        average_target=average_target,
        historical_sanity_check=historical_sanity_check,
        risk_flags=risk_flags,
        reverse_valuation=reverse_valuation,
    )
    model_confidence, model_confidence_score = infer_model_confidence(
        selected_mode=selected_mode,
        valuation_basis_type=valuation_basis_type,
        multiple_framework=multiple_framework,
        quality_warnings=quality_warnings,
    )
    model_use = infer_model_use(
        valuation_basis_type=valuation_basis_type,
        model_confidence=model_confidence,
    )
    guardrailed_signal = guardrail_valuation_signal(
        valuation_signal=valuation_signal,
        valuation_basis_type=valuation_basis_type,
        model_confidence=model_confidence,
    )
    if guardrailed_signal == MODEL_USE_LOW_CONFIDENCE_NO_STANDALONE_SIGNAL:
        warnings.append(
            "Model confidence is low; do not use valuationSignal as a standalone buy/hold/expensive conclusion"
        )

    result = {
        "symbol": symbol,
        "asOf": as_of,
        "currentPrice": round2(current_price),
        "requestedValuationMode": requested_mode,
        "selectedValuationMode": selected_mode,
        "valuationBasis": valuation_basis,
        "valuationBasisType": valuation_basis_type,
        "multipleFramework": multiple_framework,
        "forecastFiscalYear": forecast_fiscal_year,
        "developmentScenarioPolicy": development_scenario_policy,
        "developmentStage": development_stage or selected_mode == "development_ev_sales",
        "riskFlags": risk_flags,
        "warnings": warnings,
        "qualityWarnings": quality_warnings,
        "modelConfidence": model_confidence,
        "modelConfidenceScore": model_confidence_score,
        "modelUse": model_use,
        "netDebt": round2(net_debt),
        "dilutedSharesOutstanding": round2(diluted_shares_outstanding),
        "scenarios": scenario_results,
        "priceBand": price_band,
        "valuationBand": valuation_band,
        "sensitivityBand": sensitivity_band,
        "valuationSignal": valuation_signal,
        "guardrailedValuationSignal": guardrailed_signal,
        "reverseValuation": reverse_valuation,
        "stageMultipleSensitivity": stage_multiple_sensitivity,
        "valuationFamilies": valuation_families,
        "historicalSanityCheck": historical_sanity_check,
        "analystTargets": {
            "average": round2(average_target),
            "high": round2(high_target),
            "low": round2(low_target),
            "averageVsCurrentPct": round2(percent_delta(average_target, current_price)) if average_target is not None else None,
            "overlay": classify_analyst_overlay(current_price, average_target),
        },
        "options": {
            "putCallVolumeRatio": round2(put_call_volume),
            "putCallOiRatio": round2(put_call_oi),
            "overlay": options_overlay(put_call_volume, put_call_oi),
        },
    }

    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
