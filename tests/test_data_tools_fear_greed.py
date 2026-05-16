# -*- coding: utf-8 -*-
"""
Contract tests for get_fear_greed_index tool output semantics.
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.agent.tools.data_tools import _handle_get_fear_greed_index


class _DummySvcNotConfigured:
    is_available = False


class _DummySvcUnavailableQuota:
    is_available = True

    def get_fear_greed_context(self, _stock_code: str):
        return None

    def get_score(self, _stock_code: str):
        return None

    def get_last_error(self, _stock_code: str):
        return "查询股票个数额度已用完，需手动删减已查询股票"


class _DummySvcUnavailableUnknown:
    is_available = True

    def get_fear_greed_context(self, _stock_code: str):
        return None

    def get_score(self, _stock_code: str):
        return None

    def get_last_error(self, _stock_code: str):
        return None


class _DummySvcOk:
    is_available = True

    def get_fear_greed_context(self, _stock_code: str):
        return "ctx"

    def get_score(self, _stock_code: str):
        return 72.0, "极度贪婪"

    def get_last_error(self, _stock_code: str):
        return None


class TestGetFearGreedIndexContract(unittest.TestCase):

    def test_not_configured(self) -> None:
        with patch(
            "src.agent.tools.data_tools._get_fear_greed_service",
            return_value=_DummySvcNotConfigured(),
        ):
            result = _handle_get_fear_greed_index("TSLA")

        self.assertEqual(result["stock_code"], "TSLA")
        self.assertEqual(result["status"], "not_configured")
        self.assertIn("SZDT_AUTH_TOKEN", result["note"])

    def test_unavailable_with_quota_reason(self) -> None:
        with patch(
            "src.agent.tools.data_tools._get_fear_greed_service",
            return_value=_DummySvcUnavailableQuota(),
        ):
            result = _handle_get_fear_greed_index("NOW")

        self.assertEqual(result["stock_code"], "NOW")
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason_code"], "binding_quota_exhausted")
        self.assertIn("查询股票个数额度已用完", result["reason_detail"])
        self.assertEqual(result["proxy_score"], 0.0)
        self.assertEqual(result["proxy_label"], "中性(代理)")

    def test_unavailable_unknown_reason(self) -> None:
        with patch(
            "src.agent.tools.data_tools._get_fear_greed_service",
            return_value=_DummySvcUnavailableUnknown(),
        ):
            result = _handle_get_fear_greed_index("NOW")

        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason_code"], "unknown")
        self.assertEqual(result["proxy_score"], 0.0)

    def test_ok_response(self) -> None:
        with patch(
            "src.agent.tools.data_tools._get_fear_greed_service",
            return_value=_DummySvcOk(),
        ):
            result = _handle_get_fear_greed_index("TSLA")

        self.assertEqual(result["stock_code"], "TSLA")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["score"], 72.0)
        self.assertEqual(result["label"], "极度贪婪")


if __name__ == "__main__":
    unittest.main()
