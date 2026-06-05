# -*- coding: utf-8 -*-
"""Unit tests for USLiquidityService (Yahoo Finance liquidity indicators)."""

import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.services.us_liquidity_service import (
    USLiquidityService,
    _classify_signal,
    _INDICATORS,
)


def _mock_ticker(closes):
    """Build a fake yfinance Ticker whose history() returns DataFrame with given closes."""
    df = pd.DataFrame({"Close": closes})
    ticker = MagicMock()
    ticker.history.return_value = df
    return ticker


# ---------------------------------------------------------------------------
# _classify_signal
# ---------------------------------------------------------------------------

class TestClassifySignal(unittest.TestCase):
    """阈值分级（单测，不依赖网络）。"""

    def test_vix_levels(self):
        self.assertEqual(_classify_signal("vix", "level", 15.0, 0)[0], "🟢")
        self.assertEqual(_classify_signal("vix", "level", 25.0, 0)[0], "🟡")
        self.assertEqual(_classify_signal("vix", "level", 35.0, 0)[0], "🔴")

    def test_move_levels(self):
        self.assertEqual(_classify_signal("move", "level", 80.0, 0)[0], "🟢")
        self.assertEqual(_classify_signal("move", "level", 120.0, 0)[0], "🟡")
        self.assertEqual(_classify_signal("move", "level", 160.0, 0)[0], "🔴")

    def test_tnx_rate_bp_loose(self):
        # 5d 变化 -15bp 且利率在警戒区(≥4.2%)以下 → 利率下行 → 🟢
        sig, text = _classify_signal("tnx", "rate_bp", 4.0, -0.15)
        self.assertEqual(sig, "🟢")
        self.assertIn("宽松", text)

    def test_tnx_warning_zone_overrides_direction(self):
        # 利率进入 4.2-4.5% 警戒区时，等级风险优先于方向性下行 → 🟡
        sig, text = _classify_signal("tnx", "rate_bp", 4.2, -0.15)
        self.assertEqual(sig, "🟡")
        self.assertIn("警戒区", text)

    def test_tnx_rate_bp_tighten(self):
        # 5d 变化 +12bp → 收紧 → 🔴
        sig, _ = _classify_signal("tnx", "rate_bp", 4.5, 0.12)
        self.assertEqual(sig, "🔴")

    def test_tnx_rate_bp_neutral(self):
        sig, _ = _classify_signal("tnx", "rate_bp", 4.4, 0.05)  # +5bp
        self.assertEqual(sig, "🟡")

    def test_dxy_pct(self):
        # -1.5% → 美元走弱 → 🟢
        sig, text = _classify_signal("dxy", "pct", 102.0, -1.5)
        self.assertEqual(sig, "🟢")
        self.assertIn("美元走弱", text)
        # +1.5% → 美元走强 → 🔴
        sig, _ = _classify_signal("dxy", "pct", 104.0, 1.5)
        self.assertEqual(sig, "🔴")
        # 0.3% → 横盘 🟡
        sig, _ = _classify_signal("dxy", "pct", 103.0, 0.3)
        self.assertEqual(sig, "🟡")

    def test_hyg_pct(self):
        sig, _ = _classify_signal("hyg", "pct", 80.0, 1.5)   # +1.5%
        self.assertEqual(sig, "🟢")
        sig, _ = _classify_signal("hyg", "pct", 78.0, -1.5)  # -1.5%
        self.assertEqual(sig, "🔴")
        sig, _ = _classify_signal("hyg", "pct", 79.0, 0.4)
        self.assertEqual(sig, "🟡")


# ---------------------------------------------------------------------------
# USLiquidityService._fetch / get_liquidity_data / get_liquidity_block
# ---------------------------------------------------------------------------

