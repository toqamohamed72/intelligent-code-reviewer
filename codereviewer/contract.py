"""Structured-output validation: the gate between the model and the pipeline.

The model must answer with these markdown sections, in order::

    ## EXPLANATION       (optional by configuration)
    ## BUG_REPORT        (bullets only)
    ## REFACTORED_CODE   (exactly one fenced code block)

Anything else is a contract violation and the response is **rejected**, so a
malformed report is never pushed downstream. Parsing is fence-aware: a line such
as ``## BUG_REPORT`` inside a code block is code, not a header.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

HEADER_RE = re.compile(r"^##\s+([A-Z_]+)\s*$")
ANY_HEADING_RE = re.compile(r"^#{1,6}\s+\S")
FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})(.*)$")
BULLET_RE = re.compile(r"^\s*[-*\u2022]\s+(.*\S)\s*$")
FINDING_RE = re.compile(
    r"^\[(HIGH|MEDIUM|LOW)\]\s*\[([A-Za-z_ ]+)\]\s*"
    r"(?:lines?\s*(\d+)(?:\s*[-\u2013\u2014]\s*(\d+))?\s*[:\-\u2013]?\s*)?(.*)$",
    re.I | re.S,
)
NO_DEFECTS_RE = re.compile(r"^no\s+(defects|issues|bugs|problems)(\s+found)?\.?$", re.I)
LINE_PREFIX_RE = re.compile(r"^\s*\d+ \| ?")

SEVERITY_ORDER = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}
_ALIASES = {
    "py": "python", "python3": "python", "js": "javascript", "node": "javascript", "ts": "typescript",
    "c++": "cpp", "cc": "cpp", "cs": "csharp", "c#": "csharp", "sh": "bash", "shell": "bash",
    "rs": "rust", "kt": "kotlin", "rb": "ruby", "golang": "go",
}


def canonical_language(tag: str) -> str:
    tag = tag.strip().lower()
    return _ALIASES.get(tag, tag)


class ContractError(Exception):
    """The response violates the output contract."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass
class Finding:
    severity: str | None       # HIGH | MEDIUM | LOW | None when the bullet was not tagged
    category: str | None
    line: int | None
    end_line: int | None
    message: str
    fix: str = ""
    source: str = "model"      # model | static

    @property
    def rank(self) -> int:
        return SEVERITY_ORDER.get(self.severity or "", 0)

    def to_dict(self) -> dict:
        return {"severity": self.severity, "category": self.category, "line": self.line,
                "end_line": self.end_line, "message": self.message, "fix": self.fix, "source": self.source}


@dataclass
class ParsedReview:
    explanation: list[str]
    findings: list[Finding]
    no_defects: bool
    refactored_code: str
    code_language: str
    warnings: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #

def _unwrap_outer_fence(text: str) -> str:
    """Some models wrap the whole answer in ```markdown ... ```. Remove that wrapper."""
    lines = text.strip().splitlines()
    if len(lines) >= 3 and re.match(r"^```(?:markdown|md)?\s*$", lines[0]) and lines[-1].strip() == "```":
        inner = "\n".join(lines[1:-1])
        if re.search(r"^##\s+BUG_REPORT\s*$", inner, re.M):
            return inner
    return text


def split_sections(text: str) -> tuple[list[str], list[tuple[str, list[str]]]]:
    """Return ``(preamble_lines, [(HEADER, body_lines), ...])`` ignoring headers inside code fences."""
    preamble: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    fence: tuple[str, int] | None = None
    for line in text.splitlines():
        in_fence = fence is not None
        m = FENCE_RE.match(line)
        if m:
            marker, rest = m.group(1), m.group(2)
            if fence is None:
                fence = (marker[0], len(marker))
            elif marker[0] == fence[0] and len(marker) >= fence[1] and not rest.strip():
                fence = None
        header = None if in_fence or m else HEADER_RE.match(line)
        if header:
            sections.append((header.group(1), []))
        elif sections:
            sections[-1][1].append(line)
        else:
            preamble.append(line)
    return preamble, sections


def _parse_bullets(body: list[str], problems: list[str], name: str) -> list[str]:
    bullets: list[str] = []
    stray: list[str] = []
    for line in body:
        if not line.strip():
            continue
        m = BULLET_RE.match(line)
        if m:
            bullets.append(m.group(1))
        elif bullets and line.startswith(("  ", "\t")):
            bullets[-1] += " " + line.strip()          # wrapped continuation of the previous bullet
        else:
            stray.append(line.strip())
    if stray:
        shown = "; ".join(f"'{s[:50]}'" for s in stray[:3])
        problems.append(f"{name} must contain only bullet points, but has other text: {shown}.")
    if not bullets:
        problems.append(f"{name} is empty; it needs at least one bullet point.")
    return bullets


def _parse_finding(bullet: str) -> Finding:
    m = FINDING_RE.match(bullet)
    if not m:
        return Finding(None, None, None, None, bullet)
    sev, cat, start, end, rest = m.groups()
    message, fix = rest.strip(), ""
    split = re.split(r"\bFix:\s*", rest, maxsplit=1, flags=re.I)
    if len(split) == 2:
        message, fix = split[0].strip(), split[1].strip()
    return Finding(
        sev.upper(), cat.strip().upper().replace(" ", "_"), int(start) if start else None,
        int(end) if end else None, message, fix,
    )


