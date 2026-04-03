# API Guide

All primary JSON APIs live under `/api`.

Authentication:

```http
Authorization: Bearer <api-key>
```

The first admin key is seeded from `BOOTSTRAP_API_KEY`.

## 1. Auth

### `GET /api/auth/keys`

List current API keys without exposing full token values.

### `POST /api/auth/keys`

Create a new API key.

Request:

```json
{
  "name": "automation",
  "notes": "CI / integration"
}
```

### `POST /api/auth/keys/{key_id}/revoke`

Deactivate one key.

## 2. Providers

### `GET /api/providers`

List provider metadata plus auth config and last verify result.

### `GET /api/providers/{provider_id}/config`

Get a single provider config.

### `PUT /api/providers/{provider_id}/config`

Update provider auth config and provider metadata.

Request example:

```json
{
  "enabled": true,
  "priority": 20,
  "notes": "cloudflare cookies loaded",
  "user_agent": "Mozilla/5.0 ...",
  "headers": {
    "accept-language": "en-US,en;q=0.9"
  },
  "cookies": {
    "cf_clearance": "..."
  },
  "tokens": {
    "x-turnstile-token": "..."
  }
}
```

### `POST /api/providers/{provider_id}/sync`

Queue one provider sync job and return the queued job row.

### `POST /api/sync/run`

Queue one job that syncs all enabled providers.

### `GET /api/jobs`

List recent queued, running, completed, or failed jobs.

## 3. Apps

### `GET /api/apps`

List app summaries with counts.

### `POST /api/apps`

Create or update an app label.

```json
{
  "slug": "openai",
  "name": "OpenAI",
  "notes": "registration flow"
}
```

## 4. Numbers

### `GET /api/numbers`

Query params:

- `country_name`
- `provider_id`
- `app_slug`
- `limit`

### `POST /api/numbers/pick`

Pick one eligible number.

```json
{
  "country_name": "United States",
  "provider_id": null,
  "app_slug": "openai",
  "include_cooling": false
}
```

### `GET /api/numbers/{number_id}`

Return canonical number detail, source evidence, and app states.

### `GET /api/numbers/{number_id}/messages`

Return recent messages for one number.

### `POST /api/numbers/{number_id}/blacklist`

```json
{
  "reason": "already blocked everywhere"
}
```

### `POST /api/numbers/{number_id}/blacklist/clear`

Clear global blacklist.

### `POST /api/numbers/{number_id}/app-state`

```json
{
  "app_slug": "openai",
  "app_name": "OpenAI",
  "status": "used",
  "notes": "success on signup"
}
```

## 5. Claims

### `GET /api/claims`

Query params:

- `active_only`
- `limit`

### `POST /api/claims`

Create a short-lived number claim.

```json
{
  "app_slug": "openai",
  "app_name": "OpenAI",
  "country_name": "United States",
  "provider_id": null,
  "purpose": "signup verification",
  "include_cooling": false,
  "ttl_minutes": 10
}
```

### `GET /api/claims/{claim_token}`

Return claim detail plus recent messages on the claimed number.

### `GET /api/claims/{claim_token}/messages`

Return recent messages only.

### `POST /api/claims/{claim_token}/release`

Release a claim and typically return app state to `available`.

### `POST /api/claims/{claim_token}/complete`

Complete a claim and usually mark app state to `success`.

### `POST /api/claims/{claim_token}/block`

Complete a claim and usually mark app state to `blocked`.

## 6. OpenAPI

FastAPI interactive docs are available at `/docs`.

## 7. Related Web UI

- `/auth`: create and revoke API keys
- `/providers`: edit provider cookies / headers / tokens
- `/claims`: create and manage claims
- `/claims/{claim_token}`: inspect one claim and recent SMS
- `/apps/{slug}`: inspect one app's number history
