"""Build the downloadable deliverables. Each returns bytes (or str for the
Markdown summary) so the Streamlit page can offer a download directly.

  1. decision_spreadsheet_xlsx   2. redirect_map_csv     3. refresh_queue_xlsx
  4. consolidation_plan_xlsx     5. repurpose_backlog_xlsx
  6. snapshot_json               7. executive_summary_md
  8. action_plan_xlsx (priority-ranked)  + executive_summary_pdf
  + grade_against_snapshot (per-URL grading vs a prior audit)
"""
from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from utils import router as R

# Columns surfaced in the human-facing decision spreadsheet.
_SIGNAL_VIEW = [
    "url", "action", "reason", "priority_score", "destination_url", "confidence", "source",
    "clicks_12mo", "impressions_12mo", "avg_position", "ctr",
    "sessions_12mo", "non_organic_sessions_12mo", "conversions_12mo", "revenue_12mo",
    "referring_domains", "backlinks", "internal_links_in", "word_count",
    "days_since_modified", "is_indexable", "is_orphan", "needs_internal_links",
    "has_year_in_url", "clicks_change_pct", "is_declining", "evidence_score",
    "topical_cluster", "intent", "top_query", "note",
]


def _xlsx(sheets: dict[str, pd.DataFrame]) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        for name, frame in sheets.items():
            (frame if not frame.empty else pd.DataFrame({"info": ["(none)"]})).to_excel(
                writer, sheet_name=name[:31], index=False
            )
    return buf.getvalue()


def _view(df: pd.DataFrame) -> pd.DataFrame:
    cols = [c for c in _SIGNAL_VIEW if c in df.columns]
    return df[cols].copy()


# --------------------------------------------------------------------------- #
# 1. Decision spreadsheet                                                      #
# --------------------------------------------------------------------------- #
def decision_spreadsheet_xlsx(decided: pd.DataFrame) -> bytes:
    decided = decided.copy()
    if "priority_score" not in decided:
        decided["priority_score"] = compute_priority(decided)
    view = _view(decided)
    view["manual_override"] = ""  # user edits before re-running
    return _xlsx({"Decisions": view})


# --------------------------------------------------------------------------- #
# 2. Redirect map                                                              #
# --------------------------------------------------------------------------- #
def redirect_map_csv(decided: pd.DataFrame) -> bytes:
    rows = []
    for _, r in decided.iterrows():
        if r["action"] == R.DELETE_301:
            rows.append({"source_url": r["url"],
                         "destination_url": r.get("destination_url") or "",
                         "status_code": 301})
        elif r["action"] == R.DELETE_410:
            rows.append({"source_url": r["url"], "destination_url": "", "status_code": 410})
    df = pd.DataFrame(rows, columns=["source_url", "destination_url", "status_code"])
    return df.to_csv(index=False).encode("utf-8")


# --------------------------------------------------------------------------- #
# 3. Refresh queue                                                             #
# --------------------------------------------------------------------------- #
def _roi_proxy(row) -> float:
    """clicks_12mo x position_to_first_page_gap (distance below the first page)."""
    clicks = pd.to_numeric(pd.Series([row.get("clicks_12mo", 0)]), errors="coerce").fillna(0).iloc[0]
    pos = row.get("avg_position")
    gap = max(0.0, (float(pos) - 10.0)) if pos is not None and pd.notna(pos) else 0.0
    return float(clicks) * gap


def refresh_queue_xlsx(decided: pd.DataFrame, suggestions: Optional[dict] = None) -> bytes:
    suggestions = suggestions or {}
    ref = decided[decided["action"] == R.REFRESH].copy()
    if ref.empty:
        return _xlsx({"Refresh Queue": pd.DataFrame()})
    ref["roi_proxy"] = ref.apply(_roi_proxy, axis=1)
    ref["update_suggestions"] = ref["url"].map(
        lambda u: " | ".join(suggestions.get(u, [])) if suggestions.get(u) else ""
    )
    cols = ["url", "reason", "roi_proxy", "clicks_12mo", "impressions_12mo",
            "avg_position", "ctr", "top_query", "update_suggestions", "note"]
    cols = [c for c in cols if c in ref.columns]
    ref = ref.sort_values(["roi_proxy", "clicks_12mo", "impressions_12mo"],
                          ascending=False)[cols]
    return _xlsx({"Refresh Queue": ref})


