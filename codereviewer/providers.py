"""LLM providers behind one small interface.

``generate(system, user, ...)`` takes the **system instruction** and the **user
payload** separately, which is exactly how the PDF's architecture hard-codes
behavioural rules into the client call.

* ``GeminiProvider`` - Google GenAI SDK (``google-genai``). The PDF's reference provider.
* ``OpenAIProvider`` - Chat Completions.
* ``MockProvider``   - deterministic offline demo; its text is NOT model output.

All providers raise ``ProviderError`` with a ``kind`` and a ``retryable`` flag;
Tenacity retries only the retryable ones (rate limits, 5xx, network).
"""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from tenacity import (
    Retrying,
    before_sleep_log,
    retry_if_exception,
    stop_after_attempt,
    wait_random_exponential,
)

from .config import ConfigError, Settings

log = logging.getLogger("codereviewer.providers")


class ProviderError(Exception):
    def __init__(self, message: str, kind: str = "api", retryable: bool = False):
        super().__init__(message)
        self.kind = kind
        self.retryable = retryable


@dataclass
class ProviderResponse:
    text: str
    model: str
    provider: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    thinking_tokens: int = 0
    finish_reason: str | None = None
    latency_s: float = 0.0
    notes: list[str] = field(default_factory=list)


class Provider(Protocol):
    name: str
    model: str

    def generate(self, system: str, user: str, *, max_output_tokens: int, temperature: float | None) -> ProviderResponse: ...


def _retrying(settings: Settings) -> Retrying:
    return Retrying(
        stop=stop_after_attempt(settings.max_retries),
        wait=wait_random_exponential(multiplier=settings.retry_base_delay_s, max=settings.retry_max_delay_s),
        retry=retry_if_exception(lambda e: isinstance(e, ProviderError) and e.retryable),
        before_sleep=before_sleep_log(log, logging.WARNING),
        reraise=True,
    )


def _is_network_error(exc: BaseException) -> bool:
    modules = {c.__module__.split(".")[0] for c in type(exc).__mro__}
    return bool(modules & {"httpx", "httpx2", "httpcore", "requests", "aiohttp"}) or isinstance(exc, (TimeoutError, ConnectionError))


# --------------------------------------------------------------------------- #
# Gemini
# --------------------------------------------------------------------------- #

