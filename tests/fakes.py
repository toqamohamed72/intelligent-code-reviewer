"""Test doubles for providers and SDK clients."""
from __future__ import annotations

from types import SimpleNamespace

from codereviewer.providers import ProviderResponse

try:  # the SDKs ship their own HTTP layer (httpx or httpx2)
    import httpx2 as httpx
except ImportError:  # pragma: no cover
    import httpx


class ScriptedProvider:
    """Returns scripted texts (or raises) and records every call."""
    name, model = "scripted", "scripted-model"

    def __init__(self, script):
        self.script, self.calls = list(script), []

    def generate(self, system, user, *, max_output_tokens, temperature):
        self.calls.append({"system": system, "user": user, "max": max_output_tokens, "temperature": temperature})
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(item, Exception):
            raise item
        if callable(item):
            item = item(system, user)
        return ProviderResponse(text=item, model=self.model, provider=self.name, prompt_tokens=100,
                                completion_tokens=50, thinking_tokens=10, latency_s=0.1)


def gemini_response(text, *, finish="STOP", model_version="gemini-fake", feedback=None):
    return SimpleNamespace(
        text=text,
        candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name=finish))],
        usage_metadata=SimpleNamespace(prompt_token_count=11, candidates_token_count=7, thoughts_token_count=3),
        prompt_feedback=feedback, model_version=model_version,
    )


class FakeGeminiClient:
    def __init__(self, script):
        self.script, self.calls = list(script), []
        self.models = SimpleNamespace(generate_content=self._generate)

    def _generate(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(item, BaseException):
            raise item
        return item


def openai_response(text, *, finish="stop", refusal=None):
    message = SimpleNamespace(content=text, refusal=refusal)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish)], model="openai-fake",
        usage=SimpleNamespace(prompt_tokens=20, completion_tokens=9,
                              completion_tokens_details=SimpleNamespace(reasoning_tokens=4)),
    )


class FakeOpenAIClient:
    def __init__(self, script):
        self.script, self.calls = list(script), []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **params):
        self.calls.append(params)
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(item, BaseException):
            raise item
        return item


def openai_error(cls, status, message="boom"):
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    response = httpx.Response(status, request=request, json={"error": {"message": message}})
    return cls(message, response=response, body={"error": {"message": message}})