# --------------------------------------------------------------------------- #
# 4. Consolidation plan                                                        #
# --------------------------------------------------------------------------- #
def consolidation_plan_xlsx(decided: pd.DataFrame) -> bytes:
    con = decided[decided["action"] == R.CONSOLIDATE].copy()
    if con.empty:
        return _xlsx({"Clusters": pd.DataFrame(), "Redirects": pd.DataFrame()})

    con["_authority"] = (
        pd.to_numeric(con.get("backlinks", 0), errors="coerce").fillna(0)
        + pd.to_numeric(con.get("clicks_12mo", 0), errors="coerce").fillna(0)
    )
    group_key = "topical_cluster" if con["topical_cluster"].notna().any() else "top_query"
    con[group_key] = con[group_key].fillna("(ungrouped)")

    clusters, redirects = [], []
    for key, grp in con.groupby(group_key):
        winner = grp.sort_values("_authority", ascending=False).iloc[0]
        losers = grp[grp["url"] != winner["url"]]
        clusters.append({
            "cluster": key,
            "winner_url": winner["url"],
            "winner_authority": winner["_authority"],
            "members": len(grp),
            "merge_notes": "Merge unique sections from members into the winner; "
                           "preserve top-performing headings and internal links.",
        })
        for _, lo in losers.iterrows():
            redirects.append({"source_url": lo["url"], "destination_url": winner["url"],
                              "status_code": 301})
    return _xlsx({"Clusters": pd.DataFrame(clusters), "Redirects": pd.DataFrame(redirects)})


# --------------------------------------------------------------------------- #
# 5. Repurpose backlog                                                         #
# --------------------------------------------------------------------------- #
def repurpose_backlog_xlsx(decided: pd.DataFrame, suggestions: Optional[dict] = None) -> bytes:
    suggestions = suggestions or {}
    rep = decided[decided["action"] == R.REPURPOSE].copy()
    if rep.empty:
        return _xlsx({"Repurpose Backlog": pd.DataFrame()})
    rep["format_suggestions"] = rep["url"].map(
        lambda u: " | ".join(suggestions.get(u, [])) if suggestions.get(u) else ""
    )
    cols = ["url", "reason", "clicks_12mo", "impressions_12mo", "intent",
            "topical_cluster", "format_suggestions", "note"]
    cols = [c for c in cols if c in rep.columns]
    return _xlsx({"Repurpose Backlog": rep[cols]})


# --------------------------------------------------------------------------- #
# 6. Snapshot JSON                                                             #
# --------------------------------------------------------------------------- #
def action_counts(decided: pd.DataFrame) -> dict:
    if decided.empty or "action" not in decided:
        return {}
    return {k: int(v) for k, v in decided["action"].value_counts().to_dict().items()}


def snapshot_json(decided: pd.DataFrame, config, meta: Optional[dict] = None,
                  llm_overrides: Optional[dict] = None,
                  manual_overrides: Optional[dict] = None,
                  assignments: Optional[dict] = None) -> bytes:
    """Full audit state. Persists LLM judgments + cluster/intent assignments so a
    future run can restore them (no re-paying for the same LLM calls)."""
    records = json.loads(_view(decided).to_json(orient="records"))
    payload = {
        "schema": "content-audit-engine/snapshot/v2",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "config": config.as_dict() if hasattr(config, "as_dict") else dict(config),
        "counts": action_counts(decided),
        "url_count": int(len(decided)),
        "decisions": records,
        "llm_overrides": llm_overrides or {},
        "manual_overrides": manual_overrides or {},
        "assignments": assignments or {},  # url -> {topical_cluster, intent}
        "meta": meta or {},
    }
    return json.dumps(payload, indent=2, default=str).encode("utf-8")


def assignments_from_decided(decided: pd.DataFrame) -> dict:
    """Extract {url: {topical_cluster, intent}} for any URL that has them."""
    if decided.empty or "url" not in decided:
        return {}
    out = {}
    for _, r in decided.iterrows():
        cluster = r.get("topical_cluster")
        intent = r.get("intent")
        rec = {}
        if cluster is not None and not pd.isna(cluster):
            rec["topical_cluster"] = cluster
        if intent is not None and not pd.isna(intent):
            rec["intent"] = intent
        if rec:
            out[r["url"]] = rec
    return out


# --------------------------------------------------------------------------- #
# 8. Priority score + Action Plan                                              #
# --------------------------------------------------------------------------- #
# Per-action weight (rough impact-per-effort for sequencing the work).
_ACTION_WEIGHT = {
    R.CONSOLIDATE: 1.0, R.REFRESH: 0.9, R.REPURPOSE: 0.7, R.DELETE_301: 0.6,
    R.NOINDEX: 0.5, R.DELETE_410: 0.4, R.SCHEDULE_UPDATE: 0.3,
    R.AMBIGUOUS: 0.2, R.KEEP: 0.0, R.NO_ACTION: 0.0,
}


