# -*- coding: utf-8 -*-
"""Unit tests for ValuationPercentileService (akshare baidu + yfinance.info)."""

import os
import sys
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.services.valuation_percentile_service import (
    ValuationPercentileService,
    _classify_rating,
    _detect_market,
)


# ---------------------------------------------------------------------------
# _detect_market
# ---------------------------------------------------------------------------

class TestDetectMarket(unittest.TestCase):
    def test_a_share(self):
        self.assertEqual(_detect_market("600519"), "cn")
        self.assertEqual(_detect_market("000001"), "cn")
        self.assertEqual(_detect_market("300750"), "cn")

    def test_hk_prefix(self):
        self.assertEqual(_detect_market("HK00700"), "hk")
        self.assertEqual(_detect_market("hk00700"), "hk")

    def test_hk_suffix(self):
        self.assertEqual(_detect_market("00700.HK"), "hk")

    def test_hk_5digit(self):
        self.assertEqual(_detect_market("00700"), "hk")

    def test_us(self):
        self.assertEqual(_detect_market("AAPL"), "us")
        self.assertEqual(_detect_market("TSLA"), "us")
        self.assertEqual(_detect_market("BRK.B"), "us")

    def test_unknown(self):
        self.assertEqual(_detect_market(""), "unknown")
        self.assertEqual(_detect_market("XXXXXXXXX"), "unknown")
        self.assertEqual(_detect_market("123"), "unknown")


# ---------------------------------------------------------------------------
# _classify_rating
# ---------------------------------------------------------------------------

class TestClassifyRating(unittest.TestCase):
    def test_extreme_low(self):
        emoji, label, _ = _classify_rating(5)
        self.assertEqual(emoji, "🟢")
        self.assertEqual(label, "历史极低")
        emoji, label, _ = _classify_rating(10)
        self.assertEqual(label, "历史极低")

    def test_low(self):
        emoji, label, _ = _classify_rating(20)
        self.assertEqual(emoji, "🟢")
        self.assertEqual(label, "历史偏低")
        _, label, _ = _classify_rating(30)
        self.assertEqual(label, "历史偏低")

    def test_fair(self):
        _, label, _ = _classify_rating(30.1)
        self.assertEqual(label, "历史中位")
        _, label, _ = _classify_rating(50)
        self.assertEqual(label, "历史中位")
        _, label, _ = _classify_rating(69.9)
        self.assertEqual(label, "历史中位")

    def test_high(self):
        _, label, _ = _classify_rating(70)
        self.assertEqual(label, "历史偏高")
        _, label, _ = _classify_rating(89.9)
        self.assertEqual(label, "历史偏高")

    def test_extreme_high(self):
        emoji, label, _ = _classify_rating(90)
        self.assertEqual(emoji, "🔴")
        self.assertEqual(label, "历史极高")
        _, label, _ = _classify_rating(99)
        self.assertEqual(label, "历史极高")


# ---------------------------------------------------------------------------
# ValuationPercentileService — A-share (akshare)
# ---------------------------------------------------------------------------

def _make_baidu_df(values):
    """Build a fake DataFrame with date + value columns mimicking baidu API."""
    return pd.DataFrame({
        "date": pd.date_range(end=pd.Timestamp.now(tz="UTC").normalize(), periods=len(values)),
        "value": values,
    })


