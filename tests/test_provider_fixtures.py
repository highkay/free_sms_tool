from __future__ import annotations

import json
from pathlib import Path

from app.config import Settings
from app.services import sync as sync_module
from scripts import provider_probe

FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "providers"
MESSAGE_PROVIDERS = (
    "receive_smss",
    "temp_number",
    "sms24",
    "smstome",
    "receive_sms_free_cc",
)


def _read_text(provider_id: str, name: str) -> str:
    return (FIXTURE_ROOT / provider_id / name).read_text(encoding="utf-8")


def _read_json(provider_id: str, name: str):
    return json.loads(_read_text(provider_id, name))


def test_temporary_phone_number_restricted_detection():
    html = """
    <div class="direct-chat-msg">
      Register or login to the website before view sms
    </div>
    """
    detail = provider_probe.parse_detail("temporary_phone_number", html)
    assert detail["restricted"] is True
    assert detail["restricted_reason"] == "login_required_for_sms"
    assert detail["latest_age_min"] is None


def test_freephonenum_register_to_view_detection():
    html = """
    <div class="message_details">
      Register to View
    </div>
    """
    detail = provider_probe.parse_detail("freephonenum", html)
    assert detail["restricted"] is True
    assert detail["restricted_reason"] == "register_to_view"
    assert detail["latest_age_min"] is None


def test_quackr_discovery_fixture():
    payload = _read_json("quackr", "numbers.json")
    expected = _read_json("quackr", "entries.json")
    actual = sync_module._discover_quackr_entries_from_payload(payload, len(expected))
    assert actual == expected


def test_quackr_entries_are_restricted_discovery_only():
    entries = _read_json("quackr", "entries.json")
    assert entries
    assert all(entry["restricted"] for entry in entries)
    assert all(entry["restricted_reason"] == "message_api_verification_required" for entry in entries)


def test_html_discovery_fixtures():
    for provider_id in MESSAGE_PROVIDERS:
        probe_config = provider_probe.PROVIDERS[provider_id]
        discovery_html = _read_text(provider_id, "discovery.html")
        expected_entry = _read_json(provider_id, "entry.json")
        entries = sync_module._build_html_entries(
            probe_config.discovery_url,
            discovery_html,
            probe_config.link_patterns,
            5,
        )
        assert entries
        assert expected_entry in entries


def test_detail_parse_fixtures():
    for provider_id in MESSAGE_PROVIDERS:
        detail_html = _read_text(provider_id, "detail.html")
        detail = provider_probe.parse_detail(provider_id, detail_html)
        assert detail["restricted"] is False
        assert detail["latest_age_min"] is not None
        assert detail["evidence"]


def test_message_parse_fixtures():
    for provider_id in MESSAGE_PROVIDERS:
        detail_html = _read_text(provider_id, "detail.html")
        messages = sync_module._parse_messages(provider_id, detail_html)
        assert messages
        assert any(message["body"] for message in messages)


def test_extract_number_key_and_country_name_from_fixtures():
    for provider_id in MESSAGE_PROVIDERS:
        entry = _read_json(provider_id, "entry.json")
        detail_html = _read_text(provider_id, "detail.html")
        number_key = sync_module._extract_number_key(provider_id, entry["detail_url"])
        assert number_key
        assert sync_module._e164_from_key(number_key).startswith("+")
        country_name = sync_module._extract_country_name(
            provider_id,
            entry["detail_url"],
            detail_html,
        )
        assert country_name


def test_jiemahao_discovery_fixture_filters_dirty_rows_and_sets_number_key():
    probe_config = provider_probe.PROVIDERS["jiemahao"]
    discovery_html = _read_text("jiemahao", "discovery.html")
    raw_entries = sync_module._build_html_entries(
        probe_config.discovery_url,
        discovery_html,
        probe_config.link_patterns,
        10,
    )
    assert raw_entries
    assert any(entry["label"] == "+100" for entry in raw_entries)

    entries = sync_module._postprocess_discovered_entries("jiemahao", raw_entries)
    expected_entry = _read_json("jiemahao", "entry.json")

    assert expected_entry in entries
    assert all(entry["label"] != "+100" for entry in entries)
    assert all(entry["number_key"] == sync_module._normalize_public_number_key(entry["label"]) for entry in entries)


