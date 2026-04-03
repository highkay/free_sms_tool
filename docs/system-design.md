# Free SMS Pool App Design

Verified against live targets on 2026-03-30.

## Current Implementation Status

As of 2026-03-31, the current repository implements:

- SQLite-backed number pool, source evidence, recent message storage
- per-app state, global blacklist, and short-lived claims
- provider config management for headers / cookies / tokens
- API key-protected JSON APIs under `/api/*`
- local FastAPI web UI pages for dashboard, numbers, auth, apps, claims, and providers
- `uv`-managed project setup, `.env`-driven config, and `loguru` logging

See also:

- `README.md`
- `docs/api.md`
- `docs/provider-freshness-validation.md`

## 1. Goal

Build a Python web app with:

- FastAPI for the backend
- HTMX + DaisyUI for the UI
- `httpx` for provider fetching
- SQLite for persistence

The app must:

- discover public phone numbers from supported free SMS sites
- normalize and merge them into a local number pool
- score which numbers look active and still usable
- select a number randomly or by filters such as country/provider/app
- fetch recent SMS for a selected number
- support global blacklist, used, and app-specific used/blocked states
- keep evidence, not guesses, for why a number is considered usable or not

## 2. Hard Constraints Learned From Live Verification

The supported sites do not behave uniformly. The architecture must not assume "one provider = one static HTML parser".

### Provider matrix

| Provider | Discovery | Message fetch | Live result on 2026-03-30 | Anti-bot / auth | Recommended support level |
| --- | --- | --- | --- | --- | --- |
| `freephonenum.com` | Country pages like `/us` | Detail pages like `/us/receive-sms/6203221059` | Browser-like fetch showed public HTML; local `httpx` got `403` from Cloudflare | Likely needs `cf_clearance` + matching UA | Phase 2 |
| `quackr.io` | Public `https://quackr.io/numbers.json` | `GET /api/messages/{number}?limit=&timeFilter=` | Number list works via plain `httpx`; message API returned `403 {"error":"Verification failed"}` | Requires Turnstile token in `x-turnstile-token` | Phase 1 for discovery, Phase 3 for messages |
| `smsreceivefree.com` | Unverified | Unverified | `https` returned SSL EOF, `http` returned `502` from current network | Site currently unstable or misconfigured | Disabled until re-test |
| `smstome.com` | Country pages like `/country/netherlands` | Detail pages like `/netherlands/phone/.../sms/...` | Browser-like fetch showed public HTML; local `httpx` got `403` | Likely Cloudflare clearance cookie | Phase 2 |
| `receive-smss.com` | Homepage and `/sms/{number}/` | Same detail page | Full plain `httpx` success; homepage exposed 41 open numbers across 12 countries in current sample | No login required for public pages | Phase 1 anchor provider |
| `temporary-phone-number.com` | Country/number HTML pages | Same detail page | Browser-like fetch showed public HTML; local `httpx` got `403` | Likely Cloudflare clearance cookie | Phase 2 |
| `temp-number.com` | Country pages like `/countries/united-kingdom` | Detail pages like `/temporary-numbers/.../...` | Browser-like fetch showed public HTML; local `httpx` got `403` | Likely Cloudflare clearance cookie | Phase 2 |
| `www.receivesms.org` | Discovery currently weak | Detail pages are public once route is known | Homepage and sitemap did not expose active numbers cleanly; `/active-numbers/` had no usable entries; direct detail pages still work | No hard block seen, but poor discoverability | Phase 3 detail-only unless seed list exists |
| `receive-sms-free.cc` | Homepage/country HTML | Same detail page | Browser-like fetch showed public HTML; local `httpx` got `403` | Likely Cloudflare clearance cookie | Phase 2 |
| `jiemahao.com` | Listing page `/phone-numbers/` | Detail page pattern `/sms/?phone=...` | Browser-like listing works, but live list contains garbage rows like `+100` and `1970-01-01`; message retrieval flow not fully verified | Local `httpx` got `403`; likely Cloudflare plus JS quirks | Phase 3 |
| `oksms.org` | Homepage and `/receive/...` pages | Same detail page | Browser-like fetch showed public HTML; local `httpx` got `403` | Likely Cloudflare clearance cookie | Phase 2 |
| `sms24.me` | Country pages like `/en/countries/us` | Detail pages like `/en/numbers/...` | Browser-like fetch showed public HTML; local `httpx` got `403` | Likely Cloudflare clearance cookie | Phase 2 |