def compute_priority(decided: pd.DataFrame) -> pd.Series:
    """A rough sequencing score: impact (clicks + link equity + revenue) times a
    per-action weight. Higher = do first. Advisory, not gospel."""
    if decided.empty:
        return pd.Series(dtype=float)
    num = lambda c: pd.to_numeric(decided.get(c, 0), errors="coerce").fillna(0)  # noqa: E731
    impact = num("clicks_12mo") + num("referring_domains") * 5 + num("revenue_12mo") * 0.1 + num("impressions_12mo") * 0.01
    weight = decided["action"].map(_ACTION_WEIGHT).fillna(0.3)
    return (impact * weight).round(1)


def action_plan_xlsx(decided: pd.DataFrame) -> bytes:
    df = decided.copy()
    df["priority_score"] = compute_priority(df)
    actionable = df[~df["action"].isin([R.KEEP, R.NO_ACTION])].copy()
    actionable = actionable.sort_values("priority_score", ascending=False)
    cols = [c for c in ["url", "action", "reason", "priority_score", "clicks_12mo",
                        "referring_domains", "revenue_12mo", "destination_url",
                        "needs_internal_links", "note"] if c in actionable.columns]
    return _xlsx({"Action Plan": actionable[cols]})


# --------------------------------------------------------------------------- #
# Per-URL grading vs a prior snapshot                                          #
# --------------------------------------------------------------------------- #
def grade_against_snapshot(decided: pd.DataFrame, prior: dict) -> pd.DataFrame:
    """Compare this audit to a prior snapshot at the URL level: what was decided
    then, what the page is doing now, and a plain-language verdict."""
    prior_rows = {r.get("url"): r for r in prior.get("decisions", []) if r.get("url")}
    if not prior_rows or decided.empty:
        return pd.DataFrame()
    cur = decided.set_index("url")
    out = []
    for url, pr in prior_rows.items():
        prior_action = pr.get("action")
        prior_clicks = float(pr.get("clicks_12mo") or 0)
        cur_clicks = float(cur.loc[url, "clicks_12mo"]) if url in cur.index and "clicks_12mo" in cur else 0.0
        cur_action = cur.loc[url, "action"] if url in cur.index else "(gone from inventory)"
        verdict = _grade_verdict(prior_action, prior_clicks, cur_clicks, url in cur.index)
        out.append({"url": url, "prior_action": prior_action, "prior_clicks": prior_clicks,
                    "current_clicks": cur_clicks, "current_action": cur_action, "verdict": verdict})
    return pd.DataFrame(out).sort_values("prior_clicks", ascending=False)


def _grade_verdict(prior_action, prior_clicks, cur_clicks, present) -> str:
    if prior_action in (R.DELETE_301, R.DELETE_410, R.NOINDEX):
        if not present:
            return "Removed as planned."
        if cur_clicks > 0:
            return f"⚠️ Marked for removal but now has {cur_clicks:g} clicks — re-check."
        return "Removal still looks safe (no clicks)."
    if prior_action in (R.KEEP, R.SCHEDULE_UPDATE):
        if prior_clicks and cur_clicks < prior_clicks * 0.5:
            return f"⚠️ Keeper declined {100 * (1 - cur_clicks / prior_clicks):.0f}% — consider refresh."
        if cur_clicks >= prior_clicks:
            return "Keeper held or grew."
        return "Keeper slightly down."
    if prior_action == R.REFRESH:
        if cur_clicks > prior_clicks:
            return f"Refresh paid off (+{cur_clicks - prior_clicks:g} clicks)."
        return "Refresh pending or not yet effective."
    return "—"


