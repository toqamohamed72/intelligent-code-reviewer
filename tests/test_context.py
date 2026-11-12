import ast

from codereviewer.context import estimate_tokens, number_lines, plan_chunks
from codereviewer.ingest import from_text, split_lines


def py_file(n_funcs=40):
    body = ["import os", "import sys", ""]
    for i in range(n_funcs):
        body += ["@decorator", f"def func_{i}(x):", f"    y = x + {i}", "    return y * 2", ""]
    return "\n".join(body) + "\n"


def test_numbering_and_token_estimate():
    assert number_lines(["a", "b"], 9) == " 9 | a\n10 | b"
    assert number_lines(["a"]) == "1 | a" and estimate_tokens("x" * 35) == 10


def test_small_file_is_a_single_whole_chunk():
    chunks = plan_chunks(from_text("x = 1\ny = 2\n", "a.py", "python"), 1000)
    assert len(chunks) == 1 and chunks[0].is_whole and (chunks[0].start_line, chunks[0].end_line) == (1, 2)


def test_python_chunks_cover_every_line_once_and_cut_on_definitions():
    src = from_text(py_file(), "big.py", "python")
    lines = split_lines(src.text)
    chunks = plan_chunks(src, 300)
    assert len(chunks) > 3
    covered = [n for c in chunks for n in range(c.start_line, c.end_line + 1)]
    assert covered == list(range(1, len(lines) + 1))
    for c in chunks[1:]:
        assert lines[c.start_line - 1].startswith("@decorator")          # never mid-function
        ast.parse("\n".join(c.lines))                                   # each chunk is parseable on its own
    assert "import os" in chunks[1].shared_context and chunks[0].total == len(chunks)


def test_non_python_uses_line_windows_preferring_blank_lines():
    text = "\n".join(("" if i % 10 == 0 else f"line {i};") for i in range(1, 400)) + "\n"
    src = from_text(text, "a.js", "javascript")
    lines = split_lines(text)
    chunks = plan_chunks(src, 400)
    assert len(chunks) > 2
    assert [n for c in chunks for n in range(c.start_line, c.end_line + 1)] == list(range(1, len(lines) + 1))
    assert all(not lines[c.end_line - 1].strip() for c in chunks[:-1])   # cut at blank lines when possible


def test_a_single_huge_function_is_split_with_a_warning():
    body = "def huge():\n" + "".join(f"    v{i} = {i}\n" for i in range(300)) + "\ndef small():\n    return 1\n"
    chunks = plan_chunks(from_text(body, "h.py", "python"), 500)
    assert len(chunks) > 2 and any("exceeds the budget" in w for c in chunks for w in c.warnings)


def test_unparseable_python_falls_back_to_windows():
    text = "def broken(:\n" + "x = 1\n" * 400
    chunks = plan_chunks(from_text(text, "b.py", "python"), 300)
    assert len(chunks) > 1 and any("line windows" in w for w in chunks[0].warnings)
