# -*- coding: utf-8 -*-
"""Intraday K-line history loader for intraday technical analysis.

Provides minute-level (5m, 15m, 30m, etc.) and hourly-level (1H, 4H) data
loading from supported data providers (Longbridge with Futu OpenD fallback).
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)


def _map_timeframe_to_longbridge_period(timeframe: str):
    """Map timeframe string to Longbridge Period enum."""
    from longbridge.openapi import Period

    timeframe_map = {
        "1m": Period.Min_1,
        "2m": Period.Min_2,
        "3m": Period.Min_3,
        "5m": Period.Min_5,
        "15m": Period.Min_15,
        "30m": Period.Min_30,
        "1H": Period.Min_60,
        "4H": Period.Min_240,
        "1D": Period.Day,
    }

    period = timeframe_map.get(timeframe)
    if period is None:
        raise ValueError(
            f"Unsupported timeframe: {timeframe}. "
            f"Supported: {list(timeframe_map.keys())}"
        )
    return period


def _calculate_intraday_lookback(timeframe: str, bars: int) -> timedelta:
    """Calculate calendar days needed for the requested number of intraday bars.

    Assumes market hours and adds buffer for holidays/weekends.
    """
    # Rough estimates of bars per trading day
    bars_per_day = {
        "1m": 240,    # ~4 hours * 60 minutes (US market)
        "2m": 120,
        "3m": 80,
        "5m": 48,
        "15m": 16,
        "30m": 8,
        "1H": 4,
        "4H": 1,
        "1D": 1,
    }

    daily_count = bars_per_day.get(timeframe, 48)
    calendar_days = int(bars / daily_count * 1.8) + 10  # 1.8x buffer + 10 day margin
    return timedelta(days=max(calendar_days, 5))  # At least 5 days


def load_intraday_history(
    stock_code: str,
    timeframe: str = "5m",
    bars: int = 300,
    end_date: Optional[date] = None,
) -> Tuple[Optional[pd.DataFrame], str]:
    """Load provider-native intraday K-line history.

    Args:
        stock_code: Stock code (e.g., '600519', 'AAPL', 'HK00700')
        timeframe: Timeframe string ('5m', '15m', '30m', '1H', '4H', '1D')
        bars: Number of bars to fetch (default: 300)
        end_date: End date (default: today)

    Returns:
        (df, source) where df has columns [date, open, high, low, close, volume]
        and source is 'longbridge', 'futu', or 'none' on failure.
    """
    from data_provider.longbridge_fetcher import LongbridgeFetcher
    from data_provider.base import normalize_stock_code

    normalized_code = normalize_stock_code(stock_code)
    end = end_date or date.today()
    lookback = _calculate_intraday_lookback(timeframe, bars)
    start = end - lookback

    try:
        fetcher = LongbridgeFetcher()
        if fetcher.is_available_for_request("intraday_data"):
            period = _map_timeframe_to_longbridge_period(timeframe)
            df = fetcher.fetch_intraday_candlesticks(
                normalized_code,
                start,
                end,
                period,
            )
            if df is not None and not df.empty:
                if len(df) > bars:
                    df = df.tail(bars).reset_index(drop=True)
                logger.debug(
                    "load_intraday_history(%s, %s): %d bars from Longbridge",
                    stock_code,
                    timeframe,
                    len(df),
                )
                return df, "longbridge"
    except Exception as exc:
        logger.warning(
            "load_intraday_history(%s, %s): Longbridge failed: %s",
            stock_code,
            timeframe,
            exc,
        )

    from data_provider.futu_fetcher import FutuFetcher

    futu_fetcher = FutuFetcher()
    try:
        if futu_fetcher.is_available_for_request("intraday_data"):
            df = futu_fetcher.get_intraday_data(
                stock_code,
                timeframe=timeframe,
                bars=bars,
                start_date=start,
                end_date=end,
            )
            if df is not None and not df.empty:
                logger.debug(
                    "load_intraday_history(%s, %s): %d bars from Futu",
                    stock_code,
                    timeframe,
                    len(df),
                )
                return df, "futu"
    except Exception as exc:
        logger.warning(
            "load_intraday_history(%s, %s): Futu failed: %s",
            stock_code,
            timeframe,
            exc,
        )
    finally:
        futu_fetcher.close()

    logger.warning(
        "load_intraday_history(%s, %s): No provider returned data",
        stock_code,
        timeframe,
    )
    return None, "none"
