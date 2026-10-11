"""Entra External ID OIDC helper (#240)."""

from __future__ import annotations

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.services import entra_auth_service as entra


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError("http error")

    def json(self) -> dict:
        return self._payload


@pytest.fixture
def entra_settings(monkeypatch):
    from app.core import config

    monkeypatch.setattr(config.settings, "entra_external_id_enabled", True)
    monkeypatch.setattr(config.settings, "entra_tenant_id", "tenant-1")
    monkeypatch.setattr(config.settings, "entra_client_id", "client-1")
    monkeypatch.setattr(config.settings, "entra_client_secret", "")
    monkeypatch.setattr(config.settings, "entra_authority", "https://login.example/tenant-1/v2.0")
    monkeypatch.setattr(
        config.settings,
        "entra_redirect_uri",
        "http://localhost:3000/api/auth/callback/entra",
    )
    monkeypatch.setattr(config.settings, "auth_login_rate_limit_per_minute", 100)
    monkeypatch.setattr(config.settings, "auth_register_rate_limit_per_hour", 100)


def test_entra_is_configured(entra_settings) -> None:
    assert entra.entra_is_configured()


def test_begin_login_builds_authorize_url(entra_settings) -> None:
    metadata = {
        "authorization_endpoint": "https://login.example/authorize",
        "token_endpoint": "https://login.example/token",
        "jwks_uri": "https://login.example/jwks",
        "issuer": "https://login.example/tenant-1/v2.0",
    }

    def fake_get(url, timeout=10.0):
        assert "openid-configuration" in url
        return _FakeResponse(metadata)

    start = entra.begin_login(http_get=fake_get)
    assert start.authorization_url.startswith("https://login.example/authorize?")
    assert "client_id=client-1" in start.authorization_url
    assert start.state
    assert start.nonce


def test_complete_login_links_existing_identity(entra_settings, monkeypatch) -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    issuer = "https://login.example/tenant-1/v2.0"
    nonce = "nonce-abc"
    id_token = jwt.encode(
        {
            "sub": "oid-subject-1",
            "aud": "client-1",
            "iss": issuer,
            "exp": 4_000_000_000,
            "iat": 1_700_000_000,
            "nonce": nonce,
            "preferred_username": "alice@example.com",
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "k1"},
    )

    metadata = {
        "authorization_endpoint": "https://login.example/authorize",
        "token_endpoint": "https://login.example/token",
        "jwks_uri": "https://login.example/jwks",
        "issuer": issuer,
    }

    def fake_get(url, timeout=10.0):
        return _FakeResponse(metadata)

    def fake_post(url, data=None, timeout=15.0):
        assert url == "https://login.example/token"
        return _FakeResponse({"id_token": id_token})

    class _Key:
        def __init__(self):
            self.key = public_key

    class _Jwks:
        def __init__(self, uri):
            self.uri = uri

        def get_signing_key_from_jwt(self, token):
            return _Key()

    monkeypatch.setattr(
        entra.oauth_identity_repository,
        "is_available",
        lambda: True,
    )
    monkeypatch.setattr(
        entra.oauth_identity_repository,
        "get_user_id",
        lambda **kwargs: "u-existing",
    )

    uid = entra.complete_login(
        code="auth-code",
        state="state-1",
        expected_state="state-1",
        expected_nonce=nonce,
        client_ip="127.0.0.1",
        http_get=fake_get,
        http_post=fake_post,
        jwks_client_factory=_Jwks,
    )
    assert uid == "u-existing"


def test_complete_login_rejects_bad_state(entra_settings) -> None:
    with pytest.raises(entra.EntraAuthError, match="Invalid OAuth state"):
        entra.complete_login(
            code="x",
            state="a",
            expected_state="b",
            expected_nonce="n",
            client_ip="127.0.0.1",
        )
