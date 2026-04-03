from __future__ import annotations

import json
from pathlib import Path

from app.config import get_settings
from app.db.core import Database
from app.services import sync as sync_module
from scripts import provider_probe

ROOT = Path(__file__).resolve().parents[1]

FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "providers"
TARGETS = {
    "receive_smss": "messages",
    "temp_number": "messages",
    "sms24": "messages",
    "smstome": "messages",
    "receive_sms_free_cc": "messages",
    "quackr": "discovery_only",
    "jiemahao": "detail_only",
    "freephonenum": "detail_only",
    "receivesms_org": "detail_only",
}


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _capture_quackr(runtime: sync_module.ProviderRuntimeConfig) -> None:
    client = provider_probe.httpx.Client(
        timeout=30.0,
        headers=sync_module._build_direct_headers(runtime),
        follow_redirects=True,
    )
    if runtime.cookies:
        client.cookies.update(runtime.cookies)
    response = client.get("https://quackr.io/numbers.json")
    response.raise_for_status()
    payload = response.json()
    entries = sync_module._discover_quackr_entries_from_payload(payload, 3)
    if not entries:
        raise RuntimeError("quackr discovery returned no usable entries")
    base = FIXTURE_ROOT / "quackr"
    _write_json(base / "numbers.json", payload)
    _write_json(base / "entries.json", entries)


def _accept_detail_sample(mode: str, provider_id: str, detail: dict, messages: list[dict]) -> bool:
    if mode == "messages":
        return detail.get("latest_age_min") is not None and bool(messages)
    if provider_id in {"freephonenum", "jiemahao"}:
        return detail.get("restricted") or detail.get("latest_age_min") is not None
    return detail.get("latest_age_min") is not None


def _discovery_inputs(provider_id: str) -> tuple[str | None, list[str] | None]:
    probe_config = provider_probe.PROVIDERS[provider_id]
    return probe_config.discovery_url, probe_config.seed_details


def _capture_html_provider(
    provider_id: str,
    runtime: sync_module.ProviderRuntimeConfig,
    mode: str,
) -> None:
    probe_config = provider_probe.PROVIDERS[provider_id]
    discovery_url, seed_details = _discovery_inputs(provider_id)
    discovery_html = ""

    if runtime.transport_mode == "flaresolverr":
        client = provider_probe.httpx.Client(timeout=90.0)
        session = f"fixture-{provider_id}-{provider_probe.uuid.uuid4().hex[:8]}"
        client.post(
            provider_probe.FLARESOLVERR_URL,
            json={"cmd": "sessions.create", "session": session},
            timeout=90.0,
        )
        try:
            if discovery_url and probe_config.link_patterns:
                discovery_html, _ = provider_probe.flare_get(
                    client,
                    session,
                    discovery_url,
                    cookies=sync_module._build_flaresolverr_cookies(runtime, discovery_url),
                )
                entries = sync_module._build_html_entries(
                    discovery_url,
                    discovery_html,
                    probe_config.link_patterns,
                    5,
                )
                entries = sync_module._postprocess_discovered_entries(provider_id, entries)
            else:
                entries = [{"detail_url": url, "discovery_url": None, "label": None} for url in seed_details or []]

            if not entries:
                raise RuntimeError(f"{provider_id} discovery returned no entries")

            selected_html = ""
            selected_entry: dict[str, str | None] | None = None
            for entry in entries:
                detail_html, _ = provider_probe.flare_get(
                    client,
                    session,
                    entry["detail_url"],
                    cookies=sync_module._build_flaresolverr_cookies(runtime, entry["detail_url"]),
                )
                detail = provider_probe.parse_detail(provider_id, detail_html)
                messages = sync_module._parse_messages(provider_id, detail_html)
                if _accept_detail_sample(mode, provider_id, detail, messages):
                    selected_html = detail_html
                    selected_entry = entry
                    break
            if not selected_entry:
                raise RuntimeError(f"{provider_id} could not find a representative detail page")
        finally:
            client.post(
                provider_probe.FLARESOLVERR_URL,
                json={"cmd": "sessions.destroy", "session": session},
                timeout=90.0,
            )
    else:
        client = provider_probe.httpx.Client(timeout=30.0)
        if discovery_url and probe_config.link_patterns:
            discovery_html, _ = provider_probe.direct_get(
                client,
                discovery_url,
                headers=sync_module._build_direct_headers(runtime),
                cookies=runtime.cookies,
            )
            entries = sync_module._build_html_entries(
                discovery_url,
                discovery_html,
                probe_config.link_patterns,
                5,
            )
            entries = sync_module._postprocess_discovered_entries(provider_id, entries)
        else:
            entries = [{"detail_url": url, "discovery_url": None, "label": None} for url in seed_details or []]

        if not entries:
            raise RuntimeError(f"{provider_id} discovery returned no entries")

        selected_html = ""
        selected_entry = None
        for entry in entries:
            detail_html, _ = provider_probe.direct_get(
                client,
                entry["detail_url"],
                headers=sync_module._build_direct_headers(runtime),
                cookies=runtime.cookies,
            )
            detail = provider_probe.parse_detail(provider_id, detail_html)
            messages = sync_module._parse_messages(provider_id, detail_html)
            if _accept_detail_sample(mode, provider_id, detail, messages):
                selected_html = detail_html
                selected_entry = entry
                break
        if not selected_entry:
            raise RuntimeError(f"{provider_id} could not find a representative detail page")

    base = FIXTURE_ROOT / provider_id
    if discovery_html:
        _write_text(base / "discovery.html", discovery_html)
    _write_text(base / "detail.html", selected_html)
    _write_json(base / "entry.json", selected_entry)


def main() -> None:
    settings = get_settings()
    database = Database(settings.database_path)

    for provider_id, mode in TARGETS.items():
        runtime = sync_module._load_provider_runtime(database, provider_id)
        if provider_id == "quackr":
            _capture_quackr(runtime)
            continue
        try:
            _capture_html_provider(provider_id, runtime, mode)
        except Exception as exc:  # noqa: BLE001
            print(f"skip {provider_id}: {exc}")

    print(f"captured fixtures in {FIXTURE_ROOT}")


if __name__ == "__main__":
    main()
