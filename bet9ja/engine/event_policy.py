"""Exclude labelled simulations from booking and harvested candidate lists."""
from __future__ import annotations

import re
import unicodedata

# Match markers as tokens: ordinary names such as Zurich and Arsenal stay eligible.
SIMULATION = re.compile(r"(?<!\w)z\s*\.|\bsrl\b|\b(?:simulated|simulation|virtual)(?:s)?\b", re.I)


def simulation_reason(*labels: str) -> str | None:
    for label in labels:
        text = unicodedata.normalize("NFKC", label or "")
        match = SIMULATION.search(text)
        if match:
            return f"simulation marker {match.group(0)!r} in {label!r}"
    return None


def coupon_simulation_reason(coupon) -> str | None:
    for selection in coupon.selections:
        reason = simulation_reason(selection.event_name, selection.league, selection.sport)
        if reason:
            return reason
    return None