class TestAShareFlow(unittest.TestCase):
    def test_a_share_ok_returns_full_percentile(self):
        # 100 个值，当前值 50 → 应该接近 P50 = 合理
        values = list(range(1, 101))  # 1..100, current = 100 (放最后)
        df = _make_baidu_df(values)

        fake_ak = MagicMock()
        fake_ak.stock_zh_valuation_baidu.return_value = df

        with patch.dict(sys.modules, {"akshare": fake_ak}):
            svc = ValuationPercentileService()
            result = svc.get_valuation_data("600519", metric="pe", lookback_years=5)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["market"], "cn")
        self.assertEqual(result["metric"], "pe")
        self.assertEqual(result["samples"], 100)
        self.assertEqual(result["current"], 100.0)
        self.assertEqual(result["current_percentile"], 100.0)  # 100 是历史最高
        self.assertEqual(result["rating"], "历史极高")
        self.assertIn("p5", result["percentiles"])
        self.assertIn("p95", result["percentiles"])
        fake_ak.stock_zh_valuation_baidu.assert_called_once_with(
            symbol="600519", indicator="市盈率(TTM)", period="近五年"
        )

    def test_a_share_low_current_value(self):
        # 100 个值递增，把当前值放成 5（最低区间）
        values = list(range(1, 100)) + [5]
        df = _make_baidu_df(values)
        fake_ak = MagicMock()
        fake_ak.stock_zh_valuation_baidu.return_value = df
        with patch.dict(sys.modules, {"akshare": fake_ak}):
            svc = ValuationPercentileService()
            result = svc.get_valuation_data("600519", metric="pe")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["current"], 5.0)
        self.assertLessEqual(result["current_percentile"], 30)
        self.assertIn(result["rating"], ("历史极低", "历史偏低"))

    def test_a_share_insufficient_samples(self):
        # 仅 10 个值，<30 → status='partial'
        df = _make_baidu_df([10, 12, 14, 13, 11, 15, 14, 13, 12, 11])
        fake_ak = MagicMock()
        fake_ak.stock_zh_valuation_baidu.return_value = df
        with patch.dict(sys.modules, {"akshare": fake_ak}):
            svc = ValuationPercentileService()
            result = svc.get_valuation_data("600519")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["samples"], 10)
        self.assertIn("note", result)

    def test_a_share_empty_data(self):
        fake_ak = MagicMock()
        fake_ak.stock_zh_valuation_baidu.return_value = pd.DataFrame()
        with patch.dict(sys.modules, {"akshare": fake_ak}):
            svc = ValuationPercentileService()
            result = svc.get_valuation_data("999999")
        self.assertEqual(result["status"], "unavailable")

    def test_a_share_akshare_error(self):
        fake_ak = MagicMock()
        fake_ak.stock_zh_valuation_baidu.side_effect = RuntimeError("network down")
        with patch.dict(sys.modules, {"akshare": fake_ak}):
            svc = ValuationPercentileService()
            result = svc.get_valuation_data("600519")
        self.assertEqual(result["status"], "error")
        self.assertIn("network down", result["note"])

    def test_period_mapping(self):
        # lookback_years=3 → "近三年"
        df = _make_baidu_df(list(range(1, 100)))
        fake_ak = MagicMock()
        fake_ak.stock_zh_valuation_baidu.return_value = df
        with patch.dict(sys.modules, {"akshare": fake_ak}):
            svc = ValuationPercentileService()
            svc.get_valuation_data("600519", lookback_years=3)
        fake_ak.stock_zh_valuation_baidu.assert_called_with(
            symbol="600519", indicator="市盈率(TTM)", period="近三年"
        )


# ---------------------------------------------------------------------------
# ValuationPercentileService — US (yfinance)
# ---------------------------------------------------------------------------

