# -*- coding: utf-8 -*-
"""Tests for src.market_context market detection and LLM guideline routing."""

import unittest
from unittest.mock import patch

from src.config import Config
from src.market_context import detect_market, get_market_guidelines


class TestParseDefaultMarketContext(unittest.TestCase):
    def test_valid_values(self) -> None:
        self.assertEqual(Config._parse_default_market_context(None), "us")
        self.assertEqual(Config._parse_default_market_context(""), "us")
        self.assertEqual(Config._parse_default_market_context("CN"), "cn")
        self.assertEqual(Config._parse_default_market_context("hk"), "hk")

    def test_invalid_falls_back_us(self) -> None:
        self.assertEqual(Config._parse_default_market_context("xx"), "us")


class TestDetectMarket(unittest.TestCase):
    def test_codes_unchanged(self) -> None:
        self.assertEqual(detect_market("600519"), "cn")
        self.assertEqual(detect_market("HK00700"), "hk")
        self.assertEqual(detect_market("AAPL"), "us")
        self.assertEqual(detect_market("BRK.B"), "us")

    @patch("src.config.get_config")
    def test_empty_uses_config_default(self, mock_get_config) -> None:
        mock_get_config.return_value.default_market_context = "hk"
        self.assertEqual(detect_market(""), "hk")
        self.assertEqual(detect_market(None), "hk")

    @patch("src.config.get_config")
    def test_empty_whitespace_code_uses_config(self, mock_get_config) -> None:
        mock_get_config.return_value.default_market_context = "cn"
        self.assertEqual(detect_market("   "), "cn")

    @patch("src.config.get_config")
    def test_invalid_config_attribute_falls_back_us(self, mock_get_config) -> None:
        mock_get_config.return_value.default_market_context = "xx"
        self.assertEqual(detect_market(""), "us")

    @patch("src.config.get_config", side_effect=RuntimeError("no config"))
    def test_get_config_error_falls_back_us(self, _mock) -> None:
        self.assertEqual(detect_market(""), "us")


class TestGetMarketGuidelines(unittest.TestCase):
    @patch("src.config.get_config")
    def test_empty_respects_default_us(self, mock_get_config) -> None:
        mock_get_config.return_value.default_market_context = "us"
        text = get_market_guidelines("", "zh")
        self.assertIn("美股", text)

    @patch("src.config.get_config")
    def test_empty_respects_default_cn(self, mock_get_config) -> None:
        mock_get_config.return_value.default_market_context = "cn"
        text = get_market_guidelines("", "zh")
        self.assertIn("A 股", text)

