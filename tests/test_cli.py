import io
import json
from pathlib import Path

import pytest

from codereviewer import cli
from codereviewer.cli import main
from tests.fakes import ScriptedProvider

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
PY, JS, JAVA = (str(SAMPLES / n) for n in ("inventory.py", "cart.js", "Calculator.java"))


def run(argv, capsys):
    code = main(argv)
    out = capsys.readouterr()
    return code, out.out, out.err


def test_terminal_review_with_the_mock_provider(capsys):
    code, out, _ = run([PY, "--provider", "mock", "--no-color"], capsys)
    assert code == 0 and "inventory.py" in out and "BUG_REPORT" in out and "REFACTORED_CODE" in out
    assert "Static pre-check" in out and "SQL string is built at runtime" in out


def test_json_and_markdown_formats(capsys):
    code, out, _ = run([PY, "--provider", "mock", "--format", "json"], capsys)
    data = json.loads(out)
    assert code == 0 and data[0]["ok"] and data[0]["counts"]["HIGH"] == 2 and data[0]["counts"]["MEDIUM"] == 3 and data[0]["file"] == "inventory.py"
    code, out, _ = run([JS, "--provider", "mock", "--format", "markdown", "--no-explain"], capsys)
    assert "## BUG_REPORT" in out and "## EXPLANATION" not in out and "```javascript" in out


def test_out_and_html_files_are_written(tmp_path, capsys):
    out, html = tmp_path / "r.md", tmp_path / "r.html"
    code, _, err = run([PY, "--provider", "mock", "--out", str(out), "--html", str(html), "--diff"], capsys)
    assert code == 0 and "## BUG_REPORT" in out.read_text() and "<span" in html.read_text() and "Saved:" in err


def test_directory_mode_reviews_all_supported_files_and_summarises(tmp_path, capsys):
    out_dir = tmp_path / "reports"
    code, out, _ = run([str(SAMPLES), "-r", "--provider", "mock", "--no-color", "--out", str(out_dir), "--jobs", "2"], capsys)
    assert code == 0 and "Review summary" in out
    assert sorted(p.name for p in out_dir.iterdir()) == ["Calculator.java.review.md", "cart.js.review.md", "inventory.py.review.md"]
    code, _, err = run([str(SAMPLES), "--provider", "mock"], capsys)
    assert code == 3 and "--recursive" in err


def test_fail_on_turns_the_tool_into_a_ci_gate(capsys):
    assert run([PY, "--provider", "mock", "--fail-on", "high", "--format", "json"], capsys)[0] == 1
    assert run([PY, "--provider", "mock", "--fail-on", "low", "--format", "json"], capsys)[0] == 1
    assert run([JAVA, "--provider", "mock", "--fail-on", "high", "--format", "json"], capsys)[0] == 0   # no static checks for Java


def test_input_problems_exit_with_code_3(tmp_path, capsys):
    (tmp_path / "x.unknown").write_text("hi")
    for target in (str(tmp_path / "missing.py"), str(tmp_path / "x.unknown")):
        code, _, err = run([target, "--provider", "mock", "--no-color"], capsys)
        assert code == 3 and "Could not review" in err


def test_missing_api_key_is_explained(capsys):
    code, _, err = run([JS, "--no-color"], capsys)
    assert code == 3 and "GEMINI_API_KEY" in err
    code, _, err = run([JS, "--provider", "openai", "--no-color"], capsys)
    assert code == 3 and "OPENAI_API_KEY" in err


def test_a_rejected_model_response_exits_4_and_can_be_shown(monkeypatch, capsys):
    monkeypatch.setattr(cli, "create_provider", lambda settings: ScriptedProvider(["Sure! no headers here"]))
    code, _, err = run([JS, "--no-color", "--show-rejected"], capsys)
    assert code == 4 and "rejected by the output validator" in err.lower() and "Sure! no headers here" in err


def test_stdin_mode(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("def f(a=[]):\n    return a\n"))
    code, out, _ = run(["-", "--language", "python", "--provider", "mock", "--no-color"], capsys)
    assert code == 0 and "stdin" in out and "Mutable default" in out
    with pytest.raises(SystemExit) as exc:
        main(["-", "--provider", "mock"])
    assert exc.value.code == 2


@pytest.mark.parametrize("bad", [["--temperature", "9"], ["--provider", "claude"], ["--jobs", "0"], ["--fail-on", "critical"]])
def test_invalid_arguments_exit_2(bad):
    with pytest.raises(SystemExit) as exc:
        main([JS, *bad])
    assert exc.value.code == 2


def test_no_paths_is_a_usage_error():
    with pytest.raises(SystemExit) as exc:
        main(["--provider", "mock"])
    assert exc.value.code == 2


def test_model_flag_applies_to_the_selected_provider(monkeypatch, capsys):
    seen = {}
    monkeypatch.setattr(cli, "create_provider", lambda s: seen.setdefault("settings", s) and ScriptedProvider(["x"]))
    run([JS, "--provider", "openai", "--model", "gpt-4.1-mini", "--temperature", "0.3", "--thinking-level", "low"], capsys)
    s = seen["settings"]
    assert (s.provider, s.openai_model, s.temperature, s.gemini_thinking_level) == ("openai", "gpt-4.1-mini", 0.3, "low")


def test_doctor(capsys):
    code, out, _ = run(["--doctor", "--provider", "mock"], capsys)
    assert code == 0 and "Provider      : mock" in out
    code, out, _ = run(["--doctor", "--provider", "mock", "--ping"], capsys)
    assert code == 0 and "OK" in out
    code, out, _ = run(["--doctor"], capsys)
    assert code == 3 and "No API key" in out and "(not set)" in out
