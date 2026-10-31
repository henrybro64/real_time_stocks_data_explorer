"""Entra External ID OIDC helper (#240, #299)."""

from __future__ import annotations

import json
import logging

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from starlette.requests import Request

from app.core.logging import ConsoleLogFormatter, JsonLogFormatter
from app.services import entra_auth_service as entra


_SECRET = "s3cret-client-value-do-not-log"
_AUTH_CODE = "auth-code-xyz-do-not-log"
_ID_TOKEN = "eyJhbGciOi.not-a-real-token.do-not-log"
_ERROR_DESCRIPTION = (
    "AADSTS7000215: Invalid client secret provided. "
    "Secret 's3cret-client-value-do-not-log' does not match. "
    "Trace ID: 11111111-2222-3333-4444-555555555555 "
    "Correlation ID: 66666666-7777-8888-9999-000000000000"
)
_CORR = "aaaaaaa1-bbbb-cccc-dddd-eeeeeeeeeeee"
_TRACE = "ffffffff-1111-2222-3333-444444444444"
_MS_REQUEST = "req-aaaaaaaa-bbbb-cccc-dddd"


class _FakeResponse:
    def __init__(
        self,
        payload: dict | None,
        status_code: int = 200,
        *,
        json_error: bool = False,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._payload = payload
        self.status_code = status_code
        self._json_error = json_error
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError("http error")

    def json(self) -> dict:
        if self._json_error:
            raise ValueError("No JSON object could be decoded")
        assert self._payload is not None
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


def _metadata() -> dict:
    return {
        "authorization_endpoint": "https://login.example/authorize",
        "token_endpoint": "https://login.example/token",
        "jwks_uri": "https://login.example/jwks",
        "issuer": "https://login.example/tenant-1/v2.0",
    }


def _fake_get(_url, timeout=10.0, **_kwargs):
    return _FakeResponse(_metadata())


def _log_blob(caplog: pytest.LogCaptureFixture) -> str:
    json_formatter = JsonLogFormatter()
    console_formatter = ConsoleLogFormatter()
    parts: list[str] = []
    for record in caplog.records:
        parts.append(record.getMessage())
        parts.append(json_formatter.format(record))
        parts.append(console_formatter.format(record))
        parts.append(repr(record.__dict__))
    return "\n".join(parts)


def _assert_no_secrets(blob: str) -> None:
    lowered = blob.lower()
    assert _SECRET not in blob
    assert _AUTH_CODE not in blob
    assert _ID_TOKEN not in blob
    assert _ERROR_DESCRIPTION not in blob
    assert "error_description" not in lowered
    assert "invalid client secret provided" not in lowered
    assert "set-cookie" not in lowered


def test_entra_is_configured(entra_settings) -> None:
    assert entra.entra_is_configured()


def test_missing_authority_is_not_configured(entra_settings, monkeypatch) -> None:
    from app.core import config

    monkeypatch.setattr(config.settings, "entra_authority", "")
    assert entra.entra_is_configured() is False
    assert entra._authority() == ""


def test_begin_login_builds_authorize_url_with_pkce(entra_settings) -> None:
    start = entra.begin_login(http_get=_fake_get)
    assert start.authorization_url.startswith("https://login.example/authorize?")
    assert "client_id=client-1" in start.authorization_url
    assert "code_challenge_method=S256" in start.authorization_url
    assert "code_challenge=" in start.authorization_url
    assert start.state
    assert start.nonce
    assert start.code_verifier
    assert start.code_verifier not in start.authorization_url


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

    posted: dict = {}

    def fake_post(url, data=None, timeout=15.0, **kwargs):
        assert url == "https://login.example/token"
        posted["data"] = data
        posted["headers"] = kwargs.get("headers")
        return _FakeResponse({"id_token": id_token})

    class _Key:
        def __init__(self):
            self.key = public_key

    class _Jwks:
        def __init__(self, uri):
            self.uri = uri

        def get_signing_key_from_jwt(self, token):
            return _Key()

    monkeypatch.setattr(entra.oauth_identity_repository, "is_available", lambda: True)
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
        code_verifier="pkce-verifier-1",
        client_ip="127.0.0.1",
        http_get=_fake_get,
        http_post=fake_post,
        jwks_client_factory=_Jwks,
    )
    assert uid == "u-existing"
    assert posted["data"]["code_verifier"] == "pkce-verifier-1"
    assert posted["data"]["grant_type"] == "authorization_code"
    assert "client_secret" not in posted["data"]
    assert posted["headers"]["Content-Type"] == "application/x-www-form-urlencoded"


