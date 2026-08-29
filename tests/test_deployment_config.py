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


def test_unraid_config_and_recycle_defaults_are_siblings():
    """
    The template defaulted /config to the appdata root while the
    deployment guide put it in an appdata/remuxarr/config subfolder, so
    the two Unraid-facing documents disagreed about the same install.
    With the template's value /recycle landed inside /config, which puts
    the recycle bin's 20GB ceiling inside the directory CA Appdata Backup
    takes wholesale.

    Both are now subfolders and neither contains the other. The nesting
    check is the part worth keeping: pointing /recycle back inside
    /config would work perfectly, break nothing, and pass every other
    test here, which is exactly why it needs saying out loud.

    Compares the template against the guide rather than against a literal
    so the two cannot drift apart again silently - the failure mode this
    started as.
    """
    import re

    root = ET.fromstring(_read("templates/remuxarr.xml"))

    def default_for(target):
        entry = next(
            (c for c in root.findall("Config") if c.get("Target") == target), None
        )
        assert entry is not None, f"the Unraid template has no {target} mapping"
        value = (entry.get("Default") or "").rstrip("/")
        assert value, f"the Unraid template has no default host path for {target}"
        return value

    config = default_for("/config")
    recycle = default_for("/recycle")

    assert config != recycle, "/config and /recycle default to the same host path"
    assert not recycle.startswith(config + "/"), (
        f"the template nests /recycle ({recycle}) inside /config ({config}). "
        f"The recycle bin is bounded at 20GB by default, and under /config it "
        f"lands inside the appdata directory backup plugins take whole."
    )
    assert not config.startswith(recycle + "/"), (
        f"the template nests /config ({config}) inside /recycle ({recycle})"
    )

    guide = _read("UNRAID_DEPLOYMENT.md")
    for target, value in (("/config", config), ("/recycle", recycle)):
        # Boundary-anchored rather than a substring test: plain `in` lets a
        # truncated path pass by matching inside the longer correct one, so
        # a template saying .../conf satisfied a guide saying .../config.
        # Found by mutation; the substring form survived it.
        assert re.search(re.escape(value) + r"(?![\w/-])", guide), (
            f"the template defaults {target} to {value}, which does not appear "
            f"in UNRAID_DEPLOYMENT.md. Both describe the same install to the "
            f"same person, so they have to agree."
        )


def test_unraid_template_offers_a_timezone_variable():
    """
    Unraid does not pass TZ to containers - its own Date & Time setting
    governs the host - so a template without this field leaves every
    Community Apps install on UTC with no way to change it short of
    adding the variable by hand.

    TZ decides when scheduled scans and the Plex analyze window fire,
    while displayed timestamps are stored in UTC and converted by the
    browser. So an unset zone produces no visible symptom: every clock in
    the UI reads correctly and the only evidence is a scan starting at
    the wrong hour. That is why the field has to exist rather than being
    left to the docs.

    Asserts optional-and-visible rather than checking the default. An
    empty default is deliberate, since seeding a city would be
    confidently wrong for most people, but it is a judgement that could
    reasonably be revisited; the field existing at all is not.
    """
    root = ET.fromstring(_read("templates/remuxarr.xml"))

    entry = next(
        (c for c in root.findall("Config") if c.get("Target") == "TZ"), None
    )
    assert entry is not None, (
        "the Unraid template declares no TZ variable, so a Community Apps "
        "install runs on UTC and scheduled scans fire at the wrong hour"
    )
    assert entry.get("Type") == "Variable", (
        f"TZ is declared as {entry.get('Type')!r}, not a Variable"
    )
    assert entry.get("Required") == "false", (
        "TZ is marked required, which blocks startup on a field that is "
        "legitimately empty - an unset zone is a working container on UTC"
    )
    assert entry.get("Display") == "always", (
        "TZ is hidden behind Unraid's Advanced view, where the people who "
        "most need it will not find it"
    )

    guide = _read("UNRAID_DEPLOYMENT.md")
    assert "TZ" in guide, (
        "the template offers a TZ variable that UNRAID_DEPLOYMENT.md never "
        "mentions; both describe the same install to the same person"
    )


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

    Deliberately tolerant on the test count: this fails when a number is
    stale by enough to mislead, not when a single test is added.

    Exact on the file count, and the difference is the point. That number
    drifted from 54 to 56 without two modules being added, because 56 is
    what you get counting .py files under tests/ — conftest.py and
    sample_library/parse_ffprobe_dump.py included, neither of which holds
    a test. Both readings were defensible while neither was written down.
    A tolerance of even two would have let exactly that drift through, so
    there is none: the count is the modules pytest collects, and adding a
    test file is a large enough event to update a number for.
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

    modules = {
        line.split("::", 1)[0]
        for line in collected.stdout.splitlines()
        if "::" in line and line.startswith("tests/")
    }
    if not modules:
        pytest.skip("could not determine the collected module list")
    actual_files = len(modules)

    for name in ("README.md", "tests/README.md"):
        text = _read(name)
        quoted = re.search(r"(\d[\d,]*) tests across", text)
        assert quoted, f"{name} no longer quotes a backend test count"
        claimed = int(quoted.group(1).replace(",", ""))
        assert abs(claimed - actual) <= 25, (
            f"{name} claims {claimed} backend tests; there are {actual}"
        )

        quoted_files = re.search(r"tests across ([\d,]+) (?:test )?files", text)
        assert quoted_files, (
            f"{name} no longer quotes a backend test file count"
        )
        claimed_files = int(quoted_files.group(1).replace(",", ""))
        assert claimed_files == actual_files, (
            f"{name} claims {claimed_files} test files; pytest collects "
            f"{actual_files}. Count collected modules, not .py files under "
            f"tests/ — conftest.py and sample_library/parse_ffprobe_dump.py "
            f"hold no tests and are not counted."
        )


