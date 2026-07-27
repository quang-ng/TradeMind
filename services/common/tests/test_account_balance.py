from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from common.account_balance import AccountBalanceSnapshot


def test_balance_snapshot_reports_freshness() -> None:
    now = datetime.now(timezone.utc)
    snapshot = AccountBalanceSnapshot(
        equity_usdt=Decimal("115"),
        free_balance_usdt=Decimal("100"),
        captured_at=now - timedelta(seconds=30),
    )

    assert snapshot.is_fresh(now=now, max_age_seconds=90) is True
    assert snapshot.is_fresh(now=now + timedelta(seconds=61), max_age_seconds=90) is False


def test_balance_snapshot_rejects_free_balance_above_equity() -> None:
    with pytest.raises(ValidationError):
        AccountBalanceSnapshot(
            equity_usdt=Decimal("100"),
            free_balance_usdt=Decimal("101"),
            captured_at=datetime.now(timezone.utc),
        )
