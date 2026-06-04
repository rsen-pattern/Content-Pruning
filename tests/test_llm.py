"""Unit tests for the LLM layer's parsing & normalisation — no gateway calls.

Run: python -m tests.test_llm   (from repo root)
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from utils import llm  # noqa: E402
from utils.router import AMBIGUOUS, DELETE_301, REFRESH  # noqa: E402


def test_extract_json():
    assert llm._extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert llm._extract_json('prose before [1,2,3] after') == [1, 2, 3]
    assert llm._extract_json("not json at all") is None


def test_normalize_action():
    assert llm.normalize_action("Refresh") == REFRESH
    assert llm.normalize_action("  refresh ") == REFRESH
    assert llm.normalize_action("delete-301") == DELETE_301
    assert llm.normalize_action("delete 301") == DELETE_301
    assert llm.normalize_action("nonsense") == AMBIGUOUS
    assert llm.normalize_action(None) == AMBIGUOUS


def test_judge_ambiguous_normalises(monkeypatch=None):
    """A messy model response should still yield valid, normalised actions."""
    canned = (
        '[{"url":"https://e.com/a","action":"Refresh","rationale":"r","confidence":0.8},'
        '{"url":"https://e.com/b","action":"delete-301","rationale":"r2","confidence":0.6},'
        '{"url":"https://e.com/c","action":"wat","rationale":"r3","confidence":0.1}]'
    )
    llm.call_with_fallback = lambda *a, **k: (canned, "anthropic/claude-sonnet-4-6")
    df = pd.DataFrame({"url": ["https://e.com/a", "https://e.com/b", "https://e.com/c"],
                       "title": ["", "", ""]})
    out, used = llm.judge_ambiguous(None, "model", df, "guides", batch_size=5, fetch=False)
    assert out["https://e.com/a"]["action"] == REFRESH
    assert out["https://e.com/b"]["action"] == DELETE_301
    assert out["https://e.com/c"]["action"] == AMBIGUOUS  # unknown -> re-escalate
    assert used == "anthropic/claude-sonnet-4-6"


def test_estimate_respects_fetch():
    df = pd.DataFrame({"url": [f"https://e.com/{i}" for i in range(10)], "title": [""] * 10})
    guides = "g" * 500
    with_fetch = llm.estimate_ambiguous_cost(df, "anthropic/claude-sonnet-4-6", 5, guides, True)
    no_fetch = llm.estimate_ambiguous_cost(df, "anthropic/claude-sonnet-4-6", 5, guides, False)
    assert no_fetch["input_tokens"] < with_fetch["input_tokens"]
    assert no_fetch["usd"] < with_fetch["usd"]


def main() -> int:
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  [ok ] {t.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"  [FAIL] {t.__name__}: {exc}")
    print("\nAll LLM unit tests passed." if not failed else f"\n{failed} test(s) failed.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
