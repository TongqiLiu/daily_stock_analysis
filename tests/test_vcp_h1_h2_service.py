import pandas as pd

from src.services.vcp_h1_h2_service import analyze_vcp_h1_h2_buy


def _vcp_breakout_fixture() -> pd.DataFrame:
    rows = []
    for i in range(230):
        close = 80 + i * 0.1
        rows.append(
            {
                "date": f"2025-01-{i + 1:03d}",
                "open": close - 0.1,
                "high": close + 0.4,
                "low": close - 0.4,
                "close": close,
                "volume": 1000,
            }
        )

    for i in range(10):
        close = 103 + i * 0.03
        rows.append(
            {
                "date": f"2025-02-{i + 1:03d}",
                "open": close - 0.1,
                "high": close + 2.0,
                "low": close - 2.0,
                "close": close,
                "volume": 1000,
            }
        )

    for i in range(5):
        close = 103.5 + i * 0.03
        rows.append(
            {
                "date": f"2025-03-{i + 1:03d}",
                "open": close - 0.05,
                "high": close + 0.8,
                "low": close - 0.8,
                "close": close,
                "volume": 850,
            }
        )

    for i in range(5):
        close = 103.8 + i * 0.02
        rows.append(
            {
                "date": f"2025-04-{i + 1:03d}",
                "open": close - 0.03,
                "high": close + 0.25,
                "low": close - 0.25,
                "close": close,
                "volume": 650,
            }
        )

    rows.append(
        {
            "date": "2025-04-006",
            "open": 104.0,
            "high": 104.8,
            "low": 103.9,
            "close": 104.6,
            "volume": 1500,
        }
    )
    return pd.DataFrame(rows)


def test_vcp_h1_h2_buy_returns_break_buy_signal():
    result = analyze_vcp_h1_h2_buy(_vcp_breakout_fixture())

    assert result["status"] == "ok"
    assert result["grade"] == 3
    assert result["verdict"] == "BUY"
    assert result["setup"]["valid_setup"] is True
    assert result["signals"]["break_buy"] is True
    assert result["signals"]["buy"] is True
    assert result["pivot"] is not None
    assert result["risk_plan"]["stop_reference"] is not None


def test_vcp_h1_h2_buy_requires_enough_history():
    result = analyze_vcp_h1_h2_buy(_vcp_breakout_fixture().tail(100))

    assert result["status"] == "insufficient_data"
    assert result["grade"] == 0
