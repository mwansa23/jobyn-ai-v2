"""Targeted tests for the Supabase login/register proxy.

These verify the demo/admin migration path: when Supabase Auth is
configured, POST /auth/login performs Supabase's password grant and
returns a Supabase-issued JWT (with ``iss``) that passes the production
authentication gate, and POST /auth/register provisions both the
Supabase identity and the linked Jobyn account.

The Supabase Auth API and JWKS endpoints are mocked via
httpx.MockTransport.  No live Supabase project is required.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from pydantic import SecretStr

from backend.auth import supabase as supabase_auth
from backend.core.config import get_settings

_PROJECT_URL = "https://proxy-test-project.supabase.co"
_ISSUER = f"{_PROJECT_URL}/auth/v1"
_KID = "proxy-test-signing-key"
_PUBLISHABLE_KEY = "proxy-test-publishable-key"

REGISTER_URL = "/api/v1/auth/register"
LOGIN_URL = "/api/v1/auth/login"
ME_URL = "/api/v1/auth/me"


@pytest.fixture
def rsa_keys():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    public_jwk.update({"kid": _KID, "alg": "RS256", "use": "sig"})
    return private_key, public_jwk


@pytest.fixture
def supabase_passwords() -> dict[str, str]:
    """Email → password pairs "registered" in the mocked Supabase Auth."""
    return {}


@pytest.fixture
def supabase_users() -> dict[str, dict[str, Any]]:
    """sub → Supabase user records created through the mocked signup."""
    return {}


@pytest.fixture
def mocked_supabase_auth(
    monkeypatch: pytest.MonkeyPatch,
    rsa_keys,
    supabase_passwords: dict[str, str],
    supabase_users: dict[str, dict[str, Any]],
):
    """Mock Supabase Auth (signup, password grant, JWKS, /user)."""
    private_key, public_jwk = rsa_keys
    settings = get_settings()
    monkeypatch.setattr(settings, "SUPABASE_URL", _PROJECT_URL)
    monkeypatch.setattr(settings, "SUPABASE_JWT_SECRET", None)
    monkeypatch.setattr(settings, "SUPABASE_PUBLISHABLE_KEY", SecretStr(_PUBLISHABLE_KEY))
    supabase_auth._JWKS_CACHE.clear()

    def mint_token(sub: str, email: str) -> str:
        claims = {
            "sub": sub,
            "iss": _ISSUER,
            "aud": "authenticated",
            "iat": int(time.time()),
            "exp": int(time.time()) + 600,
            "email": email,
            "role": "authenticated",
        }
        return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": _KID})

    def handle(request: httpx.Request) -> httpx.Response:
        url = str(request.url)

        if url.endswith("/.well-known/jwks.json"):
            return httpx.Response(200, json={"keys": [public_jwk]})

        if url.endswith("/token?grant_type=password"):
            body = json.loads(request.content)
            email = body.get("email", "").lower()
            password = supabase_passwords.get(email)
            if password is None or password != body.get("password"):
                return httpx.Response(
                    400, json={"error": "invalid_grant", "error_description": "Invalid login credentials"}
                )
            for sub, record in supabase_users.items():
                if record["email"] == email:
                    token = mint_token(sub, email)
                    return httpx.Response(
                        200,
                        json={"access_token": token, "token_type": "bearer",
                              "expires_in": 3600, "user": record},
                    )
            return httpx.Response(400, json={"error": "invalid_grant"})

        if url.endswith("/signup"):
            body = json.loads(request.content)
            email = body.get("email", "").lower()
            if email in supabase_passwords:
                return httpx.Response(
                    422, json={"msg": "User already registered", "code": 400}
                )
            if len(body.get("password") or "") < 6:
                return httpx.Response(400, json={"msg": "Password should be at least 6 characters."})
            supabase_passwords[email] = body["password"]
            sub = str(uuid.uuid4())
            record = {"id": sub, "email": email, "email_confirmed_at": None}
            supabase_users[sub] = record
            # Auto-confirm signups so the session is issued immediately.
            record["email_confirmed_at"] = "2026-01-01T00:00:00Z"
            token = mint_token(sub, email)
            return httpx.Response(
                200, json={"access_token": token, "token_type": "bearer",
                           "expires_in": 3600, "user": record},
            )

        if url.endswith("/user"):
            access_token = request.headers["authorization"].removeprefix("Bearer ")
            claims = jwt.decode(
                access_token,
                options={"verify_signature": False, "verify_exp": False, "verify_aud": False},
            )
            record = supabase_users.get(claims["sub"], {})
            return httpx.Response(200, json=record)

        return httpx.Response(404, json={"error": "not found"})

    transport = httpx.MockTransport(handle)
    original = httpx.AsyncClient

    def patched(**kwargs):
        return original(transport=transport, **kwargs)

    monkeypatch.setattr(supabase_auth.httpx, "AsyncClient", patched)
    yield mint_token
    supabase_auth._JWKS_CACHE.clear()


# ── Login proxy ───────────────────────────────────────────────────────────────


def test_login_returns_supabase_token_when_configured(
    client: TestClient,
    mocked_supabase_auth,
    supabase_passwords: dict[str, str],
    supabase_users: dict[str, dict[str, Any]],
) -> None:
    """When Supabase is configured, /auth/login must return a Supabase JWT
    (with an iss claim), not a legacy HS256 token."""
    email = f"proxy-login-{uuid.uuid4().hex[:8]}@example.com"
    supabase_passwords[email] = "ProxyLogin123!"
    sub = str(uuid.uuid4())
    supabase_users[sub] = {
        "id": sub, "email": email, "email_confirmed_at": "2026-01-01T00:00:00Z"
    }

    r = client.post(LOGIN_URL, json={"email": email, "password": "ProxyLogin123!"})
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]

    unverified = jwt.decode(
        token, options={"verify_signature": False, "verify_exp": False, "verify_aud": False}
    )
    assert unverified.get("iss") == _ISSUER, "login must return a Supabase-issued token"


def test_login_rejects_wrong_password_with_401(
    client: TestClient,
    mocked_supabase_auth,
    supabase_passwords: dict[str, str],
) -> None:
    email = f"proxy-badpw-{uuid.uuid4().hex[:8]}@example.com"
    supabase_passwords[email] = "CorrectPassword123!"

    r = client.post(LOGIN_URL, json={"email": email, "password": "WrongPassword123!"})
    assert r.status_code == 401


def test_login_supabase_token_passes_production_gate(
    client: TestClient,
    mocked_supabase_auth,
    supabase_passwords: dict[str, str],
    supabase_users: dict[str, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The full demo/admin migration loop: login returns a Supabase token,
    and that token authenticates successfully even in production mode."""
    email = f"proxy-fullloop-{uuid.uuid4().hex[:8]}@example.com"
    supabase_passwords[email] = "ProxyLogin123!"
    sub = str(uuid.uuid4())
    supabase_users[sub] = {
        "id": sub, "email": email, "email_confirmed_at": "2026-01-01T00:00:00Z"
    }

    r = client.post(LOGIN_URL, json={"email": email, "password": "ProxyLogin123!"})
    assert r.status_code == 200
    token = r.json()["access_token"]

    monkeypatch.setattr(get_settings(), "ENVIRONMENT", "production")

    me = client.get(ME_URL, headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200, (
        f"Supabase token from the login proxy must pass the production gate; "
        f"got {me.status_code}: {me.text}"
    )
    assert me.json()["email"] == email


# ── Register proxy ────────────────────────────────────────────────────────────


def test_register_creates_supabase_and_jobyn_user(
    client: TestClient,
    mocked_supabase_auth,
) -> None:
    """POST /auth/register with Supabase configured must provision both the
    Supabase identity and a linked, passwordless Jobyn account."""
    email = f"proxy-reg-{uuid.uuid4().hex[:8]}@example.com"

    r = client.post(
        REGISTER_URL,
        json={"email": email, "password": "ProxyRegister123!", "full_name": "Proxy Test"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["email"] == email
    assert "hashed_password" not in body
    assert body["is_active"] is True

    # The returned token (from the signup session) authenticates /me.
    # Re-login to obtain a token.
    r_login = client.post(LOGIN_URL, json={"email": email, "password": "ProxyRegister123!"})
    assert r_login.status_code == 200
    token = r_login.json()["access_token"]

    me = client.get(ME_URL, headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["id"] == body["id"], "login must resolve the SAME Jobyn account created at signup"


def test_register_duplicate_email_returns_409(
    client: TestClient,
    mocked_supabase_auth,
    supabase_passwords: dict[str, str],
) -> None:
    email = f"proxy-dup-{uuid.uuid4().hex[:8]}@example.com"
    first = client.post(
        REGISTER_URL, json={"email": email, "password": "ProxyRegister123!"}
    )
    assert first.status_code == 201

    second = client.post(
        REGISTER_URL, json={"email": email, "password": "OtherPassword123!"}
    )
    assert second.status_code == 409


# ── Fallback when unconfigured ───────────────────────────────────────────────


def test_legacy_login_still_works_when_supabase_unconfigured(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without SUPABASE_URL, /auth/login falls back to the legacy flow so the
    test suite and local development are unaffected."""
    monkeypatch.setattr(get_settings(), "SUPABASE_URL", None)

    email = f"fallback-{uuid.uuid4().hex[:8]}@example.com"
    client.post(REGISTER_URL, json={"email": email, "password": "Fallback123!"})
    r = client.post(LOGIN_URL, json={"email": email, "password": "Fallback123!"})
    assert r.status_code == 200
    token = r.json()["access_token"]

    # Legacy token has no iss claim.
    unverified = jwt.decode(
        token, options={"verify_signature": False, "verify_exp": False, "verify_aud": False}
    )
    assert "iss" not in unverified

    # And it works in the test environment.
    me = client.get(ME_URL, headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