def test_the_documented_install_commands_include_the_app_dependencies():
    """
    Both READMEs tell a newcomer how to install before running the suite,
    and both named only tests/requirements-test.txt. That file holds
    pytest, pytest-cov and the TestClient HTTP backend — no fastapi, no
    sqlalchemy, no pydantic-settings — so following the instructions
    verbatim in a clean environment dies at collection on 29 of the 54
    modules before a single test runs.

    It survived precisely because nobody who could have noticed ever runs
    it: anyone with a working checkout already has the app dependencies
    installed, so the short form appears to work for every person in a
    position to spot that it does not.

    Asserted against every pip line in the file rather than one known
    snippet, so the two-command form (requirements.txt on its own line)
    passes too. Option B installs only the test extras on purpose — it
    runs inside the container, where requirements.txt was installed at
    build time — which is why this looks for one satisfying line rather
    than requiring every line to qualify.
    """
    for name in ("README.md", "tests/README.md"):
        installs = [
            line for line in _read(name).splitlines() if "pip install" in line
        ]
        assert installs, f"{name} no longer documents an install command"
        assert any("-r requirements.txt" in line for line in installs), (
            f"{name} documents installing only the test extras. Without "
            f"requirements.txt there is no fastapi, sqlalchemy or "
            f"pydantic-settings, so pytest cannot import app/ and the run "
            f"ends at collection."
        )


def test_ci_and_the_image_build_the_frontend_on_the_same_node_major():
    """
    The image moved to node:24-slim when Node 20 went end-of-life, and the
    CI job stayed on 20, so for a while the frontend suite was verified on
    one major and shipped on another. Nothing caught it because nothing
    compared the two files.

    That gap is worse than it sounds: a Node-major behaviour difference
    shows up as a passing CI run and a broken image, which is the failure
    mode with the longest feedback loop available - it reaches users
    before it reaches anyone who could fix it.

    Compares only the major. The Dockerfile pins a tag (node:24-slim) and
    setup-node takes a major ("24") that resolves to whatever is current
    at run time, so the patch versions legitimately differ and asserting
    on them would fail constantly for no reason.
    """
    import re

    dockerfile = _read("Dockerfile")
    from_line = re.search(r"FROM node:(\d+)[.\-]", dockerfile)
    assert from_line, "Dockerfile no longer pins a node: base image for the UI build"
    image_major = from_line.group(1)

    workflow = _read(".github/workflows/ci.yml")
    pinned = re.search(r"node-version:\s*[\"']?(\d+)", workflow)
    assert pinned, "ci.yml no longer pins a node-version"
    ci_major = pinned.group(1)

    assert image_major == ci_major, (
        f"the image builds the frontend on Node {image_major} and CI tests "
        f"it on Node {ci_major}. Bring them together, or the suite is not "
        f"testing what ships."
    )


def test_the_release_notes_file_is_shipped_in_the_image():
    """
    The route resolves RELEASE_NOTES.md from its own __file__, four
    directories up, which is /app in the container. The Dockerfile copies
    app/ and the built UI and nothing else from the repo root, so without
    an explicit COPY the file is simply absent.

    The endpoint handles that by reporting no notes — deliberately, so a
    source checkout without the file still runs. Which means the whole
    feature would fail in exactly the way it exists to prevent: silently,
    with users told nothing, and nothing in the logs to say why.
    """
    assert "COPY RELEASE_NOTES.md" in _read("Dockerfile")