### Re-validation through local FlareSolverr on `127.0.0.1:8191`

Using local FlareSolverr `3.4.6`, most of the previously blocked Cloudflare sites became fetchable as real HTML pages, including their public number lists and detail pages.

| Provider | FlareSolverr result on 2026-03-30 | Design impact |
| --- | --- | --- |
| `freephonenum.com` | Homepage and detail page fetched successfully with `cf_clearance`, `laravel_session`, `XSRF-TOKEN` | Can be supported via browser-assisted transport |
| `quackr.io` | HTML pages fetched successfully, but `/api/messages/{number}` still returned `{"error":"Verification failed"}` even inside the solved session | Discovery can use `numbers.json`; message fetch still needs provider-specific verification handling |
| `receive-smss.com` | Still works directly; FlareSolverr also works | Keep as plain-HTTP anchor provider |
| `smstome.com` | Country page and detail page fetched successfully | Good candidate for FlareSolverr-backed HTML provider |
| `temporary-phone-number.com` | Homepage and detail page fetched successfully | Good candidate for FlareSolverr-backed HTML provider |
| `temp-number.com` | Country page and detail page fetched successfully | Good candidate for FlareSolverr-backed HTML provider |
| `receive-sms-free.cc` | Homepage and detail page fetched successfully | Good candidate for FlareSolverr-backed HTML provider |
| `jiemahao.com` | Listing and detail page fetched successfully, but dirty data remained | Still needs strong filtering before pool import |
| `sms24.me` | Country and detail page fetched successfully and detail page exposed SMS rows | Good candidate for FlareSolverr-backed HTML provider |
| `www.receivesms.org` | Still fetchable; discovery remains weak | No major change |
| `oksms.org` | FlareSolverr timed out on homepage and detail page | Keep disabled for now |
| `smsreceivefree.com` | FlareSolverr failed with `ERR_NAME_NOT_RESOLVED` | Keep disabled for now |

### Evidence worth designing around

1. `quackr.io` is not just HTML. It exposes `numbers.json` directly, but its message API is protected.
2. `receive-smss.com` is currently the cleanest end-to-end provider for a first working pipeline.
3. Several sites become visible in browser-like fetches but not via raw local `httpx`, so cookie/session injection is a first-class requirement.
4. Some providers include bad data:
   - `jiemahao.com` showed placeholder `+100` rows and bogus `1970-01-01` timestamps.
   - `freephonenum.com` showed rows marked `Register to view`.
5. Some pages duplicate or pollute message content:
   - `freephonenum.com` duplicated some rows on the live sample page.
   - `sms24.me`, `temp-number.com`, `temporary-phone-number.com`, and `receive-sms-free.cc` mix SMS rows with large SEO sections.
   - `receive-sms-free.cc` and same-family pages inject anti-JS fragments into page text; parser cleanup is required.

## 3. Design Principles

### 3.1 Evidence-first pool state

A number is not "available" because a site listed it. A number is only considered usable after storing evidence such as:

- last successful fetch time
- last provider HTTP status
- last message seen time
- current provider page state such as `Open`, `Offline`, `Register to view`
- app-specific usage outcomes

### 3.2 Canonical number, multiple sources

The same real phone number may appear on multiple mirror sites. The system must:

- normalize to E.164
- create one canonical number record
- attach multiple provider source rows to that number
- merge message observations without losing source provenance

### 3.3 Provider capability flags

Each provider adapter must declare:

- discovery mode: `html`, `json`, `seed_only`
- detail mode: `html`, `json_api`, `unsupported`
- auth mode: `none`, `cookie_clearance`, `turnstile_token`
- transport mode: `httpx`, `flaresolverr`
- status: `enabled`, `partial`, `disabled`

### 3.4 Separate collection from web UI

Do not make HTMX requests perform slow provider refreshes inline unless the scope is tiny. Use:

- one FastAPI web process
- one lightweight collector process or scheduled loop

Both share the same SQLite database.

## 4. Suggested Architecture

## 4.1 Runtime components

### Web app

- list/filter numbers
- claim a number for an app
- show number details and recent SMS
- manage provider cookies/tokens
- mark numbers as used / blacklisted / app-blocked

### Collector

- periodic discovery runs
- detail refresh runs
- health checks per provider
- short watch loop for claimed numbers
- scoring and retirement

### Shared libraries

- phone normalization
- OTP extraction
- provider session management
- HTML/JSON parsing helpers
- scoring rules

## 4.2 Suggested project layout

