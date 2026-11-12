import pytest

from codereviewer.contract import ContractError, canonical_language, parse_review
from tests.conftest import contract_text


def problems(text, **kw):
    with pytest.raises(ContractError) as exc:
        parse_review(text, **kw)
    return " | ".join(exc.value.problems)


def test_valid_response_is_parsed_into_structure():
    r = parse_review(contract_text(code="def f():\n    return 2", bullets=[
        "- [HIGH] [SECURITY] line 12: SQL injection. Fix: use parameters.",
        "- [low] [style] lines 3-5: Long function.",
        "- [MEDIUM] [LOGIC] Something without a line number.",
    ]), expected_language="python")
    assert r.explanation == ["Sets a value.", "Nothing else."]
    a, b, c = r.findings
    assert (a.severity, a.category, a.line, a.message, a.fix) == ("HIGH", "SECURITY", 12, "SQL injection.", "use parameters.")
    assert (b.severity, b.category, b.line, b.end_line, b.fix) == ("LOW", "STYLE", 3, 5, "")
    assert c.line is None and c.severity == "MEDIUM"
    assert r.refactored_code == "def f():\n    return 2" and r.code_language == "python" and not r.warnings


def test_no_defects_bullet():
    r = parse_review(contract_text(bullets=["- No defects found."]))
    assert r.no_defects and r.findings == []


def test_missing_headers_are_rejected_with_every_problem_listed():
    text = problems("## BUG_REPORT\n- [LOW] [STYLE] line 1: x.\n")
    assert "Missing required section header '## EXPLANATION'" in text and "'## REFACTORED_CODE'" in text


def test_conversational_filler_is_rejected():
    assert "conversational filler" in problems("Sure! Here is your review:\n\n" + contract_text())


def test_text_after_code_block_and_trailing_chatter_is_rejected():
    assert "only one code block" in problems(contract_text() + "\n\nHope this helps!")


def test_prose_in_bug_report_is_rejected():
    assert "only bullet points" in problems(contract_text(bullets=["The code is mostly fine.", "- [LOW] [STYLE] line 1: x."]))


def test_empty_sections_and_empty_code():
    assert "BUG_REPORT is empty" in problems("## EXPLANATION\n- a\n## BUG_REPORT\n\n## REFACTORED_CODE\n```python\nx\n```")
    assert "block is empty" in problems(contract_text(code=""))


def test_sections_out_of_order_duplicated_or_unexpected():
    swapped = "## BUG_REPORT\n- No defects found.\n## EXPLANATION\n- a\n## REFACTORED_CODE\n```python\nx\n```"
    assert "out of order" in problems(swapped)
    assert "appears 2 times" in problems(contract_text() + "\n## BUG_REPORT\n- another")
    assert "Unexpected section '## SUMMARY'" in problems(contract_text() + "\n## SUMMARY\n- more")


def test_exactly_one_code_block_and_unclosed_fence():
    two = contract_text() + "\n\n```python\ny = 2\n```"
    assert "exactly one fenced code block, found 2" in problems(two)
    cut = "## EXPLANATION\n- a\n## BUG_REPORT\n- No defects found.\n## REFACTORED_CODE\n```python\ndef f():\n    return"
    assert "unclosed code fence" in problems(cut)


def test_headers_inside_code_fences_are_code_not_headers():
    code = 'doc = """\n## BUG_REPORT\n- not a bullet\n"""'
    r = parse_review(contract_text(code=code))
    assert r.refactored_code == code and len(r.findings) == 1


def test_longer_fences_can_contain_backtick_fences():
    text = "## EXPLANATION\n- a\n## BUG_REPORT\n- No defects found.\n## REFACTORED_CODE\n````markdown\n```python\nx = 1\n```\n````"
    assert parse_review(text).refactored_code == "```python\nx = 1\n```"


def test_whole_answer_wrapped_in_a_markdown_fence_is_unwrapped():
    assert parse_review("```markdown\n" + contract_text() + "\n```").refactored_code == "x = 1"


def test_explanation_can_be_disabled_and_then_is_forbidden():
    r = parse_review(contract_text(explain=False), require_explanation=False)
    assert r.explanation == []
    assert "was not requested" in problems(contract_text(explain=True), require_explanation=False)


def test_line_number_prefixes_in_code_are_stripped_with_a_warning():
    r = parse_review(contract_text(code="1 | def f():\n2 |     return 1"))
    assert r.refactored_code == "def f():\n    return 1" and "Line-number prefixes" in r.warnings[0]


def test_untagged_bullets_and_wrong_language_tag_are_warnings_not_rejections():
    r = parse_review(contract_text(language="js", bullets=["- Just a plain bullet."]), expected_language="python")
    assert r.findings[0].severity is None
    assert any("did not use" in w for w in r.warnings) and any("tagged 'js'" in w for w in r.warnings)


def test_language_aliases_do_not_warn():
    assert canonical_language("JS") == "javascript" and canonical_language("c++") == "cpp"
    assert not parse_review(contract_text(language="js", code="let a;"), expected_language="javascript").warnings
