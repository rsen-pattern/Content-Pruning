"""Generate a larger, realistic-ish set of sample exports for UI testing at scale.

Writes samples/large/{screaming_frog,gsc,ga4,backlinks}_large.csv.
Run: python -m samples.generate_large_sample [N]   (default N=250)
Deterministic (seeded) so fixtures are stable across runs.
"""
import csv
import random
import sys
from datetime import date, timedelta
from pathlib import Path

OUT = Path(__file__).resolve().parent / "large"
SECTIONS = ["blog", "guides", "products", "news", "resources", "compare", "help"]
TOPICS = ["running shoes", "email marketing", "tax filing", "home office",
          "credit cards", "yoga", "web hosting", "meal prep", "solar panels", "vpn"]


def gen(n: int = 250) -> None:
    rnd = random.Random(42)
    OUT.mkdir(parents=True, exist_ok=True)
    today = date(2026, 6, 4)

    frog, gsc, ga4, bl = [], [], [], []
    for i in range(n):
        section = rnd.choice(SECTIONS)
        topic = rnd.choice(TOPICS)
        year = rnd.choice(["", "", "", "-2024", "-2023"])
        slug = f"{topic.replace(' ', '-')}-{i}{year}"
        is_pdf = rnd.random() < 0.06
        url = f"https://example.com/{section}/{slug}" + (".pdf" if is_pdf else "")

        age_days = rnd.choice([30, 120, 300, 400, 800, 1200])
        last_mod = (today - timedelta(days=age_days)).isoformat()
        clicks = rnd.choices([0, rnd.randint(1, 90), rnd.randint(100, 3000)],
                             weights=[5, 3, 2])[0]
        rd = rnd.choices([0, rnd.randint(1, 5), rnd.randint(6, 60)], weights=[6, 2, 1])[0]
        words = 0 if is_pdf else rnd.choice([80, 220, 600, 1200, 2400])
        inlinks = rnd.choice([0, 0, 2, 8, 30])

        frog.append([url, 200, "Indexable" if rnd.random() > 0.05 else "Non-Indexable",
                     words, inlinks, last_mod,
                     f"{topic.title()} {i}", f"{topic.title()}",
                     "application/pdf" if is_pdf else "text/html; charset=UTF-8"])

        if clicks > 0 and not is_pdf:
            pos = round(rnd.uniform(1, 30), 1)
            impr = clicks * rnd.randint(8, 40)
            ctr = round(clicks / max(impr, 1), 4)
            # share a query within a topic to create some cannibalisation
            query = topic if rnd.random() < 0.3 else f"{topic} {i}"
            gsc.append([query, url, clicks, impr, ctr, pos])

        sessions = clicks + rnd.randint(0, 400)
        channel = rnd.choice(["Organic Search", "Email", "Direct", "Organic Social", "Referral"])
        ga4.append([url, channel, sessions, round(rnd.uniform(0.2, 0.9), 2),
                    rnd.randint(0, 20), rnd.randint(0, 5000)])

        if rd > 0:
            bl.append([url, rd, rd * rnd.randint(2, 12)])

    _write("screaming_frog_large.csv",
           ["Address", "Status Code", "Indexability", "Word Count", "Unique Inlinks",
            "Last Modified", "Title 1", "H1-1", "Content Type"], frog)
    _write("gsc_large.csv",
           ["Query", "Page", "Clicks", "Impressions", "CTR", "Position"], gsc)
    _write("ga4_large.csv",
           ["Page path", "Default channel group", "Sessions", "Engagement rate",
            "Conversions", "Total revenue"], ga4)
    _write("backlinks_large.csv", ["URL", "Referring Domains", "Backlinks"], bl)
    print(f"Wrote {n} URLs to {OUT}/ ({len(gsc)} GSC rows, {len(bl)} backlink rows).")


def _write(name: str, header: list, rows: list) -> None:
    with open(OUT / name, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


if __name__ == "__main__":
    gen(int(sys.argv[1]) if len(sys.argv) > 1 else 250)
