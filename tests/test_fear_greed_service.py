# -*- coding: utf-8 -*-
"""Unit tests for FearGreedService (szdt.tech 贪恐指数)."""

import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.services.fear_greed_service import (
    FearGreedService,
    _score_label,
    to_szdt_code,
)


# ---------------------------------------------------------------------------
# _score_label
# ---------------------------------------------------------------------------

class TestScoreLabel(unittest.TestCase):
    """Boundary tests for _score_label()."""

    def test_extreme_panic(self):
        self.assertEqual(_score_label(-100), "极度恐慌")
        self.assertEqual(_score_label(-60), "极度恐慌")

    def test_panic(self):
        self.assertEqual(_score_label(-59), "恐慌")
        self.assertEqual(_score_label(-20), "恐慌")

    def test_neutral(self):
        self.assertEqual(_score_label(-19), "中性")
        self.assertEqual(_score_label(0), "中性")
        self.assertEqual(_score_label(19), "中性")

    def test_greed(self):
        self.assertEqual(_score_label(20), "贪婪")
        self.assertEqual(_score_label(59), "贪婪")

    def test_extreme_greed(self):
        self.assertEqual(_score_label(60), "极度贪婪")
        self.assertEqual(_score_label(100), "极度贪婪")


# ---------------------------------------------------------------------------
# to_szdt_code
# ---------------------------------------------------------------------------

class TestToSzdtCode(unittest.TestCase):
    """Code format conversion tests."""

    # A 股
    def test_a_share_sh(self):
        self.assertEqual(to_szdt_code("600519"), ("SH.600519", "a"))

    def test_a_share_sh_leading5(self):
        self.assertEqual(to_szdt_code("510050"), ("SH.510050", "a"))  # ETF

    def test_a_share_sz(self):
        self.assertEqual(to_szdt_code("000001"), ("SZ.000001", "a"))

    def test_a_share_sz_cyb(self):
        self.assertEqual(to_szdt_code("300750"), ("SZ.300750", "a"))

    def test_a_share_uppercase(self):
        self.assertEqual(to_szdt_code("600519"), ("SH.600519", "a"))

    # 港股
    def test_hk_share(self):
        self.assertEqual(to_szdt_code("HK00700"), ("HK.00700", "a"))

    def test_hk_share_lower(self):
        self.assertEqual(to_szdt_code("hk00700"), ("HK.00700", "a"))

    # 美股
    def test_us_share_alpha(self):
        self.assertEqual(to_szdt_code("AAPL"), ("US.AAPL", "us"))

    def test_us_share_short(self):
        self.assertEqual(to_szdt_code("U"), ("US.U", "us"))

    def test_us_share_5_chars(self):
        self.assertEqual(to_szdt_code("GOOGL"), ("US.GOOGL", "us"))

    def test_us_share_from_watchlist(self):
        for ticker in ("MSTR", "CRCL", "ASTS", "AMZN", "MARA", "CLSK",
                       "BITF", "BMNR", "CPNG", "HIMS", "RDDT", "DUOL",
                       "NBIS", "OKLO", "TEM", "SOFI"):
            with self.subTest(ticker=ticker):
                result = to_szdt_code(ticker)
                self.assertIsNotNone(result)
                self.assertEqual(result[0], f"US.{ticker}")
                self.assertEqual(result[1], "us")

    # 无法识别
    def test_unknown_returns_none(self):
        self.assertIsNone(to_szdt_code(""))
        self.assertIsNone(to_szdt_code(None))
        self.assertIsNone(to_szdt_code("12345"))       # 5 位纯数字
        self.assertIsNone(to_szdt_code("1234567"))     # 7 位纯数字


# ---------------------------------------------------------------------------
# FearGreedService — is_available
# ---------------------------------------------------------------------------

class TestFearGreedServiceAvailability(unittest.TestCase):

    def test_unavailable_without_token(self):
        svc = FearGreedService(auth_token=None)
        self.assertFalse(svc.is_available)

    def test_unavailable_with_empty_token(self):
        svc = FearGreedService(auth_token="   ")
        self.assertFalse(svc.is_available)

    def test_available_with_token(self):
        svc = FearGreedService(auth_token="test-token")
        self.assertTrue(svc.is_available)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_response(status=200, json_body=None):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = json_body or {}
    return resp


def _success_body(score=42, name="Apple Inc.", price=180.0, ts="2026-01-01 15:00"):
    return {
        "status": 1,
        "data": {"score": score, "name": name, "price": price, "time": ts},
    }


# ---------------------------------------------------------------------------
# FearGreedService — get_score
# ---------------------------------------------------------------------------

