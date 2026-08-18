import pytest

from kis_trader.account import StockAccounts
from kis_trader.client import KISClient
from kis_trader.domestic.namespace import DomesticAccount
from kis_trader.errors import KISUsageError
from kis_trader.overseas.namespace import OverseasAccount


def _c(account: str | None) -> KISClient:
    return KISClient(app_key="k", app_secret="s", account=account, environment="paper")


def test_account_string_moved_to_private():
    assert _c("12345678-01")._account == "12345678-01"
    assert _c(None)._account is None


def test_account_returns_stock_view_for_product_01():
    kis = _c("12345678-01")
    view = kis.account
    assert isinstance(view, StockAccounts)
    assert isinstance(view.domestic, DomesticAccount)
    assert isinstance(view.overseas, OverseasAccount)


def test_account_without_account_fails_closed():
    kis = _c(None)
    with pytest.raises(KISUsageError):
        _ = kis.account


def test_account_unsupported_product_fails_closed():
    kis = _c("12345678-03")  # derivatives -- added in Plan B
    with pytest.raises(KISUsageError):
        _ = kis.account
