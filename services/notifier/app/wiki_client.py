"""Bounded, non-force Git publisher for the allowlisted public report model."""

import asyncio
import base64
import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path

from common.config import NotifierSettings
from PIL import Image

from .public_report import PublicWeeklyReport, render_public_report
from .report_charts import CHART_OWNER, render_report_charts

logger = logging.getLogger(__name__)
_MARKER = "<!-- trademind-public-report-v1 -->"
_PAGE = re.compile(r"Weekly-\d{4}-\d{2}-\d{2}-\d{6}\.md")


class WikiClient:
    def __init__(self, settings: NotifierSettings) -> None:
        self.settings = settings

    def _environment(self) -> dict[str, str]:
        credential = base64.b64encode(
            f"x-access-token:{self.settings.wiki_report_token.get_secret_value()}".encode()
        ).decode()
        # Do not inherit tracing, credentials/helpers or arbitrary Git config from the host.
        return {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_COUNT": "3",
            "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
            "GIT_CONFIG_VALUE_0": f"Authorization: Basic {credential}",
            "GIT_CONFIG_KEY_1": "http.followRedirects", "GIT_CONFIG_VALUE_1": "false",
            "GIT_CONFIG_KEY_2": "core.hooksPath", "GIT_CONFIG_VALUE_2": os.devnull,
            "GIT_AUTHOR_NAME": "TradeMind Reports",
            "GIT_AUTHOR_EMAIL": "reports@users.noreply.github.com",
            "GIT_COMMITTER_NAME": "TradeMind Reports",
            "GIT_COMMITTER_EMAIL": "reports@users.noreply.github.com",
        }

    def _git(self, directory: Path, *args: str) -> bytes:
        return subprocess.run(
            ["git", *args], cwd=directory, env=self._environment(), check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=45,
        ).stdout

    @staticmethod
    def _write_owned(path: Path, content: str) -> None:
        if path.is_symlink() or (path.exists() and not path.read_text().startswith(_MARKER)):
            raise ValueError("Refusing to overwrite a page not owned by the report publisher")
        path.write_text(content, encoding="utf-8")

    def _publish_sync(self, report: PublicWeeklyReport) -> None:
        repository = self.settings.wiki_report_repository
        with tempfile.TemporaryDirectory(prefix="trademind-wiki-") as directory:
            root = Path(directory)
            self._git(root, "clone", "--depth", "1",
                      f"https://github.com/{repository}.wiki.git", "wiki")
            wiki = root / "wiki"
            filename = f"{report.page_name}.md"
            self._write_owned(wiki / filename, render_public_report(report))
            chart_name = f"{report.page_name}-charts.png"
            chart = wiki / chart_name
            if chart.is_symlink():
                raise ValueError("Refusing to overwrite a chart symlink")
            if chart.exists():
                with Image.open(chart) as existing:
                    if existing.info.get("Software") != CHART_OWNER:
                        raise ValueError("Refusing to overwrite a chart not owned by publisher")
            chart.write_bytes(render_report_charts(report))
            pages = sorted(
                (p.stem for p in wiki.iterdir() if _PAGE.fullmatch(p.name)
                 and not p.is_symlink() and p.is_file()
                 and p.read_text().startswith(_MARKER)), reverse=True,
            )
            self._write_owned(wiki / "Operations-Reports.md", "\n".join([
                _MARKER, "# TradeMind — Lịch sử báo cáo tuần", "",
                "Báo cáo tổng hợp công khai. Mỗi trang ghi rõ khoảng thời gian UTC.", "",
                *[f"- [{page.removeprefix('Weekly-')}]({page})" for page in pages], "",
            ]))
            self._git(wiki, "add", "--", filename, chart_name, "Operations-Reports.md")
            if not self._git(wiki, "diff", "--cached", "--name-only"):
                return
            self._git(wiki, "commit", "-m", f"Archive public weekly report {report.page_name}")
            # Concurrent updates reject this push; publish() clones afresh on retry.
            self._git(wiki, "push", "origin", "HEAD")

    async def publish(self, report: PublicWeeklyReport) -> bool:
        if type(report) is not PublicWeeklyReport:
            raise TypeError("Public report model required")
        if not self.settings.wiki_report_repository:
            return False
        if not self.settings.wiki_report_token.get_secret_value():
            logger.warning("wiki_report_token_missing")
            return False
        for attempt in range(3):
            try:
                await asyncio.to_thread(self._publish_sync, report)
                logger.info("wiki_report_published", extra={"page": report.page_name})
                return True
            except (OSError, subprocess.SubprocessError, ValueError):
                # Never log subprocess command/output or exception text: may include auth.
                logger.warning("wiki_report_publish_failed", extra={
                    "page": report.page_name, "attempt": attempt + 1,
                })
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)
        return False
