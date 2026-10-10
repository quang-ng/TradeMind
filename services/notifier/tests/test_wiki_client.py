import subprocess
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from common.config import NotifierSettings
from pydantic import ValidationError

from notifier.app.wiki_client import WikiClient
from notifier.tests.test_public_report import report


def client():
    return WikiClient(NotifierSettings(
        wiki_report_repository="dqflow/TradeMind", wiki_report_token="SECRET-TOKEN",
    ))


def test_credentials_are_not_in_commands_or_inherited_trace_env(monkeypatch):
    monkeypatch.setenv("GIT_TRACE_CURL", "1")
    monkeypatch.setenv("POSTGRES_DSN", "SECRET-DSN")
    env = client()._environment()
    assert "GIT_TRACE_CURL" not in env and "POSTGRES_DSN" not in env
    assert "SECRET-TOKEN" not in repr(client().settings)
    assert env["GIT_CONFIG_VALUE_1"] == "false"
    for destination in ("https://evil.invalid/a", "owner/../repo", "owner/repo\n", "-flag/repo"):
        with pytest.raises(ValidationError):
            NotifierSettings(wiki_report_repository=destination)


async def test_disabled_and_missing_token_never_run_git(monkeypatch):
    def forbidden(*args):
        raise AssertionError("must not run")
    monkeypatch.setattr(WikiClient, "_publish_sync", forbidden)
    assert not await WikiClient(NotifierSettings()).publish(report())
    settings = NotifierSettings(wiki_report_repository="dqflow/TradeMind")
    assert not await WikiClient(settings).publish(report())


async def test_failure_retries_and_never_logs_subprocess_secret(monkeypatch, caplog):
    attempts = []
    def fail(self, value):
        attempts.append(value)
        raise subprocess.CalledProcessError(1, "SECRET-TOKEN", stderr=b"SECRET-TOKEN")
    monkeypatch.setattr(WikiClient, "_publish_sync", fail)
    monkeypatch.setattr("notifier.app.wiki_client.asyncio.sleep", AsyncMock())
    assert not await client().publish(report())
    assert len(attempts) == 3
    assert "SECRET-TOKEN" not in caplog.text


def test_writes_refuse_unowned_pages_and_symlinks(tmp_path):
    path = tmp_path / "page.md"
    path.write_text("manual content")
    with pytest.raises(ValueError):
        WikiClient._write_owned(path, "replacement")
    assert path.read_text() == "manual content"
    link = tmp_path / "link.md"
    link.symlink_to(path)
    with pytest.raises(ValueError):
        WikiClient._write_owned(link, "replacement")


def test_real_git_preserves_history_home_and_idempotent_index(tmp_path, monkeypatch):
    def git(root, *args):
        return subprocess.check_output(["git", *args], cwd=root, stderr=subprocess.PIPE)
    remote = tmp_path / "remote.git"
    remote.mkdir()
    git(remote, "init", "--bare", "--initial-branch=master")
    seed = tmp_path / "seed"
    git(tmp_path, "clone", str(remote), str(seed))
    git(seed, "config", "user.name", "Test")
    git(seed, "config", "user.email", "test@example.invalid")
    (seed / "Home.md").write_text("Existing home")
    git(seed, "add", ".")
    git(seed, "commit", "-m", "Initialize")
    git(seed, "push", "origin", "HEAD")
    publisher = client()
    real_git = publisher._git
    def local_git(directory: Path, *args: str):
        if args[0] == "clone":
            args = (*args[:3], str(remote), *args[4:])
        assert "SECRET-TOKEN" not in " ".join(args)
        return real_git(directory, *args)
    monkeypatch.setattr(publisher, "_git", local_git)
    publisher._publish_sync(report())
    first = git(remote, "rev-parse", "HEAD")
    publisher._publish_sync(report())
    assert first == git(remote, "rev-parse", "HEAD")
    from datetime import timedelta
    older = report().model_copy(update={"window_end": report().window_end-timedelta(days=7)})
    publisher._publish_sync(older)
    assert git(remote, "show", "HEAD:Home.md") == b"Existing home"
    index = git(remote, "show", "HEAD:Operations-Reports.md").decode()
    assert index.count(f"]({report().page_name})") == 1
    assert index.count(f"]({older.page_name})") == 1
    assert index.index(report().page_name) < index.index(older.page_name)
    assert git(remote, "show", f"HEAD:{report().page_name}.md")
