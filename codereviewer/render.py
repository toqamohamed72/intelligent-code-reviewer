"""Phase 3: deterministic markdown and rich rendering.

The model's validated output is re-assembled into one canonical markdown document
(``## EXPLANATION`` / ``## BUG_REPORT`` / ``## REFACTORED_CODE``). ``rich.markdown.Markdown``
turns that into highlighted terminal output, so code fences get colour from their
language tag. The same document is saved as ``.md`` or exported as HTML.
"""
from __future__ import annotations

import difflib
import io
import re
from typing import Sequence

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from .contract import Finding
from .ingest import split_lines
from .pipeline import FileReport, ReviewResult

SEVERITY_STYLE = {"HIGH": "bold red", "MEDIUM": "bold yellow", "LOW": "cyan", "UNSPECIFIED": "dim"}
CODE_THEME = "monokai"


def _fence_for(code: str) -> str:
    longest = max((len(m) for m in re.findall(r"`+", code)), default=0)
    return "`" * max(3, longest + 1)


def finding_bullet(f: Finding) -> str:
    if f.severity is None:
        return f"- {f.message}"
    where = ""
    if f.line:
        where = f"line {f.line}" if not f.end_line else f"lines {f.line}-{f.end_line}"
        where += ": "
    text = f"- [{f.severity}] [{f.category}] {where}{f.message}"
    if f.fix:
        text += f" Fix: {f.fix}"
    if f.source == "static":
        text += " *(static pre-check)*"
    return text


def counts_line(result: ReviewResult) -> str:
    c = result.counts()
    return " | ".join(f"{c[k]} {k}" for k in ("HIGH", "MEDIUM", "LOW") if c[k]) or "no defects reported"


def to_markdown(result: ReviewResult, *, title: bool = True) -> str:
    parts: list[str] = []
    if title:
        parts.append(f"# Code review: `{result.source.name}`\n\n"
                     f"*{result.source.language} | {result.provider} / {result.model} | {counts_line(result)}*\n")
    if result.explain:
        parts.append("## EXPLANATION\n" + "\n".join(f"- {e}" for e in result.explanation) + "\n")
    bullets = [finding_bullet(f) for f in result.findings] or ["- No defects found."]
    parts.append("## BUG_REPORT\n" + "\n".join(bullets) + "\n")
    blocks = []
    for start, end, code in result.refactored_parts:
        fence = _fence_for(code)
        label = f"*Lines {start}-{end}*\n\n" if len(result.refactored_parts) > 1 else ""
        blocks.append(f"{label}{fence}{result.source.language}\n{code}\n{fence}")
    parts.append("## REFACTORED_CODE\n" + "\n\n".join(blocks))
    return "\n".join(parts) + "\n"


def unified_diff(result: ReviewResult) -> str:
    original = split_lines(result.source.text)
    out: list[str] = []
    for start, end, code in result.refactored_parts:
        out.extend(difflib.unified_diff(
            original[start - 1 : end], code.splitlines(),
            fromfile=f"a/{result.source.name}", tofile=f"b/{result.source.name}", lineterm="", n=2,
        ))
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# Terminal
# --------------------------------------------------------------------------- #

def _severity_summary(result: ReviewResult) -> Text:
    text = Text()
    counts = result.counts()
    for key in ("HIGH", "MEDIUM", "LOW"):
        if counts[key]:
            text.append(f"{counts[key]} {key}  ", style=SEVERITY_STYLE[key])
    if not text.plain:
        text.append("no defects reported", style="green")
    return text


def render_result(result: ReviewResult, console: Console, *, show_diff: bool = False, show_static: bool = True) -> None:
    header = Text()
    header.append(f"{result.source.name}", style="bold")
    header.append(f"  {result.source.language} | {result.provider} / {result.model}\n", style="dim")
    header.append_text(_severity_summary(result))
    console.print(Panel(header, title="Code review", border_style="blue", expand=False))
    for note in result.notes + result.warnings:
        console.print(f"[yellow]note:[/yellow] {note}", highlight=False)
    if show_static and result.static:
        console.print("[bold]Static pre-check[/bold] (deterministic, before the model)")
        for f in result.static:
            console.print(f"  [{SEVERITY_STYLE[f.severity]}]{f.severity}[/] {f.category} line {f.line}: {f.message}", highlight=False)
        console.print()
    console.print(Markdown(to_markdown(result, title=False), code_theme=CODE_THEME))
    if show_diff:
        diff = unified_diff(result)
        console.print("\n[bold]Changes[/bold] (original -> refactored)")
        console.print(Syntax(diff, "diff", theme=CODE_THEME, word_wrap=True) if diff else "[dim]No changes.[/dim]")
    u = result
    console.print(f"[dim]tokens: {u.prompt_tokens} in / {u.completion_tokens} out"
                  f"{f' / {u.thinking_tokens} thinking' if u.thinking_tokens else ''} | {u.latency_s:.1f}s[/dim]\n")


def render_failure(report: FileReport, console: Console, *, show_rejected: bool = False) -> None:
    name = report.path.name if report.path else "input"
    console.print(Panel(Text(report.error or "Unknown error", style="red"), title=f"Could not review {name}",
                        border_style="red", expand=False))
    if show_rejected and report.result and report.result.chunks and report.result.chunks[-1].raw:
        console.print("[dim]Rejected model response:[/dim]")
        console.print(report.result.chunks[-1].raw, markup=False, highlight=False)


def render_summary(reports: Sequence[FileReport], console: Console) -> None:
    table = Table(title="Review summary", header_style="bold")
    for col in ("File", "Status", "HIGH", "MEDIUM", "LOW"):
        table.add_column(col, justify="left" if col in ("File", "Status") else "right")
    for r in reports:
        name = str(r.path) if r.path else "input"
        if r.ok:
            c = r.result.counts()
            table.add_row(name, "[green]reviewed[/green]", str(c["HIGH"]), str(c["MEDIUM"]), str(c["LOW"]))
        else:
            table.add_row(name, f"[red]{r.error_kind or 'failed'}[/red]", "-", "-", "-")
    console.print(table)


def to_html(result: ReviewResult, *, show_diff: bool = False) -> str:
    console = Console(record=True, force_terminal=True, width=110, file=io.StringIO(), color_system="truecolor")
    render_result(result, console, show_diff=show_diff)
    return console.export_html(inline_styles=True)
