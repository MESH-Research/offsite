import base64
from contextlib import nullcontext

import pytest

from offsite_backup.config import EmailConfig, NotifyConfig, NtfyTarget
from offsite_backup.notify import Notification, Notifier, notification_for
from offsite_backup.results import ComponentResult, RunReport


class FakeOpener:
    """Records urllib requests; raises for URLs containing any `failing` fragment."""

    def __init__(self, failing=()):
        self.requests = []
        self.failing = failing

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        if any(fragment in request.full_url for fragment in self.failing):
            raise OSError("connection refused")
        return nullcontext()

    def to(self, fragment):
        return [r for r in self.requests if fragment in r.full_url]


class FakeSmtp:
    """Records an SMTP session; raises on send when `failing`."""

    def __init__(self, host, port, timeout=None, *, failing=False):
        self.host, self.port = host, port
        self.failing = failing
        self.tls = False
        self.login_args = None
        self.sent = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        self.tls = True

    def login(self, user, password):
        self.login_args = (user, password)

    def send_message(self, message):
        if self.failing:
            raise OSError("smtp down")
        self.sent.append(message)


class FakeSmtpFactory:
    def __init__(self, failing=False):
        self.sessions = []
        self.failing = failing

    def __call__(self, host, port, timeout=None):
        session = FakeSmtp(host, port, timeout, failing=self.failing)
        self.sessions.append(session)
        return session


NTFY_A = NtfyTarget(url="https://ntfy.example.org", topic="kc-backups", user="u", password="p")
NTFY_B = NtfyTarget(url="https://ntfy.sh/", topic="oncall")
EMAIL = EmailConfig(
    to=("ops@example.org", "martin@example.org"),
    smtp_host="smtp.example.org",
    smtp_from="backups@example.org",
    smtp_port=2525,
    smtp_user="mailer",
    smtp_password="mail-pw",
)
GOOD = Notification(title="backup OK", body="all fine", ok=True)
BAD = Notification(title="backup FAILED", body="db: replica timed out", ok=False)


def notifier(cfg, opener=None, smtp=None):
    return Notifier(
        cfg, opener=opener or FakeOpener(), smtp_factory=smtp or FakeSmtpFactory()
    )


class TestNtfy:
    def test_success_posted_to_every_target_with_body(self):
        opener = FakeOpener()
        errors = notifier(NotifyConfig(ntfy=(NTFY_A, NTFY_B)), opener).notify(GOOD)
        assert errors == []
        (a,) = opener.to("ntfy.example.org/kc-backups")
        (b,) = opener.to("ntfy.sh/oncall")
        for request in (a, b):
            assert request.get_method() == "POST"
            assert request.data == b"all fine"
            assert request.get_header("Title") == "backup OK"

    def test_failure_uses_high_priority_and_success_does_not(self):
        opener = FakeOpener()
        n = notifier(NotifyConfig(ntfy=(NTFY_B,)), opener)
        n.notify(GOOD)
        n.notify(BAD)
        good, bad = opener.to("oncall")
        assert bad.get_header("Priority") == "high"
        assert good.get_header("Priority") != "high"

    def test_basic_auth_only_when_credentials_configured(self):
        opener = FakeOpener()
        notifier(NotifyConfig(ntfy=(NTFY_A, NTFY_B)), opener).notify(GOOD)
        (authed,) = opener.to("kc-backups")
        (anonymous,) = opener.to("oncall")
        expected = "Basic " + base64.b64encode(b"u:p").decode()
        assert authed.get_header("Authorization") == expected
        assert anonymous.get_header("Authorization") is None

    def test_one_failing_target_does_not_block_others(self):
        opener = FakeOpener(failing=("ntfy.example.org",))
        errors = notifier(NotifyConfig(ntfy=(NTFY_A, NTFY_B)), opener).notify(GOOD)
        assert len(opener.to("oncall")) == 1
        assert len(errors) == 1
        assert "kc-backups" in errors[0]


