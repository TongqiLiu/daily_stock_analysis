# -*- coding: utf-8 -*-
"""Tests for market strategy blueprints."""

import unittest

from src.core.market_strategy import get_market_strategy_blueprint
from src.market_analyzer import MarketAnalyzer, MarketOverview


class TestMarketStrategyBlueprint(unittest.TestCase):
    """Validate CN/US strategy blueprint basics."""

    def test_cn_blueprint_contains_action_framework(self):
        blueprint = get_market_strategy_blueprint("cn")
        block = blueprint.to_prompt_block()

        self.assertIn("A股市场三段式复盘策略", block)
        self.assertIn("Action Framework", block)
        self.assertIn("进攻", block)

    def test_us_blueprint_contains_regime_strategy(self):
        blueprint = get_market_strategy_blueprint("us")
        block = blueprint.to_prompt_block()

        self.assertIn("美股市场复盘策略框架", block)
        self.assertIn("风险偏好上升", block)
        self.assertIn("宏观与资金流向", block)


class TestMarketAnalyzerStrategyPrompt(unittest.TestCase):
    """Validate strategy section is injected into prompt/report."""

    def test_cn_prompt_contains_strategy_plan_section(self):
        analyzer = MarketAnalyzer(region="cn")
        prompt = analyzer._build_review_prompt(MarketOverview(date="2026-02-24"), [])

        self.assertIn("策略计划", prompt)
        self.assertIn("A股市场三段式复盘策略", prompt)

    def test_us_prompt_contains_strategy_plan_section(self):
        analyzer = MarketAnalyzer(region="us")
        prompt = analyzer._build_review_prompt(MarketOverview(date="2026-02-24"), [])

        self.assertIn("策略计划", prompt)
        self.assertIn("美股市场复盘策略框架", prompt)
        self.assertIn("全文必须使用简体中文", prompt)


if __name__ == "__main__":
    unittest.main()