```text
app/
  main.py
  config.py
  web/
    routes/
      dashboard.py
      numbers.py
      providers.py
      apps.py
      claims.py
    templates/
    static/
  db/
    models.py
    session.py
    migrations/
    repositories/
  providers/
    base.py
    registry.py
    sessions.py
    common/
      html.py
      cleanup.py
      phone.py
    receive_smss.py
    quackr.py
    freephonenum.py
    smstome.py
    temporary_phone_number.py
    temp_number.py
    receive_sms_free_cc.py
    oksms.py
    sms24.py
    receivesms_org.py
    jiemahao.py
  services/
    discovery.py
    detail_refresh.py
    selection.py
    claims.py
    app_usage.py
    scoring.py
    sms_extract.py
  collector/
    main.py
    scheduler.py
tests/
  fixtures/
    providers/
  test_providers/
  test_services/
docs/
  system-design.md
```

## 5. Provider Adapter Contract

Each adapter should implement a narrow interface:

```python
class ProviderAdapter(Protocol):
    provider_id: str
    discovery_mode: DiscoveryMode
    detail_mode: DetailMode
    auth_mode: AuthMode

    async def discover_numbers(self, session: ProviderSession) -> list[DiscoveredNumber]: ...
    async def fetch_number_detail(self, session: ProviderSession, source: NumberSource) -> NumberDetail: ...
    async def fetch_messages(self, session: ProviderSession, source: NumberSource) -> list[RemoteMessage]: ...
    async def healthcheck(self, session: ProviderSession) -> ProviderHealth: ...
```

### Important adapter rules

1. `discover_numbers()` only returns what was actually observed.
2. `fetch_number_detail()` stores enough raw evidence to reproduce parser decisions.
3. `fetch_messages()` may return `requires_auth` instead of silently failing.
4. Parsers must be pure functions so live captures can become fixtures.

## 6. Provider Session and Cookie Design

Because many sites are Cloudflare-gated, session config must be explicit and per-provider.

### Execution transport

Each provider should choose one fetch transport:

- `httpx`: direct HTTP requests, preferred when the site is stable and unprotected
- `flaresolverr`: browser-assisted requests through local `http://127.0.0.1:8191/v1`

Recommended defaults after re-validation:

- `httpx`: `receive-smss.com`, `quackr.io` discovery, `www.receivesms.org`
- `flaresolverr`: `freephonenum.com`, `smstome.com`, `temporary-phone-number.com`, `temp-number.com`, `receive-sms-free.cc`, `jiemahao.com`, `sms24.me`
- disabled: `oksms.org`, `smsreceivefree.com`

### Store per provider

- `user_agent`
- `headers_json`
- `cookies_json`
- `token_json`
- `last_verified_at`
- `last_verify_status`

### Supported auth modes

#### `none`

Used by providers such as current `receive-smss.com` discovery/detail flow.

#### `cookie_clearance`

For providers where browser rendering works but raw `httpx` gets `403`.

Expected config shape:

```json
{
  "user_agent": "Mozilla/5.0 ... Chrome/123 ...",
  "headers": {
    "accept-language": "en-US,en;q=0.9,zh-CN;q=0.8"
  },
  "cookies": {
    "cf_clearance": "...",
    "__cf_bm": "..."
  }
}
```

#### `turnstile_token`

Needed for `quackr.io` message API. Keep it separate from normal cookies:

```json
{
  "cookies": {},
  "headers": {},
  "tokens": {
    "x-turnstile-token": "..."
  }
}
```

The app should not assume this token is long-lived. UI needs a "Test token" action.

## 7. SQLite Data Model

Use SQLite in WAL mode. Keep the schema simple but explicit.

## 7.1 Core tables

### `providers`

- `id` text primary key
- `name`
- `homepage_url`
- `discovery_mode`
- `detail_mode`
- `auth_mode`
- `enabled` bool
- `priority` int
- `notes`

### `provider_configs`

- `provider_id` primary key
- `user_agent`
- `headers_json`
- `cookies_json`
- `tokens_json`
- `last_verified_at`
- `last_verify_status`
- `last_verify_error`

### `numbers`

- `id` integer primary key
- `e164` text unique
- `country_code` text
- `country_name`
- `national_number`
- `status` enum: `candidate`, `active`, `stale`, `suspect`, `blocked`, `blacklisted`, `retired`
- `activity_score` real
- `first_seen_at`
- `last_seen_at`
- `last_message_at`
- `last_selected_at`
- `blacklist_reason`
- `notes`

### `number_sources`

