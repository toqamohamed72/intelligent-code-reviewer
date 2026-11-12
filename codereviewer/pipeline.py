"""The complete IPO pipeline.

    ingest -> static pre-check -> context plan -> model call -> contract
    validation (+ one repair) -> syntax verification -> merge -> result

Per-file problems are returned as values (``FileReport.error``), never raised, so
reviewing a directory never aborts because of one bad file.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from .config import ConfigError, Settings
from .context import Chunk, plan_chunks
from .contract import ContractError, Finding, ParsedReview, parse_review
from .ingest import IngestError, SourceFile, read_source
from .prompts import PROMPT_VERSION, build_repair_message, build_system_instruction, build_user_message
from .providers import Provider, ProviderError
from .static_checks import StaticFinding, SyntaxVerdict, precheck, verify_syntax

log = logging.getLogger("codereviewer.pipeline")


@dataclass
class ChunkReview:
    chunk: Chunk
    parsed: ParsedReview | None = None
    attempts: int = 0
    raw: str = ""
    syntax: SyntaxVerdict | None = None
    error: str | None = None


@dataclass
class ReviewResult:
    source: SourceFile
    provider: str
    model: str
    explain: bool = True
    static: list[StaticFinding] = field(default_factory=list)
    chunks: list[ChunkReview] = field(default_factory=list)
    explanation: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    refactored_parts: list[tuple[int, int, str]] = field(default_factory=list)   # (start, end, code)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    thinking_tokens: int = 0
    latency_s: float = 0.0
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    prompt_version: str = PROMPT_VERSION

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def refactored_code(self) -> str:
        return "\n\n".join(code for _, _, code in self.refactored_parts)

    @property
    def max_rank(self) -> int:
        return max((f.rank for f in self.findings), default=0)

    def counts(self) -> dict[str, int]:
        out = {"HIGH": 0, "MEDIUM": 0, "LOW": 0, "UNSPECIFIED": 0}
        for f in self.findings:
            out[f.severity or "UNSPECIFIED"] += 1
        return out

    def to_dict(self) -> dict:
        return {
            "file": self.source.name, "language": self.source.language, "provider": self.provider,
            "model": self.model, "ok": self.ok, "error": self.error, "prompt_version": self.prompt_version,
            "chunks": len(self.chunks), "counts": self.counts(),
            "explanation": self.explanation, "findings": [f.to_dict() for f in self.findings],
            "refactored_code": self.refactored_code,
            "static_precheck": [f.as_bullet() for f in self.static],
            "usage": {"prompt_tokens": self.prompt_tokens, "completion_tokens": self.completion_tokens,
                      "thinking_tokens": self.thinking_tokens, "latency_s": round(self.latency_s, 2)},
            "warnings": self.warnings, "notes": self.notes,
        }


@dataclass
class FileReport:
    path: Path | None
    result: ReviewResult | None = None
    error: str | None = None
    error_kind: str | None = None

    @property
    def ok(self) -> bool:
        return self.result is not None and self.result.ok


def _covered(static: StaticFinding, findings: Sequence[Finding]) -> bool:
    """Did the model already report this static finding? Same category AND nearby line (or a range containing it)."""
    for f in findings:
        if f.category != static.category:
            continue
        if static.line is None:
            return True
        if f.line is not None and (abs(f.line - static.line) <= 2 or (f.end_line and f.line <= static.line <= f.end_line)):
            return True
    return False


class CodeReviewer:
    def __init__(self, provider: Provider, settings: Settings):
        self.provider = provider
        self.settings = settings

    # -- one chunk ---------------------------------------------------------- #
    def _review_chunk(self, source: SourceFile, chunk: Chunk, static: list[StaticFinding], system: str,
                      explain: bool, result: ReviewResult) -> ChunkReview:
        user = build_user_message(source, chunk, static)
        review = ChunkReview(chunk)
        problems: list[str] = []
        last_ok: tuple[ParsedReview, SyntaxVerdict] | None = None
        for attempt in range(1 + self.settings.max_repairs):
            review.attempts = attempt + 1
            message = build_repair_message(user, problems) if problems else user
            try:
                resp = self.provider.generate(
                    system, message, max_output_tokens=self.settings.max_output_tokens,
                    temperature=self.settings.temperature,
                )
            except ProviderError as exc:
                review.error = str(exc)
                return review
            result.prompt_tokens += resp.prompt_tokens
            result.completion_tokens += resp.completion_tokens
            result.thinking_tokens += resp.thinking_tokens
            result.latency_s += resp.latency_s
            result.model = resp.model
            for note in resp.notes:
                if note not in result.notes:
                    result.notes.append(note)
            review.raw = resp.text
            try:
                parsed = parse_review(resp.text, require_explanation=explain, expected_language=source.language)
            except ContractError as exc:
                problems = exc.problems
                review.error = "Rejected by the output validator: " + " ".join(problems)
                continue
            verdict = verify_syntax(source.language, parsed.refactored_code)
            last_ok = (parsed, verdict)
            if verdict.status == "error":
                problems = [f"The refactored code does not compile: {verdict.message}. Fix it and return the full response again."]
                review.error = None
                continue
            review.parsed, review.syntax, review.error = parsed, verdict, None
            return review
        if last_ok:                      # valid contract but unverified/broken syntax: keep it, flag it
            review.parsed, review.syntax = last_ok
            review.error = None
        return review

    # -- one file ----------------------------------------------------------- #
    def review(self, source: SourceFile, *, explain: bool = True) -> ReviewResult:
        result = ReviewResult(source=source, provider=self.provider.name, model=self.provider.model, explain=explain)
        result.warnings.extend(source.warnings)
        result.static = precheck(source.language, source.text)
        chunks = plan_chunks(source, self.settings.max_context_tokens)
        if len(chunks) > 1:
            result.notes.append(f"The file exceeds the context budget and was reviewed in {len(chunks)} parts; "
                                "bugs that span parts can be missed.")
        system = build_system_instruction(source.language, explain=explain)

        for chunk in chunks:
            review = self._review_chunk(source, chunk, result.static, system, explain, result)
            result.chunks.append(review)
            result.warnings.extend(chunk.warnings)
            if review.parsed is None:
                result.error = (f"Part {chunk.index}/{chunk.total}: " if chunk.total > 1 else "") + (review.error or "Review failed.")
                return result
            parsed = review.parsed
            result.warnings.extend(parsed.warnings)
            if review.syntax and review.syntax.status == "error":
                result.warnings.append(f"The refactored code failed the syntax check: {review.syntax.message}")
            elif review.syntax and review.syntax.status == "skipped":
                result.notes.append(review.syntax.message)
            tag = f"Lines {chunk.start_line}-{chunk.end_line}: " if chunk.total > 1 else ""
            result.explanation.extend(tag + e for e in parsed.explanation)
            result.findings.extend(parsed.findings)
            result.refactored_parts.append((chunk.start_line, chunk.end_line, parsed.refactored_code))

            mine = [s for s in result.static if s.line is None or chunk.start_line <= s.line <= chunk.end_line]
            missing = [s for s in mine if not _covered(s, parsed.findings)]
            if parsed.no_defects and mine:
                result.warnings.append("The model reported no defects, but static analysis found some; they were added.")
            for s in missing:
                result.findings.append(Finding(s.severity, s.category, s.line, None, s.message, "", "static"))

        result.findings.sort(key=lambda f: (-f.rank, f.line or 0))
        return result

    # -- paths -------------------------------------------------------------- #
    def review_path(self, path: str | Path, *, language: str | None = None, explain: bool = True) -> FileReport:
        path = Path(path)
        try:
            source = read_source(path, language=language, max_bytes=self.settings.max_file_bytes)
        except IngestError as exc:
            return FileReport(path, error=str(exc), error_kind=exc.kind)
        try:
            result = self.review(source, explain=explain)
        except ConfigError as exc:
            return FileReport(path, error=str(exc), error_kind="config")
        except Exception as exc:  # last line of defence for one file in a batch
            log.exception("Unexpected failure reviewing %s", path)
            return FileReport(path, error=f"Unexpected error: {exc}", error_kind="internal")
        return FileReport(path, result=result, error=result.error, error_kind=None if result.ok else "review")

    def review_many(self, paths: Sequence[str | Path], *, language: str | None = None, explain: bool = True,
                    jobs: int | None = None) -> list[FileReport]:
        jobs = max(1, min(jobs or self.settings.jobs, len(paths) or 1))
        if jobs == 1:
            return [self.review_path(p, language=language, explain=explain) for p in paths]
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            return list(pool.map(lambda p: self.review_path(p, language=language, explain=explain), paths))
