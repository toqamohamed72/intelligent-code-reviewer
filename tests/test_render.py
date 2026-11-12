import io

from rich.console import Console

from codereviewer.ingest import from_text
from codereviewer.pipeline import CodeReviewer, FileReport
from codereviewer.render import (finding_bullet, render_failure, render_result, render_summary, to_html,
                                 to_markdown, unified_diff)
from tests.conftest import contract_text
from tests.fakes import ScriptedProvider

ORIGINAL = "def add(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b"


def make_result(settings, code=FIXED, bullets=None):
    bullets = bullets or ["- [HIGH] [LOGIC] line 2: Subtracts instead of adding. Fix: use +."]
    return CodeReviewer(ScriptedProvider([contract_text(code=code, bullets=bullets)]), settings).review(from_text(ORIGINAL, "add.py", "python"))


def terminal():
    buf = io.StringIO()
    return Console(file=buf, force_terminal=True, color_system="truecolor", width=100), buf


def test_markdown_has_the_three_contract_sections_in_order(settings):
    md = to_markdown(make_result(settings))
    assert md.index("## EXPLANATION") < md.index("## BUG_REPORT") < md.index("## REFACTORED_CODE")
    assert "# Code review: `add.py`" in md and "- [HIGH] [LOGIC] line 2: Subtracts instead of adding. Fix: use +." in md
    assert "```python\ndef add(a, b):\n    return a + b\n```" in md


def test_fence_grows_when_the_code_contains_backticks(settings):
    res = make_result(settings, code='s = "```"\n')
    assert "````python" in to_markdown(res)


def test_static_findings_are_labelled_and_no_defect_case_is_explicit(settings):
    res = CodeReviewer(ScriptedProvider([contract_text(bullets=["- No defects found."])]), settings).review(from_text("x = eval(s)\n", "e.py", "python"))
    assert "*(static pre-check)*" in to_markdown(res)
    clean = CodeReviewer(ScriptedProvider([contract_text(bullets=["- No defects found."])]), settings).review(from_text("x = 1\n", "c.py", "python"))
    assert "- No defects found." in to_markdown(clean)
    assert finding_bullet(clean.findings[0]) if clean.findings else True


def test_terminal_rendering_is_syntax_highlighted(settings):
    console, buf = terminal()
    render_result(make_result(settings), console)
    out = buf.getvalue()
    assert "\x1b[" in out and "BUG_REPORT" in out and "REFACTORED_CODE" in out and "add.py" in out
    assert "1 HIGH" in out and "Subtracts instead of adding" in out and "tokens:" in out
    plain = Console(file=io.StringIO(), force_terminal=False, width=100)
    render_result(make_result(settings), plain)
    assert "\x1b[" not in plain.file.getvalue()


def test_diff_shows_what_changed(settings):
    res = make_result(settings)
    diff = unified_diff(res)
    assert "--- a/add.py" in diff and "-    return a - b" in diff and "+    return a + b" in diff
    console, buf = terminal()
    render_result(res, console, show_diff=True)
    assert "Changes" in buf.getvalue()
    same = make_result(settings, code=ORIGINAL.rstrip("\n"))
    assert unified_diff(same) == ""


def test_html_export_contains_the_report(settings):
    html = to_html(make_result(settings))
    assert "<span" in html and "BUG_REPORT" in html and "add.py" in html


def test_failure_and_summary_views(settings):
    console, buf = terminal()
    bad = CodeReviewer(ScriptedProvider(["garbage"]), settings).review(from_text("x = 1\n", "x.py", "python"))
    report = FileReport(None, bad, bad.error, "review")
    render_failure(report, console, show_rejected=True)
    assert "Could not review" in buf.getvalue() and "garbage" in buf.getvalue()
    good = FileReport(None, make_result(settings))
    console, buf = terminal()
    render_summary([good, report], console)
    assert "reviewed" in buf.getvalue() and "review" in buf.getvalue()
