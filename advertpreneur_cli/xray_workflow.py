from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class XrayProgress:
    previous_rows: int = 0
    refreshed: bool = False


def next_xray_action(progress: XrayProgress, observed_rows: int, load_more_visible: bool) -> str:
    """Choose one bounded, evidence-based Xray action."""
    if observed_rows <= 0:
        return "refresh" if not progress.refreshed else "no_data"
    if load_more_visible and observed_rows > progress.previous_rows:
        return "load_more"
    if load_more_visible and not progress.refreshed:
        return "refresh"
    return "export" if not load_more_visible else "no_data"
