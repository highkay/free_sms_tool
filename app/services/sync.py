from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup
from loguru import logger

from app.config import Settings
from app.db.core import Database
from app.services.selection import activity_score_from_age, freshness_bucket, number_status_from_age
from scripts import provider_probe

AGE_PATTERN = re.compile(
    (
        r"(just now|"
        r"\d+\s+(?:second|seconds|minute|minutes|hour|hours|day|days|week|weeks|month|months|year|years)\s+ago|"
        r"\d+\s*分钟前|\d+\s*小?时前|\d+\s*天前|\d+\s*周前)"
    ),
    re.I,
)
OTP_PATTERN = re.compile(r"\b(\d{4,8})\b")


@dataclass(slots=True)
class SyncResult:
    provider_id: str
    discovered_count: int
    synced_count: int
    message_count: int
    error: str | None = None


@dataclass(slots=True)
class ProviderRuntimeConfig:
    provider_id: str
    transport_mode: str
    discovery_mode: str
    detail_mode: str
    auth_mode: str
    user_agent: str
    headers: dict[str, str]
    cookies: dict[str, str]
    tokens: dict[str, str]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_from_age(age_min: int | None) -> str | None:
    if age_min is None:
        return None
    return (_utc_now() - timedelta(minutes=age_min)).isoformat()


def _extract_otp(text: str) -> str | None:
    match = OTP_PATTERN.search(text)
    return match.group(1) if match else None


def _message_hash(sender: str | None, body: str, received_at: str | None) -> str:
    payload = f"{sender or ''}|{body}|{received_at or ''}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _normalize_public_number_key(value: str | None) -> str | None:
    digits = re.sub(r"\D+", "", value or "")
    if len(digits) < 8:
        return None
    return digits


def _json_object(text: str | None) -> dict[str, str]:
    raw = (text or "").strip()
    if not raw:
        return {}
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        return {}
    return {str(key): str(value) for key, value in parsed.items()}


def _load_provider_runtime(database: Database, provider_id: str) -> ProviderRuntimeConfig:
    with database.connection() as conn:
        row = conn.execute(
            """
            SELECT
                p.id AS provider_id,
                p.transport_mode,
                p.discovery_mode,
                p.detail_mode,
                p.auth_mode,
                pc.user_agent,
                pc.headers_json,
                pc.cookies_json,
                pc.tokens_json
            FROM providers p
            JOIN provider_configs pc ON pc.provider_id = p.id
            WHERE p.id = ?
            """,
            (provider_id,),
        ).fetchone()
    if not row:
        raise ValueError(f"provider not found: {provider_id}")
    return ProviderRuntimeConfig(
        provider_id=row["provider_id"],
        transport_mode=row["transport_mode"],
        discovery_mode=row["discovery_mode"],
        detail_mode=row["detail_mode"],
        auth_mode=row["auth_mode"],
        user_agent=row["user_agent"] or provider_probe.UA,
        headers=_json_object(row["headers_json"]),
        cookies=_json_object(row["cookies_json"]),
        tokens=_json_object(row["tokens_json"]),
    )


def _build_direct_headers(runtime: ProviderRuntimeConfig) -> dict[str, str]:
    headers = {
        "User-Agent": runtime.user_agent or provider_probe.UA,
        "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
    }
    headers.update(runtime.headers)
    headers.update(runtime.tokens)
    return headers


def _build_flaresolverr_cookies(runtime: ProviderRuntimeConfig, url: str) -> list[dict[str, Any]]:
    if not runtime.cookies:
        return []
    hostname = urlparse(url).hostname or ""
    return [
        {
            "name": name,
            "value": value,
            "domain": hostname,
            "path": "/",
        }
        for name, value in runtime.cookies.items()
    ]


def _build_html_entries(
    discovery_url: str,
    html: str,
    link_patterns: list[re.Pattern[str]],
    limit: int,
) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    links = provider_probe.extract_links(discovery_url, html, link_patterns)
    entries: list[dict[str, Any]] = []
    for url in links[:limit]:
        label = None
        for anchor in soup.find_all("a", href=True):
            href = anchor.get("href")
            full = provider_probe.urljoin(discovery_url, href)
            if full == url:
                label = " ".join(anchor.get_text(" ", strip=True).split())
                break
        entries.append({"detail_url": url, "discovery_url": discovery_url, "label": label})
    return entries