def test_complete_login_sends_secret_when_configured(entra_settings, monkeypatch) -> None:
    from app.core import config

    monkeypatch.setattr(config.settings, "entra_client_secret", _SECRET)
    posted: dict = {}

    def fake_post(url, data=None, timeout=15.0, **kwargs):
        posted["data"] = dict(data or {})
        return _FakeResponse({"id_token": "x"}, status_code=401, json_error=True)

    with pytest.raises(entra.EntraAuthError, match="Entra token exchange failed"):
        entra.complete_login(
            code=_AUTH_CODE,
            state="state-1",
            expected_state="state-1",
            expected_nonce="nonce",
            code_verifier="verifier",
            client_ip="127.0.0.1",
            http_get=_fake_get,
            http_post=fake_post,
        )
    assert posted["data"]["client_secret"] == _SECRET
    assert posted["data"]["code"] == _AUTH_CODE


def test_complete_login_creates_new_user_for_unknown_subject(entra_settings, monkeypatch) -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    issuer = "https://login.example/tenant-1/v2.0"
    nonce = "nonce-new"
    id_token = jwt.encode(
        {
            "sub": "oid-subject-new",
            "aud": "client-1",
            "iss": issuer,
            "exp": 4_000_000_000,
            "iat": 1_700_000_000,
            "nonce": nonce,
            "preferred_username": "new-entra@example.com",
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "k1"},
    )

    def fake_post(url, data=None, timeout=15.0, **kwargs):
        return _FakeResponse({"id_token": id_token})

    class _Key:
        def __init__(self):
            self.key = public_key

    class _Jwks:
        def __init__(self, uri):
            self.uri = uri

        def get_signing_key_from_jwt(self, token):
            return _Key()

    created: dict = {}
    monkeypatch.setattr(entra.oauth_identity_repository, "is_available", lambda: True)
    monkeypatch.setattr(entra.oauth_identity_repository, "get_user_id", lambda **kwargs: None)

    def fake_create(**kwargs):
        created.update(kwargs)
        return kwargs["user_id"]

    monkeypatch.setattr(
        entra.oauth_identity_repository,
        "create_user_with_identity",
        fake_create,
    )
    monkeypatch.setattr(
        "app.repositories.user_repository.user_repository.get_by_username",
        lambda _username: None,
    )

    uid = entra.complete_login(
        code="auth-code",
        state="state-1",
        expected_state="state-1",
        expected_nonce=nonce,
        code_verifier="pkce-verifier-1",
        client_ip="127.0.0.1",
        http_get=_fake_get,
        http_post=fake_post,
        jwks_client_factory=_Jwks,
    )
    assert uid == created["user_id"]
    assert created["subject"] == "oid-subject-new"
    assert created["provider"] == "entra_external_id"
    assert created["user_id"].startswith("u-")


