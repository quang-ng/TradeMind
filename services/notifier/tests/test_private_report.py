import asyncio
from datetime import timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from common.config import NotifierSettings

from notifier.app.archive_worker import archive_loop
from notifier.app.private_report import (
    AccountSnapshot,
    PrivateTrade,
    PrivateWeeklyReport,
    render_private_report,
)
from notifier.app.private_report_client import PrivateReportClient
from notifier.app.public_report import render_public_report
from notifier.tests.test_public_report import END, report


def private_report():
    return PrivateWeeklyReport(
        summary=report(), observed_at=END + timedelta(days=5),
        account=AccountSnapshot(equity_usdt=Decimal('98765.4321'), open_positions=2,
                                killswitch_enabled=True, dry_run=False),
        trades=(PrivateTrade(symbol='BTC/USDT', closed_at=END-timedelta(hours=1),
                             pnl_usdt=Decimal('-1.2345'), pnl_pct=Decimal('-0.01'),
                             entry_price=Decimal('12345'), exit_price=Decimal('12200'),
                             dry_run=False),),
    )


def test_private_detail_and_snapshot_do_not_leak_into_public():
    value = private_report()
    public = render_public_report(value.summary)
    private = render_private_report(value)
    for text in ['98765.4321', 'BTC/USDT', '12345', '| live |']:
        assert text in private and text not in public
    assert 'ĐANG CHẶN LỆNH MỞ MỚI' in private
    assert 'không phải số dư/trạng thái tại cuối kỳ' in private
    assert 'Profit factor' in private
    assert 'Việc cần kiểm tra' in private and 'Việc cần kiểm tra' in public
    assert 'DRY_RUN=true' not in private
    missing = render_private_report(value.model_copy(update={'account': None}))
    assert 'CHƯA XÁC MINH' in missing and '98765.4321' not in missing


@pytest.mark.parametrize('payload', [
    {'full_name': 'dqflow/TradeMind-Reports', 'private': False, 'visibility': 'public'},
    {'full_name': 'dqflow/TradeMind-Reports', 'private': True, 'visibility': 'internal'},
    {'full_name': 'wrong/repo', 'private': True, 'visibility': 'private'},
    {'full_name': 'dqflow/TradeMind-Reports', 'private': 'true', 'visibility': 'private'},
])
def test_private_destination_fails_closed_before_git(payload, monkeypatch):
    client = PrivateReportClient(NotifierSettings(
        private_report_repository='dqflow/TradeMind-Reports', private_report_token='SECRET',
    ))
    response = httpx.Response(200, json=payload, request=httpx.Request('GET', 'https://api.github.com'))
    monkeypatch.setattr(httpx.Client, 'get', lambda *args, **kwargs: response)
    git = MagicMock()
    monkeypatch.setattr(client.git, '_git', git)
    with pytest.raises(ValueError):
        client._publish_sync(private_report())
    git.assert_not_called()


def test_private_visibility_checked_again_before_push(tmp_path, monkeypatch):
    client = PrivateReportClient(NotifierSettings(
        private_report_repository='dqflow/TradeMind-Reports', private_report_token='SECRET',
    ))
    verify = MagicMock(side_effect=[None, ValueError('now public')])
    monkeypatch.setattr(client, '_verify_private', verify)
    commands = []
    def git(root, *args):
        commands.append(args)
        if args[0] == 'clone':
            (root / 'repo').mkdir()
        return b'changed'
    monkeypatch.setattr(client.git, '_git', git)
    monkeypatch.setattr('notifier.app.private_report_client.render_report_charts', lambda r: b'png')
    with pytest.raises(ValueError):
        client._publish_sync(private_report())
    assert verify.call_count == 2
    assert not any(cmd[0] == 'push' for cmd in commands)


async def test_archive_loop_retries_private_and_ignores_retired_wiki(monkeypatch):
    public = AsyncMock()
    private = AsyncMock(side_effect=[False, True])
    monkeypatch.setattr('notifier.app.public_report.publish_weekly_report', public)
    monkeypatch.setattr('notifier.app.archive_worker.publish_private_week', private)
    sleep = AsyncMock(side_effect=[None, None, asyncio.CancelledError])
    monkeypatch.setattr('notifier.app.archive_worker.asyncio.sleep', sleep)
    with pytest.raises(asyncio.CancelledError):
        await archive_loop(MagicMock(), NotifierSettings(
            wiki_report_repository='dqflow/TradeMind',
            private_report_repository='dqflow/TradeMind-Reports',
        ))
    assert private.await_count == 2
    public.assert_not_called()