class GeminiProvider:
    name = "gemini"

    def __init__(self, settings: Settings, *, client: Any = None):
        self.settings = settings
        self.model = settings.gemini_model
        self._client = client
        self._lock = threading.Lock()

    def _get_client(self):
        # Directory mode reviews files in parallel threads. Exactly ONE client must be created and kept
        # referenced: a client that loses its last reference is garbage-collected and closes its connection
        # pool, which breaks requests still running on it ("client has been closed").
        if self._client is None:
            with self._lock:
                if self._client is None:
                    if not self.settings.gemini_api_key:
                        raise ConfigError(
                            "GEMINI_API_KEY is not set. Copy .env.example to .env and add a key "
                            "(create one in Google AI Studio), or use --provider mock for an offline demo."
                        )
                    from google import genai
                    from google.genai import types

                    self._client = genai.Client(
                        api_key=self.settings.gemini_api_key,
                        http_options=types.HttpOptions(timeout=int(self.settings.timeout_s * 1000)),  # milliseconds
                    )
        return self._client

    @staticmethod
    def _translate(exc: Exception) -> ProviderError:
        from google.genai import errors

        if isinstance(exc, errors.APIError):
            code = getattr(exc, "code", None)
            text = f"{getattr(exc, 'message', '')} {exc}".lower()
            if code == 429:
                return ProviderError("Rate limit or quota exceeded. Wait, lower --jobs, or check your quota.", "rate_limit", True)
            if code == 408:
                return ProviderError("The request timed out.", "timeout", True)
            if code is not None and code >= 500:
                return ProviderError("The Gemini API had a server error or is overloaded. Try again shortly.", "server", True)
            if code in (401, 403) or "api key not valid" in text or "api_key_invalid" in text:
                return ProviderError("The API key was rejected or lacks access. Check GEMINI_API_KEY.", "auth")
            if code == 404:
                return ProviderError("Model not found. Check GEMINI_MODEL (model names change).", "model")
            return ProviderError(f"The API rejected the request: {getattr(exc, 'message', exc)}", "bad_request")
        if _is_network_error(exc):
            return ProviderError("Could not reach the Gemini API. Check your internet connection.", "network", True)
        return ProviderError(f"Unexpected error from the Gemini SDK: {exc}", "api")

    def _call(self, model: str, contents: str, config: Any):
        try:
            return self._get_client().models.generate_content(model=model, contents=contents, config=config)
        except ConfigError:
            raise
        except ProviderError:
            raise
        except Exception as exc:  # SDK and transport errors are translated, never leaked
            raise self._translate(exc) from exc

    def generate(self, system, user, *, max_output_tokens, temperature) -> ProviderResponse:
        from google.genai import types

        kwargs: dict[str, Any] = {"system_instruction": system, "max_output_tokens": max_output_tokens}
        notes: list[str] = []
        if temperature is not None:
            kwargs["temperature"] = temperature
            if self.model.startswith("gemini-3") and temperature < 1.0:
                notes.append(
                    "Google recommends leaving temperature at its default (1.0) for Gemini 3 models; "
                    "lower values can cause looping or degraded output."
                )
        level = self.settings.gemini_thinking_level
        if level:
            kwargs["thinking_config"] = types.ThinkingConfig(thinking_level=types.ThinkingLevel[level.upper()])
        config = types.GenerateContentConfig(**kwargs)

        started = time.perf_counter()
        response = _retrying(self.settings)(self._call, self.model, user, config)
        return self._to_response(response, notes, started)

    def _to_response(self, response: Any, notes: list[str], started: float) -> ProviderResponse:
        try:
            text = response.text
        except Exception:
            text = None
        candidates = getattr(response, "candidates", None) or []
        finish = getattr(candidates[0], "finish_reason", None) if candidates else None
        finish_name = getattr(finish, "name", str(finish)) if finish is not None else None
        if not text:
            feedback = getattr(response, "prompt_feedback", None)
            block = getattr(feedback, "block_reason", None)
            if block is not None:
                raise ProviderError(f"The prompt was blocked by the API ({getattr(block, 'name', block)}).", "blocked")
            if finish_name == "MAX_TOKENS":
                raise ProviderError(
                    "The model used its whole output budget before answering (thinking counts toward it). "
                    "Raise MAX_OUTPUT_TOKENS or lower GEMINI_THINKING_LEVEL.", "truncated")
            if finish_name and finish_name not in ("STOP", "FINISH_REASON_UNSPECIFIED"):
                raise ProviderError(f"The response was blocked or stopped ({finish_name}).", "blocked")
            raise ProviderError("The model returned an empty response.", "empty")
        if finish_name == "MAX_TOKENS":
            notes.append("The output hit the token limit and may be cut off.")
        usage = getattr(response, "usage_metadata", None)
        return ProviderResponse(
            text=text.strip(), model=getattr(response, "model_version", None) or self.model, provider=self.name,
            prompt_tokens=getattr(usage, "prompt_token_count", 0) or 0,
            completion_tokens=getattr(usage, "candidates_token_count", 0) or 0,
            thinking_tokens=getattr(usage, "thoughts_token_count", 0) or 0,
            finish_reason=finish_name, latency_s=time.perf_counter() - started, notes=notes,
        )


# --------------------------------------------------------------------------- #
# OpenAI
# --------------------------------------------------------------------------- #

