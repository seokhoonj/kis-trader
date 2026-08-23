import pytest

from kis_trader.account import StockAccount
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
    assert isinstance(view, StockAccount)
    assert isinstance(view.domestic, DomesticAccount)
    assert isinstance(view.overseas, OverseasAccount)


def test_account_without_account_fails_closed():
    kis = _c(None)
    with pytest.raises(KISUsageError):
        _ = kis.account


def test_account_returns_stock_view_for_pension_savings_22():
    # 연금저축(22)은 위탁과 같은 국내주식 계좌 엔드포인트를 쓴다 -> StockAccount.
    view = _c("12345678-22").account
    assert isinstance(view, StockAccount)
    assert isinstance(view.domestic, DomesticAccount)


def test_account_returns_stock_view_for_irp_29():
    # IRP(29)도 같은 조회 엔드포인트 -> StockAccount. 조회전용이라 orderable 은 꺼진다.
    kis = _c("12345678-29")
    assert kis._orderable is False
    assert isinstance(kis.account, StockAccount)


def test_account_unsupported_product_fails_closed():
    kis = _c("12345678-02")  # 수익증권(펀드) 등 -- kis.account 뷰 미지원
    with pytest.raises(KISUsageError):
        _ = kis.account


def test_old_market_account_path_removed():
    kis = _c("12345678-01")
    with pytest.raises(AttributeError):
        _ = kis.domestic.account  # moved to kis.account.domestic
    with pytest.raises(AttributeError):
        _ = kis.overseas.account  # moved to kis.account.overseas
