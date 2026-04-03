from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app


@pytest.fixture
def web_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "web.db"))
    monkeypatch.setenv("LOG_PATH", str(tmp_path / "web.log"))
    monkeypatch.setenv("BOOTSTRAP_API_KEY", "web-ui-token")
    get_settings.cache_clear()
    app = create_app()

    app.state.repository.upsert_app(slug="openai", name="OpenAI", notes="web page test")

    with app.state.database.connection() as conn:
        conn.executemany(
            """
            INSERT INTO numbers (
                id, e164, country_name, national_number, status, activity_score,
                first_seen_at, last_seen_at, last_message_at, last_message_age_min, freshness_bucket
            ) VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'), datetime('now'), ?, ?)
            """,
            [
                (1, "+15550001111", "United States", "15550001111", "active", 100.0, 5, "hot"),
                (2, "+15550002222", "United States", "15550002222", "active", 90.0, 20, "warm"),
            ],
        )
        conn.executemany(
            """
            INSERT INTO number_sources (
                id, number_id, provider_id, provider_number_key, detail_url, discovery_url,
                provider_country_label, source_status, restricted, restricted_reason,
                last_seen_at, last_checked_at, last_success_at, last_real_message_at,
                last_real_message_age_min, raw_snapshot_json
            ) VALUES (
                ?, ?, 'receive_smss', ?, ?, ?, ?, 'open', 0, NULL,
                datetime('now'), datetime('now'), datetime('now'), datetime('now'), ?, '{}'
            )
            """,
            [
                (
                    1,
                    1,
                    "15550001111",
                    "https://receive-smss.com/sms/15550001111/",
                    "https://receive-smss.com/",
                    "United States",
                    5,
                ),
                (
                    2,
                    2,
                    "15550002222",
                    "https://receive-smss.com/sms/15550002222/",
                    "https://receive-smss.com/",
                    "United States",
                    20,
                ),
            ],
        )
        conn.executemany(
            """
            INSERT INTO messages (
                id, number_id, source_id, sender, body, received_at, observed_at, otp_code, dedupe_hash
            ) VALUES (?, ?, ?, 'Test', ?, datetime('now'), datetime('now'), ?, ?)
            """,
            [
                (1, 1, 1, "Your OTP is 123456", "123456", "web-message-1"),
                (2, 2, 2, "Your OTP is 654321", "654321", "web-message-2"),
            ],
        )

    claim = app.state.repository.create_claim(
        app_slug="openai",
        app_name="OpenAI",
        country_name="United States",
        provider_id=None,
        purpose="web page test",
        include_cooling=False,
        ttl_minutes=10,
    )

    with TestClient(app) as test_client:
        yield test_client, claim.claim_token


def test_navigation_labels_are_consistent(web_client):
    client, _claim_token = web_client
    response = client.get("/")
    assert response.status_code == 200

    for label in ["总览", "号码池", "认领记录", "应用", "来源站点", "API 密钥", "API 文档"]:
        assert label in response.text


@pytest.mark.parametrize(
    ("path_template", "expected_title", "expected_text"),
    [
        ("/", "总览 - Free SMS Tool", "总览"),
        ("/numbers", "号码池 - Free SMS Tool", "号码池"),
        ("/numbers/1", "号码详情 - Free SMS Tool", "号码详情"),
        ("/claims", "认领记录 - Free SMS Tool", "认领记录"),
        ("/claims/{claim_token}", "认领详情 - Free SMS Tool", "认领详情"),
        ("/apps", "应用 - Free SMS Tool", "应用"),
        ("/apps/openai", "应用详情 - Free SMS Tool", "应用详情"),
        ("/providers", "来源站点 - Free SMS Tool", "来源站点"),
        ("/auth", "API 密钥 - Free SMS Tool", "API 密钥"),
    ],
)
def test_main_web_pages_have_consistent_titles(web_client, path_template: str, expected_title: str, expected_text: str):
    client, claim_token = web_client
    path = path_template.format(claim_token=claim_token)
    response = client.get(path)

    assert response.status_code == 200
    assert f"<title>{expected_title}</title>" in response.text
    assert expected_text in response.text


def test_pick_partial_returns_detail_link(web_client):
    client, _claim_token = web_client
    response = client.get("/numbers/pick")

    assert response.status_code == 200
    assert "已选中号码" in response.text
    assert "/numbers/2" in response.text


def test_sync_page_queues_job(web_client):
    client, _claim_token = web_client
    response = client.post("/sync/run")

    assert response.status_code == 200
    assert "同步任务已入队" in response.text
    assert "排队中" in response.text


def test_web_ui_basic_auth_when_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "auth.db"))
    monkeypatch.setenv("LOG_PATH", str(tmp_path / "auth.log"))
    monkeypatch.setenv("BOOTSTRAP_API_KEY", "auth-token")
    monkeypatch.setenv("WEB_UI_USERNAME", "operator")
    monkeypatch.setenv("WEB_UI_PASSWORD", "secret-pass")
    get_settings.cache_clear()
    app = create_app()

    with TestClient(app) as client:
        unauthorized = client.get("/auth")
        assert unauthorized.status_code == 401

        token = base64.b64encode(b"operator:secret-pass").decode("ascii")
        authorized = client.get("/auth", headers={"Authorization": f"Basic {token}"})
        assert authorized.status_code == 200
