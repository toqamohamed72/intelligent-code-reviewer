# Intelligent Code Reviewer & Explainer

A developer utility that reads a source file, explains it in plain language, reports bugs with line
numbers, and returns a corrected version, rendered with syntax highlighting in your terminal (or a web UI).

DecodeLabs · Generative AI · Project 4 (optional mastery phase, Batch 2026)

```bash
python review.py samples/inventory.py --diff
python review.py src/ --recursive --fail-on high      # use it as a CI gate
cat script.py | python review.py - --language python  # or pipe code in
streamlit run app.py                                   # web interface
```

## How it works (the PDF's IPO pipeline, hardened)

```
1 INPUT         file -> bytes -> string       exact whitespace/CRLF, encoding ladder, binary/size/permission triage
2 PROCESS       static pre-check (Python AST)  deterministic facts: syntax errors, eval, SQL injection, ...
                context plan                   line-numbered listing, token budget, AST-aware chunking
                system instruction + payload   "cold, analytical QA engineer" persona, data fenced by random markers
                model call (Tenacity retries)  Gemini (default), OpenAI, or an offline mock
                contract validation            must contain ## EXPLANATION, ## BUG_REPORT, ## REFACTORED_CODE
                repair (once) / reject         malformed answers never reach the report
                syntax verification            refactored Python is parsed; JavaScript via `node --check` if installed
3 OUTPUT        canonical markdown -> rich.markdown.Markdown -> highlighted terminal / .md / .html / .json / diff
```

### Traceability to the PDF

| PDF requirement | Where |
|---|---|
| Ingest a raw `.py` / `.js` / `.java` file as a string | `codereviewer/ingest.py` (also `.ts .c .cpp .go .rs ...`) |
| Preserve whitespace, carriage returns, indentation exactly | file is read as bytes and decoded; tested with CRLF + tabs |
| Triage `FileNotFoundError`, `PermissionError`, `UnicodeDecodeError` | `IngestError` kinds; codec fallback UTF-8 -> Windows-1252 -> Latin-1 with a warning |
| Strict system instruction ("cold, analytical Senior QA Engineer ... no friendly greetings") | `prompts.SYSTEM_TEMPLATE`, sent as the real *system instruction* |
| Distinct bug report + optimized code block | `## BUG_REPORT` (bullets only) and `## REFACTORED_CODE` (one fenced block) |
| Reject responses missing the section headers | `contract.parse_review` -> one repair attempt -> rejection, exit code 4 |
| Render markdown with syntax highlighting (`rich.markdown.Markdown`) | `codereviewer/render.py` |
| Code-as-context management | `codereviewer/context.py` |
| Google GenAI payload | `GeminiProvider` (`google-genai`); OpenAI is an alternative |

Additions beyond the PDF: `## EXPLANATION` (the project's "Explainer" half; `--no-explain` gives the exact
two-section contract), static pre-checks, syntax verification of the refactored code, line-numbered context
and chunking for large files, diff view, directory/stdin modes, a `--fail-on` CI gate, HTML/JSON/Markdown
export, a Streamlit UI, and retries.

## Quick start (VS Code)

```bash
python -m venv .venv
source .venv/bin/activate              # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env                   # Windows: copy .env.example .env
# edit .env: set GEMINI_API_KEY (or LLM_PROVIDER=openai and OPENAI_API_KEY)

python review.py --doctor              # configuration check
python review.py --doctor --ping       # one tiny real request
python review.py samples/inventory.py
```

No key yet? Add `--provider mock`. The mock engine lists only the deterministic static findings and echoes the
code unchanged: it demonstrates the pipeline and rendering, it is **not** a model and says so in its output.

## Usage

```text
python review.py PATH [PATH ...] [-r] [--language L] [--no-explain]
                 [--provider gemini|openai|mock] [--model M] [--temperature T] [--thinking-level LEVEL]
                 [--format terminal|markdown|json] [--diff] [--out FILE_OR_DIR] [--html FILE_OR_DIR]
                 [--jobs N] [--max-context-tokens N] [--fail-on low|medium|high] [--show-rejected]
```

| Exit code | Meaning |
|---|---|
| 0 | success (and nothing at or above `--fail-on`) |
| 1 | findings at or above `--fail-on` |
| 2 | invalid arguments |
| 3 | configuration or input problem (missing key, missing/unsupported/binary file, ...) |
| 4 | the review itself failed (provider error, or the response violated the contract twice) |

Finding format the model must use: `- [HIGH|MEDIUM|LOW] [SYNTAX|LOGIC|SECURITY|PERFORMANCE|ERROR_HANDLING|STYLE] line N: problem. Fix: how.`

## Choosing a model and temperature

- **Gemini 3 models:** Google's developer guide recommends leaving temperature at its default (1.0); lower values
  can cause looping or degraded output. So the tool sends **no temperature unless you ask** (`--temperature`),
  and warns if you set a value below 1.0 on a `gemini-3*` model. The default model id `gemini-3.5-flash` is the one
  used in Google's current quickstart; model names change quickly, so check the
  [models page](https://ai.google.dev/gemini-api/docs/models) and set `GEMINI_MODEL`.
- **Thinking:** thinking tokens count toward `MAX_OUTPUT_TOKENS`. If a review comes back empty or truncated, raise it
  or lower `GEMINI_THINKING_LEVEL`.
- **OpenAI:** per OpenAI's [GPT-6 guide](https://developers.openai.com/api/docs/guides/latest-model), reasoning-capable
  models reject `temperature` unless reasoning effort is `none`. The provider sends it only where accepted and
  adapts if the API rejects it.

## Project structure

```
intelligent-code-reviewer/
├── review.py               CLI entry point          app.py   Streamlit UI
├── codereviewer/
│   ├── ingest.py           bytes -> string, encoding ladder, error triage, discovery
│   ├── static_checks.py    Python AST pre-checks, syntax verification of refactored code
│   ├── context.py          line numbering, token budget, AST-aware chunking
│   ├── prompts.py          system instruction, fenced user message, repair message
│   ├── contract.py         fence-aware validator for the three-section output
│   ├── providers.py        Gemini, OpenAI, mock (+ Tenacity retries, error translation)
│   ├── pipeline.py         CodeReviewer: orchestration, repair, merge, parallel files
│   ├── render.py           markdown, rich terminal, diff, HTML
│   ├── cli.py              argparse interface and exit codes
│   └── config.py           environment settings
├── samples/                inventory.py, cart.js, Calculator.java with planted bugs + manifest.json
├── scripts/evaluate_samples.py   seeded-bug recall with a real model
├── tests/                  125 tests
└── docs/
```

## Testing and evaluation

```bash
pip install -r requirements-dev.txt && python -m pytest -q            # 125 tests, no API key needed
python scripts/evaluate_samples.py --provider gemini --runs 3        # real-model recall on planted bugs
```

The static layer alone finds **5 of the 16** planted bugs (all mechanical Python ones). Everything else (off-by-one,
division by zero, missing `await`, unclosed resources, all of the JavaScript and Java issues) is the model's job,
and measuring it requires your API key. See `docs/TESTING_AND_EVALUATION.md`.


