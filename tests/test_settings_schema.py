"""
Every setting must be reachable, end to end.

A setting is only real if four things line up: it has a default, the API
will accept a write to it, the schema describes it, and some category in
the settings UI renders its group. Break any one and the setting still
"exists" — it is just unusable, silently, with nothing failing.

That is not hypothetical. The four Recycle Bin settings were added with
defaults and KNOWN_KEYS entries but no schema, and stayed invisible in the
UI for a dozen commits while every test passed. Nothing was broken; there
was simply no way to reach them.

These tests are derived from the data rather than listing keys by hand, so
a new setting joins them automatically. If one fails after adding a
setting, the fix is the missing piece it names, not a change here.

The frontend category list is parsed out of SettingsPage.jsx. Reading
another language's source with a regex is ugly and it is the only way to
check this boundary from the backend suite — the alternative is the exact
gap described above, which no amount of Python testing catches.

UNRENDERED_KEYS is the sanctioned exception, deliberately small: three
keys with bespoke UI in MaintenanceSection.jsx. Adding to it is a claim
that a setting is rendered somewhere else, and the test verifies that
claim rather than taking it.

Verified by mutation, 6 applied, 6 killed:

  • A schema key removed from KNOWN_KEYS         → killed
  • A schema key's default removed               → killed
  • A schema group removed from every category   → killed
  • A settings key given no schema entry         → killed
  • UNRENDERED_KEYS naming a key that has no
    bespoke UI either                            → killed
  • A retention limit given a minimum of 1       → killed

The third of those is the one worth noting: removing the Recycle Bin
category from SettingsPage.jsx reproduces exactly the state this feature
shipped in for a dozen commits, and it now fails.
"""
import re
from pathlib import Path

import pytest

from app.api.routes.settings import KNOWN_KEYS, SETTINGS_SCHEMA
from app.database.session import DEFAULT_APP_SETTINGS


FRONTEND = Path(__file__).resolve().parent.parent / "frontend" / "src"
SETTINGS_PAGE = FRONTEND / "components" / "settings" / "SettingsPage.jsx"

# Settings with no schema entry because they have bespoke UI instead.
# Every one is rendered by MaintenanceSection.jsx, which reads them
# directly — asserted below rather than trusted.
UNRENDERED_KEYS = {
    "auto_cleanup_on_scan",
    "scheduled_scan_enabled",
    "scheduled_scan_times",
}

SCHEMA_KEYS = {field["key"] for field in SETTINGS_SCHEMA}


def _rendered_groups():
    """Every group name some category in the settings UI renders."""
    if not SETTINGS_PAGE.exists():
        pytest.skip("frontend sources not present in this checkout")

    source = SETTINGS_PAGE.read_text()
    match = re.search(r"const CATEGORIES = \[(.*?)\n\];", source, re.S)
    assert match, "could not find CATEGORIES in SettingsPage.jsx"

    groups = set()
    for listing in re.findall(r"groups:\s*\[([^\]]*)\]", match.group(1)):
        groups.update(re.findall(r'"([^"]+)"', listing))
    assert groups, "parsed CATEGORIES but found no groups — parser is broken"
    return groups


# ── The four links in the chain ──────────────────────────────────────────────

def test_every_schema_key_is_writable():
    """
    A key absent from KNOWN_KEYS is rejected by PUT with a 400, so the UI
    renders the control and saving it fails.
    """
    assert not (SCHEMA_KEYS - KNOWN_KEYS)


def test_every_schema_key_has_a_default():
    """
    Without one the control renders empty and a user who never touches it
    saves a blank over whatever the code assumed.
    """
    assert not (SCHEMA_KEYS - set(DEFAULT_APP_SETTINGS))


def test_every_schema_group_is_rendered_by_a_category():
    """
    The gap that hid the Recycle Bin settings. A group no category lists
    is fetched by the page and drawn by nothing — the setting is present
    in the API, absent from the UI, and no test on either side notices.
    """
    groups = {field["group"] for field in SETTINGS_SCHEMA}
    unrendered = groups - _rendered_groups()

    assert not unrendered, (
        f"settings groups no category renders: {sorted(unrendered)} — add "
        f"them to CATEGORIES in SettingsPage.jsx or they are invisible"
    )


def test_every_setting_is_either_in_the_schema_or_deliberately_not():
    """
    The reverse direction. A setting with a default and no schema entry is
    invisible unless something renders it specially, and the exemption
    list is where that claim gets made explicitly.
    """
    missing = set(DEFAULT_APP_SETTINGS) - SCHEMA_KEYS - UNRENDERED_KEYS

    assert not missing, (
        f"settings with no schema entry and no bespoke UI: {sorted(missing)} "
        f"— add a SETTINGS_SCHEMA entry, or add to UNRENDERED_KEYS if "
        f"something else renders them"
    )


def test_the_exemptions_are_actually_rendered_somewhere():
    """
    Otherwise UNRENDERED_KEYS becomes a place to silence the test above by
    asserting the very thing it was supposed to check.
    """
    maintenance = FRONTEND / "components" / "settings" / "MaintenanceSection.jsx"
    if not maintenance.exists():
        pytest.skip("frontend sources not present in this checkout")

    source = maintenance.read_text()
    unrendered = [key for key in UNRENDERED_KEYS if key not in source]

    assert not unrendered, (
        f"exempted from the schema but not rendered by MaintenanceSection "
        f"either: {sorted(unrendered)}"
    )


def test_the_exemption_list_is_not_stale():
    """
    A key that gained a schema entry should leave the exemption list, or
    it quietly protects nothing while looking like it protects something.
    """
    stale = UNRENDERED_KEYS & SCHEMA_KEYS
    assert not stale, f"exempted keys that now have schema entries: {sorted(stale)}"


# ── The recycle bin settings specifically ────────────────────────────────────

def test_the_recycle_bin_settings_are_all_present():
    """
    Named explicitly, not because the derived tests above miss them, but
    because they were added in one commit and rendered in another — this
    is the test that would have failed in between.
    """
    expected = {"revert_enabled", "revert_retention_days",
                "revert_retention_max_gb", "revert_require_point"}

    assert expected <= SCHEMA_KEYS
    assert all(f["group"] == "Recycle Bin"
               for f in SETTINGS_SCHEMA if f["key"] in expected)


def test_the_retention_limits_accept_zero():
    """
    Zero means "no limit" for both, not "discard immediately". A min of 1
    would make the disable case unreachable from the UI.
    """
    limits = [f for f in SETTINGS_SCHEMA
              if f["key"] in ("revert_retention_days",
                              "revert_retention_max_gb")]

    assert len(limits) == 2
    for field in limits:
        assert field.get("min", 0) == 0, f"{field['key']} cannot be set to 0"
