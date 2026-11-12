# Demo Script (8 minutes)

Before presenting: `python review.py --doctor --ping` and `python -m pytest -q`.

| # | Scenario | Command | Point out |
|---|---|---|---|
| 1 | The problem | `cat samples/inventory.py` | Eight planted bugs; find a few by eye |
| 2 | Offline pipeline | `python review.py samples/inventory.py --provider mock` | Static pre-check, the three contract sections, colour highlighting; say clearly it is not a model |
| 3 | Real review | `python review.py samples/inventory.py --diff` | Line numbers match; explanation in plain language; diff of the fix |
| 4 | Other languages | `python review.py samples/cart.js samples/Calculator.java` | Missing `await`, `==` on strings, unclosed reader; summary table |
| 5 | Provider failure | `python review.py samples/cart.js --model not-a-model` | "Model not found" message, no retry, exit code 4 (the format-rejection path is covered by `tests/test_pipeline.py`) |
| 6 | Input triage | `python review.py nope.py`, a binary file, a `.txt` file | Clean messages, exit code 3, no stack trace |
| 7 | CI gate | `python review.py samples -r --fail-on high; echo $?` | Exit code 1 |
| 8 | Reports | `--out reports/ --html reports/` | Markdown and HTML export |
| 9 | Web UI | `streamlit run app.py` | Upload/paste, finding cards, refactored code, diff, downloads |
| 10 | Evaluation | `python scripts/evaluate_samples.py --provider gemini --runs 3` | Planted-bug recall vs the 5/16 static baseline |

Slide outline: problem (human review limits) -> IPO pipeline -> ingestion triage -> system instruction and output
contract -> hybrid static + LLM -> rendering -> results from scenario 10 (your numbers only) -> limitations -> next steps
(SARIF output, more static analysers, project-wide context).
