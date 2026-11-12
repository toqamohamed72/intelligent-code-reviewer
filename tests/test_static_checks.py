import shutil

import pytest

from codereviewer.static_checks import precheck, verify_syntax


def kinds(code):
    return {(f.severity, f.category) for f in precheck("python", code)}


@pytest.mark.parametrize("code,expected", [
    ("def f(a, b=[]):\n    return b\n", ("MEDIUM", "LOGIC")),
    ("def f(a, *, b={}):\n    return b\n", ("MEDIUM", "LOGIC")),
    ("try:\n    pass\nexcept:\n    pass\n", ("MEDIUM", "ERROR_HANDLING")),
    ("x = eval(input())\n", ("HIGH", "SECURITY")),
    ("import subprocess\nsubprocess.run('ls', shell=True)\n", ("HIGH", "SECURITY")),
    ("cur.execute(f'SELECT {x}')\n", ("HIGH", "SECURITY")),
    ("cur.execute('SELECT ' + x)\n", ("HIGH", "SECURITY")),
    ("cur.execute('SELECT %s' % x)\n", ("HIGH", "SECURITY")),
    ("cur.execute('SELECT {}'.format(x))\n", ("HIGH", "SECURITY")),
    ("if x is 'a':\n    pass\n", ("MEDIUM", "LOGIC")),
    ("if x == None:\n    pass\n", ("LOW", "STYLE")),
])
def test_each_check_fires(code, expected):
    assert expected in kinds(code)


def test_clean_code_has_no_findings_and_safe_patterns_are_not_flagged():
    code = ("def f(a, b=None):\n    b = b or []\n    cur.execute('SELECT ?', (a,))\n"
            "    if a is None or a is True:\n        pass\n    try:\n        pass\n    except ValueError:\n        pass\n")
    assert precheck("python", code) == []


def test_syntax_error_reports_line_and_stops():
    findings = precheck("python", "def f(:\n    pass\n")
    assert len(findings) == 1 and findings[0].category == "SYNTAX" and findings[0].line == 1


def test_other_languages_have_no_static_prechecks():
    assert precheck("javascript", "var x == ;") == [] and precheck("java", "class {") == []


def test_bullet_format_matches_the_contract():
    f = precheck("python", "x = eval(s)\n")[0]
    assert f.as_bullet().startswith("- [HIGH] [SECURITY] line 1: ")


def test_verify_python():
    assert verify_syntax("python", "x = 1\n").status == "ok"
    bad = verify_syntax("python", "def f(:\n")
    assert bad.status == "error" and "line 1" in bad.message


def test_verify_javascript_without_node_is_skipped(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    verdict = verify_syntax("javascript", "let x = 1;")
    assert verdict.status == "skipped" and "Node.js" in verdict.message


@pytest.mark.skipif(not shutil.which("node"), reason="Node.js not installed")
def test_verify_javascript_with_node():
    assert verify_syntax("javascript", "const a = 1;\nconsole.log(a);\n").status == "ok"
    assert verify_syntax("javascript", "const = ;").status == "error"
    assert verify_syntax("javascript", "import fs from 'fs';\nconsole.log(fs);\n").status == "ok"


def test_unverifiable_languages_are_skipped_honestly():
    assert verify_syntax("java", "class A {}").status == "skipped"
