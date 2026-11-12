"""Measure review quality on the seeded-bug samples (needs a real provider and API key).

    python scripts/evaluate_samples.py --provider gemini
    python scripts/evaluate_samples.py --provider openai --model gpt-6-luna --runs 3

For every bug listed in samples/manifest.json it checks whether the final bug report contains a finding
within +-2 lines of the planted line. Recall is "planted bugs found / planted bugs". It cannot measure
precision (false alarms) or the quality of the fixes: read the reports for that. Results depend on the
model, the prompt version and sampling noise, so use --runs to see the spread. Nothing is hard-coded or
simulated here; with --provider mock you only measure the static layer.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from codereviewer.config import Settings  # noqa: E402
from codereviewer.pipeline import CodeReviewer  # noqa: E402
from codereviewer.prompts import PROMPT_VERSION  # noqa: E402
from codereviewer.providers import create_provider  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--provider", choices=["gemini", "openai", "mock"], default=None)
    ap.add_argument("--model")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--tolerance", type=int, default=2, help="accepted distance in lines")
    args = ap.parse_args()

    overrides = {"provider": args.provider}
    chosen = args.provider or Settings.from_env().provider
    if args.model:
        overrides["gemini_model" if chosen == "gemini" else "openai_model"] = args.model
    settings = Settings.from_env(**overrides)
    reviewer = CodeReviewer(create_provider(settings), settings)
    manifest = json.loads((ROOT / "samples" / "manifest.json").read_text())

    total = found = 0
    print(f"provider={settings.provider} model={reviewer.provider.model} prompt={PROMPT_VERSION} runs={args.runs}\n")
    for name, bugs in manifest.items():
        for run in range(args.runs):
            report = reviewer.review_path(ROOT / "samples" / name)
            if not report.ok:
                print(f"{name} (run {run + 1}): FAILED - {report.error}")
                total += len(bugs)
                continue
            hits = [b for b in bugs if any(f.line is not None and abs(f.line - b["line"]) <= args.tolerance
                                           for f in report.result.findings)]
            misses = [b for b in bugs if b not in hits]
            total += len(bugs)
            found += len(hits)
            print(f"{name} (run {run + 1}): {len(hits)}/{len(bugs)} planted bugs found; "
                  f"{len(report.result.findings)} findings reported")
            for b in misses:
                print(f"    missed line {b['line']}: {b['what']}")
    print(f"\nRecall: {found}/{total} = {found / total:.0%}" if total else "No samples.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
