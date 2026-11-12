"""Seeded-bug recall of the *deterministic* layer, measurable without any API key.

samples/manifest.json lists the bugs planted in the sample files. The static
pre-check cannot find them all (that is the model's job), but what it does claim
must hold, and the number must not regress silently.
"""
import json
from pathlib import Path

from codereviewer.static_checks import precheck

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
MANIFEST = json.loads((SAMPLES / "manifest.json").read_text())


def recall(name: str) -> tuple[list[int], list[int]]:
    code = (SAMPLES / name).read_text()
    found = precheck("python", code)
    hit, miss = [], []
    for bug in MANIFEST[name]:
        ok = any(f.category == bug["category"] and abs((f.line or -99) - bug["line"]) <= 0 for f in found)
        (hit if ok else miss).append(bug["line"])
    return hit, miss


def test_manifest_lines_point_at_the_planted_bugs():
    lines = (SAMPLES / "inventory.py").read_text().splitlines()
    assert "stock=[]" in lines[3] and "len(prices) + 1" in lines[10] and "return total / len(prices)" in lines[12]
    assert "execute(f" in lines[17] and "open(path)" in lines[22] and "eval(data)" in lines[25]
    assert lines[26].strip() == "except:" and 'is "gold"' in lines[31]
    assert "<= items.length" in (SAMPLES / "cart.js").read_text().splitlines()[2]
    assert '== "admin"' in (SAMPLES / "Calculator.java").read_text().splitlines()[8]


def test_static_layer_catches_exactly_the_mechanical_python_bugs():
    hit, miss = recall("inventory.py")
    assert hit == [4, 18, 26, 27, 32]          # mutable default, SQL injection, eval, bare except, 'is' literal
    assert miss == [11, 13, 23]                # off-by-one, division by zero, leaked file handle: need the model
    assert len(hit) / (len(hit) + len(miss)) == 5 / 8


def test_other_languages_rely_entirely_on_the_model():
    assert precheck("javascript", (SAMPLES / "cart.js").read_text()) == []
    assert precheck("java", (SAMPLES / "Calculator.java").read_text()) == []
