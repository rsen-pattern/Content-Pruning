"""Bundle A — safety & correctness tests.

Run: python -m tests.test_safety
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from utils import loaders, router  # noqa: E402
from utils.config import Config  # noqa: E402


def _cfg():
    c = Config()
    c.apply_scenario("balanced")
    return c


def _rec(**kw):
    base = {"url": "https://e.com/old-page", "clicks_12mo": 0, "impressions_12mo": 0,
            "referring_domains": 0, "non_organic_sessions_12mo": 0, "word_count": 1000,
            "days_since_modified": 800, "is_indexable": True, "is_html": True,
            "is_stale": True, "has_year_in_url": False, "cannibalising_count": 0,
            "conversions_12mo": 0, "revenue_12mo": 0}
    base.update(kw)
    return base


def test_hard_delete_when_all_available():
    d = router.route(_rec(), _cfg(), set(), {"gsc": True, "ga4": True, "backlinks": True, "frog": True})
    assert d.action == router.DELETE_410, d.action


def test_absent_gsc_suppresses_delete():
    # GSC missing => '0 clicks' is unknown => must NOT hard-delete; escalate instead.
    d = router.route(_rec(), _cfg(), set(), {"gsc": False, "ga4": True, "backlinks": True, "frog": True})
    assert d.action == router.AMBIGUOUS, d.action


def test_absent_backlinks_suppresses_delete():
    d = router.route(_rec(), _cfg(), set(), {"gsc": True, "ga4": True, "backlinks": False, "frog": True})
    assert d.action == router.AMBIGUOUS, d.action


def test_converting_page_protected_from_deletion():
    d = router.route(_rec(conversions_12mo=12), _cfg(), set(),
                     {"gsc": True, "ga4": True, "backlinks": True, "frog": True})
    assert d.action == router.KEEP and d.reason == "protected-converter", (d.action, d.reason)


def test_revenue_page_protected():
    d = router.route(_rec(revenue_12mo=500.0), _cfg(), set(),
                     {"gsc": True, "ga4": True, "backlinks": True, "frog": True})
    assert d.action == router.KEEP and d.reason == "protected-converter", (d.action, d.reason)


def _lr(df, source):
    return loaders.LoadResult(df=df, source=source, warnings=[], found={}, rows=len(df))


def test_path_only_urls_reconcile_and_join():
    frog = _lr(pd.DataFrame({"url": ["https://e.com/a", "https://e.com/b"],
                             "word_count": [100, 200]}), "frog")
    ga4 = _lr(pd.DataFrame({"url": ["/a", "/b"], "sessions_12mo": [10, 20]}), "GA4")
    merged = loaders.merge_sources(frog=frog, ga4=ga4)
    assert len(merged) == 2, f"path-only URLs failed to join: {len(merged)} rows"
    assert merged["sessions_12mo"].notna().all(), "GA4 sessions did not attach after reconcile"


def test_join_report():
    frog = _lr(pd.DataFrame({"url": ["https://e.com/a", "https://e.com/b"]}), "frog")
    ga4 = _lr(pd.DataFrame({"url": ["/a", "/zzz"]}), "GA4")  # one matches, one doesn't
    merged = loaders.merge_sources(frog=frog, ga4=ga4)
    rep = loaders.join_report({"frog": frog, "ga4": ga4}, merged)
    ga4_row = rep[rep["source"] == "ga4"].iloc[0]
    assert ga4_row["matched"] == 1, ga4_row.to_dict()


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
    print("\nAll safety tests passed." if not failed else f"\n{failed} failed.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
