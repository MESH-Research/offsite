"""Outbound notifications: ntfy targets, error email, and the dead-man ping.

Every transport is injected (an HTTP opener and an SMTP factory) so tests use
fakes. Delivery problems are collected and returned, never raised: a broken
notification channel must not fail a good backup.
"""

from __future__ import annotations

import base64
import smtplib
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from email.message import EmailMessage

from offsite_backup.config import EmailConfig, NotifyConfig, NtfyTarget
from offsite_backup.errors import NotificationError
from offsite_backup.results import RunReport

TIMEOUT_SECONDS = 15

Opener = Callable[..., object]
SmtpFactory = Callable[..., smtplib.SMTP]


@dataclass(frozen=True)
class Notification:
    """What to tell people: a title, a body, and whether it is good news."""

    title: str
    body: str
    ok: bool


def notification_for(operation: str, report: RunReport) -> Notification:
    """Summarise a `RunReport` for `operation` (e.g. ``backup``) as a Notification.

    The title states the operation and overall outcome; the body has one
    line per component with its snapshot ids or error.
    """
    lines = []
    for result in report.results:
        if result.ok:
            ids = ", ".join(s[:8] for s in result.snapshot_ids) or "no snapshots"
            lines.append(f"OK   {result.name}: {ids} ({result.duration_s:.0f}s)")
        else:
            lines.append(f"FAIL {result.name}: {result.error or 'unknown error'}")
    outcome = "OK" if report.ok else "FAILED"
    return Notification(
        title=f"offsite-backup {operation} {outcome}",
        body="\n".join(lines) or "nothing to report",
        ok=report.ok,
    )


class Notifier:
    """Deliver a Notification through every configured channel."""

    def __init__(
        self,
        cfg: NotifyConfig,
        *,
        opener: Opener = urllib.request.urlopen,
        smtp_factory: SmtpFactory = smtplib.SMTP,
    ) -> None:
        """Bind to the notification config; transports default to the stdlib."""
        self._cfg = cfg
        self._opener = opener
        self._smtp_factory = smtp_factory

    def notify(self, notification: Notification) -> list[NotificationError]:
        """Send `notification` everywhere it should go.

        ntfy targets receive both successes and failures; email goes out only
        on failure; the ping URL is hit on success and ``<url>/fail`` on
        failure. Returns the delivery failures as `NotificationError` values
        (empty when everything was delivered). Never raises.
        """
        errors: list[NotificationError] = []
        for target in self._cfg.ntfy:
            self._attempt(
                errors,
                "ntfy",
                f"{target.url.rstrip('/')}/{target.topic}",
                lambda target=target: self._post_ntfy(target, notification),
            )
        email = self._cfg.email
        if not notification.ok and email is not None:
            self._attempt(
                errors, "email", email.smtp_host, lambda: self._send_email(email, notification)
            )
        if self._cfg.ping_url:
            self._attempt(
                errors,
                "ping",
                self._cfg.ping_url,
                lambda: self._ping(self._cfg.ping_url, notification.ok),
            )
        return errors

    @staticmethod
    def _attempt(
        errors: list[NotificationError], channel: str, target: str, deliver: Callable[[], None]
    ) -> None:
        """Run one delivery, converting any failure into a collected error."""
        try:
            deliver()
        except Exception as exc:  # noqa: BLE001 - delivery must never raise
            errors.append(NotificationError(channel, target, exc))

    def _post_ntfy(self, target: NtfyTarget, notification: Notification) -> None:
        headers = {
            "Title": notification.title,
            "Priority": "default" if notification.ok else "high",
            "Tags": "white_check_mark" if notification.ok else "rotating_light",
        }
        if target.user:
            credentials = f"{target.user}:{target.password}".encode()
            headers["Authorization"] = "Basic " + base64.b64encode(credentials).decode()
        request = urllib.request.Request(
            f"{target.url.rstrip('/')}/{target.topic}",
            data=notification.body.encode(),
            headers=headers,
            method="POST",
        )
        with self._opener(request, timeout=TIMEOUT_SECONDS):
            pass

    def _send_email(self, email: EmailConfig, notification: Notification) -> None:
        message = EmailMessage()
        message["Subject"] = notification.title
        message["From"] = email.smtp_from
        message["To"] = ", ".join(email.to)
        message.set_content(notification.body)
        with self._smtp_factory(email.smtp_host, email.smtp_port, timeout=TIMEOUT_SECONDS) as smtp:
            smtp.starttls()
            if email.smtp_user:
                smtp.login(email.smtp_user, email.smtp_password or "")
            smtp.send_message(message)

    def _ping(self, url: str, ok: bool) -> None:
        target = url.rstrip("/") + ("" if ok else "/fail")
        with self._opener(urllib.request.Request(target), timeout=TIMEOUT_SECONDS):
            pass
