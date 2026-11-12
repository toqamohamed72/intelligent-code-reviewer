import pytest

from codereviewer.ingest import (IngestError, decode_bytes, detect_newline, discover, from_text, language_for,
                                 read_source, split_lines)


def write(tmp_path, name, data: bytes):
    p = tmp_path / name
    p.write_bytes(data)
    return p


def test_text_is_preserved_exactly_including_crlf_and_tabs(tmp_path):
    data = b"def f():\r\n\treturn 1  \r\n"
    src = read_source(write(tmp_path, "a.py", data))
    assert src.text == data.decode() and src.newline == "\r\n" and not src.warnings
    assert src.language == "python" and src.name == "a.py" and src.line_count == 2


def test_utf8_bom_is_removed_and_flagged(tmp_path):
    src = read_source(write(tmp_path, "a.py", b"\xef\xbb\xbfprint(1)\n"))
    assert src.text == "print(1)\n" and src.had_bom and "byte-order mark" in src.warnings[0]


def test_utf16_is_decoded(tmp_path):
    src = read_source(write(tmp_path, "a.py", "print('hi')\n".encode("utf-16")))
    assert src.encoding == "utf-16" and "print('hi')" in src.text


def test_encoding_fallback_ladder_flags_each_step():
    text, enc, _, warns = decode_bytes(b"# caf\xe9\n")
    assert enc == "cp1252" and "caf\u00e9" in text and "Windows-1252" in warns[0]
    text, enc, _, warns = decode_bytes(b"x = '\x81'\n")
    assert enc == "latin-1" and "Latin-1" in warns[0]
    assert decode_bytes("caf\u00e9".encode())[1] == "utf-8"


@pytest.mark.parametrize("name,data,kind", [
    ("b.py", b"\x00\x01\x02binary\x00", "binary"),
    ("e.py", b"  \n\n", "empty"),
    ("x.unknown", b"data", "unsupported"),
])
def test_rejections(tmp_path, name, data, kind):
    with pytest.raises(IngestError) as exc:
        read_source(write(tmp_path, name, data))
    assert exc.value.kind == kind


def test_missing_directory_and_size_limit(tmp_path):
    with pytest.raises(IngestError) as exc:
        read_source(tmp_path / "nope.py")
    assert exc.value.kind == "not_found"
    d = tmp_path / "dir.py"
    d.mkdir()
    with pytest.raises(IngestError) as exc:
        read_source(d)
    assert exc.value.kind == "is_directory"
    big = write(tmp_path, "big.py", b"x = 1\n" * 100)
    with pytest.raises(IngestError) as exc:
        read_source(big, max_bytes=50)
    assert exc.value.kind == "too_large" and "MAX_FILE_BYTES" in str(exc.value)


def test_permission_error_is_triaged(tmp_path, monkeypatch):
    p = write(tmp_path, "a.py", b"x = 1\n")
    monkeypatch.setattr(type(p), "read_bytes", lambda self: (_ for _ in ()).throw(PermissionError("denied")))
    with pytest.raises(IngestError) as exc:
        read_source(p)
    assert exc.value.kind == "permission"


def test_language_override_and_aliases(tmp_path):
    assert language_for("a.mjs") == "javascript" and language_for("a.PY") == "python"
    assert language_for("whatever.txt", "Python") == "python"
    src = read_source(write(tmp_path, "script.txt", b"print(1)\n"), language="python")
    assert src.language == "python"


def test_line_helpers():
    assert split_lines("a\r\nb\rc\nd\n") == ["a", "b", "c", "d"] and split_lines("") == []
    assert detect_newline("a\nb\n") == "\n" and detect_newline("a\r\nb") == "\r\n"
    assert detect_newline("a\r\nb\n") == "mixed" and detect_newline("abc") == "none"


def test_from_text_and_discover(tmp_path):
    assert from_text("print(1)", "x", "Python").language == "python"
    with pytest.raises(IngestError):
        from_text("   \n", "x")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("x = 1\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "skip.js").write_text("1\n")
    (tmp_path / "notes.txt").write_text("hi")
    with pytest.raises(IngestError):
        discover([tmp_path])
    found = discover([tmp_path], recursive=True)
    assert [p.name for p in found] == ["a.py"]
    with pytest.raises(IngestError):
        discover([tmp_path], recursive=True, max_files=0)
