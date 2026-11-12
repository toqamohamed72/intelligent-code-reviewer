import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from codereviewer.config import Settings  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    for var in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "LLM_PROVIDER", "GEMINI_MODEL",
                "OPENAI_MODEL", "GEMINI_THINKING_LEVEL", "REVIEW_TEMPERATURE", "MAX_CONTEXT_TOKENS"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("OUTPUTS_DIR", str(tmp_path / "outputs"))


@pytest.fixture
def settings(tmp_path) -> Settings:
    """Fast settings: no sleeping between retries."""
    return Settings(provider="gemini", gemini_api_key="AIza-test-key-123456", retry_base_delay_s=0.0,
                    retry_max_delay_s=0.0, max_retries=3, outputs_dir=tmp_path / "outputs")


def contract_text(language="python", code="x = 1", bullets=None, explain=True):
    """A valid model response."""
    bullets = bullets if bullets is not None else ["- [HIGH] [LOGIC] line 1: Wrong value. Fix: use 2."]
    parts = []
    if explain:
        parts.append("## EXPLANATION\n- Sets a value.\n- Nothing else.\n")
    parts.append("## BUG_REPORT\n" + "\n".join(bullets) + "\n")
    parts.append(f"## REFACTORED_CODE\n```{language}\n{code}\n```")
    return "\n".join(parts)
