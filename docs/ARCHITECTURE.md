# Architecture

## One idea

The model is a **probabilistic component inside a deterministic pipeline**. Everything we can decide with
certainty (what the file contains, whether it parses, whether the answer has the right shape) is decided by
code; the model only does what code cannot: judge logic, explain intent, propose a fix.

## Pipeline

```
 path / stdin / upload
        │
        ▼
 ┌─ ingest.py ───────────────────────────────────────────────┐
 │ bytes -> decode (utf-8 / BOM / utf-16 / cp1252 / latin-1) │  raises IngestError(kind)
 │ rejects: missing, directory, permission, empty, binary,    │
 │          too large, unsupported extension                  │
 └───────────────┬───────────────────────────────────────────┘
                 ▼
 ┌─ static_checks.py ──┐    ┌─ context.py ─────────────────────────────┐
 │ Python AST facts    │    │ number lines · estimate tokens · chunk    │
 └─────────┬───────────┘    └──────────────┬────────────────────────────┘
           └───────────────┬───────────────┘
                           ▼
 ┌─ prompts.py ───────────────────────────────────────────────┐
 │ system instruction (persona + output contract)             │
 │ user message: static facts + <<<CODE_BEGIN id>>> ... END   │
 └───────────────┬────────────────────────────────────────────┘
                 ▼
 ┌─ providers.py ───────────┐   Tenacity: retry 429 / 5xx / network only
 │ Gemini | OpenAI | mock   │
 └───────────────┬──────────┘
                 ▼
 ┌─ contract.py ───────────────────────────────────────────────┐
 │ fence-aware parse of ## EXPLANATION / BUG_REPORT / REFACTORED│──► ContractError ─► repair once ─► reject
 └───────────────┬─────────────────────────────────────────────┘
                 ▼
 ┌─ static_checks.verify_syntax ──┐   refactored code does not parse ─► repair once ─► keep, flagged
 └───────────────┬────────────────┘
                 ▼
 ┌─ pipeline.py merge ─────────────────────────────────────────┐
 │ add static findings the model missed (same category, near   │
 │ line) · sort by severity · chunks -> one report             │
 └───────────────┬─────────────────────────────────────────────┘
                 ▼
 ┌─ render.py ─────────────────────────────────────────────────┐
 │ canonical markdown -> rich Markdown (highlighted) | .md |    │
 │ .html | .json | unified diff                                 │
 └──────────────────────────────────────────────────────────────┘
```

## Design decisions

| Decision | Why |
|---|---|
| Read **bytes**, decode explicitly | Text-mode reading translates `\r\n`; the PDF requires whitespace preserved perfectly. Decoding bytes also lets us triage encodings instead of crashing. |
| Number every line in the prompt | LLMs miscount lines. With a numbered listing, "line 42" in the bug report is reliable. The contract forbids prefixes in the refactored code and the validator strips them if they leak. |
| Random fence markers (`<<<CODE_BEGIN 3fa91c20>>>`) | A fixed delimiter can be closed by the file itself. A per-request random id that is absent from the code cannot. |
| System instruction vs user data | Rules go in the system instruction; the file is data. The prompt states that instructions inside the code are to be ignored. This reduces, not removes, prompt-injection risk. |
| Markdown contract with exact headers | It is what the PDF specifies, it is human-readable, and it is cheap to validate. The parser tracks code fences so `## BUG_REPORT` inside a code block is code, not a header. |
| Reject, don't repair silently | A malformed report is never "fixed up" in code. The model gets one chance to correct itself with the validator's exact complaints; after that the run fails loudly (exit 4). |
| Static pre-check **and** post-merge | Before: the model gets certain facts. After: certain facts it missed are added to the report (marked as static). Coverage requires the same category and a nearby line, so two different issues next to each other are not confused. |
| Verify the refactored code parses | "Corrected, compilable code" is a claim; Python is checked with `ast.parse`, JavaScript with `node --check`. Nothing is ever executed. |
| Keep syntax-broken output (flagged) after one repair | The bug report is still valuable; hiding it would be worse than a clear warning. |
| No temperature by default | Google recommends the default for Gemini 3; OpenAI reasoning models reject it. An explicit `--temperature` is honoured and explained. |
| Failures are values | `review_path` returns a `FileReport` with `error_kind`; one bad file never aborts a directory run. |
| Providers behind one method | `generate(system, user, ...)`: swapping Gemini for OpenAI, or a fake in tests, touches nothing else. |

## Error map

| Situation | Handling | Exit |
|---|---|---|
| File missing / directory / permission / empty / binary / too large / unsupported type | `IngestError(kind)` -> clean panel | 3 |
| Non-UTF-8 text | cp1252 then Latin-1, with a warning | 0 |
| No API key | `ConfigError` with the fix; mock suggested | 3 |
| 429, 5xx, timeout, network | Tenacity: randomized exponential backoff, then `ProviderError` | 4 |
| 401/403, 404 model, 400 | not retried; actionable message | 4 |
| Prompt blocked, empty reply, token budget used up by thinking | specific `ProviderError.kind` | 4 |
| Reply violates the contract | repair once, then reject (`--show-rejected` prints it) | 4 |
| Refactored code does not parse | repair once, then keep with a warning | 0 |
| Findings at or above `--fail-on` | report printed | 1 |

## Chunking (code as context)

If the numbered listing exceeds `MAX_CONTEXT_TOKENS` (estimated), Python files are split between top-level
statements (decorators stay with their function), packed greedily; a definition larger than the budget is split
by line windows with a warning. Other languages use line windows that prefer blank lines. Import statements outside
a chunk are supplied as read-only context. Each chunk is reviewed independently and the results merged.
Limitation: cross-chunk bugs can be missed.

## Extending

- **New language:** add the extension to `LANGUAGES`; optionally a `precheck` and a `verify_syntax` branch.
- **New provider:** implement `generate(system, user, *, max_output_tokens, temperature)` and register it in `create_provider`.
- **Prompt change:** edit `prompts.py`, bump `PROMPT_VERSION`, run the tests and `scripts/evaluate_samples.py`.