class TestUSLiquidityService(unittest.TestCase):
    """端到端（mock 掉 yfinance）。"""

    def _patched_yf(self, ticker_map):
        """Helper: patch yfinance.Ticker to return ticker_map[symbol]."""
        def _factory(symbol):
            return ticker_map.get(symbol, _mock_ticker([]))
        yf_mock = MagicMock()
        yf_mock.Ticker.side_effect = _factory
        return yf_mock

    def test_all_indicators_green(self):
        """5 指标全部偏宽松 → 综合判断偏宽松。"""
        # VIX 15 (低)、MOVE 85 (低)、TNX 5d 下行 15bp 且在 4.2% 警戒区以下、DXY -1.5%、HYG +1.5%
        ticker_map = {
            "^VIX": _mock_ticker([20, 19, 18, 17, 16, 15.0, 15.0]),
            "^MOVE": _mock_ticker([95, 94, 93, 91, 89, 87, 85.0]),
            "^TNX": _mock_ticker([4.10, 4.05, 4.00, 3.96, 3.94, 3.92, 3.90]),
            "DX-Y.NYB": _mock_ticker([105, 104.5, 104, 103.5, 103.2, 103.5, 103.43]),
            "HYG": _mock_ticker([77.0, 77.2, 77.3, 77.6, 77.8, 78.1, 78.16]),
        }
        with patch.dict(sys.modules, {"yfinance": self._patched_yf(ticker_map)}):
            svc = USLiquidityService()
            data = svc.get_liquidity_data()

        self.assertIsNotNone(data)
        # VIX < 20 → 🟢
        self.assertEqual(data["vix"]["signal"], "🟢")
        # MOVE < 100 → 🟢
        self.assertEqual(data["move"]["signal"], "🟢")
        # TNX 5d 下行 15bp → 🟢
        self.assertEqual(data["tnx"]["signal"], "🟢")

        block = svc.get_liquidity_block()
        self.assertIsNotNone(block)
        self.assertIn("美股资金流动性面板", block)
        self.assertIn("VIX 波动率", block)
        # 综合判断应该偏宽松
        self.assertTrue("偏宽松" in block or "🟢" in block)

    def test_mixed_signals_render_table(self):
        """混合信号：表格应有 5 行 + 综合判断。"""
        ticker_map = {
            "^VIX": _mock_ticker([14, 14, 14, 14, 14, 14, 18]),  # 升高但 <20 → 🟢
            "^MOVE": _mock_ticker([120, 120, 120, 120, 120, 120, 120]),  # 🟡
            "^TNX": _mock_ticker([4.2, 4.2, 4.2, 4.2, 4.2, 4.2, 4.5]),  # +30bp → 🔴
            "DX-Y.NYB": _mock_ticker([102, 102, 102, 102, 102, 102, 102.5]),  # +0.5% → 🟡
            "HYG": _mock_ticker([78, 78, 78, 78, 78, 78, 78.3]),  # +0.38% → 🟡
        }
        with patch.dict(sys.modules, {"yfinance": self._patched_yf(ticker_map)}):
            svc = USLiquidityService()
            block = svc.get_liquidity_block()

        self.assertIsNotNone(block)
        # 5 个指标都应在面板里出现
        for name in ("VIX 波动率", "MOVE 债市波动率", "10Y 美债收益率", "美元指数", "HYG 高收益债"):
            self.assertIn(name, block)
        # 至少一项 🔴 利率收紧
        self.assertIn("🔴", block)

    def test_partial_failure_does_not_break(self):
        """部分指标拉取失败时仍返回（缺失项标 N/A）。"""
        # 只成功 VIX，其余抛异常
        failing = MagicMock()
        failing.history.side_effect = RuntimeError("network down")
        ticker_map = {
            "^VIX": _mock_ticker([15, 15, 15, 15, 15, 15, 15]),
            "^MOVE": failing,
            "^TNX": failing,
            "DX-Y.NYB": failing,
            "HYG": failing,
        }
        with patch.dict(sys.modules, {"yfinance": self._patched_yf(ticker_map)}):
            svc = USLiquidityService()
            data = svc.get_liquidity_data()

        self.assertIsNotNone(data)
        self.assertIsNotNone(data["vix"]["current"])
        self.assertIsNone(data["move"]["current"])
        block = svc.get_liquidity_block()
        self.assertIn("数据不可用", block)

    def test_all_failure_returns_none(self):
        """所有指标都失败 → get_liquidity_data 返回 None，get_liquidity_block 返回 None。"""
        failing = MagicMock()
        failing.history.side_effect = RuntimeError("offline")
        yf_mock = MagicMock()
        yf_mock.Ticker.return_value = failing

        with patch.dict(sys.modules, {"yfinance": yf_mock}):
            svc = USLiquidityService()
            self.assertIsNone(svc.get_liquidity_data())
            self.assertIsNone(svc.get_liquidity_block())

    def test_cache_ttl(self):
        """同一天内重复调用使用缓存，不重复发起请求。"""
        ticker_map = {
            "^VIX": _mock_ticker([16, 16, 16, 16, 16, 16, 16]),
            "^MOVE": _mock_ticker([90, 90, 90, 90, 90, 90, 90]),
            "^TNX": _mock_ticker([4.3, 4.3, 4.3, 4.3, 4.3, 4.3, 4.3]),
            "DX-Y.NYB": _mock_ticker([102, 102, 102, 102, 102, 102, 102]),
            "HYG": _mock_ticker([78, 78, 78, 78, 78, 78, 78]),
        }
        yf_mock = MagicMock()
        yf_mock.Ticker.side_effect = lambda s: ticker_map.get(s, _mock_ticker([]))

        with patch.dict(sys.modules, {"yfinance": yf_mock}):
            svc = USLiquidityService()
            _ = svc.get_liquidity_data()
            first_call_count = yf_mock.Ticker.call_count
            _ = svc.get_liquidity_data()
            second_call_count = yf_mock.Ticker.call_count

        self.assertEqual(first_call_count, second_call_count, "缓存未生效：第二次调用又拉了一次")

    def test_indicators_complete(self):
        """_INDICATORS 包含 5 个核心指标（开发回归用）。"""
        keys = {ind["key"] for ind in _INDICATORS}
        self.assertSetEqual(keys, {"vix", "move", "tnx", "dxy", "hyg"})


if __name__ == "__main__":
    unittest.main()
