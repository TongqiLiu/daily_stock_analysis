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
    assert result["grade"] == 4
    assert result["verdict"] == "突破 BUY"
    assert result["setup"]["valid_setup"] is True
    assert result["setup"]["vcp_setup"] is False
    assert result["signals"]["lc_raw"] is False
    assert result["signals"]["lc"] is False
    assert result["signals"]["break_buy"] is True
    assert result["signals"]["labels"] == ["BUY"]
    assert result["signals"]["extend"] is False
    assert result["signals"]["fail"] is False
    assert result["risk_ok"] is True
    assert result["risk_pct_to_stop"] <= 10
    assert result["pivot"] is not None
    assert result["stop"] is not None
    assert result["risk_plan"]["entry_reference"] is not None
    assert result["parameters"]["near_high_ratio"] == 0.88
    assert result["parameters"]["range_max"] == 0.18
    assert result["parameters"]["risk_max"] == 0.10
    assert result["parameters"]["valid_setup_window"] == 10
    assert result["parameters"]["failure_window"] == 10


def test_vcp_breakout_trader_blocks_buy_when_risk_is_too_wide():
    frame = _breakout_trader_fixture()
    frame.loc[frame.index[-1], ["open", "high", "low", "close", "volume"]] = [107.0, 108.5, 106.0, 108.0, 2400]

    result = analyze_vcp_breakout_trader(frame)

    assert result["setup"]["valid_setup"] is True
    assert result["risk_ok"] is False
    assert result["risk_pct_to_stop"] > 10
    assert result["signals"]["break_raw"] is False
    assert result["signals"]["break_buy"] is False
    assert result["grade"] < 4


def test_vcp_breakout_trader_lc_only_before_main_pivot_breakout():
    frame = _breakout_trader_fixture()
    previous_window = frame.index[-11:-1]
    frame.loc[previous_window, "open"] = frame.loc[previous_window, "close"] + 0.2
    frame.loc[frame.index[-1], ["open", "high", "low", "close", "volume"]] = [102.7, 103.3, 102.2, 103.0, 700]

    result = analyze_vcp_breakout_trader(frame)

    assert result["setup"]["valid_setup"] is True
    assert result["current_price"] < result["pivot"]
    assert result["signals"]["lc_raw"] is True
    assert result["signals"]["lc"] is True
    assert result["signals"]["break_raw"] is False
    assert result["signals"]["break_buy"] is False
    assert result["signals"]["labels"] == ["LC"]
    assert result["grade"] == 3


def test_vcp_breakout_trader_uses_ext_label_for_extension_marker():
    frame = _breakout_trader_fixture()
    frame.loc[frame.index[-1], ["open", "high", "low", "close", "volume"]] = [102.4, 125.0, 102.2, 123.0, 800]

    result = analyze_vcp_breakout_trader(frame)

    assert result["signals"]["extend"] is True
    assert result["signals"]["extend_first"] is True
    assert result["signals"]["labels"] == ["EXT"]
    assert "EXT" in result["risk_plan"]["extension_warning"]


def test_vcp_breakout_trader_requires_enough_history():
    result = analyze_vcp_breakout_trader(_breakout_trader_fixture().tail(100))

    assert result["status"] == "insufficient_data"
    assert result["grade"] == 0
