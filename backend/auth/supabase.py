"""Verification for Supabase Auth access tokens."""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

import httpx
import jwt
from backend.auth.jwt import InvalidTokenError
from backend.core.config import get_settings
from fastapi import HTTPException, status

_JWKS_CACHE_TTL_SECONDS = 300
_ASYMMETRIC_ALGORITHMS = frozenset({"ES256", "RS256", "EdDSA"})
_JWKS_CACHE: dict[str, tuple[float, dict[str, dict[str, Any]]]] = {}


class SupabaseVerificationUnavailable(HTTPException):
    """Raised when required Supabase verification configuration or keys are unavailable."""

    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Supabase token verification is temporarily unavailable.",
        )


def has_issuer_claim(token: str) -> bool:
    """Route issuer-bearing tokens to Supabase verification without accepting claims."""
    try:
        claims = jwt.decode(
            token,
            options={
                "verify_signature": False,
                "verify_exp": False,
                "verify_aud": False,
            },
        )
    except jwt.PyJWTError as exc:
        raise InvalidTokenError() from exc
    if not isinstance(claims, dict):
        raise InvalidTokenError()
    return "iss" in claims


def _project_urls() -> tuple[str, str]:
    project_url = get_settings().SUPABASE_URL
    if not project_url:
        raise SupabaseVerificationUnavailable()

    parsed = urlparse(project_url)
    local_http = (
        parsed.scheme == "http"
        and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        and get_settings().ENVIRONMENT in {"development", "test"}
    )
    if (
        parsed.hostname is None
        or (parsed.scheme != "https" and not local_http)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise SupabaseVerificationUnavailable()

    issuer = f"{project_url.rstrip('/')}/auth/v1"
    return issuer, f"{issuer}/.well-known/jwks.json"


async def _fetch_jwks(url: str) -> dict[str, dict[str, Any]]:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise SupabaseVerificationUnavailable() from exc

    if not isinstance(payload, dict) or not isinstance(payload.get("keys"), list):
        raise SupabaseVerificationUnavailable()

    keys: dict[str, dict[str, Any]] = {}
    for key in payload["keys"]:
        if not isinstance(key, dict) or not isinstance(key.get("kid"), str):
            continue
        if key["kid"] in keys:
            raise SupabaseVerificationUnavailable()
        keys[key["kid"]] = key
    if not keys:
        raise SupabaseVerificationUnavailable()
    return keys


async def fetch_supabase_user(access_token: str) -> dict[str, Any]:
    """Fetch the authenticated Supabase user for an access token."""
    settings = get_settings()
    issuer, _ = _project_urls()
    publishable_key = settings.SUPABASE_PUBLISHABLE_KEY
    if publishable_key is None or not publishable_key.get_secret_value().strip():
        raise SupabaseVerificationUnavailable()

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                f"{issuer}/user",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "apikey": publishable_key.get_secret_value(),
                },
            )
    except httpx.HTTPError as exc:
        raise SupabaseVerificationUnavailable() from exc

    if response.status_code in {401, 403}:
        raise InvalidTokenError(detail="Invalid Supabase access token.")
    if not response.is_success:
        raise SupabaseVerificationUnavailable()

    try:
        payload = response.json()
    except ValueError as exc:
        raise SupabaseVerificationUnavailable() from exc
    if not isinstance(payload, dict):
        raise SupabaseVerificationUnavailable()
    return payload


async def _jwks_for_key(url: str, kid: str) -> dict[str, Any] | None:
    cached = _JWKS_CACHE.get(url)
    if cached is None or cached[0] <= time.monotonic():
        refreshed = True
        keys = await _fetch_jwks(url)
        _JWKS_CACHE[url] = (time.monotonic() + _JWKS_CACHE_TTL_SECONDS, keys)
    else:
        refreshed = False
        keys = cached[1]

    if kid not in keys and not refreshed:
        keys = await _fetch_jwks(url)
        _JWKS_CACHE[url] = (time.monotonic() + _JWKS_CACHE_TTL_SECONDS, keys)
    return keys.get(kid)


async def verify_supabase_access_token(token: str) -> dict[str, Any]:
    """Verify a Supabase token's signature and required standard claims."""
    settings = get_settings()
    issuer, jwks_url = _project_urls()

    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise InvalidTokenError() from exc

    algorithm = header.get("alg")
    if algorithm == "HS256":
        secret = settings.SUPABASE_JWT_SECRET
        if secret is None or len(secret.get_secret_value().encode()) < 32:
            raise SupabaseVerificationUnavailable()
        signing_key: Any = secret.get_secret_value()
    elif algorithm in _ASYMMETRIC_ALGORITHMS:
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise InvalidTokenError(detail="Invalid Supabase token signing key.")
        jwk = await _jwks_for_key(jwks_url, kid)
        if (
            jwk is None
            or jwk.get("alg") not in (None, algorithm)
            or jwk.get("use") not in (None, "sig")
            or (isinstance(jwk.get("key_ops"), list) and "verify" not in jwk["key_ops"])
        ):
            raise InvalidTokenError(detail="Invalid Supabase token signing key.")
        try:
            signing_key = jwt.PyJWK.from_dict(jwk, algorithm=algorithm).key
        except (jwt.PyJWTError, TypeError, ValueError) as exc:
            raise InvalidTokenError(detail="Invalid Supabase token signing key.") from exc
    else:
        raise InvalidTokenError(detail="Unsupported Supabase token signing method.")

    try:
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=[algorithm],
            audience="authenticated",
            issuer=issuer,
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise InvalidTokenError(detail="Supabase access token has expired.") from exc
    except (jwt.PyJWTError, TypeError, ValueError) as exc:
        raise InvalidTokenError(detail="Invalid Supabase access token.") from exc

    try:
        UUID(claims["sub"])
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidTokenError(detail="Invalid Supabase subject.") from exc
    return claims


