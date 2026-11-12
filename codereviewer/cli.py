"""Command line:  python review.py path/to/file.py

Exit codes (usable as a CI gate): 0 ok | 1 findings at or above --fail-on |
2 invalid usage | 3 configuration or input problem | 4 the review itself failed.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Sequence

from rich.console import Console

from . import __version__
from .config import ConfigError, Settings
from .contract import SEVERITY_ORDER
from .ingest import IngestError, discover, from_text
from .pipeline import CodeReviewer, FileReport
from .providers import create_provider
from .render import render_failure, render_result, render_summary, to_html, to_markdown

EXIT_OK, EXIT_FINDINGS, EXIT_USAGE, EXIT_INPUT, EXIT_REVIEW = 0, 1, 2, 3, 4


def temperature_type(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a number") from None
    if not 0.0 <= value <= 2.0:
        raise argparse.ArgumentTypeError("temperature must be between 0 and 2")
    return value


def positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not an integer") from None
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="review.py", description="Intelligent Code Reviewer & Explainer: bug report + refactored code, rendered with syntax highlighting.",
        epilog="Use '-' as the path to read code from stdin (then --language is required).",
    )
    p.add_argument("paths", nargs="*", help="source files (.py .js .java ...) or directories with --recursive")
    p.add_argument("-r", "--recursive", action="store_true", help="review supported files inside directories")
    p.add_argument("--language", help="override the language detected from the extension (required for stdin)")
    p.add_argument("--no-explain", action="store_true", help="skip the plain-language EXPLANATION section")
    p.add_argument("--provider", choices=["gemini", "openai", "mock"], help="default: LLM_PROVIDER or gemini")
    p.add_argument("--model", help="model name for the chosen provider")
    p.add_argument("--temperature", type=temperature_type, help="default: the provider's own default (recommended)")
    p.add_argument("--thinking-level", choices=["minimal", "low", "medium", "high"], help="Gemini 3 thinking level")
    p.add_argument("--format", choices=["terminal", "markdown", "json"], default="terminal")
    p.add_argument("--diff", action="store_true", help="also show a unified diff of original vs refactored code")
    p.add_argument("--out", help="write the report: a file for one input, a directory for several")
    p.add_argument("--html", help="also export the rendered report as HTML (file, or directory for several inputs)")
    p.add_argument("--jobs", type=positive_int, help="files reviewed in parallel (default: REVIEW_JOBS or 4)")
    p.add_argument("--max-files", type=positive_int, default=200)
    p.add_argument("--max-context-tokens", type=positive_int, help="chunk files larger than this many (estimated) tokens")
    p.add_argument("--fail-on", choices=["low", "medium", "high"], help="exit 1 when a finding at or above this severity exists")
    p.add_argument("--show-rejected", action="store_true", help="print the raw response of a rejected review")
    p.add_argument("--no-color", action="store_true")
    p.add_argument("--log-level", default="WARNING", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    p.add_argument("--doctor", action="store_true", help="check the configuration (add --ping for one real request)")
    p.add_argument("--ping", action="store_true", help="with --doctor: make one tiny real request")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def _settings(args) -> Settings:
    provider = args.provider
    overrides: dict = {"provider": provider, "temperature": args.temperature,
                       "max_context_tokens": args.max_context_tokens, "jobs": args.jobs,
                       "gemini_thinking_level": args.thinking_level}
    base = Settings.from_env()
    chosen = provider or base.provider
    if args.model:
        overrides["gemini_model" if chosen == "gemini" else "openai_model"] = args.model
    return Settings.from_env(**overrides)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _target(base: str, name: str, suffix: str, multiple: bool) -> Path:
    p = Path(base)
    return (p / f"{name}{suffix}") if multiple or p.is_dir() else p


def _doctor(args, settings: Settings, console: Console) -> int:
    provider = create_provider(settings)
    console.print(f"Python        : {sys.version.split()[0]}")
    console.print(f"Provider      : {settings.provider}")
    console.print(f"Model         : {provider.model}")
    console.print(f"API key       : {settings.masked_key}")
    console.print(f"Temperature   : {settings.temperature if settings.temperature is not None else 'provider default'}")
    console.print(f"Thinking level: {settings.gemini_thinking_level or 'provider default'}")
    console.print(f"Budgets       : context {settings.max_context_tokens} tokens, output {settings.max_output_tokens} tokens, file {settings.max_file_bytes} bytes")
    if settings.provider != "mock" and not settings.api_key:
        console.print("\n[red]No API key is set.[/red] Copy .env.example to .env and add one, or use --provider mock.")
        return EXIT_INPUT
    if not args.ping:
        console.print("\nConfiguration looks fine. Add --ping to test a real request.")
        return EXIT_OK
    report = CodeReviewer(provider, settings).review(
        from_text("def add(a, b):\n    return a - b\n", "ping.py", "python"), explain=False)
    if report.ok:
        console.print(f"\n[green]OK[/green]: {report.model} answered in {report.latency_s:.1f}s "
                      f"({report.prompt_tokens}+{report.completion_tokens} tokens), {len(report.findings)} finding(s).")
        return EXIT_OK
    console.print(f"\n[red]FAILED[/red]: {report.error}")
    return EXIT_REVIEW


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level), format="%(levelname)s %(name)s: %(message)s")
    console = Console(no_color=args.no_color)
    err = Console(stderr=True, no_color=args.no_color)

    try:
        settings = _settings(args)
        if args.doctor:
            return _doctor(args, settings, console)
        if not args.paths:
            parser.error("give at least one file or directory (or --doctor)")
        reviewer = CodeReviewer(create_provider(settings), settings)
        explain = not args.no_explain

        if args.paths == ["-"]:
            if not args.language:
                parser.error("--language is required when reading from stdin")
            try:
                source = from_text(sys.stdin.read(), "stdin", args.language)
            except IngestError as exc:
                err.print(f"[red]{exc}[/red]")
                return EXIT_INPUT
            result = reviewer.review(source, explain=explain)
            reports = [FileReport(None, result, result.error, None if result.ok else "review")]
        else:
            try:
                files = discover(args.paths, recursive=args.recursive, max_files=args.max_files)
            except IngestError as exc:
                err.print(f"[red]{exc}[/red]")
                return EXIT_INPUT
            reports = reviewer.review_many(files, language=args.language, explain=explain, jobs=args.jobs)
    except ConfigError as exc:
        Console(stderr=True).print(f"[red]Error:[/red] {exc}")
        return EXIT_INPUT

    multiple = len(reports) > 1
    json_payload = []
    for rep in reports:
        name = rep.path.name if rep.path else "stdin"
        if not rep.ok:
            if args.format == "terminal":
                render_failure(rep, err, show_rejected=args.show_rejected)
            else:
                json_payload.append({"file": str(rep.path or name), "ok": False, "error": rep.error, "kind": rep.error_kind})
                err.print(f"[red]{name}: {rep.error}[/red]")
            continue
        result = rep.result
        if args.format == "terminal":
            render_result(result, console, show_diff=args.diff)
        elif args.format == "markdown":
            print(to_markdown(result))
        else:
            json_payload.append(result.to_dict())
        if args.out:
            suffix = ".review.json" if args.format == "json" else ".review.md"
            body = json.dumps(result.to_dict(), ensure_ascii=False, indent=2) if args.format == "json" else to_markdown(result)
            err.print(f"Saved: {_write(_target(args.out, name, suffix, multiple), body)}")
        if args.html:
            err.print(f"Saved: {_write(_target(args.html, name, '.review.html', multiple), to_html(result, show_diff=args.diff))}")
    if args.format == "json":
        print(json.dumps(json_payload, ensure_ascii=False, indent=2))
    if multiple and args.format == "terminal":
        render_summary(reports, console)

    failed = [r for r in reports if not r.ok]
    if failed:
        only_input = all(r.error_kind in ("not_found", "is_directory", "permission", "io", "empty", "too_large",
                                          "binary", "unsupported", "config") for r in failed)
        return EXIT_INPUT if only_input else EXIT_REVIEW
    if args.fail_on:
        threshold = SEVERITY_ORDER[args.fail_on.upper()]
        if any(r.result.max_rank >= threshold for r in reports):
            return EXIT_FINDINGS
    return EXIT_OK
