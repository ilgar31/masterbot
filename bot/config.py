from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_env_file(path: str = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "да"}


@dataclass(frozen=True)
class Settings:
    api_base_url: str
    api_token: str | None
    api_token_header: str
    api_token_prefix: str
    api_key: str | None
    api_key_header: str
    api_timeout: float
    database_path: str
    consent_file: str
    vk_group_token: str | None
    vk_group_id: str | None
    vk_confirmation_token: str | None
    vk_secret_key: str | None
    vk_api_version: str
    host: str
    port: int
    admin_enabled: bool
    admin_host: str
    admin_port: int
    admin_login: str
    admin_password: str
    bot_name: str

    @classmethod
    def from_env(cls) -> "Settings":
        load_env_file()
        return cls(
            api_base_url=os.getenv("BOT_API_BASE_URL", "http://localhost:8000"),
            api_token=os.getenv("BOT_API_TOKEN"),
            api_token_header=os.getenv("BOT_API_TOKEN_HEADER", "Authorization"),
            api_token_prefix=os.getenv("BOT_API_TOKEN_PREFIX", "Bearer "),
            api_key=os.getenv("BOT_API_KEY"),
            api_key_header=os.getenv("BOT_API_KEY_HEADER", "apikey"),
            api_timeout=float(os.getenv("BOT_API_TIMEOUT", "5")),
            database_path=os.getenv("BOT_DATABASE_PATH", "data/bot.sqlite3"),
            consent_file=os.getenv("BOT_CONSENT_FILE", "consent.txt"),
            vk_group_token=os.getenv("VK_GROUP_TOKEN"),
            vk_group_id=os.getenv("VK_GROUP_ID"),
            vk_confirmation_token=os.getenv("VK_CONFIRMATION_TOKEN"),
            vk_secret_key=os.getenv("VK_SECRET_KEY"),
            vk_api_version=os.getenv("VK_API_VERSION", "5.199"),
            host=os.getenv("BOT_HOST", "0.0.0.0"),
            port=int(os.getenv("BOT_PORT", "8080")),
            admin_enabled=_bool(os.getenv("ADMIN_ENABLED"), True),
            admin_host=os.getenv("ADMIN_HOST", "127.0.0.1"),
            admin_port=int(os.getenv("ADMIN_PORT", "8081")),
            admin_login=os.getenv("ADMIN_LOGIN", "admin"),
            admin_password=os.getenv("ADMIN_PASSWORD", "admin"),
            bot_name=os.getenv("BOT_NAME", "Бот клиники"),
        )