# ---------------------------------------------------------------------------
# Supabase Auth API client (login / signup proxy)
# ---------------------------------------------------------------------------


def supabase_auth_configured() -> bool:
    """Return True when the Supabase password-grant proxy can be used.

    Requires both ``SUPABASE_URL`` and ``SUPABASE_PUBLISHABLE_KEY`` to be
    set.  In development and test environments these are typically unset,
    in which case the legacy email/password flow remains in place.
    """
    settings = get_settings()
    if not settings.SUPABASE_URL:
        return False
    publishable_key = settings.SUPABASE_PUBLISHABLE_KEY
    if publishable_key is None or not publishable_key.get_secret_value().strip():
        return False
    return True


class SupabaseAuthRejected(HTTPException):
    """Raised when the Supabase Auth API rejects credentials."""

    def __init__(self, detail: str) -> None:
        super().__init__(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


class SupabaseEmailAlreadyRegistered(HTTPException):
    """Raised when the Supabase Auth API reports a duplicate signup."""

    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email address already exists.",
        )


async def supabase_password_grant(email: str, password: str) -> dict[str, Any]:
    """Authenticate email+password against Supabase Auth (password grant).

    Returns:
        The Supabase token response containing ``access_token``,
        ``refresh_token`` and ``expires_in``.

    Raises:
        SupabaseAuthRejected: Invalid credentials (401 from Supabase).
        SupabaseVerificationUnavailable: Network or configuration failure.
    """
    issuer, _ = _project_urls()
    settings = get_settings()
    publishable_key = settings.SUPABASE_PUBLISHABLE_KEY
    if publishable_key is None or not publishable_key.get_secret_value().strip():
        raise SupabaseVerificationUnavailable()

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{issuer}/token?grant_type=password",
                headers={"apikey": publishable_key.get_secret_value()},
                json={"email": email, "password": password},
            )
    except httpx.HTTPError as exc:
        raise SupabaseVerificationUnavailable() from exc

    if response.status_code in {400, 401, 403}:
        raise SupabaseAuthRejected(detail="Invalid email or password.")
    if not response.is_success:
        raise SupabaseVerificationUnavailable()

    try:
        payload = response.json()
    except ValueError as exc:
        raise SupabaseVerificationUnavailable() from exc
    if not isinstance(payload, dict) or not payload.get("access_token"):
        raise SupabaseVerificationUnavailable()
    return payload


async def supabase_signup(
    email: str,
    password: str,
    full_name: str | None = None,
) -> dict[str, Any]:
    """Create a user in Supabase Auth via the signup endpoint.

    Returns:
        The Supabase signup response.  When email confirmation is disabled
        the response includes a session (``access_token``); otherwise it
        contains only the pending ``user`` object.

    Raises:
        SupabaseEmailAlreadyRegistered: The email already exists (422).
        SupabaseAuthRejected: Password policy rejection (400).
        SupabaseVerificationUnavailable: Network or configuration failure.
    """
    issuer, _ = _project_urls()
    settings = get_settings()
    publishable_key = settings.SUPABASE_PUBLISHABLE_KEY
    if publishable_key is None or not publishable_key.get_secret_value().strip():
        raise SupabaseVerificationUnavailable()

    body: dict[str, Any] = {"email": email, "password": password}
    if full_name:
        body["data"] = {"full_name": full_name}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{issuer}/signup",
                headers={"apikey": publishable_key.get_secret_value()},
                json=body,
            )
    except httpx.HTTPError as exc:
        raise SupabaseVerificationUnavailable() from exc

    if response.status_code == 422:
        raise SupabaseEmailAlreadyRegistered()
    if response.status_code == 400:
        try:
            message = response.json().get("msg") or response.json().get("error")
        except ValueError:
            message = None
        raise SupabaseAuthRejected(
            detail=message or "Password does not meet the security policy."
        )
    if not response.is_success:
        raise SupabaseVerificationUnavailable()

    try:
        payload = response.json()
    except ValueError as exc:
        raise SupabaseVerificationUnavailable() from exc
    if not isinstance(payload, dict):
        raise SupabaseVerificationUnavailable()
    return payload
