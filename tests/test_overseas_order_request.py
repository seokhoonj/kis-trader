"""해외 주문 요청 조립 -- build_order_request.

TR 표(시장 x 매수/매도 x 실전/모의)와 거래소코드 매핑(NAS->NASD 등), 지정가 바디, SLL_TYPE(매도만),
정수 수량/거래소/시장가 거부를 원장 코드표에 맞게 전수 검증한다(순수 함수, 전송 없음).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from kis_openapi._overseas.orders import build_order_request
from kis_openapi.errors import KisUsageError


def _build(**over):
    args = {"side": "buy", "symbol": "AAPL", "quantity": Decimal(3),
            "limit_price": Decimal("150.25"), "exchange": "NAS", "cano": "12345678",
            "product_code": "01", "environment": "real"}
    args.update(over)
    return build_order_request(**args)


def test_us_buy_real_tr_and_body():
    method, path, tr_id, body = _build()
    assert method == "POST"
    assert path == "/uapi/overseas-stock/v1/trading/order"
    assert tr_id == "TTTT1002U"
    assert body["OVRS_EXCG_CD"] == "NASD"          # NAS -> NASD
    assert body["PDNO"] == "AAPL"
    assert body["ORD_QTY"] == "3"
    assert body["OVRS_ORD_UNPR"] == "150.25"
    assert body["ORD_DVSN"] == "00"                # 지정가
    assert "SLL_TYPE" not in body                  # 매수는 SLL_TYPE 없음


def test_sell_sets_sll_type():
    _, _, tr_id, body = _build(side="sell")
    assert tr_id == "TTTT1006U"                     # 미국 매도 실전
    assert body["SLL_TYPE"] == "00"


def test_full_tr_table_matches_ledger():
    # (exchange, market)별 실전/모의 x 매수/매도 TR 전수 (원장 '해외주식 주문').
    cases = {
        ("NAS", "real"): ("TTTT1002U", "TTTT1006U"),
        ("NAS", "demo"): ("VTTT1002U", "VTTT1001U"),   # 모의 미국매도는 VTTT1001U(비대칭)
        ("TSE", "real"): ("TTTS0308U", "TTTS0307U"),
        ("TSE", "demo"): ("VTTS0308U", "VTTS0307U"),
        ("SHS", "real"): ("TTTS0202U", "TTTS1005U"),
        ("HKS", "real"): ("TTTS1002U", "TTTS1001U"),
        ("SZS", "real"): ("TTTS0305U", "TTTS0304U"),
        ("HSX", "real"): ("TTTS0311U", "TTTS0310U"),   # 베트남(호치민)
    }
    for (exchange, env), (buy_tr, sell_tr) in cases.items():
        assert _build(exchange=exchange, environment=env)[2] == buy_tr
        assert _build(exchange=exchange, environment=env, side="sell")[2] == sell_tr


def test_exchange_code_mapping():
    for excd, order_excd in [("NYS", "NYSE"), ("AMS", "AMEX"), ("HKS", "SEHK"),
                             ("SHS", "SHAA"), ("SZS", "SZAA"), ("TSE", "TKSE"),
                             ("HNX", "HASE"), ("HSX", "VNSE")]:
        assert _build(exchange=excd)[3]["OVRS_EXCG_CD"] == order_excd


def test_rejects_market_order():
    with pytest.raises(KisUsageError, match="지정가"):
        _build(limit_price=None)


def test_rejects_fractional_quantity():
    with pytest.raises(KisUsageError, match="정수"):
        _build(quantity=Decimal("1.5"))


def test_rejects_unknown_exchange():
    with pytest.raises(KisUsageError, match="거래소"):
        _build(exchange="XXX")
