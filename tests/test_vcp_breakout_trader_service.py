import pandas as pd

from src.services.vcp_breakout_trader_service import analyze_vcp_breakout_trader


def _breakout_trader_fixture() -> pd.DataFrame:
    rows = []
    for i in range(225):
        close = 50 + i * 0.2
        rows.append(
            {
                "date": f"2025-01-{i + 1:03d}",
                "open": close - 0.08,
                "high": close + 0.35,
                "low": close - 0.35,
                "close": close,
                "volume": 1000,
            }
        )

    for i in range(10):
        close = 99.8 + i * 0.08
        rows.append(
            {
                "date": f"2025-02-{i + 1:03d}",
                "open": close - 0.15,
                "high": close + 3.0,
                "low": close - 3.0,
                "close": close,
                "volume": 1000,
            }
        )

    for i in range(5):
        close = 101.2 + i * 0.08
        rows.append(
            {
                "date": f"2025-03-{i + 1:03d}",
                "open": close - 0.10,
                "high": close + 1.2,
                "low": close - 1.2,
                "close": close,
                "volume": 650,
            }
        )

    for i in range(4):
        close = 102.1 + i * 0.07
        rows.append(
            {
                "date": f"2025-04-{i + 1:03d}",
                "open": close - 0.08,
                "high": close + 0.45,
                "low": close - 0.45,
                "close": close,
                "volume": 550,
            }
        )

    rows.append(
        {
            "date": "2025-04-005",
            "open": 102.4,
            "high": 106.4,
            "low": 102.2,
            "close": 105.8,
            "volume": 2400,
        }
    )
    return pd.DataFrame(rows)


def test_vcp_breakout_trader_returns_break_buy_signal():
    result = analyze_vcp_breakout_trader(_breakout_trader_fixture())

    assert result["status"] == "ok"
    assert result["grade"] == 3
    assert result["verdict"] == "突破 BUY"
    assert result["setup"]["valid_setup"] is True
    assert result["signals"]["break_buy"] is True
    assert result["signals"]["fail"] is False
    assert result["pivot"] is not None
    assert result["stop"] is not None
    assert result["risk_plan"]["entry_reference"] is not None


def test_vcp_breakout_trader_requires_enough_history():
    result = analyze_vcp_breakout_trader(_breakout_trader_fixture().tail(100))

    assert result["status"] == "insufficient_data"
    assert result["grade"] == 0