_NEEDS_NONE = ("gpt-6-luna", "gpt-6-sol", "gpt-6.1-terra", "gpt-5.1", "gpt-5.2", "gpt-5.4")
_NEVER = ("o1", "o3", "o4", "gpt-5-", "gpt-6-astra", "gpt-6.1-sol")
_CLASSIC = ("gpt-3.5", "gpt-4")


def openai_sampling(model: str, temperature: float | None, effort: str | None) -> tuple[dict, list[str]]:
    """Which sampling/reasoning parameters this model accepts (rules known in Nov 2026; see README)."""
    name = model.lower().split("/")[-1]
    kwargs: dict[str, Any] = {}
    notes: list[str] = []
    classic = name.startswith(_CLASSIC)
    needs_none = name.startswith(_NEEDS_NONE)
    never = name == "gpt-5" or name.startswith(_NEVER)
    if effort and not classic:
        kwargs["reasoning_effort"] = effort
    if temperature is None:
        return kwargs, notes
    if never:
        notes.append(f"{model} does not accept temperature; it was not sent.")
    elif needs_none:
        if effort in (None, "none"):
            kwargs.update({"temperature": temperature, "reasoning_effort": "none"})
        else:
            notes.append(f"reasoning_effort={effort!r} disables temperature on {model}; it was not sent.")
    else:
        kwargs["temperature"] = temperature
    return kwargs, notes


class OpenAIProvider:
    name = "openai"

    def __init__(self, settings: Settings, *, client: Any = None):
        self.settings = settings
        self.model = settings.openai_model
        self._client = client
        self._drop_temperature = False
        self._lock = threading.Lock()

    def _get_client(self):
        if self._client is None:                      # one shared client across threads (see GeminiProvider)
            with self._lock:
                if self._client is None:
                    if not self.settings.openai_api_key and not self.settings.openai_base_url:
                        raise ConfigError("OPENAI_API_KEY is not set. Copy .env.example to .env and add it, or use --provider mock.")
                    import openai

                    self._client = openai.OpenAI(
                        api_key=self.settings.openai_api_key or "not-needed", base_url=self.settings.openai_base_url,
                        timeout=self.settings.timeout_s, max_retries=0,
                    )
        return self._client

    @staticmethod
    def _translate(exc: Exception) -> ProviderError:
        import openai

        if isinstance(exc, openai.AuthenticationError):
            return ProviderError("The API key was rejected. Check OPENAI_API_KEY.", "auth")
        if isinstance(exc, openai.PermissionDeniedError):
            return ProviderError("Your key has no access to this model. Check OPENAI_MODEL.", "permission")
        if isinstance(exc, openai.NotFoundError):
            return ProviderError("Model not found. Check OPENAI_MODEL (names change).", "model")
        if isinstance(exc, openai.RateLimitError):
            return ProviderError("Rate limit or quota exceeded. Wait, lower --jobs, or check billing.", "rate_limit", True)
        if isinstance(exc, openai.APITimeoutError):
            return ProviderError("The request timed out.", "timeout", True)
        if isinstance(exc, openai.APIConnectionError):
            return ProviderError("Could not reach the API. Check your internet connection.", "network", True)
        if isinstance(exc, openai.InternalServerError):
            return ProviderError("The API had a server error. Try again shortly.", "server", True)
        if isinstance(exc, openai.BadRequestError):
            return ProviderError(f"The API rejected the request: {getattr(exc, 'message', exc)}", "bad_request")
        return ProviderError(f"Unexpected API error: {exc}", "api")

    def _call(self, params: dict):
        import openai

        try:
            return self._get_client().chat.completions.create(**params)
        except ConfigError:
            raise
        except openai.BadRequestError as exc:
            text = f"{getattr(exc, 'message', '')} {exc}".lower()
            if "temperature" in text and "temperature" in params:
                raise _DropTemperature() from exc
            raise self._translate(exc) from exc
        except openai.OpenAIError as exc:
            raise self._translate(exc) from exc

    def generate(self, system, user, *, max_output_tokens, temperature) -> ProviderResponse:
        started = time.perf_counter()
        for _ in range(2):
            extra, notes = openai_sampling(self.model, None if self._drop_temperature else temperature,
                                           self.settings.openai_reasoning_effort)
            params = {
                "model": self.model, "max_completion_tokens": max_output_tokens,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}], **extra,
            }
            try:
                response = _retrying(self.settings)(self._call, params)
                break
            except _DropTemperature:
                self._drop_temperature = True
        else:
            raise ProviderError("The API kept rejecting the request.", "bad_request")
        choice = response.choices[0]
        if getattr(choice.message, "refusal", None):
            raise ProviderError(f"The model declined the request: {choice.message.refusal}", "refusal")
        text = (choice.message.content or "").strip()
        if not text:
            raise ProviderError("The model returned an empty response.", "empty")
        if choice.finish_reason == "length":
            notes.append("The output hit the token limit and may be cut off.")
        usage = getattr(response, "usage", None)
        details = getattr(usage, "completion_tokens_details", None)
        return ProviderResponse(
            text=text, model=getattr(response, "model", None) or self.model, provider=self.name,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            thinking_tokens=getattr(details, "reasoning_tokens", 0) or 0,
            finish_reason=choice.finish_reason, latency_s=time.perf_counter() - started, notes=notes,
        )


