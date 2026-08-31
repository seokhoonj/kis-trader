from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from kis_trader.execution import TwapExecutionResult
from kis_trader.report import ExecutionReport, OrderStatus

_KST = timezone(timedelta(hours=9))


class _Stock:
    def __init__(self, log):
        self._log = log
        self._n = 0

    def buy(self, *, quantity):
        self._n += 1
        self._log.append(quantity)
        return ExecutionReport(client_order_id=f"c{self._n}", order_id="o", symbol="005930",
                               side="buy", status=OrderStatus.FILLED,
                               filled_quantity=Decimal(quantity), average_price=Decimal(70000),
                               recorded_at=datetime(2026, 8, 31, 10, tzinfo=_KST))


class _Dom:
    def __init__(self, stock):
        self._stock = stock

    def stock(self, symbol):
        return self._stock


class _Orders:
    def reconcile(self, cid):
        return None


class _Kis:
    def __init__(self, stock):
        self.domestic = _Dom(stock)
        self.orders = _Orders()


def test_stock_handle_twap_builds_and_runs():
    from kis_trader.domestic.stock import DomesticStock

    log = []
    kis = _Kis(_Stock(log))
    handle = DomesticStock(kis, "005930", market="KRX")
    result = handle.twap(side="buy", quantity=100, over="20m", slices=4,
                         start=datetime(2026, 8, 31, 10, tzinfo=_KST))
    assert isinstance(result, TwapExecutionResult)
    assert log == [25, 25, 25, 25]
    assert result.filled_quantity == Decimal(100)
