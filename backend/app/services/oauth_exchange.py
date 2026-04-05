"""OAuth2 authorization-code token exchange + user identity extraction."""

from __future__ import annotations

import base64
import json
import time
from typing import Any

import httpx
import jwt
from jwt import PyJWTError

from app.core.config import Settings


def _apple_client_secret(settings: Settings) -> str:
    pem = settings.apple_private_key.replace("\\n", "\n").strip()
    headers = {"alg": "ES256", "kid": settings.apple_key_id}
    now = int(time.time())
    payload = {
        "iss": settings.apple_team_id,
        "iat": now,
        "exp": now + 86400 * 180,
        "aud": "https://appleid.apple.com",
        "sub": settings.apple_client_id,
    }
    return jwt.encode(
        payload,
        pem,
        algorithm="ES256",
        headers=headers,
    )


def exchange_google(
    settings: Settings,
    *,
    code: str,
    redirect_uri: str,
) -> tuple[str, str | None]:
    with httpx.Client(timeout=30.0) as client:
        r = client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
        )
        r.raise_for_status()
        data = r.json()
        access_token = data["access_token"]
        u = client.get(
            "https://openidconnect.googleapis.com/v1/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        u.raise_for_status()
        info = u.json()
    sub = str(info["sub"])
    email = info.get("email")
    if email is not None:
        email = str(email)
    return sub, email


def exchange_microsoft(
    settings: Settings,
    *,
    code: str,
    redirect_uri: str,
) -> tuple[str, str | None]:
    tenant = settings.microsoft_tenant.strip() or "consumers"
    token_url = (
        f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
    )
    with httpx.Client(timeout=30.0) as client:
        r = client.post(
            token_url,
            data={
                "client_id": settings.microsoft_client_id,
                "client_secret": settings.microsoft_client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
        )
        r.raise_for_status()
        data = r.json()
        access_token = data["access_token"]
        u = client.get(
            "https://graph.microsoft.com/oidc/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        u.raise_for_status()
        info = u.json()
    sub = str(info.get("sub") or info.get("id") or "")
    if not sub:
        raise ValueError("Microsoft userinfo missing sub")
    email = info.get("email")
    if email is not None:
        email = str(email)
    return sub, email


def _decode_jwt_payload_segment(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Invalid id_token")
    pad = "=" * (-len(parts[1]) % 4)
    raw = base64.urlsafe_b64decode(parts[1] + pad)
    return json.loads(raw.decode("utf-8"))


def exchange_apple(
    settings: Settings,
    *,
    code: str,
    redirect_uri: str,
) -> tuple[str, str | None]:
    secret = _apple_client_secret(settings)
    with httpx.Client(timeout=30.0) as client:
        r = client.post(
            "https://appleid.apple.com/auth/token",
            data={
                "client_id": settings.apple_client_id,
                "client_secret": secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        r.raise_for_status()
        data = r.json()
    id_token = data.get("id_token")
    if not id_token or not isinstance(id_token, str):
        raise ValueError("Apple token response missing id_token")
    try:
        payload = jwt.decode(
            id_token,
            options={"verify_signature": False},
        )
    except PyJWTError as exc:
        raise ValueError("Invalid Apple id_token") from exc
    sub = str(payload.get("sub") or "")
    if not sub:
        raise ValueError("Apple id_token missing sub")
    email = payload.get("email")
    if email is not None:
        email = str(email)
    return sub, email