def test_matching_local_username_does_not_reuse_that_user(entra_settings, monkeypatch) -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    issuer = "https://login.example/tenant-1/v2.0"
    nonce = "nonce-collision"
    id_token = jwt.encode(
        {
            "sub": "oid-subject-collision",
            "aud": "client-1",
            "iss": issuer,
            "exp": 4_000_000_000,
            "iat": 1_700_000_000,
            "nonce": nonce,
            "preferred_username": "local-user@example.com",
            "email": "local-user@example.com",
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "k1"},
    )

    class _Key:
        def __init__(self):
            self.key = public_key

    class _Jwks:
        def __init__(self, uri):
            self.uri = uri

        def get_signing_key_from_jwt(self, token):
            return _Key()

    created: dict = {}
    monkeypatch.setattr(entra.oauth_identity_repository, "is_available", lambda: True)
    monkeypatch.setattr(entra.oauth_identity_repository, "get_user_id", lambda **kwargs: None)

    def fake_create(**kwargs):
        created.update(kwargs)
        return kwargs["user_id"]

    monkeypatch.setattr(
        entra.oauth_identity_repository,
        "create_user_with_identity",
        fake_create,
    )

    def existing_local_user(username: str):
        if username == "local-userexample.com":
            return {"id": "u-local-existing", "username": username}
        return None

    monkeypatch.setattr(
        "app.repositories.user_repository.user_repository.get_by_username",
        existing_local_user,
    )

    uid = entra.complete_login(
        code="auth-code",
        state="state-1",
        expected_state="state-1",
        expected_nonce=nonce,
        code_verifier="pkce-verifier-1",
        client_ip="127.0.0.1",
        http_get=_fake_get,
        http_post=lambda url, data=None, timeout=15.0, **kwargs: _FakeResponse({"id_token": id_token}),
        jwks_client_factory=_Jwks,
    )
    assert uid != "u-local-existing"
    assert uid.startswith("u-")
    assert created["username"] == "local-userexample.com-1"
    assert created["subject"] == "oid-subject-collision"


def test_complete_login_maps_persist_error_to_generic_signin_failure(
    entra_settings, monkeypatch
) -> None:
    from app.repositories.oauth_identity_repository import OAuthIdentityPersistError

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    issuer = "https://login.example/tenant-1/v2.0"
    nonce = "nonce-persist"
    id_token = jwt.encode(
        {
            "sub": "oid-persist",
            "aud": "client-1",
            "iss": issuer,
            "exp": 4_000_000_000,
            "iat": 1_700_000_000,
            "nonce": nonce,
            "preferred_username": "persist@example.com",
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "k1"},
    )

    class _Key:
        def __init__(self):
            self.key = public_key

    class _Jwks:
        def __init__(self, uri):
            self.uri = uri

        def get_signing_key_from_jwt(self, token):
            return _Key()

    monkeypatch.setattr(entra.oauth_identity_repository, "is_available", lambda: True)
    monkeypatch.setattr(entra.oauth_identity_repository, "get_user_id", lambda **kwargs: None)
    monkeypatch.setattr(
        entra.oauth_identity_repository,
        "create_user_with_identity",
        lambda **kwargs: (_ for _ in ()).throw(
            OAuthIdentityPersistError("IntegrityError secret details")
        ),
    )
    monkeypatch.setattr(
        "app.repositories.user_repository.user_repository.get_by_username",
        lambda _username: None,
    )

    with pytest.raises(entra.EntraAuthError, match="Entra sign-in failed") as exc:
        entra.complete_login(
            code="auth-code",
            state="state-1",
            expected_state="state-1",
            expected_nonce=nonce,
            code_verifier="pkce-verifier-1",
            client_ip="127.0.0.1",
            http_get=_fake_get,
            http_post=lambda url, data=None, timeout=15.0, **kwargs: _FakeResponse(
                {"id_token": id_token}
            ),
            jwks_client_factory=_Jwks,
        )
    assert "IntegrityError" not in str(exc.value)
    assert "secret details" not in str(exc.value)


def test_complete_login_rejects_bad_state(entra_settings) -> None:
    with pytest.raises(entra.EntraAuthError, match="Invalid OAuth state"):
        entra.complete_login(
            code="x",
            state="a",
            expected_state="b",
            expected_nonce="n",
            code_verifier="v",
            client_ip="127.0.0.1",
        )


def test_complete_login_rejects_missing_pkce_verifier(entra_settings) -> None:
    with pytest.raises(entra.EntraAuthError, match="Missing PKCE verifier"):
        entra.complete_login(
            code="x",
            state="s",
            expected_state="s",
            expected_nonce="n",
            code_verifier=None,
            client_ip="127.0.0.1",
        )


def _failing_token_login(entra_settings, monkeypatch, response_or_exc) -> None:
    def fake_post(url, data=None, timeout=15.0, **kwargs):
        if isinstance(response_or_exc, BaseException):
            raise response_or_exc
        return response_or_exc

    entra.complete_login(
        code=_AUTH_CODE,
        state="state-1",
        expected_state="state-1",
        expected_nonce="nonce",
        code_verifier="verifier",
        client_ip="127.0.0.1",
        http_get=_fake_get,
        http_post=fake_post,
    )


