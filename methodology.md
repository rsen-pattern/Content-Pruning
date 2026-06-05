# Methodology

The Content Audit Engine routes every URL to one of eight actions. Most URLs
route **deterministically**; Bi Frost (Pattern's LLM gateway) is reserved for
genuinely ambiguous URLs and for generating human-facing suggestions.

> ⚠️ **Verification status.** The 7 source reference guides were **not present**
> when this engine was built. The rule thresholds and the ambiguous-judgment
> context are therefore based on the build spec and general SEO best practice,
> **not yet verified against the source literature**. Drop the guides into
> `references/*.md` and re-review before treating the rationale as authoritative.

## Signals computed per URL

From the four exports (any subset; missing data degrades gracefully):

| Source | Signals |
| --- | --- |
| Screaming Frog | `status_code`, `is_indexable`, `word_count`, `internal_links_in`, `last_modified`, `title`, `h1`, `mime_type` |
| GSC | `clicks_12mo`, `impressions_12mo`, `avg_position`, `ctr`, `top_query` |
| GA4 | `sessions_12mo`, `engagement_rate`, `conversions_12mo`, `revenue_12mo`, `non_organic_sessions_12mo` |
| Ahrefs/Semrush | `referring_domains`, `backlinks` |
| Computed | `is_orphan`, `cannibalising_urls`, `has_year_in_url`, `days_since_modified`, `is_stale`, `is_html`, `expected_ctr` |
| LLM (optional) | `topical_cluster`, `intent` |

"Organic" means **organic search only** — GA4 "Organic Social" counts toward
`non_organic_sessions_12mo`.

## Rule order (first match wins)

1. **Already-deindexed** — not indexable & 0 clicks → `DELETE_301` if it has link
   equity, else `NO_ACTION`.
2. **Non-HTML path** — linked asset with an HTML twin → `DELETE_301`; linked with
   no twin → `KEEP`; unlinked & untrafficked → `DELETE_410`.
3. **Strong keep** — `clicks ≥ keep_threshold` & not stale.
4. **Refresh (stale earner)** — `clicks ≥ keep_threshold` & stale.
5. **Consolidate** — cannibalising siblings exist.
6. **Refresh (sweet-spot)** — position in `[low, high]` & `impressions ≥ floor`.
7. **Refresh (title/meta)** — top-10, `impressions ≥ ctr_min_impressions`, CTR below
   `baseline × ctr_underperform_ratio`.
8. **Schedule update** — dated URL / known obsolescence.
9. **Useful-but-unindexed** — 0 clicks & `non_organic > threshold` → `NOINDEX`
   (or `KEEP` if "preserve visibility" is on).
10. **Preserve link equity** — 0 clicks & `referring_domains ≥ 1` → `DELETE_301`.
11. **Hard delete** — 0 clicks, 0 referring domains, older than `delete_410_age_days` → `DELETE_410`.
12. **Thin-page catch** — 0 clicks, 0 referring domains, thin → `NOINDEX`.
13. **Everything else** → `AMBIGUOUS` → Bi Frost judgment.

`REPURPOSE` is **only** produced by LLM judgment (step 13); no deterministic rule
emits it.

## Router deviations from the build spec

These five intentional changes were approved before implementation:

1. **Non-organic before the 301.** The "useful-but-unindexed" check (step 9) runs
   *before* the link-equity 301 (step 10), so a page another channel actively
   lands on is never redirected away.
2. **CTR rule gains an impressions floor** (`ctr_min_impressions`, default 500) so
   low-impression noise is not flagged as a title/meta opportunity.
3. **Cannibalisation before refresh.** `CONSOLIDATE` (step 5) is evaluated before
   the sweet-spot/CTR refresh rules, so we never refresh one half of a split.
4. **No mislabelled KEEP.** The opening already-deindexed branch produces
   `NO_ACTION` (or `DELETE_301` when equity exists) rather than the valuable `KEEP`.
5. **Thin-page catch** (step 12) routes thin, link-less, click-less pages
   deterministically to `NOINDEX` instead of escalating thousands of them to the LLM.

## Safety & data-availability (Layer A)

The router never lets *missing* data read as *zero*:

- **Data-availability gating.** Each run records which sources were uploaded
  (`gsc / ga4 / backlinks / frog`). Traffic- and link-based **deletions** only
  fire when the source that justifies them is present. Upload only a crawl and
  no page is deleted on "no traffic" — those URLs escalate to AMBIGUOUS instead.
- **Conversion/revenue guardrail.** A page is never auto-**deleted** (301/410)
  when it has conversions ≥ `protect_conversions_floor` or revenue ≥
  `protect_revenue_floor`, even with zero organic clicks (paid-landing / email
  pages). NOINDEX is *not* guarded — it keeps the page live for email/direct,
  only removing it from search.
- **URL reconciliation.** Path-only exports (GA4's "Page path") are prefixed
  with the inferred site host before joining, so they don't silently fail to
  match an absolute-URL crawl. The Data Upload page shows a per-source
  **join-rate diagnostic**; a low rate flags a URL-format mismatch.
- **Manual override round-trip.** Edit the `manual_override` column in the
  Decision Spreadsheet and re-upload it on the Audit page; those decisions win
  over rules and LLM and survive deterministic re-runs.

## Configuration & provenance

Every threshold has a default (**grey**), can be detected from the data
(**blue**), or overridden by the user (**green**). Overrides always win over the
scenario preset. Scenarios shift deletion appetite:

- **conservative** — lower keep threshold, longer 410 age, easier non-organic keep.
- **balanced** — the documented defaults.
- **aggressive** — higher keep threshold, shorter 410 age.

## Bi Frost call sites

| # | Call | Batched? | Default model |
| --- | --- | --- | --- |
| 1 | Topical clustering | yes | `batch_model` (Haiku 4.5) |
| 2 | Intent classification | yes | `batch_model` |
| 3 | Ambiguous judgment | grouped (`ambiguous_batch_size`) | `judgment_model` (Sonnet 4.6) |
| 4 | Refresh recommendations | per-URL | `judgment_model` |
| 5 | Repurpose ideas | per-URL | `judgment_model` |

**Cost control.** Ambiguous judgment groups URLs per call and sends the large
static guide context once per batch (gateway-side prompt caching reuses it). The
Audit page shows a pre-flight estimate and refuses to run when it exceeds
`max_llm_cost_usd`. All calls go through `call_with_fallback`; a banner appears
when a model falls back.

## Each run stands alone

No database, no project persistence. The **Snapshot JSON** is the only cross-run
memory: download it now and upload it on a future audit to compare action counts
and grade what actually happened against what was decided.
