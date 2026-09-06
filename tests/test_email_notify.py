"""
email_notify.py — the SMTP send path for failure notifications.

The largest gap left in the backend suite: 47 of this module's 56
statements were uncovered, which is everything below the imports. The
deciding half of the feature — whether an email is owed at all, and which
of the two kinds it is — lives in worker._load_email_notify_data and is
already pinned by test_assorted_regressions.py. Nothing exercised the half
that talks to a server.

What can be got wrong here is silent by construction. Both senders catch
Exception and log, deliberately, so that a bad SMTP config can never turn a
successful remux into a failed job. That is the right trade, but it leaves
the module with no user-visible failure signal: a defect in it produces
missing email, and missing email is indistinguishable from having had no
failures worth reporting. test_email_connection is the sole exception,
existing so the user can ask directly, which makes its result dict the only
thing standing between a broken configuration and silence.

The mutation that motivated the file: replacing `if encryption ==
"starttls"` with `if False`, so the connection is never upgraded and the
login below it hands the password to the server in the clear, left all 1176
tests passing. Encryption selection, credential handling and the
connect-then-clean-up ordering are pinned here for that reason.

NAMING TRAP: this file imports the module and calls through it rather than
importing test_email_connection by name. A from-import binds a callable
named test_* into this namespace, where pytest collects it as a test, sees
its `cfg` parameter and fails the run with "fixture 'cfg' not found" — an
error attributed to a file whose own tests all pass. Confirmed by running
it that way before this line was written.

MIME TRAP: by the time a message reaches sendmail its payload is base64,
because MIMEText(..., "utf-8") selects a base64 Content-Transfer-Encoding.
A substring assertion against the raw message therefore cannot match any
body text, so _body() re-parses and decodes instead. Also confirmed rather
than assumed.

Verified by mutation: 25 mutations applied to email_notify.py, all 25
killed. No survivors, so nothing is recorded here as equivalent.
"""
import smtplib
from email import message_from_string

import pytest

import app.core.email_notify as email_notify


# A complete, valid configuration in the shape get_app_settings actually
# returns: recipients is a list and the port an int, both per
# DEFAULT_APP_SETTINGS. Individual tests override only the key under test.
BASE_CFG = {
    "email_smtp_host":  "smtp.example.com",
    "email_smtp_port":  2525,
    "email_encryption": "starttls",
    "email_username":   "postmaster@example.com",
    "email_password":   "hunter2",
    "email_from":       "remuxarr@example.com",
    "email_recipients": ["ops@example.com"],
}


# ── Harness ──────────────────────────────────────────────────────────────────

@pytest.fixture
def smtp(monkeypatch):
    """
    Replace both SMTP classes on smtplib itself. email_notify does a plain
    `import smtplib` and resolves the attribute at call time, so patching
    the attributes is enough and the fixture needs no import-order care.

    Connections and events are recorded separately because several tests
    here are about order — STARTTLS before login, quit after a failed
    send — rather than about any one call's arguments.
    """
    state = {
        "connections":     [],
        "events":          [],
        "sendmail_raises": None,
        "quit_raises":     None,
    }

    def _fake(kind):
        class _Fake:
            def __init__(self, host, port, timeout=None):
                state["connections"].append(
                    {"kind": kind, "host": host, "port": port,
                     "timeout": timeout}
                )

            def starttls(self):
                state["events"].append(("starttls",))

            def login(self, username, password):
                state["events"].append(("login", username, password))

            def sendmail(self, from_addr, recipients, message):
                state["events"].append(
                    ("sendmail", from_addr, list(recipients), message)
                )
                if state["sendmail_raises"] is not None:
                    raise state["sendmail_raises"]

            def quit(self):
                state["events"].append(("quit",))
                if state["quit_raises"] is not None:
                    raise state["quit_raises"]

        return _Fake

    monkeypatch.setattr(smtplib, "SMTP", _fake("plain"))
    monkeypatch.setattr(smtplib, "SMTP_SSL", _fake("ssl"))
    return state


def _kinds(state):
    return [event[0] for event in state["events"]]


def _sendmail(state):
    for event in state["events"]:
        if event[0] == "sendmail":
            return event
    raise AssertionError(f"nothing was sent; events were {_kinds(state)}")


def _headers(state):
    return message_from_string(_sendmail(state)[3])


def _body(state):
    return _headers(state).get_payload(decode=True).decode("utf-8")


# ── Encryption and connection ────────────────────────────────────────────────

def test_starttls_is_issued_before_the_credentials(smtp):
    """
    The whole sequence rather than a membership check: an upgrade that
    happens after login has already leaked the password, so where STARTTLS
    sits is the point, not that it appears somewhere.
    """
    email_notify._send(BASE_CFG, "subject", "body")

    assert _kinds(smtp) == ["starttls", "login", "sendmail", "quit"]