def _discover_quackr_entries_from_payload(payload: Any, limit: int) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        return []
    entries: list[dict[str, Any]] = []
    for item in payload[:limit]:
        if not isinstance(item, dict):
            continue
        number_key = _normalize_public_number_key(str(item.get("number") or "").strip())
        if not number_key:
            continue
        locale = str(item.get("locale") or "").strip() or None
        entries.append(
            {
                "detail_url": f"https://quackr.io/number/{number_key}",
                "discovery_url": "https://quackr.io/numbers.json",
                "label": locale,
                "number_key": number_key,
                "country_name": locale,
                "restricted": True,
                "restricted_reason": "message_api_verification_required",
            }
        )
    return entries


def _postprocess_discovered_entries(provider_id: str, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if provider_id != "jiemahao":
        return entries
    normalized_entries: list[dict[str, Any]] = []
    for entry in entries:
        number_key = _normalize_public_number_key(entry.get("label"))
        if not number_key:
            continue
        normalized_entry = dict(entry)
        normalized_entry["number_key"] = number_key
        normalized_entries.append(normalized_entry)
    return normalized_entries


def _start_fetch_run(database: Database, provider_id: str, run_type: str = "sync") -> int:
    with database.connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO fetch_runs (
                provider_id, run_type, status, started_at, evidence_json
            ) VALUES (?, ?, 'running', ?, '{}')
            """,
            (provider_id, run_type, _utc_now().isoformat()),
        )
        return int(cursor.lastrowid)


def _finish_fetch_run(
    database: Database,
    run_id: int,
    *,
    status: str,
    numbers_seen: int,
    messages_seen: int,
    error_summary: str = "",
    evidence: dict[str, Any] | None = None,
) -> None:
    with database.connection() as conn:
        conn.execute(
            """
            UPDATE fetch_runs
            SET status = ?, finished_at = ?, numbers_seen = ?, messages_seen = ?,
                error_summary = ?, evidence_json = ?
            WHERE id = ?
            """,
            (
                status,
                _utc_now().isoformat(),
                numbers_seen,
                messages_seen,
                error_summary.strip() or None,
                json.dumps(evidence or {}, ensure_ascii=False),
                run_id,
            ),
        )


def _provider_ids_to_sync(database: Database, provider_id: str | None = None) -> list[str]:
    with database.connection() as conn:
        if provider_id:
            rows = conn.execute(
                "SELECT id FROM providers WHERE id = ?",
                (provider_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id FROM providers WHERE enabled = 1 ORDER BY priority ASC"
            ).fetchall()
    return [row["id"] for row in rows]


def _extract_number_key(provider_id: str, detail_url: str) -> str | None:
    patterns = {
        "receive_smss": re.compile(r"/sms/(\d+)/"),
        "temp_number": re.compile(r"/temporary-numbers/.+?/(\d+)$"),
        "sms24": re.compile(r"/en/numbers/(\d+)$"),
        "smstome": re.compile(r"/phone/(\d+)/sms/\d+$"),
        "freephonenum": re.compile(r"/receive-sms/(\d+)$"),
        "receive_sms_free_cc": re.compile(r"Phone-Number/(\d+)/$"),
        "temporary_phone_number": re.compile(r"/Phone-Number/(\d+)$"),
    }
    if provider_id == "jiemahao":
        query = parse_qs(urlparse(detail_url).query)
        values = query.get("phone")
        return _normalize_public_number_key(values[0] if values else None)
    pattern = patterns.get(provider_id)
    if not pattern:
        return None
    match = pattern.search(detail_url)
    return match.group(1) if match else None


def _e164_from_key(number_key: str | None) -> str | None:
    digits = _normalize_public_number_key(number_key)
    return f"+{digits}" if digits else None


def _extract_country_name(provider_id: str, detail_url: str, html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    text = " ".join(soup.get_text(" ", strip=True).split())
    if provider_id == "receive_smss":
        match = re.search(r"Click to Copy\s+(.+?)\s+mobile phone number", text)
        return match.group(1) if match else None
    if provider_id == "temp_number":
        match = re.search(r"Free\s+(.+?)\s+Temporary Phone Number", text)
        return match.group(1) if match else "United Kingdom"
    if provider_id == "sms24":
        for heading in soup.find_all(["h1", "h2"]):
            heading_text = " ".join(heading.get_text(" ", strip=True).split())
            match = re.search(r"^(.+?)\s+Phone Number\s+\+\d+", heading_text)
            if match:
                return match.group(1)
        return "United States"
    if provider_id == "smstome":
        match = re.search(r"Phone Number in\s+(.+?)\s+\+", text)
        return match.group(1) if match else "Netherlands"
    if provider_id == "receive_sms_free_cc":
        match = re.search(r"/Free-(.+?)-Phone-Number/", detail_url)
        if match:
            name = match.group(1).replace("-", " ")
            return {"USA": "United States"}.get(name, name)
    if provider_id == "temporary_phone_number":
        match = re.search(r"Receive SMS online\s+(.+?)\s+.+?phone number for verification code", text)
        return match.group(1) if match else None
    if provider_id == "jiemahao":
        match = re.search(r"号码归属地:\s*([^\s]+)", text)
        return match.group(1) if match else None
    return None


def _discover_entries(
    provider_id: str,
    settings: Settings,
    runtime: ProviderRuntimeConfig,
    limit: int,
) -> list[dict[str, Any]]:
    probe_config = provider_probe.PROVIDERS.get(provider_id)
    if provider_id == "quackr":
        with provider_probe.httpx.Client(
            timeout=30.0,
            headers=_build_direct_headers(runtime),
            follow_redirects=True,
        ) as client:
            if runtime.cookies:
                client.cookies.update(runtime.cookies)
            response = client.get("https://quackr.io/numbers.json")
            response.raise_for_status()
            return _discover_quackr_entries_from_payload(response.json(), limit)

    if not probe_config:
        return []
    if probe_config.seed_details and (not probe_config.discovery_url or not probe_config.link_patterns):
        return [{"detail_url": url, "discovery_url": None, "label": None} for url in probe_config.seed_details[:limit]]
    if not probe_config.discovery_url or not probe_config.link_patterns:
        return []

    entries: list[dict[str, Any]] = []
    if runtime.transport_mode == "flaresolverr":
        with provider_probe.httpx.Client(timeout=90.0) as client:
            session = f"sync-{provider_id}-{provider_probe.uuid.uuid4().hex[:8]}"
            client.post(
                settings.flaresolverr_url,
                json={"cmd": "sessions.create", "session": session},
                timeout=90.0,
            )
            try:
                html, _ = provider_probe.flare_get(
                    client,
                    session,
                    probe_config.discovery_url,
                    cookies=_build_flaresolverr_cookies(runtime, probe_config.discovery_url),
                    flaresolverr_url=settings.flaresolverr_url,
                )
                entries.extend(
                    _build_html_entries(
                        probe_config.discovery_url,
                        html,
                        probe_config.link_patterns,
                        limit,
                    )
                )
            finally:
                client.post(
                    settings.flaresolverr_url,
                    json={"cmd": "sessions.destroy", "session": session},
                    timeout=90.0,
                )
        return _postprocess_discovered_entries(provider_id, entries)

    with provider_probe.httpx.Client() as client:
        html, _ = provider_probe.direct_get(
            client,
            probe_config.discovery_url,
            headers=_build_direct_headers(runtime),
            cookies=runtime.cookies,
        )
        entries.extend(
            _build_html_entries(
                probe_config.discovery_url,
                html,
                probe_config.link_patterns,
                limit,
            )
        )
    return _postprocess_discovered_entries(provider_id, entries)


def _fetch_detail_html(
    provider_id: str,
    detail_url: str,
    runtime: ProviderRuntimeConfig,
    settings: Settings,
) -> str:
    if runtime.transport_mode == "flaresolverr":
        with provider_probe.httpx.Client(timeout=90.0) as client:
            session = f"detail-{provider_id}-{provider_probe.uuid.uuid4().hex[:8]}"
            client.post(
                settings.flaresolverr_url,
                json={"cmd": "sessions.create", "session": session},
                timeout=90.0,
            )
            try:
                html, _ = provider_probe.flare_get(
                    client,
                    session,
                    detail_url,
                    cookies=_build_flaresolverr_cookies(runtime, detail_url),
                    flaresolverr_url=settings.flaresolverr_url,
                )
                return html
            finally:
                client.post(
                    settings.flaresolverr_url,
                    json={"cmd": "sessions.destroy", "session": session},
                    timeout=90.0,
                )

    with provider_probe.httpx.Client() as client:
        html, _ = provider_probe.direct_get(
            client,
            detail_url,
            headers=_build_direct_headers(runtime),
            cookies=runtime.cookies,
        )
        return html


def _parse_messages_receive_smss(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict[str, Any]] = []
    for node in soup.select(".message_details"):
        text = " ".join(node.get_text(" ", strip=True).split())
        match = re.match(r"^Message\s+(.*?)\s+Sender\s+(.*?)\s+Time\s+(.+)$", text)
        if not match:
            continue
        body, sender, age_text = match.groups()
        age = provider_probe.age_from_text(age_text)
        rows.append(
            {
                "sender": sender,
                "body": body,
                "age_min": age,
                "received_at": _iso_from_age(age),
                "otp_code": _extract_otp(body),
            }
        )
    return rows


def _parse_messages_sms24(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict[str, Any]] = []
    for dt, dd in zip(soup.select("#sms_msg dt"), soup.select("#sms_msg dd")):
        age_text = " ".join(dt.get_text(" ", strip=True).split())
        dd_text = " ".join(dd.get_text(" ", strip=True).split())
        sender = None
        body = dd_text
        if dd_text.startswith("From:"):
            rest = dd_text[5:].strip()
            if " " in rest:
                sender, body = rest.split(" ", 1)
            else:
                sender = rest
                body = ""
        age = provider_probe.age_from_text(age_text)
        rows.append(
            {
                "sender": sender,
                "body": body.strip(),
                "age_min": age,
                "received_at": _iso_from_age(age),
                "otp_code": _extract_otp(body),
            }
        )
    return rows


def _parse_messages_smstome(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict[str, Any]] = []
    for tr in soup.select("table tr"):
        cells = tr.find_all("td")
        if len(cells) != 3:
            continue
        sender = " ".join(cells[0].get_text(" ", strip=True).split())
        age_text = " ".join(cells[1].get_text(" ", strip=True).split())
        body = " ".join(cells[2].get_text(" ", strip=True).split())
        if not sender or not body:
            continue
        age = provider_probe.age_from_text(age_text)
        rows.append(
            {
                "sender": sender,
                "body": body,
                "age_min": age,
                "received_at": _iso_from_age(age),
                "otp_code": _extract_otp(body),
            }
        )
    return rows


def _parse_messages_temp_number(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict[str, Any]] = []
    for article in soup.select("article"):
        from_node = article.select_one(".msg-from")
        time_node = article.select_one(".msg-time")
        body_node = article.select_one(".msg-body")
        if not from_node or not time_node or not body_node:
            continue
        sender = " ".join(from_node.get_text(" ", strip=True).split())
        age_text = " ".join(time_node.get_text(" ", strip=True).split())
        body = " ".join(body_node.get_text(" ", strip=True).split())
        age = provider_probe.age_from_text(age_text)
        rows.append(
            {
                "sender": sender,
                "body": body,
                "age_min": age,
                "received_at": _iso_from_age(age),
                "otp_code": _extract_otp(body),
            }
        )
    return rows


def _parse_messages_receive_sms_free_cc(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict[str, Any]] = []
    for node in soup.select(".sms-item"):
        text = " ".join(node.get_text(" ", strip=True).split())
        match = AGE_PATTERN.search(text)
        if not match:
            continue
        age_text = match.group(1)
        prefix = text[: match.start()].strip()
        body = text[match.end() :].strip()
        sender = prefix or None
        age = provider_probe.age_from_text(age_text)
        rows.append(
            {
                "sender": sender,
                "body": body,
                "age_min": age,
                "received_at": _iso_from_age(age),
                "otp_code": _extract_otp(body),
            }
        )
    return rows


def _parse_messages(provider_id: str, html: str) -> list[dict[str, Any]]:
    parsers = {
        "receive_smss": _parse_messages_receive_smss,
        "sms24": _parse_messages_sms24,
        "smstome": _parse_messages_smstome,
        "temp_number": _parse_messages_temp_number,
        "receive_sms_free_cc": _parse_messages_receive_sms_free_cc,
    }
    parser = parsers.get(provider_id)
    return parser(html) if parser else []


class SyncService:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings

    def sync_enabled_providers(
        self,
        provider_id: str | None = None,
        limit_per_provider: int | None = None,
    ) -> list[SyncResult]:
        results: list[SyncResult] = []
        for current_provider_id in _provider_ids_to_sync(self.database, provider_id):
            results.append(self.sync_provider(current_provider_id, limit_per_provider=limit_per_provider))
        return results

    def sync_provider(self, provider_id: str, limit_per_provider: int | None = None) -> SyncResult:
        synced_count = 0
        message_count = 0
        entries: list[dict[str, Any]] = []
        run_id = _start_fetch_run(self.database, provider_id)
        try:
            runtime = _load_provider_runtime(self.database, provider_id)
            sync_limit = max(1, int(limit_per_provider or self.settings.sync_limit_per_provider))
            entries = _discover_entries(
                provider_id,
                self.settings,
                runtime,
                sync_limit,
            )
            with self.database.connection() as conn:
                for entry in entries:
                    detail_url = entry["detail_url"]
                    html = ""
                    detail: dict[str, Any]
                    if provider_id == "quackr":
                        detail = {
                            "latest_age_min": None,
                            "restricted": bool(entry.get("restricted", True)),
                            "restricted_reason": entry.get("restricted_reason"),
                            "evidence": [entry.get("label") or "numbers.json discovery"],
                        }
                    else:
                        html = _fetch_detail_html(provider_id, detail_url, runtime, self.settings)
                        detail = provider_probe.parse_detail(provider_id, html)
                    number_key = entry.get("number_key") or _extract_number_key(provider_id, detail_url)
                    e164 = _e164_from_key(number_key)
                    if not number_key or not e164:
                        continue

                    country_name = entry.get("country_name") or _extract_country_name(provider_id, detail_url, html)
                    age_min = detail.get("latest_age_min")
                    restricted = bool(detail.get("restricted"))
                    restricted_reason = detail.get("restricted_reason")
                    now_iso = _utc_now().isoformat()
                    bucket = freshness_bucket(age_min, self.settings)
                    status = number_status_from_age(age_min, restricted, self.settings)
                    score = activity_score_from_age(age_min, restricted)

                    conn.execute(
                        """
                        INSERT INTO numbers (
                            e164, country_name, national_number, status, activity_score,
                            first_seen_at, last_seen_at, last_message_at, last_message_age_min,
                            freshness_bucket
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(e164) DO UPDATE SET
                            country_name = COALESCE(excluded.country_name, numbers.country_name),
                            national_number = COALESCE(excluded.national_number, numbers.national_number),
                            status = CASE
                                WHEN COALESCE(TRIM(numbers.blacklist_reason), '') <> '' THEN 'blacklisted'
                                ELSE excluded.status
                            END,
                            activity_score = excluded.activity_score,
                            last_seen_at = excluded.last_seen_at,
                            last_message_at = COALESCE(excluded.last_message_at, numbers.last_message_at),
                            last_message_age_min = excluded.last_message_age_min,
                            freshness_bucket = excluded.freshness_bucket
                        """,
                        (
                            e164,
                            country_name,
                            number_key,
                            status,
                            score,
                            now_iso,
                            now_iso,
                            _iso_from_age(age_min),
                            age_min,
                            bucket,
                        ),
                    )
                    number_id = conn.execute("SELECT id FROM numbers WHERE e164 = ?", (e164,)).fetchone()["id"]

                    conn.execute(
                        """
                        INSERT INTO number_sources (
                            number_id, provider_id, provider_number_key, detail_url, discovery_url,
                            provider_country_label, source_status, restricted, restricted_reason,
                            last_seen_at, last_checked_at, last_success_at, last_real_message_at,
                            last_real_message_age_min, raw_snapshot_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(provider_id, provider_number_key) DO UPDATE SET
                            number_id = excluded.number_id,
                            detail_url = excluded.detail_url,
                            discovery_url = excluded.discovery_url,
                            provider_country_label = COALESCE(
                                excluded.provider_country_label,
                                number_sources.provider_country_label
                            ),
                            source_status = excluded.source_status,
                            restricted = excluded.restricted,
                            restricted_reason = excluded.restricted_reason,
                            last_seen_at = excluded.last_seen_at,
                            last_checked_at = excluded.last_checked_at,
                            last_success_at = excluded.last_success_at,
                            last_real_message_at = excluded.last_real_message_at,
                            last_real_message_age_min = excluded.last_real_message_age_min,
                            raw_snapshot_json = excluded.raw_snapshot_json
                        """,
                        (
                            number_id,
                            provider_id,
                            number_key,
                            detail_url,
                            entry.get("discovery_url"),
                            country_name,
                            "restricted" if restricted else "open",
                            1 if restricted else 0,
                            restricted_reason,
                            now_iso,
                            now_iso,
                            now_iso,
                            _iso_from_age(age_min),
                            age_min,
                            json.dumps(
                                {
                                    "evidence": detail.get("evidence", []),
                                    "label": entry.get("label"),
                                },
                                ensure_ascii=False,
                            ),
                        ),
                    )
                    source_id = conn.execute(
                        """
                        SELECT id FROM number_sources
                        WHERE provider_id = ? AND provider_number_key = ?
                        """,
                        (provider_id, number_key),
                    ).fetchone()["id"]

                    for message in _parse_messages(provider_id, html):
                        conn.execute(
                            """
                            INSERT INTO messages (
                                number_id, source_id, sender, body, received_at, observed_at,
                                otp_code, raw_payload_json, dedupe_hash
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(source_id, dedupe_hash) DO NOTHING
                            """,
                            (
                                number_id,
                                source_id,
                                message.get("sender"),
                                message["body"],
                                message.get("received_at"),
                                now_iso,
                                message.get("otp_code"),
                                json.dumps(message, ensure_ascii=False),
                                _message_hash(
                                    message.get("sender"),
                                    message["body"],
                                    message.get("received_at"),
                                ),
                            ),
                        )
                        message_count += 1

                    synced_count += 1
                    self._recalculate_number(conn, number_id)

            result = SyncResult(
                provider_id=provider_id,
                discovered_count=len(entries),
                synced_count=synced_count,
                message_count=message_count,
            )
            _finish_fetch_run(
                self.database,
                run_id,
                status="success",
                numbers_seen=synced_count,
                messages_seen=message_count,
                evidence={"discovered_count": len(entries)},
            )
            logger.info(
                "sync_provider_success provider_id={} discovered={} synced={} messages={}",
                provider_id,
                len(entries),
                synced_count,
                message_count,
            )
            with self.database.connection() as conn:
                conn.execute(
                    """
                    UPDATE provider_configs
                    SET last_verified_at = ?, last_verify_status = 'success', last_verify_error = ''
                    WHERE provider_id = ?
                    """,
                    (_utc_now().isoformat(), provider_id),
                )
            return result
        except Exception as exc:  # noqa: BLE001
            error_text = str(exc)
            _finish_fetch_run(
                self.database,
                run_id,
                status="failed",
                numbers_seen=synced_count,
                messages_seen=message_count,
                error_summary=error_text,
                evidence={"discovered_count": len(entries)},
            )
            logger.exception("sync_provider_failed provider_id={}", provider_id)
            with self.database.connection() as conn:
                conn.execute(
                    """
                    UPDATE provider_configs
                    SET last_verified_at = ?, last_verify_status = 'failed', last_verify_error = ?
                    WHERE provider_id = ?
                    """,
                    (_utc_now().isoformat(), error_text, provider_id),
                )
            return SyncResult(
                provider_id=provider_id,
                discovered_count=len(entries),
                synced_count=synced_count,
                message_count=message_count,
                error=error_text,
            )

    def _recalculate_number(self, conn: Any, number_id: int) -> None:
        row = conn.execute(
            """
            SELECT
                MIN(CASE WHEN restricted = 0 THEN last_real_message_age_min END) AS best_age,
                MAX(CASE WHEN restricted = 0 THEN last_real_message_at END) AS last_message_at
            FROM number_sources
            WHERE number_id = ?
            """,
            (number_id,),
        ).fetchone()
        age_min = row["best_age"]
        bucket = freshness_bucket(age_min, self.settings)
        status = number_status_from_age(age_min, False, self.settings)
        score = activity_score_from_age(age_min, False)
        conn.execute(
            """
            UPDATE numbers
            SET status = CASE
                    WHEN COALESCE(TRIM(blacklist_reason), '') <> '' THEN 'blacklisted'
                    ELSE ?
                END,
                activity_score = ?, last_message_at = ?,
                last_message_age_min = ?, freshness_bucket = ?
            WHERE id = ?
            """,
            (status, score, row["last_message_at"], age_min, bucket, number_id),
        )