class TestUSFlow(unittest.TestCase):
    def _patch_yf(self, info_dict):
        ticker = MagicMock()
        ticker.info = info_dict
        yf_mock = MagicMock()
        yf_mock.Ticker.return_value = ticker
        return yf_mock

    def test_us_partial_with_pe(self):
        yf_mock = self._patch_yf({"trailingPE": 36.12})
        with patch.dict(sys.modules, {"yfinance": yf_mock}):
            svc = ValuationPercentileService()
            result = svc.get_valuation_data("AAPL", metric="pe")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["market"], "us")
        self.assertEqual(result["current"], 36.12)
        self.assertIsNone(result["current_percentile"])
        self.assertIn("rating_summary", result)

    def test_us_missing_field(self):
        yf_mock = self._patch_yf({})  # info 中无 trailingPE
        with patch.dict(sys.modules, {"yfinance": yf_mock}):
            svc = ValuationPercentileService(realtime_quote_fetcher=lambda _: None)
            result = svc.get_valuation_data("XXXX", metric="pe")
        self.assertEqual(result["status"], "unavailable")

    def test_us_yfinance_error(self):
        yf_mock = MagicMock()
        yf_mock.Ticker.side_effect = RuntimeError("offline")
        with patch.dict(sys.modules, {"yfinance": yf_mock}):
            svc = ValuationPercentileService(realtime_quote_fetcher=lambda _: None)
            result = svc.get_valuation_data("AAPL", metric="pb")
        self.assertEqual(result["status"], "error")

    def test_us_yfinance_error_falls_back_to_realtime_pe(self):
        yf_mock = MagicMock()
        yf_mock.Ticker.side_effect = RuntimeError("offline")
        quote = SimpleNamespace(
            pe_ratio=22.34,
            pb_ratio=5.67,
            source=SimpleNamespace(value="longbridge"),
        )
        with patch.dict(sys.modules, {"yfinance": yf_mock}):
            svc = ValuationPercentileService(realtime_quote_fetcher=lambda _: quote)
            result = svc.get_valuation_data("AAPL", metric="pe")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["market"], "us")
        self.assertEqual(result["current"], 22.34)
        self.assertEqual(result["source"], "realtime_quote.longbridge")
        self.assertEqual(result["fallback_from"], "yfinance.Ticker.info")
        self.assertIn("upstream", result["note"])

    def test_us_yfinance_error_with_quote_but_no_ratio_is_unavailable(self):
        yf_mock = MagicMock()
        yf_mock.Ticker.side_effect = RuntimeError("offline")
        quote = SimpleNamespace(
            pe_ratio=None,
            pb_ratio=None,
            source=SimpleNamespace(value="longbridge"),
        )
        with patch.dict(sys.modules, {"yfinance": yf_mock}):
            svc = ValuationPercentileService(realtime_quote_fetcher=lambda _: quote)
            result = svc.get_valuation_data("ASTS", metric="pe")
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("missing pe_ratio", result["note"])

    def test_us_missing_yfinance_field_falls_back_to_realtime_pb(self):
        yf_mock = self._patch_yf({})
        quote = SimpleNamespace(
            pe_ratio=22.34,
            pb_ratio=5.67,
            source=SimpleNamespace(value="longbridge"),
        )
        with patch.dict(sys.modules, {"yfinance": yf_mock}):
            svc = ValuationPercentileService(realtime_quote_fetcher=lambda _: quote)
            result = svc.get_valuation_data("AAPL", metric="pb")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["current"], 5.67)
        self.assertEqual(result["source"], "realtime_quote.longbridge")

    def test_us_ps_does_not_call_realtime_fallback(self):
        yf_mock = MagicMock()
        yf_mock.Ticker.side_effect = RuntimeError("offline")
        fetcher = MagicMock()
        with patch.dict(sys.modules, {"yfinance": yf_mock}):
            svc = ValuationPercentileService(realtime_quote_fetcher=fetcher)
            result = svc.get_valuation_data("AAPL", metric="ps")
        self.assertEqual(result["status"], "error")
        self.assertIn("does not support ps", result["note"])
        fetcher.assert_not_called()


# ---------------------------------------------------------------------------
# ValuationPercentileService — HK + invalid
# ---------------------------------------------------------------------------

class TestEdgeCases(unittest.TestCase):
    def test_hk_unavailable(self):
        svc = ValuationPercentileService()
        result = svc.get_valuation_data("HK00700")
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["market"], "hk")

    def test_unknown_market(self):
        svc = ValuationPercentileService()
        result = svc.get_valuation_data("123")
        self.assertEqual(result["status"], "error")

    def test_invalid_metric(self):
        svc = ValuationPercentileService()
        result = svc.get_valuation_data("600519", metric="dividend_yield")
        self.assertEqual(result["status"], "error")
        self.assertIn("unsupported metric", result["note"])


# ---------------------------------------------------------------------------
# Cache behaviour
# ---------------------------------------------------------------------------

class TestCacheBehaviour(unittest.TestCase):
    def test_same_day_uses_cache(self):
        df = _make_baidu_df(list(range(1, 100)))
        fake_ak = MagicMock()
        fake_ak.stock_zh_valuation_baidu.return_value = df
        with patch.dict(sys.modules, {"akshare": fake_ak}):
            svc = ValuationPercentileService()
            svc.get_valuation_data("600519", metric="pe")
            svc.get_valuation_data("600519", metric="pe")  # second call should hit cache
        self.assertEqual(fake_ak.stock_zh_valuation_baidu.call_count, 1)

    def test_different_metric_separate_cache(self):
        df = _make_baidu_df(list(range(1, 100)))
        fake_ak = MagicMock()
        fake_ak.stock_zh_valuation_baidu.return_value = df
        with patch.dict(sys.modules, {"akshare": fake_ak}):
            svc = ValuationPercentileService()
            svc.get_valuation_data("600519", metric="pe")
            svc.get_valuation_data("600519", metric="pb")  # different metric, different cache key
        self.assertEqual(fake_ak.stock_zh_valuation_baidu.call_count, 2)