class _DropTemperature(Exception):
    """Internal signal: retry without temperature."""


# --------------------------------------------------------------------------- #
# Mock
# --------------------------------------------------------------------------- #

_LANG_RE = re.compile(r"^Review the (\S+) file", re.M)
_CODE_RE = re.compile(r"<<<CODE_BEGIN (\w+)>>>\n(.*)\n<<<CODE_END \1>>>", re.S)
_STATIC_RE = re.compile(r"^- \[(?:HIGH|MEDIUM|LOW)\] \[[A-Z_]+\].*$", re.M)


class MockProvider:
    """Deterministic offline stand-in. It lists the static pre-check findings and echoes the code.

    It exists to demo the pipeline, rendering and UI without an API key. It is not a model.
    """

    name = "mock"
    model = "mock-reviewer"

    def generate(self, system, user, *, max_output_tokens, temperature) -> ProviderResponse:
        language = (_LANG_RE.search(user) or [None, "python"])[1]
        match = _CODE_RE.search(user)
        numbered = match.group(2) if match else ""
        code = "\n".join(re.sub(r"^\s*\d+ \| ?", "", ln, count=1) for ln in numbered.split("\n"))
        static = _STATIC_RE.findall(user.split("<<<CODE_BEGIN")[0])
        explain = "## EXPLANATION" in system
        parts: list[str] = []
        if explain:
            lines = len(numbered.split("\n")) if numbered else 0
            parts.append(
                "## EXPLANATION\n"
                f"- Demo engine: this {language} excerpt has {lines} lines; no language model was consulted.\n"
                "- The listing below contains only findings from deterministic static analysis.\n"
            )
        bullets = static or ["- No defects found."]
        if static:
            bullets = static + ["- [LOW] [STYLE] Demo engine: only deterministic checks ran; the refactored code is unchanged."]
        parts.append("## BUG_REPORT\n" + "\n".join(bullets) + "\n")
        parts.append(f"## REFACTORED_CODE\n```{language}\n{code}\n```")
        text = "\n".join(parts)
        return ProviderResponse(
            text=text, model=self.model, provider=self.name, prompt_tokens=len(user) // 4,
            completion_tokens=len(text) // 4, finish_reason="stop",
            notes=["Demo engine: findings come from static analysis only; no language model reviewed this code."],
        )


def create_provider(settings: Settings) -> Provider:
    if settings.provider == "gemini":
        return GeminiProvider(settings)
    if settings.provider == "openai":
        return OpenAIProvider(settings)
    return MockProvider()
