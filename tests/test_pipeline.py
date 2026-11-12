import json
import re

from codereviewer.config import ConfigError, Settings
from codereviewer.ingest import from_text
from codereviewer.pipeline import CodeReviewer
from codereviewer.providers import ProviderError
from tests.conftest import contract_text
from tests.fakes import ScriptedProvider


def reviewer(script, settings, **over):
    s = Settings(**{**settings.__dict__, **over})
    provider = ScriptedProvider(script)
    return CodeReviewer(provider, s), provider


def src(code="x = 1\n", lang="python", name="a.py"):
    return from_text(code, name, lang)


def test_happy_path_aggregates_structure_and_usage(settings):
    r, provider = reviewer([contract_text(code="x = 2")], settings)
    res = r.review(src())
    assert res.ok and res.refactored_code == "x = 2" and res.explanation == ["Sets a value.", "Nothing else."]
    assert res.counts()["HIGH"] == 1 and res.findings[0].source == "model"
    assert (res.prompt_tokens, res.completion_tokens, res.thinking_tokens) == (100, 50, 10)
    call = provider.calls[0]
    assert call["max"] == settings.max_output_tokens and call["temperature"] is None
    assert "## BUG_REPORT" in call["system"] and "1 | x = 1" in call["user"]
    assert json.dumps(res.to_dict())            # serialisable


def test_contract_violation_triggers_one_repair_with_validator_feedback(settings):
    r, provider = reviewer(["Sure! Here is my review.", contract_text()], settings)
    res = r.review(src())
    assert res.ok and res.chunks[0].attempts == 2 and len(provider.calls) == 2
    assert "VALIDATOR_FEEDBACK" in provider.calls[1]["user"] and "conversational filler" in provider.calls[1]["user"]
    assert "VALIDATOR_FEEDBACK" not in provider.calls[0]["user"]


def test_persistent_violation_is_rejected_and_nothing_malformed_is_returned(settings):
    r, provider = reviewer(["garbage", "still garbage"], settings)
    res = r.review(src())
    assert not res.ok and "Rejected by the output validator" in res.error and len(provider.calls) == 2
    assert res.refactored_parts == [] and res.findings == [] and res.chunks[0].raw == "still garbage"


def test_refactored_code_that_does_not_parse_is_sent_back_once(settings):
    bad, good = contract_text(code="def f(:\n    pass"), contract_text(code="def f():\n    pass")
    r, provider = reviewer([bad, good], settings)
    res = r.review(src())
    assert res.ok and res.refactored_code == "def f():\n    pass" and res.chunks[0].syntax.status == "ok"
    assert "does not compile" in provider.calls[1]["user"]


def test_code_that_stays_broken_is_kept_but_flagged(settings):
    r, _ = reviewer([contract_text(code="def f(:\n    pass")], settings)
    res = r.review(src())
    assert res.ok and any("failed the syntax check" in w for w in res.warnings)


def test_unverifiable_language_adds_a_note_not_an_error(settings):
    r, _ = reviewer([contract_text(language="java", code="class A {}")], settings)
    res = r.review(src("class A {}", "java", "A.java"))
    assert res.ok and any("not compiled" in n for n in res.notes)


def test_static_findings_missed_by_the_model_are_added_not_duplicated(settings):
    code = "def f(a=[]):\n    return eval(a)\n"
    r, provider = reviewer([contract_text(code=code, bullets=["- [HIGH] [SECURITY] line 2: eval is dangerous. Fix: avoid it."])], settings)
    res = r.review(src(code))
    by = [(f.category, f.source) for f in res.findings]
    assert ("SECURITY", "model") in by and ("LOGIC", "static") in by and by.count(("SECURITY", "static")) == 0
    assert "Pre-detected by deterministic static analysis" in provider.calls[0]["user"]

    r, _ = reviewer([contract_text(code=code, bullets=["- No defects found."])], settings)
    res = r.review(src(code))
    assert any("reported no defects" in w for w in res.warnings) and {f.source for f in res.findings} == {"static"}


def test_provider_and_config_errors_become_values(settings):
    r, _ = reviewer([ProviderError("Rate limit or quota exceeded.", "rate_limit")], settings)
    res = r.review(src())
    assert not res.ok and "Rate limit" in res.error
    r, _ = reviewer([ConfigError("GEMINI_API_KEY is not set.")], settings)
    rep = r.review_path(__file__.replace("test_pipeline.py", "conftest.py"))
    assert not rep.ok and rep.error_kind == "config" and "GEMINI_API_KEY" in rep.error


def test_no_explain_changes_the_contract(settings):
    r, provider = reviewer([contract_text(explain=False)], settings)
    res = r.review(src(), explain=False)
    assert res.ok and res.explanation == [] and "## EXPLANATION" not in provider.calls[0]["system"]


def big_python():
    return "import os\n\n" + "".join(f"def f{i}(x):\n    return x + {i}\n\n" for i in range(80))


def chunk_answer(system, user):
    a, b = map(int, re.search(r"lines (\d+)-(\d+)", user).groups())
    return contract_text(code=f"# refactored {a}-{b}", bullets=[f"- [LOW] [STYLE] line {a}: note for {a}.", f"- [HIGH] [LOGIC] line {b}: bug at {b}."])


def test_large_files_are_chunked_and_merged(settings):
    r, provider = reviewer([chunk_answer], settings, max_context_tokens=300)
    res = r.review(src(big_python(), name="big.py"))
    n = len(res.chunks)
    assert res.ok and n > 3 and len(provider.calls) == n and any("reviewed in" in x for x in res.notes)
    assert [p[2] for p in res.refactored_parts][0].startswith("# refactored 1-")
    assert res.explanation[0].startswith("Lines 1-")
    assert [f.rank for f in res.findings] == sorted((f.rank for f in res.findings), reverse=True)
    assert "CONTEXT_BEGIN" in provider.calls[1]["user"] and "CONTEXT_BEGIN" not in provider.calls[0]["user"]


def test_failure_in_one_part_is_reported_with_its_position(settings):
    script = [chunk_answer, chunk_answer, ProviderError("The model returned an empty response.", "empty")]
    r, _ = reviewer(script, settings, max_context_tokens=300)
    res = r.review(src(big_python(), name="big.py"))
    assert not res.ok and res.error.startswith("Part 3/") and len(res.refactored_parts) == 2


def test_review_many_preserves_order_and_isolates_failures(settings, tmp_path):
    good, odd, empty = tmp_path / "a.py", tmp_path / "b.txt", tmp_path / "c.py"
    good.write_text("x = 1\n")
    odd.write_text("hi")
    empty.write_text("")
    r, _ = reviewer([contract_text()], settings)
    reports = r.review_many([good, tmp_path / "missing.py", odd, empty], jobs=3)
    assert [p.path.name for p in reports] == ["a.py", "missing.py", "b.txt", "c.py"]
    assert [p.ok for p in reports] == [True, False, False, False]
    assert [p.error_kind for p in reports[1:]] == ["not_found", "unsupported", "empty"]


def test_provider_failure_for_one_file_does_not_stop_the_others(settings, tmp_path):
    files = []
    for name in ("a.py", "bad.py", "c.py"):
        p = tmp_path / name
        p.write_text(f"# {name}\nx = 1\n")
        files.append(p)

    def answer(system, user):
        if "bad.py" in user:
            raise ProviderError("boom", "server")
        return contract_text()

    r, _ = reviewer([answer], settings)
    assert [x.ok for x in r.review_many(files, jobs=1)] == [True, False, True]
