# Content Audit Engine

A Streamlit app for SEO content auditing. It takes a website's URL inventory plus
performance, link and crawl data and produces a per-URL recommendation across
**8 actions** — keep · refresh · repurpose · consolidate · schedule-update ·
noindex · delete-301 · delete-410 — plus the deliverables a content team needs to
execute them.

A Pattern tool, mirroring the conventions of `rsen-pattern/SEO-Forecast`:
deterministic-first routing, an assumptions panel with `defaulted / detected /
overridden` provenance, a snapshot-JSON-per-run comparison pattern, and all LLM
calls through **Bi Frost** (`call_with_fallback`, models from `config/models.json`).

## Quickstart

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```

Then, in the app:

1. **Data Upload** — drop in Screaming Frog / GSC / GA4 / Ahrefs exports (any
   subset; toggle "Use bundled sample exports" to try it immediately).
2. **Configuration** — pick a scenario, tune thresholds, choose models.
3. **Audit** — run the deterministic router; optionally enrich with Bi Frost.
4. **Consolidation Planner / Refresh Suggestions** — review and generate suggestions.
5. **Deliverables** — download the 7 outputs.

### Bi Frost key

The key resolves with this precedence (the sidebar shows which source is active):

1. **Sidebar input** — pasted in the password field (persists across pages).
2. **`st.secrets`** — `.streamlit/secrets.toml` (see `.streamlit/secrets.toml.example`)
   or Streamlit Cloud secrets.
3. **Environment** — `BIFROST_API_KEY` or the legacy `BIFROST_KEY`.

```toml
# .streamlit/secrets.toml   (gitignored)
BIFROST_API_KEY = "your-bifrost-key-here"
```

Without a key the deterministic audit still runs fully — only the LLM steps are
disabled. The key is never logged.

## Deliverables

1. Decision Spreadsheet (XLSX, with editable `manual_override` column)
2. Redirect Map (CSV: `source_url, destination_url, status_code`)
3. Refresh Queue (XLSX, ROI-ranked, with LLM update suggestions)
4. Consolidation Plan (XLSX: clusters, winners, redirects, merge notes)
5. Repurpose Backlog (XLSX)
6. Snapshot JSON (for next audit's comparison)
7. Executive Summary (Markdown + PDF)
8. Action Plan (XLSX, priority-ranked across all actions)

The Audit page shows an action-distribution chart and a priority-ranked table;
uploading a prior snapshot on Deliverables produces **per-URL grading** (did the
keepers hold? were any deletions premature?).

## Layout

```
streamlit_app.py        # home / overview
pages/                  # 1 Methodology … 7 Deliverables
config/models.json      # Bi Frost model catalogue + fallback chain
prompts/*.txt           # the 5 LLM prompts (data, not inline strings)
utils/
  bifrost.py            # Bi Frost client + call_with_fallback + cost helpers
  prompts.py            # prompt loader
  loaders.py            # parse the 4 exports (graceful degradation)
  signals.py            # per-URL signal computation (pure)
  router.py             # the deterministic rules engine (pure)
  llm.py                # the 5 Bi Frost call sites + content fetch + cost pre-flight
  exporters.py          # build the 7 deliverables
  ctr_curves.py         # industry CTR-by-position baseline
  config.py             # defaults, scenarios, provenance
  ui.py                 # shared Streamlit helpers
references/             # the 7 source guides (NOT yet present — see methodology.md)
samples/                # sample exports for first-run testing
tests/test_pipeline.py  # end-to-end loaders→signals→router check
```

## Status & caveats

- **Reference guides loaded.** The 7 source guides are in `references/`; the
  router's rules have been verified against them and the LLM judgment context is
  grounded in them (see `methodology.md` → "Verification against the references").
- Router order **deviates from the original spec in 7 documented ways**
  (5 original + a literature-driven maturity guard and a 301-over-410 preference;
  see `methodology.md` → "Router deviations").
- The tool **recommends**; it does not edit pages or deploy redirects.

## Tests

```bash
python -m tests.test_pipeline   # loaders -> signals -> router (14 URLs)
python -m tests.test_safety     # availability gating, delete guardrails, URL join
python -m tests.test_analytical # intent staleness, decline, evidence, orphan flag
python -m tests.test_llm        # JSON parsing, action normalisation, Pydantic validation
```

CI (`.github/workflows/tests.yml`) byte-compiles every module and runs all three
suites on push/PR.

## Re-using LLM work across runs

The Snapshot JSON (Deliverables) stores the LLM judgments, manual overrides and
cluster/intent assignments. On the Audit page, **Restore LLM state from a
snapshot** re-applies them so you don't re-pay for the same Bi Frost calls.