def test_jiemahao_detail_fixture_is_turnstile_restricted():
    entry = _read_json("jiemahao", "entry.json")
    detail_html = _read_text("jiemahao", "detail.html")
    detail = provider_probe.parse_detail("jiemahao", detail_html)

    assert detail["restricted"] is True
    assert detail["restricted_reason"] == "turnstile_required_for_sms"
    assert detail["latest_age_min"] is None
    assert sync_module._extract_number_key("jiemahao", entry["detail_url"]) is None
    assert sync_module._e164_from_key(entry["number_key"]) == "+15613969222"
    assert sync_module._extract_country_name("jiemahao", entry["detail_url"], detail_html) == "美国"
    assert sync_module._parse_messages("jiemahao", detail_html) == []


def test_freephonenum_discovery_fixture_contains_restricted_candidates():
    probe_config = provider_probe.PROVIDERS["freephonenum"]
    discovery_html = _read_text("freephonenum", "discovery.html")
    expected_entry = _read_json("freephonenum", "entry.json")
    entries = sync_module._build_html_entries(
        probe_config.discovery_url,
        discovery_html,
        probe_config.link_patterns,
        5,
    )

    assert expected_entry in entries
    assert any("Register to View" in (entry["label"] or "") for entry in entries)


def test_freephonenum_detail_fixture_is_stale_and_message_less():
    entry = _read_json("freephonenum", "entry.json")
    detail_html = _read_text("freephonenum", "detail.html")
    detail = provider_probe.parse_detail("freephonenum", detail_html)

    assert detail["restricted"] is False
    assert detail["latest_age_min"] is not None
    assert detail["latest_age_min"] >= 60 * 24 * 365
    assert sync_module._extract_number_key("freephonenum", entry["detail_url"]) == "5417083275"
    assert sync_module._extract_country_name("freephonenum", entry["detail_url"], detail_html) is None
    assert sync_module._parse_messages("freephonenum", detail_html) == []


def test_receivesms_org_detail_fixture_is_stale_seed_only():
    entry = _read_json("receivesms_org", "entry.json")
    detail_html = _read_text("receivesms_org", "detail.html")
    detail = provider_probe.parse_detail("receivesms_org", detail_html)

    assert entry["discovery_url"] is None
    assert detail["restricted"] is False
    assert detail["latest_age_min"] is not None
    assert detail["latest_age_min"] >= 60 * 24 * 30
    assert sync_module._extract_number_key("receivesms_org", entry["detail_url"]) is None
    assert sync_module._extract_country_name("receivesms_org", entry["detail_url"], detail_html) is None
    assert sync_module._parse_messages("receivesms_org", detail_html) == []


def test_flaresolverr_runtime_uses_settings_url(monkeypatch):
    calls: list[str] = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url, json=None, timeout=None):
            calls.append(url)
            return object()

    def fake_flare_get(
        client,
        session,
        url,
        *,
        cookies=None,
        wait_in_seconds=None,
        flaresolverr_url=provider_probe.FLARESOLVERR_URL,
    ):
        calls.append(flaresolverr_url)
        return ('<a href="/temporary-numbers/united-kingdom/447700900001">number</a>', {})

    monkeypatch.setattr(provider_probe.httpx, "Client", FakeClient)
    monkeypatch.setattr(provider_probe, "flare_get", fake_flare_get)

    settings = Settings(flaresolverr_url="http://flaresolverr-service:8191/v1")
    runtime = sync_module.ProviderRuntimeConfig(
        provider_id="temp_number",
        transport_mode="flaresolverr",
        discovery_mode="html",
        detail_mode="html",
        auth_mode="cookie_clearance",
        user_agent="pytest-agent",
        headers={},
        cookies={},
        tokens={},
    )

    entries = sync_module._discover_entries("temp_number", settings, runtime, 1)
    detail_html = sync_module._fetch_detail_html(
        "temp_number",
        "https://temp-number.com/temporary-numbers/united-kingdom/447700900001",
        runtime,
        settings,
    )

    assert entries
    assert detail_html
    assert calls
    assert all(call == "http://flaresolverr-service:8191/v1" for call in calls)
