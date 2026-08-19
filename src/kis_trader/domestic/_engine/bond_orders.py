"""국내 장내채권 매수 주문 와이어 빌더 (내부) -- derivative_orders.py 의 형제.

주문 실행의 안전 규칙(이중체결 방지·쓰기 재시도 금지·보수적 재조회)은 국내주식·해외·파생과
공유하는 안전 코어(:func:`~kis_trader.domestic._engine.orders.place`)가 맡는다. 이 모듈은 그
코어에 ``build_request`` 로 주입할 **국내 장내채권(BOND) 매수 주문의 와이어 요청**만 조립한다 --
순수 함수라 오케스트레이션 없이 단독 검증된다. 접수 응답(krx_fwdg_ord_orgno/odno/ord_tmd)은
국내주식과 같은 표준 형상이라 안전 코어의 기본 output 파서를 그대로 쓴다(전용 파서 불필요).

KIS URL/TR-ID (KIS 명세 대조, sheet '장내채권 매수주문'/'장내채권 매도주문'):
- 매수: ``POST /uapi/domestic-bond/v1/trading/buy``, 실전 ``TTTC0952U`` (**모의투자 미지원**).
- 매도: ``POST /uapi/domestic-bond/v1/trading/sell``, 실전 ``TTTC0958U`` (**모의투자 미지원**).
  채권은 지정가(채권단가 ``BOND_ORD_UNPR``) 전용이라 시장가가 없다. 수량은 액면(face) 단위,
  가격은 채권단가다. 매도는 종목별(``ORD_DVSN="01"``)로 특정 매수 lot(``BUY_DT``+``BUY_SEQ``)을
  지목하며, 그 lot 은 :class:`~kis_trader.order.Order` 의 재사용 슬롯(bond_buy_date/bond_buy_seq)에서
  읽는다. 정정취소(TTTC0953U)는 매수/매도 공통이다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..._internal._wire import format_wire_decimal
from ...errors import KISUsageError, OrderError
from ...order import _BOND_EXCHANGE, Order, WireRequest

if TYPE_CHECKING:
    from decimal import Decimal

    from ...order import ChangeAction, ImmediateOrderFingerprint
    from ...report import ExecutionReport
    from ...transport import Environment

_PLACE_PATH = "/uapi/domestic-bond/v1/trading/buy"
_PLACE_TR = "TTTC0952U"                            # 실전 전용(모의 미지원)

_SELL_PATH = "/uapi/domestic-bond/v1/trading/sell"
_SELL_TR = "TTTC0958U"                             # 실전 전용(모의 미지원)

_CHANGE_PATH = "/uapi/domestic-bond/v1/trading/order-rvsecncl"
_CHANGE_TR = "TTTC0953U"                            # 실전 전용(모의 미지원)


def is_bond_exchange(exchange: str) -> bool:
    """``exchange`` 가 국내 장내채권(BOND)이면 True. 안전 코어의 주문 라우팅에 쓴다."""
    return exchange == _BOND_EXCHANGE


def make_order_request(
    order: Order, *, cano: str, product_code: str, environment: Environment
) -> WireRequest:
    """안전 코어(:func:`~kis_trader.domestic._engine.orders.place`)에 넘길 국내 장내채권 발주 빌더.

    채권은 **실전 전용**(모의투자 미지원)이라 ``paper`` 면 fail-closed(client 라우팅에서 먼저 막지만
    빌더에서도 방어). 채권은 지정가(채권단가) 전용이라 ``limit_price`` 가 없으면 거부한다. 주문 형상
    가드(지정가·정규장·day)는 매수/매도 공통이며, 그 뒤 ``side`` 로 갈라 매수는
    :func:`_buy_body`(``buy``/``TTTC0952U``), 매도는 :func:`_sell_body`(``sell``/``TTTC0958U``, 매수 lot
    지목)로 조립한다."""
    if order.exchange != _BOND_EXCHANGE:
        raise OrderError(f"채권 빌더에 비-BOND 주문이 들어왔다: {order.exchange!r} (라우팅 오류).")
    if environment == "paper":
        raise KISUsageError("장내채권 주문은 모의투자 미지원 -- 실전에서만.")
    if order.limit_price is None:
        raise KISUsageError("장내채권 주문은 지정가(채권단가) 필수.")
    if order.order_type != "limit":
        raise KISUsageError("장내채권 주문은 지정가만 지원한다.")
    if order.stop_price:
        raise KISUsageError("장내채권 주문은 stop 가격을 지원하지 않는다.")
    if order.session != "regular":
        raise KISUsageError("장내채권 주문은 정규장만 지원한다.")
    if order.time_in_force != "day":
        raise KISUsageError("장내채권 주문은 day 만 지원한다.")
    if order.side == "sell":
        return WireRequest("POST", _SELL_PATH, _SELL_TR,
                           _sell_body(order, cano=cano, product_code=product_code))
    return WireRequest("POST", _PLACE_PATH, _PLACE_TR,
                       _buy_body(order, cano=cano, product_code=product_code))


def _buy_body(order: Order, *, cano: str, product_code: str) -> dict[str, str]:
    """장내채권 매수(``TTTC0952U``) 와이어 바디. 일반시장(``SAMT_MKET_PTCI_YN="N"``)·소매시장 아님
    (``BOND_RTL_MKET_YN="N"``)·주문서버구분 "0" 는 고정값이다."""
    assert order.limit_price is not None      # 호출부에서 이미 검증
    return {
        "CANO": cano,
        "ACNT_PRDT_CD": product_code,
        "PDNO": order.symbol,
        "ORD_QTY2": format_wire_decimal(order.quantity),
        "BOND_ORD_UNPR": format_wire_decimal(order.limit_price),
        "SAMT_MKET_PTCI_YN": "N",                 # 일반시장(소액채권 시장참여 아님)
        "BOND_RTL_MKET_YN": "N",                  # 소매시장 아님
        "IDCR_STFNO": "",
        "MGCO_APTM_ODNO": "",
        "ORD_SVR_DVSN_CD": "0",
        "CTAC_TLNO": "",
    }


def _sell_body(order: Order, *, cano: str, product_code: str) -> dict[str, str]:
    """장내채권 매도(``TTTC0958U``) 와이어 바디. 종목별(``ORD_DVSN="01"``)로 특정 매수 lot 을 지목하며,
    매수일자(``BUY_DT``)·매수순번(``BUY_SEQ``)은 Order 의 재사용 슬롯(bond_buy_date/bond_buy_seq)에서
    읽는다 -- 둘 중 하나라도 비면 엉뚱한 lot 을 팔지 않도록 fail-closed. ``SPRX_YN="N"`` 은 분리과세
    미신청(v1 은 분리과세를 지원하지 않고 항상 "N"). ``SLL_AGCO_OPPS_SLL_YN``(대차반대매도 여부)·
    ``SAMT_MKET_PTCI_YN``(소액채권 시장참여)·``BOND_RTL_MKET_YN``(소매시장) 은 "N", 주문서버구분 "0" 는
    고정값이다."""
    assert order.limit_price is not None      # 호출부에서 이미 검증
    if not (order.bond_buy_date and order.bond_buy_seq):
        raise KISUsageError(
            "장내채권 매도는 매수 lot(buy_date/buy_seq)이 필요하다 -- "
            "kis.account.domestic.bonds.balance() 의 BondPosition(buy_date/buy_sequence)."
        )
    return {
        "CANO": cano,
        "ACNT_PRDT_CD": product_code,
        "ORD_DVSN": "01",                         # 종목별(매수일자+매수순번 지목)
        "PDNO": order.symbol,
        "ORD_QTY2": format_wire_decimal(order.quantity),
        "BOND_ORD_UNPR": format_wire_decimal(order.limit_price),
        "SPRX_YN": "N",                           # 분리과세 미신청(v1 미지원 -- 항상 N)
        "BUY_DT": order.bond_buy_date,
        "BUY_SEQ": order.bond_buy_seq,
        "SAMT_MKET_PTCI_YN": "N",                 # 일반시장(소액채권 시장참여 아님)
        "SLL_AGCO_OPPS_SLL_YN": "N",              # 대차반대매도 아님
        "BOND_RTL_MKET_YN": "N",                  # 소매시장 아님
        "MGCO_APTM_ODNO": "",
        "ORD_SVR_DVSN_CD": "0",
        "CTAC_TLNO": "",
    }


def make_change_request(
    *, original_report: ExecutionReport, original_fingerprint: ImmediateOrderFingerprint,
    action: ChangeAction, quantity: Decimal, limit_price: Decimal | None,
    cano: str, product_code: str, environment: Environment,
) -> WireRequest:
    """안전 코어(:func:`~kis_trader.domestic._engine.orders.submit_change`)에 넘길 국내 장내채권
    정정·취소 빌더(order-rvsecncl ``TTTC0953U``, 실전 전용).

    채권은 **실전 전용**(모의투자 미지원)이라 ``paper`` 면 fail-closed(client 라우팅에서 먼저 막지만
    빌더에서도 방어). 원주문 지목은 ``ORGN_ODNO``(발주 응답의 거래소 주문번호)로 하고, 채권은 부분
    정정·취소를 지원하므로(``ORD_QTY2``) 코어가 넘긴 목표 수량(잔량/지정)을 그대로 싣는다
    (``QTY_ALL_ORD_YN="N"`` -- 잔량전부 플래그가 아니라 명시 수량). 채권은 지정가(채권단가) 전용이라
    정정은 새 ``limit_price``(채권단가)가 필수이고, 취소는 원지문의 채권단가를 유지한다."""
    if environment == "paper":
        raise KISUsageError("장내채권 정정·취소는 모의투자 미지원 -- 실전에서만.")
    if action == "modify" and limit_price is None:
        raise KISUsageError("장내채권 정정은 새 지정가(채권단가)가 필요하다.")
    body = {
        "CANO": cano,
        "ACNT_PRDT_CD": product_code,
        "PDNO": original_report.symbol,
        "ORGN_ODNO": str(original_report.order_id),
        "ORD_QTY2": format_wire_decimal(quantity),
        "BOND_ORD_UNPR": (
            format_wire_decimal(limit_price) if limit_price is not None
            else original_fingerprint.limit_price
        ),
        "RVSE_CNCL_DVSN_CD": "01" if action == "modify" else "02",
        "QTY_ALL_ORD_YN": "N",
        "MGCO_APTM_ODNO": "",
        "ORD_SVR_DVSN_CD": "0",
        "CTAC_TLNO": "",
    }
    return WireRequest("POST", _CHANGE_PATH, _CHANGE_TR, body)
