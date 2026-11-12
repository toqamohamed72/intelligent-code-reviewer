"""Phase 2: context orchestration ("code-as-context" management).

Three jobs:

1. **Number the lines.** Models are poor at counting lines, so every line is
   prefixed with its number (``  12 | code``) and the prompt says line numbers
   refer to that listing. This makes the bug report's "line 12" trustworthy.
2. **Budget the context.** Tokens are estimated (about 3.5 characters per token
   for code; an approximation, not a tokenizer) and compared with a per-request
   budget.
3. **Chunk when needed.** Over budget, Python files are split on top-level
   definition boundaries (never inside a function); other languages are split
   into line windows that prefer blank lines. Each Python chunk also receives the
   module's import lines as read-only context.

Known limitation: chunks are reviewed independently, so a bug that spans two
chunks (e.g. a function in chunk 1 called wrongly in chunk 3) can be missed.
"""
from __future__ import annotations

import ast
import math
from dataclasses import dataclass, field

from .ingest import SourceFile, split_lines

CHARS_PER_TOKEN = 3.5


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def number_lines(lines: list[str], start: int = 1) -> str:
    width = len(str(start + len(lines) - 1))
    return "\n".join(f"{start + i:>{width}} | {line}" for i, line in enumerate(lines))


@dataclass
class Chunk:
    index: int
    total: int
    start_line: int
    end_line: int
    lines: list[str]
    shared_context: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def is_whole(self) -> bool:
        return self.total == 1

    @property
    def numbered(self) -> str:
        return number_lines(self.lines, self.start_line)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def _python_units(source: SourceFile, lines: list[str]) -> tuple[list[tuple[int, int]], list[tuple[int, int, str]]] | None:
    """Contiguous (start, end) 1-based line ranges, one per top-level statement, plus import statements."""
    try:
        tree = ast.parse(source.text)
    except (SyntaxError, ValueError, RecursionError):
        return None
    if not tree.body:
        return None
    imports: list[tuple[int, int, str]] = []
    starts: list[int] = []
    for node in tree.body:
        first = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])
        starts.append(first)
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imports.append((node.lineno, node.end_lineno, "\n".join(lines[node.lineno - 1 : node.end_lineno])))
    starts[0] = 1  # leading comments / docstring belong to the first unit
    units = [(s, (starts[i + 1] - 1) if i + 1 < len(starts) else len(lines)) for i, s in enumerate(starts)]
    return units, imports


def _line_cost(line: str, width: int) -> int:
    return len(line) + width + 4  # "NNN | " prefix and the newline


def _windows(start: int, end: int, lines: list[str], budget_chars: int) -> list[tuple[int, int]]:
    """Split [start, end] into windows under budget, preferring to cut at blank lines."""
    width = len(str(len(lines)))
    out: list[tuple[int, int]] = []
    cur = start
    while cur <= end:
        total, stop = 0, cur
        while stop <= end and (total + _line_cost(lines[stop - 1], width) <= budget_chars or stop == cur):
            total += _line_cost(lines[stop - 1], width)
            stop += 1
        last = stop - 1
        if last < end:                                    # look back for a blank line in the last 20 %
            floor = cur + int((last - cur) * 0.8)
            for probe in range(last, floor, -1):
                if not lines[probe - 1].strip():
                    last = probe
                    break
        out.append((cur, last))
        cur = last + 1
    return out


def plan_chunks(source: SourceFile, budget_tokens: int) -> list[Chunk]:
    """Return one chunk when the file fits the budget, otherwise a contiguous partition of the lines."""
    lines = split_lines(source.text)
    if estimate_tokens(number_lines(lines)) <= budget_tokens:
        return [Chunk(1, 1, 1, len(lines), lines)]

    budget_chars = int(budget_tokens * CHARS_PER_TOKEN)
    width = len(str(len(lines)))
    warnings: list[str] = []
    imports: list[tuple[int, int, str]] = []
    units: list[tuple[int, int]] | None = None
    if source.language == "python":
        planned = _python_units(source, lines)
        if planned:
            units, imports = planned
        else:
            warnings.append("Python parsing failed, so the file was split by line windows instead of definitions.")
    if units is None:
        units = [(1, len(lines))]

    prefix = [0]
    for line in lines:
        prefix.append(prefix[-1] + _line_cost(line, width))

    def cost(a: int, b: int) -> int:
        return prefix[b] - prefix[a - 1]

    groups: list[tuple[int, int]] = []
    for a, b in units:
        if cost(a, b) > budget_chars:                    # one definition is bigger than a whole chunk
            if source.language == "python" and len(units) > 1:
                warnings.append(f"A single definition spanning lines {a}-{b} exceeds the budget and was split.")
            groups.extend(_windows(a, b, lines, budget_chars))
        elif groups and cost(groups[-1][0], b) <= budget_chars:
            groups[-1] = (groups[-1][0], b)             # pack into the previous chunk
        else:
            groups.append((a, b))

    def shared_for(a: int, b: int) -> str:
        """Import statements that live outside this chunk, as read-only context (skipped if too large)."""
        text = "\n".join(t for lo, hi, t in imports if hi < a or lo > b)
        return text if len(text) < budget_chars // 4 else ""

    chunks = [Chunk(i + 1, len(groups), a, b, lines[a - 1 : b], shared_for(a, b)) for i, (a, b) in enumerate(groups)]
    if chunks:
        chunks[0].warnings.extend(warnings)
    return chunks
