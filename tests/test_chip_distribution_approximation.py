# -*- coding: utf-8 -*-
"""Tests for approximate chip distribution on US/HK markets."""

import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd

if "litellm" not in sys.modules:
    sys.modules["litellm"] = MagicMock()
if "json_repair" not in sys.modules:
    sys.modules["json_repair"] = MagicMock()
if "tenacity" not in sys.modules:
    sys.modules["tenacity"] = MagicMock()
if "fake_useragent" not in sys.modules:
    sys.modules["fake_useragent"] = MagicMock()

from data_provider.base import DataFetchError, DataFetcherManager
from data_provider.realtime_types import RealtimeSource, UnifiedRealtimeQuote


class _StubFetcher:
    def __init__(self, name: str, priority: int):
        self.name = name
        self.priority = priority


def _make_us_daily_df() -> pd.DataFrame:
    # Keep OHLC equal to close for deterministic weighted-percentile assertions.
    return pd.DataFrame(
        [
            {"date": "2026-05-12", "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1},
            {"date": "2026-05-13", "open": 110.0, "high": 110.0, "low": 110.0, "close": 110.0, "volume": 2},
            {"date": "2026-05-14", "open": 120.0, "high": 120.0, "low": 120.0, "close": 120.0, "volume": 1},
            {"date": "2026-05-15", "open": 130.0, "high": 130.0, "low": 130.0, "close": 130.0, "volume": 2},
        ]
    )


class TestChipDistributionApproximation(unittest.TestCase):
    @patch("src.config.get_config")
    def test_us_chip_distribution_uses_volume_profile_approximation(self, mock_get_config) -> None:
        mock_get_config.return_value = SimpleNamespace(enable_chip_distribution=True)
        manager = DataFetcherManager(fetchers=[_StubFetcher("YfinanceFetcher", 4)])
        quote = UnifiedRealtimeQuote(
            code="AAPL",
            source=RealtimeSource.FALLBACK,
            price=118.0,
        )

        with patch.object(manager, "get_daily_data", return_value=(_make_us_daily_df(), "YfinanceFetcher")) as daily_mock, patch.object(
            manager,
            "get_realtime_quote",
            return_value=quote,
        ) as quote_mock:
            chip = manager.get_chip_distribution("AAPL")

        self.assertIsNotNone(chip)
        if chip is None:
            self.fail("expected approximate chip distribution")
        self.assertEqual(chip.code, "AAPL")
        self.assertEqual(chip.source, "approx_volume_profile:YfinanceFetcher")
        self.assertEqual(chip.date, "2026-05-15")
        # Weighted by volume: <=118 covers 100(1) + 110(2) over total weight 6.
        self.assertAlmostEqual(chip.profit_ratio, 0.5, places=6)
        self.assertAlmostEqual(chip.avg_cost, 116.6666667, places=6)
        self.assertAlmostEqual(chip.cost_90_low, 100.0, places=6)
        self.assertAlmostEqual(chip.cost_90_high, 130.0, places=6)
        self.assertAlmostEqual(chip.concentration_90, 30.0 / 230.0, places=6)
        self.assertAlmostEqual(chip.cost_70_low, 100.0, places=6)
        self.assertAlmostEqual(chip.cost_70_high, 130.0, places=6)
        self.assertAlmostEqual(chip.concentration_70, 30.0 / 230.0, places=6)

        daily_mock.assert_called_once_with("AAPL", days=120)
        quote_mock.assert_called_once_with("AAPL", log_final_failure=False)

    @patch("src.config.get_config")
    def test_us_chip_distribution_falls_back_to_last_close_when_no_realtime_quote(self, mock_get_config) -> None:
        mock_get_config.return_value = SimpleNamespace(enable_chip_distribution=True)
        manager = DataFetcherManager(fetchers=[_StubFetcher("YfinanceFetcher", 4)])

        with patch.object(manager, "get_daily_data", return_value=(_make_us_daily_df(), "YfinanceFetcher")), patch.object(
            manager,
            "get_realtime_quote",
            return_value=None,
        ):
            chip = manager.get_chip_distribution("AAPL")

        self.assertIsNotNone(chip)
        if chip is None:
            self.fail("expected approximate chip distribution")
        # Latest close is 130, so all weighted costs are profitable.
        self.assertAlmostEqual(chip.profit_ratio, 1.0, places=6)

    @patch("src.config.get_config")
    def test_us_chip_distribution_returns_none_when_daily_data_unavailable(self, mock_get_config) -> None:
        mock_get_config.return_value = SimpleNamespace(enable_chip_distribution=True)
        manager = DataFetcherManager(fetchers=[_StubFetcher("YfinanceFetcher", 4)])

        with patch.object(manager, "get_daily_data", side_effect=DataFetchError("boom")), patch.object(
            manager,
            "get_realtime_quote",
            return_value=None,
        ):
            chip = manager.get_chip_distribution("AAPL")

        self.assertIsNone(chip)


if __name__ == "__main__":
    unittest.main()
