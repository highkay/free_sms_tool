from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

FLARESOLVERR_URL = os.getenv("FLARESOLVERR_URL", "http://127.0.0.1:8191/v1")
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
)


@dataclass
class ProviderConfig:
    mode: str
    sample_limit: int
    discovery_url: str | None = None
    seed_details: list[str] | None = None
    link_patterns: list[re.Pattern[str]] | None = None


PROVIDERS: dict[str, ProviderConfig] = {
    "receive_smss": ProviderConfig(
        mode="httpx",
        discovery_url="https://receive-smss.com/",
        link_patterns=[re.compile(r"^/sms/\d+/")],
        sample_limit=4,
    ),
    "freephonenum": ProviderConfig(
        mode="flaresolverr",
        discovery_url="https://freephonenum.com/us",
        link_patterns=[re.compile(r"^/us/receive-sms/\d+$"), re.compile(r"^us/receive-sms/\d+$")],
        sample_limit=4,
    ),
    "smstome": ProviderConfig(
        mode="flaresolverr",
        discovery_url="https://smstome.com/country/netherlands",
        link_patterns=[
            re.compile(r"^https://smstome\.com/.+/phone/\d+/sms/\d+$"),
            re.compile(r"^/.+/phone/\d+/sms/\d+$"),
        ],
        sample_limit=4,
    ),
    "temp_number": ProviderConfig(
        mode="flaresolverr",
        discovery_url="https://temp-number.com/countries/united-kingdom",
        link_patterns=[
            re.compile(r"^https://temp-number\.com/temporary-numbers/.+?/\d+$"),
            re.compile(r"^/temporary-numbers/.+?/\d+$"),
        ],
        sample_limit=4,
    ),
    "temporary_phone_number": ProviderConfig(
        mode="flaresolverr",
        discovery_url="https://temporary-phone-number.com/",
        link_patterns=[
            re.compile(r"^https://temporary-phone-number\.com/.+-Phone-Number/\d+$"),
            re.compile(r"^/.+-Phone-Number/\d+$"),
        ],
        sample_limit=4,
    ),
    "receive_sms_free_cc": ProviderConfig(
        mode="flaresolverr",
        discovery_url="https://receive-sms-free.cc/",
        link_patterns=[
            re.compile(r"^https://receive-sms-free\.cc/Free-.+-Phone-Number/\d+/$"),
            re.compile(r"^/Free-.+-Phone-Number/\d+/$"),
        ],
        sample_limit=4,
    ),
    "sms24": ProviderConfig(
        mode="flaresolverr",
        discovery_url="https://sms24.me/en/countries/us",
        link_patterns=[
            re.compile(r"^/en/numbers/\d+$"),
            re.compile(r"^https://sms24\.me/en/numbers/\d+$"),
        ],
        sample_limit=4,
    ),
    "jiemahao": ProviderConfig(
        mode="flaresolverr",
        discovery_url="https://jiemahao.com/phone-numbers/",
        link_patterns=[
            re.compile(r"^https://jiemahao\.com/sms/\?phone=\d+$"),
            re.compile(r"^/sms/\?phone=\d+$"),
        ],
        sample_limit=4,
    ),
    "receivesms_org": ProviderConfig(
        mode="httpx",
        seed_details=["https://www.receivesms.org/uk-phone-number/220/"],
        sample_limit=1,
    ),
}

RELATIVE_UNITS: list[tuple[re.Pattern[str], int]] = [
    (re.compile(r"(\d+)\s*minute[s]?\s+ago", re.I), 1),
    (re.compile(r"(\d+)\s*hour[s]?\s+ago", re.I), 60),
    (re.compile(r"(\d+)\s*day[s]?\s+ago", re.I), 1440),
    (re.compile(r"(\d+)\s*week[s]?\s+ago", re.I), 10080),
    (re.compile(r"(\d+)\s*month[s]?\s+ago", re.I), 43200),
    (re.compile(r"(\d+)\s*year[s]?\s+ago", re.I), 525600),
    (re.compile(r"(\d+)\s*分钟前"), 1),
    (re.compile(r"(\d+)\s*小?时前"), 60),
    (re.compile(r"(\d+)\s*天前"), 1440),
    (re.compile(r"(\d+)\s*周前"), 10080),
]


def age_from_text(text: str) -> int | None:
    s = " ".join(text.split()).replace("~", " ")
    if "just now" in s.lower():
        return 0
    for pat, mult in RELATIVE_UNITS:
        match = pat.search(s)
        if match:
            return int(match.group(1)) * mult
    return None


def is_number_link(href: str | None, patterns: list[re.Pattern[str]]) -> bool:
    return any(p.search(href or "") for p in patterns)


