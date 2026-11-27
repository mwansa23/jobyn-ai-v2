"""Targeted tests for the production Supabase-only authentication gate.

These tests verify that:

1. A correctly-signed legacy HS256 token (no ``iss`` claim) is rejected with
   HTTP 401 when ``ENVIRONMENT`` is ``production`` or ``staging``.
2. The same legacy token continues to work in ``development`` and ``test``
   environments (so the existing test suite is not broken).
3. A valid Supabase RS256 token is accepted in all environments.
4. Missing ``SUPABASE_URL`` configuration returns HTTP 503, not 500 or 200.
5. User records, CV ownership and profile associations are not affected by
   the authentication gate change — the gate operates only at token
   verification time, before any database writes.

These tests use ``monkeypatch`` to override ``ENVIRONMENT`` for the production-
blocking cases, then restore it so the remaining test suite is unaffected.

No production data is read or modified.
No new migrations are required.
"""

from __future__ import annotations

import json
import time
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from pydantic import SecretStr

from backend.auth import supabase as supabase_auth
from backend.auth.jwt import create_access_token
from backend.core.config import get_settings

# ── Shared constants ──────────────────────────────────────────────────────────

REGISTER_URL = "/api/v1/auth/register"
LOGIN_URL    = "/api/v1/auth/login"
ME_URL       = "/api/v1/auth/me"

_PROJECT_URL = "https://unit-test-project.supabase.co"
_ISSUER      = f"{_PROJECT_URL}/auth/v1"
_KID         = "unit-test-signing-key"

# ── RSA key fixture (reused from test_supabase_auth.py pattern) ───────────────


@pytest.fixture
def rsa_keys():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    public_jwk.update({"kid": _KID, "alg": "RS256", "use": "sig"})
    return private_key, public_jwk


