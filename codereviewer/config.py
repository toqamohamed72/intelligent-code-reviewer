"""Runtime configuration from environment variables / a local .env file."""
from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROVIDERS = ("gemini", "openai", "mock")
THINKING_LEVELS = ("minimal", "low", "medium", "high")
REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")


class ConfigError(Exception):
    """Invalid or missing configuration."""


def _num(name: str, default, cast):
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    try:
        return cast(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a valid {cast.__name__}, got {raw!r}") from exc


@dataclass(frozen=True)
class Settings:
    provider: str = "gemini"
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.5-flash"
    gemini_thinking_level: str | None = None
    openai_api_key: str | None = None
    openai_model: str = "gpt-6-luna"
    openai_base_url: str | None = None
    openai_reasoning_effort: str | None = None
    temperature: float | None = None          # None = provider default (recommended for Gemini 3)
    max_output_tokens: int = 16000
    timeout_s: float = 120.0
    max_retries: int = 4
    retry_base_delay_s: float = 1.0
    retry_max_delay_s: float = 30.0
    max_file_bytes: int = 512_000             # hard ingestion limit
    max_context_tokens: int = 30_000          # approx. code tokens per request before chunking
    max_repairs: int = 1
    jobs: int = 4
    outputs_dir: Path = PROJECT_ROOT / "outputs"

    @property
    def model(self) -> str:
        return {"gemini": self.gemini_model, "openai": self.openai_model}.get(self.provider, "mock-reviewer")

    @property
    def api_key(self) -> str | None:
        return {"gemini": self.gemini_api_key, "openai": self.openai_api_key}.get(self.provider)

    @property
    def masked_key(self) -> str:
        key = self.api_key
        if self.provider == "mock":
            return "(not needed)"
        if not key:
            return "(not set)"
        return f"{key[:3]}…{key[-4:]}" if len(key) > 10 else "***"

    def with_overrides(self, **changes) -> "Settings":
        new = replace(self, **{k: v for k, v in changes.items() if v is not None})
        new.validate()
        return new

    def validate(self) -> None:
        if self.provider not in PROVIDERS:
            raise ConfigError(f"LLM_PROVIDER must be one of {PROVIDERS}, got {self.provider!r}")
        if self.gemini_thinking_level and self.gemini_thinking_level not in THINKING_LEVELS:
            raise ConfigError(f"GEMINI_THINKING_LEVEL must be one of {THINKING_LEVELS}")
        if self.openai_reasoning_effort and self.openai_reasoning_effort not in REASONING_EFFORTS:
            raise ConfigError(f"OPENAI_REASONING_EFFORT must be one of {REASONING_EFFORTS}")
        if self.temperature is not None and not 0.0 <= self.temperature <= 2.0:
            raise ConfigError("REVIEW_TEMPERATURE must be between 0 and 2")
        for name in ("max_output_tokens", "max_retries", "jobs", "max_context_tokens", "max_file_bytes"):
            if getattr(self, name) < 1:
                raise ConfigError(f"{name.upper()} must be at least 1")

    @classmethod
    def from_env(cls, **overrides) -> "Settings":
        load_dotenv(PROJECT_ROOT / ".env", override=False)
        env = os.getenv
        settings = cls(
            provider=env("LLM_PROVIDER", "gemini").strip().lower(),
            gemini_api_key=env("GEMINI_API_KEY") or env("GOOGLE_API_KEY") or None,
            gemini_model=env("GEMINI_MODEL", "gemini-3.5-flash").strip(),
            gemini_thinking_level=(env("GEMINI_THINKING_LEVEL") or "").strip().lower() or None,
            openai_api_key=env("OPENAI_API_KEY") or None,
            openai_model=env("OPENAI_MODEL", "gpt-6-luna").strip(),
            openai_base_url=env("OPENAI_BASE_URL") or None,
            openai_reasoning_effort=(env("OPENAI_REASONING_EFFORT") or "").strip().lower() or None,
            temperature=_num("REVIEW_TEMPERATURE", None, float),
            max_output_tokens=_num("MAX_OUTPUT_TOKENS", 16000, int),
            timeout_s=_num("REQUEST_TIMEOUT_S", 120.0, float),
            max_retries=_num("MAX_RETRIES", 4, int),
            retry_base_delay_s=_num("RETRY_BASE_DELAY_S", 1.0, float),
            retry_max_delay_s=_num("RETRY_MAX_DELAY_S", 30.0, float),
            max_file_bytes=_num("MAX_FILE_BYTES", 512_000, int),
            max_context_tokens=_num("MAX_CONTEXT_TOKENS", 30_000, int),
            max_repairs=_num("MAX_REPAIRS", 1, int),
            jobs=_num("REVIEW_JOBS", 4, int),
            outputs_dir=Path(os.getenv("OUTPUTS_DIR") or (PROJECT_ROOT / "outputs")),
        )
        if overrides:
            settings = replace(settings, **{k: v for k, v in overrides.items() if v is not None})
        settings.validate()
        return settings
