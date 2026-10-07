"""Order의 지역/자산/거래소 축 검증."""

import pytest

from kis_trader.errors import KISUsageError
from kis_trader.order import Order


def test_domestic_stock_rejects_un_exchange() -> None:
    with pytest.raises(KISUsageError):
        Order.limit(
            "005930", side="buy", quantity=10, limit_price=70000, exchange="UN",
        )


def test_domestic_stock_accepts_sor() -> None:
    order = Order.limit(
        "005930", side="buy", quantity=10, limit_price=70000, exchange="SOR",
    )
    assert (order.region, order.asset_class, order.exchange) == ("domestic", "stock", "SOR")


def test_default_is_domestic_stock_krx() -> None:
    order = Order.limit("005930", side="buy", quantity=10, limit_price=70000)
    assert (order.region, order.asset_class, order.exchange) == ("domestic", "stock", "KRX")
