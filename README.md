# Free SMS Tool

[中文说明](README.zh-CN.md)

Evidence-first free SMS pool manager built with:

- FastAPI
- HTMX + DaisyUI
- SQLite
- `httpx`
- local FlareSolverr on `127.0.0.1:8191`
- `uv` for dependency management

## What It Does

- sync public numbers from validated free SMS providers into a local pool
- score number freshness from recent real SMS evidence
- manage global blacklist and per-app used/blocked state
- create short-lived claims to avoid double allocation
- auto-replenish the pool when default eligible inventory falls below the configured watermark
- expose a web UI and API key-protected JSON APIs
- manage provider auth config such as cookies / headers / tokens

## Quick Start

```bash
uv sync
```

Create a local `.env` based on `.env.example`.

Example:

```bash
cp .env.example .env
```

Important:

- set `BOOTSTRAP_API_KEY` before first start so `/api/*` is immediately usable
- make sure local FlareSolverr is running on `http://127.0.0.1:8191/v1`
- set `WEB_UI_USERNAME` and `WEB_UI_PASSWORD` if the Web UI should require Basic Auth

Run the web app:

```bash
uv run uvicorn app.main:app --reload
```

Run the collector in a second terminal:

```bash
uv run python -m app.collector.main
```

Local access:

- Web UI: `http://127.0.0.1:8000/`
- OpenAPI Docs: `http://127.0.0.1:8000/docs`
- Auth Keys UI: `http://127.0.0.1:8000/auth`

Docker Compose:

```bash
docker compose up -d --build
```

The compose stack starts:

- `app` on `http://127.0.0.1:${HOST_PORT:-18000}`
- `collector` for queued sync jobs
- an internal `flaresolverr` sidecar reachable from the app at `http://flaresolverr:8191/v1`

Automatic replenish:

- the collector now watches default eligible inventory and auto-queues a deeper sync when pool consumption crosses `AUTO_REPLENISH_CONSUMPTION_THRESHOLD`
- default app exhaustion also queues one replenish job when a target app has no eligible numbers left
- auto jobs are deduplicated and throttled by `AUTO_REPLENISH_COOLDOWN_SECONDS`

If local `8000` is already occupied, keep or override `HOST_PORT` in `.env`.

Useful Docker commands:

```bash
docker compose ps
docker compose logs -f app
docker compose logs -f collector
docker compose logs -f flaresolverr
docker compose down
```

For Docker Compose with default settings:

- Web UI: `http://127.0.0.1:18000/`
- OpenAPI Docs: `http://127.0.0.1:18000/docs`
- Auth Keys UI: `http://127.0.0.1:18000/auth`
- Health Check: `http://127.0.0.1:18000/healthz`

## JSON API Auth

All main JSON APIs under `/api/*` require an API key:

```http
Authorization: Bearer <your-api-key>
```

The initial bootstrap key is seeded from `BOOTSTRAP_API_KEY`.

Use it to create managed keys:

- `GET /api/auth/keys`
- `POST /api/auth/keys`
- `POST /api/auth/keys/{key_id}/revoke`

## Main API Groups

- `auth`: API key management
- `providers`: list/update provider configs and queue sync jobs
- `apps`: manage app labels
- `numbers`: list/pick/inspect/blacklist/set app state
- `claims`: create/release/complete short-lived number claims
- `sync`: queue enabled-provider sync
- `jobs`: inspect queued/running/completed jobs

## Common Workflows

The examples below assume Docker Compose default access:

- `BASE_URL=http://127.0.0.1:18000`
- `API_KEY=<your-bootstrap-or-managed-api-key>`

### 1. Check service health and list providers

```bash
curl "$BASE_URL/healthz"
curl -H "Authorization: Bearer $API_KEY" "$BASE_URL/api/providers"
```

Use this first to confirm the app is reachable and the provider registry is seeded.

### 2. Load Cloudflare cookies for one provider and queue a sync job

