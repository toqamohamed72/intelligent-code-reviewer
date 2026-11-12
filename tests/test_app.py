"""Drive the Streamlit app headlessly with Streamlit's AppTest (no browser needed)."""
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = str(Path(__file__).resolve().parent.parent / "app.py")


@pytest.fixture
def at(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    app = AppTest.from_file(APP, default_timeout=30)
    app.run()
    return app


def test_app_starts_in_demo_mode(at):
    assert not at.exception
    assert any("Demo engine" in w.value for w in at.sidebar.warning)
    assert not at.button(key="review").disabled


def test_example_then_review_shows_findings_and_refactored_code(at):
    at.button(key="example").click().run()
    assert "stock=[]" in at.text_area(key="pasted_code").value
    at.button(key="review").click().run()
    assert not at.exception
    result = at.session_state["result"]
    assert result.ok and result.counts()["HIGH"] == 1 and result.counts()["MEDIUM"] == 1
    assert [m.label for m in at.metric][:3] == ["High", "Medium", "Low"]
    assert any("Mutable default" in m.value for m in at.markdown)       # finding card rendered


def test_empty_input_shows_an_error_not_a_crash(at):
    at.button(key="review").click().run()
    assert not at.exception
    assert any("nothing to review" in e.value for e in at.error)


def test_html_in_model_findings_is_escaped(monkeypatch):
    """Model output is untrusted: it must never reach the page as live HTML."""
    from codereviewer.config import Settings
    from codereviewer.ingest import from_text
    from codereviewer.pipeline import CodeReviewer
    from tests.conftest import contract_text
    from tests.fakes import ScriptedProvider

    monkeypatch.setenv("LLM_PROVIDER", "mock")
    evil = contract_text(bullets=["- [HIGH] [LOGIC] line 1: <script>alert(1)</script> Fix: <img src=x onerror=y>"])
    result = CodeReviewer(ScriptedProvider([evil]), Settings(provider="mock")).review(from_text("x = 1\n", "x.py", "python"))
    app = AppTest.from_file(APP, default_timeout=30)
    app.session_state["result"] = result
    app.run()
    assert not app.exception
    rendered = " ".join(m.value for m in app.markdown)
    assert "&lt;script&gt;" in rendered and "<script>alert(1)" not in rendered and "<img src=x" not in rendered
