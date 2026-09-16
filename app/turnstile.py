from __future__ import annotations

from urllib.parse import urlparse

import httpx

from .config import settings

SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"

def turnstile_enabled() -> bool:
    return bool(settings.turnstile_site_key and settings.turnstile_secret_key)

def verify_turnstile(token: str, expected_action: str) -> bool:
    """Validate a single-use Turnstile token and its intended form action."""
    if not turnstile_enabled():
        return True
    if not token:
        return False

    try:
        response = httpx.post(
            SITEVERIFY_URL,
            data={"secret": settings.turnstile_secret_key, "response": token},
            timeout=5.0,
        )
        response.raise_for_status()
        result = response.json()
    except (httpx.HTTPError, ValueError):
        return False
    if not isinstance(result, dict):
        return False

    expected_hostname = urlparse(settings.base_url).hostname
    return bool(
        result.get("success")
        and result.get("action") == expected_action
        and (not expected_hostname or result.get("hostname") == expected_hostname)
    )
