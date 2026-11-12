# Testing and Evaluation

Two different questions: **does the software behave?** (automated tests, no key needed) and
**is the review any good?** (needs a real model and human reading).

## 1. Automated tests: 125

| File | What it proves |
|---|---|
| `test_ingest.py` | CRLF/tabs preserved exactly; BOM, UTF-16, cp1252, Latin-1 ladder with warnings; binary, empty, missing, directory, permission, too-large, unsupported all triaged; directory discovery skips `node_modules` etc. |
| `test_static_checks.py` | Each AST check fires and safe code is not flagged; syntax errors carry a line; `verify_syntax` for Python, JavaScript (when Node exists), and honest "skipped" otherwise |
| `test_context.py` | Numbering; chunks cover every line exactly once; Python cuts only on definitions and each chunk parses; windows prefer blank lines; oversized function splits with a warning; unparseable Python falls back |
| `test_prompts.py` | Persona and contract are in the system instruction; the code cannot close its own fence; static facts and import context reach only the chunks they belong to; repair message |
| `test_contract.py` | Valid parse; every violation is rejected (filler, missing/duplicate/out-of-order/unexpected sections, prose in bug report, 0 or 2 code blocks, unclosed fence); headers inside code are code; longer fences; wrapped answers; prefix stripping; warnings vs rejections |
| `test_providers.py` | Exact Gemini config (system instruction, no default temperature, thinking level); blocked/truncated/empty replies; retry on 5xx/429/network, none on auth/model/bad request; OpenAI parameter rules and temperature fallback; the mock always satisfies the contract; **one shared client under thread contention** (regression test for a real race found during verification) |
| `test_pipeline.py` | Happy path; repair with validator feedback; persistent rejection; syntax repair; static merge without duplicates; chunk merge and failure position; parallel files with failure isolation |
| `test_render.py` | Section order; fence escalation; static labels; ANSI colour in a terminal and none when piped; diff; HTML; failure and summary views |
| `test_cli.py` | Every exit code; formats; `--out`/`--html`; directory mode; stdin; `--fail-on`; invalid arguments; `--doctor` |
| `test_app.py` | Streamlit app headless; example -> review; empty input; **model text is HTML-escaped** |
| `test_samples_eval.py` | The deterministic layer finds exactly 5 of the 8 planted Python bugs and 0 in JS/Java |

Providers are replaced by fakes that raise the real SDK exception classes, so retry logic is exercised without a network.

## 2. Live smoke test (you run it once)

```bash
python review.py --doctor --ping
python review.py samples/inventory.py --diff
```

Pass: exit 0, three sections rendered, line numbers in the report match the file, refactored code parses.

## 3. Measuring review quality: seeded bugs

`samples/manifest.json` lists planted bugs (8 Python, 4 JavaScript, 4 Java).
`scripts/evaluate_samples.py` runs the reviewer and counts how many planted bugs have a finding within +-2 lines.

```bash
python scripts/evaluate_samples.py --provider gemini --runs 3
```

Baseline you can reproduce today without a key: the static layer alone scores **5/16 (31 %)** (`--provider mock`).
Anything above that is the model's contribution. Report it with the model name, `PROMPT_VERSION` and number of runs.

What recall does not measure: **false alarms** (read the unmatched findings), **fix quality** (read the diff and
run your own tests), and **explanation quality** (rate 1-5 for accuracy and plain language).

## 4. Human rubric (1-5)

| Criterion | Question |
|---|---|
| Accuracy | Is each reported defect real? |
| Coverage | Which planted or obvious defects were missed? |
| Locality | Do line numbers point at the right place? |
| Fix quality | Does the refactored code fix the defect without changing behaviour you did not ask to change? |
| Explanation | Is it correct and understandable by a junior developer? |
| Discipline | No filler, correct format, no invented problems? |

## 5. Edge cases

| Case | Expected |
|---|---|
| CRLF file | Preserved in the prompt; reported newline style `\r\n` |
| Non-UTF-8 file | Decoded with a warning |
| Empty / binary file | Rejected with a clear message, exit 3 |
| Code containing `<<<CODE_END>>>` or "ignore previous instructions" | Stays inside the fenced data block |
| Code containing triple backticks | Report fence grows; refactored block still parses |
| File with a syntax error | Static pre-check reports the line; the model is told |
| Defect-free code | Model must answer `- No defects found.` |
| File above the context budget | Chunked, with a note |
| Model returns prose | Rejected, one repair, then exit 4 |