def test_token_invalid_client_logs_aadsts_without_secrets(
    entra_settings, monkeypatch, caplog
) -> None:
    from app.core import config

    monkeypatch.setattr(config.settings, "entra_client_secret", _SECRET)
    caplog.set_level(logging.ERROR, logger="app.services.entra_auth_service")
    response = _FakeResponse(
        {
            "error": "invalid_client",
            "error_description": _ERROR_DESCRIPTION,
            "error_codes": [7000215],
            "correlation_id": _CORR,
            "trace_id": _TRACE,
            "error_uri": "https://login.microsoftonline.com/error?code=7000215",
        },
        status_code=401,
        headers={"x-ms-request-id": _MS_REQUEST},
    )
    with pytest.raises(entra.EntraAuthError, match="Entra token exchange failed"):
        _failing_token_login(entra_settings, monkeypatch, response)
    records = [r for r in caplog.records if r.name == "app.services.entra_auth_service"]
    assert records
    record = records[-1]
    assert record.http_status == 401
    assert record.oauth_error == "invalid_client"
    assert record.aadsts_code == "AADSTS7000215"
    assert record.entra_correlation_id == _CORR
    assert record.entra_trace_id == _TRACE
    assert record.entra_request_id == _MS_REQUEST
    assert record.client_secret_configured is True
    assert record.entra_client_id == "client-1"
    blob = _log_blob(caplog)
    _assert_no_secrets(blob)
    payload = json.loads(JsonLogFormatter().format(record))
    assert "error_description" not in payload
    assert payload["aadsts_code"] == "AADSTS7000215"
    console = ConsoleLogFormatter().format(record)
    assert "aadsts_code=AADSTS7000215" in console
    assert "oauth_error=invalid_client" in console
    assert "client_secret_configured=True" in console
    assert "http_status=401" in console


def test_token_invalid_grant_extracts_aadsts_from_description(
    entra_settings, monkeypatch, caplog
) -> None:
    caplog.set_level(logging.ERROR, logger="app.services.entra_auth_service")
    description = (
        "AADSTS9002313: Invalid request. Input authorization code "
        f"{_AUTH_CODE} is malformed. {_SECRET}"
    )
    response = _FakeResponse(
        {
            "error": "invalid_grant",
            "error_description": description,
            "correlation_id": _CORR,
        },
        status_code=400,
    )
    with pytest.raises(entra.EntraAuthError, match="Entra token exchange failed"):
        _failing_token_login(entra_settings, monkeypatch, response)
    record = caplog.records[-1]
    assert record.http_status == 400
    assert record.oauth_error == "invalid_grant"
    assert record.aadsts_code == "AADSTS9002313"
    _assert_no_secrets(_log_blob(caplog))


def test_token_error_description_ids_are_logged_without_the_description(
    entra_settings, monkeypatch, caplog
) -> None:
    caplog.set_level(logging.ERROR, logger="app.services.entra_auth_service")
    description = (
        "AADSTS7000215: Invalid client secret provided. "
        f"Trace ID: {_TRACE} Correlation ID: {_CORR} {_SECRET} {_AUTH_CODE}"
    )
    response = _FakeResponse(
        {"error": "invalid_client", "error_description": description},
        status_code=401,
    )
    with pytest.raises(entra.EntraAuthError, match="Entra token exchange failed"):
        _failing_token_login(entra_settings, monkeypatch, response)
    record = caplog.records[-1]
    assert record.aadsts_code == "AADSTS7000215"
    assert record.entra_trace_id == _TRACE
    assert record.entra_correlation_id == _CORR
    assert record.client_secret_configured is False
    _assert_no_secrets(_log_blob(caplog))


def test_token_malformed_response_is_generic(entra_settings, monkeypatch, caplog) -> None:
    caplog.set_level(logging.ERROR, logger="app.services.entra_auth_service")
    response = _FakeResponse(
        None,
        status_code=401,
        json_error=True,
        headers={"x-ms-request-id": _MS_REQUEST},
    )
    with pytest.raises(entra.EntraAuthError, match="Entra token exchange failed"):
        _failing_token_login(entra_settings, monkeypatch, response)
    record = caplog.records[-1]
    assert record.http_status == 401
    assert record.oauth_error == "malformed_response"
    assert record.entra_request_id == _MS_REQUEST
    _assert_no_secrets(_log_blob(caplog))


