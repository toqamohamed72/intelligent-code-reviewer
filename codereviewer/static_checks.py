"""Deterministic checks that complement the model.

Why: an LLM reads code statistically and can miss a fact a parser knows for sure
(a syntax error on line 42) or invent one. Cheap, certain checks give the model
grounded facts *before* it answers, give us a safety net *after* it answers, and
let us verify that the refactored code at least parses.

Nothing here executes the reviewed code. Python is parsed with ``ast`` only;
JavaScript is checked with ``node --check`` (parse only) when Node is installed.
"""
from __future__ import annotations

import ast
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StaticFinding:
    severity: str          # HIGH | MEDIUM | LOW
    category: str          # SYNTAX | LOGIC | SECURITY | ERROR_HANDLING | STYLE
    line: int | None
    message: str

    def as_bullet(self) -> str:
        where = f"line {self.line}: " if self.line else ""
        return f"- [{self.severity}] [{self.category}] {where}{self.message}"


_MUTABLE_CALLS = {"list", "dict", "set", "defaultdict", "deque"}


def _is_mutable_default(node: ast.AST) -> bool:
    if isinstance(node, (ast.List, ast.Dict, ast.Set, ast.ListComp, ast.DictComp, ast.SetComp)):
        return True
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _MUTABLE_CALLS


def _dynamic_sql(arg: ast.AST) -> bool:
    """String built at runtime: f-string, concatenation, % formatting or .format()."""
    if isinstance(arg, ast.JoinedStr):
        return True
    if isinstance(arg, ast.BinOp) and isinstance(arg.op, (ast.Add, ast.Mod)):
        return True
    return (
        isinstance(arg, ast.Call)
        and isinstance(arg.func, ast.Attribute)
        and arg.func.attr == "format"
    )


def precheck(language: str, text: str) -> list[StaticFinding]:
    """Python only for now; other languages return an empty list."""
    if language != "python":
        return []
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return [StaticFinding("HIGH", "SYNTAX", exc.lineno, f"SyntaxError: {exc.msg}.")]
    except (ValueError, RecursionError) as exc:  # null bytes, absurd nesting
        return [StaticFinding("HIGH", "SYNTAX", None, f"The file cannot be parsed: {exc}.")]

    findings: list[StaticFinding] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            defaults = list(node.args.defaults) + [d for d in node.args.kw_defaults if d is not None]
            for default in defaults:
                if _is_mutable_default(default):
                    name = getattr(node, "name", "<lambda>")
                    findings.append(StaticFinding(
                        "MEDIUM", "LOGIC", default.lineno,
                        f"Mutable default argument in '{name}' is shared between calls. Fix: default to None and create it inside.",
                    ))
        elif isinstance(node, ast.ExceptHandler) and node.type is None:
            findings.append(StaticFinding(
                "MEDIUM", "ERROR_HANDLING", node.lineno,
                "Bare 'except:' also swallows KeyboardInterrupt and SystemExit. Fix: catch specific exceptions.",
            ))
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in {"eval", "exec"}:
                findings.append(StaticFinding(
                    "HIGH", "SECURITY", node.lineno,
                    f"'{func.id}()' executes arbitrary code. Fix: use ast.literal_eval or an explicit parser.",
                ))
            if isinstance(func, ast.Attribute) and func.attr == "execute" and node.args and _dynamic_sql(node.args[0]):
                findings.append(StaticFinding(
                    "HIGH", "SECURITY", node.lineno,
                    "SQL string is built at runtime, which allows SQL injection. Fix: use parameterized queries.",
                ))
            for kw in node.keywords:
                if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                    findings.append(StaticFinding(
                        "HIGH", "SECURITY", node.lineno,
                        "shell=True lets input reach the shell. Fix: pass an argument list without shell=True.",
                    ))
        elif isinstance(node, ast.Compare):
            for op, right in zip(node.ops, node.comparators):
                if isinstance(op, (ast.Is, ast.IsNot)) and isinstance(right, ast.Constant) and right.value is not None \
                        and not isinstance(right.value, (bool, type(Ellipsis))):
                    findings.append(StaticFinding(
                        "MEDIUM", "LOGIC", node.lineno,
                        "Identity comparison ('is') with a literal depends on interning. Fix: use '=='.",
                    ))
                if isinstance(op, (ast.Eq, ast.NotEq)) and isinstance(right, ast.Constant) and right.value is None:
                    findings.append(StaticFinding(
                        "LOW", "STYLE", node.lineno, "Compare with None using 'is' / 'is not', not '=='.",
                    ))
    return sorted(set(findings), key=lambda f: (f.line or 0, f.message))


# --------------------------------------------------------------------------- #
# Syntax verification of the model's refactored code
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class SyntaxVerdict:
    status: str            # ok | error | skipped
    message: str = ""


def verify_syntax(language: str, code: str) -> SyntaxVerdict:
    """Does the refactored code parse? Parse only: the code is never run."""
    if language == "python":
        try:
            ast.parse(code)
            return SyntaxVerdict("ok")
        except SyntaxError as exc:
            return SyntaxVerdict("error", f"SyntaxError on line {exc.lineno}: {exc.msg}")
        except (ValueError, RecursionError) as exc:
            return SyntaxVerdict("error", f"The code cannot be parsed: {exc}")
    if language == "javascript":
        node = shutil.which("node")
        if not node:
            return SyntaxVerdict("skipped", "Node.js is not installed, so JavaScript syntax was not verified.")
        suffix = ".mjs" if re.search(r"^\s*(import|export)\s", code, re.M) else ".js"
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / f"check{suffix}"
            target.write_text(code, encoding="utf-8")
            try:
                done = subprocess.run([node, "--check", str(target)], capture_output=True, text=True, timeout=15)
            except (subprocess.TimeoutExpired, OSError) as exc:
                return SyntaxVerdict("skipped", f"Could not run node --check: {exc}")
        if done.returncode == 0:
            return SyntaxVerdict("ok")
        detail = next((ln for ln in done.stderr.splitlines() if "Error" in ln), done.stderr.strip()[:200])
        return SyntaxVerdict("error", f"node --check failed: {detail}")
    return SyntaxVerdict("skipped", f"No syntax verifier for {language}; the refactored code was not compiled.")