```bash
curl \
  -X PUT \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  "$BASE_URL/api/providers/sms24/config" \
  -d '{
    "enabled": true,
    "priority": 30,
    "notes": "cloudflare cookies loaded from browser",
    "user_agent": "Mozilla/5.0",
    "headers": {
      "accept-language": "en-US,en;q=0.9"
    },
    "cookies": {
      "cf_clearance": "replace-me"
    },
    "tokens": {}
  }'

curl \
  -X POST \
  -H "Authorization: Bearer $API_KEY" \
  "$BASE_URL/api/providers/sms24/sync"
```

This now queues a background job. The collector process executes it asynchronously.

### 3. Pick one currently usable number for a target app

```bash
curl \
  -X POST \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  "$BASE_URL/api/numbers/pick" \
  -d '{
    "country_name": "United States",
    "provider_id": null,
    "app_slug": "openai",
    "include_cooling": false
  }'
```

This returns one eligible number after freshness filtering, blacklist filtering, and app-specific exclusion.

If the requested app has exhausted all currently eligible numbers, the app/API also asks the collector to auto-queue one deeper replenish sync.

### 4. Create a claim and read the latest SMS on that number

```bash
curl \
  -X POST \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  "$BASE_URL/api/claims" \
  -d '{
    "app_slug": "openai",
    "app_name": "OpenAI",
    "country_name": "United States",
    "provider_id": null,
    "purpose": "signup verification",
    "include_cooling": false,
    "ttl_minutes": 10
  }'
```

Take the returned `claim_token`, then inspect claim detail and recent SMS:

```bash
curl -H "Authorization: Bearer $API_KEY" "$BASE_URL/api/claims/<claim_token>"
curl -H "Authorization: Bearer $API_KEY" "$BASE_URL/api/claims/<claim_token>/messages"
```

This is the normal workflow for one registration or verification session.

### 5. Finish the claim, block it, or mark the number as bad

Mark one claim as successful:

```bash
curl \
  -X POST \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  "$BASE_URL/api/claims/<claim_token>/complete" \
  -d '{
    "result": "success"
  }'
```

Mark one claim as blocked:

```bash
curl \
  -X POST \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  "$BASE_URL/api/claims/<claim_token>/complete" \
  -d '{
    "result": "rate_limited",
    "app_state": "blocked"
  }'
```

Blacklist one number globally:

```bash
curl \
  -X POST \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  "$BASE_URL/api/numbers/<number_id>/blacklist" \
  -d '{
    "reason": "dead number or known bad target behavior"
  }'
```

Mark one number as already used for one app:

```bash
curl \
  -X POST \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  "$BASE_URL/api/numbers/<number_id>/app-state" \
  -d '{
    "app_slug": "openai",
    "app_name": "OpenAI",
    "status": "used",
    "notes": "already consumed in signup flow"
  }'
```

Use these actions to keep the pool clean instead of repeatedly allocating dead or already-consumed numbers.

## Main Web Pages

- `/numbers`: number pool and pick flow
- `/numbers/{id}`: number evidence page
- `/claims`: claim list and creation
- `/claims/{claim_token}`: claim detail and recent SMS
- `/apps`: app summaries
- `/apps/{slug}`: one app's number usage detail
- `/providers`: provider auth/config management
- `/auth`: API key management

Detailed examples: [docs/api.md](docs/api.md)

## Useful Commands

```bash
uv run python -m scripts.capture_provider_fixtures
uv run python -m compileall app scripts
uv run python -m app.collector.main --once
uv run pytest
uv run pytest tests/test_provider_fixtures.py
uv run pytest -m live
uv run ruff check .
docker compose up -d --build
docker compose ps
docker compose down
```

## Project Notes

- `receive_smss`, `temp_number`, `sms24`, `smstome` are the main default pool providers
- `quackr` is discovery-only for now because message fetch still needs extra verification
- provider auth config is stored in SQLite and can be edited in `/providers`
- claims automatically expire and release their app-level claimed state
- auto-replenish reuses the same `sync_providers` job type, but with a higher per-provider discovery limit and deduped cooldown
- provider fixture captures live under `tests/fixtures/providers`
- fixture tests currently cover 9 provider surfaces: 5 full message parsers, `quackr` discovery-only, and 3 detail-only/stale-or-restricted providers (`jiemahao`, `freephonenum`, `receivesms_org`)