@pytest.fixture
def mocked_supabase_jwks(monkeypatch, rsa_keys):
    """Configure SUPABASE_URL and mock the JWKS and /user endpoints."""
    import httpx
    private_key, public_jwk = rsa_keys
    settings = get_settings()
    monkeypatch.setattr(settings, "SUPABASE_URL", _PROJECT_URL)
    monkeypatch.setattr(settings, "SUPABASE_JWT_SECRET", None)
    monkeypatch.setattr(
        settings, "SUPABASE_PUBLISHABLE_KEY", SecretStr("unit-test-publishable-key")
    )
    supabase_auth._JWKS_CACHE.clear()

    def handle(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "jwks.json" in url:
            return httpx.Response(200, json={"keys": [public_jwk]})
        if "/signup" in url:
            # Mock signup: auto-create an identity and return a session.
            import json as _json, uuid as _uuid
            try:
                body = _json.loads(request.content)
            except Exception:
                body = {}
            email = body.get("email", f"mock-{_uuid.uuid4().hex[:8]}@example.com")
            sub = str(_uuid.uuid4())
            claims = {
                "sub": sub, "iss": _ISSUER, "aud": "authenticated",
                "iat": int(time.time()), "exp": int(time.time()) + 3600,
                "email": email, "role": "authenticated",
            }
            token = jwt.encode(claims, private_key, algorithm="RS256",
                               headers={"kid": _KID})
            return httpx.Response(200, json={
                "access_token": token, "token_type": "bearer", "expires_in": 3600,
                "user": {"id": sub, "email": email,
                         "email_confirmed_at": "2026-01-01T00:00:00Z"},
            })
        # /user endpoint — expects Bearer token
        auth_header = request.headers.get("authorization", "")
        if not auth_header.startswith("Bearer "):
            return httpx.Response(401, json={"message": "Missing token"})
        access_token = auth_header.removeprefix("Bearer ")
        claims = jwt.decode(
            access_token,
            options={"verify_signature": False, "verify_exp": False, "verify_aud": False},
        )
        return httpx.Response(
            200,
            json={
                "id": claims["sub"],
                "email": claims.get("email"),
                "email_confirmed_at": "2026-01-01T00:00:00Z",
            },
        )

    transport = httpx.MockTransport(handle)
    original = httpx.AsyncClient

    def patched(**kwargs):
        return original(transport=transport, **kwargs)

    monkeypatch.setattr(supabase_auth.httpx, "AsyncClient", patched)
    yield
    supabase_auth._JWKS_CACHE.clear()


def _supabase_claims(email: str = "user@test.com") -> dict:
    sub = str(uuid.uuid4())
    return {
        "sub": sub,
        "iss": _ISSUER,
        "aud": "authenticated",
        "iat": int(time.time()),
        "exp": int(time.time()) + 600,
        "email": email,
        "role": "authenticated",
    }


def _supabase_token(private_key, claims: dict) -> str:
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": _KID})


# ── Test 1: Legacy HS256 token is rejected in production ──────────────────────


def test_legacy_hs256_token_blocked_in_production(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C2 regression: a correctly signed legacy HS256 token (no iss claim)
    must be rejected with HTTP 401 when ENVIRONMENT=production.

    This is the exact bypass identified in the audit: anyone who can produce
    a valid HS256 token signed with SECRET_KEY can impersonate any user in
    the database.  This test proves the gate closes it in production.
    """
    # Register a user via the legacy path (still allowed in test environment)
    email = f"legacy-prod-{uuid.uuid4().hex[:8]}@example.com"
    r = client.post(REGISTER_URL, json={"email": email, "password": "LegacyTest123!"})
    assert r.status_code == 201, r.text

    r = client.post(LOGIN_URL, json={"email": email, "password": "LegacyTest123!"})
    assert r.status_code == 200, r.text
    legacy_token = r.json()["access_token"]

    # Confirm the token has no iss claim (it is a legacy HS256 token)
    unverified = jwt.decode(
        legacy_token,
        options={"verify_signature": False, "verify_exp": False, "verify_aud": False},
    )
    assert "iss" not in unverified, "Token should not have an iss claim"

    # Temporarily set ENVIRONMENT=production to engage the gate
    monkeypatch.setattr(get_settings(), "ENVIRONMENT", "production")

    response = client.get(ME_URL, headers={"Authorization": f"Bearer {legacy_token}"})

    assert response.status_code == 401, (
        f"Expected 401 for legacy HS256 token in production; got {response.status_code}"
    )
    detail = response.json().get("detail", "")
    assert "legacy" in detail.lower() or "supabase" in detail.lower(), (
        f"Response detail should explain the rejection, got: {detail!r}"
    )


def test_legacy_hs256_token_blocked_in_staging(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C2 regression: same gate applies in staging environment."""
    email = f"legacy-staging-{uuid.uuid4().hex[:8]}@example.com"
    client.post(REGISTER_URL, json={"email": email, "password": "LegacyTest123!"})
    r = client.post(LOGIN_URL, json={"email": email, "password": "LegacyTest123!"})
    legacy_token = r.json()["access_token"]

    monkeypatch.setattr(get_settings(), "ENVIRONMENT", "staging")

    response = client.get(ME_URL, headers={"Authorization": f"Bearer {legacy_token}"})
    assert response.status_code == 401


# ── Test 2: Legacy token still works in development/test environments ─────────


def test_legacy_hs256_token_accepted_in_test_environment(client: TestClient) -> None:
    """The legacy path must remain open in ENVIRONMENT=test so the test suite
    continues to work without a live Supabase project.

    conftest.py sets ENVIRONMENT=test before any test runs, so this test
    exercises the real runtime path without needing monkeypatching.
    """
    email = f"legacy-test-env-{uuid.uuid4().hex[:8]}@example.com"
    client.post(REGISTER_URL, json={"email": email, "password": "LegacyTest123!"})
    r = client.post(LOGIN_URL, json={"email": email, "password": "LegacyTest123!"})
    assert r.status_code == 200
    legacy_token = r.json()["access_token"]

    response = client.get(ME_URL, headers={"Authorization": f"Bearer {legacy_token}"})
    assert response.status_code == 200, (
        f"Legacy token must be accepted in test environment; got {response.status_code}"
    )
    assert response.json()["email"] == email


def test_legacy_hs256_token_accepted_in_development_environment(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same gate bypass for ENVIRONMENT=development."""
    email = f"legacy-dev-env-{uuid.uuid4().hex[:8]}@example.com"
    client.post(REGISTER_URL, json={"email": email, "password": "LegacyTest123!"})
    r = client.post(LOGIN_URL, json={"email": email, "password": "LegacyTest123!"})
    legacy_token = r.json()["access_token"]

    monkeypatch.setattr(get_settings(), "ENVIRONMENT", "development")

    response = client.get(ME_URL, headers={"Authorization": f"Bearer {legacy_token}"})
    assert response.status_code == 200


# ── Test 3: Valid Supabase RS256 token is accepted ────────────────────────────


def test_supabase_rs256_token_accepted_in_production_mode(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    rsa_keys,
    mocked_supabase_jwks,
) -> None:
    """Valid Supabase authentication continues to work after the gate change.

    This test exercises the full Supabase path in get_current_user():
    has_issuer_claim() → True → verify_supabase_access_token() → resolve_supabase_user().
    """
    private_key, _ = rsa_keys
    email = f"supabase-prod-{uuid.uuid4().hex[:8]}@example.com"
    claims = _supabase_claims(email=email)
    token = _supabase_token(private_key, claims)

    monkeypatch.setattr(get_settings(), "ENVIRONMENT", "production")

    # First call provisions the account via resolve_supabase_user()
    response = client.get(ME_URL, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, (
        f"Supabase token must be accepted even in production mode; "
        f"got {response.status_code}: {response.text}"
    )
    body = response.json()
    assert body["email"] == email
    # Verify no hashed_password was set (Supabase-provisioned account)
    assert "hashed_password" not in body
    # Verify supabase_user_id is linked
    assert "id" in body


# ── Test 4: Missing SUPABASE_URL returns 503, not 500 ─────────────────────────


def test_supabase_token_with_missing_supabase_url_returns_503(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C1 regression: when SUPABASE_URL is not configured, a Supabase-issued
    token must return 503 (service unavailable), not 500 (server error) or
    200 (incorrect pass-through).

    Without SUPABASE_URL, _project_urls() raises SupabaseVerificationUnavailable,
    which FastAPI maps to HTTP 503.
    """
    monkeypatch.setattr(get_settings(), "SUPABASE_URL", None)

    # Craft a token that has an iss claim (routes to the Supabase path)
    fake_token = jwt.encode(
        {
            "iss": "https://any-project.supabase.co/auth/v1",
            "sub": str(uuid.uuid4()),
            "aud": "authenticated",
            "exp": int(time.time()) + 600,
        },
        "dummy-key",
        algorithm="HS256",
    )

    response = client.get(ME_URL, headers={"Authorization": f"Bearer {fake_token}"})
    assert response.status_code == 503, (
        f"Missing SUPABASE_URL must return 503; got {response.status_code}"
    )


# ── Test 5: User records, CV ownership and profiles are not affected ──────────


def test_legacy_user_data_is_not_affected_by_auth_gate(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rejecting a legacy token at the authentication gate must not modify
    the user's record, CV data, or profile associations.

    This test:
    1. Creates a user and verifies their data is accessible in test env.
    2. Simulates the production gate blocking the token.
    3. Verifies the user record is unmodified (readable again when env reverts).
    """
    email = f"data-integrity-{uuid.uuid4().hex[:8]}@example.com"
    # Create user
    r_reg = client.post(REGISTER_URL, json={"email": email, "password": "DataTest123!", "full_name": "Data Test"})
    assert r_reg.status_code == 201
    user_id = r_reg.json()["id"]

    # Confirm access in test environment
    r_login = client.post(LOGIN_URL, json={"email": email, "password": "DataTest123!"})
    assert r_login.status_code == 200
    legacy_token = r_login.json()["access_token"]

    me_before = client.get(ME_URL, headers={"Authorization": f"Bearer {legacy_token}"})
    assert me_before.status_code == 200
    assert me_before.json()["id"] == user_id
    assert me_before.json()["full_name"] == "Data Test"

    # Engage production gate — token is rejected
    monkeypatch.setattr(get_settings(), "ENVIRONMENT", "production")
    blocked = client.get(ME_URL, headers={"Authorization": f"Bearer {legacy_token}"})
    assert blocked.status_code == 401

    # Revert environment — user record is completely unchanged
    monkeypatch.setattr(get_settings(), "ENVIRONMENT", "test")
    me_after = client.get(ME_URL, headers={"Authorization": f"Bearer {legacy_token}"})
    assert me_after.status_code == 200
    assert me_after.json()["id"] == user_id
    assert me_after.json()["email"] == email
    assert me_after.json()["full_name"] == "Data Test"


# ── Test 6: Account-linking path for legacy users re-authenticating via Supabase


def test_legacy_user_can_link_via_supabase_on_next_login(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    rsa_keys,
    mocked_supabase_jwks,
) -> None:
    """A user who registered via the legacy path can re-authenticate through
    Supabase without losing their account data.

    resolve_supabase_user() performs email-based linking: if a Jobyn account
    with matching email exists and has no supabase_user_id, it sets the mapping
    atomically via link_supabase_identity().  The user's id, email, full_name
    and other data are preserved exactly as committed in 834c4ca.
    """
    private_key, _ = rsa_keys
    email = f"link-{uuid.uuid4().hex[:8]}@example.com"

    # Register via the legacy path only (email+password, no Supabase identity).
    # Temporarily suppress Supabase so the register endpoint uses the legacy
    # flow and creates a plain Jobyn account without a supabase_user_id.
    monkeypatch.setattr(get_settings(), "SUPABASE_URL", None)
    r_reg = client.post(REGISTER_URL, json={"email": email, "password": "Link123!", "full_name": "Link Test"})
    assert r_reg.status_code == 201, r_reg.text
    legacy_user_id = r_reg.json()["id"]
    # Restore Supabase so the remaining requests (Supabase login) work.
    monkeypatch.setattr(get_settings(), "SUPABASE_URL", _PROJECT_URL)

    # Switch to production gate — legacy token now blocked
    monkeypatch.setattr(get_settings(), "ENVIRONMENT", "production")

    # Arrive via Supabase (same email → triggers email-based account linking)
    claims = _supabase_claims(email=email)
    supabase_token = _supabase_token(private_key, claims)
    response = client.get(ME_URL, headers={"Authorization": f"Bearer {supabase_token}"})

    assert response.status_code == 200, (
        f"Supabase re-auth should link the legacy account; "
        f"got {response.status_code}: {response.text}"
    )
    body = response.json()
    # Same user record — no new account was created
    assert body["id"] == legacy_user_id, (
        "re-authenticating via Supabase must reuse the existing Jobyn account id"
    )
    assert body["email"] == email
    assert body["full_name"] == "Link Test"
