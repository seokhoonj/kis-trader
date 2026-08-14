"""계좌 잔고(DATA) -- :class:`Position`, :class:`Balance`, :class:`Portfolio`.

:class:`Position` 은 한 종목의 보유 현황, :class:`Balance` 는 계좌 현금·자산 요약,
:class:`Portfolio` 는 그 둘을 한 스냅샷으로 묶은 것이다. 금액은 종목/계좌 통화의 Decimal
(국내는 KRW). 다중통화가 필요해지면 ``Money`` 로 승격한다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from ._internal._freeze import freeze_vendor_payload


@dataclass(frozen=True, slots=True)
class Position:
    """한 종목의 보유 현황(불변).

    ``average_purchase_price`` 는 매입평균단가(취득원가)로, 체결가 평균인
    ``ExecutionReport.average_price`` 와 다른 개념이라 이름을 구분한다. 0수량 잔여 lot(정산
    대기)도 그대로 담긴다 -- 필터는 호출자 몫.
    """

    symbol: str
    security_name: str
    currency: str
    quantity: Decimal
    sellable_quantity: Decimal
    average_purchase_price: Decimal
    purchase_amount: Decimal
    current_price: Decimal
    market_value: Decimal             # 평가금액 = 현재가 x 수량
    unrealized_pnl: Decimal
    unrealized_pnl_percent: Decimal
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class Balance:
    """계좌의 현금·자산 요약(불변).

    ``deposit`` 예수금총액, ``settlement_cash_d1`` / ``settlement_cash_d2`` D+1 / D+2 정산예정 현금
    (D+2가 실질 인출가능), ``net_asset`` 순자산(현금+평가), ``market_value`` 보유 종목 평가금액
    합계, ``total_evaluation`` KIS 총평가금액, ``unrealized_pnl`` 평가손익 합계.
    """

    currency: str
    deposit: Decimal
    settlement_cash_d1: Decimal
    settlement_cash_d2: Decimal
    total_evaluation: Decimal
    net_asset: Decimal
    purchase_amount: Decimal
    market_value: Decimal
    unrealized_pnl: Decimal
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class Portfolio:
    """계좌 스냅샷 -- 현금·자산 요약과 보유 종목 한 벌(무거운 조회를 한 번만)."""

    balance: Balance
    positions: tuple[Position, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "positions", tuple(self.positions))


@dataclass(frozen=True, slots=True)
class AccountAssets:
    """투자계좌 자산현황 요약(불변). 자산군 전반의 총자산·순자산·예수금·대출·외화까지 아우른다.

    :class:`Balance`(주식 잔고 요약)보다 넓은 계좌 전체 관점이다 -- ``total_foreign_evaluation``
    외화평가총액, ``overseas_stock_evaluation`` 해외주식평가금액, ``total_substitute_amount``
    총대용금액, ``total_loan_amount`` 대출금액합계 등을 포함한다. 자산군별 내역(output1)은 계좌
    유형에 따라 항목 순서가 달라 라벨을 단정하지 않고 ``_raw`` 로 남긴다. 금액은 KRW Decimal.
    """

    total_asset_amount: Decimal         # 총자산금액(tot_asst_amt)
    total_net_asset_amount: Decimal     # 순자산총금액(nass_tot_amt)
    total_purchase_amount: Decimal      # 매입금액합계(pchs_amt_smtl)
    total_evaluation_amount: Decimal    # 평가금액합계(evlu_amt_smtl)
    total_evaluation_pnl: Decimal       # 평가손익합계(evlu_pfls_amt_smtl)
    total_loan_amount: Decimal          # 대출금액합계(loan_amt_smtl)
    total_deposit: Decimal              # 총예수금액(tot_dncl_amt)
    deposit: Decimal                    # 예수금액(dncl_amt)
    total_foreign_evaluation: Decimal   # 외화평가총액(frcr_evlu_tota)
    overseas_stock_evaluation: Decimal  # 해외주식평가금액(ovrs_stck_evlu_amt1)
    total_substitute_amount: Decimal    # 총대용금액(tot_sbst_amt)
    today_receivable: Decimal           # 당일미수금액(thdt_rcvb_amt)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))
