"""기간별 계좌 권리현황(DATA) -- :class:`AccountRight`.

계좌 보유종목에 발생한 권리(유상·무상 증자 배정, 배당, 상환 등)를 기간별로 조회한 한 건이다.
시장 전체 일정을 보는 :mod:`~kis_trader.calendar_items`(예탁결제원)와 달리, 이 계좌에 실제로
배정/신청/환불된 권리 내역을 담는다. 금액은 KRW Decimal.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from ._internal._freeze import freeze_vendor_payload


@dataclass(frozen=True, slots=True)
class AccountRight:
    """계좌 권리현황 한 건(불변).

    ``right_type_code`` 권리유형코드(01 유상 등), ``record_date`` 기준일자, ``allocated_quantity``
    최종배정수량, ``total_allocated_quantity`` 총배정수량, ``allocated_amount`` 최종배정금액,
    ``subscription_price`` 청약단가, ``requested_quantity``/``requested_amount`` 신청 수량/금액,
    ``refund_amount`` 환불금액, ``tax_amount`` 세금금액. 각 일자는 순수 :class:`~datetime.date`
    (없으면 None). 유형/상태 코드의 세부 의미는 원본(``_raw``)을 함께 본다.
    """

    account_number: str               # 계좌번호10(acno10)
    right_type_code: str              # 권리유형코드(rght_type_cd)
    record_date: date | None          # 기준일자(bass_dt)
    symbol: str                       # 상품번호(pdno)
    short_symbol: str                 # 단축상품번호(shtn_pdno)
    name: str                         # 상품명(prdt_name)
    balance_quantity: Decimal         # 잔고수량(cblc_qty)
    allocated_quantity: Decimal       # 최종배정수량(last_alct_qty)
    excess_allocated_quantity: Decimal  # 초과배정수량(excs_alct_qty)
    total_allocated_quantity: Decimal  # 총배정수량(tot_alct_qty)
    allocated_amount: Decimal         # 최종배정금액(last_alct_amt)
    subscription_price: Decimal       # 청약단가(sbsc_unpr)
    requested_quantity: Decimal       # 신청수량(rqst_qty)
    requested_amount: Decimal         # 신청금액(rqst_amt)
    request_date: date | None         # 신청일자(rqst_dt)
    subscription_end_date: date | None  # 청약종료일자(sbsc_end_dt)
    listing_date: date | None         # 상장일자(lstg_dt)
    cash_payment_date: date | None    # 현금지급일자(cash_dfrm_dt)
    refund_date: date | None          # 환불일자(rfnd_dt)
    refund_amount: Decimal            # 환불금액(rfnd_amt)
    tax_amount: Decimal               # 세금금액(tax_amt)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))
