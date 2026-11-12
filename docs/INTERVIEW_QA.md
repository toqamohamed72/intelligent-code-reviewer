# Interview Questions and Answers

**1. Why read the file as bytes instead of `open().read()`?**
Text mode applies universal-newline translation (`\r\n` becomes `\n`) and assumes an encoding. Reading bytes and
decoding explicitly preserves the file exactly, lets us detect BOMs and binary content, and gives a controlled
fallback ladder instead of a `UnicodeDecodeError` crash.

**2. Why number the lines in the prompt?**
Language models are unreliable at counting lines. A numbered listing makes "line 42" in the report meaningful. The
contract forbids the prefixes in the refactored code and the validator strips them if they leak.

**3. How do you stop the reviewed code from hijacking the model?**
Layers: a system instruction that declares the code untrusted data; fence markers with a random per-request id that
does not occur in the code (so it cannot close the fence); output validation against a strict contract; the model has
no tools, so the worst case is bad text. None of this is a guarantee, which is why output is validated and reviewed.

**4. Why a markdown contract rather than JSON?**
It is what the project specifies and it renders directly. JSON mode or structured outputs are a good alternative
(they guarantee syntax); the trade-off is that code inside JSON strings is escaped and unreadable if something goes
wrong. The validator is fence-aware, so headers inside code are not mistaken for sections.

**5. What happens when the model breaks the format?**
The response is rejected. One repair request repeats the task plus the validator's exact complaints. If it fails
again, the run exits with code 4; nothing malformed is ever rendered as a report.

**6. Why combine static analysis with the LLM?**
They are complementary. A parser knows a syntax error for certain; an LLM can judge an off-by-one. Static facts go
in the prompt, are merged back if the model misses them, and the refactored code is parsed to confirm it is valid
syntax. Coverage matching needs the same category and a nearby line, otherwise two adjacent issues would hide each other.

**7. Can you guarantee the refactored code is correct?**
No. We can verify syntax (Python, JavaScript with Node) but not behaviour. Tests, a human read of the diff, and CI are still required.

**8. How do you handle a file larger than the context window?**
Estimate tokens, and if over budget split at safe boundaries: Python between top-level definitions (decorators stay
attached), otherwise line windows preferring blank lines. Imports outside a chunk are passed as context. The
limitation is cross-chunk bugs.

**9. Which errors do you retry?**
Only those that can succeed later: 429, 5xx, timeouts, network failures, with randomized exponential backoff (jitter
avoids synchronized retries). Authentication, permission, unknown model and bad requests are never retried.

**10. Why not set a low temperature for deterministic code review?**
For Gemini 3, Google recommends the default temperature and warns lower values can loop; some OpenAI reasoning
models reject the parameter. Consistency comes from the validated contract and static facts. Always check the
provider's current documentation.

**11. How would you run this in CI?**
`python review.py src -r --provider gemini --fail-on high --format json`. Exit code 1 blocks the merge on a HIGH
finding, 4 signals a failed review (treat as infrastructure, not code quality). Cache results by file hash, cap
file count and cost, and treat the review as advisory next to tests and linters.

**12. How do you evaluate the reviewer itself?**
Planted-bug recall (`samples/manifest.json`), plus human review for precision, fix quality and explanation. The
deterministic layer alone scores 5/16 on the samples, which is the baseline the model has to beat.

**13. What are the weaknesses?**
Review quality is the model's; static checks cover Python only; syntax verification covers Python and JavaScript;
chunking misses cross-chunk bugs; prompt injection is mitigated, not solved; token budgets are estimates.

**14. Walk me through one run.**
Read bytes -> decode -> (Python) AST pre-check -> number lines, fit budget -> system instruction + fenced payload ->
provider call with retries -> parse contract -> repair if needed -> parse refactored code -> merge static findings ->
render markdown through rich (or save `.md` / `.html` / `.json`) -> exit code from `--fail-on`.
