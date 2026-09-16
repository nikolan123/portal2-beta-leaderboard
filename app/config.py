from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

@dataclass(frozen=True)
class Settings:
    app_name: str
    base_url: str
    discord_client_id: str
    discord_client_secret: str
    discord_redirect_uri: str
    owner_discord_ids: tuple[str, ...]
    session_secret: str
    database_url: str
    cookie_secure: bool
    turnstile_site_key: str
    turnstile_secret_key: str
    discord_moderation_webhook_url: str = ""

def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}

def _env_list(name: str) -> tuple[str, ...]:
    """Parse a comma-separated env var into a tuple, dropping blanks."""
    return tuple(part.strip() for part in os.getenv(name, "").split(",") if part.strip())

def load_settings() -> Settings:
    base_url = os.getenv("BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    redirect_uri = os.getenv("DISCORD_REDIRECT_URI", f"{base_url}/auth/callback")
    database_url = os.getenv("DATABASE_URL", "sqlite:///./data/portal2_runs.db")

    if database_url.startswith("sqlite:///./data/"):
        Path("data").mkdir(parents=True, exist_ok=True)

    return Settings(
        app_name=os.getenv("APP_NAME", "Portal 2 Runs"),
        base_url=base_url,
        discord_client_id=os.getenv("DISCORD_CLIENT_ID", ""),
        discord_client_secret=os.getenv("DISCORD_CLIENT_SECRET", ""),
        discord_redirect_uri=redirect_uri,
        owner_discord_ids=_env_list("OWNER_DISCORD_ID"),
        session_secret=os.getenv("SESSION_SECRET", "dev-only-change-me"),
        database_url=database_url,
        cookie_secure=_env_bool("COOKIE_SECURE", False),
        turnstile_site_key=os.getenv("TURNSTILE_SITE_KEY", ""),
        turnstile_secret_key=os.getenv("TURNSTILE_SECRET_KEY", ""),
        discord_moderation_webhook_url=os.getenv("DISCORD_MODERATION_WEBHOOK_URL", ""),
    )

settings = load_settings()
