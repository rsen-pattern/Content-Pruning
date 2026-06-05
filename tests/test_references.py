"""Wiring-in-the-guides tests: maturity guard, 301-over-410, guide loading.

Run: python -m tests.test_references
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from utils import llm, router  # noqa: E402
from utils.config import Config  # noqa: E402


def _cfg():
    c = Config()
    c.apply_scenario("balanced")
    return c


def _rec(**kw):
    base = {"url": "https://e.com/p", "clicks_12mo": 0, "referring_domains": 0,
            "non_organic_sessions_12mo": 0, "word_count": 1000, "days_since_modified": 800,
            "is_indexable": True, "is_html": True, "is_stale": True,
            "conversions_12mo": 0, "revenue_12mo": 0}
    base.update(kw)
    return base


def test_mature_page_still_deletes():
    d = router.route(_rec(days_since_modified=800), _cfg(), set())
    assert d.action == router.DELETE_410


def test_too_new_page_is_protected():
    # Thin page (would be NOINDEX) but changed 30 days ago => protected.
    d = router.route(_rec(word_count=90, days_since_modified=30), _cfg(), set())
    assert d.action == router.AMBIGUOUS and d.reason == "too-new-to-prune", (d.action, d.reason)


def test_unknown_age_does_not_trigger_guard():
    # Thin page with unknown last_modified => can't assess maturity => NOINDEX stands.
    d = router.route(_rec(word_count=90, days_since_modified=float("nan")), _cfg(), set())
    assert d.action == router.NOINDEX, d.action


def test_410_upgraded_to_301_when_cluster_match():
    df = pd.DataFrame([
        {"url": "https://e.com/dead", "action": router.DELETE_410, "reason": "hard-delete",
         "topical_cluster": "widgets", "clicks_12mo": 0, "referring_domains": 0,
         "destination_url": None},
        {"url": "https://e.com/hub", "action": router.KEEP, "reason": "strong-keep",
         "topical_cluster": "widgets", "clicks_12mo": 500, "referring_domains": 10,
         "destination_url": None},
    ])
    out = router.prefer_redirect_for_deletes(df).set_index("url")
    assert out.loc["https://e.com/dead", "action"] == router.DELETE_301
    assert out.loc["https://e.com/dead", "destination_url"] == "https://e.com/hub"


def test_410_stands_without_cluster_match():
    df = pd.DataFrame([
        {"url": "https://e.com/dead", "action": router.DELETE_410, "reason": "hard-delete",
         "topical_cluster": "widgets", "clicks_12mo": 0, "referring_domains": 0,
         "destination_url": None},
        {"url": "https://e.com/hub", "action": router.KEEP, "reason": "strong-keep",
         "topical_cluster": "gadgets", "clicks_12mo": 500, "referring_domains": 10,
         "destination_url": None},
    ])
    out = router.prefer_redirect_for_deletes(df).set_index("url")
    assert out.loc["https://e.com/dead", "action"] == router.DELETE_410


def test_load_guides_excludes_readme_and_loads_sources():
    guides = llm.load_guides()
    assert "No reference guides are loaded" not in guides  # the 7 guides are present
    assert "Reference guides" not in guides[:200]          # README heading not injected
    # A couple of distinctive phrases from the real guides:
    assert "wardrobe" in guides or "crawl budget" in guides


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
    print("\nAll reference-wiring tests passed." if not failed else f"\n{failed} failed.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
