import smtplib

from common.config import NotifierSettings
from notifier.app.email_client import EmailClient


async def test_send_email_is_noop_when_unconfigured():
    client = EmailClient(NotifierSettings(smtp_host="", email_to=""))
    assert await client.send_email("subject", "body") is False


async def test_send_email_calls_smtp_and_returns_true(monkeypatch):
    sent = {}

    class _FakeSMTP:
        def __init__(self, host, port, timeout=None):
            sent["host"] = host
            sent["port"] = port

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def starttls(self):
            sent["starttls"] = True

        def login(self, username, password):
            sent["login"] = (username, password)

        def send_message(self, message):
            sent["message"] = message

    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    settings = NotifierSettings(
        smtp_host="smtp.gmail.com",
        smtp_port=587,
        smtp_username="bot@gmail.com",
        smtp_password="app-password",
        email_to="quangnv1400@gmail.com",
    )

    result = await EmailClient(settings).send_email("Weekly Summary", "line1\nline2")

    assert result is True
    assert sent["host"] == "smtp.gmail.com"
    assert sent["login"] == ("bot@gmail.com", "app-password")
    assert sent["message"]["Subject"] == "Weekly Summary"
    assert sent["message"]["To"] == "quangnv1400@gmail.com"
    # email_from left blank -> falls back to smtp_username.
    assert sent["message"]["From"] == "bot@gmail.com"


async def test_send_email_returns_false_on_smtp_error(monkeypatch):
    class _FailingSMTP:
        def __init__(self, host, port, timeout=None):
            raise smtplib.SMTPConnectError(421, "unreachable")

    monkeypatch.setattr(smtplib, "SMTP", _FailingSMTP)
    settings = NotifierSettings(
        smtp_host="smtp.gmail.com", smtp_username="bot@gmail.com", email_to="a@b.com"
    )

    assert await EmailClient(settings).send_email("subject", "body") is False