class TestFearGreedServiceGetScore(unittest.TestCase):

    def setUp(self):
        self.svc = FearGreedService(auth_token="tok")

    def test_returns_none_when_unavailable(self):
        svc = FearGreedService(auth_token=None)
        self.assertIsNone(svc.get_score("AAPL"))

    def test_returns_none_for_unknown_code(self):
        self.assertIsNone(self.svc.get_score("12345"))

    def test_score_and_label_on_success(self):
        with patch("requests.post", return_value=_make_response(json_body=_success_body(score=42))):
            result = self.svc.get_score("AAPL")
        self.assertIsNotNone(result)
        score, label = result
        self.assertAlmostEqual(score, 42.0)
        self.assertEqual(label, "贪婪")

    def test_negative_score_returns_panic_label(self):
        with patch("requests.post", return_value=_make_response(json_body=_success_body(score=-75))):
            result = self.svc.get_score("600519")
        self.assertIsNotNone(result)
        self.assertEqual(result[1], "极度恐慌")

    def test_returns_none_on_api_error_status(self):
        body = {"status": 0, "msg": "无权限"}
        with patch("requests.post", return_value=_make_response(json_body=body)):
            result = self.svc.get_score("AAPL")
        self.assertIsNone(result)

    def test_returns_none_on_http_error(self):
        with patch("requests.post", return_value=_make_response(status=401)):
            result = self.svc.get_score("AAPL")
        self.assertIsNone(result)

    def test_returns_none_on_network_exception(self):
        with patch("requests.post", side_effect=Exception("conn refused")):
            result = self.svc.get_score("AAPL")
        self.assertIsNone(result)

    def test_cache_reuse_no_second_request(self):
        with patch("requests.post", return_value=_make_response(json_body=_success_body(score=10))) as mock_post:
            self.svc.get_score("AAPL")
            self.svc.get_score("AAPL")
        self.assertEqual(mock_post.call_count, 1)

    def test_get_score_and_context_share_cache(self):
        """get_score then get_fear_greed_context must not produce a second HTTP call."""
        with patch("requests.post", return_value=_make_response(json_body=_success_body(score=25))) as mock_post:
            self.svc.get_score("AAPL")
            ctx = self.svc.get_fear_greed_context("AAPL")
        self.assertEqual(mock_post.call_count, 1)
        self.assertIsNotNone(ctx)


# ---------------------------------------------------------------------------
# FearGreedService — get_fear_greed_context
# ---------------------------------------------------------------------------

class TestFearGreedServiceGetContext(unittest.TestCase):

    def setUp(self):
        self.svc = FearGreedService(auth_token="tok")

    def test_returns_none_when_unavailable(self):
        svc = FearGreedService(auth_token=None)
        self.assertIsNone(svc.get_fear_greed_context("AAPL"))

    def test_returns_none_for_unrecognised_code(self):
        self.assertIsNone(self.svc.get_fear_greed_context("????"))

    def test_returns_text_on_success(self):
        with patch("requests.post", return_value=_make_response(json_body=_success_body(score=55))):
            ctx = self.svc.get_fear_greed_context("AAPL")
        self.assertIsInstance(ctx, str)
        self.assertIn("贪恐", ctx)
        self.assertIn("AAPL", ctx)
        self.assertIn("贪婪", ctx)

    def test_returns_none_on_api_failure(self):
        body = {"status": 0, "msg": "err"}
        with patch("requests.post", return_value=_make_response(json_body=body)):
            ctx = self.svc.get_fear_greed_context("AAPL")
        self.assertIsNone(ctx)

    def test_contains_score_and_label(self):
        with patch("requests.post", return_value=_make_response(json_body=_success_body(score=-85))):
            ctx = self.svc.get_fear_greed_context("600519")
        self.assertIn("极度恐慌", ctx)
        self.assertIn("-85", ctx)

    def test_a_share_code_in_context(self):
        with patch("requests.post", return_value=_make_response(json_body=_success_body(score=0, name="贵州茅台"))):
            ctx = self.svc.get_fear_greed_context("600519")
        self.assertIn("贵州茅台", ctx)

    def test_us_share_context(self):
        with patch("requests.post", return_value=_make_response(json_body=_success_body(score=80, name="MicroStrategy"))):
            ctx = self.svc.get_fear_greed_context("MSTR")
        self.assertIn("极度贪婪", ctx)
        self.assertIn("MSTR", ctx)


# ---------------------------------------------------------------------------
# FearGreedService — _format
# ---------------------------------------------------------------------------

class TestFearGreedFormat(unittest.TestCase):

    def test_format_with_all_fields(self):
        data = {"score": 30, "name": "TSLA", "price": 250.0, "time": "2026-01-01 10:00"}
        text = FearGreedService._format("TSLA", data)
        self.assertIn("30", text)
        self.assertIn("贪婪", text)
        self.assertIn("250.0", text)
        self.assertIn("2026-01-01 10:00", text)

    def test_format_missing_score_shows_no_data(self):
        data = {"name": "AAPL"}
        text = FearGreedService._format("AAPL", data)
        self.assertIn("暂无数据", text)

    def test_format_missing_price_no_crash(self):
        data = {"score": -50}
        text = FearGreedService._format("000001", data)
        self.assertIn("恐慌", text)

    def test_format_explanation_always_present(self):
        text = FearGreedService._format("AAPL", {"score": 0})
        self.assertIn("贪恐指数范围约 -100~100", text)


# ---------------------------------------------------------------------------
# Cache TTL expiry
# ---------------------------------------------------------------------------

class TestFearGreedCacheTTL(unittest.TestCase):

    def test_cache_expires_after_ttl(self):
        svc = FearGreedService(auth_token="tok")
        first_body = _success_body(score=10)
        second_body = _success_body(score=99)

        with patch("requests.post", return_value=_make_response(json_body=first_body)):
            r1 = svc.get_score("AAPL")

        # Manually expire cache
        expired_time = time.monotonic() - 99999
        with svc._lock:
            for k in svc._cache:
                svc._cache[k] = (expired_time, svc._cache[k][1])

        with patch("requests.post", return_value=_make_response(json_body=second_body)):
            r2 = svc.get_score("AAPL")

        self.assertAlmostEqual(r1[0], 10.0)
        self.assertAlmostEqual(r2[0], 99.0)


if __name__ == "__main__":
    unittest.main()