def flare_get(
    client: httpx.Client,
    session: str,
    url: str,
    *,
    cookies: list[dict[str, Any]] | None = None,
    wait_in_seconds: int | None = None,
    flaresolverr_url: str = FLARESOLVERR_URL,
) -> tuple[str, dict[str, Any]]:
    payload: dict[str, Any] = {
        "cmd": "request.get",
        "session": session,
        "url": url,
        "maxTimeout": 60000,
    }
    if cookies:
        payload["cookies"] = cookies
    if wait_in_seconds is not None:
        payload["waitInSeconds"] = wait_in_seconds
    response = client.post(
        flaresolverr_url,
        json=payload,
        timeout=90.0,
    )
    data = response.json()
    if data.get("status") != "ok":
        raise RuntimeError(data.get("message") or "flaresolverr error")
    solution = data.get("solution") or {}
    return solution.get("response") or "", solution


def direct_get(
    client: httpx.Client,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    cookies: dict[str, str] | None = None,
) -> tuple[str, dict[str, Any]]:
    request_headers = {
        "User-Agent": UA,
        "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
    }
    if headers:
        request_headers.update(headers)
    if cookies:
        client.cookies.clear()
        client.cookies.update(cookies)
    response = client.get(
        url,
        timeout=30.0,
        follow_redirects=True,
        headers=request_headers,
    )
    response.raise_for_status()
    return response.text, {"url": str(response.url), "status": response.status_code}


def extract_links(base_url: str, html: str, patterns: list[re.Pattern[str]]) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    seen: list[str] = []
    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href")
        full = urljoin(base_url, href)
        if is_number_link(full, patterns) or is_number_link(href, patterns):
            if full not in seen:
                seen.append(full)
    return seen


def _parse_age_candidates(snippets: list[str]) -> list[tuple[int, str]]:
    candidates: list[tuple[int, str]] = []
    for snippet in snippets:
        cleaned = " ".join(snippet.split())
        if not cleaned:
            continue
        age = age_from_text(cleaned)
        if age is None:
            continue
        lowered = cleaned.lower()
        if any(
            bad in lowered
            for bad in ["added:", "上线时间", "latest article", "latest free temporary phone numbers"]
        ):
            continue
        candidates.append((age, cleaned[:180]))
    candidates.sort(key=lambda item: item[0])
    return candidates


