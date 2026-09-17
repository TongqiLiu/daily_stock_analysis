# -*- coding: utf-8 -*-
"""API tests for DecisionSignal P5 outcomes and feedback."""

from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

try:
    import litellm  # noqa: F401
except ModuleNotFoundError:
    sys.modules["litellm"] = MagicMock()

import src.auth as auth
from api.app import create_app
from src.config import Config
from src.storage import DatabaseManager, StockDaily


def _reset_auth_globals() -> None:
    auth._auth_enabled = None
    auth._session_secret = None
    auth._password_hash_salt = None
    auth._password_hash_stored = None
    auth._rate_limit = {}


@pytest.fixture()
def client_and_db(tmp_path):
    old_env_file = os.environ.get("ENV_FILE")
    old_database_path = os.environ.get("DATABASE_PATH")
    env_path = tmp_path / ".env"
    db_path = tmp_path / "decision_signal_outcome_api.db"
    static_dir = tmp_path / "empty-static"
    static_dir.mkdir()
    env_path.write_text(
        "\n".join(
            [
                "STOCK_LIST=600519",
                "GEMINI_API_KEY=test",
                "ADMIN_AUTH_ENABLED=false",
                f"DATABASE_PATH={db_path}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    os.environ["ENV_FILE"] = str(env_path)
    os.environ["DATABASE_PATH"] = str(db_path)
    _reset_auth_globals()
    Config.reset_instance()
    DatabaseManager.reset_instance()
    app = create_app(static_dir=Path(static_dir))
    client = TestClient(app)
    db = DatabaseManager.get_instance()
    try:
        yield client, db
    finally:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        _reset_auth_globals()
        if old_env_file is None:
            os.environ.pop("ENV_FILE", None)
        else:
            os.environ["ENV_FILE"] = old_env_file
        if old_database_path is None:
            os.environ.pop("DATABASE_PATH", None)
        else:
            os.environ["DATABASE_PATH"] = old_database_path


def _payload(**overrides):
    payload = {
        "stock_code": "600519",
        "stock_name": "贵州茅台",
        "market": "cn",
        "source_type": "analysis",
        "source_agent": "api-test",
        "source_report_id": 4301,
        "trace_id": "trace-outcome-api",
        "decision_profile": "balanced",
        "market_phase": "postmarket",
        "trigger_source": "api",
        "action": "buy",
        "confidence": 0.75,
        "score": 80,
        "horizon": "3d",
        "entry_low": 100,
        "stop_loss": 95,
        "reason": "突破平台",
        "data_quality_summary": {"level": "good"},
        "metadata": {
            "market_phase_summary": {"session_date": "2024-01-02"},
            "holding_state": "holding",
            "profile_source": "auto_default",
        },
    }
    payload.update(overrides)
    return payload


def _seed_bars(db: DatabaseManager, *, code: str = "600519") -> None:
    with db.session_scope() as session:
        session.add(StockDaily(code=code, date=date(2024, 1, 2), open=100, high=101, low=99, close=100))
        session.add(StockDaily(code=code, date=date(2024, 1, 3), open=103, high=104, low=102, close=103))
        session.add(StockDaily(code=code, date=date(2024, 1, 4), open=104, high=105, low=103, close=104))
        session.add(StockDaily(code=code, date=date(2024, 1, 5), open=105, high=106, low=104, close=105))


def test_outcome_run_list_stats_signal_outcomes_and_feedback(client_and_db) -> None:
    client, db = client_and_db
    created_resp = client.post("/api/v1/decision-signals", json=_payload())
    assert created_resp.status_code == 200, created_resp.text
    signal_id = created_resp.json()["item"]["id"]
    _seed_bars(db)

    run_resp = client.post(
        "/api/v1/decision-signals/outcomes/run",
        json={"signal_id": signal_id},
    )
    assert run_resp.status_code == 200, run_resp.text
    run_data = run_resp.json()
    assert run_data["evaluated"] == 1
    assert run_data["created"] == 1
    assert run_data["items"][0]["outcome"] == "hit"
    assert run_data["items"][0]["stock_return_pct"] == 5.0
    assert run_data["items"][0]["holding_state"] == "holding"

    second_run_resp = client.post(
        "/api/v1/decision-signals/outcomes/run",
        json={"signal_id": signal_id},
    )
    assert second_run_resp.status_code == 200, second_run_resp.text
    assert second_run_resp.json()["evaluated"] == 0
    assert second_run_resp.json()["skipped"] == 1

    list_resp = client.get(
        "/api/v1/decision-signals/outcomes",
        params={"signal_id": signal_id, "horizon": "3d"},
    )
    assert list_resp.status_code == 200, list_resp.text
    assert list_resp.json()["total"] == 1
    audit_item = list_resp.json()["items"][0]
    assert audit_item["stock_code"] == "600519"
    assert audit_item["stock_name"] == "贵州茅台"
    assert audit_item["reason"] == "突破平台"
    assert audit_item["entry_low"] == 100
    assert audit_item["stop_loss"] == 95

    scoped_list_resp = client.get(
        "/api/v1/decision-signals/outcomes",
        params={"stock_code": "600519", "source_type": "analysis"},
    )
    assert scoped_list_resp.status_code == 200, scoped_list_resp.text
    assert scoped_list_resp.json()["total"] == 1

    stats_resp = client.get("/api/v1/decision-signals/outcomes/stats")
    assert stats_resp.status_code == 200, stats_resp.text
    stats = stats_resp.json()
    assert stats["total"] == 1
    assert stats["hit"] == 1
    assert stats["breakdowns"]["action"][0]["value"] == "buy"
    calibration = stats["profile_calibration"]
    assert calibration["minimum_completed_sample_size"] == 30
    assert set(calibration["breakdowns"]) == {
        "decision_profile",
        "decision_profile_action",
        "decision_profile_horizon",
        "decision_profile_market_phase",
        "decision_profile_data_quality_level",
        "profile_source",
    }
    assert calibration["breakdowns"]["decision_profile"][0] == {
        "dimensions": {"decision_profile": "balanced"},
        "total": 1,
        "completed": 1,
        "unable": 0,
        "hit": 1,
        "miss": 0,
        "neutral": 0,
        "sample_sufficient": False,
        "hit_rate_pct": None,
        "avg_stock_return_pct": None,
        "miss_rate_pct": None,
        "unable_rate_pct": None,
        "max_adverse_excursion_pct": None,
    }
    assert calibration["breakdowns"]["decision_profile_action"][0]["dimensions"] == {
        "decision_profile": "balanced",
        "action": "buy",
    }
    assert calibration["breakdowns"]["decision_profile_horizon"][0]["dimensions"] == {
        "decision_profile": "balanced",
        "horizon": "3d",
    }
    assert calibration["breakdowns"]["profile_source"][0]["dimensions"] == {
        "profile_source": "auto_default",
    }

    signal_outcomes_resp = client.get(f"/api/v1/decision-signals/{signal_id}/outcomes")
    assert signal_outcomes_resp.status_code == 200, signal_outcomes_resp.text
    assert signal_outcomes_resp.json()["items"][0]["signal_id"] == signal_id

    empty_feedback_resp = client.get(f"/api/v1/decision-signals/{signal_id}/feedback")
    assert empty_feedback_resp.status_code == 200, empty_feedback_resp.text
    assert empty_feedback_resp.json()["feedback_value"] is None

    put_feedback_resp = client.put(
        f"/api/v1/decision-signals/{signal_id}/feedback",
        json={
            "feedback_value": "useful",
            "reason_code": "matched_plan",
            "note": "后验表现符合预期",
            "source": "web",
        },
    )
    assert put_feedback_resp.status_code == 200, put_feedback_resp.text
    assert put_feedback_resp.json()["feedback_value"] == "useful"
    assert put_feedback_resp.json()["source"] == "web"

    get_feedback_resp = client.get(f"/api/v1/decision-signals/{signal_id}/feedback")
    assert get_feedback_resp.status_code == 200, get_feedback_resp.text
    assert get_feedback_resp.json()["reason_code"] == "matched_plan"


def test_outcome_api_rejects_invalid_params_and_returns_404(client_and_db) -> None:
    client, _db = client_and_db

    missing_run_resp = client.post(
        "/api/v1/decision-signals/outcomes/run",
        json={"signal_id": 999999},
    )
    assert missing_run_resp.status_code == 404

    invalid_run_resp = client.post(
        "/api/v1/decision-signals/outcomes/run",
        json={"horizons": ["bad"]},
    )
    assert invalid_run_resp.status_code == 422

    invalid_list_resp = client.get(
        "/api/v1/decision-signals/outcomes",
        params={"outcome": "bad"},
    )
    assert invalid_list_resp.status_code == 400

    missing_outcomes_resp = client.get("/api/v1/decision-signals/999999/outcomes")
    assert missing_outcomes_resp.status_code == 404

    missing_feedback_resp = client.get("/api/v1/decision-signals/999999/feedback")
    assert missing_feedback_resp.status_code == 404

    empty_stats_resp = client.get("/api/v1/decision-signals/outcomes/stats")
    assert empty_stats_resp.status_code == 200
    empty_calibration = empty_stats_resp.json()["profile_calibration"]
    assert empty_calibration["minimum_completed_sample_size"] == 30
    assert all(not buckets for buckets in empty_calibration["breakdowns"].values())


def test_outcome_stats_filters_agent_source(client_and_db) -> None:
    client, db = client_and_db
    analysis = client.post("/api/v1/decision-signals", json=_payload())
    agent = client.post(
        "/api/v1/decision-signals",
        json=_payload(source_type="agent", source_report_id=4302, trace_id="trace-agent-outcome-api"),
    )
    assert analysis.status_code == 200
    assert agent.status_code == 200
    _seed_bars(db)
    run = client.post(
        "/api/v1/decision-signals/outcomes/run",
        json={"horizons": ["3d"], "limit": 10},
    )
    assert run.status_code == 200, run.text

    stats = client.get(
        "/api/v1/decision-signals/outcomes/stats",
        params={"source_type": "agent"},
    )

    assert stats.status_code == 200, stats.text
    assert stats.json()["source_type"] == "agent"
    assert stats.json()["total"] == 1


def test_ai_post_review_endpoint_is_read_only_and_scoped(client_and_db, monkeypatch) -> None:
    from src.services.decision_signal_post_review_service import DecisionSignalPostReviewService

    captured = {}

    def fake_generate(self, *, horizons=None, source_type=None):
        captured.update(horizons=horizons, source_type=source_type)
        return {
            "content": "## 结论\n仅描述统计。",
            "provider": "fixture",
            "model": "fixture-model",
            "prompt_version": "decision-signal-post-review-v1",
            "completed_samples": 12,
            "generated_at": "2026-09-05T00:00:00+00:00",
        }

    monkeypatch.setattr(DecisionSignalPostReviewService, "generate", fake_generate)
    client, _db = client_and_db

    response = client.post(
        "/api/v1/decision-signals/outcomes/ai-review",
        json={"horizons": ["1d", "3d"], "source_type": "agent"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["completed_samples"] == 12
    assert captured == {"horizons": ["1d", "3d"], "source_type": "agent"}


def test_ai_post_review_endpoint_reports_backend_unavailable(client_and_db, monkeypatch) -> None:
    from src.services.decision_signal_post_review_service import DecisionSignalPostReviewService

    def fail_generate(self, *, horizons=None, source_type=None):
        raise RuntimeError("provider timeout")

    monkeypatch.setattr(DecisionSignalPostReviewService, "generate", fail_generate)
    client, _db = client_and_db
    response = client.post(
        "/api/v1/decision-signals/outcomes/ai-review",
        json={"horizons": ["3d"]},
    )

    assert response.status_code == 503
    assert response.json()["error"] == "ai_backend_unavailable"


def test_outcome_run_retries_transient_unable_by_default(client_and_db) -> None:
    client, db = client_and_db
    created_resp = client.post("/api/v1/decision-signals", json=_payload())
    assert created_resp.status_code == 200, created_resp.text
    signal_id = created_resp.json()["item"]["id"]
    with db.session_scope() as session:
        session.add(StockDaily(code="600519", date=date(2024, 1, 2), open=100, high=101, low=99, close=100))
        session.add(StockDaily(code="600519", date=date(2024, 1, 3), open=103, high=104, low=102, close=103))

    first_run = client.post(
        "/api/v1/decision-signals/outcomes/run",
        json={"signal_id": signal_id},
    )
    assert first_run.status_code == 200, first_run.text
    assert first_run.json()["items"][0]["unable_reason"] == "insufficient_forward_bars"

    with db.session_scope() as session:
        session.add(StockDaily(code="600519", date=date(2024, 1, 4), open=104, high=105, low=103, close=104))
        session.add(StockDaily(code="600519", date=date(2024, 1, 5), open=105, high=106, low=104, close=105))
    second_run = client.post(
        "/api/v1/decision-signals/outcomes/run",
        json={"signal_id": signal_id},
    )

    assert second_run.status_code == 200, second_run.text
    second_data = second_run.json()
    assert second_data["evaluated"] == 1
    assert second_data["updated"] == 1
    assert second_data["skipped"] == 0
    assert second_data["items"][0]["eval_status"] == "completed"
    assert second_data["items"][0]["stock_return_pct"] == 5.0


def test_outcome_run_uses_hk_alias_stock_code_filter(client_and_db) -> None:
    client, db = client_and_db
    created_resp = client.post(
        "/api/v1/decision-signals",
        json=_payload(
            stock_code="00700",
            stock_name="Tencent",
            market="hk",
            horizon="1d",
            trace_id="trace-outcome-api-hk",
        ),
    )
    assert created_resp.status_code == 200, created_resp.text
    signal_id = created_resp.json()["item"]["id"]
    assert created_resp.json()["item"]["stock_code"] == "HK00700"
    _seed_bars(db, code="HK00700")

    run_resp = client.post(
        "/api/v1/decision-signals/outcomes/run",
        json={"stock_code": "00700", "horizons": ["1d"]},
    )
    assert run_resp.status_code == 200, run_resp.text
    run_data = run_resp.json()
    assert run_data["evaluated"] == 1
    assert run_data["created"] == 1
    assert run_data["items"][0]["signal_id"] == signal_id

    force_resp = client.post(
        "/api/v1/decision-signals/outcomes/run",
        json={"stock_code": "00700", "horizons": ["1d"], "force": True},
    )
    assert force_resp.status_code == 200, force_resp.text
    force_data = force_resp.json()
    assert force_data["evaluated"] == 1
    assert force_data["updated"] == 1
    assert force_data["items"][0]["signal_id"] == signal_id