def _parse_code(body: list[str], problems: list[str]) -> tuple[str, str]:
    blocks: list[tuple[str, list[str]]] = []
    outside: list[str] = []
    fence: tuple[str, int] | None = None
    for line in body:
        m = FENCE_RE.match(line)
        if fence is None:
            if m:
                fence = (m.group(1)[0], len(m.group(1)))
                blocks.append((m.group(2).strip(), []))
            elif line.strip():
                outside.append(line.strip())
            continue
        if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= fence[1] and not m.group(2).strip():
            fence = None
        else:
            blocks[-1][1].append(line)
    if fence is not None:
        problems.append("REFACTORED_CODE has an unclosed code fence (the output may have been cut off).")
    if outside:
        problems.append(f"REFACTORED_CODE must contain only one code block, but has other text: '{outside[0][:50]}'.")
    if len(blocks) != 1:
        problems.append(f"REFACTORED_CODE must contain exactly one fenced code block, found {len(blocks)}.")
        return "", ""
    tag, lines = blocks[0]
    code = "\n".join(lines)
    if not code.strip():
        problems.append("The REFACTORED_CODE block is empty.")
    return tag.split()[0] if tag else "", code


def parse_review(text: str, *, require_explanation: bool = True, expected_language: str | None = None) -> ParsedReview:
    """Validate ``text`` against the contract. Raises ``ContractError`` listing every problem found."""
    problems: list[str] = []
    warnings: list[str] = []
    text = _unwrap_outer_fence(text)
    preamble, sections = split_sections(text)

    if any(line.strip() for line in preamble):
        first = next(line for line in preamble if line.strip())
        problems.append(f"Text before the first section header (conversational filler): '{first.strip()[:60]}'.")

    required = (["EXPLANATION"] if require_explanation else []) + ["BUG_REPORT", "REFACTORED_CODE"]
    names = [name for name, _ in sections]
    for name in required:
        if name not in names:
            problems.append(f"Missing required section header '## {name}'.")
    for name in sorted(set(names)):
        if names.count(name) > 1:
            problems.append(f"Section '## {name}' appears {names.count(name)} times.")
        if name not in ("EXPLANATION", "BUG_REPORT", "REFACTORED_CODE"):
            problems.append(f"Unexpected section '## {name}'.")
    if not require_explanation and "EXPLANATION" in names:
        problems.append("Unexpected section '## EXPLANATION' (it was not requested).")
    present = [n for n in names if n in required]
    if not problems and present != required:
        problems.append(f"Sections are out of order: expected {', '.join(required)}; got {', '.join(present)}.")

    body = {name: lines for name, lines in sections}
    for name, lines in sections:
        if name != "REFACTORED_CODE":
            stray_heading = next((ln for ln in lines if ANY_HEADING_RE.match(ln)), None)
            if stray_heading:
                problems.append(f"Unexpected heading inside {name}: '{stray_heading.strip()[:50]}'.")

    explanation: list[str] = []
    if require_explanation and "EXPLANATION" in body:
        for ln in body["EXPLANATION"]:
            if not ln.strip():
                continue
            m = BULLET_RE.match(ln)
            if m:
                explanation.append(m.group(1))
            elif explanation and ln.startswith(("  ", "\t")):
                explanation[-1] += " " + ln.strip()
            else:
                explanation.append(ln.strip())
        if not explanation:
            problems.append("EXPLANATION is empty.")

    findings: list[Finding] = []
    no_defects = False
    if "BUG_REPORT" in body:
        bullets = _parse_bullets(body["BUG_REPORT"], problems, "BUG_REPORT")
        if len(bullets) == 1 and NO_DEFECTS_RE.match(bullets[0].strip()):
            no_defects = True
        else:
            findings = [_parse_finding(b) for b in bullets]
            untagged = sum(1 for f in findings if f.severity is None)
            if untagged:
                warnings.append(f"{untagged} bug-report bullet(s) did not use the [SEVERITY] [CATEGORY] format.")

    code, tag = "", ""
    if "REFACTORED_CODE" in body:
        tag, code = _parse_code(body["REFACTORED_CODE"], problems)

    if problems:
        raise ContractError(problems)

    code_lines = code.splitlines()
    nonblank = [ln for ln in code_lines if ln.strip()]
    if nonblank and all(LINE_PREFIX_RE.match(ln) for ln in nonblank):
        code = "\n".join(LINE_PREFIX_RE.sub("", ln, count=1) for ln in code_lines)
        warnings.append("Line-number prefixes were found in the refactored code and removed.")
    if expected_language and tag and canonical_language(tag) != canonical_language(expected_language):
        warnings.append(f"The code block is tagged '{tag}' but the file is {expected_language}.")
    return ParsedReview(explanation, findings, no_defects, code, canonical_language(tag) if tag else "", warnings)
