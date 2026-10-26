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

The note on the settings a routine scan does not re-apply came later, with
three more mutations, each run against the whole 2082-test suite before the
two tests at the end, all three survived: one of those settings left off the
list, the note given to Existing Subtitle Files as well, and the note never
appended. All three are killed here. 9 applied, 9 killed.
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


# ── Labels, and the Container Path pairs ─────────────────────────────────────
#
# Verified by mutation, 4 applied, 4 killed, each surviving the whole suite
# without these tests: the Movies label made the same as the TV one (killed
# by the uniqueness test), the TV example naming the wrong Sonarr path,
# Sonarr's placeholder back to /media, and the Movies example's Remuxarr
# file put in the TV folder (each killed by its service's example test).

def test_every_label_is_unique():
    """
    A label is also its control's accessible name (SettingInput uses it as
    the aria-label), so two settings sharing one are two controls a screen
    reader cannot tell apart. It came up with the Container Paths: worded
    alike, Sonarr's and Radarr's Remuxarr fields would both have been
    "Remuxarr's Container Path", on the same page.
    """
    labels = [field["label"] for field in SETTINGS_SCHEMA]
    repeated = sorted({label for label in labels if labels.count(label) > 1})

    assert not repeated, f"labels used by more than one setting: {repeated}"


# Each pair is one folder as the *arr's container names it (remote) and as
# Remuxarr's names it (local). The local field's description walks through an
# example; these read it back out.
EXAMPLE = re.compile(
    r"calls a file (?P<arr_file>/.+?) and Remuxarr calls it (?P<our_file>/.+?), "
    r"enter (?P<remote>/\S+) above and (?P<local>/\S+) here\.")


@pytest.mark.parametrize("service", ["sonarr", "radarr"])
def test_the_container_path_example_is_what_the_translation_does(service):
    """
    The example is the explanation, so it has to be right: entering the two
    paths it names turns its *arr file into its Remuxarr file and back,
    through the same function the webhooks and the rescan use, and the two
    paths it names are the fields' own placeholders, so the boxes and the
    sentence under them agree.

    The placeholders were /media and /media/tv, a folder and its own
    subfolder, which read as a typing slip rather than as two names for one
    folder. Two unrelated paths are pinned here too.
    """
    from app.core.pathmap import translate_path

    fields = {f["key"]: f for f in SETTINGS_SCHEMA}
    remote = fields[f"{service}_path_prefix_remote"]
    local = fields[f"{service}_path_prefix_local"]
    example = EXAMPLE.search(local["description"])
    assert example, f"{service}: no worked example in the description"

    assert (example["remote"], example["local"]) == (remote["placeholder"], local["placeholder"])
    assert translate_path(example["arr_file"], example["remote"], example["local"]) == example["our_file"]
    assert translate_path(example["our_file"], example["local"], example["remote"]) == example["arr_file"]

    a, b = remote["placeholder"], local["placeholder"]
    assert a != b and not b.startswith(a + "/") and not a.startswith(b + "/")


# ── Saying when a change reaches files already scanned ──────────────────────
#
# A routine scan skips a file whose size and mtime are unchanged, so a change
# to a setting the decision engine reads reaches only waiting jobs and files
# scanned afterwards; the rest keep their result until a Force Full Rescan.
# Reproduced against a real server: with Keep Audio Languages narrowed from
# English and French to English, a routine scan left a seen file's French
# track in place and only Force Full Rescan removed it. Nothing said so
# outside the Force Full Rescan card, so the settings that work this way say
# it themselves now.

DECISION_SOURCE = Path(__file__).resolve().parent.parent / "app" / "core" / "decision.py"

# Read by analyze_file, but it only names the subtitles a job extracts:
# changing it can make no unchanged file need work, so a rescan would apply
# nothing and the note would mislead.
NAMING_ONLY = {"existing_srt_handling"}


def _settings_the_decision_engine_reads():
    source = DECISION_SOURCE.read_text()
    return {entry["key"] for entry in SETTINGS_SCHEMA
            if re.search(r"""["']%s["']""" % re.escape(entry["key"]), source)}


def test_the_rescan_note_covers_every_setting_the_decision_engine_reads():
    """
    Derived from decision.py, so a new setting the engine reads fails here
    until it carries the note or is added to NAMING_ONLY with its reason.
    """
    from app.api.routes.settings import APPLIES_ON_RESCAN

    reads = _settings_the_decision_engine_reads()

    assert NAMING_ONLY <= reads, "NAMING_ONLY names a setting decision.py does not read"
    assert APPLIES_ON_RESCAN == reads - NAMING_ONLY


def test_each_of_those_settings_says_it():
    from app.api.routes.settings import APPLIES_ON_RESCAN, RESCAN_NOTE

    described = {entry["key"]: entry["description"] for entry in SETTINGS_SCHEMA}

    assert all(described[key].endswith(RESCAN_NOTE) for key in APPLIES_ON_RESCAN)
    assert all(RESCAN_NOTE not in described[key] for key in NAMING_ONLY)