def test_ssl_wraps_the_connection_instead_of_upgrading_it(smtp):
    email_notify._send(BASE_CFG | {"email_encryption": "ssl"}, "s", "b")

    assert smtp["connections"][0]["kind"] == "ssl"
    assert "starttls" not in _kinds(smtp)


def test_encryption_none_neither_wraps_nor_upgrades(smtp):
    email_notify._send(BASE_CFG | {"email_encryption": "none"}, "s", "b")

    assert smtp["connections"][0]["kind"] == "plain"
    assert "starttls" not in _kinds(smtp)


def test_encryption_defaults_to_starttls_when_the_key_is_absent(smtp):
    """
    The stored setting always exists, so this default only fires for a cfg
    assembled elsewhere — and the safe direction for it to fail is the one
    that encrypts.
    """
    cfg = {k: v for k, v in BASE_CFG.items() if k != "email_encryption"}

    email_notify._send(cfg, "s", "b")

    assert "starttls" in _kinds(smtp)


def test_the_port_falls_back_to_587_when_unset_or_empty(smtp):
    """
    Two cases, because `cfg.get(...) or 587` and `cfg.get(..., 587)` differ
    only on a stored empty value, which is what a cleared number field in
    the settings form produces.
    """
    cfg = {k: v for k, v in BASE_CFG.items() if k != "email_smtp_port"}
    email_notify._send(cfg, "s", "b")
    email_notify._send(BASE_CFG | {"email_smtp_port": ""}, "s", "b")

    assert [c["port"] for c in smtp["connections"]] == [587, 587]


def test_the_connection_carries_a_timeout(smtp):
    """
    The send runs on an executor thread from the worker. Without a timeout
    a server that accepts the socket and then says nothing holds that
    thread for as long as the process lives.
    """
    email_notify._send(BASE_CFG, "s", "b")

    assert smtp["connections"][0]["timeout"] == 15


def test_the_host_and_port_are_the_configured_ones(smtp):
    email_notify._send(BASE_CFG, "s", "b")

    assert smtp["connections"][0]["host"] == "smtp.example.com"
    assert smtp["connections"][0]["port"] == 2525


# ── Credentials ──────────────────────────────────────────────────────────────

def test_the_configured_credentials_are_used(smtp):
    email_notify._send(BASE_CFG, "s", "b")

    assert ("login", "postmaster@example.com", "hunter2") in smtp["events"]


def test_login_is_skipped_when_no_username_is_configured(smtp):
    """
    An unauthenticated relay is a supported configuration. Calling login
    with an empty username against one is rejected outright, so the guard
    is what makes that setup work at all.
    """
    email_notify._send(
        BASE_CFG | {"email_username": "", "email_from": "r@example.com"},
        "s", "b",
    )

    assert "login" not in _kinds(smtp)
    assert "sendmail" in _kinds(smtp)


# ── Addresses ────────────────────────────────────────────────────────────────

def test_the_from_address_falls_back_to_the_username(smtp):
    email_notify._send(BASE_CFG | {"email_from": ""}, "s", "b")

    assert _sendmail(smtp)[1] == "postmaster@example.com"


def test_blank_recipients_are_dropped_and_the_rest_stripped(smtp):
    """
    The recipients field is edited as free text, so a trailing separator
    leaves an empty entry behind. An unstripped address is rejected by some
    servers and silently accepted-then-dropped by others.
    """
    email_notify._send(
        BASE_CFG | {"email_recipients":
                    ["  a@example.com  ", "", "   ", "b@example.com"]},
        "s", "b",
    )

    assert _sendmail(smtp)[2] == ["a@example.com", "b@example.com"]


def test_every_recipient_is_listed_in_the_to_header(smtp):
    """
    The envelope and the header are separate arguments to sendmail and a
    mistake in either is invisible to the sender: the mail arrives, and
    only the recipients notice they cannot see each other.
    """
    email_notify._send(
        BASE_CFG | {"email_recipients": ["a@example.com", "b@example.com"]},
        "s", "b",
    )

    assert _headers(smtp)["To"] == "a@example.com, b@example.com"


@pytest.mark.parametrize("override, named", [
    ({"email_smtp_host": "   "},                 "SMTP host"),
    ({"email_recipients": ["", "  "]},           "recipient"),
    ({"email_from": "", "email_username": ""},   "From address"),
])
def test_incomplete_configuration_raises_before_connecting(
        smtp, override, named):
    """
    Ordering as much as the message: validating after opening the socket
    would leave a connection to tear down on a path that has no handle on
    it, and would make a misconfiguration cost a round trip to a server
    that was never going to be asked for anything.
    """
    with pytest.raises(ValueError) as raised:
        email_notify._send(BASE_CFG | override, "s", "b")

    assert named in str(raised.value)
    assert smtp["connections"] == []


