from __future__ import annotations

import httpx
import pytest

from app.config import get_settings
from app.db.bootstrap import initialize_database
from app.db.core import Database
from app.services import sync as sync_module
from scripts import provider_probe


@pytest.mark.live
@pytest.mark.parametrize("provider_id", ["receive_smss", "sms24", "quackr"])
def test_live_provider_discovery(provider_id: str, tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "live.db"))
    monkeypatch.setenv("LOG_PATH", str(tmp_path / "live.log"))
    get_settings.cache_clear()

    settings = get_settings()
    database = Database(settings.database_path)
    initialize_database(database, settings)
    runtime = sync_module._load_provider_runtime(database, provider_id)
    try:
        entries = sync_module._discover_entries(provider_id, settings, runtime, 1)
    except (httpx.HTTPError, RuntimeError) as exc:
        pytest.skip(f"live provider discovery unavailable: {exc}")

    assert entries
    if provider_id == "quackr":
        assert entries[0]["restricted"] is True
        return

    try:
        html = sync_module._fetch_detail_html(provider_id, entries[0]["detail_url"], runtime, settings)
    except (httpx.HTTPError, RuntimeError) as exc:
        pytest.skip(f"live provider detail unavailable: {exc}")
    detail = provider_probe.parse_detail(provider_id, html)
    messages = sync_module._parse_messages(provider_id, html)

    assert detail["latest_age_min"] is not None
    assert messages