# --------------------------------------------------------------------------- #
# Executive summary (PDF)                                                      #
# --------------------------------------------------------------------------- #
def _simple_pdf(lines: list[str]) -> bytes:
    """Minimal dependency-free single-page PDF of text lines (Helvetica 12)."""
    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    wrapped: list[str] = []
    for ln in lines:
        ln = ln or " "
        while len(ln) > 95:
            wrapped.append(ln[:95])
            ln = ln[95:]
        wrapped.append(ln)
    body = ["BT", "/F1 12 Tf", "50 770 Td", "15 TL"]
    for ln in wrapped[:48]:  # one page; the Markdown summary is the full version
        body += [f"({esc(ln)}) Tj", "T*"]
    body.append("ET")
    stream = "\n".join(body)

    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        "/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
    ]
    out = "%PDF-1.4\n"
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out.encode("latin-1", "replace")))
        out += f"{i} 0 obj\n{o}\nendobj\n"
    xref_pos = len(out.encode("latin-1", "replace"))
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n"
    out += "".join(f"{off:010d} 00000 n \n" for off in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF"
    return out.encode("latin-1", "replace")


def executive_summary_pdf(decided: pd.DataFrame, config, guides_loaded: bool = False) -> Optional[bytes]:
    """Render the executive summary to a small text PDF (no external deps)."""
    counts = action_counts(decided)
    total = len(decided)
    scenario = config.get("scenario", "balanced") if hasattr(config, "get") else "balanced"
    lines = [
        "Content Audit - Executive Summary",
        f"Scenario: {scenario}  |  {total} URLs  |  est. index reduction "
        f"~{estimated_index_reduction(decided)}%",
        "",
        "Recommended actions:",
    ]
    for action in R.ACTIONS:
        n = counts.get(action, 0)
        if n:
            share = f"{100.0 * n / total:.0f}%" if total else "0%"
            lines.append(f"  - {action.replace('_', '-')}: {n} ({share})")
    lines += [
        "",
        "Timeline: allow 4-8 weeks for ranking impact to settle; deploy in batches",
        "and monitor (410s + noindex first, then 301s, then refresh/consolidation).",
    ]
    if not guides_loaded:
        lines += ["", "Note: reference guides not loaded - LLM rationale and timeline citations",
                  "are unverified against the source literature."]
    return _simple_pdf(lines)


# --------------------------------------------------------------------------- #
# 7. Executive summary                                                         #
# --------------------------------------------------------------------------- #
def estimated_index_reduction(decided: pd.DataFrame) -> float:
    if decided.empty:
        return 0.0
    indexable = decided["is_indexable"].fillna(True).astype(bool).sum() if "is_indexable" in decided else len(decided)
    if indexable == 0:
        return 0.0
    removed = decided["action"].isin([R.NOINDEX, R.DELETE_301, R.DELETE_410]).sum()
    return round(100.0 * removed / indexable, 1)


def executive_summary_md(decided: pd.DataFrame, config, guides_loaded: bool = False) -> str:
    counts = action_counts(decided)
    total = len(decided)
    reduction = estimated_index_reduction(decided)
    scenario = config.get("scenario", "balanced") if hasattr(config, "get") else "balanced"
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    lines = [
        "# Content Audit — Executive Summary",
        f"_Generated {date} · scenario: **{scenario}** · {total} URLs analysed_",
        "",
        "## Recommended actions",
        "",
        "| Action | URLs | Share |",
        "| --- | ---: | ---: |",
    ]
    label = {
        R.KEEP: "Keep", R.REFRESH: "Refresh", R.REPURPOSE: "Repurpose",
        R.CONSOLIDATE: "Consolidate", R.SCHEDULE_UPDATE: "Schedule update",
        R.NOINDEX: "Noindex", R.DELETE_301: "Delete (301)", R.DELETE_410: "Delete (410)",
        R.NO_ACTION: "No action", R.AMBIGUOUS: "Unresolved (review)",
    }
    for action in R.ACTIONS:
        n = counts.get(action, 0)
        if n:
            share = f"{100.0 * n / total:.0f}%" if total else "0%"
            lines.append(f"| {label.get(action, action)} | {n} | {share} |")

    lines += [
        "",
        f"**Estimated index reduction:** ~{reduction}% of indexable URLs "
        "(noindex + 301 + 410).",
    ]

    # Layer B insights, only when the underlying signals exist.
    if "needs_internal_links" in decided:
        orphans = int(decided["needs_internal_links"].fillna(False).astype(bool).sum())
        if orphans:
            lines.append(f"**Orphaned keepers:** {orphans} valuable page(s) have no internal "
                         "links in — add internal links so they can rank.")
    if "is_declining" in decided and decided["is_declining"].fillna(False).astype(bool).any():
        declining = int(decided["is_declining"].fillna(False).astype(bool).sum())
        lines.append(f"**Declining pages:** {declining} page(s) are trending down vs the "
                     "previous period and were prioritised for refresh.")
    if "evidence_score" in decided:
        low_ev = int((pd.to_numeric(decided["evidence_score"], errors="coerce") < 0.5).sum())
        if low_ev:
            lines.append(f"**Low-evidence decisions:** {low_ev} URL(s) were decided on data "
                         "from under half the sources — review before executing.")

    lines += [
        "",
        "## Expected timeline",
        "",
        "- **Ranking impact window:** allow **4–8 weeks** after deployment for "
        "consolidation/redirect effects to settle in search (per reference Doc 3).",
        "- **Rollout:** deploy in **batches and monitor** rather than all at once, so "
        "regressions are isolated and reversible (per reference Doc 4). Suggested order: "
        "410s and noindex first, then 301s, then refresh/consolidation work.",
        "",
        "## Deliverables in this pack",
        "- Decision Spreadsheet (with `manual_override` column)",
        "- Redirect Map (301/410) for the dev team",
        "- Refresh Queue (ROI-ranked) and Consolidation Plan",
        "- Repurpose Backlog and Snapshot JSON (for next audit's comparison)",
    ]
    if not guides_loaded:
        lines += [
            "",
            "> ⚠️ **Methodology note:** the 7 source guides were not loaded for this "
            "run, so LLM-judged rationales and the cited timeline windows are "
            "**unverified against the source literature**. See methodology.md.",
        ]
    return "\n".join(lines)
