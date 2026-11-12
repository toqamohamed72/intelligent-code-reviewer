import pytest
from google.genai import errors, types

from codereviewer.config import ConfigError, Settings
from codereviewer.contract import parse_review
from codereviewer.providers import (GeminiProvider, MockProvider, OpenAIProvider, ProviderError, create_provider,
                                    openai_sampling)
from tests.fakes import (FakeGeminiClient, FakeOpenAIClient, gemini_response, openai_error, openai_response)

KW = dict(max_output_tokens=2000, temperature=None)


def gemini(settings, script, **over):
    s = Settings(**{**settings.__dict__, **over})
    client = FakeGeminiClient(script)
    return GeminiProvider(s, client=client), client


def api_error(cls, code, message="boom", status="X"):
    return cls(code, {"error": {"code": code, "message": message, "status": status}})


def test_gemini_sends_the_system_instruction_and_omits_temperature_by_default(settings):
    provider, client = gemini(settings, [gemini_response("hello")])
    resp = provider.generate("SYSTEM RULES", "USER CODE", **KW)
    call = client.calls[0]
    assert call["model"] == "gemini-3.5-flash" and call["contents"] == "USER CODE"
    assert call["config"].system_instruction == "SYSTEM RULES" and call["config"].max_output_tokens == 2000
    assert call["config"].temperature is None and call["config"].thinking_config is None
    assert (resp.text, resp.prompt_tokens, resp.completion_tokens, resp.thinking_tokens) == ("hello", 11, 7, 3)
    assert resp.finish_reason == "STOP" and resp.provider == "gemini"


def test_gemini_temperature_and_thinking_level_and_gemini3_warning(settings):
    provider, client = gemini(settings, [gemini_response("x")], gemini_thinking_level="low")
    resp = provider.generate("s", "u", max_output_tokens=10, temperature=0.2)
    cfg = client.calls[0]["config"]
    assert cfg.temperature == 0.2 and cfg.thinking_config.thinking_level == types.ThinkingLevel.LOW
    assert any("default (1.0) for Gemini 3" in n for n in resp.notes)


@pytest.mark.parametrize("script_item,kind", [
    (gemini_response(None, finish="SAFETY"), "blocked"),
    (gemini_response(None, finish="MAX_TOKENS"), "truncated"),
    (gemini_response(None), "empty"),
])
def test_gemini_unusable_responses_become_clear_errors(settings, script_item, kind):
    provider, _ = gemini(settings, [script_item])
    with pytest.raises(ProviderError) as exc:
        provider.generate("s", "u", **KW)
    assert exc.value.kind == kind


def test_gemini_blocked_prompt_feedback(settings):
    fb = type("F", (), {"block_reason": type("B", (), {"name": "PROHIBITED_CONTENT"})()})()
    provider, _ = gemini(settings, [gemini_response(None, feedback=fb)])
    with pytest.raises(ProviderError, match="PROHIBITED_CONTENT"):
        provider.generate("s", "u", **KW)


def test_gemini_truncated_but_non_empty_output_adds_a_note(settings):
    provider, _ = gemini(settings, [gemini_response("partial", finish="MAX_TOKENS")])
    assert any("token limit" in n for n in provider.generate("s", "u", **KW).notes)


def test_gemini_retries_transient_errors_then_succeeds(settings):
    script = [api_error(errors.ServerError, 503, status="UNAVAILABLE"), api_error(errors.ClientError, 429), gemini_response("ok")]
    provider, client = gemini(settings, script)
    assert provider.generate("s", "u", **KW).text == "ok" and len(client.calls) == 3


def test_gemini_gives_up_after_max_retries(settings):
    provider, client = gemini(settings, [api_error(errors.ServerError, 500)])
    with pytest.raises(ProviderError) as exc:
        provider.generate("s", "u", **KW)
    assert exc.value.kind == "server" and len(client.calls) == settings.max_retries


@pytest.mark.parametrize("err,kind", [
    (api_error(errors.ClientError, 400, "API key not valid. Please pass a valid API key."), "auth"),
    (api_error(errors.ClientError, 403, "denied"), "auth"),
    (api_error(errors.ClientError, 404, "model not found"), "model"),
    (api_error(errors.ClientError, 400, "bad field"), "bad_request"),
])
def test_gemini_non_retryable_errors_are_not_retried(settings, err, kind):
    provider, client = gemini(settings, [err])
    with pytest.raises(ProviderError) as exc:
        provider.generate("s", "u", **KW)
    assert exc.value.kind == kind and not exc.value.retryable and len(client.calls) == 1


def test_gemini_network_failures_are_retried_and_translated(settings):
    from tests.fakes import httpx
    for failure in (ConnectionError("down"), httpx.ConnectError("no route")):
        provider, client = gemini(settings, [failure])
        with pytest.raises(ProviderError) as exc:
            provider.generate("s", "u", **KW)
        assert exc.value.kind == "network" and len(client.calls) == settings.max_retries