def parse_detail(provider: str, html: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    text = " ".join(soup.get_text(" ", strip=True).split())
    restricted = False
    restricted_reason: str | None = None

    if provider == "freephonenum" and "register to view" in text.lower():
        return {
            "latest_age_min": None,
            "evidence": ["register_to_view"],
            "restricted": True,
            "restricted_reason": "register_to_view",
        }

    if provider == "jiemahao":
        if "cf-turnstile" in html and "查看短信" in text:
            return {
                "latest_age_min": None,
                "evidence": ["turnstile_required_for_sms"],
                "restricted": True,
                "restricted_reason": "turnstile_required_for_sms",
            }
        meta_items = [
            " ".join(node.get_text(" ", strip=True).split())
            for node in soup.select(".article-meta .item")
        ]
        candidates = _parse_age_candidates(meta_items)
        return {
            "latest_age_min": candidates[0][0] if candidates else None,
            "evidence": candidates[:5] if candidates else meta_items[:3],
            "restricted": False,
            "restricted_reason": None,
        }

    if provider == "temporary_phone_number":
        direct_msgs = [
            " ".join(node.get_text(" ", strip=True).split())
            for node in soup.select(".direct-chat-msg")
        ]
        if any("register or login to the website before view sms" in msg.lower() for msg in direct_msgs):
            restricted = True
            restricted_reason = "login_required_for_sms"
        candidates = _parse_age_candidates(direct_msgs)
        if restricted:
            candidates = []
        return {
            "latest_age_min": candidates[0][0] if candidates else None,
            "evidence": candidates[:5] if candidates else direct_msgs[:3],
            "restricted": restricted,
            "restricted_reason": restricted_reason,
        }

    if provider == "receive_sms_free_cc":
        sms_items = [
            " ".join(node.get_text(" ", strip=True).split())
            for node in soup.select(".sms-item")
        ]
        candidates = _parse_age_candidates(sms_items)
        return {
            "latest_age_min": candidates[0][0] if candidates else None,
            "evidence": candidates[:5] if candidates else sms_items[:3],
            "restricted": False,
            "restricted_reason": None,
        }

    selector_sets = {
        "receive_smss": ["div", "p", "span"],
        "freephonenum": ["div", "tr", "td", "span"],
        "smstome": ["tr", "div", "li", "p"],
        "temp_number": ["li", "div", "tr", "dd", "dt"],
        "temporary_phone_number": ["tr", "div", "li", "td", "span"],
        "receive_sms_free_cc": ["div", "li", "tr", "dd", "dt"],
        "sms24": ["dt", "dd", "div", "li", "span"],
        "jiemahao": ["div", "li", "span", "td"],
        "receivesms_org": ["div", "li", "span", "td", "p"],
    }
    tags = selector_sets.get(provider, ["div", "li", "span", "td"])
    snippets: list[str] = []

    for tag in soup.find_all(tags):
        snippet = " ".join(tag.get_text(" ", strip=True).split())
        if not snippet:
            continue
        lowered = snippet.lower()
        if provider == "receivesms_org" and "sms received today: 0" in lowered:
            continue
        snippets.append(snippet)

    candidates = _parse_age_candidates(snippets)

    if not candidates and any(
        marker in text.lower()
        for marker in ["from:", "sender", "update messages", "received message", "verification", "验证码", "sms"]
    ):
        age = age_from_text(text)
        if age is not None:
            candidates.append((age, "page_fallback"))

    return {
        "latest_age_min": candidates[0][0] if candidates else None,
        "evidence": candidates[:5] if candidates else snippets[:3],
        "restricted": restricted,
        "restricted_reason": restricted_reason,
    }


def probe() -> dict[str, Any]:
    results: dict[str, Any] = {}
    flare_client = httpx.Client(timeout=90.0)
    direct_client = httpx.Client()

    for provider, cfg in PROVIDERS.items():
        print("=" * 120, flush=True)
        print("PROVIDER", provider, flush=True)
        provider_result: dict[str, Any] = {"detail_results": []}
        try:
            if cfg.mode == "flaresolverr":
                session = f"probe-{provider}-{uuid.uuid4().hex[:8]}"
                flare_client.post(
                    FLARESOLVERR_URL,
                    json={"cmd": "sessions.create", "session": session},
                    timeout=90.0,
                )
                assert cfg.discovery_url and cfg.link_patterns
                html, _ = flare_get(flare_client, session, cfg.discovery_url)
                detail_urls = extract_links(cfg.discovery_url, html, cfg.link_patterns)[: cfg.sample_limit]
                provider_result["discovery_url"] = cfg.discovery_url
                provider_result["discovered_count_sampled_from_page"] = len(detail_urls)
                print("discovered", len(detail_urls), flush=True)
                for url in detail_urls:
                    try:
                        detail_html, _ = flare_get(flare_client, session, url)
                        row = {"url": url, **parse_detail(provider, detail_html)}
                        provider_result["detail_results"].append(row)
                        print(
                            url,
                            "age_min=",
                            row.get("latest_age_min"),
                            "restricted=",
                            row.get("restricted"),
                            "evidence=",
                            row.get("evidence", [])[:2],
                            flush=True,
                        )
                    except Exception as exc:  # noqa: BLE001
                        row = {"url": url, "error": str(exc)}
                        provider_result["detail_results"].append(row)
                        print(url, "ERROR", exc, flush=True)
                flare_client.post(
                    FLARESOLVERR_URL,
                    json={"cmd": "sessions.destroy", "session": session},
                    timeout=90.0,
                )
            else:
                detail_urls = list(cfg.seed_details or [])
                if cfg.discovery_url and cfg.link_patterns:
                    html, _ = direct_get(direct_client, cfg.discovery_url)
                    detail_urls = extract_links(cfg.discovery_url, html, cfg.link_patterns)[: cfg.sample_limit]
                    provider_result["discovery_url"] = cfg.discovery_url
                    provider_result["discovered_count_sampled_from_page"] = len(detail_urls)
                for url in detail_urls:
                    try:
                        detail_html, _ = direct_get(direct_client, url)
                        row = {"url": url, **parse_detail(provider, detail_html)}
                        provider_result["detail_results"].append(row)
                        print(
                            url,
                            "age_min=",
                            row.get("latest_age_min"),
                            "restricted=",
                            row.get("restricted"),
                            "evidence=",
                            row.get("evidence", [])[:2],
                            flush=True,
                        )
                    except Exception as exc:  # noqa: BLE001
                        row = {"url": url, "error": str(exc)}
                        provider_result["detail_results"].append(row)
                        print(url, "ERROR", exc, flush=True)
        except Exception as exc:  # noqa: BLE001
            provider_result["error"] = str(exc)
            print("PROVIDER ERROR", exc, flush=True)

        fresh_60 = 0
        fresh_180 = 0
        fresh_360 = 0
        unrestricted_count = 0
        for row in provider_result["detail_results"]:
            if row.get("restricted"):
                continue
            unrestricted_count += 1
            age = row.get("latest_age_min")
            if age is None:
                continue
            if age <= 60:
                fresh_60 += 1
            if age <= 180:
                fresh_180 += 1
            if age <= 360:
                fresh_360 += 1
        provider_result["summary"] = {
            "sample_count": len(provider_result["detail_results"]),
            "unrestricted_count": unrestricted_count,
            "fresh_60m_count": fresh_60,
            "fresh_180m_count": fresh_180,
            "fresh_360m_count": fresh_360,
        }
        results[provider] = provider_result

    return results


if __name__ == "__main__":
    result = probe()
    print("JSON_RESULT_START")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("JSON_RESULT_END")
