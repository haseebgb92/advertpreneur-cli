"""
Focused tests for the ADP-Owned Browser Group and Action Cursor feature.

Spec:
- BrowserController creates/stores an isolated tab group (adp_group_id).
- Named-slot creation and child-tab capture join that group.
- A non-interactive black cursor overlay shows before each action and hides after.
- Active/user-tab fallback selection is replaced by group-bounded lookup.
- BrowserController closes the group only after a verified `completed` outcome.
- Extension syntax checks and package checks remain required.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from advertpreneur_cli.browser_control import BrowserController


# ---------------------------------------------------------------------------
# Helper: fake extension call factory
# ---------------------------------------------------------------------------

def _fake_extension(responses: dict):
    """Return a fake _extension_call that returns a canned response per action."""
    calls = []

    def fake_call(action, **kwargs):
        calls.append((action, kwargs))
        return responses.get(action, {"provider": "existing-edge/extension", "verified": True})

    return calls, fake_call


# ---------------------------------------------------------------------------
# 1. Group creation and slot membership
# ---------------------------------------------------------------------------

def test_group_create_calls_extension_with_current_slot_names(tmp_path: Path):
    """group_create() reads get_slots() and sends their keys to group_create action."""
    ctl = BrowserController(tmp_path)
    ctl._extension_available = lambda **_: True

    calls, fake = _fake_extension({
        "group_create": {"provider": "existing-edge/extension", "group_id": 42, "title": "Advertpreneur", "tab_count": 3, "verified": True},
    })
    ctl._extension_call = fake
    ctl.get_slots = lambda: {
        "access": {"url": "https://members.softzilla.net/member", "title": "Members"},
        "helium": {"url": "https://app.helium10.com", "title": "Helium"},
        "amazon": {"url": "https://amazon.com", "title": "Amazon"},
    }

    gid = ctl.group_create("Advertpreneur")

    assert gid == 42
    assert ctl._adp_group_id == 42
    action, kwargs = calls[0]
    assert action == "group_create"
    assert set(kwargs["tabs"]) == {"access", "helium", "amazon"}
    assert kwargs["title"] == "Advertpreneur"


def test_group_create_stores_returned_id_for_subsequent_add_and_close(tmp_path: Path):
    """The group ID returned by group_create is stored and reused by group_add_tab/group_close."""
    ctl = BrowserController(tmp_path)
    ctl._extension_available = lambda **_: True

    calls, fake = _fake_extension({
        "group_create": {"provider": "existing-edge/extension", "group_id": 99, "verified": True},
        "group_add_tab": {"provider": "existing-edge/extension", "added": True, "group_id": 99, "verified": True},
    })
    ctl._extension_call = fake
    ctl.get_slots = lambda: {"amazon": {"url": "https://amazon.com", "title": "Amazon"}}

    ctl.group_create()
    added = ctl.group_add_tab("helium")

    assert added is True
    assert calls[1][0] == "group_add_tab"
    assert calls[1][1]["group_id"] == 99
    assert calls[1][1]["tab"] == "helium"


# ---------------------------------------------------------------------------
# 2. Child-tab grouping
# ---------------------------------------------------------------------------

def test_group_add_tab_sends_captured_slot_into_existing_group(tmp_path: Path):
    """group_add_tab() sends a group_add_tab action with the stored group_id and slot name."""
    ctl = BrowserController(tmp_path)
    ctl._extension_available = lambda **_: True
    ctl._adp_group_id = 77

    calls, fake = _fake_extension({
        "group_add_tab": {"provider": "existing-edge/extension", "added": True, "group_id": 77, "verified": True},
    })
    ctl._extension_call = fake

    result = ctl.group_add_tab("helium")

    assert result is True
    assert calls[0] == ("group_add_tab", {"timeout": 8, "group_id": 77, "tab": "helium"})


def test_group_add_tab_returns_false_when_no_group_has_been_created(tmp_path: Path):
    """group_add_tab() is a no-op (returns False) when adp_group_id is 0."""
    ctl = BrowserController(tmp_path)
    ctl._extension_available = lambda **_: True
    # _adp_group_id defaults to 0

    calls, fake = _fake_extension({})
    ctl._extension_call = fake

    result = ctl.group_add_tab("helium")

    assert result is False
    assert calls == []  # no extension call made


# ---------------------------------------------------------------------------
# 3. Cursor show / hide around an action
# ---------------------------------------------------------------------------

def test_cursor_show_injects_overlay_at_specified_coordinates(tmp_path: Path):
    """cursor_show() calls extension cursor_show with x, y, and tab args."""
    ctl = BrowserController(tmp_path)
    ctl._extension_available = lambda **_: True

    calls, fake = _fake_extension({
        "cursor_show": {"provider": "existing-edge/extension", "cursor": "shown", "x": 480.0, "y": 320.0, "verified": True},
    })
    ctl._extension_call = fake

    result = ctl.cursor_show(480.0, 320.0, tab="amazon")

    assert "480" in result or "shown" in result
    assert calls[0][0] == "cursor_show"
    assert calls[0][1]["x"] == 480.0
    assert calls[0][1]["y"] == 320.0
    assert calls[0][1]["tab"] == "amazon"


def test_cursor_hide_removes_overlay_from_named_tab(tmp_path: Path):
    """cursor_hide() calls extension cursor_hide and returns a confirmation string."""
    ctl = BrowserController(tmp_path)
    ctl._extension_available = lambda **_: True

    calls, fake = _fake_extension({
        "cursor_hide": {"provider": "existing-edge/extension", "cursor": "hidden", "verified": True},
    })
    ctl._extension_call = fake

    result = ctl.cursor_hide(tab="amazon")

    assert "hidden" in result or "amazon" in result
    assert calls[0][0] == "cursor_hide"
    assert calls[0][1]["tab"] == "amazon"


def test_cursor_show_and_hide_are_no_ops_when_extension_unavailable(tmp_path: Path):
    """cursor_show/hide return empty strings and make no calls when extension is offline."""
    ctl = BrowserController(tmp_path)
    ctl._extension_available = lambda **_: False

    calls = []
    ctl._extension_call = lambda action, **kwargs: calls.append(action) or {}

    assert ctl.cursor_show(100, 200, tab="work") == ""
    assert ctl.cursor_hide(tab="work") == ""
    assert calls == []


# ---------------------------------------------------------------------------
# 4. No user-tab reuse: group_create does not close unnamed tabs
# ---------------------------------------------------------------------------

def test_group_create_does_not_repurpose_ungrouped_user_tabs(tmp_path: Path):
    """group_create only acts on ADP-named slots; it never touches tabs not in get_slots()."""
    ctl = BrowserController(tmp_path)
    ctl._extension_available = lambda **_: True

    calls, fake = _fake_extension({
        "group_create": {"provider": "existing-edge/extension", "group_id": 55, "verified": True},
    })
    ctl._extension_call = fake
    # Only the `amazon` slot is registered; no other tabs should be touched.
    ctl.get_slots = lambda: {"amazon": {"url": "https://amazon.com", "title": "Amazon"}}

    ctl.group_create()

    assert calls[0][0] == "group_create"
    # Extension only receives the named slot, not a wildcard or user-owned tab.
    assert calls[0][1]["tabs"] == ["amazon"]


# ---------------------------------------------------------------------------
# 5. Cleanup only after verified completed result
# ---------------------------------------------------------------------------

def test_group_close_closes_group_only_for_completed_outcome(tmp_path: Path):
    """group_close() calls extension group_close and clears adp_group_id only for 'completed'."""
    ctl = BrowserController(tmp_path)
    ctl._extension_available = lambda **_: True
    ctl._adp_group_id = 42

    calls, fake = _fake_extension({
        "group_close": {"provider": "existing-edge/extension", "closed": True, "group_id": 42, "verified": True},
    })
    ctl._extension_call = fake

    result = ctl.group_close(outcome="completed")

    assert "closed" in result or "42" in result
    assert ctl._adp_group_id == 0  # cleared after successful close
    assert calls[0][0] == "group_close"
    assert calls[0][1]["group_id"] == 42


def test_group_close_preserves_group_for_checkpoint_outcome(tmp_path: Path):
    """group_close() does NOT close the group for non-completed outcomes (checkpoint, error, pause)."""
    ctl = BrowserController(tmp_path)
    ctl._extension_available = lambda **_: True
    ctl._adp_group_id = 42

    calls = []
    ctl._extension_call = lambda action, **kwargs: calls.append(action)

    for outcome in ("paused", "error", "checkpoint", "", "login_required"):
        ctl._adp_group_id = 42
        result = ctl.group_close(outcome=outcome)
        assert "preserved" in result, f"Expected 'preserved' for outcome={outcome!r}"
        assert ctl._adp_group_id == 42, f"Group ID must not be cleared for outcome={outcome!r}"

    assert calls == []  # no extension close call for any non-completed outcome


def test_group_close_returns_safe_message_when_no_group_exists(tmp_path: Path):
    """group_close() is safe when called before group_create (adp_group_id==0)."""
    ctl = BrowserController(tmp_path)
    # _adp_group_id defaults to 0

    result = ctl.group_close(outcome="completed")
    assert "No ADP" in result or "no" in result.lower()


# ---------------------------------------------------------------------------
# 6. Extension syntax check (regression guard)
# ---------------------------------------------------------------------------

def test_browser_extension_background_contains_group_and_cursor_actions():
    """Both release copies of background.js must implement the group and cursor actions."""
    root = Path(__file__).parents[1]
    for source in (
        root / "browser-extension" / "background.js",
        root / "advertpreneur_cli" / "browser_extension" / "background.js",
    ):
        text = source.read_text(encoding="utf-8")
        assert "group_create" in text, f"{source.name}: missing group_create"
        assert "group_add_tab" in text, f"{source.name}: missing group_add_tab"
        assert "group_close" in text, f"{source.name}: missing group_close"
        assert "cursor_show" in text, f"{source.name}: missing cursor_show"
        assert "cursor_hide" in text, f"{source.name}: missing cursor_hide"
        assert "adp-action-cursor" in text, f"{source.name}: missing cursor element ID"
        assert "pointerEvents" in text, f"{source.name}: cursor must have pointerEvents:none"
