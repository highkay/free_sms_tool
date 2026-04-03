# Free SMS Tool

[English README](README.md)

一个以证据优先为原则的免费短信号码池管理工具，基于：

- FastAPI
- HTMX + DaisyUI
- SQLite
- `httpx`
- 本地 FlareSolverr `127.0.0.1:8191`
- `uv` 作为依赖管理工具

## 功能概览

- 从已验证的免费短信 provider 同步公共号码到本地号码池
- 根据最近真实短信证据给号码打活跃度和新鲜度分
- 管理全局黑名单和按应用隔离的已用 / 阻塞状态
- 创建短时 claim，避免同一号码被重复分配
- 当默认可分配库存低于阈值时自动扩池
- 提供 Web UI 和带 API Key 鉴权的 JSON API
- 管理 provider 的 cookies / headers / tokens 等配置

## 快速开始

```bash
uv sync
```

基于 `.env.example` 创建本地 `.env`：

```bash
cp .env.example .env
```

启动前建议确认：

- 先设置 `BOOTSTRAP_API_KEY`，保证 `/api/*` 一启动就能使用
- 本地 FlareSolverr 可访问 `http://127.0.0.1:8191/v1`
- 如果 Web UI 需要密码，再设置 `WEB_UI_USERNAME` / `WEB_UI_PASSWORD`

启动 Web：

```bash
uv run uvicorn app.main:app --reload
```

在第二个终端启动 collector：

```bash
uv run python -m app.collector.main
```

本地访问：

- Web UI: `http://127.0.0.1:8000/`
- OpenAPI 文档: `http://127.0.0.1:8000/docs`
- API Key 页面: `http://127.0.0.1:8000/auth`

## Docker Compose

```bash
docker compose up -d --build
```

Compose 会启动：

- `app`，默认暴露在 `http://127.0.0.1:${HOST_PORT:-18000}`
- `collector`，负责消费同步任务和自动扩池
- `flaresolverr`，供容器内应用通过 `http://flaresolverr:8191/v1` 访问

自动扩池行为：

- collector 会监控默认可分配库存，当消耗率超过 `AUTO_REPLENISH_CONSUMPTION_THRESHOLD` 时，自动入队一次更深的同步任务
- 如果某个 `app_slug` 已经没有任何可分配号码，默认 pick / claim 流程也会触发一次补池
- 自动补池会去重，并受 `AUTO_REPLENISH_COOLDOWN_SECONDS` 控制，避免反复打 provider

如果本机 `8000` 已被占用，可以在 `.env` 中改 `HOST_PORT`。

常用 Docker 命令：

```bash
docker compose ps
docker compose logs -f app
docker compose logs -f collector
docker compose logs -f flaresolverr
docker compose down
```

默认 Compose 访问地址：

- Web UI: `http://127.0.0.1:18000/`
- OpenAPI 文档: `http://127.0.0.1:18000/docs`
- API Key 页面: `http://127.0.0.1:18000/auth`
- 健康检查: `http://127.0.0.1:18000/healthz`

## JSON API 鉴权

所有主要 JSON API 都位于 `/api/*`，并要求：

```http
Authorization: Bearer <your-api-key>
```

初始 bootstrap key 来自 `BOOTSTRAP_API_KEY`。

可用它创建和管理正式 key：

- `GET /api/auth/keys`
- `POST /api/auth/keys`
- `POST /api/auth/keys/{key_id}/revoke`

## 主要 API 分组

- `auth`: API Key 管理
- `providers`: 查看 / 更新 provider 配置并入队同步任务
- `apps`: 管理应用标签
- `numbers`: 列表 / 选择 / 详情 / 黑名单 / 应用状态
- `claims`: 创建 / 释放 / 完成短时号码 claim
- `sync`: 入队启用 provider 的同步任务
- `jobs`: 查看最近 queued / running / completed / failed 的 job

## 常见操作流程

以下示例默认使用 Docker Compose 暴露的地址：

- `BASE_URL=http://127.0.0.1:18000`
- `API_KEY=<your-bootstrap-or-managed-api-key>`

### 1. 检查服务健康状态并列出 provider

```bash
curl "$BASE_URL/healthz"
curl -H "Authorization: Bearer $API_KEY" "$BASE_URL/api/providers"
```

先确认服务可用、provider 注册表已初始化。

### 2. 给某个 provider 配置 Cloudflare cookies 并入队同步任务

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

现在同步会入队为后台 job，由 collector 异步执行。

### 3. 为某个应用挑选一个当前可用号码

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

返回结果会经过新鲜度、全局黑名单、活动 claim 和应用级历史状态过滤。

如果指定的 `app_slug` 当前已经没有任何可分配号码，本次请求仍会返回 `null`，但系统会顺手触发一次自动补池。

### 4. 创建 claim 并读取该号码最近短信

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

取返回的 `claim_token` 后，再查看 claim 详情和最近短信：

```bash
curl -H "Authorization: Bearer $API_KEY" "$BASE_URL/api/claims/<claim_token>"
curl -H "Authorization: Bearer $API_KEY" "$BASE_URL/api/claims/<claim_token>/messages"
```

这是单次注册或验证流程的标准路径。

### 5. 完成 claim、标记阻塞或拉黑号码

将某个 claim 标记为成功：

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

将某个 claim 标记为阻塞：

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

将号码加入全局黑名单：

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

将号码标记为某个应用已消费：

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

这些操作用于保持号码池干净，避免重复分配死号或已消费号码。

## 主要 Web 页面

- `/numbers`: 号码池与快速选择
- `/numbers/{id}`: 号码证据详情
- `/claims`: claim 列表与创建
- `/claims/{claim_token}`: claim 详情与最近短信
- `/apps`: 应用汇总
- `/apps/{slug}`: 单个应用的号码使用详情
- `/providers`: provider 配置管理
- `/auth`: API Key 管理

更详细的 API 示例见 [docs/api.md](docs/api.md)。

## 常用命令

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

## 项目说明

- `receive_smss`、`temp_number`、`sms24`、`smstome` 是默认主池 provider
- `quackr` 目前仍然只是 discovery-only，因为 message 获取还需要进一步验证
- provider 认证配置保存在 SQLite，可直接在 `/providers` 页面修改
- claim 会自动过期，并释放应用级的 `claimed` 状态
- provider fixture 保存在 `tests/fixtures/providers`
- 目前 fixture 测试覆盖 9 个 provider 表面：5 个完整 message parser、`quackr` 的 discovery-only，以及 3 个 detail-only / stale-or-restricted provider（`jiemahao`、`freephonenum`、`receivesms_org`）

## 友情链接

- [Linux.do](https://linux.do/)
