"""Focused tests for Supabase token verification and account migration."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import jwt
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from backend.auth import supabase as supabase_auth
from backend.auth.jwt import InvalidTokenError, create_access_token
from backend.auth.password import hash_password
from backend.core.config import get_settings
from backend.core.errors import AuthenticationError
from backend.database.session import async_session_factory
from backend.models.user import User
from backend.models.user_profile import UserSettings
from backend.repositories.user import UserRepository
from backend.services.auth import (
    AuthService,
    InvalidCredentialsError,
    SupabaseIdentityConflictError,
)
from backend.services.user import UserService
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

_PROJECT_URL = "https://unit-test-project.supabase.co"
_ISSUER = f"{_PROJECT_URL}/auth/v1"
_KID = "unit-test-signing-key"
_HS_SECRET = "unit-test-supabase-jwt-secret-with-sufficient-length"


@pytest.fixture
def rsa_keys() -> tuple[Any, dict[str, Any]]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    public_jwk.update({"kid": _KID, "alg": "RS256", "use": "sig"})
    return private_key, public_jwk


@pytest.fixture
def mocked_jwks(
    monkeypatch: pytest.MonkeyPatch,
    rsa_keys: tuple[Any, dict[str, Any]],
) -> Iterator[None]:
    _, public_jwk = rsa_keys
    settings = get_settings()
    monkeypatch.setattr(settings, "SUPABASE_URL", _PROJECT_URL)
    monkeypatch.setattr(settings, "SUPABASE_JWT_SECRET", None)
    supabase_auth._JWKS_CACHE.clear()

    def handle_jwks(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == f"{_ISSUER}/.well-known/jwks.json"
        return httpx.Response(200, json={"keys": [public_jwk]})

    transport = httpx.MockTransport(handle_jwks)
    original_async_client = httpx.AsyncClient

    def mock_async_client(*, timeout: float) -> httpx.AsyncClient:
        return original_async_client(transport=transport, timeout=timeout)

    monkeypatch.setattr(supabase_auth.httpx, "AsyncClient", mock_async_client)
    yield
    supabase_auth._JWKS_CACHE.clear()


def _claims(subject: uuid.UUID | None = None, **overrides: Any) -> dict[str, Any]:
    claims: dict[str, Any] = {
        "sub": str(subject or uuid.uuid4()),
        "iss": _ISSUER,
        "aud": "authenticated",
        "iat": int(time.time()),
        "exp": int(time.time()) + 600,
        "email": "supabase@example.com",
        "email_verified": True,
        "role": "authenticated",
    }
    claims.update(overrides)
    return claims


def _token(
    private_key: Any,
    claims: dict[str, Any],
    *,
    kid: str = _KID,
    algorithm: str = "RS256",
) -> str:
    return jwt.encode(
        claims,
        private_key,
        algorithm=algorithm,
        headers={"kid": kid},
    )


@pytest.mark.asyncio
async def test_supabase_jwks_token_is_accepted(
    rsa_keys: tuple[Any, dict[str, Any]],
    mocked_jwks: None,
) -> None:
    private_key, _ = rsa_keys
    claims = _claims()

    verified = await supabase_auth.verify_supabase_access_token(_token(private_key, claims))

    assert verified["sub"] == claims["sub"]


@pytest.mark.asyncio
async def test_supabase_token_rejects_invalid_signature(
    rsa_keys: tuple[Any, dict[str, Any]],
    mocked_jwks: None,
) -> None:
    other_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = _token(other_private_key, _claims())

    with pytest.raises(InvalidTokenError):
        await supabase_auth.verify_supabase_access_token(token)


@pytest.mark.asyncio
async def test_supabase_token_rejects_expired_token(
    rsa_keys: tuple[Any, dict[str, Any]],
    mocked_jwks: None,
) -> None:
    private_key, _ = rsa_keys
    with pytest.raises(InvalidTokenError, match="expired"):
        await supabase_auth.verify_supabase_access_token(
            _token(private_key, _claims(exp=int(time.time()) - 1))
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"iss": "https://other-project.supabase.co/auth/v1"},
        {"aud": "another-audience"},
    ],
)
@pytest.mark.asyncio
async def test_supabase_token_rejects_wrong_issuer_or_audience(
    rsa_keys: tuple[Any, dict[str, Any]],
    mocked_jwks: None,
    overrides: dict[str, str],
) -> None:
    private_key, _ = rsa_keys
    claims = _claims()
    claims.update(overrides)

    with pytest.raises(InvalidTokenError):
        await supabase_auth.verify_supabase_access_token(_token(private_key, claims))


@pytest.mark.asyncio
async def test_supabase_token_rejects_unsupported_algorithm(
    mocked_jwks: None,
) -> None:
    token = jwt.encode(_claims(), "x" * 64, algorithm="HS512")

    with pytest.raises(InvalidTokenError, match="Unsupported"):
        await supabase_auth.verify_supabase_access_token(token)


@pytest.mark.asyncio
async def test_supabase_hs256_token_uses_only_configured_supabase_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pydantic import SecretStr

    settings = get_settings()
    monkeypatch.setattr(settings, "SUPABASE_URL", _PROJECT_URL)
    monkeypatch.setattr(settings, "SUPABASE_JWT_SECRET", SecretStr(_HS_SECRET))
    claims = _claims()
    token = jwt.encode(claims, _HS_SECRET, algorithm="HS256")

    verified = await supabase_auth.verify_supabase_access_token(token)

    assert verified["sub"] == claims["sub"]


@pytest.mark.asyncio
async def test_existing_jobyn_account_is_linked_without_changing_primary_key_or_data(
    db_session: AsyncSession,
) -> None:
    jobyn_id = uuid.uuid4()
    user = User(
        id=jobyn_id,
        email="legacy@example.com",
        hashed_password=hash_password("legacy-password"),
        full_name="Legacy User",
        is_active=True,
        is_verified=False,
        role="admin",
    )
    settings = UserSettings(user_id=jobyn_id, preferred_language="fr")
    db_session.add_all([user, settings])
    await db_session.commit()

    claims = _claims(
        email="LEGACY@example.com",
        app_metadata={"role": "admin"},
        user_metadata={"role": "admin"},
    )
    resolved = await AuthService(UserRepository(db_session, User)).resolve_supabase_user(claims)

    assert resolved.id == jobyn_id
    assert resolved.supabase_user_id == uuid.UUID(claims["sub"])
    assert resolved.full_name == "Legacy User"
    assert resolved.is_verified is False
    assert resolved.is_active is True
    assert resolved.role == "admin"
    assert settings.user_id == jobyn_id
    assert settings.preferred_language == "fr"


@pytest.mark.asyncio
async def test_existing_supabase_mapping_resolves_without_email_claims(
    db_session: AsyncSession,
) -> None:
    supabase_id = uuid.uuid4()
    user = User(
        email="mapped@example.com",
        hashed_password=None,
        supabase_user_id=supabase_id,
        is_active=True,
        is_verified=True,
        role="user",
    )
    db_session.add(user)
    await db_session.commit()

    resolved = await AuthService(UserRepository(db_session, User)).resolve_supabase_user(
        {"sub": str(supabase_id)}
    )

    assert resolved.id == user.id


@pytest.mark.asyncio
async def test_verified_new_identity_is_provisioned_as_passwordless_user(
    db_session: AsyncSession,
) -> None:
    claims = _claims(
        email="new@example.com",
        app_metadata={"role": "admin"},
        user_metadata={"role": "admin"},
        role="admin",
    )

    user = await AuthService(UserRepository(db_session, User)).resolve_supabase_user(claims)

    assert user.supabase_user_id == uuid.UUID(claims["sub"])
    assert user.email == "new@example.com"
    assert user.hashed_password is None
    assert user.is_verified is True
    assert user.is_active is True
    assert user.role == "user"


@pytest.mark.asyncio
async def test_unverified_supabase_email_is_rejected_without_provisioning(
    db_session: AsyncSession,
) -> None:
    claims = _claims(email="unverified@example.com", email_verified=False)
    service = AuthService(UserRepository(db_session, User))

    with pytest.raises(InvalidTokenError, match="verified"):
        await service.resolve_supabase_user(claims)

    assert await service.user_repository.count() == 0


@pytest.mark.asyncio
async def test_duplicate_email_identity_requires_manual_resolution(
    db_session: AsyncSession,
) -> None:
    existing = User(
        email="duplicate@example.com",
        hashed_password=hash_password("legacy-password"),
        supabase_user_id=uuid.uuid4(),
        is_active=True,
        is_verified=True,
        role="user",
    )
    db_session.add(existing)
    await db_session.commit()

    claims = _claims(email="duplicate@example.com")
    with pytest.raises(SupabaseIdentityConflictError):
        await AuthService(UserRepository(db_session, User)).resolve_supabase_user(claims)

    assert await db_session.scalar(select(func.count()).select_from(User)) == 1


@pytest.mark.asyncio
async def test_duplicate_supabase_uuid_is_rejected_by_database(
    db_session: AsyncSession,
) -> None:
    supabase_id = uuid.uuid4()
    db_session.add(
        User(
            email="first@example.com",
            hashed_password=None,
            supabase_user_id=supabase_id,
        )
    )
    await db_session.commit()
    db_session.add(
        User(
            email="second@example.com",
            hashed_password=None,
            supabase_user_id=supabase_id,
        )
    )

    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_concurrent_provisioning_is_idempotent(
    db_session: AsyncSession,
) -> None:
    claims = _claims(email="concurrent@example.com")

    async def provision() -> User:
        async with async_session_factory() as session:
            service = AuthService(UserRepository(session, User))
            return await service.resolve_supabase_user(claims)

    first, second = await asyncio.gather(provision(), provision())

    assert first.id == second.id
    assert first.supabase_user_id == second.supabase_user_id
    assert await db_session.scalar(select(func.count()).select_from(User)) == 1


@pytest.mark.asyncio
async def test_passwordless_supabase_users_cannot_use_legacy_password_login(
    db_session: AsyncSession,
) -> None:
    user = User(
        email="passwordless@example.com",
        hashed_password=None,
        supabase_user_id=uuid.uuid4(),
    )
    db_session.add(user)
    await db_session.commit()
    repository = UserRepository(db_session, User)

    with pytest.raises(InvalidCredentialsError):
        await AuthService(repository).authenticate_user(
            "passwordless@example.com",
            "any-password",
        )
    with pytest.raises(AuthenticationError):
        await UserService(repository).authenticate(
            "passwordless@example.com",
            "any-password",
        )


def test_api_accepts_supabase_token_and_metadata_cannot_grant_admin(
    client: TestClient,
    rsa_keys: tuple[Any, dict[str, Any]],
    mocked_jwks: None,
) -> None:
    private_key, _ = rsa_keys
    claims = _claims(
        email=f"{uuid.uuid4().hex}@example.com",
        app_metadata={"role": "admin"},
        user_metadata={"role": "admin"},
    )
    token = _token(private_key, claims)

    profile = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    admin_list = client.get(
        "/api/v1/admin/users",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert profile.status_code == 200
    assert admin_list.status_code == 403


def test_invalid_supabase_signature_is_not_downgraded_to_legacy_auth(
    client: TestClient,
    rsa_keys: tuple[Any, dict[str, Any]],
    mocked_jwks: None,
) -> None:
    other_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = _token(other_private_key, _claims())

    response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401


def test_legacy_signed_token_with_issuer_is_not_accepted_as_supabase(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pydantic import SecretStr

    monkeypatch.setattr(get_settings(), "SUPABASE_URL", _PROJECT_URL)
    monkeypatch.setattr(
        get_settings(),
        "SUPABASE_JWT_SECRET",
        SecretStr(_HS_SECRET),
    )
    legacy = create_access_token(
        str(uuid.uuid4()),
        additional_claims={"iss": _ISSUER, "aud": "authenticated"},
    )

    response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {legacy}"},
    )

    assert response.status_code == 401


def test_inactive_legacy_account_cannot_access_protected_endpoint(client: TestClient) -> None:
    email = f"inactive-{uuid.uuid4().hex}@example.com"
    assert (
        client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": "supersecret1"},
        ).status_code
        == 201
    )
    login = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "supersecret1"},
    )
    token = login.json()["access_token"]

    async def deactivate() -> None:
        async with async_session_factory() as session:
            user = await UserRepository(session, User).get_by_email(email)
            assert user is not None
            user.is_active = False
            await session.commit()

    asyncio.run(deactivate())
    response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403


def test_migration_adds_nullable_unique_mapping_without_changing_existing_ids() -> None:
    migration_path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "f4e8d2a6b1c9_add_supabase_identity_mapping.py"
    )
    spec = importlib.util.spec_from_file_location("supabase_identity_migration", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE users (
                id CHAR(32) PRIMARY KEY,
                email VARCHAR(255) NOT NULL UNIQUE,
                hashed_password VARCHAR(255) NOT NULL
            )
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TABLE user_settings (
                id CHAR(32) PRIMARY KEY,
                user_id CHAR(32) NOT NULL REFERENCES users(id),
                preferred_language VARCHAR(10) NOT NULL
            )
            """
        )
        jobyn_id = uuid.uuid4()
        connection.exec_driver_sql(
            "INSERT INTO users (id, email, hashed_password) VALUES (?, ?, ?)",
            (jobyn_id.hex, "old@example.com", "existing-hash"),
        )
        connection.exec_driver_sql(
            "INSERT INTO user_settings (id, user_id, preferred_language) VALUES (?, ?, ?)",
            (uuid.uuid4().hex, jobyn_id.hex, "fr"),
        )
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()

        assert connection.exec_driver_sql(
            "SELECT id, hashed_password FROM users WHERE email = 'old@example.com'"
        ).one() == (jobyn_id.hex, "existing-hash")
        assert (
            connection.exec_driver_sql(
                "SELECT preferred_language FROM user_settings WHERE user_id = ?",
                (jobyn_id.hex,),
            ).scalar_one()
            == "fr"
        )

        connection.exec_driver_sql(
            "INSERT INTO users (id, email, hashed_password, supabase_user_id) "
            "VALUES (?, ?, ?, NULL)",
            (uuid.uuid4().hex, "null-one@example.com", None),
        )
        connection.exec_driver_sql(
            "INSERT INTO users (id, email, hashed_password, supabase_user_id) "
            "VALUES (?, ?, ?, NULL)",
            (uuid.uuid4().hex, "null-two@example.com", None),
        )
        mapped_id = uuid.uuid4()
        connection.exec_driver_sql(
            "INSERT INTO users (id, email, hashed_password, supabase_user_id) VALUES (?, ?, ?, ?)",
            (uuid.uuid4().hex, "mapped@example.com", None, mapped_id.hex),
        )
        with pytest.raises(IntegrityError):
            connection.exec_driver_sql(
                "INSERT INTO users (id, email, hashed_password, supabase_user_id) "
                "VALUES (?, ?, ?, ?)",
                (uuid.uuid4().hex, "duplicate-map@example.com", None, mapped_id.hex),
            )
