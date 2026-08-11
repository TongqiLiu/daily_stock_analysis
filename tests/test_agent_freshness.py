# -*- coding: utf-8 -*-
"""Tests for the current-analysis market-data freshness gate."""

from src.agent.freshness import validate_fresh_market_data


def _tool_message(name: str, payload: dict) -> dict:
    import json

    return {
        "role": "tool",
        "name": name,
        "content": json.dumps(payload, ensure_ascii=False),
    }


def _fresh_messages(code: str = "VST") -> list[dict]:
    return [
        _tool_message(
            "get_realtime_quote",
            {"code": code, "price": 144.91, "fetched_at": "2026-08-12T01:00:00+00:00"},
        ),
        _tool_message(
            "get_daily_history",
            {
                "code": code,
                "source": "LongbridgeFetcher",
                "cache_hit": False,
                "refresh_mode": "network",
                "fetched_at": "2026-08-12T01:00:01+00:00",
                "latest_data_date": "2026-08-11",
                "data": [{"date": "2026-08-11", "close": 144.91}],
            },
        ),
    ]


def test_accepts_fresh_quote_and_network_history_for_required_stock() -> None:
    result = validate_fresh_market_data(_fresh_messages(), required_stock_codes={"VST"})

    assert result.ok is True
    assert result.missing == []


def test_ignores_previous_turn_tool_messages_when_current_turn_is_missing() -> None:
    result = validate_fresh_market_data(
        _fresh_messages() + [{"role": "user", "content": "本轮没有工具结果"}],
        required_stock_codes={"KORU"},
    )

    assert result.ok is False
    assert any("KORU" in item for item in result.missing)


def test_rejects_db_cache_even_when_it_has_a_recent_date() -> None:
    messages = _fresh_messages()
    messages[1]["content"] = messages[1]["content"].replace(
        '"refresh_mode": "network"', '"refresh_mode": "cache"'
    ).replace('"cache_hit": false', '"cache_hit": true')

    result = validate_fresh_market_data(messages, required_stock_codes={"VST"})

    assert result.ok is False
    assert any("get_daily_history" in item for item in result.missing)
