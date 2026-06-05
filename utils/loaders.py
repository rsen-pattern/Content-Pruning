"""Parse the four data exports into normalised, URL-keyed frames.

Graceful degradation is the rule: a missing column produces a warning and a
column of NaN/defaults, never a crash. Each loader returns a LoadResult so the
Data Upload page can show exactly what was found and what was missing.

Supported (column names are matched fuzzily, case-insensitive):
  - Screaming Frog  internal_html export
  - Google Search Console  performance export (pages, or query+page)
  - GA4  pages export (with optional channel-group split)
  - Ahrefs / Semrush  backlinks / top-pages export
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlsplit, urlunsplit

import numpy as np
import pandas as pd


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #
def normalize_url(url: Any) -> Optional[str]:
    """Canonicalise for joins: lowercase scheme+host, drop fragment, strip a
    single trailing slash (but keep the root '/')."""
    if url is None or (isinstance(url, float) and pd.isna(url)):
        return None
    s = str(url).strip()
    if not s:
        return None
    parts = urlsplit(s)
    scheme = (parts.scheme or "https").lower()
    netloc = parts.netloc.lower()
    path = parts.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def _read_any(file: Any, filename: str = "") -> pd.DataFrame:
    """Read CSV or XLSX from a path or a Streamlit UploadedFile."""
    name = (filename or getattr(file, "name", "") or "").lower()
    data = file.read() if hasattr(file, "read") else None
    if name.endswith((".xlsx", ".xls")):
        return pd.read_excel(io.BytesIO(data) if data is not None else file)
    if data is not None:
        # Frog/GSC exports are usually UTF-8 or UTF-16 (tab) — try a couple.
        for enc in ("utf-8-sig", "utf-16", "latin-1"):
            try:
                return pd.read_csv(io.BytesIO(data), encoding=enc, sep=None, engine="python")
            except Exception:
                continue
        return pd.read_csv(io.BytesIO(data))
    return pd.read_csv(file, sep=None, engine="python")


def _find_col(df: pd.DataFrame, candidates: list[str]) -> Optional[str]:
    """First column whose lowercased name contains any candidate substring."""
    lowered = {c: str(c).lower().strip() for c in df.columns}
    for cand in candidates:
        cand = cand.lower()
        for col, low in lowered.items():
            if low == cand:
                return col
    for cand in candidates:
        cand = cand.lower()
        for col, low in lowered.items():
            if cand in low:
                return col
    return None


def _to_num(series: pd.Series) -> pd.Series:
    """Coerce strings like '1,234', '12.3%', '$1.2k' to floats."""
    if series is None:
        return series
    s = series.astype(str).str.strip()
    s = s.str.replace(",", "", regex=False)
    s = s.str.replace("%", "", regex=False)
    s = s.str.replace("$", "", regex=False)
    s = s.str.replace("£", "", regex=False)
    return pd.to_numeric(s, errors="coerce")


@dataclass
class LoadResult:
    df: pd.DataFrame
    source: str
    warnings: list[str] = field(default_factory=list)
    found: dict[str, str] = field(default_factory=dict)  # canonical -> source column
    rows: int = 0

    @property
    def ok(self) -> bool:
        return not self.df.empty


def _column_map(df: pd.DataFrame, spec: dict[str, list[str]], source: str):
    """Resolve a {canonical: [candidates]} map; collect warnings for misses."""
    warnings: list[str] = []
    found: dict[str, str] = {}
    for canonical, cands in spec.items():
        col = _find_col(df, cands)
        if col is not None:
            found[canonical] = col
        else:
            warnings.append(f"[{source}] missing '{canonical}' (looked for {cands[0]}…) — defaulted")
    return found, warnings


# --------------------------------------------------------------------------- #
# Screaming Frog                                                               #
# --------------------------------------------------------------------------- #
def load_screaming_frog(file: Any, filename: str = "") -> LoadResult:
    df = _read_any(file, filename)
    spec = {
        "url": ["address", "url"],
        "status_code": ["status code", "status"],
        "is_indexable": ["indexability", "indexability status"],
        "word_count": ["word count", "words"],
        "internal_links_in": ["unique inlinks", "inlinks", "links in", "internal inlinks"],
        "last_modified": ["last modified", "last-modified", "modified"],
        "title": ["title 1", "title"],
        "h1": ["h1-1", "h1"],
        "mime_type": ["content type", "content-type", "mime"],
    }
    found, warnings = _column_map(df, spec, "Screaming Frog")
    out = pd.DataFrame()
    out["url"] = df[found["url"]].map(normalize_url) if "url" in found else None
    out["status_code"] = _to_num(df[found["status_code"]]) if "status_code" in found else pd.NA
    if "is_indexable" in found:
        out["is_indexable"] = (
            df[found["is_indexable"]].astype(str).str.strip().str.lower().eq("indexable")
        )
    else:
        out["is_indexable"] = True
    out["word_count"] = _to_num(df[found["word_count"]]) if "word_count" in found else pd.NA
    out["internal_links_in"] = (
        _to_num(df[found["internal_links_in"]]) if "internal_links_in" in found else pd.NA
    )
    out["last_modified"] = (
        pd.to_datetime(df[found["last_modified"]], errors="coerce")
        if "last_modified" in found else pd.NaT
    )
    out["title"] = df[found["title"]] if "title" in found else ""
    out["h1"] = df[found["h1"]] if "h1" in found else ""
    out["mime_type"] = df[found["mime_type"]] if "mime_type" in found else "text/html"
    out = out.dropna(subset=["url"]).drop_duplicates(subset=["url"])
    return LoadResult(out, "Screaming Frog", warnings, found, len(out))


# --------------------------------------------------------------------------- #
# Google Search Console                                                        #
# --------------------------------------------------------------------------- #
def load_gsc(file: Any, filename: str = "") -> LoadResult:
    """Accepts a pages export or a combined query+page export.

    When a query column is present we derive top_query per URL and the
    cannibalisation map (queries shared across URLs)."""
    df = _read_any(file, filename)
    page_col = _find_col(df, ["page", "landing page", "top pages", "url", "address", "full url"])
    query_col = _find_col(df, ["query", "search query", "keyword", "queries"])
    clicks_col = _find_col(df, ["clicks", "url clicks"])
    imp_col = _find_col(df, ["impressions", "impr"])
    ctr_col = _find_col(df, ["ctr", "click through", "click-through rate"])
    pos_col = _find_col(df, ["position", "avg. pos", "average position", "avg position"])

    warnings: list[str] = []
    found: dict[str, str] = {}
    if page_col is None:
        warnings.append("[GSC] no page/URL column found — GSC signals unavailable")
        return LoadResult(pd.DataFrame(columns=["url"]), "GSC", warnings, found, 0)

    df = df.copy()
    df["_url"] = df[page_col].map(normalize_url)
    df = df.dropna(subset=["_url"])
    for canon, col in (("clicks", clicks_col), ("impressions", imp_col),
                       ("ctr", ctr_col), ("position", pos_col)):
        if col is None:
            warnings.append(f"[GSC] missing '{canon}' — defaulted to 0")
        else:
            found[canon] = col
            df[f"_{canon}"] = _to_num(df[col])
    for canon in ("clicks", "impressions", "ctr", "position"):
        if f"_{canon}" not in df:
            df[f"_{canon}"] = 0.0
    # CTR may be a fraction or a percent; normalise to fraction.
    if df["_ctr"].notna().any() and df["_ctr"].max() > 1.5:
        df["_ctr"] = df["_ctr"] / 100.0

    top_query: dict[str, Optional[str]] = {}
    cannibal: dict[str, list[str]] = {}
    if query_col is not None:
        found["top_query"] = query_col
        df["_query"] = df[query_col].astype(str)
        # top query per URL = highest-click query
        idx = df.sort_values("_clicks", ascending=False).groupby("_url").head(1)
        top_query = dict(zip(idx["_url"], idx["_query"]))
        # cannibalisation: for each URL's top query, which other URLs also rank?
        by_query = df.groupby("_query")["_url"].apply(lambda s: sorted(set(s)))
        for url, q in top_query.items():
            others = [u for u in by_query.get(q, []) if u != url]
            cannibal[url] = others
    else:
        warnings.append("[GSC] no query column — top_query & cannibalisation unavailable")

    agg = (
        df.groupby("_url")
        .agg(clicks_12mo=("_clicks", "sum"),
             impressions_12mo=("_impressions", "sum"),
             ctr=("_ctr", "mean"),
             avg_position=("_position", "mean"))
        .reset_index()
        .rename(columns={"_url": "url"})
    )
    agg["top_query"] = agg["url"].map(top_query) if top_query else None
    agg["cannibalising_urls"] = agg["url"].map(lambda u: cannibal.get(u, [])) if cannibal else [[]] * len(agg)
    return LoadResult(agg, "GSC", warnings, found, len(agg))


# --------------------------------------------------------------------------- #
# GA4                                                                          #
# --------------------------------------------------------------------------- #
def load_ga4(file: Any, filename: str = "") -> LoadResult:
    df = _read_any(file, filename)
    page_col = _find_col(df, ["page path", "page path and screen class", "landing page",
                              "page", "url", "address", "full url"])
    sessions_col = _find_col(df, ["sessions", "session", "total sessions"])
    eng_col = _find_col(df, ["engagement rate", "engaged sessions rate"])
    conv_col = _find_col(df, ["conversions", "key events", "total conversions", "total key events"])
    rev_col = _find_col(df, ["total revenue", "revenue", "purchase revenue", "event revenue"])
    channel_col = _find_col(df, ["default channel group", "channel group", "channel", "source / medium", "session source"])

    warnings: list[str] = []
    found: dict[str, str] = {}
    if page_col is None:
        warnings.append("[GA4] no page/URL column found — GA4 signals unavailable")
        return LoadResult(pd.DataFrame(columns=["url"]), "GA4", warnings, found, 0)

    df = df.copy()
    df["_url"] = df[page_col].map(normalize_url)
    df = df.dropna(subset=["_url"])
    found["url"] = page_col
    df["_sessions"] = _to_num(df[sessions_col]) if sessions_col else 0.0
    if not sessions_col:
        warnings.append("[GA4] missing 'sessions' — defaulted to 0")
    else:
        found["sessions"] = sessions_col
    df["_eng"] = _to_num(df[eng_col]) if eng_col else np.nan
    df["_conv"] = _to_num(df[conv_col]) if conv_col else 0.0
    df["_rev"] = _to_num(df[rev_col]) if rev_col else 0.0
    for canon, col in (("engagement_rate", eng_col), ("conversions", conv_col), ("revenue", rev_col)):
        if col is None:
            warnings.append(f"[GA4] missing '{canon}' — defaulted")
        else:
            found[canon] = col

    # Engagement rate may be a fraction or a percent; normalise to fraction.
    if eng_col is not None and df["_eng"].notna().any() and df["_eng"].max() > 1.5:
        df["_eng"] = df["_eng"] / 100.0

    # Split organic vs non-organic if a channel column exists.
    if channel_col is not None:
        found["channel"] = channel_col
        ch = df[channel_col].astype(str).str.lower().str.strip()
        # Organic = organic *search* only. "Organic Social" is non-organic here.
        df["_is_organic"] = ch.str.contains("organic search") | (ch == "organic")
        df["_non_org"] = df["_sessions"].where(~df["_is_organic"], 0.0)
    else:
        warnings.append("[GA4] no channel-group column — non_organic_sessions unavailable (set to 0)")
        df["_non_org"] = 0.0

    agg = (
        df.groupby("_url")
        .agg(sessions_12mo=("_sessions", "sum"),
             engagement_rate=("_eng", "mean"),
             conversions_12mo=("_conv", "sum"),
             revenue_12mo=("_rev", "sum"),
             non_organic_sessions_12mo=("_non_org", "sum"))
        .reset_index()
        .rename(columns={"_url": "url"})
    )
    return LoadResult(agg, "GA4", warnings, found, len(agg))


# --------------------------------------------------------------------------- #
# Ahrefs / Semrush backlinks                                                   #
# --------------------------------------------------------------------------- #
def load_backlinks(file: Any, filename: str = "") -> LoadResult:
    df = _read_any(file, filename)
    spec = {
        "url": ["url", "target url", "target", "target page", "page", "page url", "address"],
        "referring_domains": ["referring domains", "ref domains", "referring domain", "domains", "rd"],
        "backlinks": ["backlinks", "total backlinks", "ext. backlinks", "ext backlinks", "number of backlinks"],
    }
    found, warnings = _column_map(df, spec, "Backlinks")
    if "url" not in found:
        warnings.append("[Backlinks] no URL column — backlink signals unavailable")
        return LoadResult(pd.DataFrame(columns=["url"]), "Backlinks", warnings, found, 0)
    out = pd.DataFrame()
    out["url"] = df[found["url"]].map(normalize_url)
    out["referring_domains"] = _to_num(df[found["referring_domains"]]) if "referring_domains" in found else 0
    out["backlinks"] = _to_num(df[found["backlinks"]]) if "backlinks" in found else 0
    out = out.dropna(subset=["url"]).groupby("url", as_index=False).max(numeric_only=True)
    return LoadResult(out, "Backlinks", warnings, found, len(out))


# --------------------------------------------------------------------------- #
# Merge                                                                        #
# --------------------------------------------------------------------------- #
def _infer_base_url(results: list[LoadResult]) -> Optional[str]:
    """Most common scheme://host across sources that carry absolute URLs.

    Used to repair path-only exports (GA4 often emits '/path' not the full URL),
    which would otherwise silently fail to join the inventory."""
    hosts: dict[str, int] = {}
    for res in results:
        if res is None or not res.ok:
            continue
        for u in res.df["url"].dropna().astype(str):
            parts = urlsplit(u)
            if parts.netloc:
                hosts[f"{parts.scheme}://{parts.netloc}"] = hosts.get(f"{parts.scheme}://{parts.netloc}", 0) + 1
    return max(hosts, key=hosts.get) if hosts else None


def _reconcile_urls(df: pd.DataFrame, base: Optional[str]) -> pd.DataFrame:
    """Prefix path-only URLs (empty host) with the inferred base host."""
    if not base or df.empty or "url" not in df:
        return df
    bp = urlsplit(base)

    def fix(u):
        if not isinstance(u, str):
            return u
        p = urlsplit(u)
        if not p.netloc:
            return urlunsplit((bp.scheme, bp.netloc, p.path or "/", p.query, ""))
        return u

    df = df.copy()
    df["url"] = df["url"].map(fix)
    return df


def merge_sources(
    frog: Optional[LoadResult] = None,
    gsc: Optional[LoadResult] = None,
    ga4: Optional[LoadResult] = None,
    backlinks: Optional[LoadResult] = None,
) -> pd.DataFrame:
    """Outer-join all available sources on the normalised URL.

    Path-only URLs are reconciled against the inferred site host first, so a
    GA4 'Page path' export still joins a Screaming Frog absolute-URL crawl.
    Screaming Frog is the spine when present; otherwise the union of URLs."""
    results = [r for r in (frog, gsc, ga4, backlinks) if r is not None and r.ok]
    if not results:
        return pd.DataFrame()
    base = _infer_base_url(results)
    frames = [_reconcile_urls(r.df, base) for r in results]
    merged = frames[0]
    for f in frames[1:]:
        merged = merged.merge(f, on="url", how="outer")
    return merged.reset_index(drop=True)


def join_report(results: dict, merged: pd.DataFrame) -> pd.DataFrame:
    """Per-source diagnostics: rows loaded and what % joined the inventory.

    `results` maps source-key -> LoadResult. A low match rate usually means a
    URL-format mismatch (protocol, www, trailing slash, path-only)."""
    valid = {k: r for k, r in results.items() if r is not None and r.ok}
    base = _infer_base_url(list(valid.values()))
    reconciled = {k: set(_reconcile_urls(r.df, base)["url"].dropna()) for k, r in valid.items()}
    rows = []
    for key, res in results.items():
        if key not in valid:
            rows.append({"source": key, "rows": 0, "matched": 0, "match_rate": "—"})
            continue
        urls = reconciled[key]
        # Match against the union of the OTHER sources, not the unioned merge.
        others = set().union(*[u for k, u in reconciled.items() if k != key]) if len(reconciled) > 1 else set()
        matched = len(urls & others)
        rate = f"{100.0 * matched / len(urls):.0f}%" if urls and others else "—"
        rows.append({"source": key, "rows": res.rows, "matched": matched, "match_rate": rate})
    return pd.DataFrame(rows)
