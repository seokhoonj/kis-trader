"""해외 주문 DATA -- 미체결·알고·예약 주문.

해외 주문 조회(``kis.account.overseas.open_orders`` / ``.algo_orders`` /
``.algo_executions`` / ``kis.overseas_reserved_orders``)가 돌려준다."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, time
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from ..._internal._freeze import freeze_vendor_payload
from ...money import Money
from ...order import Side


@dataclass(frozen=True, slots=True)
class OverseasOpenOrder:
    """해외 미체결 주문 한 건(불변). 브로커 측 미체결 목록이라 우리 ``client_order_id`` 는 없고
    거래소 주문번호(``order_id``)로 식별한다. ``unfilled_quantity`` 는 아직 체결 안 된 잔량."""

    symbol: str
    name: str
    exchange: str                     # 해외거래소코드
    order_id: str                     # 거래소 주문번호(odno)
    side: Side                        # buy / sell
    order_quantity: Decimal           # 주문수량
    filled_quantity: Decimal          # 체결수량
    unfilled_quantity: Decimal        # 미체결 잔량
    order_price: Money                # 주문단가(종목 통화)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class OverseasAlgoOrder:
    """해외 지정가(TWAP/VWAP 등 알고) 주문 한 건(불변). ``order_id`` 로 지목해 체결내역을 조회한다.

    ``split_attribute`` 분할매수속성(예: "정규장 종료" 또는 시간범위), ``branch_number`` 주문채번지점번호
    (체결내역 조회 시 함께 넘긴다).
    """

    order_id: str                     # 주문번호(odno)
    trade_type_name: str                   # 매매구분명(trad_dvsn_name)
    symbol: str
    name: str
    order_quantity: Decimal           # 주문수량(ft_ord_qty)
    order_price: Decimal              # 주문단가(ft_ord_unpr3)
    filled_quantity: Decimal          # 체결수량(ft_ccld_qty)
    split_attribute: str              # 분할매수속성명(splt_buy_attr_name)
    branch_number: str                # 주문채번지점번호(ord_gno_brno)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class OverseasAlgoExecution:
    """해외 알고주문의 체결 한 건(불변). ``sequence`` 체결순번, ``executed_at`` 체결시각(HH:MM:SS)."""

    sequence: str                     # 체결순번(CCLD_SEQ)
    executed_at: time | None          # 체결시간(CCLD_BTWN, HHMMSS)
    symbol: str
    name: str
    filled_quantity: Decimal          # 체결수량(FT_CCLD_QTY)
    price: Money                      # 체결단가(FT_CCLD_UNPR3, 거래통화 TR_CRCY)
    amount: Money                     # 체결금액(FT_CCLD_AMT3, 거래통화 TR_CRCY)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class OverseasReservedOrder:
    """해외주식 예약주문 한 건(불변; 미국·아시아 공통). 정규장 시작 전에 걸어둔 예약으로,
    ``reserved_order_id``(해외예약주문번호)로 식별한다. 집행되면 ``executed_order_id``(주문번호)가 채워진다.

    ``status`` 예약 상태(예: "접수"), ``canceled`` 취소 여부, ``filled_quantity`` 체결수량,
    ``unprocessed_reason`` 미처리 사유. 가격/수량은 Decimal(통화는 거래소별 -- 미국 USD,
    아시아는 HKD/CNY/USD·JPY·VND 등; 이 타입은 통화 메타를 싣지 않아 Money 가 아니다).
    """

    reserved_order_id: str            # 해외예약주문번호(ovrs_rsvn_odno)
    receipt_date: date | None         # 예약주문접수일자(rsvn_ord_rcit_dt)
    order_date: date | None           # 주문일자(ord_dt) -- 집행 전이면 None
    executed_order_id: str            # 집행 주문번호(odno), 미집행이면 ""
    symbol: str
    name: str
    side: Side                        # buy / sell
    status: str                       # 해외예약주문상태명(ovrs_rsvn_ord_stat_cd_name)
    exchange: str                     # 해외거래소코드(ovrs_excg_cd)
    order_quantity: Decimal           # 주문수량(ft_ord_qty)
    order_price: Decimal              # 주문단가(ft_ord_unpr3)
    filled_quantity: Decimal          # 체결수량(ft_ccld_qty)
    canceled: bool                    # 취소여부(cncl_yn)
    unprocessed_reason: str           # 미처리사유(nprc_rson_text)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))
