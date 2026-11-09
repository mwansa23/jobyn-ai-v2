from __future__ import annotations

import json
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


Environment = Literal["development", "test", "staging", "production"]
LLMProviderName = Literal["freellmapi", "nvidia"]

class Settings(BaseSettings):
    """
    Application configuration.

    Values are loaded from environment variables.

    Vercel:
        Environment variables are supplied by the Vercel runtime.

    Local development:
        Values can be supplied through .env.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # Application
    # ------------------------------------------------------------------

    APP_NAME: str = "Jobyn AI"
    ENVIRONMENT: Environment = "development"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"
    API_V1_PREFIX: str = "/api/v1"

    # ------------------------------------------------------------------
    # Security
    # ------------------------------------------------------------------

    SECRET_KEY: str = Field(
        default="dev-only-change-this-secret-key",
        min_length=16,
    )

    JWT_ALGORITHM: str = "HS256"

    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    # Comma-separated administrator email addresses. Empty means no admin access.
    ADMIN_EMAILS: str | list[str] = Field(default_factory=list)

    # ------------------------------------------------------------------
    # Database
    # ------------------------------------------------------------------

    DATABASE_URL: str = "sqlite+aiosqlite:///./jobyn.db"

    # ------------------------------------------------------------------
    # Redis / Celery
    # ------------------------------------------------------------------

    REDIS_URL: str = "redis://localhost:6379/0"

    CELERY_BROKER_URL: str = "redis://localhost:6379/0"

    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/0"

    CELERY_TASK_TRACK_STARTED: bool = False

    CELERY_WORKER_CONCURRENCY: int = 1

    # ------------------------------------------------------------------
    # CORS
    # ------------------------------------------------------------------

    BACKEND_CORS_ORIGINS: str | list[str] = Field(default_factory=list)

    # ------------------------------------------------------------------
    # LLM provider selection
    # ------------------------------------------------------------------

    LLM_PROVIDER: LLMProviderName = "nvidia"

    FREELLMAPI_BASE_URL: str | None = None
    FREELLMAPI_API_KEY: str | None = None
    FREELLMAPI_MODEL: str = "auto"

    # ------------------------------------------------------------------
    # NVIDIA NIM
    # ------------------------------------------------------------------

    NVIDIA_API_KEY: str | None = None
    NVIDIA_BASE_URL: str = "https://integrate.api.nvidia.com/v1"
    NVIDIA_MODEL: str = "nvidia/nemotron-3.5-lightning-30b-a3b"

    # ------------------------------------------------------------------
    # Job Ingestion Engine
    # ------------------------------------------------------------------

    JOB_INGESTION_ENABLED: bool = True

    # Greenhouse — comma-separated board slugs, e.g. "stripe,shopify"
    GREENHOUSE_ENABLED: bool = True
    GREENHOUSE_BOARDS: str = ""   # empty = no boards configured

    # Lever — comma-separated company slugs, e.g. "netflix,airbnb"
    LEVER_ENABLED: bool = True
    LEVER_BOARDS: str = ""

    # Ashby — comma-separated org slugs
    ASHBY_ENABLED: bool = True
    ASHBY_BOARDS: str = ""

    # ReliefWeb — always uses the public API; no key needed
    RELIEFWEB_ENABLED: bool = True

    # UN Careers — official RSS feed
    UN_CAREERS_ENABLED: bool = True

    # HTTP client settings for the ingestion engine
    INGESTION_REQUEST_TIMEOUT: float = 30.0
    INGESTION_MAX_RETRIES: int = 3
    INGESTION_USER_AGENT: str = "Jobyn-AI-Ingestion/2.0 (+https://jobyn.ai)"
    INGESTION_OBSERVATION_RETENTION_DAYS: int = 90

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------

    @field_validator("ADMIN_EMAILS", mode="before")
    @classmethod
    def parse_admin_emails(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item).strip().lower() for item in value if str(item).strip()]
        return [item.strip().lower() for item in str(value).split(",") if item.strip()]

    @field_validator("BACKEND_CORS_ORIGINS", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: object) -> list[str]:
        """
        Accept either:

        JSON:
            ["https://example.com", "http://localhost:3000"]

        Comma separated:
            https://example.com,http://localhost:3000

        Or a single origin.
        """
        if value is None:
            return []

        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]

        if isinstance(value, str):
            value = value.strip()

            if not value:
                return []

            # Try JSON first.
            if value.startswith("["):
                try:
                    parsed = json.loads(value)
                    if isinstance(parsed, list):
                        return [
                            str(item).strip()
                            for item in parsed
                            if str(item).strip()
                        ]
                except json.JSONDecodeError:
                    pass

            return [
                item.strip()
                for item in value.split(",")
                if item.strip()
            ]

        return [str(value)]

    @field_validator("LLM_PROVIDER", mode="before")
    @classmethod
    def normalize_llm_provider(cls, value: object) -> str:
        if value is None:
            return "nvidia"
        normalized = str(value).strip().lower()
        if normalized not in {"freellmapi", "nvidia"}:
            raise ValueError(
                f"LLM_PROVIDER must be 'freellmapi' or 'nvidia', got {value!r}"
            )
        return normalized

    @field_validator("ENVIRONMENT", mode="before")
    @classmethod
    def normalize_environment(cls, value: object) -> str:
        if value is None:
            return "development"

        value = str(value).strip().lower()

        if not value:
            return "development"

        return value

    @field_validator("DEBUG", mode="before")
    @classmethod
    def normalize_debug(cls, value: object) -> bool:
        if value is None:
            return False

        if isinstance(value, bool):
            return value

        value = str(value).strip().lower()

        if value in {"true", "1", "yes", "on"}:
            return True

        if value in {"false", "0", "no", "off", ""}:
            return False

        return False

    @field_validator(
        "CELERY_TASK_TRACK_STARTED",
        mode="before",
    )
    @classmethod
    def normalize_celery_tracking(cls, value: object) -> bool:
        if value is None:
            return False

        if isinstance(value, bool):
            return value

        value = str(value).strip().lower()

        if value in {"true", "1", "yes", "on"}:
            return True

        return False

    @field_validator(
        "ACCESS_TOKEN_EXPIRE_MINUTES",
        "CELERY_WORKER_CONCURRENCY",
        mode="before",
    )
    @classmethod
    def normalize_positive_int(cls, value: object) -> int:
        if value is None:
            return 1

        if isinstance(value, int):
            return value

        value = str(value).strip()

        if not value:
            return 1

        return int(value)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