def test_token_timeout_is_generic(entra_settings, monkeypatch, caplog) -> None:
    caplog.set_level(logging.ERROR, logger="app.services.entra_auth_service")
    with pytest.raises(entra.EntraAuthError, match="Entra token exchange failed"):
        _failing_token_login(
            entra_settings,
            monkeypatch,
            httpx.TimeoutException("timed out talking to login.example"),
        )
    record = caplog.records[-1]
    assert record.oauth_error == "timeout"
    blob = _log_blob(caplog)
    assert "timed out talking" not in blob
    _assert_no_secrets(blob)


def test_token_network_error_is_generic(entra_settings, monkeypatch, caplog) -> None:
    caplog.set_level(logging.ERROR, logger="app.services.entra_auth_service")
    with pytest.raises(entra.EntraAuthError, match="Entra token exchange failed"):
        _failing_token_login(
            entra_settings,
            monkeypatch,
            httpx.ConnectError(f"failed to connect while exchanging {_AUTH_CODE}"),
        )
    record = caplog.records[-1]
    assert record.oauth_error == "network_error"
    blob = _log_blob(caplog)
    assert "failed to connect" not in blob
    _assert_no_secrets(blob)


def test_unknown_oauth_error_is_not_allowlisted() -> None:
    assert entra.allowlisted_oauth_error("invalid_client") == "invalid_client"
    assert entra.allowlisted_oauth_error("not_a_real_error") is None
    assert entra.allowlisted_oauth_error(_ERROR_DESCRIPTION) is None


def test_extract_aadsts_code_from_error_codes_and_description() -> None:
    assert entra.extract_aadsts_code([7000215]) == "AADSTS7000215"
    assert entra.extract_aadsts_code(_ERROR_DESCRIPTION) == "AADSTS7000215"
    assert entra.extract_aadsts_code("no code here") is None


def test_diagnose_never_returns_description_or_body(entra_settings) -> None:
    response = _FakeResponse(
        {
            "error": "invalid_client",
            "error_description": _ERROR_DESCRIPTION,
            "error_codes": [7000215],
            "access_token": _ID_TOKEN,
        },
        status_code=401,
    )
    diagnostic = entra.diagnose_token_error_response(response)
    serialized = json.dumps(diagnostic)
    assert "error_description" not in diagnostic
    assert "access_token" not in diagnostic
    assert _ERROR_DESCRIPTION not in serialized
    assert _ID_TOKEN not in serialized
    assert diagnostic["aadsts_code"] == "AADSTS7000215"


def test_callback_does_not_leak_microsoft_error_description(monkeypatch, caplog) -> None:
    from app.api.routes import auth as auth_routes

    monkeypatch.setattr(auth_routes.settings, "auth_enabled", True)
    monkeypatch.setattr(auth_routes, "entra_is_configured", lambda: True)
    caplog.set_level(logging.INFO, logger="app.api.routes.auth")
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/auth/callback/entra",
            "headers": [],
            "query_string": b"",
        }
    )
    with pytest.raises(HTTPException) as exc:
        auth_routes.callback_entra(
            request,
            error="invalid_client",
            error_description=_ERROR_DESCRIPTION,
        )
    assert exc.value.status_code == 401
    assert exc.value.detail == entra.PUBLIC_SIGNIN_FAILED
    assert _ERROR_DESCRIPTION not in str(exc.value.detail)
    assert "AADSTS" not in str(exc.value.detail)
    blob = _log_blob(caplog)
    _assert_no_secrets(blob)
    assert _ERROR_DESCRIPTION not in blob
    records = [r for r in caplog.records if r.name == "app.api.routes.auth"]
    assert records
    record = records[-1]
    assert record.oauth_error == "invalid_client"
    assert record.aadsts_code == "AADSTS7000215"
    assert record.entra_trace_id == "11111111-2222-3333-4444-555555555555"
    assert record.entra_correlation_id == "66666666-7777-8888-9999-000000000000"
