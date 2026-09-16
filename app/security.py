from __future__ import annotations

import hmac
import secrets

from fastapi import HTTPException, Request

def ensure_csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token

def verify_csrf(request: Request, submitted_token: str) -> None:
    expected = request.session.get("csrf_token")
    if not expected or not submitted_token or not hmac.compare_digest(expected, submitted_token):
        raise HTTPException(status_code=403, detail="Invalid form token. Reload the page and try again.")