- `id` integer primary key
- `number_id` foreign key
- `provider_id` foreign key
- `provider_number_key`
- `detail_url`
- `discovery_url`
- `provider_country_label`
- `provider_status_label`
- `source_status` enum: `discovered`, `open`, `offline`, `restricted`, `gone`, `error`
- `requires_auth` bool
- `first_seen_at`
- `last_seen_at`
- `last_checked_at`
- `last_success_at`
- `last_http_status`
- `last_error`
- `raw_snapshot_json`

Unique key:

- `(provider_id, provider_number_key)`

### `messages`

- `id` integer primary key
- `number_id` foreign key
- `source_id` foreign key
- `provider_message_key` nullable
- `sender`
- `body`
- `received_at`
- `observed_at`
- `otp_code`
- `service_hint`
- `language_hint`
- `raw_payload_json`
- `dedupe_hash`

Useful unique key:

- `(source_id, dedupe_hash)`

### `apps`

- `id` integer primary key
- `slug` text unique
- `name`
- `notes`

### `app_number_states`

- `id` integer primary key
- `app_id` foreign key
- `number_id` foreign key
- `status` enum: `available`, `claimed`, `used`, `success`, `blocked`, `blacklisted`, `ignore`
- `use_count`
- `last_result`
- `last_claimed_at`
- `last_used_at`
- `notes`

Unique key:

- `(app_id, number_id)`

### `claims`

- `id` integer primary key
- `claim_token` text unique
- `number_id` foreign key
- `app_id` nullable
- `requested_country`
- `requested_provider`
- `purpose`
- `status` enum: `watching`, `completed`, `released`, `expired`
- `created_at`
- `expires_at`
- `released_at`

### `fetch_runs`

- `id` integer primary key
- `provider_id`
- `run_type` enum: `healthcheck`, `discover`, `detail`, `messages`
- `status` enum: `success`, `partial`, `failed`
- `started_at`
- `finished_at`
- `http_status`
- `numbers_seen`
- `messages_seen`
- `error_summary`
- `evidence_json`

## 7.2 Optional but useful

### `number_tags`

For manual labels such as:

- `high_spam`
- `slow_delivery`
- `mirror_duplicate`
- `manual_seed`

## 8. Number State and Scoring

Avoid opaque ML-style scores. Use simple rule-based scoring so decisions are explainable.

### Recommended rules

Start with `activity_score = 0`, then:

- `+40` if a provider detail fetch succeeded in the last 6 hours
- `+25` if a message was seen in the last 24 hours
- `+10` if the source page explicitly says `Open` or equivalent
- `+10` if the number is seen on more than one provider
- `-20` if the latest provider result is `restricted` or `Register to view`
- `-30` if the page is empty or parsing failed 3 consecutive times
- `-40` if the provider now marks the number offline/gone
- `-100` if globally blacklisted

### Recommended status mapping

- `active`: score `>= 50`
- `candidate`: score `20..49`
- `stale`: score `< 20` with old evidence
- `suspect`: repeated parser/auth failures
- `blocked`: provider explicitly blocks access
- `blacklisted`: manual or automatic local ban
- `retired`: long dead / removed from all sources

## 9. Selection Rules

Selecting a number should filter in this order:

1. global status not in `blacklisted`, `retired`, `blocked`
2. app-specific state not in `used`, `blocked`, `blacklisted`
3. country match if requested
4. provider match if requested
5. latest real SMS is recent enough
   - default target: `<= 180 minutes`
   - weak fallback only: `181..360 minutes`
   - default exclude: `> 360 minutes`
6. activity score desc
7. random tie-break within top candidates

This avoids always picking the single hottest number, which public sites quickly burn.

See also `docs/provider-freshness-validation.md` for the validated provider ranking and freshness thresholds.

## 10. Message Parsing and OTP Extraction

## 10.1 Message normalization

For each raw message:

- trim whitespace
- collapse repeated spaces
- remove obvious anti-bot text injected outside the message block
- preserve original raw body in `raw_payload_json`

### Site-family cleanup needed

For `temp-number.com`, `temporary-phone-number.com`, and `receive-sms-free.cc`:

- stop parsing at the first non-message content block
- ignore SEO sections such as "How to use", "About", and country guides
- strip injected Cloudflare/onClick fragments

For `freephonenum.com`:

- dedupe repeated message rows on the same page

## 10.2 OTP extraction

Run a conservative extractor:

