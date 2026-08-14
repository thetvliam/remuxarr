"""
Deployment configuration for the recycle volume.

These are cheap file assertions, and they exist for one expensive reason:
the app decides whether the recycle bin is configured by asking whether
its directory exists. That works only because nothing creates the
directory except the user's own bind mount.

Adding /recycle to the Dockerfile's VOLUME list would look like a tidy-up
and would quietly break it. Docker creates an anonymous volume for any
declared VOLUME the user did not map, so the directory would exist
whether or not anyone chose where it lives — the readiness check would
report ready, revert sidecars would go to storage nobody sized, monitors
or backs up, and they would look fine right up until the volume was
pruned. Nothing in the Python suite can catch that, because from inside
the container the directory simply exists.

The rest pin that the volume is actually documented in the three places
a user would look, since a feature that requires a mount nobody mentions
is a feature that silently never turns on.

Verified by mutation, 4 applied, 4 killed:

  • /recycle added to the Dockerfile VOLUME list  → killed
  • the recycle mount removed from docker-compose → killed
  • the Unraid template entry removed             → killed
  • the template entry marked Required="true"     → killed

The last one is not pedantry: Unraid's template UI blocks the container
from starting on a missing required path, so marking it required would
force the recycle bin on every existing user at their next template
refresh, including those who deliberately do not want it.
"""
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent


def _read(name):
    path = ROOT / name
    if not path.exists():
        pytest.skip(f"{name} not present in this checkout")
    return path.read_text()


# ── The trap ─────────────────────────────────────────────────────────────────

def test_recycle_is_not_a_declared_docker_volume():
    """
    See the module docstring. An anonymous volume here would make a
    missing mount indistinguishable from a configured one.
    """
    dockerfile = _read("Dockerfile")

    volume_lines = [ln for ln in dockerfile.splitlines()
                    if ln.strip().startswith("VOLUME")]
    assert volume_lines, "no VOLUME instruction found — did the Dockerfile change?"
    assert not any("recycle" in ln for ln in volume_lines), (
        "/recycle is declared as a VOLUME; Docker will create an anonymous "
        "volume for it and the app can no longer tell a missing mount from "
        "a configured one"
    )


# ── Documented where a user would look ───────────────────────────────────────

def test_compose_maps_the_recycle_volume():
    compose = _read("docker-compose.yml")
    assert ":/recycle" in compose


def test_unraid_template_offers_the_recycle_path():
    template = _read("templates/remuxarr.xml")
    root = ET.fromstring(template)

    entries = [c for c in root.findall("Config") if c.get("Target") == "/recycle"]
    assert entries, "the Unraid template has no /recycle path mapping"


def test_unraid_recycle_path_is_optional():
    """
    Unraid blocks startup on a missing required path. Marking this
    required would force the recycle bin on at the next template refresh
    for every existing user, including those who do not want it.
    """
    root = ET.fromstring(_read("templates/remuxarr.xml"))
    entry = next(c for c in root.findall("Config") if c.get("Target") == "/recycle")

    assert entry.get("Required") == "false"


def test_unraid_template_still_parses():
    """
    A malformed template is not rejected loudly by Unraid — it just fails
    to appear, which looks like the app not existing.
    """
    ET.fromstring(_read("templates/remuxarr.xml"))


def test_env_example_documents_the_recycle_dir():
    assert "REMUXARR_RECYCLE_DIR" in _read(".env.example")
