"""Provision the first administrator for the repository-local SQLite database."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
from pathlib import Path

from pydantic import EmailStr, TypeAdapter
from sqlalchemy import select, text
from sqlalchemy.engine import make_url

from backend.auth.password import hash_password
from backend.core.config import get_settings
from backend.database.session import async_session_maker, engine
from backend.models.user import User

_ADMIN_EMAIL_ENV = "JOBYN_FIRST_ADMIN_EMAIL"
_REPO_ROOT = Path(__file__).resolve().parents[2]
_EXPECTED_LOCAL_DB_PATH = (_REPO_ROOT / "jobyn.db").resolve()


def _normalize_email(email: str) -> str:
    """Validate and normalize an administrator email."""
    normalized = TypeAdapter(EmailStr).validate_python(email)
    return str(normalized).strip().lower()


def _resolve_sqlite_database_path(database_url: str) -> Path:
    """Resolve the effective SQLite file path using the same semantics as SQLAlchemy."""
    parsed = make_url(database_url)
    if parsed.get_backend_name() != "sqlite":
        raise RuntimeError("Only SQLite databases are supported for first-admin provisioning.")

    database_name = parsed.database
    if database_name in (None, "", ":memory:"):
        raise RuntimeError("First-admin provisioning requires a file-backed SQLite database.")

    candidate = Path(database_name)
    if not candidate.is_absolute():
        candidate = (Path.cwd() / candidate).resolve()
    else:
        candidate = candidate.resolve()
    return candidate


def _get_effective_engine_database_path() -> Path:
    engine_url = getattr(engine, "url", None)
    if engine_url is None:
        raise RuntimeError("Could not determine the active database URL from the SQLAlchemy engine.")

    try:
        return _resolve_sqlite_database_path(str(engine_url))
    except Exception as exc:  # pragma: no cover - defensive guard for unsupported engine states
        raise RuntimeError(
            "First-admin provisioning requires the active SQLAlchemy engine to use a file-backed SQLite database."
        ) from exc


def validate_local_sqlite_database(database_url: str | None = None) -> Path:
    """Require the selected SQLite target to resolve to the repository-local database."""
    if database_url is not None:
        resolved_path = _resolve_sqlite_database_path(database_url)
    else:
        resolved_path = _get_effective_engine_database_path()

    if resolved_path != _EXPECTED_LOCAL_DB_PATH:
        raise RuntimeError(
            "First-admin provisioning is restricted to the repository-local SQLite database. "
            f"Refusing to provision against: {resolved_path}."
        )

    return resolved_path


def _ensure_provisioning_is_allowed_for_runtime() -> None:
    """The production bootstrap only runs outside the test environment."""
    if os.getenv("ENVIRONMENT", "development").lower() == "test":
        raise RuntimeError("First-admin provisioning is disabled in the test environment.")


def _validate_password(password: str) -> str:
    """Match the application's actual registration policy: 8-128 characters, non-empty."""
    if password is None:
        raise ValueError("A first-administrator password is required.")

    if not isinstance(password, str) or not password.strip():
        raise ValueError("A first-administrator password is required.")

    if len(password) < 8 or len(password) > 128:
        raise ValueError("The administrator password must be between 8 and 128 characters long.")

    return password


def _read_email_and_password(*, email: str | None) -> tuple[str, str]:
    resolved_email = (email or os.getenv(_ADMIN_EMAIL_ENV) or "").strip()
    if not resolved_email:
        resolved_email = input("First administrator email: ").strip()

    if not resolved_email:
        raise ValueError("A first-administrator email is required.")

    resolved_password = getpass.getpass("First administrator password: ")
    resolved_password = _validate_password(resolved_password)
    return _normalize_email(resolved_email), resolved_password


async def provision_first_admin(email: str, password: str) -> User | None:
    """Create the first admin only when no admin exists yet."""
    _ensure_provisioning_is_allowed_for_runtime()
    validate_local_sqlite_database()
    normalized_email = _normalize_email(email)
    password = _validate_password(password)

    async with async_session_maker() as session:
        try:
            async with session.begin():
                try:
                    await session.execute(text("SELECT role FROM users LIMIT 1"))
                except Exception as exc:  # pragma: no cover - schema guard exercised in runtime use
                    raise RuntimeError(
                        "The local database is missing the users.role migration. "
                        "Run 'python -m alembic upgrade head' before provisioning the first administrator."
                    ) from exc

                admin = (
                    await session.execute(
                        select(User)
                        .where(User.role == "admin", User.deleted_at.is_(None))
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if admin is not None:
                    raise RuntimeError(
                        "An administrator already exists. Refusing to create another first administrator."
                    )

                existing_user = (
                    await session.execute(
                        select(User)
                        .where(User.email == normalized_email, User.deleted_at.is_(None))
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if existing_user is not None:
                    return None

                user = User(
                    email=normalized_email,
                    hashed_password=hash_password(password),
                    full_name=None,
                    is_active=True,
                    is_verified=True,
                    role="admin",
                )
                session.add(user)

            await session.refresh(user)
            return user
        except RuntimeError:
            raise
        except Exception:
            await session.rollback()
            raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Provision the first administrator on the repository-local SQLite database.",
    )
    parser.add_argument(
        "--email",
        help="Optional administrator email. If omitted, JOBYN_FIRST_ADMIN_EMAIL or an interactive prompt is used.",
    )
    return parser


async def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        email, password = _read_email_and_password(email=args.email)
        created = await provision_first_admin(email, password)
    except Exception as exc:  # pragma: no cover - CLI failure path is intentionally simple
        print(f"First administrator provisioning failed: {exc}", file=sys.stderr)
        return 1

    if created is None:
        print(f"The account for '{email}' already exists and was left unchanged.")
        return 0

    print(f"Provisioned first administrator: {created.email}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
