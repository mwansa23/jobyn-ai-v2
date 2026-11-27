"""Authentication HTTP routes.

Thin endpoints that delegate authentication use cases to AuthService.
Request-scoped database sessions and authenticated-user resolution are provided
by backend.api.deps.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from backend.api.deps import get_auth_service, get_current_user
from backend.auth.supabase import (
    SupabaseEmailAlreadyRegistered,
    SupabaseAuthRejected,
    SupabaseVerificationUnavailable,
    supabase_auth_configured,
    supabase_password_grant,
    supabase_signup,
)
from backend.models.user import User
from backend.schemas.auth import TokenResponse, UserLogin
from backend.schemas.user import UserCreate, UserRead
from backend.services.auth import AuthService

router = APIRouter(
    prefix="/auth",
    tags=["Authentication"],
)


@router.post(
    "/register",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user account",
)
async def register(
    user_data: UserCreate,
    auth_service: AuthService = Depends(get_auth_service),
) -> UserRead:
    """Create a new user account.

    When Supabase Auth is configured (SUPABASE_URL + SUPABASE_PUBLISHABLE_KEY),
    the account is created in Supabase via the signup proxy and the Jobyn
    record is linked or provisioned without a local password.  Otherwise the
    legacy email/password flow is used.
    """
    if supabase_auth_configured():
        from uuid import UUID as _UUID

        try:
            signup = await supabase_signup(
                str(user_data.email),
                user_data.password,
                user_data.full_name,
            )
        except SupabaseEmailAlreadyRegistered:
            raise
        except SupabaseAuthRejected as exc:
            from fastapi import HTTPException as _HTTPException

            raise _HTTPException(status_code=exc.status_code, detail=exc.detail)

        supabase_user = signup.get("user") or {}
        raw_id = supabase_user.get("id")
        try:
            supabase_user_id = _UUID(raw_id)
        except (TypeError, ValueError, AttributeError):
            raise SupabaseVerificationUnavailable()

        verified = bool(
            signup.get("access_token")
            or supabase_user.get("email_confirmed_at")
        )
        user = await auth_service.link_or_provision_for_signup(
            str(user_data.email),
            user_data.full_name,
            supabase_user_id,
            verified,
        )
        return UserRead.model_validate(user)

    return await auth_service.register_user(user_data)


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Authenticate and issue an access token",
)
async def login(
    request: Request,
    auth_service: AuthService = Depends(get_auth_service),
) -> TokenResponse:
    """Authenticate JSON frontend credentials or OAuth2 password-form credentials.

    The frontend sends ``{"email": ..., "password": ...}`` as JSON, while
    Swagger's OAuth2 password flow sends ``username=...&password=...`` as
    ``application/x-www-form-urlencoded``. Both representations are accepted
    at this single endpoint so the existing authentication implementation and
    JWT/token validation remain unchanged.
    """

    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()

    if content_type == "application/x-www-form-urlencoded":
        form = await request.form()
        data = UserLogin.model_validate(
            {
                "email": form.get("username"),
                "password": form.get("password"),
            }
        )
    else:
        try:
            payload = await request.json()
        except ValueError as exc:
            raise RequestValidationError(
                [{"type": "json_invalid", "loc": ("body",), "msg": "Invalid JSON", "input": None}]
            ) from exc
        try:
            data = UserLogin.model_validate(payload)
        except ValidationError as exc:
            raise RequestValidationError(exc.errors()) from exc

    # — Supabase password-grant proxy (production/staging) —
    if supabase_auth_configured():
        grant = await supabase_password_grant(str(data.email), data.password)
        expires_in = grant.get("expires_in")
        return TokenResponse(
            access_token=grant["access_token"],
            token_type="bearer",
            expires_in=int(expires_in) if isinstance(expires_in, (int, float)) else 3600,
        )

    # — Legacy email/password flow (development/test) —
    user = await auth_service.authenticate_user(
        email=str(data.email),
        password=data.password,
    )

    return auth_service.create_access_token_response(user)


@router.get(
    "/me",
    response_model=UserRead,
    summary="Return the authenticated user's profile",
)
async def read_current_user(
    current_user: User = Depends(get_current_user),
) -> UserRead:
    """Return the profile of the authenticated user."""

    return UserRead.model_validate(current_user)
