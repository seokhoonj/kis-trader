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


def test_open_stock_account_queries_domestic_and_overseas():
    fake = FakeTransport()
    result = _client(fake, account="12345678-01").orders.open()
    assert isinstance(result, OpenOrders)
    assert result.domestic == () and result.overseas == () and result.derivatives == ()
    assert len(result) == 0
    # 두 조회가 각각 실제로 나갔는지(단순 건수가 아니라 TR 로 증명 -- 해외는 시장군마다 한 번씩).
    trs = {c["tr_id"] for c in fake.calls}
    assert "TTTC0084R" in trs      # 국내 정정취소가능주문
    assert "TTTS3018R" in trs      # 해외 미체결


def test_open_derivatives_account_queries_derivatives():
    fake = FakeTransport()
    result = _client(fake, account="12345678-03").orders.open()
    assert isinstance(result, OpenOrders)
    assert result.domestic == () and result.overseas == ()
    assert "TTTO5201R" in {c["tr_id"] for c in fake.calls}   # 파생 미체결(inquire-ccnl)


def test_open_derivatives_populated_wraps_rows_and_counts():
    # 빈 응답만이 아니라 실제 행이 튜플에 담기고 len 이 세는지 -- 03 파생 미체결 한 건.
    from kis_trader import DerivativeOpenOrder
    row = {"odno": "33", "orgn_odno": "", "pdno": "A05609", "prdt_name": "미니코스피 F 202609",
           "sll_buy_dvsn_cd": "02", "ord_qty": "1", "tot_ccld_qty": "0", "ord_idx": "1000.00",
           "ord_tmd": "084419", "nmpr_type_name": "지정가"}
    resp = RawResponse(rt_cd="0", msg_cd="M", msg1="",
                       body={"output1": [row], "ctx_area_nk200": "", "ctx_area_fk200": ""}, tr_cont="D")
    fake = FakeTransport(by_tr={"TTTO5201R": resp})
    result = _client(fake, account="12345678-03").orders.open()
    assert len(result.derivatives) == 1
    assert isinstance(result.derivatives[0], DerivativeOpenOrder)
    assert len(result) == 1


def test_open_overseas_derivatives_unsupported():
    # 08 은 미체결 전용 조회가 없어 fail-closed -- 와이어 전에, 정확한 사유로.
    client = _client(FakeTransport(), account="12345678-08")
    fake = client.transport
    with pytest.raises(KISUsageError, match="해외선물옵션"):
        client.orders.open()
    assert fake.calls == []


def test_open_requires_account():
    client = _client(FakeTransport(), account=None)
    fake = client.transport
    with pytest.raises(KISUsageError):
        client.orders.open()
    assert fake.calls == []


def test_open_stock_account_paper_fails_closed():
    # 주식계좌 미체결 조회(TTTC0084R/TTTS3018R)는 실전전용 -- 모의는 하위 조회에서 fail-closed.
    with pytest.raises(KISUsageError):
        _client(FakeTransport(), account="12345678-01", environment="paper").orders.open()
