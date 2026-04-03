from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("LOG_PATH", str(tmp_path / "test.log"))
    monkeypatch.setenv("BOOTSTRAP_API_KEY", "bootstrap-test-token")
    get_settings.cache_clear()
    app = create_app()

    with app.state.database.connection() as conn:
        conn.execute(
            """
            INSERT INTO numbers (
                e164, country_name, national_number, status, activity_score,
                first_seen_at, last_seen_at, last_message_at, last_message_age_min, freshness_bucket
            ) VALUES (?, ?, ?, ?, ?, datetime('now'), datetime('now'), datetime('now'), ?, ?)
            """,
            ("+15550001111", "United States", "15550001111", "active", 100.0, 5, "hot"),
        )
        number_id = conn.execute("SELECT id FROM numbers WHERE e164 = ?", ("+15550001111",)).fetchone()["id"]
        conn.execute(
            """
            INSERT INTO number_sources (
                number_id, provider_id, provider_number_key, detail_url, discovery_url,
                provider_country_label, source_status, restricted, restricted_reason,
                last_seen_at, last_checked_at, last_success_at, last_real_message_at,
                last_real_message_age_min, raw_snapshot_json
            ) VALUES (
                ?, 'receive_smss', '15550001111', ?, ?, ?, 'open', 0, NULL,
                datetime('now'), datetime('now'), datetime('now'), datetime('now'), 5, '{}'
            )
            """,
            (
                number_id,
                "https://receive-smss.com/sms/15550001111/",
                "https://receive-smss.com/",
                "United States",
            ),
        )
        source_id = conn.execute(
            """
            SELECT id FROM number_sources
            WHERE provider_id = 'receive_smss' AND provider_number_key = '15550001111'
            """
        ).fetchone()["id"]
        conn.execute(
            """
            INSERT INTO messages (
                number_id, source_id, sender, body, received_at, observed_at, otp_code, dedupe_hash
            ) VALUES (?, ?, 'Test', 'Your OTP is 123456', datetime('now'), datetime('now'), '123456', 'seed-hash')
            """,
            (number_id, source_id),
        )

    with TestClient(app) as test_client:
        yield test_client


def test_api_auth_and_provider_config(client: TestClient):
    health = client.get("/healthz")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    assert client.get("/api/auth/keys").status_code == 401

    headers = {"Authorization": "Bearer bootstrap-test-token"}
    keys = client.get("/api/auth/keys", headers=headers)
    assert keys.status_code == 200
    assert len(keys.json()) >= 1

    update_resp = client.put(
        "/api/providers/receive_smss/config",
        headers=headers,
        json={
            "enabled": True,
            "priority": 9,
            "notes": "updated by test",
            "user_agent": "pytest-agent",
            "headers": {"accept-language": "en-US"},
            "cookies": {},
            "tokens": {},
        },
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["priority"] == 9
    assert update_resp.json()["headers"]["accept-language"] == "en-US"

    sync_resp = client.post("/api/providers/receive_smss/sync", headers=headers)
    assert sync_resp.status_code == 202
    assert sync_resp.json()["job_type"] == "sync_providers"
    assert sync_resp.json()["provider_id"] == "receive_smss"
    assert sync_resp.json()["status"] == "queued"

    jobs_resp = client.get("/api/jobs", headers=headers)
    assert jobs_resp.status_code == 200
    assert jobs_resp.json()[0]["provider_id"] == "receive_smss"
    assert jobs_resp.json()[0]["status"] == "queued"


def test_number_claim_and_complete_flow(client: TestClient):
    headers = {"Authorization": "Bearer bootstrap-test-token"}

    pick_resp = client.post("/api/numbers/pick", headers=headers, json={"app_slug": "openai"})
    assert pick_resp.status_code == 200
    selected = pick_resp.json()
    assert selected["e164"] == "+15550001111"

    claim_resp = client.post(
        "/api/claims",
        headers=headers,
        json={"app_slug": "openai", "app_name": "OpenAI", "purpose": "signup"},
    )
    assert claim_resp.status_code == 201
    claim = claim_resp.json()
    assert claim["status"] == "watching"

    claim_detail = client.get(f"/api/claims/{claim['claim_token']}", headers=headers)
    assert claim_detail.status_code == 200
    assert claim_detail.json()["messages"][0]["otp_code"] == "123456"

    complete_resp = client.post(
        f"/api/claims/{claim['claim_token']}/complete",
        headers=headers,
        json={"result": "success"},
    )
    assert complete_resp.status_code == 200
    assert complete_resp.json()["status"] == "completed"

    blacklist_resp = client.post(
        f"/api/numbers/{claim['number_id']}/blacklist",
        headers=headers,
        json={"reason": "test blacklist"},
    )
    assert blacklist_resp.status_code == 200

    clear_resp = client.post(f"/api/numbers/{claim['number_id']}/blacklist/clear", headers=headers)
    assert clear_resp.status_code == 200


def test_pick_triggers_auto_replenish_when_app_is_exhausted(client: TestClient):
    headers = {"Authorization": "Bearer bootstrap-test-token"}

    mark_used = client.post(
        "/api/numbers/1/app-state",
        headers=headers,
        json={
            "app_slug": "openai",
            "app_name": "OpenAI",
            "status": "used",
            "notes": "exhausted by api smoke test",
        },
    )
    assert mark_used.status_code == 200

    pick_resp = client.post("/api/numbers/pick", headers=headers, json={"app_slug": "openai"})
    assert pick_resp.status_code == 200
    assert pick_resp.json() is None

    jobs_resp = client.get("/api/jobs", headers=headers)
    assert jobs_resp.status_code == 200
    payload = jobs_resp.json()[0]
    assert payload["provider_id"] is None
    assert "auto_replenish_app_exhausted" in payload["payload_json"]
