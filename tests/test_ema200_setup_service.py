from datetime import datetime, timedelta

import pandas as pd

from src.services.ema200_setup_service import analyze_ema200_setup


def _ema200_fixture(*, wide_resistance: bool = True) -> pd.DataFrame:
    rows = []
    for i in range(260):
        open_ = 100.8
        high = 101.2
        low = 100.8
        close = 100.0

        if i == 246:
            open_, high, low, close = 100.2, 100.3, 99.8, 100.0
        elif i == 247:
            open_, high, low, close = 100.5, 101.0, 100.4, 100.6
        elif i == 248:
            open_, high, low, close = 100.6, 101.0, 100.5, 100.7
        elif i == 249:
            open_, high, low, close = 100.6, 101.0, 100.4, 100.7
        elif i == 250:
            open_, high, low, close = 100.7, 101.0, 100.05, 100.6
        elif i == 251:
            open_, high, low, close = 100.6, 101.1, 100.45, 100.8
        elif i == 252:
            open_, high, low, close = 100.8, 101.2, 100.5, 100.9
        elif i == 253:
            open_, high, low, close = 100.9, 101.3, 100.55, 101.0
        elif i == 254:
            open_, high, low, close = 100.9, 101.1, 100.45, 100.8
        elif i == 255:
            open_, high, low, close = 100.8, 101.2, 100.35, 100.9
        elif i == 256:
            open_, high, low, close = 100.9, 101.4, 100.6, 101.1
        elif i == 257:
            open_, high, low, close = 101.0, 101.5, 100.7, 101.2
        elif i == 258:
            open_, high, low, close = 101.1, 103.0 if wide_resistance else 102.0, 100.9, 101.5
        elif i == 259:
            open_, high, low, close = 101.1, 102.0, 100.9, 101.5

        rows.append(
            {
                "date": f"2026-01-{i + 1:03d}",
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": 1000,
            }
        )
    return pd.DataFrame(rows)


def test_ema5_200_setup_returns_candidate_after_reclaim():
    result = analyze_ema200_setup(_ema200_fixture(), setup_id="ema5_200_setup")

    assert result["status"] == "ok"
    assert result["grade"] == 2
    assert result["stage1"]["satisfied"] is True
    assert result["stage2"]["satisfied"] is False
    assert result["candidate"] is not None
    assert result["risk_plan"]["one_r_target"] is None


def test_ema_200_highlow_returns_executable_when_structure_and_rr_are_clear():
    result = analyze_ema200_setup(_ema200_fixture(wide_resistance=True), setup_id="ema_200_highlow")

    assert result["status"] == "ok"
    assert result["grade"] == 3
    assert result["stage1"]["satisfied"] is True
    assert result["stage2"]["satisfied"] is True
    assert result["structure"]["has_structure"] is True
    assert result["structure"]["structure_type"] == "higher_low"
    assert result["risk_reward"]["stop_reference"] is not None
    assert result["risk_reward"]["one_r_target"] is not None
    assert result["risk_reward"]["has_min_rr"] is True


def test_ema_200_highlow_stays_candidate_when_rr_is_too_small():
    result = analyze_ema200_setup(_ema200_fixture(wide_resistance=False), setup_id="ema_200_highlow")

    assert result["status"] == "ok"
    assert result["grade"] == 2
    assert result["stage1"]["satisfied"] is True
    assert result["stage2"]["satisfied"] is False
    assert result["risk_reward"]["has_min_rr"] is False
    assert any("1.00R" in item for item in result["missing"])


def test_ema200_setup_requires_enough_history():
    short_df = _ema200_fixture().tail(100)

    result = analyze_ema200_setup(short_df, setup_id="ema_200_highlow")

    assert result["status"] == "insufficient_data"
    assert result["grade"] == 0
    assert result["verdict"] == "信息不足"


def _spy_orb_fixture() -> pd.DataFrame:
    rows = []
    start = datetime(2026, 1, 5, 9, 30)
    for i in range(220):
        session_day = i // 78
        session_bar = i % 78
        close = 99.75 if i % 2 == 0 else 100.25
        rows.append(
            {
                "date": (start + timedelta(days=session_day, minutes=session_bar * 5)).isoformat(sep=" "),
                "open": close,
                "high": close + 0.1,
                "low": close - 0.1,
                "close": close,
                "volume": 1000,
            }
        )

    current_day = datetime(2026, 1, 12, 9, 30)
    for bar in range(12):
        close = 99.85 if bar % 2 == 0 else 100.15
        rows.append(
            {
                "date": (current_day + timedelta(minutes=bar * 5)).isoformat(sep=" "),
                "open": close,
                "high": 100.2,
                "low": 99.8,
                "close": close,
                "volume": 1000,
            }
        )

    rows.append(
        {
            "date": (current_day + timedelta(minutes=12 * 5)).isoformat(sep=" "),
            "open": 100.05,
            "high": 100.35,
            "low": 100.0,
            "close": 100.3,
            "volume": 1300,
        }
    )
    return pd.DataFrame(rows)


def test_spy_orb_ema200_returns_long_signal_after_orb_breakout():
    result = analyze_ema200_setup(_spy_orb_fixture(), setup_id="spy_orb_ema200", timeframe="5m")

    assert result["status"] == "ok"
    assert result["grade"] == 3
    assert result["verdict"] == "顺势多头信号"
    assert result["latest_signal"] == "long"
    assert result["orb"]["break_up_seen"] is True
    assert result["filters"]["can_trade"] is True
    assert result["risk_plan"]["long_stop_reference"] is not None
