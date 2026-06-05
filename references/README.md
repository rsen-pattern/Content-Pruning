# Reference guides

The 7 source guides that inform the router thresholds and the ambiguous-judgment
prompt context. Loaded automatically by `utils/llm.py::load_guides()` (which
skips this README).

| File | Source / theme |
| --- | --- |
| `01_kondo_pruning.md` | The wardrobe principle; keep/consolidate/delete; 301-before-delete |
| `02_ahrefs_pruning.md` | How pruning works; risks; non-traffic value; batch + monitor |
| `03_conductor_pruning.md` | Ongoing maintenance; year-in-URL rule; severity buckets; noindex tags |
| `04_clearscope_pruning.md` | Drawbacks; high-value low-traffic pages; conversion value |
| `05_aioseo_pruning.md` | Per-URL judgment; 100-clicks review line; 8–15 sweet spot; 301-vs-410 |
| `06_recycle_workflow.md` | The refresh playbook (update / visuals / links / republish) |
| `07_repurpose_for_seo.md` | Format transformation & re-targeting (video, infographic, ebook, split) |

The router's rules and thresholds have been **verified against these guides**
(see `methodology.md` → "Verification against the references"). Replacing or
editing a guide changes the LLM judgment context on the next run, no code change
needed.
