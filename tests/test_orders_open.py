"""kis.orders.open() -- 세션 계좌의 미체결 주문을 계좌 종류로 디스패치해 구조체로 모은다.

주식계좌는 국내+해외, 국내선물옵션(03)은 파생, 해외선물옵션(08)은 미지원(전용 조회 없음).
네트워크 없이 FakeTransport 로, 라우팅과 fail-closed 분기를 못 박는다.
"""

from __future__ import annotations

import threading

import pytest

from kis_trader.client import KISClient
from kis_trader.errors import KISUsageError
from kis_trader.open_orders import OpenOrders
from kis_trader.transport import RawResponse


class FakeTransport:
    def __init__(self, *, by_tr=None):
        self.by_tr = by_tr or {}
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent, tr_cont=""):
        with self._lock:
            self.calls.append({"path": path, "tr_id": tr_id})
        empty = RawResponse(rt_cd="0", msg_cd="M", msg1="",
                            body={"output": [], "output1": [], "output2": []}, tr_cont="")
        return self.by_tr.get(tr_id, empty)


def _client(transport, *, account, environment="real"):
    return KISClient(app_key="k", app_secret="s", account=account,
                     environment=environment, transport=transport)


def test_open_stock_account_returns_domestic_and_overseas():
    fake = FakeTransport()
    result = _client(fake, account="12345678-01").orders.open()
    assert isinstance(result, OpenOrders)
    assert result.domestic == ()
    assert result.overseas == ()
    assert result.derivatives == ()
    assert len(result) == 0
    # 국내·해외 미체결 조회가 각각 실제로 나갔는지(라우팅) -- 최소 두 조회.
    trs = [c["tr_id"] for c in fake.calls]
    assert len(trs) >= 2


def test_open_derivatives_account_returns_derivatives():
    fake = FakeTransport()
    result = _client(fake, account="12345678-03").orders.open()
    assert isinstance(result, OpenOrders)
    assert result.domestic == ()
    assert result.overseas == ()
    assert result.derivatives == ()


def test_open_overseas_derivatives_unsupported():
    fake = FakeTransport()
    with pytest.raises(KISUsageError):
        _client(fake, account="12345678-08").orders.open()


def test_open_requires_account():
    fake = FakeTransport()
    with pytest.raises(KISUsageError):
        _client(fake, account=None).orders.open()
