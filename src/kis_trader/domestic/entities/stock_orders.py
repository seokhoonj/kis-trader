"""국내주식 일별 주문·체결 내역 DATA -- 개별 주문·체결 행(:class:`StockOrder`)과 기간 합계
요약(:class:`StockOrderHistory`).

``kis.account.domestic.order_history(start=, end=)`` 가 돌려주는 조회 전용 스냅샷이다. 한 행은 한 주문의
주문·체결 상태를 함께 담는다(주문수량/단가, 체결수량/평균가/금액, 잔여·거부수량, 취소여부).
금액·수량은 KRW Decimal. 실전·모의(``TTTC0081R``/``VTTC0081R``) 모두 지원한다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from ..._internal._freeze import freeze_vendor_payload
from ...instrument import DomesticOrderExchange
from ...order import Side


@dataclass(frozen=True, slots=True)
class StopTrigger:
    """스톱지정가 주문의 트리거 정보(불변). 비-스톱 주문 체결에는 붙지 않는다(:class:`StockOrder`
    의 ``stop`` 은 그 경우 ``None``). ``trigger_price`` 조건가격, ``effective_datetime`` 효력발생
    상세시각(아직 미발생이면 None)."""

    trigger_price: Decimal               # 스톱지정가조건가격(stpm_cndt_pric)
    effective_datetime: datetime | None  # 스톱지정가효력발생상세시각(stpm_efct_occr_dtmd)


@dataclass(frozen=True, slots=True)
class StockOrder:
    """국내주식 일별 주문·체결 내역 한 건(불변).

    ``order_quantity`` / ``order_price`` 는 주문, ``filled_quantity`` / ``average_price`` /
    ``filled_amount`` 는 체결, ``unfilled_quantity`` 는 잔여(미체결)수량, ``rejected_quantity`` 는
    거부수량이다. ``canceled`` 는 취소여부, ``canceled_quantity`` 는 취소 확인된 수량. ``side`` 는
    매수/매도. ``exchange`` 는 체결 거래소(KRX/NXT/SOR; NXT 도입 전 주문은 미채움이라 None).
    ``stop`` 은 스톱지정가 주문이면 트리거 정보, 아니면 None.
    """

    order_date: date | None                 # 주문일자(ord_dt)
    order_id: str                           # 주문번호(odno)
    original_order_id: str                  # 원주문번호(orgn_odno)
    order_type_name: str                    # 주문구분명(ord_dvsn_name)
    side: Side                              # 매도매수구분(sll_buy_dvsn_cd)
    symbol: str                             # 상품번호/종목코드(pdno)
    name: str                               # 상품명(prdt_name)
    order_quantity: Decimal                 # 주문수량(ord_qty)
    order_price: Decimal                    # 주문단가(ord_unpr)
    order_time: time | None                 # 주문시각(ord_tmd)
    filled_quantity: Decimal                # 총체결수량(tot_ccld_qty)
    average_price: Decimal                  # 체결평균가(avg_prvs)
    filled_amount: Decimal                  # 총체결금액(tot_ccld_amt)
    unfilled_quantity: Decimal              # 잔여수량(rmn_qty)
    rejected_quantity: Decimal              # 거부수량(rjct_qty)
    canceled: bool                          # 취소여부(cncl_yn)
    canceled_quantity: Decimal              # 취소확인수량(cnc_cfrm_qty): 취소 체결된 수량
    branch_number: str                      # 주문채번지점번호(ord_gno_brno)
    exchange_division_code: str             # 거래소구분코드(excg_dvsn_cd): KIS 원코드(예 "02"/"12")
    fill_condition_name: str                # 체결조건명(ccld_cndt_name): 예 IOC/FOK/"없음"
    order_type_code: str                    # 주문구분코드(ord_dvsn_cd): order_type_name 의 기계안정 코드
    product_type_code: str                  # 상품유형코드(prdt_type_cd)
    order_organization_number: str          # 주문조직번호(ord_orgno)
    exchange: DomesticOrderExchange | None  # 거래소ID구분(excg_id_dvsn_cd): KRX/NXT/SOR(미채움이면 None)
    reservation_end_date: date | None       # 예약주문종료일자(rsvn_ord_end_dt)
    loan_date: date | None                  # 대출일자(loan_dt): 신용 주문 외엔 None
    stop: StopTrigger | None                # 스톱지정가 트리거(비-스톱 주문이면 None)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class StockOrderHistory:
    """국내주식 일별 주문·체결 내역과 기간 합계 요약(불변).

    ``orders`` 는 개별 주문·체결 행(:class:`StockOrder`)이고, ``total_*`` 은 조회 기간의 합계다.
    ``average_purchase_price`` 는 기간 매입평균가격, ``estimated_expenses`` 는 추정제비용합계.
    금액·수량은 KRW Decimal.
    """

    orders: tuple[StockOrder, ...]   # 개별 주문·체결 내역(output1)
    total_order_quantity: Decimal    # 총주문수량(tot_ord_qty)
    total_filled_quantity: Decimal   # 총체결수량(tot_ccld_qty)
    total_filled_amount: Decimal     # 총체결금액(tot_ccld_amt)
    average_purchase_price: Decimal  # 매입평균가격(pchs_avg_pric)
    estimated_expenses: Decimal      # 추정제비용합계(prsm_tlex_smtl)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "orders", tuple(self.orders))
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class StockOrderHistoryCollection:
    """국내주식 최근·과거 조회를 함께 담은 주문 내역(불변).

    ``orders`` 는 최근 조회 다음 과거 조회 순서의 통합 행이다. 완전한 주문 식별정보와
    모든 필드·원본이 같은 두 조회의 관측만 중복을 합친다. 같은 행의 반복 횟수는 두 조회 중
    큰 횟수를 보존한다. ``recent`` / ``historical`` 은 각 조회의 행과 합계 요약 원본이다.
    통합 평균가격·제비용·공급자 합계는 계산하지 않는다.
    """

    orders: tuple[StockOrder, ...]
    recent: StockOrderHistory
    historical: StockOrderHistory

    def __post_init__(self) -> None:
        object.__setattr__(self, "orders", tuple(self.orders))


@dataclass(frozen=True, slots=True)
class StockOrderHistoryPeriod:
    """최대 365일의 양 끝 날짜와 해당 기간의 최근·과거 조회 원본(불변)."""

    start: date
    end: date
    history: StockOrderHistoryCollection


@dataclass(frozen=True, slots=True)
class StockOrderHistorySeries:
    """365일을 넘는 주문 내역을 오래된 기간부터 담은 결과(불변).

    ``orders`` 는 각 기간의 통합 행을 순서대로 연결한다. ``periods`` 의 ``history`` 에
    공급자별 행과 요약을 보존하며 기간 간 평균가격·제비용·합계는 계산하지 않는다.
    """

    orders: tuple[StockOrder, ...]
    periods: tuple[StockOrderHistoryPeriod, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "orders", tuple(self.orders))
        object.__setattr__(self, "periods", tuple(self.periods))