# ── Clean-up ─────────────────────────────────────────────────────────────────

def test_the_connection_is_closed_even_when_the_send_fails(smtp):
    smtp["sendmail_raises"] = smtplib.SMTPDataError(451, b"try later")

    with pytest.raises(smtplib.SMTPDataError):
        email_notify._send(BASE_CFG, "s", "b")

    assert _kinds(smtp)[-1] == "quit"


def test_a_failing_quit_does_not_mask_the_send_error(smtp):
    """
    Both fail together in the ordinary case, because a dropped connection
    is what broke the send and is equally what breaks the close. The user
    needs the reason the mail did not go out, not the reason the socket
    would not shut down politely.
    """
    smtp["sendmail_raises"] = smtplib.SMTPDataError(451, b"try later")
    smtp["quit_raises"] = OSError("connection already gone")

    with pytest.raises(smtplib.SMTPDataError):
        email_notify._send(BASE_CFG, "s", "b")


# ── Best-effort senders ──────────────────────────────────────────────────────

def test_a_failure_email_is_attempted_and_smtp_errors_are_swallowed(smtp):
    smtp["sendmail_raises"] = smtplib.SMTPAuthenticationError(535, b"nope")

    email_notify.send_failure_email(BASE_CFG, "Show.mkv", "ffmpeg exited 1", 3)

    assert "sendmail" in _kinds(smtp)


def test_a_breaker_email_is_attempted_and_smtp_errors_are_swallowed(smtp):
    smtp["sendmail_raises"] = smtplib.SMTPAuthenticationError(535, b"nope")

    email_notify.send_breaker_tripped_email(BASE_CFG, 5)

    assert "sendmail" in _kinds(smtp)


def test_a_misconfigured_failure_email_is_swallowed_too(smtp):
    """
    The swallow has to cover the validation raise as well as the SMTP one.
    An unconfigured host is the likeliest state of all — it is the default
    — and a job must not fail because notifications were never set up.
    """
    email_notify.send_failure_email(
        BASE_CFG | {"email_smtp_host": ""}, "Show.mkv", "boom", 1,
    )

    assert smtp["connections"] == []


# ── Message content ──────────────────────────────────────────────────────────

def test_the_failure_email_names_the_file_the_error_and_the_count(smtp):
    email_notify.send_failure_email(
        BASE_CFG, "Show.S01E01.mkv", "ffmpeg exited 1", 3,
    )

    assert _headers(smtp)["Subject"] == "Remuxarr: Show.S01E01.mkv failed"
    body = _body(smtp)
    assert "Show.S01E01.mkv" in body
    assert "ffmpeg exited 1" in body
    assert "#3" in body


def test_a_failure_with_no_captured_error_says_so(smtp):
    """
    error is None whenever the job failed without an exception message,
    and the fallback is what stops the mail reading "Error: None".
    """
    email_notify.send_failure_email(BASE_CFG, "Show.mkv", None, 1)

    body = _body(smtp)
    assert "(no error message captured)" in body
    assert "None" not in body


def test_the_breaker_email_reports_the_count_and_its_own_subject(smtp):
    email_notify.send_breaker_tripped_email(BASE_CFG, 5)

    assert _headers(smtp)["Subject"] == "Remuxarr: failure notifications paused"
    assert "5 consecutive job failures" in _body(smtp)


# ── test_email_connection ────────────────────────────────────────────────────

def test_a_successful_test_email_reports_success(smtp):
    result = email_notify.test_email_connection(BASE_CFG)

    assert result == {"success": True, "message": "Test email sent"}
    assert "sendmail" in _kinds(smtp)


def test_a_failed_test_email_returns_the_reason_rather_than_raising(smtp):
    """
    This is the one place a send failure is allowed to reach the user, and
    the string is the entire diagnostic: the Settings button shows it
    verbatim and there is nothing else to go on.
    """
    smtp["sendmail_raises"] = smtplib.SMTPAuthenticationError(
        535, b"bad credentials")

    result = email_notify.test_email_connection(BASE_CFG)

    assert result["success"] is False
    assert "bad credentials" in result["error"]


def test_a_misconfigured_test_email_names_the_missing_setting(smtp):
    result = email_notify.test_email_connection(
        BASE_CFG | {"email_smtp_host": ""})

    assert result["success"] is False
    assert "SMTP host" in result["error"]
    assert smtp["connections"] == []
