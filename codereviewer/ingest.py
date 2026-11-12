"""Phase 1: input and payload capture.

Source code is read as **bytes** and decoded explicitly, never through text-mode
universal newlines, so indentation, tabs and carriage returns reach the model
exactly as they are on disk. The file content is treated as an untrusted string.

Failure modes are triaged into one exception type with a machine-readable
``kind`` so callers can show a clean message instead of a stack trace:

    not_found | is_directory | permission | io | empty | too_large | binary | unsupported
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

LANGUAGES: dict[str, str] = {
    ".py": "python", ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".java": "java", ".c": "c", ".h": "c",
    ".cpp": "cpp", ".cc": "cpp", ".hpp": "cpp", ".cs": "csharp", ".go": "go", ".rs": "rust",
    ".kt": "kotlin", ".php": "php", ".rb": "ruby", ".sh": "bash",
}
SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".idea", ".vscode"}


class IngestError(Exception):
    """A file could not be turned into a reviewable string."""

    def __init__(self, message: str, kind: str, path: Path | None = None):
        super().__init__(message)
        self.kind = kind
        self.path = path


@dataclass
class SourceFile:
    name: str
    language: str
    text: str
    encoding: str = "utf-8"
    newline: str = "\n"            # "\n", "\r\n", "\r", "mixed" or "none"
    had_bom: bool = False
    path: Path | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def line_count(self) -> int:
        return len(split_lines(self.text))


_LINE_SPLIT = re.compile(r"\r\n|\r|\n")


def split_lines(text: str) -> list[str]:
    """Split like an editor does (``\\r\\n``, ``\\r`` and ``\\n`` only; a trailing newline adds no line)."""
    lines = _LINE_SPLIT.split(text)
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def detect_newline(text: str) -> str:
    crlf = text.count("\r\n")
    lone_cr = text.count("\r") - crlf
    lone_lf = text.count("\n") - crlf
    kinds = [(crlf, "\r\n"), (lone_lf, "\n"), (lone_cr, "\r")]
    present = [k for n, k in kinds if n]
    if not present:
        return "none"
    return present[0] if len(present) == 1 else "mixed"


def language_for(path: Path | str, override: str | None = None) -> str:
    if override:
        return override.strip().lower()
    suffix = Path(path).suffix.lower()
    if suffix not in LANGUAGES:
        supported = ", ".join(sorted(LANGUAGES))
        raise IngestError(
            f"Unsupported file type {suffix or '(no extension)'!r}. Supported: {supported}. "
            "Use --language to force one.",
            "unsupported",
            Path(path),
        )
    return LANGUAGES[suffix]


def decode_bytes(data: bytes) -> tuple[str, str, bool, list[str]]:
    """Decode with a fallback ladder. Returns ``(text, encoding, had_bom, warnings)``."""
    warnings: list[str] = []
    if data.startswith(b"\xef\xbb\xbf"):
        warnings.append("UTF-8 byte-order mark removed.")
        return data[3:].decode("utf-8"), "utf-8", True, warnings
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        warnings.append("File is UTF-16; decoded and will be reviewed as text.")
        return data.decode("utf-16"), "utf-16", True, warnings
    try:
        return data.decode("utf-8"), "utf-8", False, warnings
    except UnicodeDecodeError:
        pass
    try:
        text = data.decode("cp1252")
        warnings.append("Not valid UTF-8; decoded as Windows-1252. Check accented characters.")
        return text, "cp1252", False, warnings
    except UnicodeDecodeError:
        warnings.append("Not valid UTF-8 or Windows-1252; decoded as Latin-1. Some characters may be wrong.")
        return data.decode("latin-1"), "latin-1", False, warnings


def from_text(text: str, name: str = "pasted_code", language: str = "python") -> SourceFile:
    """Build a ``SourceFile`` from a string (stdin, a text box)."""
    if not text.strip():
        raise IngestError("The code is empty; there is nothing to review.", "empty")
    return SourceFile(name=name, language=language.lower(), text=text, newline=detect_newline(text))


def source_from_bytes(data: bytes, name: str, language: str, *, path: Path | None = None) -> SourceFile:
    """Shared by files on disk and uploads: reject empty/binary input, decode with the fallback ladder."""
    where = path or name
    if not data.strip():
        raise IngestError(f"{where} is empty; there is nothing to review.", "empty", path)
    if b"\x00" in data[:8192] and not data.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise IngestError(f"{where} looks like a binary file, not source code.", "binary", path)
    text, encoding, had_bom, warnings = decode_bytes(data)
    return SourceFile(
        name=Path(name).name, language=language, text=text, encoding=encoding, newline=detect_newline(text),
        had_bom=had_bom, path=path, warnings=warnings,
    )


def read_source(path: str | Path, *, language: str | None = None, max_bytes: int = 512_000) -> SourceFile:
    path = Path(path)
    lang = language_for(path, language)
    try:
        if not path.exists():
            raise IngestError(f"File not found: {path}", "not_found", path)
        if path.is_dir():
            raise IngestError(f"{path} is a directory; pass a file or use --recursive.", "is_directory", path)
        size = path.stat().st_size
        if size > max_bytes:
            raise IngestError(
                f"{path} is {size:,} bytes, above the {max_bytes:,}-byte limit (set MAX_FILE_BYTES to change it).",
                "too_large", path,
            )
        data = path.read_bytes()
    except PermissionError as exc:
        raise IngestError(f"Permission denied reading {path}.", "permission", path) from exc
    except OSError as exc:
        raise IngestError(f"Could not read {path}: {exc.strerror or exc}", "io", path) from exc
    return source_from_bytes(data, path.name, lang, path=path)


def discover(paths: list[str | Path], *, recursive: bool = False, max_files: int = 200) -> list[Path]:
    """Expand files and directories into reviewable files, skipping vendor/cache folders."""
    found: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            if not recursive:
                raise IngestError(f"{p} is a directory; add --recursive to review its files.", "is_directory", p)
            for child in sorted(p.rglob("*")):
                if any(part in SKIP_DIRS for part in child.parts):
                    continue
                if child.is_file() and child.suffix.lower() in LANGUAGES:
                    found.append(child)
        else:
            found.append(p)
    if len(found) > max_files:
        raise IngestError(f"{len(found)} files found; the limit is {max_files}. Narrow the path or raise --max-files.", "too_large")
    return found
