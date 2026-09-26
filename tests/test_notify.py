import httpx2
import pytest

from offsite_backup.config import EmailConfig, NotifyConfig, NtfyTarget
from offsite_backup.errors import NotificationError
from offsite_backup.notify import Notification, Notifier, notification_for
from offsite_backup.results import ComponentResult, RunReport


class HttpRecorder:
    """httpx MockTransport handler recording requests and scripting responses.

    URLs containing any `failing` fragment raise a connection error; `status`
    overrides the response status; `ntfy_body` overrides the JSON ntfy returns.
    """

    def __init__(self, failing=(), status=200, ntfy_body=None):
        self.requests = []
        self.failing = failing
        self.status = status
        self.ntfy_body = ntfy_body

    def handle(self, request):
        self.requests.append(request)
        if any(fragment in str(request.url) for fragment in self.failing):
            raise httpx2.ConnectError("connection refused", request=request)
        if request.method == "POST":
            body = self.ntfy_body
            if body is None:
                body = {
                    "id": "abc123",
                    "time": 1_700_000_000,
                    "event": "message",
                    "topic": str(request.url).rsplit("/", 1)[-1],
                    "message": request.content.decode(),
                }
            return httpx2.Response(self.status, json=body)
        return httpx2.Response(self.status, text="OK")

    def client(self):
        return httpx2.Client(transport=httpx2.MockTransport(self.handle))

    def to(self, fragment):
        return [r for r in self.requests if fragment in str(r.url)]


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


def notifier(cfg, http=None, smtp=None):
    http = http or HttpRecorder()
    return Notifier(cfg, http=http.client(), smtp_factory=smtp or FakeSmtpFactory())


class TestNtfy:
    def test_success_posted_to_every_target_with_body(self):
        http = HttpRecorder()
        errors = notifier(NotifyConfig(ntfy=(NTFY_A, NTFY_B)), http).notify(GOOD)
        assert errors == []
        (a,) = http.to("ntfy.example.org/kc-backups")
        (b,) = http.to("ntfy.sh/oncall")
        for request in (a, b):
            assert request.method == "POST"
            assert request.content == b"all fine"
            assert request.headers["Title"] == "backup OK"

    def test_failure_uses_high_priority_and_success_does_not(self):
        http = HttpRecorder()
        n = notifier(NotifyConfig(ntfy=(NTFY_B,)), http)
        n.notify(GOOD)
        n.notify(BAD)
        good, bad = http.to("oncall")
        assert bad.headers["Priority"] == "high"
        assert good.headers["Priority"] != "high"

    def test_basic_auth_only_when_credentials_configured(self):
        http = HttpRecorder()
        notifier(NotifyConfig(ntfy=(NTFY_A, NTFY_B)), http).notify(GOOD)
        (authed,) = http.to("kc-backups")
        (anonymous,) = http.to("oncall")
        assert authed.headers["Authorization"] == "Basic dTpw"  # base64("u:p")
        assert "Authorization" not in anonymous.headers

    def test_one_failing_target_does_not_block_others(self):
        http = HttpRecorder(failing=("ntfy.example.org",))
        errors = notifier(NotifyConfig(ntfy=(NTFY_A, NTFY_B)), http).notify(GOOD)
        assert len(http.to("oncall")) == 1
        (error,) = errors
        assert isinstance(error, NotificationError)
        assert error.channel == "ntfy"
        assert "kc-backups" in error.target
        assert isinstance(error.cause, httpx2.HTTPError)
        assert "kc-backups" in str(error)

    def test_non_2xx_response_is_a_delivery_error(self):
        http = HttpRecorder(status=503)
        errors = notifier(NotifyConfig(ntfy=(NTFY_B,)), http).notify(GOOD)
        (error,) = errors
        assert error.channel == "ntfy"
        assert "503" in str(error)

    def test_malformed_publish_response_is_a_delivery_error(self):
        http = HttpRecorder(ntfy_body={"unexpected": "shape"})
        errors = notifier(NotifyConfig(ntfy=(NTFY_B,)), http).notify(GOOD)
        (error,) = errors
        assert error.channel == "ntfy"
        assert "oncall" in error.target


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
        (error,) = errors
        assert isinstance(error, NotificationError)
        assert error.channel == "email"
        assert error.target == "smtp.example.org"
        assert isinstance(error.cause, OSError)


class TestPing:
    def test_success_hits_url_and_failure_hits_fail_suffix(self):
        http = HttpRecorder()
        n = notifier(NotifyConfig(ping_url="https://hc.example.org/ping/abc/"), http)
        n.notify(GOOD)
        n.notify(BAD)
        urls = [str(r.url) for r in http.requests]
        assert urls == ["https://hc.example.org/ping/abc", "https://hc.example.org/ping/abc/fail"]
        assert all(r.method == "GET" for r in http.requests)

    def test_ping_failure_is_reported_not_raised(self):
        http = HttpRecorder(failing=("hc.example.org",))
        errors = notifier(NotifyConfig(ping_url="https://hc.example.org/ping/abc"), http).notify(
            GOOD
        )
        (error,) = errors
        assert isinstance(error, NotificationError)
        assert error.channel == "ping"
        assert error.target == "https://hc.example.org/ping/abc"

    def test_ping_non_2xx_is_a_delivery_error(self):
        http = HttpRecorder(status=404)
        errors = notifier(NotifyConfig(ping_url="https://hc.example.org/ping/abc"), http).notify(
            GOOD
        )
        (error,) = errors
        assert error.channel == "ping"


class TestNothingConfigured:
    def test_no_channels_means_no_traffic_and_no_errors(self):
        http, smtp = HttpRecorder(), FakeSmtpFactory()
        assert notifier(NotifyConfig(), http, smtp).notify(BAD) == []
        assert http.requests == []
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
