"""Bundle B — analytical depth tests.

Run: python -m tests.test_analytical
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from utils import router, signals  # noqa: E402
from utils.config import Config, DEFAULTS  # noqa: E402


def _cfg():
    c = Config()
    c.apply_scenario("balanced")
    return c


REF = datetime(2026, 6, 1, tzinfo=timezone.utc)


def test_intent_weighted_staleness():
    # 200 days old: transactional (factor 0.5 -> 182.5d) is stale; informational (365d) is not.
    mod = (REF - timedelta(days=200)).isoformat()
    df = pd.DataFrame({
        "url": ["https://e.com/buy", "https://e.com/learn"],
        "last_modified": [mod, mod],
        "intent": ["transactional", "informational"],
    })
    out = signals.compute_signals(df, _cfg(), reference_date=REF).set_index("url")
    assert bool(out.loc["https://e.com/buy", "is_stale"]) is True
    assert bool(out.loc["https://e.com/learn", "is_stale"]) is False


def test_declining_earner_refreshes():
    rec = {"url": "https://e.com/x", "clicks_12mo": 500, "is_stale": False,
           "is_declining": True, "is_html": True, "is_indexable": True}
    d = router.route(rec, _cfg())
    assert d.action == router.REFRESH and d.reason == "refresh-declining", (d.action, d.reason)
    rec["is_declining"] = False
    d2 = router.route(rec, _cfg())
    assert d2.action == router.KEEP and d2.reason == "strong-keep", (d2.action, d2.reason)


def test_evidence_score_from_presence():
    df = pd.DataFrame({
        "url": ["https://e.com/a", "https://e.com/b"],
        "present_frog": [True, True],
        "present_gsc": [True, False],
    })
    out = signals.compute_signals(df, _cfg(), reference_date=REF).set_index("url")
    assert out.loc["https://e.com/a", "evidence_score"] == 1.0
    assert out.loc["https://e.com/b", "evidence_score"] == 0.5


def test_orphan_keeper_flagged():
    df = pd.DataFrame({
        "url": ["https://e.com/orphan"],
        "clicks_12mo": [500], "internal_links_in": [0],
        "last_modified": [REF.isoformat()], "is_indexable": [True],
        "mime_type": ["text/html"],
    })
    sig = signals.compute_signals(df, _cfg(), reference_date=REF)
    decided = router.run_router(sig, _cfg())
    row = decided.iloc[0]
    assert row["action"] == router.KEEP
    assert bool(row["needs_internal_links"]) is True


def test_trend_change_pct():
    df = pd.DataFrame({
        "url": ["https://e.com/down", "https://e.com/up"],
        "clicks_12mo": [80, 150], "clicks_prev_12mo": [100, 100],
    })
    out = signals.compute_signals(df, _cfg(), reference_date=REF).set_index("url")
    assert out.loc["https://e.com/down", "clicks_change_pct"] == -0.2
    assert bool(out.loc["https://e.com/down", "is_declining"]) is True
    assert bool(out.loc["https://e.com/up", "is_declining"]) is False


def test_export_window_scales_thresholds():
    cfg = _cfg()
    window = 6
    factor = window / 12.0
    _SCALE_KEYS = ("keep_threshold", "non_organic_threshold", "stale_threshold_days",
                   "delete_410_age_days")
    for k in _SCALE_KEYS:
        cfg.detect(k, max(1, round(DEFAULTS[k] * factor)))

    # Thresholds should be ~half the defaults.
    assert cfg["keep_threshold"] == round(DEFAULTS["keep_threshold"] * 0.5)
    assert cfg["stale_threshold_days"] == round(DEFAULTS["stale_threshold_days"] * 0.5)
    assert cfg.provenance["keep_threshold"] == "detected"

    # User override wins over the detected window scaling.
    cfg.override("keep_threshold", 999)
    cfg.detect("keep_threshold", 25)          # re-apply scale — should be ignored
    assert cfg["keep_threshold"] == 999, "override should survive window rescaling"


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
    print("\nAll analytical tests passed." if not failed else f"\n{failed} failed.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
