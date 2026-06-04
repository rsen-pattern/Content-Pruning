"""End-to-end check: loaders -> merge -> signals -> router against the samples.

Run: python -m tests.test_pipeline   (from repo root)
No pytest dependency — plain asserts so it runs anywhere.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from utils import loaders, signals, router  # noqa: E402
from utils.config import Config  # noqa: E402

S = ROOT / "samples"

EXPECTED = {
    "https://example.com/strong-guide": ("keep", "strong-keep"),
    "https://example.com/stale-earner": ("refresh", "refresh-stale-earner"),
    "https://example.com/best-tools-2024": ("schedule_update", "scheduled-obsolescence"),
    "https://example.com/sweet-spot": ("refresh", "refresh-sweet-spot"),
    "https://example.com/low-ctr": ("refresh", "refresh-title-meta"),
    "https://example.com/cannibal-a": ("consolidate", "cannibalisation"),
    "https://example.com/cannibal-b": ("consolidate", "cannibalisation"),
    "https://example.com/linked-deadweight": ("delete_301", "preserve-link-equity"),
    "https://example.com/email-lander": ("noindex", "non-organic-noindex"),
    "https://example.com/thin-orphan": ("noindex", "thin-no-value"),
    "https://example.com/ancient-junk": ("delete_410", "hard-delete"),
    "https://example.com/noindexed-thing": ("delete_301", "already-deindexed-with-equity"),
    "https://example.com/whitepaper.pdf": ("keep", "non-html-linked-asset"),
    "https://example.com/ambiguous-page": ("ambiguous", "needs-judgment"),
}


def main() -> int:
    frog = loaders.load_screaming_frog(open(S / "screaming_frog_sample.csv", "rb"),
                                       "screaming_frog_sample.csv")
    gsc = loaders.load_gsc(open(S / "gsc_sample.csv", "rb"), "gsc_sample.csv")
    ga4 = loaders.load_ga4(open(S / "ga4_sample.csv", "rb"), "ga4_sample.csv")
    bl = loaders.load_backlinks(open(S / "backlinks_sample.csv", "rb"), "backlinks_sample.csv")

    for res in (frog, gsc, ga4, bl):
        print(f"  {res.source}: {res.rows} rows, {len(res.warnings)} warnings")
        for w in res.warnings:
            print(f"      ! {w}")

    merged = loaders.merge_sources(frog, gsc, ga4, bl)
    cfg = Config()
    cfg.apply_scenario("balanced")
    sig = signals.compute_signals(merged, cfg)
    decided = router.run_router(sig, cfg)

    by_url = decided.set_index("url")
    failures = []
    for url, (exp_action, exp_reason) in EXPECTED.items():
        if url not in by_url.index:
            failures.append(f"{url}: MISSING from output")
            continue
        row = by_url.loc[url]
        got_action, got_reason = row["action"], row["reason"]
        mark = "ok " if (got_action == exp_action and got_reason == exp_reason) else "FAIL"
        if mark == "FAIL":
            failures.append(f"{url}: expected ({exp_action}/{exp_reason}) got ({got_action}/{got_reason})")
        print(f"  [{mark}] {url:48s} -> {got_action}/{got_reason}")

    # 301 destination spot-check
    dead = by_url.loc["https://example.com/linked-deadweight"]
    print(f"\n  linked-deadweight 301 destination -> {dead['destination_url']}")

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print("  -", f)
        return 1
    print("\nAll router assertions passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
