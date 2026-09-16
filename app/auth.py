from __future__ import annotations

import hmac
import secrets
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .models import User, utcnow

DISCORD_AUTHORIZE_URL = "https://discord.com/oauth2/authorize"
DISCORD_TOKEN_URL = "https://discord.com/api/oauth2/token"
DISCORD_ME_URL = "https://discord.com/api/v10/users/@me"

def oauth_ready() -> bool:
    return bool(settings.discord_client_id and settings.discord_client_secret and settings.owner_discord_ids)

def build_discord_authorize_url(request: Request) -> str:
    if not settings.discord_client_id or not settings.discord_client_secret:
        raise HTTPException(status_code=503, detail="Discord OAuth is not configured.")

    state = secrets.token_urlsafe(32)
    request.session["oauth_state"] = state
    params = {
        "response_type": "code",
        "client_id": settings.discord_client_id,
        "scope": "identify",
        "state": state,
        "redirect_uri": settings.discord_redirect_uri,
    }
    
    return f"{DISCORD_AUTHORIZE_URL}?{urlencode(params)}"

def verify_oauth_state(request: Request, returned_state: str | None) -> None:
    expected = request.session.pop("oauth_state", None)
    if not expected or not returned_state or not hmac.compare_digest(expected, returned_state):
        raise HTTPException(status_code=400, detail="Invalid OAuth state. Please try signing in again.")

async def exchange_code_for_user(code: str) -> dict:
    async with httpx.AsyncClient(timeout=10.0) as client:
        token_response = await client.post(
            DISCORD_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.discord_redirect_uri,
            },
            auth=(settings.discord_client_id, settings.discord_client_secret),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if token_response.is_error:
            raise HTTPException(status_code=502, detail="Discord rejected the OAuth token exchange.")
        
        token = token_response.json()
        access_token = token.get("access_token")
        if not access_token:
            raise HTTPException(status_code=502, detail="Discord did not return an access token.")

        user_response = await client.get(
            DISCORD_ME_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if user_response.is_error:
            raise HTTPException(status_code=502, detail="Could not fetch your Discord profile.")
        
        return user_response.json()

def upsert_discord_user(db: Session, profile: dict) -> User:
    discord_id = str(profile.get("id", "")).strip()
    username = str(profile.get("username", "")).strip()
    if not discord_id or not username:
        raise HTTPException(status_code=502, detail="Discord returned an incomplete user profile.")

    user = db.scalar(select(User).where(User.discord_id == discord_id))
    if user is None:
        user = User(discord_id=discord_id, username=username)
        db.add(user)

    user.username = username
    user.global_name = profile.get("global_name")
    user.avatar_hash = profile.get("avatar")
    user.last_login_at = utcnow()
    
    db.commit()
    db.refresh(user)
    
    return user