if __name__ == "__main__":
    unittest.main()


def _history_result(frame, years=5):
    fake_ak = MagicMock()
    fake_ak.stock_zh_valuation_baidu.return_value = frame
    with patch.dict(sys.modules, {"akshare": fake_ak}):
        return ValuationPercentileService().get_valuation_data("600519", lookback_years=years)


def test_history_is_sorted_by_observation_date_not_provider_row_order():
    frame = _make_baidu_df(list(range(1, 101)))
    result = _history_result(frame.iloc[::-1])
    assert result["current"] == 100
    assert result["current_percentile"] == 100
    assert result["as_of"] == frame["date"].iloc[-1].date().isoformat()
    assert result["history_coverage_years"] < 1
    assert result["interpretation"] == "relative_history_only_not_intrinsic_value"


def test_requested_window_and_identical_duplicates_do_not_inflate_sample_size():
    recent = _make_baidu_df(list(range(1, 101)))
    old = pd.DataFrame({"date": [pd.Timestamp.now(tz="UTC") - pd.DateOffset(years=6)], "value": [200]})
    result = _history_result(pd.concat([old, recent, recent.tail(1)], ignore_index=True))
    assert result["samples"] == 100
    assert result["current"] == 100


def test_conflicting_observations_on_same_date_do_not_produce_rank():
    recent = _make_baidu_df(list(range(1, 101)))
    duplicate = recent.tail(1).copy()
    duplicate["value"] = 1
    result = _history_result(pd.concat([recent, duplicate], ignore_index=True))
    assert result["status"] == "partial"
    assert result["current_percentile"] is None


def test_invalid_latest_valuation_never_reuses_earlier_positive_observation():
    for value in (0, -2, float("nan"), float("inf"), "invalid"):
        result = _history_result(_make_baidu_df(list(range(1, 100)) + [value]))
        assert result["status"] == "partial"
        assert result["current"] is None
        assert result["current_percentile"] is None


def test_nonpositive_and_infinite_history_are_excluded_from_denominator():
    frame = _make_baidu_df([-10, 0, float("inf")] + list(range(1, 101)))
    result = _history_result(frame)
    assert result["samples"] == 100
    assert result["excluded_nonpositive_or_invalid"] == 3
    assert result["current_percentile"] == 100


def test_missing_dates_cannot_support_current_historical_rank():
    result = _history_result(pd.DataFrame({"value": list(range(1, 101))}))
    assert result["status"] == "partial"
    assert result["current_percentile"] is None


def test_stale_history_is_not_called_current():
    frame = _make_baidu_df(list(range(1, 101)))
    frame["date"] -= pd.Timedelta(days=40)
    result = _history_result(frame)
    assert result["status"] == "partial"
    assert result["current_percentile"] is None


def test_unsupported_lookback_does_not_silently_claim_wrong_number_of_years():
    for years in (2, 6, 0, "invalid"):
        result = ValuationPercentileService().get_valuation_data("600519", lookback_years=years)
        assert result["status"] == "error"


def test_us_nonfinite_negative_or_zero_ratio_returns_missing_value():
    import json
    for value in (float("nan"), float("inf"), -1, 0, True):
        with patch.dict(sys.modules, {"yfinance": SimpleNamespace(Ticker=lambda _: SimpleNamespace(info={"trailingPE": value}))}):
            result = ValuationPercentileService().get_valuation_data("TEST")
        assert result["status"] == "unavailable"
        assert result["current"] is None
        json.dumps(result, allow_nan=False)


def test_us_missing_history_never_prompts_model_memory_as_evidence():
    with patch.dict(sys.modules, {"yfinance": SimpleNamespace(Ticker=lambda _: SimpleNamespace(info={"trailingPE": 25}))}):
        result = ValuationPercentileService().get_valuation_data("TEST")
    assert result["current_percentile"] is None
    assert result["as_of"] is None
    assert result["retrieved_at"]
    assert "禁止用模型记忆" in result["rating_summary"]
