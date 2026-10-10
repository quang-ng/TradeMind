"""Private-only Git archive. Verify GitHub visibility before writing and pushing."""

import asyncio
import logging
import re
import subprocess
import tempfile
from pathlib import Path

import httpx
from common.config import NotifierSettings
from pydantic import BaseModel, ConfigDict

from .private_report import PrivateWeeklyReport, render_private_report
from .report_charts import render_equity_chart, render_report_charts
from .wiki_client import WikiClient

logger = logging.getLogger(__name__)
_MARKER = "<!-- trademind-private-report-v1 -->"
_PAGE = re.compile(r"Weekly-\d{4}-\d{2}-\d{2}-\d{6}\.md")


class RepositoryMetadata(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    full_name: str
    private: bool
    visibility: str


class PrivateReportClient:
    def __init__(self, settings: NotifierSettings) -> None:
        self.settings = settings
        self.git = WikiClient(
            settings.model_copy(
                update={
                    "wiki_report_repository": settings.private_report_repository,
                    "wiki_report_token": settings.private_report_token,
                }
            )
        )

    def _verify_private(self) -> None:
        repo = self.settings.private_report_repository
        token = self.settings.private_report_token.get_secret_value()
        with httpx.Client(timeout=15, follow_redirects=False) as client:
            response = client.get(
                f"https://api.github.com/repos/{repo}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                },
            )
            response.raise_for_status()
            metadata = RepositoryMetadata.model_validate(response.json())
        if (
            not metadata.private
            or metadata.visibility != "private"
            or metadata.full_name.casefold() != repo.casefold()
        ):
            raise ValueError("Private destination required")

    def _publish_sync(self, report: PrivateWeeklyReport) -> None:
        self._verify_private()
        repo = self.settings.private_report_repository
        with tempfile.TemporaryDirectory(prefix="trademind-private-") as directory:
            root = Path(directory)
            self.git._git(root, "clone", "--depth", "1", f"https://github.com/{repo}.git", "repo")
            checkout = root / "repo"
            page = report.summary.page_name
            filename, chart_name = f"{page}.md", f"{page}-charts.png"
            equity_name = f"{page}-equity.png"
            for name in (filename, chart_name, equity_name, "REPORTS.md"):
                target = checkout / name
                if target.is_symlink():
                    raise ValueError("Refusing a symlink")
                if target.exists() and name.endswith(".md"):
                    if not target.read_text().startswith(_MARKER):
                        raise ValueError("Refusing a non-generated page")
            (checkout / filename).write_text(
                render_private_report(report).replace(
                    "[Mục lục](README.md)", "[Mục lục](REPORTS.md)"
                ),
                encoding="utf-8",
            )
            (checkout / chart_name).write_bytes(render_report_charts(report.summary))
            (checkout / equity_name).write_bytes(render_equity_chart(report))
            pages = sorted(
                [
                    p.name
                    for p in checkout.iterdir()
                    if _PAGE.fullmatch(p.name) and p.is_file() and not p.is_symlink()
                ],
                reverse=True,
            )
            (checkout / "REPORTS.md").write_text(
                "\n".join(
                    [
                        _MARKER,
                        "# TradeMind — Báo cáo nội bộ",
                        "",
                        "Kết quả tuần, việc cần kiểm tra, snapshot tài khoản và nhật ký giao dịch.",
                        "",
                        *[f"- [{p.removesuffix('.md')}]({p})" for p in pages],
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            self.git._git(checkout, "add", "--", filename, chart_name, equity_name, "REPORTS.md")
            if not self.git._git(checkout, "diff", "--cached", "--name-only"):
                return
            self.git._git(checkout, "commit", "-m", f"Archive private weekly report {page}")
            self._verify_private()
            self.git._git(checkout, "push", "origin", "HEAD")

    async def publish(self, report: PrivateWeeklyReport) -> bool:
        if not self.settings.private_report_repository:
            return False
        if not self.settings.private_report_token.get_secret_value():
            logger.warning("private_report_token_missing")
            return False
        for attempt in range(3):
            try:
                await asyncio.to_thread(self._publish_sync, report)
                logger.info("private_report_published", extra={"page": report.summary.page_name})
                return True
            except (httpx.HTTPError, OSError, ValueError, subprocess.SubprocessError):
                logger.warning("private_report_publish_failed", extra={"attempt": attempt + 1})
                if attempt < 2:
                    await asyncio.sleep(2**attempt)
        return False
