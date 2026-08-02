"""국내주식 잔고(DATA) -- :class:`Position`, :class:`Balance`, :class:`Portfolio`, 파서.

:class:`Position` 은 한 종목의 보유 현황(수량/평단/평가/손익), :class:`Balance` 는 계좌의
현금·자산 요약(예수금/정산금/순자산 등), :class:`Portfolio` 는 그 둘을 한 스냅샷으로 묶은
것이다. 한 번의 잔고조회 응답이 종목 배열(``output1``)과 계좌 요약(``output2``)을 함께 준다.

금액은 전부 원화(KRW) :class:`~decimal.Decimal` 이다. 다중통화(해외 계좌)가 필요해지면 그때
``Money``(금액+통화) 타입으로 승격한다 -- 도메스틱만 다루는 지금은 통화가 하나뿐이라 불필요.

KIS URL/TR-id:
- 주식잔고조회: ``GET /uapi/domestic-stock/v1/trading/inquire-balance``
  실전 ``TTTC8434R`` / 모의 ``VTTC8434R`` (모의 지원). ``output1`` 종목 배열, ``output2`` 요약.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from ..._wire import required_decimal


@dataclass(frozen=True, slots=True)
class Position:
    """한 종목의 보유 현황(불변). 수량/금액은 KRW Decimal.

    ``average_purchase_price`` 는 매입평균단가(취득원가 기준)로, 체결가 평균인
    ``ExecutionReport.average_price`` 와는 다른 개념이라 이름을 구분한다. 0수량 잔여 lot
    (정산 대기)도 그대로 담는다 -- KIS가 D+2까지 남기므로 필터는 호출자 몫.
    """

    symbol: str                       # pdno
    security_name: str                # prdt_name(종목명)
    currency: str                     # 국내는 항상 KRW
    quantity: Decimal                 # hldg_qty(보유수량)
    sellable_quantity: Decimal        # ord_psbl_qty(주문가능=매도가능 수량)
    average_purchase_price: Decimal   # pchs_avg_pric(매입평균가)
    purchase_amount: Decimal          # pchs_amt(매입금액)
    current_price: Decimal            # prpr(현재가)
    market_value: Decimal             # evlu_amt(평가금액 = 현재가 x 수량)
    unrealized_pnl: Decimal           # evlu_pfls_amt(평가손익금액)
    unrealized_pnl_percent: Decimal   # evlu_pfls_rt(평가손익률)
    # raw 는 동등성/해시/repr 제외 -- 값 동일성은 파싱된 필드로, dict 는 unhashable 이라
    # 포함하면 frozen 인데도 hash() 가 TypeError 를 낸다.
    raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "raw", MappingProxyType(dict(self.raw)))


@dataclass(frozen=True, slots=True)
class Balance:
    """계좌의 현금·자산 요약(불변). 금액은 KRW Decimal.

    ``deposit`` 은 예수금 총액, ``settlement_d1`` / ``settlement_d2`` 는 D+1 / D+2 정산 예정
    현금(D+2가 실질 인출가능 예수금), ``net_asset`` 은 순자산(현금+평가), ``market_value`` 는
    보유 종목 평가금액 합계, ``total_evaluation`` 은 KIS 총평가금액, ``unrealized_pnl`` 은
    평가손익 합계다.
    """

    currency: str                     # 국내는 항상 KRW
    deposit: Decimal                  # dnca_tot_amt(예수금총액)
    settlement_d1: Decimal            # nxdy_excc_amt(익일정산금 D+1)
    settlement_d2: Decimal            # prvs_rcdl_excc_amt(가수도정산금 D+2)
    total_evaluation: Decimal         # tot_evlu_amt(총평가금액)
    net_asset: Decimal                # nass_amt(순자산금액)
    purchase_amount: Decimal          # pchs_amt_smtl_amt(매입금액 합계)
    market_value: Decimal             # evlu_amt_smtl_amt(평가금액 합계)
    unrealized_pnl: Decimal           # evlu_pfls_smtl_amt(평가손익 합계)
    raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "raw", MappingProxyType(dict(self.raw)))


@dataclass(frozen=True, slots=True)
class Portfolio:
    """계좌 스냅샷 -- 현금·자산 요약(:class:`Balance`)과 보유 종목(:class:`Position`) 한 벌.

    한 번의 잔고조회로 둘 다 얻는다(무거운 TR을 두 번 치지 않는다).
    """

    balance: Balance
    positions: tuple[Position, ...]


def parse_positions(rows: Sequence[Mapping[str, Any]]) -> list[Position]:
    """잔고 ``output1`` 행들을 :class:`Position` 리스트로(순수).

    종목코드(``pdno``)가 빈 행(KIS 패딩)은 건너뛴다. 그 외 필수 수치가 비거나 파싱 실패면
    :class:`~kis_openapi.errors.KisError` 로 fail-closed 한다(잘못된 수량/금액을 지어내지 않음).
    """
    positions: list[Position] = []
    for row in rows:
        symbol = str(row.get("pdno", "")).strip()
        if not symbol:  # 종목코드 없는 패딩 행 -- 건너뜀
            continue
        positions.append(
            Position(
                symbol=symbol,
                security_name=str(row.get("prdt_name", "")).strip(),
                currency="KRW",
                quantity=required_decimal(row.get("hldg_qty"), "hldg_qty"),
                sellable_quantity=required_decimal(row.get("ord_psbl_qty"), "ord_psbl_qty"),
                average_purchase_price=required_decimal(row.get("pchs_avg_pric"), "pchs_avg_pric"),
                purchase_amount=required_decimal(row.get("pchs_amt"), "pchs_amt"),
                current_price=required_decimal(row.get("prpr"), "prpr"),
                market_value=required_decimal(row.get("evlu_amt"), "evlu_amt"),
                unrealized_pnl=required_decimal(row.get("evlu_pfls_amt"), "evlu_pfls_amt"),
                unrealized_pnl_percent=required_decimal(row.get("evlu_pfls_rt"), "evlu_pfls_rt"),
                raw=row,
            )
        )
    return positions


def parse_balance(summary: Mapping[str, Any]) -> Balance:
    """잔고 ``output2`` 요약 객체를 :class:`Balance` 로(순수). 수치 파싱은 fail-closed."""
    return Balance(
        currency="KRW",
        deposit=required_decimal(summary.get("dnca_tot_amt"), "dnca_tot_amt"),
        settlement_d1=required_decimal(summary.get("nxdy_excc_amt"), "nxdy_excc_amt"),
        settlement_d2=required_decimal(summary.get("prvs_rcdl_excc_amt"), "prvs_rcdl_excc_amt"),
        total_evaluation=required_decimal(summary.get("tot_evlu_amt"), "tot_evlu_amt"),
        net_asset=required_decimal(summary.get("nass_amt"), "nass_amt"),
        purchase_amount=required_decimal(summary.get("pchs_amt_smtl_amt"), "pchs_amt_smtl_amt"),
        market_value=required_decimal(summary.get("evlu_amt_smtl_amt"), "evlu_amt_smtl_amt"),
        unrealized_pnl=required_decimal(summary.get("evlu_pfls_smtl_amt"), "evlu_pfls_smtl_amt"),
        raw=summary,
    )
