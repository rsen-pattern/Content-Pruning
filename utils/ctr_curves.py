"""Industry CTR-by-position baseline.

Preset values approximate the Advanced Web Ranking 2025 organic CTR curve
(aggregate across industries). These are a *baseline* for spotting
title/meta underperformance, not a guarantee — they are overridable on the
Configuration page (preset vs. custom upload), mirroring the assumptions
provenance pattern.

The figures below are documented as DEFAULTED provenance. If a real AWR
export is uploaded, the loaded curve becomes DETECTED/OVERRIDDEN.
"""
from __future__ import annotations

# position -> expected organic CTR (fraction, not %)
PRESET_AWR_2025: dict[int, float] = {
    1: 0.398,
    2: 0.187,
    3: 0.103,
    4: 0.069,
    5: 0.050,
    6: 0.038,
    7: 0.030,
    8: 0.024,
    9: 0.020,
    10: 0.017,
}

# Positions beyond the first page collapse toward this floor.
_TAIL_CTR = 0.010


def expected_ctr(position: float, curve: dict[int, float] | None = None) -> float:
    """Expected CTR for a (possibly fractional) average position."""
    c = curve or PRESET_AWR_2025
    if position is None or position <= 0:
        return _TAIL_CTR
    p = int(round(position))
    if p <= 0:
        p = 1
    return c.get(p, _TAIL_CTR)


def parse_custom_curve(rows: list[tuple[int, float]]) -> dict[int, float]:
    """Build a curve dict from (position, ctr) rows; CTR accepted as % or fraction."""
    curve: dict[int, float] = {}
    for pos, ctr in rows:
        ctr = float(ctr)
        if ctr > 1.0:  # given as a percentage
            ctr /= 100.0
        curve[int(pos)] = ctr
    return curve