def test_missing_gemini_key_is_a_config_error(tmp_path):
    with pytest.raises(ConfigError, match="GEMINI_API_KEY"):
        GeminiProvider(Settings(gemini_api_key=None, outputs_dir=tmp_path)).generate("s", "u", **KW)


# --- OpenAI ---------------------------------------------------------------- #

def oai(settings, script, model="gpt-4.1-mini", **over):
    s = Settings(**{**settings.__dict__, "provider": "openai", "openai_api_key": "sk-test-123456", "openai_model": model, **over})
    client = FakeOpenAIClient(script)
    return OpenAIProvider(s, client=client), client


def test_openai_parameters_per_model_family():
    assert openai_sampling("gpt-4.1-mini", 0.3, None)[0] == {"temperature": 0.3}
    assert openai_sampling("gpt-6-luna", 0.3, None)[0] == {"temperature": 0.3, "reasoning_effort": "none"}
    kw, notes = openai_sampling("gpt-6-luna", 0.3, "high")
    assert kw == {"reasoning_effort": "high"} and notes
    kw, notes = openai_sampling("gpt-5", 0.3, None)
    assert kw == {} and "does not accept temperature" in notes[0]
    assert openai_sampling("gpt-6-luna", None, None)[0] == {}                      # default: send nothing extra


def test_openai_request_shape_and_usage(settings):
    provider, client = oai(settings, [openai_response("hello")])
    resp = provider.generate("SYS", "USR", max_output_tokens=500, temperature=0.2)
    p = client.calls[0]
    assert p["messages"] == [{"role": "system", "content": "SYS"}, {"role": "user", "content": "USR"}]
    assert p["max_completion_tokens"] == 500 and p["temperature"] == 0.2
    assert (resp.prompt_tokens, resp.completion_tokens, resp.thinking_tokens) == (20, 9, 4)


def test_openai_learns_when_temperature_is_rejected(settings):
    err = openai_error(__import__("openai").BadRequestError, 400, "Unsupported parameter: 'temperature'")
    provider, client = oai(settings, [err, openai_response("ok")], model="mystery-model")
    assert provider.generate("s", "u", max_output_tokens=10, temperature=0.5).text == "ok"
    assert "temperature" in client.calls[0] and "temperature" not in client.calls[1]


def test_openai_retry_and_auth(settings):
    import openai
    provider, client = oai(settings, [openai_error(openai.RateLimitError, 429), openai_response("ok")])
    assert provider.generate("s", "u", **KW).text == "ok" and len(client.calls) == 2
    provider, client = oai(settings, [openai_error(openai.AuthenticationError, 401)])
    with pytest.raises(ProviderError) as exc:
        provider.generate("s", "u", **KW)
    assert exc.value.kind == "auth" and len(client.calls) == 1


# --- Mock ------------------------------------------------------------------ #

def test_mock_provider_output_always_satisfies_the_contract():
    from codereviewer.context import plan_chunks
    from codereviewer.ingest import from_text
    from codereviewer.prompts import build_system_instruction, build_user_message
    from codereviewer.static_checks import precheck
    src = from_text("def f(a=[]):\n    return eval(a)\n", "m.py", "python")
    chunk = plan_chunks(src, 5000)[0]
    resp = MockProvider().generate(build_system_instruction("python"), build_user_message(src, chunk, precheck("python", src.text)), **KW)
    parsed = parse_review(resp.text, expected_language="python")
    assert {f.category for f in parsed.findings} >= {"LOGIC", "SECURITY"} and parsed.refactored_code == src.text.rstrip("\n")
    assert "Demo engine" in resp.notes[0]


def test_factory(settings):
    assert create_provider(settings).name == "gemini"
    assert create_provider(Settings(provider="mock")).name == "mock"
    assert create_provider(Settings(provider="openai")).name == "openai"


def test_one_client_is_created_even_when_many_threads_ask_at_once(settings, monkeypatch):
    """Regression: parallel file reviews used to create several clients; the losers were garbage-collected and
    closed, which broke in-flight requests ('Cannot send a request, as the client has been closed')."""
    import threading
    import time

    from google import genai

    created = []

    class SlowClient:
        def __init__(self, **kwargs):
            time.sleep(0.05)                      # widen the race window
            created.append(self)

    monkeypatch.setattr(genai, "Client", SlowClient)
    provider = GeminiProvider(settings)
    seen = []
    threads = [threading.Thread(target=lambda: seen.append(provider._get_client())) for _ in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert len(created) == 1 and len({id(c) for c in seen}) == 1 and provider._client is created[0]
