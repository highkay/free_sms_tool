from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _as_bool(value: str, default: bool = False) -> bool:
    if value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    app_name: str = "Free SMS Tool"
    debug: bool = False
    host: str = "127.0.0.1"
    port: int = 8000
    database_path: Path = Path("data/free_sms_tool.db")
    flaresolverr_url: str = "http://127.0.0.1:8191/v1"
    freshness_hot_max: int = 60
    freshness_warm_max: int = 180
    freshness_cooling_max: int = 360
    sync_limit_per_provider: int = 10
    message_limit_per_number: int = 20
    default_claim_ttl_minutes: int = 10
    bootstrap_api_key: str = ""
    bootstrap_api_key_name: str = "bootstrap"
    log_level: str = "INFO"
    log_path: Path = Path("logs/free_sms_tool.log")
    log_json: bool = False
    collector_poll_seconds: int = 5
    auto_replenish_enabled: bool = True
    auto_replenish_consumption_threshold: float = 0.8
    auto_replenish_sync_limit_per_provider: int = 30
    auto_replenish_cooldown_seconds: int = 600
    web_ui_username: str = ""
    web_ui_password: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            app_name=os.getenv("APP_NAME", "Free SMS Tool"),
            debug=_as_bool(os.getenv("DEBUG", "0")),
            host=os.getenv("HOST", "127.0.0.1"),
            port=int(os.getenv("PORT", "8000")),
            database_path=Path(os.getenv("DATABASE_PATH", "data/free_sms_tool.db")),
            flaresolverr_url=os.getenv("FLARESOLVERR_URL", "http://127.0.0.1:8191/v1"),
            freshness_hot_max=int(os.getenv("FRESHNESS_HOT_MAX", "60")),
            freshness_warm_max=int(os.getenv("FRESHNESS_WARM_MAX", "180")),
            freshness_cooling_max=int(os.getenv("FRESHNESS_COOLING_MAX", "360")),
            sync_limit_per_provider=int(os.getenv("SYNC_LIMIT_PER_PROVIDER", "10")),
            message_limit_per_number=int(os.getenv("MESSAGE_LIMIT_PER_NUMBER", "20")),
            default_claim_ttl_minutes=int(os.getenv("DEFAULT_CLAIM_TTL_MINUTES", "10")),
            bootstrap_api_key=os.getenv("BOOTSTRAP_API_KEY", "").strip(),
            bootstrap_api_key_name=os.getenv("BOOTSTRAP_API_KEY_NAME", "bootstrap").strip() or "bootstrap",
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            log_path=Path(os.getenv("LOG_PATH", "logs/free_sms_tool.log")),
            log_json=_as_bool(os.getenv("LOG_JSON", "0")),
            collector_poll_seconds=max(1, int(os.getenv("COLLECTOR_POLL_SECONDS", "5"))),
            auto_replenish_enabled=_as_bool(os.getenv("AUTO_REPLENISH_ENABLED", "1"), default=True),
            auto_replenish_consumption_threshold=min(
                1.0,
                max(0.0, float(os.getenv("AUTO_REPLENISH_CONSUMPTION_THRESHOLD", "0.8"))),
            ),
            auto_replenish_sync_limit_per_provider=max(
                1,
                int(os.getenv("AUTO_REPLENISH_SYNC_LIMIT_PER_PROVIDER", "30")),
            ),
            auto_replenish_cooldown_seconds=max(1, int(os.getenv("AUTO_REPLENISH_COOLDOWN_SECONDS", "600"))),
            web_ui_username=os.getenv("WEB_UI_USERNAME", "").strip(),
            web_ui_password=os.getenv("WEB_UI_PASSWORD", "").strip(),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()
