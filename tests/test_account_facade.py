import pytest

from kis_trader.account import StockAccount
from kis_trader.client import KISClient
from kis_trader.domestic.derivative_account import DomesticDerivativesAccount
from kis_trader.domestic.namespace import DomesticAccount
from kis_trader.errors import KISUsageError
from kis_trader.overseas.derivative_account import OverseasDerivativesAccount
from kis_trader.overseas.namespace import OverseasAccount
from kis_trader.pension.account import PensionAccount


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


def test_account_pension_returns_view_for_irp_29():
    view = _c("12345678-29").account
    assert isinstance(view, StockAccount)
    assert isinstance(view.pension, PensionAccount)


def test_account_pension_gated_for_product_01():
    view = _c("12345678-01").account
    with pytest.raises(KISUsageError):
        _ = view.pension


def test_account_pension_gated_for_pension_savings_22():
    view = _c("12345678-22").account
    with pytest.raises(KISUsageError):
        _ = view.pension


def test_top_level_pension_removed():
    kis = _c("12345678-29")
    with pytest.raises(AttributeError):
        _ = kis.pension


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


@pytest.mark.parametrize("product_code", ["01", "22", "29"])
def test_stock_account_exposes_product_code(product_code):
    # kind 는 세 상품을 "stock" 으로 뭉개므로, raw 상품코드로 연금(29)/일반(01)을 구별한다.
    view = _c(f"12345678-{product_code}").account
    assert isinstance(view, StockAccount)
    assert view.product_code == product_code


def test_domestic_derivatives_exposes_product_code():
    view = _c("81012345-03").account
    assert isinstance(view, DomesticDerivativesAccount)
    assert view.product_code == "03"


def test_overseas_derivatives_exposes_product_code():
    view = _c("81012345-08").account
    assert isinstance(view, OverseasDerivativesAccount)
    assert view.product_code == "08"


def test_product_code_distinguishes_irp_from_general():
    # 소비자(humbletrader)는 .pension try/except 없이 상품코드로 연금/일반을 분기한다.
    assert _c("12345678-29").account.product_code == "29"
    assert _c("12345678-01").account.product_code == "01"
