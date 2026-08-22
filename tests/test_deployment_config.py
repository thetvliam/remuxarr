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
is a feature that silently never turns on — and, since documenting a
variable is only half a promise, that the variable is genuinely read.

Verified by mutation, 6 applied, 6 killed:

  • /recycle added to the Dockerfile VOLUME list  → killed
  • the recycle mount removed from docker-compose → killed
  • the Unraid template entry removed             → killed
  • the template entry marked Required="true"     → killed
  • env_prefix removed from app/config.py         → killed
  • env_file removed from app/config.py           → killed

The template one is not pedantry: Unraid's template UI blocks the
container from starting on a missing required path, so marking it
required would force the recycle bin on every existing user at their next
template refresh, including those who deliberately do not want it.

The last two are recorded here because they survived the entire 1117-test
suite before these tests existed. Each is killed by its own assertion, not
by a collateral error: env_prefix by the two prefix tests (the value falls
back to its default, and a bare RECYCLE_DIR starts being honoured
instead), env_file by the dotenv test. The dotenv test also fails under
the env_prefix mutant, but with a pydantic extra_forbidden error rather
than its own assertion — it is the prefix tests that pin that one.
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


# ── The other half of that promise ───────────────────────────────────────────
#
# The test above proves REMUXARR_RECYCLE_DIR is documented. Nothing proved it
# was read. That gap is not theoretical: removing env_prefix from
# app/config.py passed all 1117 tests, because the one place the suite
# depends on the prefix (conftest.py's REMUXARR_DATABASE_PATH) falls back to
# the production default /config/remuxarr.db, and a runner that can write
# /config gets a green suite while every documented override silently does
# nothing. On a runner that cannot, it dies with a permission error — a
# failure that says nothing about the actual cause.
#
# These instantiate a fresh Settings rather than reading app.config.settings,
# and deliberately do NOT reload app.config: that rebinds `settings` to a new
# object while every module that already imported it keeps the old one, which
# breaks later tests in ways that look unrelated. A new instance reads the
# environment the same way without touching the shared one.

def test_prefixed_environment_variables_are_actually_read(tmp_path,
                                                          monkeypatch):
    """
    The container's whole configuration surface is REMUXARR_* variables.
    If the prefix stops being applied they are all ignored at once, in
    silence, and every setting reports its default as though the user had
    configured nothing.
    """
    from app.config import Settings

    # chdir first: a developer with a real .env in the repo root would
    # otherwise have its values folded in, and the assertion below would be
    # testing their machine rather than this code.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("REMUXARR_RECYCLE_DIR", "/probe/from-env")

    assert Settings().RECYCLE_DIR == "/probe/from-env"


def test_an_unprefixed_variable_is_not_read(tmp_path, monkeypatch):
    """
    The failure mode above has a second half worth pinning separately.
    Dropping env_prefix does not just ignore REMUXARR_RECYCLE_DIR — it
    starts honouring a bare RECYCLE_DIR instead, so an unrelated variable
    in the host environment silently becomes app configuration.
    """
    from app.config import Settings

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RECYCLE_DIR", "/probe/unprefixed")

    assert Settings().RECYCLE_DIR == "/recycle"


def test_the_dotenv_file_is_read(tmp_path, monkeypatch):
    """
    README's "Building from source" tells the user to `cp .env.example .env`
    and edit it. If env_file goes away that instruction becomes a no-op:
    the file is still there, still looks configured, and is never opened.
    """
    from app.config import Settings

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("REMUXARR_RECYCLE_DIR=/probe/from-dotenv\n")

    assert Settings().RECYCLE_DIR == "/probe/from-dotenv"


# ── Documentation ────────────────────────────────────────────────────────────

def test_the_readme_describes_reverting():
    """
    A feature that needs a volume mounted and is off by default is one
    nobody discovers on their own. The volume was documented from the
    start; the behaviour was not, for a dozen commits.
    """
    readme = _read("README.md")

    assert "## Reverting a processed file" in readme
    # The parts someone has to know before turning it on: what it costs,
    # and that it needs a mount.
    assert "/recycle" in readme
    assert "Settings → Recycle Bin" in readme


def test_readme_images_use_absolute_urls():
    """
    Relative paths work on GitHub and break everywhere else.

    The README is also the Docker Hub description, and Docker Hub renders
    it with no repository context — a relative src resolves against
    docker.com and 404s. The failure is invisible from inside the repo,
    because the same markup looks correct on GitHub, so nothing catches it
    until someone looks at the other page.

    Applies to href as much as src: a relative link on a screenshot is
    just as dead there.
    """
    import re

    readme = _read("README.md")
    table = readme[readme.index("<table>"):readme.index("</table>")]

    relative = [
        m.group(2) for m in re.finditer(r'(src|href)="([^"]+)"', table)
        if not m.group(2).startswith(("http://", "https://"))
    ]

    assert not relative, (
        f"relative URLs in the screenshot table: {relative} — these break "
        f"on Docker Hub, which renders this README without repo context"
    )


def test_the_readme_records_the_known_limitations():
    """
    Both are cases where a revert produces a valid file that differs from
    the original. Left undocumented, each looks like a bug to whoever
    hits it first.
    """
    readme = _read("README.md")

    assert "cover art" in readme.lower()
    assert "attachment" in readme.lower()


def test_the_documented_test_counts_are_current():
    """
    Both READMEs quote a test count, and both had drifted by more than
    two hundred before anyone looked — which makes every other number on
    the page worth less. Counting them here is cheap and the failure
    message says what to write.

    Deliberately tolerant: this fails when a number is stale by enough to
    mislead, not when a single test is added.
    """
    import re
    import subprocess

    root = Path(__file__).resolve().parent.parent
    collected = subprocess.run(
        ["python3", "-m", "pytest", "--collect-only", "-q",
         "-p", "no:cacheprovider"],
        cwd=root, capture_output=True, text=True,
    )
    match = re.search(r"(\d+) tests? collected", collected.stdout)
    if not match:
        pytest.skip("could not determine the collected test count")
    actual = int(match.group(1))

    for name in ("README.md", "tests/README.md"):
        text = _read(name)
        quoted = re.search(r"(\d[\d,]*) tests across", text)
        assert quoted, f"{name} no longer quotes a backend test count"
        claimed = int(quoted.group(1).replace(",", ""))
        assert abs(claimed - actual) <= 25, (
            f"{name} claims {claimed} backend tests; there are {actual}"
        )
