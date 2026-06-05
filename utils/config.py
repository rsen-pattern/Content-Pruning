"""Audit configuration: defaults, scenario presets, and provenance tracking.

Mirrors the SEO-Forecast assumptions panel: every threshold has a default,
can be detected from data where possible, and can be overridden. Each value
carries provenance for the grey/blue/green display:

    defaulted   -> grey   (we picked it)
    detected    -> blue   (derived from the uploaded data)
    overridden  -> green  (the user set it)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

PROVENANCE_COLOURS = {
    "defaulted": "#9e9e9e",   # grey
    "detected": "#1e88e5",    # blue
    "overridden": "#2e7d32",  # green
}

# Canonical thresholds (balanced scenario baseline).
DEFAULTS: dict[str, Any] = {
    "keep_threshold": 100,            # clicks/yr to auto-keep
    "stale_threshold_days": 365,
    "sweet_spot_position_low": 8,
    "sweet_spot_position_high": 20,
    "sweet_spot_imp_threshold": 500,
    "ctr_underperform_ratio": 0.6,
    "ctr_min_impressions": 500,       # NEW: floor so low-impression noise is not flagged
    "non_organic_threshold": 50,
    "delete_410_age_days": 730,
    "thin_word_count": 250,           # NEW: thin-page catch
    "llm_judgment_for": "ambiguous_only",  # ambiguous_only | all | none
    "batch_model": "anthropic/claude-haiku-4-5",
    "judgment_model": "anthropic/claude-sonnet-4-6",
    "scenario": "balanced",           # conservative | balanced | aggressive
    "preserve_non_organic_as_keep": False,  # NOINDEX vs KEEP for useful-but-unindexed
    "protect_conversions": True,      # never auto-delete a converting / revenue page
    "protect_conversions_floor": 1,   # conversions >= this => protected
    "protect_revenue_floor": 1.0,     # revenue >= this => protected
    # Layer B — intent modulates how fast content is considered stale (multiplier
    # on stale_threshold_days). Transactional/commercial decay faster.
    "intent_stale_factors": {"transactional": 0.5, "commercial": 0.7,
                             "informational": 1.0, "navigational": 1.5},
    "trend_decline_pct": -0.2,        # clicks change <= this (e.g. -20%) => declining
    "ambiguous_batch_size": 5,
    "max_llm_cost_usd": 5.0,          # hard pre-flight cap
}

# Scenario presets shift the *deletion appetite*. Conservative deletes fewer
# and refreshes more; aggressive deletes more. Applied on top of DEFAULTS;
# an explicit user override always wins over the scenario value.
SCENARIOS: dict[str, dict[str, Any]] = {
    "conservative": {
        "keep_threshold": 50,         # easier to keep
        "delete_410_age_days": 1095,  # must be very old to hard-delete
        "non_organic_threshold": 25,  # easier to justify keep/noindex
        "preserve_non_organic_as_keep": True,
    },
    "balanced": {},                   # = DEFAULTS
    "aggressive": {
        "keep_threshold": 200,        # harder to keep
        "delete_410_age_days": 365,   # delete sooner
        "non_organic_threshold": 100, # harder to justify keep on non-organic
    },
}


@dataclass
class Config:
    """Resolved config plus per-key provenance."""

    values: dict[str, Any] = field(default_factory=lambda: dict(DEFAULTS))
    provenance: dict[str, str] = field(
        default_factory=lambda: {k: "defaulted" for k in DEFAULTS}
    )

    def __getitem__(self, key: str) -> Any:
        return self.values[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def apply_scenario(self, scenario: str) -> None:
        """Apply a scenario preset. Does not stomp on user overrides."""
        if scenario not in SCENARIOS:
            return
        self.values["scenario"] = scenario
        for key, val in SCENARIOS[scenario].items():
            if self.provenance.get(key) == "overridden":
                continue
            self.values[key] = val
            self.provenance[key] = "defaulted" if scenario == "balanced" else "detected"

    def detect(self, key: str, value: Any) -> None:
        """Record a data-derived value (blue) unless the user overrode it."""
        if self.provenance.get(key) == "overridden":
            return
        self.values[key] = value
        self.provenance[key] = "detected"

    def override(self, key: str, value: Any) -> None:
        """Record a user override (green) — wins over everything."""
        self.values[key] = value
        self.provenance[key] = "overridden"

    def as_dict(self) -> dict[str, Any]:
        return {"values": dict(self.values), "provenance": dict(self.provenance)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Config":
        cfg = cls(values=dict(DEFAULTS), provenance={k: "defaulted" for k in DEFAULTS})
        cfg.values.update(data.get("values", {}))
        cfg.provenance.update(data.get("provenance", {}))
        return cfg
