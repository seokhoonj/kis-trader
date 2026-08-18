"""국내선물옵션 계좌 잔고(DATA) -- :class:`DerivativePosition`, :class:`DerivativeBalance`.

:class:`DerivativePosition` 은 한 종목(선물/옵션)의 보유 현황, :class:`DerivativeBalance` 는
계좌 예수금·증거금·손익 요약과 보유내역 한 벌이다. 금액·수량은 KRW Decimal(선물옵션은 원화).
계좌 요약(output2)에는 여기 타입화하지 않은 필드가 많아 원본 요약을 ``raw`` 로 함께 노출한다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from ..._internal._freeze import freeze_vendor_payload


@dataclass(frozen=True, slots=True)
class DerivativePosition:
    """한 선물옵션 종목의 보유 현황(불변).

    ``symbol`` 은 주문 API 와 왕복하는 단축상품번호(shtn_pdno, 예 "101W09"), ``isin`` 은 표준
    상품번호(pdno, 예 "KR4101RC0000")다. ``side`` 는 매도매수구분명을 그대로(strip) 담는다
    -- 청산 등으로 빈 값일 수 있다. 청산 완료된 0수량 잔여 lot 도 그대로 담기며, 필터는 호출자 몫.
    """

    symbol: str                       # 단축상품번호(shtn_pdno)
    isin: str                         # 상품번호(pdno)
    name: str                         # 상품명(prdt_name)
    side: str                         # 매도매수구분명(sll_buy_dvsn_name)
    quantity: Decimal                 # 잔고수량(cblc_qty)
    settle_price: Decimal             # 정산단가(excc_unpr)
    avg_price: Decimal                # 체결평균단가1(ccld_avg_unpr1)
    purchase_amount: Decimal          # 매입금액(pchs_amt)
    market_value: Decimal             # 평가금액(evlu_amt)
    unrealized_pnl: Decimal           # 평가손익금액(evlu_pfls_amt)
    realized_pnl: Decimal             # 매매손익금액(trad_pfls_amt)
    liquidatable_quantity: Decimal    # 청산가능수량(lqd_psbl_qty)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class DerivativeBalance:
    """선물옵션 계좌의 예수금·증거금·손익 요약과 보유내역 한 벌(불변).

    ``total_deposit`` 총예수금액, ``deposit_cash`` 예수금현금, ``total_margin`` 증거금총액,
    ``available_cash`` / ``available_total`` 주문가능현금/총액, ``account_value`` 추정예탁자산금액.
    평가·매매손익은 합계(``total_*``)와 선물/옵션 분해(``futures_*`` / ``options_*``)를 함께 담는다.
    타입화하지 않은 요약 필드는 ``raw`` 로 접근한다.
    """

    positions: tuple[DerivativePosition, ...]  # 보유내역(output1)
    total_deposit: Decimal                # 총예수금액(tot_dncl_amt)
    deposit_cash: Decimal                 # 예수금현금(dnca_cash)
    total_margin: Decimal                 # 증거금총액(mgna_tota)
    available_cash: Decimal               # 주문가능현금(ord_psbl_cash)
    available_total: Decimal              # 주문가능총액(ord_psbl_tota)
    total_unrealized_pnl: Decimal         # 평가손익금액합계(evlu_pfls_amt_smtl)
    total_realized_pnl: Decimal           # 매매손익금액합계(trad_pfls_amt_smtl)
    futures_unrealized_pnl: Decimal       # 선물평가손익금액(futr_evlu_pfls_amt)
    options_unrealized_pnl: Decimal       # 옵션평가손익금액(opt_evlu_pfls_amt)
    futures_realized_pnl: Decimal         # 선물매매손익금액(futr_trad_pfls_amt)
    options_realized_pnl: Decimal         # 옵션매매손익금액(opt_trad_pfls_amt)
    account_value: Decimal                # 추정예탁자산금액(prsm_dpast_amt)
    raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "positions", tuple(self.positions))
        object.__setattr__(self, "raw", freeze_vendor_payload(self.raw))


@dataclass(frozen=True, slots=True)
class DerivativeValuationPosition:
    """한 선물옵션 종목의 잔고평가손익 현황(불변).

    :class:`DerivativePosition` 과 같은 종목 식별(``symbol``/``isin``/``name``/``side``)에 더해
    종목별 평가/매매 손익을 담는다. ``symbol`` 은 주문 API 와 왕복하는 단축상품번호(shtn_pdno,
    예 "101W09"), ``isin`` 은 표준 상품번호(pdno, 예 "KR4101RC0000")다. ``quantity`` 는 잔고수량1
    (cblc_qty1) -- 잔고 조회의 cblc_qty 와 필드명이 다르다. 청산 완료된 0수량 잔여 lot 도 그대로
    담기며, 필터는 호출자 몫.
    """

    symbol: str                       # 단축상품번호(shtn_pdno)
    isin: str                         # 상품번호(pdno)
    name: str                         # 상품명(prdt_name)
    side: str                         # 매도매수구분명(sll_buy_dvsn_name)
    quantity: Decimal                 # 잔고수량1(cblc_qty1)
    settle_price: Decimal             # 정산단가(excc_unpr)
    avg_price: Decimal                # 체결평균단가1(ccld_avg_unpr1)
    index_close: Decimal              # 지수종가(idx_clpr)
    purchase_amount: Decimal          # 매입금액(pchs_amt)
    market_value: Decimal             # 평가금액(evlu_amt)
    unrealized_pnl: Decimal           # 평가손익금액(evlu_pfls_amt)
    realized_pnl: Decimal             # 매매손익금액(trad_pfls_amt)
    liquidatable_quantity: Decimal    # 청산가능수량(lqd_psbl_qty)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class DerivativeValuationBalance:
    """선물옵션 잔고평가손익 뷰 -- 계좌 예수금·증거금·손익 요약과 보유내역 한 벌(불변).

    :class:`DerivativeBalance` 와 같은 요약 필드 집합을 담되, 보유내역은 종목별 평가/매매 손익을
    실은 :class:`DerivativeValuationPosition` 이다. ``total_deposit`` 총예수금액, ``deposit_cash``
    예수금현금, ``total_margin`` 증거금총액, ``available_cash`` / ``available_total`` 주문가능현금/총액,
    ``account_value`` 추정예탁자산금액. 평가·매매손익은 합계(``total_*``)와 선물/옵션 분해
    (``futures_*`` / ``options_*``)를 함께 담는다. 타입화하지 않은 요약 필드는 ``raw`` 로 접근한다.
    """

    positions: tuple[DerivativeValuationPosition, ...]  # 보유내역(output1)
    total_deposit: Decimal                # 총예수금액(tot_dncl_amt)
    deposit_cash: Decimal                 # 예수금현금(dnca_cash)
    total_margin: Decimal                 # 증거금총액(mgna_tota)
    available_cash: Decimal               # 주문가능현금(ord_psbl_cash)
    available_total: Decimal              # 주문가능총액(ord_psbl_tota)
    total_unrealized_pnl: Decimal         # 평가손익금액합계(evlu_pfls_amt_smtl)
    total_realized_pnl: Decimal           # 매매손익금액합계(trad_pfls_amt_smtl)
    futures_unrealized_pnl: Decimal       # 선물평가손익금액(futr_evlu_pfls_amt)
    options_unrealized_pnl: Decimal       # 옵션평가손익금액(opt_evlu_pfls_amt)
    futures_realized_pnl: Decimal         # 선물매매손익금액(futr_trad_pfls_amt)
    options_realized_pnl: Decimal         # 옵션매매손익금액(opt_trad_pfls_amt)
    account_value: Decimal                # 추정예탁자산금액(prsm_dpast_amt)
    raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "positions", tuple(self.positions))
        object.__setattr__(self, "raw", freeze_vendor_payload(self.raw))


@dataclass(frozen=True, slots=True)
class DerivativeDeposit:
    """선물옵션 총자산현황 -- 예수금·주문가능·위탁증거금·손익 요약(불변).

    ``total_deposit`` 예수금총액, ``available_cash`` / ``available_total`` 주문가능현금/총액,
    ``brokerage_margin_cash`` / ``brokerage_margin_substitute`` 위탁증거금현금/대용,
    ``maintenance_ratio`` 유지비율, ``account_value`` 추정예탁자산금액, ``receivable`` 미수금.
    평가·매매손익은 합계(``total_*``)와 선물/옵션 분해(``futures_*`` / ``options_*``)를 함께 담는다.
    이 엔드포인트에 증거금총액(mgna_tota)은 없다 -- 위탁증거금 현금/대용을 대신 노출한다.
    타입화하지 않은 요약 필드는 ``raw`` 로 접근한다.
    """

    total_deposit: Decimal                # 예수금총액(dnca_tota)
    available_cash: Decimal               # 주문가능현금(ord_psbl_cash)
    available_total: Decimal              # 주문가능총액(ord_psbl_tota)
    brokerage_margin_cash: Decimal        # 위탁증거금현금(brkg_mgna_cash)
    brokerage_margin_substitute: Decimal  # 위탁증거금대용(brkg_mgna_sbst)
    maintenance_ratio: Decimal            # 유지비율(mtnc_rt)
    total_unrealized_pnl: Decimal         # 평가손익합계(evlu_pfls_smtl)
    total_realized_pnl: Decimal           # 매매손익합계(trad_pfls_smtl)
    futures_unrealized_pnl: Decimal       # 선물평가손익금액(futr_evlu_pfls_amt)
    options_unrealized_pnl: Decimal       # 옵션평가손익금액(opt_evlu_pfls_amt)
    futures_realized_pnl: Decimal         # 선물매매손익(futr_trad_pfls)
    options_realized_pnl: Decimal         # 옵션매매손익금액(opt_trad_pfls_amt)
    account_value: Decimal                # 추정예탁자산금액(prsm_dpast_amt)
    receivable: Decimal                   # 미수금(rcva)
    raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "raw", freeze_vendor_payload(self.raw))
