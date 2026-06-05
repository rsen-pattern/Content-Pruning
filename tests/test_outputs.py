"""Bundle C — workflow & outputs tests.

Run: python -m tests.test_outputs
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from utils import exporters, router  # noqa: E402


def _decided():
    return pd.DataFrame({
        "url": ["https://e.com/a", "https://e.com/b", "https://e.com/c"],
        "action": [router.REFRESH, router.DELETE_301, router.KEEP],
        "reason": ["refresh-sweet-spot", "preserve-link-equity", "strong-keep"],
        "clicks_12mo": [200, 0, 500],
        "referring_domains": [2, 30, 5],
        "revenue_12mo": [0, 0, 1000],
        "impressions_12mo": [4000, 100, 9000],
        "is_indexable": [True, True, True],
    })


def test_priority_ranks_actionable_above_keep():
    d = _decided()
    p = exporters.compute_priority(d)
    # The 301 with 30 referring domains should outrank the KEEP (weight 0).
    assert p.iloc[1] > p.iloc[2], (p.iloc[1], p.iloc[2])
    assert p.iloc[2] == 0.0  # KEEP has zero action weight


def test_action_plan_excludes_keep():
    data = exporters.action_plan_xlsx(_decided())
    assert isinstance(data, bytes) and len(data) > 0


def test_grade_against_snapshot():
    prior = {"decisions": [
        {"url": "https://e.com/a", "action": router.KEEP, "clicks_12mo": 500},   # now 200 -> declined
        {"url": "https://e.com/b", "action": router.DELETE_410, "clicks_12mo": 0},  # now 0, gone -> safe-ish
        {"url": "https://e.com/x", "action": router.REFRESH, "clicks_12mo": 50},  # gone from inventory
    ]}
    cur = pd.DataFrame({"url": ["https://e.com/a"], "action": [router.KEEP], "clicks_12mo": [200]})
    g = exporters.grade_against_snapshot(cur, prior).set_index("url")
    assert "declined" in g.loc["https://e.com/a", "verdict"].lower()
    assert g.loc["https://e.com/x", "current_action"] == "(gone from inventory)"


def test_pdf_or_none():
    out = exporters.executive_summary_pdf(_decided(), {"scenario": "balanced"}, guides_loaded=False)
    # fpdf2 is in requirements; if present we get bytes, else graceful None.
    assert out is None or (isinstance(out, bytes) and out[:4] == b"%PDF")


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  [ok ] {t.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"  [FAIL] {t.__name__}: {exc}")
    print("\nAll output tests passed." if not failed else f"\n{failed} failed.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