class TestEmail:
    def test_no_email_on_success(self):
        smtp = FakeSmtpFactory()
        errors = notifier(NotifyConfig(email=EMAIL), smtp=smtp).notify(GOOD)
        assert errors == []
        assert smtp.sessions == []

    def test_failure_emails_all_recipients_with_tls_and_login(self):
        smtp = FakeSmtpFactory()
        errors = notifier(NotifyConfig(email=EMAIL), smtp=smtp).notify(BAD)
        assert errors == []
        (session,) = smtp.sessions
        assert (session.host, session.port) == ("smtp.example.org", 2525)
        assert session.tls is True
        assert session.login_args == ("mailer", "mail-pw")
        (message,) = session.sent
        assert message["Subject"] == "backup FAILED"
        assert message["From"] == "backups@example.org"
        assert "ops@example.org" in message["To"]
        assert "martin@example.org" in message["To"]
        assert "replica timed out" in message.get_content()

    def test_no_login_without_credentials(self):
        smtp = FakeSmtpFactory()
        cfg = NotifyConfig(
            email=EmailConfig(
                to=("ops@example.org",),
                smtp_host="smtp.example.org",
                smtp_from="backups@example.org",
            )
        )
        notifier(cfg, smtp=smtp).notify(BAD)
        (session,) = smtp.sessions
        assert session.login_args is None
        assert len(session.sent) == 1

    def test_smtp_failure_is_reported_not_raised(self):
        smtp = FakeSmtpFactory(failing=True)
        errors = notifier(NotifyConfig(email=EMAIL), smtp=smtp).notify(BAD)
        assert len(errors) == 1
        assert "smtp" in errors[0].lower()


class TestPing:
    def test_success_hits_url_and_failure_hits_fail_suffix(self):
        opener = FakeOpener()
        n = notifier(NotifyConfig(ping_url="https://hc.example.org/ping/abc/"), opener)
        n.notify(GOOD)
        n.notify(BAD)
        urls = [r.full_url for r in opener.requests]
        assert urls == ["https://hc.example.org/ping/abc", "https://hc.example.org/ping/abc/fail"]

    def test_ping_failure_is_reported_not_raised(self):
        opener = FakeOpener(failing=("hc.example.org",))
        errors = notifier(NotifyConfig(ping_url="https://hc.example.org/ping/abc"), opener).notify(
            GOOD
        )
        assert len(errors) == 1
        assert "ping" in errors[0].lower()


class TestNothingConfigured:
    def test_no_channels_means_no_traffic_and_no_errors(self):
        opener, smtp = FakeOpener(), FakeSmtpFactory()
        assert notifier(NotifyConfig(), opener, smtp).notify(BAD) == []
        assert opener.requests == []
        assert smtp.sessions == []


class TestNotificationFor:
    def test_green_report(self):
        report = RunReport(
            [
                ComponentResult(name="ecs", ok=True, snapshot_ids=["aaaa1111"]),
                ComponentResult(name="efs", ok=True, snapshot_ids=["bbbb2222"]),
            ]
        )
        n = notification_for("backup", report)
        assert n.ok is True
        assert "backup" in n.title
        for token in ("ecs", "efs", "aaaa1111", "bbbb2222"):
            assert token in n.body

    def test_failed_report_names_component_and_error(self):
        report = RunReport(
            [
                ComponentResult(name="ecs", ok=True),
                ComponentResult(name="db", ok=False, error="replica timed out"),
            ]
        )
        n = notification_for("backup", report)
        assert n.ok is False
        assert "backup" in n.title
        assert "db" in n.body
        assert "replica timed out" in n.body

    @pytest.mark.parametrize("ok", [True, False])
    def test_title_distinguishes_outcome(self, ok):
        good = notification_for("verify", RunReport([ComponentResult(name="x", ok=True)]))
        bad = notification_for("verify", RunReport([ComponentResult(name="x", ok=False)]))
        assert good.title != bad.title
