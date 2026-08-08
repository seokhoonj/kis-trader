"""해외 계좌 DATA -- :class:`OverseasPosition`, :class:`OverseasBalance`.

해외 잔고가 돌려주는 보유 종목(:meth:`~kis_openapi.client.KISClient.overseas_positions`)과 계좌
손익 요약(:meth:`~kis_openapi.client.KISClient.overseas_balance`). 금액은 종목/조회 통화
(USD/HKD/JPY/...)라 :class:`~kis_openapi.money.Money` 로 통화를 함께 담는다 -- 국내
:class:`~kis_openapi.balance.Position`(KRW Decimal)와 달리 다통화이기 때문이다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from .money import Money


@dataclass(frozen=True, slots=True)
class OverseasCurrentPrice:
    """해외주식 현재체결가의 간결한 가격·누적거래 스냅샷."""

    symbol: str
    exchange: str
    last: Decimal
    previous_close: Decimal
    change: Decimal
    change_percent: Decimal
    previous_volume: int
    volume: int
    traded_amount: Decimal
    decimal_places: int
    buyable_status: str
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class OverseasPosition:
    """해외 보유 종목 한 건(불변). 금액은 종목 통화의 :class:`Money`.

    ``quantity`` 는 보유 수량, ``sellable_quantity`` 는 매도가능 수량. ``unrealized_pnl`` 은 외화
    평가손익, ``pnl_percent`` 는 평가손익률(%).
    """

    symbol: str
    name: str
    exchange: str                     # 조회한 해외거래소코드(OVRS_EXCG_CD)
    quantity: int                     # 보유 수량
    sellable_quantity: int            # 매도가능 수량
    average_price: Money              # 매입 평균가
    current_price: Money              # 현재가
    purchase_amount: Money            # 외화 매입금액
    market_value: Money               # 평가금액
    unrealized_pnl: Money             # 외화 평가손익
    pnl_percent: Decimal              # 평가손익률(%)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class OverseasOpenOrder:
    """해외 미체결 주문 한 건(불변). 브로커 측 미체결 목록이라 우리 ``client_order_id`` 는 없고
    거래소 주문번호(``order_id``)로 식별한다. ``unfilled_quantity`` 는 아직 체결 안 된 잔량."""

    symbol: str
    name: str
    exchange: str                     # 해외거래소코드
    order_id: str                     # 거래소 주문번호(odno)
    side: str                         # buy / sell
    quantity: int                     # 주문수량
    filled_quantity: int              # 체결수량
    unfilled_quantity: int            # 미체결 잔량
    price: Money                      # 주문단가(종목 통화)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class OverseasForeignMargin:
    """통화별 해외증거금 한 건(불변). 계좌의 통화별 외화 예수금·증거금·주문가능금액을 한 줄로.
    금액은 그 통화의 :class:`Money`.

    ``deposit`` 외화예수금액, ``margin_amount`` 외화증거금액, ``receivable_amount`` 외화미수금액,
    ``unsettled_buy_amount``/``unsettled_sell_amount`` 미결제 매수/매도금액, ``general_orderable_amount``
    외화일반주문가능금액, ``orderable_amount`` 외화주문가능금액, ``integrated_orderable_amount``
    통합주문가능금액, ``exchange_rate`` 기준환율.
    """

    country_name: str                 # 국가명(natn_name)
    currency: str                     # 통화코드(crcy_cd)
    deposit: Money                    # 외화예수금액(frcr_dncl_amt1)
    unsettled_buy_amount: Money       # 미결제매수금액(ustl_buy_amt)
    unsettled_sell_amount: Money      # 미결제매도금액(ustl_sll_amt)
    receivable_amount: Money          # 외화미수금액(frcr_rcvb_amt)
    margin_amount: Money              # 외화증거금액(frcr_mgn_amt)
    general_orderable_amount: Money   # 외화일반주문가능금액(frcr_gnrl_ord_psbl_amt)
    orderable_amount: Money           # 외화주문가능금액(frcr_ord_psbl_amt1)
    integrated_orderable_amount: Money  # 통합주문가능금액(itgr_ord_psbl_amt)
    exchange_rate: Decimal            # 기준환율(bass_exrt)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class OverseasTransaction:
    """해외주식 일별 거래내역 한 건(불변). 체결된 매매 한 줄 -- 매매일·결제일·종목·수량·단가·금액·
    수수료. 외화 금액은 그 거래 통화의 :class:`Money`, 원화 수수료는 KRW Decimal.

    ``price`` 체결단가, ``trade_amount`` 거래외화금액, ``settlement_amount`` 외화정산금액,
    ``foreign_fee`` 외화수수료, ``domestic_won_fee``/``overseas_won_fee`` 국내·해외 원화수수료,
    ``loan_type`` 대출구분명(예: 현금).
    """

    trade_date: date | None           # 매매일자(trad_dt)
    settlement_date: date | None      # 결제일자(sttl_dt)
    side: str                         # buy / sell
    symbol: str
    name: str
    quantity: Decimal                 # 체결수량(ccld_qty)
    price: Money                      # 체결단가(ft_ccld_unpr2)
    trade_amount: Money               # 거래외화금액2(tr_frcr_amt2)
    settlement_amount: Money          # 외화정산금액1(frcr_excc_amt_1)
    foreign_fee: Money                # 외화수수료1(frcr_fee1)
    domestic_won_fee: Decimal         # 국내원화수수료(dmst_wcrc_fee), KRW
    overseas_won_fee: Decimal         # 해외원화수수료(ovrs_wcrc_fee), KRW
    currency: str                     # 거래통화코드(crcy_cd)
    loan_type: str                    # 대출구분명(loan_dvsn_name)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class OverseasBuyableAmount:
    """해외주식 매수가능금액(불변). 거래소·종목·주문단가 기준. 금액은 조회 통화의 :class:`Money`.

    ``orderable_amount`` 외화 기준 주문가능금액("외화" 화면), ``max_quantity`` 그 최대 주문가능수량,
    ``integrated_orderable_amount``/``integrated_max_quantity`` 는 원화 통합("통합" 화면) 기준.
    ``orderable_foreign_cash`` 주문가능외화금액, ``reusable_sell_amount`` 매도재사용가능금액,
    ``exchange_rate`` 적용환율. **매수 시 수량단위 절사가 필요**(예: 100주 단위면 545 -> 500).
    """

    symbol: str
    exchange: str                     # 주문 거래소코드(OVRS_EXCG_CD)
    currency: str
    orderable_foreign_cash: Money     # 주문가능외화금액(ord_psbl_frcr_amt)
    reusable_sell_amount: Money       # 매도재사용가능금액(sll_ruse_psbl_amt)
    orderable_amount: Money           # 해외주문가능금액(ovrs_ord_psbl_amt) -- 외화 기준
    max_quantity: Decimal             # 최대주문가능수량(max_ord_psbl_qty) -- 외화 기준
    integrated_orderable_amount: Money  # 외화주문가능금액1(frcr_ord_psbl_amt1) -- 통합 기준
    integrated_max_quantity: Decimal  # 해외최대주문가능수량(ovrs_max_ord_psbl_qty) -- 통합 기준
    exchange_rate: Decimal            # 환율(exrt)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class OverseasBalance:
    """해외 계좌 손익 요약(불변). 조회한 거래소 그룹+통화 기준. 금액은 :class:`Money`.

    ``purchase_amount`` 보유분 매입금액, ``unrealized_pnl`` 평가손익, ``realized_pnl`` 실현손익,
    ``total_pnl`` 총손익(실현+평가), ``return_percent`` 총수익률(%). 예수금(현금)은 별도 조회다.
    """

    exchange: str                     # 조회한 해외거래소코드(OVRS_EXCG_CD)
    purchase_amount: Money            # 외화 매입금액
    unrealized_pnl: Money             # 평가손익
    realized_pnl: Money               # 실현손익
    total_pnl: Money                  # 총손익(실현+평가)
    return_percent: Decimal           # 총수익률(%)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class OverseasSettlementDate:
    """해외 시장별 현지·국내 결제일자 한 건(불변)."""

    market_type_code: str
    country_code: str
    country_name: str
    country_abbr: str
    market_code: str
    market_name: str
    local_settlement_date: date | None
    domestic_settlement_date: date | None
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class OverseasIndustry:
    """해외 거래소의 업종(섹터) 코드 한 건(불변)."""

    code: str
    name: str
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class OverseasIndustryStock:
    """해외 거래소의 한 업종에 속한 종목 시세(불변)."""

    exchange: str
    symbol: str
    name: str
    english_name: str
    last: Decimal
    change: Decimal
    change_percent: Decimal
    volume: int
    ask_price: Decimal
    ask_quantity: int
    bid_price: Decimal
    bid_quantity: int
    rank: int
    is_tradable: bool
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class OverseasStockSearchItem:
    """해외 조건검색 결과 종목."""

    realtime_symbol: str
    exchange: str
    symbol: str
    name: str
    english_name: str
    price: Decimal
    change: Decimal
    change_percent: Decimal
    open: Decimal
    high: Decimal
    low: Decimal
    volume: int
    amount: Decimal
    shares: Decimal
    market_cap: Decimal
    eps: Decimal | None
    per: Decimal | None
    rank: int
    is_tradable: bool
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class OverseasStockSearch:
    """해외 조건검색 건수 요약과 전체 연속조회 결과."""

    exchange: str
    decimal_places: int
    status: str
    total_count: int
    items: tuple[OverseasStockSearchItem, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(self.items))


@dataclass(frozen=True, slots=True)
class OverseasRight:
    """해외증권의 배당·증자·합병 등 기간별 권리."""

    base_date: date | None
    right_type_code: str
    symbol: str
    name: str
    product_type: str
    standard_symbol: str
    local_base_date: date | None
    subscription_start_date: date | None
    subscription_end_date: date | None
    cash_allocation_rate: Decimal | None
    stock_allocation_rate: Decimal | None
    currencies: tuple[str, ...]
    allocation_price: Decimal | None
    dividends_per_share: tuple[Decimal | None, ...]
    is_final: bool
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "currencies", tuple(self.currencies))
        object.__setattr__(self, "dividends_per_share", tuple(self.dividends_per_share))
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class OverseasCorporateAction:
    """외부 권리정보 제공사가 집계한 해외종목 기업행사 일정."""

    announced_date: date | None
    title: str
    ex_dividend_date: date | None
    payment_date: date | None
    record_date: date | None
    validity_date: date | None
    local_deadline: date | None
    ex_rights_date: date | None
    delisting_date: date | None
    redemption_date: date | None
    early_redemption_date: date | None
    effective_date: date | None
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class OverseasNewsHeadline:
    """해외뉴스 종합 피드의 제목 한 건."""

    news_type: str
    key: str
    timestamp: datetime
    category_code: str
    category_name: str
    source: str
    country_code: str
    exchange_code: str
    symbol: str
    symbol_name: str
    title: str
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class OverseasCollateralStock:
    """해외주식 담보대출 가능 여부와 적용 비율."""

    symbol: str
    name: str
    loan_rate: Decimal | None
    maintenance_rate: Decimal | None
    collateral_rate: Decimal | None
    is_loanable: bool
    registered_date: date | None
    market_name: str
    currency: str
    country_name: str
    exchange: str
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))
