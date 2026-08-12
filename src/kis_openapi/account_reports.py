"""계좌 리포트 DATA -- 실현손익 잔고(:class:`RealizedProfitBalance`)와 통합증거금
(:class:`IntegratedMargin`).

:class:`RealizedProfitBalance` 는 HTS [0800] 국내 체결기준잔고(실현손익 포함)를, :class:`IntegratedMargin`
은 원화+외화를 하나로 본 주식통합증거금 현황을 담는다. 둘 다 국내주식 네임스페이스의 조회 전용
스냅샷이며 **모의투자 미지원**이다. 금액은 KRW Decimal.

.. note::
   두 타입 모두 KIS 응답예시로 필드 전량을 확증하지 못했다(통합증거금은 예시에 있으나 레이아웃
   표에 없는 홍콩위안화 재사용 필드가 있고, 실현손익 잔고는 예시가 output1 에서 잘려 output2 요약
   필드가 레이아웃 기준이다). 확증되지 않은 필드는 실제 응답과 다를 수 있으므로 전체 원본은 ``_raw``
   로 함께 노출한다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True, slots=True)
class RealizedProfitPosition:
    """실현손익 잔고의 보유 종목 한 줄(불변). 신용 보유분은 ``loan_date``/``loan_amount``/
    ``expiry_date`` 가 채워진다. 금액은 KRW Decimal."""

    symbol: str                        # 상품번호(pdno)
    name: str                          # 상품명(prdt_name)
    trade_type: str                    # 매매구분명(trad_dvsn_name)
    holding_quantity: Decimal          # 보유수량(hldg_qty)
    orderable_quantity: Decimal        # 주문가능수량(ord_psbl_qty)
    average_purchase_price: Decimal    # 매입평균가격(pchs_avg_pric)
    purchase_amount: Decimal           # 매입금액(pchs_amt)
    current_price: Decimal             # 현재가(prpr)
    market_value: Decimal              # 평가금액(evlu_amt)
    unrealized_pnl: Decimal            # 평가손익금액(evlu_pfls_amt)
    unrealized_pnl_rate: Decimal       # 평가손익율(evlu_pfls_rt)
    loan_date: date | None             # 대출일자(loan_dt)
    loan_amount: Decimal               # 대출금액(loan_amt)
    expiry_date: date | None           # 만기일자(expd_dt)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class RealizedProfitBalance:
    """실현손익 포함 국내 체결기준잔고(불변). ``positions`` 는 보유 종목, 나머지는 계좌 요약이며
    ``realized_pnl``/``real_eval_pnl`` 이 이 조회의 핵심 필드다. 금액은 KRW Decimal.

    .. note:: 요약 필드는 KIS 응답예시로 확증되지 않았다(레이아웃 기준). 전체 원본은 ``_raw``.
    """

    positions: tuple[RealizedProfitPosition, ...]
    deposit_total: Decimal             # 예수금총금액(dnca_tot_amt)
    net_asset: Decimal                 # 순자산금액(nass_amt)
    total_value: Decimal               # 총평가금액(tot_evlu_amt)
    purchase_total: Decimal            # 매입금액합계금액(pchs_amt_smtl_amt)
    evaluation_total: Decimal          # 평가금액합계금액(evlu_amt_smtl_amt)
    evaluation_pnl_total: Decimal      # 평가손익합계금액(evlu_pfls_smtl_amt)
    asset_change: Decimal              # 자산증감액(asst_icdc_amt)
    asset_change_rate: Decimal         # 자산증감수익율(asst_icdc_erng_rt)
    realized_pnl: Decimal              # 실현손익(rlzt_pfls)
    realized_return_rate: Decimal      # 실현수익율(rlzt_erng_rt)
    real_eval_pnl: Decimal             # 실평가손익(real_evlu_pfls)
    real_eval_return_rate: Decimal     # 실평가손익수익율(real_evlu_pfls_erng_rt)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class IntegratedMargin:
    """주식통합증거금 현황(불변) -- 원화와 외화(USD/HKD/JPY/CNY)를 하나로 본 주문가능금액 스냅샷.
    많은 필드 중 핵심만 타입으로 노출하고 전체(레이아웃 100여 개)는 ``_raw`` 로 준다. 금액은 KRW
    Decimal, 환율은 통화당 최초고시환율(원/외화).

    .. note:: 전체 필드가 방대해 headline 만 타입화했다. 통화별 세부(재사용·타시장·현금비율별 한도
       등)는 ``_raw`` 참조.
    """

    account_margin_rate: Decimal       # 계좌증거금율(acmga_rt)
    cash_orderable: Decimal            # 주식현금주문가능금액(stck_cash_ord_psbl_amt)
    substitute_orderable: Decimal      # 주식대용주문가능금액(stck_sbst_ord_psbl_amt)
    receivable: Decimal                # 미수금액(rcvb_amt)
    limit_amount: Decimal              # 한도금액(lmt_amt)
    integrated_margin_type: str        # 해외주식통합증거금구분명(ovrs_stck_itgr_mgna_dvsn_name)
    usd_orderable: Decimal             # 미화통합주문가능금액(usd_itgr_ord_psbl_amt)
    hkd_orderable: Decimal             # 홍콩달러통합주문가능금액(hkd_itgr_ord_psbl_amt)
    jpy_orderable: Decimal             # 엔화통합주문가능금액(jpy_itgr_ord_psbl_amt)
    cny_orderable: Decimal             # 위안화통합주문가능금액(cny_itgr_ord_psbl_amt)
    usd_exchange_rate: Decimal         # 미국달러최초고시환율(usd_frst_bltn_exrt)
    hkd_exchange_rate: Decimal         # 홍콩달러최초고시환율(hkd_frst_bltn_exrt)
    jpy_exchange_rate: Decimal         # 일본엔화최초고시환율(jpy_frst_bltn_exrt)
    cny_exchange_rate: Decimal         # 중국위안화최초고시환율(cny_frst_bltn_exrt)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))
