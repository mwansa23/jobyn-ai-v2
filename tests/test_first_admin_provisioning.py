"""Focused tests for repository-local first-administrator validation."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pydantic import ValidationError

import backend.scripts.provision_first_admin as provision_module
from backend.auth.password import verify_password
from backend.schemas.user import UserCreate
from backend.scripts.provision_first_admin import (
    _normalize_email,
    _validate_password,
    provision_first_admin,
    validate_local_sqlite_database,
)


@pytest.fixture(autouse=True)
def reset_test_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep every test in the isolated test environment and never in the production target."""
    monkeypatch.setenv("ENVIRONMENT", "test")


def test_repository_local_sqlite_path_is_accepted() -> None:
    expected = (Path(__file__).resolve().parents[1] / "jobyn.db").resolve()
    resolved = validate_local_sqlite_database(f"sqlite+aiosqlite:///{expected.as_posix()}")
    assert resolved == expected


def test_alternative_sqlite_path_is_rejected() -> None:
    with pytest.raises(RuntimeError, match="repository-local SQLite database"):
        validate_local_sqlite_database("sqlite+aiosqlite:///tmp/other.db")


def test_active_engine_database_is_checked_before_provisioning() -> None:
    with pytest.raises(RuntimeError, match="repository-local SQLite database"):
        validate_local_sqlite_database(str(provision_module.engine.url))


def test_postgresql_database_is_rejected() -> None:
    with pytest.raises(RuntimeError, match="Only SQLite databases are supported"):
        validate_local_sqlite_database("postgresql+asyncpg://user:pass@localhost:5432/jobyn")


def test_in_memory_sqlite_is_rejected() -> None:
    with pytest.raises(RuntimeError, match="file-backed SQLite database"):
        validate_local_sqlite_database("sqlite+aiosqlite:///:memory:")


def test_production_provisioning_rejects_test_environment() -> None:
    with pytest.raises(RuntimeError, match="test environment"):
        asyncio.run(provision_first_admin("first-admin@example.com", "StrongPass!123"))


def test_password_policy_matches_application_rules() -> None:
    assert _validate_password("StrongPass!123") == "StrongPass!123"
    with pytest.raises(ValueError):
        _validate_password("short")
    with pytest.raises(ValueError):
        _validate_password("   ")
    with pytest.raises(ValidationError):
        UserCreate(email="example.com", password="StrongPass!123")

    assert verify_password("StrongPass!123", provision_module.hash_password("StrongPass!123")) is True


def test_normalized_email_is_valid_and_lowercase() -> None:
    assert _normalize_email(" First-Admin@Example.com ") == "first-admin@example.com"