- 4-8 digit codes
- simple alphanumeric codes only if anchored by words like `code`, `otp`, `verification`, `PIN`
- keep the full body even if no OTP is extracted

## 10.3 Service hints

Infer `service_hint` from:

- sender name
- message keywords
- optional user-provided app context

Do not auto-mark app usage from this alone. Treat it as a hint.

## 11. Blacklist and Used-State Design

These states must be separate.

### Global blacklist

Use when:

- number repeatedly fails across all use cases
- number is obvious garbage
- number is abusive/high-risk
- provider page is permanently fake/offline

### App-specific used / blocked

Use when:

- a specific app says the number was already used
- a specific app rejects the number category
- the user successfully used the number once and wants to avoid reuse there

### Automatic transitions

- if a user marks "already registered in X", set `app_number_states.status = blocked`
- if a claim ends with successful OTP reception and confirmed use, set `used` or `success` for that app
- if a provider returns no page / offline repeatedly, reduce global score, but do not immediately global-blacklist

## 12. Refresh Strategy

## 12.1 Discovery schedule

- phase 1 providers: every 10-20 minutes
- cookie-gated providers: every 20-60 minutes
- disabled providers: only manual re-test

## 12.2 Detail refresh

- active numbers: every 30-60 minutes
- stale numbers: every 6-12 hours
- claimed numbers: every 15-30 seconds for a short watch window

## 12.3 Backoff

Per provider, exponentially back off on:

- `403`
- `429`
- repeated parser failures
- repeated empty-page results

## 13. HTMX UI Design

## 13.1 Pages

### Dashboard

- provider health cards
- counts by number state
- latest messages feed
- current claims

### Numbers

- filters: country, provider, status, app-state, last-message age
- sort: score, freshness, country
- actions: claim, refresh, mark used, blacklist, open source page

### Number detail

- canonical number info
- attached provider sources
- recent messages
- extracted OTPs
- activity evidence
- per-app usage history

### Providers

- enable/disable provider
- edit cookies, headers, tokens
- test connection
- show last verify result

### Apps

- create app label such as `telegram`, `openai`, `whatsapp`
- inspect numbers blocked/used for that app

### Claims

- active watch windows
- latest SMS per claim
- release / mark success / mark failed

## 14. Implementation Order

## Milestone 0: foundation

- project scaffold
- SQLite schema
- provider registry
- provider config UI
- fetch run logging

## Milestone 1: prove end-to-end with reliable providers

- implement `receive-smss.com`
- implement `quackr.io` discovery from `numbers.json`
- implement one FlareSolverr-backed HTML provider such as `sms24.me` or `smstome.com`
- implement number pool, scoring, claims, recent message view

Expected result:

- fully working number pool with at least one end-to-end provider
- Quackr numbers visible, even if message fetch is still blocked
- at least one Cloudflare-gated provider proven via local FlareSolverr

## Milestone 2: add more FlareSolverr-backed static HTML providers

- `freephonenum.com`
- `smstome.com`
- `temporary-phone-number.com`
- `temp-number.com`
- `receive-sms-free.cc`
- `oksms.org`
- `sms24.me`

Expected result:

- same parser framework, different CSS selectors/cleanup
- provider transport can switch between direct `httpx` and local FlareSolverr

## Milestone 3: hard cases

- `quackr.io` message API via short-lived Turnstile token path
- `receivesms.org` discovery strategy
- `jiemahao.com` with aggressive dirty-data filtering
- `smsreceivefree.com` only if the site becomes reachable again

## 15. Testing Strategy

Because these sites are unstable, tests must be fixture-first.

### Unit tests

- phone normalization
- OTP extraction
- status scoring
- parser cleanup rules

### Provider fixture tests

For each provider:

- save representative HTML or JSON fixtures
- test discovery parse
- test detail parse
- test message dedupe

Current repository status:

- fixture captures now exist for `receive_smss`, `temp_number`, `sms24`, `smstome`, `receive_sms_free_cc`, and `quackr`
- live optional smoke tests exist for `receive_smss`, `sms24`, and `quackr`

### Live smoke tests

Keep them optional and explicit, for example:

```bash
pytest -m live
```

Only run when cookies/tokens are configured.

## 16. Recommended First Build Cut

If the objective is the fastest useful system, the first practical build should be:

1. `receive-smss.com` full support
2. `quackr.io` discovery-only support
3. generic provider config/cookie infrastructure
4. number pool, claim flow, message feed, blacklist/app-state logic

That delivers a real usable product early, while keeping the architecture ready for the harder Cloudflare-gated providers.
