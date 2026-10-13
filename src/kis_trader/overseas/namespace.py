"""해외 자산군 네임스페이스 -- ``kis.overseas`` (:class:`OverseasNamespace`) 와 그 계좌 하위
(:class:`OverseasAccount`, ``kis.account.overseas``).

세션 :class:`~kis_trader.client.KISClient` 아래 해외 주식·지수·파생의 시세/계좌/순위/뉴스 행위를
모은다. 각 메서드는 세션이 쥔 전송/계좌/환경으로 해외 엔진을 직접 호출한다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, overload

from ..errors import KISUsageError
from ._engine import account as overseas_account
from ._engine import derivatives as overseas_derivatives_api
from ._engine import market_data as overseas_market_data_api
from ._engine import orders as overseas_orders_api
from ._engine import reference as overseas_reference_api
from ._engine import reserved_orders as overseas_reserved_orders_api
from .derivative import OverseasDerivative
from .entities.search import OverseasStockSearch
from .index import OverseasIndex
from .ranking import OverseasRankingQueries
from .stock import OverseasStock

#: "US" 통합 검색이 훑는 미국 거래소코드(나스닥/뉴욕/아멕스) -- KIS 조건검색 TR 은 거래소당 한 번이라
#: search_stocks("US") 가 이 셋을 각각 조회해 합친다.
_US_EXCHANGES = ("NAS", "NYS", "AMS")

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date

    from .._literals import DerivativeProductFilter, Numeric, SideFilter
    from ..client import KISClient
    from ..news import NewsHeadline
    from ..quote import Quote
    from .entities.account import (
        OverseasBuyableAmount,
        OverseasForeignMargin,
        OverseasPeriodProfit,
        OverseasTransaction,
    )
    from .entities.balance import (
        OverseasBalance,
        OverseasPosition,
        OverseasPresentBalance,
        OverseasSettlementBalance,
    )
    from .entities.collateral import OverseasCollateralStockSearch
    from .entities.corporate_action import OverseasCorporateAction, OverseasRight
    from .entities.derivative import (
        OverseasDerivativeDetail,
        OverseasDerivativeMarketHours,
        OverseasFuturesOpenInterest,
    )
    from .entities.industry import OverseasIndustry, OverseasIndustryStock
    from .entities.news import OverseasNewsHeadline
    from .entities.orders import (
        OverseasAlgoExecution,
        OverseasAlgoOrder,
        OverseasOpenOrder,
        OverseasReservedOrder,
    )
    from .entities.product import OverseasProductInfo
    from .entities.settlement import OverseasSettlementDate

# 해외 지수류 kind -> FID_COND_MRKT_DIV_CODE. ``kis.overseas.index`` 가 쓴다.
_OVERSEAS_INDEX_KIND = {"index": "N", "fx": "X", "bond": "I", "gold": "S"}

#: 체결기준현재잔고(present_balance) 국가 필터. 엔진이 국가코드(840 등)로 변환하며, 그 외 값은 거부한다.
BalanceNation = Literal["all", "US", "HK", "CN", "JP", "VN"]

#: 잔고 조회구분(present_balance/settlement_balance 공통). 엔진이 INQR_DVSN_CD(00/01/02)로 변환한다.
#: ``"all"`` 전체 / ``"general"`` 일반해외주식 / ``"ministock"`` 미니스탁(KIS 소수점 거래 상품).
BalanceInquiry = Literal["all", "general", "ministock"]


class OverseasAccount:
    """``kis.account.overseas`` -- 해외 계좌 조회·계좌 단위 주문(잔고/손익/알고/예약주문).

    모든 메서드는 계좌 미설정 시 :class:`~kis_trader.errors.KISUsageError` 를 던진다. ``**모의투자
    미지원**`` 이라 표시된 메서드는 ``environment="paper"`` 에서도 :class:`~kis_trader.errors.
    KISUsageError` 다. 조회 실패·응답 부재·파싱 실패는 :class:`~kis_trader.errors.KISError`.
    """

    def __init__(self, client: KISClient) -> None:
        self._client = client

    def positions(self, *, market: str | None = None) -> list[OverseasPosition]:
        """보유 종목(거래소 그룹+통화별). ``market`` = ``"US"``/``"HK"``/``"CN_SH"``/``"CN_SZ"``/``"JP"``/
        ``"VN_HN"``/``"VN_HCM"``, 생략(``None``)하면 **전체 시장 그룹을 순회해 합친다**. 금액은 종목 통화의
        :class:`~kis_trader.money.Money`."""
        cano, product_code = self._client._require_account()
        return overseas_account.fetch_positions(
            self._client.transport, cano=cano, product_code=product_code,
            environment=self._client.environment, market=market,
        )

    @overload
    def balance(self, *, market: str) -> OverseasBalance: ...
    @overload
    def balance(self, *, market: None = ...) -> list[OverseasBalance]: ...
    def balance(self, *, market: str | None = None) -> OverseasBalance | list[OverseasBalance]:
        """계좌 손익 요약(시장/통화별) -- 매입금액·평가/실현/총손익·총수익률을 :class:`~kis_trader.money.Money`
        로. ``market`` 을 주면 그 시장 하나(:class:`~kis_trader.overseas.entities.balance.OverseasBalance`),
        생략(``None``)하면 **전체 시장을 순회해 시장별 요약 리스트**를 준다(통화가 시장마다 달라 하나로
        합칠 수 없으므로 나열; :meth:`positions`/:meth:`open_orders` 와 동일 arity). 전체 종합은
        :meth:`present_balance`, 예수금(현금)은 별도다."""
        cano, product_code = self._client._require_account()
        return overseas_account.fetch_balance(
            self._client.transport, cano=cano, product_code=product_code,
            environment=self._client.environment, market=market,
        )

    def buyable(self, symbol: str, *, exchange: str, price: Numeric) -> OverseasBuyableAmount:
        """매수가능금액. ``exchange`` 는 시세 거래소코드(NAS/NYS/AMS/HKS/SHS/SZS/TSE/HNX/HSX), ``price`` 는
        의도한 주문단가. 외화·통합 기준 주문가능금액·최대수량을 :class:`~kis_trader.money.Money` 로 준다.
        **매수 시 수량단위 절사가 필요**하다."""
        cano, product_code = self._client._require_account()
        return overseas_account.fetch_buyable_amount(
            self._client.transport, cano=cano, product_code=product_code,
            environment=self._client.environment, symbol=symbol, exchange=exchange, price=price,
        )

    def foreign_margin(self) -> list[OverseasForeignMargin]:
        """통화별 외화 예수금·증거금·주문가능금액. 금액은 각 통화의 :class:`~kis_trader.money.Money`.
        **모의투자 미지원**."""
        cano, product_code = self._client._require_account()
        return overseas_account.fetch_foreign_margin(
            self._client.transport, cano=cano, product_code=product_code, environment=self._client.environment
        )

    def present_balance(
        self, *, won_basis: bool = True, nation: BalanceNation = "all", market_code: str = "00",
        inquiry: BalanceInquiry = "all",
    ) -> OverseasPresentBalance:
        """체결기준현재잔고 -- 보유 종목·통화별 예수금·계좌 요약. ``won_basis`` 원화(True)/외화(False),
        ``nation`` 국가(``"all"``/``"US"``/``"HK"``/``"CN"``/``"JP"``/``"VN"``), ``market_code`` 거래시장코드
        (``"00"``=전체), ``inquiry`` 조회구분(``"all"``=전체/``"general"``=일반 해외주식/``"ministock"``=미니스탁). 모의는 요약만 온다.

        .. note:: 요약 필드는 KIS 예시가 잘려 레이아웃 기준이다 -- 전체 원본은 ``_raw``."""
        cano, product_code = self._client._require_account()
        return overseas_account.fetch_present_balance(
            self._client.transport, cano=cano, product_code=product_code, environment=self._client.environment,
            won_basis=won_basis, nation=nation, market_code=market_code, inquiry=inquiry,
        )

    def settlement_balance(
        self, *, basis_date: str, won_basis: bool = True, inquiry: BalanceInquiry = "all"
    ) -> OverseasSettlementBalance:
        """결제기준잔고 -- ``basis_date``(YYYYMMDD) 결제 기준의 보유 종목·통화별 예수금·계좌 요약.
        ``won_basis`` 원화(True)/외화(False), ``inquiry`` 조회구분(``"all"``=전체/``"general"``=일반 해외주식/``"ministock"``=미니스탁).
        **모의투자 미지원**."""
        cano, product_code = self._client._require_account()
        return overseas_account.fetch_settlement_balance(
            self._client.transport, cano=cano, product_code=product_code, environment=self._client.environment,
            basis_date=basis_date, won_basis=won_basis, inquiry=inquiry,
        )

    def period_profit(
        self, *, start: str, end: str, exchange: str = "", nation: str = "", currency: str = "",
        symbol: str = "", won_basis: bool = False,
    ) -> OverseasPeriodProfit:
        """기간손익 -- ``start``~``end``(YYYYMMDD) 매도청산 종목별 실현손익과 총계. ``exchange`` 거래소
        (공란=전체), ``nation`` 국가코드(NATN_CD, 공란=전체), ``currency`` 통화(공란=전체), ``symbol`` 종목
        (공란=전체), ``won_basis`` 원화(True)/외화(False). **모의투자 미지원**.

        .. note:: KIS 예시가 비어 있어 필드는 레이아웃 기준이다 -- 전체 원본은 각 행/결과의 ``_raw``."""
        cano, product_code = self._client._require_account()
        return overseas_account.fetch_period_profit(
            self._client.transport, cano=cano, product_code=product_code, environment=self._client.environment,
            start=start, end=end, exchange=exchange, nation=nation, currency=currency,
            symbol=symbol, won_basis=won_basis,
        )

    def transactions(
        self, *, start: str, end: str, symbol: str | None = None, side: SideFilter = "all"
    ) -> list[OverseasTransaction]:
        """일별 거래내역(매매·결제·수수료). ``start``/``end`` 는 등록일자 기간(YYYYMMDD), ``symbol`` 없으면
        전체, ``side`` = ``"all"``/``"sell"``/``"buy"``. 외화 금액은 거래 통화의 :class:`~kis_trader.money.Money`.
        **모의투자 미지원**."""
        cano, product_code = self._client._require_account()
        return overseas_account.fetch_transactions(
            self._client.transport, cano=cano, product_code=product_code,
            environment=self._client.environment, start=start, end=end, symbol=symbol, side=side,
        )

    def open_orders(self, *, market: str | None = None) -> list[OverseasOpenOrder]:
        """미체결(열린) 주문. 거래소 주문번호·미체결 잔량을 준다. ``market`` 생략(``None``)하면 **전체 시장
        그룹을 순회해 합친다**. **모의투자 미지원**."""
        cano, product_code = self._client._require_account()
        return overseas_orders_api.fetch_open_orders(
            self._client.transport, cano=cano, product_code=product_code,
            environment=self._client.environment, market=market,
        )

    def algo_orders(self, *, trade_date: str = "") -> list[OverseasAlgoOrder]:
        """알고(TWAP/VWAP 등 분할집행) 주문 목록. 각 건의 ``order_id``/``branch_number`` 로 :meth:`algo_executions`
        를 조회한다. ``trade_date``(YYYYMMDD)는 거래일자로, 생략하면 오늘(KST)이다. **모의투자 미지원**."""
        cano, product_code = self._client._require_account()
        return overseas_orders_api.fetch_algo_orders(
            self._client.transport, cano=cano, product_code=product_code,
            environment=self._client.environment, trade_date=trade_date,
        )

    def algo_executions(
        self, order_id: str, *, order_date: str, branch_number: str = ""
    ) -> list[OverseasAlgoExecution]:
        """한 알고주문의 체결내역. ``order_id`` 는 :meth:`algo_orders` 의 주문번호, ``order_date``(YYYYMMDD)는
        주문일자, ``branch_number`` 는 그 주문의 채번지점번호. **모의투자 미지원**."""
        cano, product_code = self._client._require_account()
        return overseas_orders_api.fetch_algo_executions(
            self._client.transport, cano=cano, product_code=product_code, environment=self._client.environment,
            order_date=order_date, order_id=order_id, branch_number=branch_number,
        )

    def reserved_orders(self, *, start: str, end: str) -> list[OverseasReservedOrder]:
        """해외 예약주문 목록(정규장 시작 전 예약) -- 미국과 아시아(일/중/홍/베)를 모두 조회해 합친다
        (한 시장만 원하면 각 건의 ``exchange`` 로 거른다). 취소 경로가 시장별로 다르다: 미국은 각 건의
        ``reserved_order_id`` 로 :meth:`cancel_reserved_order`, 아시아는 발주 리포트의
        ``client_order_id`` 로 ``kis.orders.cancel``. **모의투자 미지원**(두 조회 TR 모두 실전전용)."""
        cano, product_code = self._client._require_account()
        us = overseas_reserved_orders_api.fetch_reserved_orders(
            self._client.transport, cano=cano, product_code=product_code, environment=self._client.environment,
            start=start, end=end, market="US",
        )
        asia = overseas_reserved_orders_api.fetch_reserved_orders(
            self._client.transport, cano=cano, product_code=product_code, environment=self._client.environment,
            start=start, end=end, market="ASIA",
        )
        return us + asia

    def cancel_reserved_order(self, reserved_order_id: str, *, receipt_date: str) -> None:
        """**미국** 예약주문을 취소한다 -- ``reserved_order_id`` 는 :meth:`~kis_trader.overseas.stock.OverseasStock.reserve_buy`
        리포트의 ``order_id``, ``receipt_date``(YYYYMMDD)는 그 예약의 접수일자. 아시아(일/중/홍/베)
        예약은 이 경로가 아니라 발주 리포트의 ``client_order_id`` 로 ``kis.orders.cancel`` 이 취소한다
        (전용 취소 엔드포인트가 없어 안전코어가 원주문을 복원 재전송). 모의(VTTT3017U)를 지원한다."""
        cano, product_code = self._client._require_account()
        overseas_reserved_orders_api.cancel_overseas_reserved_order(
            self._client.transport, reserved_order_id=reserved_order_id, receipt_date=receipt_date,
            cano=cano, product_code=product_code, environment=self._client.environment,
        )


class OverseasNamespace:
    """``kis.overseas`` -- 해외 자산(주식·지수·파생) 시세/순위/뉴스/기업행위.

    계좌 조회·계좌 단위 주문은 여기가 아니라 ``kis.account.overseas`` (:class:`OverseasAccount`)."""

    def __init__(self, client: KISClient) -> None:
        self._client = client

    # -- 종목/상품 핸들 --
    def stock(self, symbol: str, *, exchange: str | None = None) -> OverseasStock:
        """해외 종목 핸들. ``exchange`` (거래소코드 NAS/NYS/AMS/TSE/HKS/...)를 생략하면 KIS 종목 마스터로
        거래소를 자동 해석한다(첫 조회는 마스터를 받아 캐시 -- 느릴 수 있다). 같은 심볼이 여러 거래소면
        ``exchange`` 를 명시해야 한다(:class:`~kis_trader.errors.KISUsageError`)."""
        if exchange is None:
            exchange = self._client.instrument(symbol).exchange
        return OverseasStock(self._client, symbol, exchange=exchange)

    def index(self, code: str, *, kind: Literal["index", "fx", "bond", "gold"] = "index") -> OverseasIndex:
        """해외 지수/환율/국채/금선물 핸들. ``kind`` 는 ``index``/``fx``/``bond``/``gold`` 중 하나, ``code``
        는 지수코드(예: ``.DJI``; 지수=비거래 분류자라 ``code``). 국내
        :meth:`~kis_trader.domestic.namespace.DomesticNamespace.index` 의 해외판이다."""
        division = _OVERSEAS_INDEX_KIND.get(kind)
        if division is None:
            raise KISUsageError(
                f"지원하지 않는 kind: {kind!r} ({'/'.join(_OVERSEAS_INDEX_KIND)})."
            )
        return OverseasIndex(self._client, code, market_division=division)

    def futures(self, symbol: str) -> OverseasDerivative:
        """해외 선물 계약 핸들. ``symbol`` 는 시리즈코드(예: ESZ25 = E-mini S&P 2025.12)."""
        return OverseasDerivative(self._client, symbol, product="future")

    def option(self, symbol: str) -> OverseasDerivative:
        """해외 옵션 계약 핸들. ``symbol`` 는 시리즈코드."""
        return OverseasDerivative(self._client, symbol, product="option")

    # -- 파생 배치/조회 --
    def futures_details(self, symbols: Sequence[str]) -> list[OverseasDerivativeDetail]:
        """여러 해외 선물 계약의 명세를 한 번에(최대 32개, 실전만)."""
        return overseas_derivatives_api.fetch_details(
            self._client.transport, srs_codes=list(symbols), product="future",
            environment=self._client.environment,
        )

    def option_details(self, symbols: Sequence[str]) -> list[OverseasDerivativeDetail]:
        """여러 해외 옵션 계약의 명세를 한 번에(최대 30개, 실전만)."""
        return overseas_derivatives_api.fetch_details(
            self._client.transport, srs_codes=list(symbols), product="option",
            environment=self._client.environment,
        )

    def derivatives_market_hours(
        self, *, product_group: str = "", asset_class: str = "", exchange: str = "",
        product: DerivativeProductFilter = "all",
    ) -> list[OverseasDerivativeMarketHours]:
        """해외 선물/옵션 상품군별 장운영시간(시장 전체, 계약 무관). 필터를 생략하면 전체를 조회한다.
        **모의투자 미지원**."""
        return overseas_derivatives_api.fetch_market_hours(
            self._client.transport, environment=self._client.environment,
            product_group=product_group, asset_class=asset_class, exchange=exchange, product=product,
        )

    def futures_open_interest(
        self, symbol: str, *, as_of: str | date, mode: Literal["quantity", "change"] = "quantity"
    ) -> list[OverseasFuturesOpenInterest]:
        """해외선물 상품의 CFTC 미결제약정 수량 또는 증감 추이(실전만).

        ``symbol`` 은 CFTC 상품 루트 심볼(와이어 PROD_ISCD, 예 ``"ES"``)이다 -- ``futures("ESZ25")``
        의 시리즈코드가 아니라 상품 단위 식별자다."""
        return overseas_derivatives_api.fetch_open_interest(
            self._client.transport, symbol=symbol, as_of=as_of, mode=mode,
            environment=self._client.environment,
        )

    def settlement_dates(self) -> list[OverseasSettlementDate]:
        """해외 각 시장의 현지·국내 결제일자(시장 전체 참조표). **모의투자 미지원**. 결제일이 비어 있거나
        유효하지 않으면 해당 날짜는 ``None`` 이다."""
        return overseas_reference_api.fetch_settlement_dates(
            self._client.transport, environment=self._client.environment
        )

    # -- 다종목/검색/정보 --
    def quotes(self, symbols: Sequence[tuple[str, str]]) -> list[Quote]:
        """여러 해외 종목의 현재가를 한 번에(최대 10). 원소는 ``(exchange, symbol)`` 튜플(거래소코드
        NAS/NYS/AMS/HKS/TSE/... 혼합 가능). 국내는
        :meth:`~kis_trader.domestic.namespace.DomesticNamespace.quotes`."""
        return overseas_market_data_api.fetch_multi_quotes(
            self._client.transport, requests=[(exchange, symbol) for exchange, symbol in symbols]
        )

    def search_stocks(
        self, exchange: str, *,
        price: tuple[Numeric, Numeric] | None = None,
        change_percent: tuple[Numeric, Numeric] | None = None,
        market_cap: tuple[Numeric, Numeric] | None = None,
        shares: tuple[Numeric, Numeric] | None = None,
        volume: tuple[Numeric, Numeric] | None = None,
        trading_amount: tuple[Numeric, Numeric] | None = None,
        eps: tuple[Numeric, Numeric] | None = None,
        per: tuple[Numeric, Numeric] | None = None,
    ) -> OverseasStockSearch:
        """해외 종목을 가격·등락률·규모·거래·밸류에이션 범위로 검색한다. 각 필터는 (시작, 끝) 범위.

        ``exchange`` 는 단일 거래소코드(``"NAS"``/``"NYS"``/``"AMS"``/``"HKS"``/...) 또는
        ``"US"`` 다. KIS 조건검색 TR 은 거래소당 한 번이라, ``"US"`` 는 나스닥·뉴욕·아멕스를 각각
        조회해 결과(``matches``)를 합쳐 준다(각 종목은 자기 ``exchange`` 를 안다). 이때 반환
        ``exchange`` 는 ``"US"``, ``total_count`` 은 세 거래소 합, 순위(``rank``)는 거래소별 순위가
        그대로 유지된다. 집계라 ``decimal_places`` 는 첫 거래소 대표값이고 ``status`` 는 비운다
        (가격은 각 ``match`` 에 이미 파싱된 ``Decimal`` 로 들어 있다)."""
        filters = {
            "price": price, "change_percent": change_percent, "market_cap": market_cap,
            "shares": shares, "volume": volume, "trading_amount": trading_amount, "eps": eps, "per": per,
        }
        if exchange.upper() == "US":
            parts = [
                overseas_market_data_api.search_stocks(self._client.transport, exchange=exchange_code, **filters)
                for exchange_code in _US_EXCHANGES
            ]
            # 집계는 거래소별 메타(decimal_places=price zdiv, status)를 하나로 못 합친다 -- 각
            # match 는 자기 exchange 와 이미 파싱된 Decimal 가격을 들고 있으니, 대표값으로 첫
            # 거래소의 decimal_places 를 싣고 status 는 집계라 비운다.
            return OverseasStockSearch(
                exchange="US", decimal_places=parts[0].decimal_places, status="",
                total_count=sum(p.total_count for p in parts),
                matches=tuple(m for p in parts for m in p.matches),
            )
        return overseas_market_data_api.search_stocks(
            self._client.transport, exchange=exchange, **filters
        )

    def product_info(self, symbol: str, *, exchange: str) -> OverseasProductInfo:
        """해외 종목의 상품기본정보(거래소·통화·상장주식수·SEDOL·블룸버그티커 등). exchange=NAS/NYS/AMS/TSE/HKS/..."""
        return overseas_market_data_api.fetch_product_info(
            self._client.transport, exchange=exchange, symbol=symbol
        )

    def industries(self, exchange: str) -> list[OverseasIndustry]:
        """해외 거래소의 업종(섹터) 코드 목록. **모의투자 미지원**."""
        return overseas_market_data_api.fetch_industries(
            self._client.transport, exchange=exchange, environment=self._client.environment
        )

    def industry_stocks(
        self, exchange: str, *, industry_code: str, min_volume: int = 0
    ) -> list[OverseasIndustryStock]:
        """해외 거래소의 한 업종에 속한 종목 시세. 거래량 하한은 0·100·1천·1만·10만·100만·1천만."""
        return overseas_market_data_api.fetch_industry_stocks(
            self._client.transport, exchange=exchange, industry_code=industry_code,
            min_volume=min_volume, environment=self._client.environment,
        )

    def collateral_stocks(
        self, symbol: str, *, country: str, sort: Literal["name", "symbol"] = "name", product_type: str = "",
        loanable: bool | None = None,
    ) -> OverseasCollateralStockSearch:
        """해외주식 담보대출 가능종목 목록(``.stocks``)과 조회 요약(``.summary``) -- 대출 가능 여부와
        적용 비율."""
        return overseas_reference_api.fetch_collateral_stocks(
            self._client.transport, symbol=symbol, country=country, sort=sort,
            product_type=product_type, loanable=loanable,
        )

    # -- 뉴스/기업행위 --
    def news(
        self, *, country: str = "", exchange: str = "", symbol: str = "",
        date_: str | date | None = None, time: str = "", category: str = "",
    ) -> list[OverseasNewsHeadline]:
        """해외뉴스 종합 제목 피드."""
        return overseas_reference_api.fetch_news(
            self._client.transport, country=country, exchange=exchange, symbol=symbol,
            date_=date_, time=time, category=category,
        )

    def breaking_news(
        self, *, symbol: str = "", title: str = "", date_: str | date | None = None, time: str = ""
    ) -> list[NewsHeadline]:
        """해외속보 제목 피드(최대 100건)."""
        return overseas_reference_api.fetch_breaking_news(
            self._client.transport, symbol=symbol, title=title, date_=date_, time=time
        )

    def rights(
        self, *, start: str | date, end: str | date, right_type: str = "%%",
        date_basis: Literal["local_base", "subscription_start", "subscription_end"] = "local_base", symbol: str = "", product_type: str = "",
    ) -> list[OverseasRight]:
        """기간별 해외증권 배당·증자·합병 등 권리."""
        return overseas_reference_api.fetch_period_rights(
            self._client.transport, start=start, end=end, right_type=right_type,
            date_basis=date_basis, symbol=symbol, product_type=product_type,
        )

    def corporate_actions(
        self, symbol: str, *, country: str, start: str | date | None = None,
        end: str | date | None = None,
    ) -> list[OverseasCorporateAction]:
        """해외종목 권리·기업행사 종합 일정."""
        return overseas_reference_api.fetch_corporate_actions(
            self._client.transport, country=country, symbol=symbol, start=start, end=end
        )

    # -- 하위 질의 네임스페이스 --
    @property
    def ranking(self) -> OverseasRankingQueries:
        """해외주식 시장 순위 질의 -- ``kis.overseas.ranking.by_volume(exchange="NAS")`` 등. 거래소별로
        조회한다(``exchange`` = NAS/NYS/HKS/...)."""
        return OverseasRankingQueries(self._client)


