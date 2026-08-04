"""해외 잔고 -- kis.overseas_positions(market=...).

inquire-balance 엔드포인트, 시장->OVRS_EXCG_CD/TR_CRCY_CD 매핑, 외화 금액을 Money(통화 포함)로,
CTX_AREA_FK200/NK200 연속조회, 실전/모의 TR, fail-closed 를 검증한다.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_openapi import KisClient, Money, OverseasPosition
from kis_openapi.errors import KisError, KisUsageError
from kis_openapi.transport import RawResponse

_BALANCE = "/uapi/overseas-stock/v1/trading/inquire-balance"


def _holding(symbol="AAPL", name="APPLE", qty="10", sellable="10", avg="140.00", now="150.25",
             pchs="1400.00", evlu="1502.50", pnl="102.50", rate="7.32", crcy="USD"):
    return {"ovrs_pdno": symbol, "ovrs_item_name": name, "ovrs_cblc_qty": qty,
            "ord_psbl_qty": sellable, "pchs_avg_pric": avg, "now_pric2": now,
            "frcr_pchs_amt1": pchs, "ovrs_stck_evlu_amt": evlu, "frcr_evlu_pfls_amt": pnl,
            "evlu_pfls_rt": rate, "tr_crcy_cd": crcy}


def _resp(rows, *, nk="", fk=""):
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상",
                       body={"output1": rows, "output2": {}, "ctx_area_nk200": nk,
                             "ctx_area_fk200": fk})


class FakeTransport:
    def __init__(self, *, response=None, pages=None):
        self.response = response
        self.pages = pages
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent):
        with self._lock:
            self.calls.append({"path": path, "tr_id": tr_id, "params": params})
            if self.pages is not None:
                return self.pages.pop(0)
        return self.response


def _client(transport, *, environment="real"):
    return KisClient(app_key="k", app_secret="s", account="12345678-01",
                     transport=transport, environment=environment)


def test_overseas_positions_maps_money_and_params():
    fake = FakeTransport(response=_resp([_holding()]))
    positions = _client(fake).overseas_positions(market="US")
    assert len(positions) == 1
    pos = positions[0]
    assert isinstance(pos, OverseasPosition)
    assert pos.symbol == "AAPL"
    assert pos.exchange == "NASD"
    assert pos.quantity == 10
    assert pos.sellable_quantity == 10
    assert pos.average_price == Money(Decimal("140.00"), "USD")
    assert pos.current_price == Money(Decimal("150.25"), "USD")
    assert pos.market_value == Money(Decimal("1502.50"), "USD")
    assert pos.unrealized_pnl == Money(Decimal("102.50"), "USD")
    assert pos.pnl_percent == Decimal("7.32")
    call = fake.calls[0]
    assert call["path"] == _BALANCE
    assert call["tr_id"] == "TTTS3012R"                # real
    assert call["params"]["OVRS_EXCG_CD"] == "NASD"
    assert call["params"]["TR_CRCY_CD"] == "USD"


def test_overseas_positions_market_maps_exchange_and_currency():
    for market, excg, crcy in [("HK", "SEHK", "HKD"), ("JP", "TKSE", "JPY"),
                               ("CN_SH", "SHAA", "CNY"), ("VN_HCM", "VNSE", "VND")]:
        fake = FakeTransport(response=_resp([]))
        _client(fake).overseas_positions(market=market)
        assert fake.calls[0]["params"]["OVRS_EXCG_CD"] == excg
        assert fake.calls[0]["params"]["TR_CRCY_CD"] == crcy


def test_overseas_positions_demo_tr():
    fake = FakeTransport(response=_resp([]))
    _client(fake, environment="demo").overseas_positions(market="US")
    assert fake.calls[0]["tr_id"] == "VTTS3012R"


def test_overseas_positions_paginates_ctx_area():
    fake = FakeTransport(pages=[
        _resp([_holding(symbol="AAPL")], nk="NEXT"),
        _resp([_holding(symbol="MSFT")], nk=""),
    ])
    positions = _client(fake).overseas_positions(market="US")
    assert [p.symbol for p in positions] == ["AAPL", "MSFT"]
    assert fake.calls[1]["params"]["CTX_AREA_NK200"] == "NEXT"


def test_overseas_positions_rejects_bad_market():
    fake = FakeTransport(response=_resp([]))
    with pytest.raises(KisUsageError):
        _client(fake).overseas_positions(market="XX")


def test_overseas_positions_non_list_output1_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok",
                                              body={"output1": "oops"}))
    with pytest.raises(KisError):
        _client(fake).overseas_positions(market="US")


def test_overseas_positions_requires_account():
    fake = FakeTransport(response=_resp([]))
    client = KisClient(app_key="k", app_secret="s", transport=fake)  # 계좌 없음
    with pytest.raises(KisUsageError):
        client.overseas_positions(market="US")
