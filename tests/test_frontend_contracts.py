"""API response contracts consumed by the Jobyn frontend."""

from __future__ import annotations

import io
import uuid
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest_asyncio
from backend.main import create_app
from backend.schemas.resume import ParsedResumeRead
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pypdf import PdfWriter

_REGISTER_URL = "/api/v1/auth/register"
_LOGIN_URL = "/api/v1/auth/login"
_ME_URL = "/api/v1/auth/me"
_JOBS_URL = "/api/v1/jobs"
_APPLICATIONS_URL = "/api/v1/applications"
_RESUME_UPLOAD_URL = "/api/v1/resume/upload"
_PASSWORD = "frontend-contract-test-password"
_MOCK_PARSE_RESULT: dict[str, Any] = {
    "name": "Contract Test",
    "skills": ["Python", "FastAPI"],
}


@pytest_asyncio.fixture
async def async_client(db_session) -> AsyncIterator[AsyncClient]:
    """Provide an async HTTP client with an isolated test database schema."""
    app: FastAPI = create_app()
    async with (
        app.router.lifespan_context(app),
        AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client,
    ):
        yield client


async def _register_and_login(client: AsyncClient) -> tuple[str, str]:
    email = f"frontend-contract-{uuid.uuid4().hex}@example.com"
    register = await client.post(
        _REGISTER_URL,
        json={"email": email, "password": _PASSWORD, "full_name": "Frontend Contract"},
    )
    assert register.status_code == 201, register.text

    login = await client.post(
        _LOGIN_URL,
        data={"username": email, "password": _PASSWORD},
    )
    assert login.status_code == 200, login.text
    token = login.json().get("access_token")
    assert isinstance(token, str) and token
    return email, token


def _make_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _assert_paginated_response(body: Any, *, offset: int, limit: int) -> None:
    assert isinstance(body, dict)
    assert isinstance(body.get("items"), list)
    assert isinstance(body.get("total"), int)
    assert isinstance(body.get("offset"), int)
    assert isinstance(body.get("limit"), int)
    assert body["offset"] == offset
    assert body["limit"] == limit


async def test_login_returns_frontend_token_contract(async_client: AsyncClient) -> None:
    email = f"frontend-login-{uuid.uuid4().hex}@example.com"
    register = await async_client.post(
        _REGISTER_URL,
        json={"email": email, "password": _PASSWORD},
    )
    assert register.status_code == 201, register.text

    response = await async_client.post(
        _LOGIN_URL,
        data={"username": email, "password": _PASSWORD},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert isinstance(body.get("access_token"), str) and body["access_token"]
    assert body.get("token_type") == "bearer"


async def test_current_user_returns_frontend_profile_keys(
    async_client: AsyncClient,
) -> None:
    email, token = await _register_and_login(async_client)

    response = await async_client.get(
        _ME_URL,
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert {"id", "email", "full_name", "role", "is_active"} <= body.keys()
    assert body["email"] == email
    assert isinstance(body["is_active"], bool)


async def test_jobs_and_applications_return_paginated_contracts(
    async_client: AsyncClient,
) -> None:
    _, token = await _register_and_login(async_client)
    headers = {"Authorization": f"Bearer {token}"}
    offset, limit = 0, 5

    jobs = await async_client.get(
        _JOBS_URL,
        params={"offset": offset, "limit": limit},
        headers=headers,
    )
    assert jobs.status_code == 200, jobs.text
    _assert_paginated_response(jobs.json(), offset=offset, limit=limit)

    applications = await async_client.get(
        _APPLICATIONS_URL,
        params={"offset": offset, "limit": limit},
        headers=headers,
    )
    assert applications.status_code == 200, applications.text
    _assert_paginated_response(applications.json(), offset=offset, limit=limit)


async def test_resume_upload_returns_parsed_resume_read(
    async_client: AsyncClient,
) -> None:
    _, token = await _register_and_login(async_client)
    with (
        patch("backend.services.resume_service.extract_text", return_value="Contract test resume"),
        patch(
            "backend.ai.cv_analyzer.CVAnalyzerService.analyze_cv",
            new=AsyncMock(return_value=_MOCK_PARSE_RESULT),
        ),
    ):
        response = await async_client.post(
            _RESUME_UPLOAD_URL,
            headers={"Authorization": f"Bearer {token}"},
            files={"file": ("resume.pdf", _make_pdf(), "application/pdf")},
        )

    assert response.status_code == 201, response.text
    body = response.json()
    parsed_resume = ParsedResumeRead.model_validate(body)
    assert parsed_resume.original_filename == "resume.pdf"
    assert parsed_resume.parsed_data is not None
    assert parsed_resume.parsed_data["name"] == _MOCK_PARSE_RESULT["name"]
