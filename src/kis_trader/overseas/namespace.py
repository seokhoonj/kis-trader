"""해외 자산군 네임스페이스 -- ``kis.overseas`` (:class:`OverseasNamespace`) 와 그 계좌 하위
(:class:`OverseasAccount`, ``kis.account.overseas``).

세션 :class:`~kis_trader.client.KISClient` 아래 해외 주식·지수·파생의 시세/계좌/순위/뉴스 행위를
모은다. 각 메서드는 세션이 쥔 전송/계좌/환경으로 해외 엔진을 직접 호출한다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

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

    from .._literals import Numeric
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


class OverseasAccount:
    """``kis.account.overseas`` -- 해외 계좌 조회·계좌 단위 주문(잔고/손익/알고/예약주문).

    모든 메서드는 계좌 미설정 시 :class:`~kis_trader.errors.KISUsageError` 를 던진다. ``**모의투자
    미지원**`` 이라 표시된 메서드는 ``environment="paper"`` 에서도 :class:`~kis_trader.errors.
    KISUsageError` 다. 조회 실패·응답 부재·파싱 실패는 :class:`~kis_trader.errors.KISError`.
    """

    def __init__(self, client: KISClient) -> None:
        self._c = client

    def positions(self, *, market: str | None = None) -> list[OverseasPosition]:
        """보유 종목(거래소 그룹+통화별). ``market`` = ``"US"``/``"HK"``/``"CN_SH"``/``"CN_SZ"``/``"JP"``/
        ``"VN_HN"``/``"VN_HCM"``, 생략(``None``)하면 **전체 시장 그룹을 순회해 합친다**. 금액은 종목 통화의
        :class:`~kis_trader.money.Money`."""
        cano, product_code = self._c._require_account()
        return overseas_account.fetch_positions(
            self._c.transport, cano=cano, product_code=product_code,
            environment=self._c.environment, market=market,
        )

    def balance(self, *, market: str) -> OverseasBalance:
        """계좌 손익 요약(시장/통화별) -- 매입금액·평가/실현/총손익·총수익률을 :class:`~kis_trader.money.Money`
        로. 전체 시장 종합은 :meth:`present_balance`. 예수금(현금)은 별도다."""
        cano, product_code = self._c._require_account()
        return overseas_account.fetch_balance(
            self._c.transport, cano=cano, product_code=product_code,
            environment=self._c.environment, market=market,
        )

    def buyable(self, symbol: str, *, exchange: str, price: Numeric) -> OverseasBuyableAmount:
        """매수가능금액. ``exchange`` 는 시세 거래소코드(NAS/NYS/AMS/HKS/SHS/SZS/TSE/HNX/HSX), ``price`` 는
        의도한 주문단가. 외화·통합 기준 주문가능금액·최대수량을 :class:`~kis_trader.money.Money` 로 준다.
        **매수 시 수량단위 절사가 필요**하다."""
        cano, product_code = self._c._require_account()
        return overseas_account.fetch_buyable_amount(
            self._c.transport, cano=cano, product_code=product_code,
            environment=self._c.environment, symbol=symbol, exchange=exchange, price=price,
        )

    def foreign_margin(self) -> list[OverseasForeignMargin]:
        """통화별 외화 예수금·증거금·주문가능금액. 금액은 각 통화의 :class:`~kis_trader.money.Money`.
        **모의투자 미지원**."""
        cano, product_code = self._c._require_account()
        return overseas_account.fetch_foreign_margin(
            self._c.transport, cano=cano, product_code=product_code, environment=self._c.environment
        )

    def present_balance(
        self, *, won_basis: bool = True, nation: str = "all", market_code: str = "00",
        inquiry: str = "00",
    ) -> OverseasPresentBalance:
        """체결기준현재잔고 -- 보유 종목·통화별 예수금·계좌 요약. ``won_basis`` 원화(True)/외화(False),
        ``nation`` 국가(``"all"``/``"US"``/``"HK"``/``"CN"``/``"JP"``/``"VN"``), ``market_code`` 거래시장코드
        (``"00"``=전체), ``inquiry`` 조회구분. 모의는 요약만 온다.

        .. note:: 요약 필드는 KIS 예시가 잘려 레이아웃 기준이다 -- 전체 원본은 ``_raw``."""
        cano, product_code = self._c._require_account()
        return overseas_account.fetch_present_balance(
            self._c.transport, cano=cano, product_code=product_code, environment=self._c.environment,
            won_basis=won_basis, nation=nation, market_code=market_code, inquiry=inquiry,
        )

    def settlement_balance(
        self, *, basis_date: str, won_basis: bool = True, inquiry: str = "00"
    ) -> OverseasSettlementBalance:
        """결제기준잔고 -- ``basis_date``(YYYYMMDD) 결제 기준의 보유 종목·통화별 예수금·계좌 요약.
        ``won_basis`` 원화(True)/외화(False), ``inquiry`` 조회구분. **모의투자 미지원**."""
        cano, product_code = self._c._require_account()
        return overseas_account.fetch_settlement_balance(
            self._c.transport, cano=cano, product_code=product_code, environment=self._c.environment,
            basis_date=basis_date, won_basis=won_basis, inquiry=inquiry,
        )

    def period_profit(
        self, *, start: str, end: str, exchange: str = "", nation: str = "", currency: str = "",
        symbol: str = "", won_basis: bool = False,
    ) -> OverseasPeriodProfit:
        """기간손익 -- ``start``~``end``(YYYYMMDD) 매도청산 종목별 실현손익과 총계. ``exchange`` 거래소
        (공란=전체), ``currency`` 통화(공란=전체), ``symbol`` 종목(공란=전체), ``won_basis`` 원화(True)/외화
        (False). **모의투자 미지원**.

        .. note:: KIS 예시가 비어 있어 필드는 레이아웃 기준이다 -- 전체 원본은 각 행/결과의 ``_raw``."""
        cano, product_code = self._c._require_account()
        return overseas_account.fetch_period_profit(
            self._c.transport, cano=cano, product_code=product_code, environment=self._c.environment,
            start=start, end=end, exchange=exchange, nation=nation, currency=currency,
            symbol=symbol, won_basis=won_basis,
        )

    def transactions(
        self, *, start: str, end: str, symbol: str | None = None, side: str = "all"
    ) -> list[OverseasTransaction]:
        """일별 거래내역(매매·결제·수수료). ``start``/``end`` 는 등록일자 기간(YYYYMMDD), ``symbol`` 없으면
        전체, ``side`` = ``"all"``/``"sell"``/``"buy"``. 외화 금액은 거래 통화의 :class:`~kis_trader.money.Money`.
        **모의투자 미지원**."""
        cano, product_code = self._c._require_account()
        return overseas_account.fetch_transactions(
            self._c.transport, cano=cano, product_code=product_code,
            environment=self._c.environment, start=start, end=end, symbol=symbol, side=side,
        )

    def open_orders(self, *, market: str | None = None) -> list[OverseasOpenOrder]:
        """미체결(열린) 주문. 거래소 주문번호·미체결 잔량을 준다. ``market`` 생략(``None``)하면 **전체 시장
        그룹을 순회해 합친다**. **모의투자 미지원**."""
        cano, product_code = self._c._require_account()
        return overseas_orders_api.fetch_open_orders(
            self._c.transport, cano=cano, product_code=product_code,
            environment=self._c.environment, market=market,
        )

    def algo_orders(self) -> list[OverseasAlgoOrder]:
        """알고(TWAP/VWAP 등 분할집행) 주문 목록. 각 건의 ``order_id``/``branch_number`` 로 :meth:`algo_executions`
        를 조회한다. **모의투자 미지원**."""
        cano, product_code = self._c._require_account()
        return overseas_orders_api.fetch_algo_orders(
            self._c.transport, cano=cano, product_code=product_code, environment=self._c.environment
        )

    def algo_executions(
        self, order_id: str, *, order_date: str, branch_number: str = ""
    ) -> list[OverseasAlgoExecution]:
        """한 알고주문의 체결내역. ``order_id`` 는 :meth:`algo_orders` 의 주문번호, ``order_date``(YYYYMMDD)는
        주문일자, ``branch_number`` 는 그 주문의 채번지점번호. **모의투자 미지원**."""
        cano, product_code = self._c._require_account()
        return overseas_orders_api.fetch_algo_executions(
            self._c.transport, cano=cano, product_code=product_code, environment=self._c.environment,
            order_date=order_date, order_id=order_id, branch_number=branch_number,
        )

    def reserved_orders(self, *, start: str, end: str) -> list[OverseasReservedOrder]:
        """해외 예약주문 목록(정규장 시작 전 예약) -- 미국과 아시아(일/중/홍/베)를 모두 조회해 합친다
        (한 시장만 원하면 각 건의 ``exchange`` 로 거른다). 취소 경로가 시장별로 다르다: 미국은 각 건의
        ``reserved_order_id`` 로 :meth:`cancel_reserved_order`, 아시아는 발주 리포트의
        ``client_order_id`` 로 ``kis.orders.cancel``. **모의투자 미지원**(두 조회 TR 모두 실전전용)."""
        cano, product_code = self._c._require_account()
        us = overseas_reserved_orders_api.fetch_reserved_orders(
            self._c.transport, cano=cano, product_code=product_code, environment=self._c.environment,
            start=start, end=end, market="US",
        )
        asia = overseas_reserved_orders_api.fetch_reserved_orders(
            self._c.transport, cano=cano, product_code=product_code, environment=self._c.environment,
            start=start, end=end, market="ASIA",
        )
        return us + asia

    def cancel_reserved_order(self, reserved_order_id: str, *, receipt_date: str) -> None:
        """**미국** 예약주문을 취소한다 -- ``reserved_order_id`` 는 :meth:`~kis_trader.overseas.stock.OverseasStock.reserve_buy`
        리포트의 ``order_id``, ``receipt_date``(YYYYMMDD)는 그 예약의 접수일자. 아시아(일/중/홍/베)
        예약은 이 경로가 아니라 발주 리포트의 ``client_order_id`` 로 ``kis.orders.cancel`` 이 취소한다
        (전용 취소 엔드포인트가 없어 안전코어가 원주문을 복원 재전송). 모의(VTTT3017U)를 지원한다."""
        cano, product_code = self._c._require_account()
        overseas_reserved_orders_api.cancel_overseas_reserved_order(
            self._c.transport, reserved_order_id=reserved_order_id, receipt_date=receipt_date,
            cano=cano, product_code=product_code, environment=self._c.environment,
        )


class OverseasNamespace:
    """``kis.overseas`` -- 해외 자산(주식·지수·파생) 시세/순위/뉴스/기업행위.

    계좌 조회·계좌 단위 주문은 여기가 아니라 ``kis.account.overseas`` (:class:`OverseasAccount`)."""

    def __init__(self, client: KISClient) -> None:
        self._c = client

    # -- 종목/상품 핸들 --
    def stock(self, symbol: str, *, exchange: str | None = None) -> OverseasStock:
        """해외 종목 핸들. ``exchange`` (거래소코드 NAS/NYS/AMS/TSE/HKS/...)를 생략하면 KIS 종목 마스터로
        거래소를 자동 해석한다(첫 조회는 마스터를 받아 캐시 -- 느릴 수 있다). 같은 심볼이 여러 거래소면
        ``exchange`` 를 명시해야 한다(:class:`~kis_trader.errors.KISUsageError`)."""
        if exchange is None:
            exchange = self._c.instrument(symbol).exchange
        return OverseasStock(self._c, symbol, exchange=exchange)

    def index(self, symbol: str, *, kind: str = "index") -> OverseasIndex:
        """해외 지수/환율/국채/금선물 핸들. ``kind`` 는 ``index``/``fx``/``bond``/``gold`` 중 하나, ``symbol``
        은 지수코드(예: ``.DJI``). 국내 :meth:`~kis_trader.domestic.namespace.DomesticNamespace.index` 의 해외판이다."""
        division = _OVERSEAS_INDEX_KIND.get(kind)
        if division is None:
            raise KISUsageError(
                f"지원하지 않는 kind: {kind!r} ({'/'.join(_OVERSEAS_INDEX_KIND)})."
            )
        return OverseasIndex(self._c, symbol, market_division=division)

    def futures(self, series_code: str) -> OverseasDerivative:
        """해외 선물 계약 핸들. ``series_code`` 는 시리즈코드(예: ESZ25 = E-mini S&P 2025.12)."""
        return OverseasDerivative(self._c, series_code, market="future")

    def option(self, series_code: str) -> OverseasDerivative:
        """해외 옵션 계약 핸들. ``series_code`` 는 시리즈코드."""
        return OverseasDerivative(self._c, series_code, market="option")

    # -- 파생 배치/조회 --
    def futures_details(self, symbols: Sequence[str]) -> list[OverseasDerivativeDetail]:
        """여러 해외 선물 계약의 명세를 한 번에(최대 32개, 실전만)."""
        return overseas_derivatives_api.fetch_details(
            self._c.transport, srs_codes=list(symbols), market="future",
            environment=self._c.environment,
        )

    def option_details(self, symbols: Sequence[str]) -> list[OverseasDerivativeDetail]:
        """여러 해외 옵션 계약의 명세를 한 번에(최대 30개, 실전만)."""
        return overseas_derivatives_api.fetch_details(
            self._c.transport, srs_codes=list(symbols), market="option",
            environment=self._c.environment,
        )

    def derivatives_market_hours(
        self, *, product_group: str = "", asset_class: str = "", exchange: str = "", kind: str = "%"
    ) -> list[OverseasDerivativeMarketHours]:
        """해외 선물/옵션 상품군별 장운영시간(시장 전체, 계약 무관). 필터를 생략하면 전체를 조회한다.
        **모의투자 미지원**."""
        return overseas_derivatives_api.fetch_market_hours(
            self._c.transport, environment=self._c.environment,
            product_group=product_group, asset_class=asset_class, exchange=exchange, kind=kind,
        )

    def futures_open_interest(
        self, product: str, *, as_of: str | date, mode: Literal["quantity", "change"] = "quantity"
    ) -> list[OverseasFuturesOpenInterest]:
        """해외선물 상품의 CFTC 미결제약정 수량 또는 증감 추이(실전만)."""
        return overseas_derivatives_api.fetch_open_interest(
            self._c.transport, product=product, as_of=as_of, mode=mode,
            environment=self._c.environment,
        )

    def settlement_dates(self) -> list[OverseasSettlementDate]:
        """해외 각 시장의 현지·국내 결제일자(시장 전체 참조표). **모의투자 미지원**. 결제일이 비어 있거나
        유효하지 않으면 해당 날짜는 ``None`` 이다."""
        return overseas_reference_api.fetch_settlement_dates(
            self._c.transport, environment=self._c.environment
        )

    # -- 다종목/검색/정보 --
    def quotes(self, symbols: Sequence[tuple[str, str]]) -> list[Quote]:
        """여러 해외 종목의 현재가를 한 번에(최대 10). 원소는 ``(exchange, symbol)`` 튜플(거래소코드
        NAS/NYS/AMS/HKS/TSE/... 혼합 가능). 국내는
        :meth:`~kis_trader.domestic.namespace.DomesticNamespace.quotes`."""
        return overseas_market_data_api.fetch_multi_quotes(
            self._c.transport, requests=[(exchange, symbol) for exchange, symbol in symbols]
        )

    def search_stocks(
        self, exchange: str, *,
        price: tuple[Numeric, Numeric] | None = None,
        change_percent: tuple[Numeric, Numeric] | None = None,
        market_cap: tuple[Numeric, Numeric] | None = None,
        shares: tuple[Numeric, Numeric] | None = None,
        volume: tuple[Numeric, Numeric] | None = None,
        amount: tuple[Numeric, Numeric] | None = None,
        eps: tuple[Numeric, Numeric] | None = None,
        per: tuple[Numeric, Numeric] | None = None,
    ) -> OverseasStockSearch:
        """해외 종목을 가격·등락률·규모·거래·밸류에이션 범위로 검색한다. 각 필터는 (시작, 끝) 범위.

        ``exchange`` 는 단일 거래소코드(``"NAS"``/``"NYS"``/``"AMS"``/``"HKS"``/...) 또는
        ``"US"`` 다. KIS 조건검색 TR 은 거래소당 한 번이라, ``"US"`` 는 나스닥·뉴욕·아멕스를 각각
        조회해 결과(``matches``)를 합쳐 준다(각 종목은 자기 ``exchange`` 를 안다). 이때 반환
        ``exchange`` 는 ``"US"``, ``total_count`` 은 세 거래소 합, 순위(``rank``)는 거래소별 순위가
        그대로 유지된다."""
        filters = {
            "price": price, "change_percent": change_percent, "market_cap": market_cap,
            "shares": shares, "volume": volume, "amount": amount, "eps": eps, "per": per,
        }
        if exchange.upper() == "US":
            parts = [
                overseas_market_data_api.search_stocks(self._c.transport, exchange=x, **filters)
                for x in _US_EXCHANGES
            ]
            return OverseasStockSearch(
                exchange="US", decimal_places=0, status="",
                total_count=sum(p.total_count for p in parts),
                matches=tuple(m for p in parts for m in p.matches),
            )
        return overseas_market_data_api.search_stocks(
            self._c.transport, exchange=exchange, **filters
        )

    def product_info(self, exchange: str, symbol: str) -> OverseasProductInfo:
        """해외 종목의 상품기본정보(거래소·통화·상장주식수·SEDOL·블룸버그티커 등). exchange=NAS/NYS/AMS/TSE/HKS/..."""
        return overseas_market_data_api.fetch_product_info(
            self._c.transport, exchange=exchange, symbol=symbol
        )

    def industries(self, exchange: str) -> list[OverseasIndustry]:
        """해외 거래소의 업종(섹터) 코드 목록. **모의투자 미지원**."""
        return overseas_market_data_api.fetch_industries(
            self._c.transport, exchange=exchange, environment=self._c.environment
        )

    def industry_stocks(
        self, exchange: str, industry_code: str, *, min_volume: int = 0
    ) -> list[OverseasIndustryStock]:
        """해외 거래소의 한 업종에 속한 종목 시세. 거래량 하한은 0·100·1천·1만·10만·100만·1천만."""
        return overseas_market_data_api.fetch_industry_stocks(
            self._c.transport, exchange=exchange, industry_code=industry_code,
            min_volume=min_volume, environment=self._c.environment,
        )

    def collateral_stocks(
        self, symbol: str, country: str, *, sort: str = "name", product_type: str = "",
        loanable: bool | None = None,
    ) -> OverseasCollateralStockSearch:
        """해외주식 담보대출 가능종목 목록(``.stocks``)과 조회 요약(``.summary``) -- 대출 가능 여부와
        적용 비율."""
        return overseas_reference_api.fetch_collateral_stocks(
            self._c.transport, symbol=symbol, country=country, sort=sort,
            product_type=product_type, loanable=loanable,
        )

    # -- 뉴스/기업행위 --
    def news(
        self, *, country: str = "", exchange: str = "", symbol: str = "",
        date_: str | date | None = None, time: str = "", category: str = "",
    ) -> list[OverseasNewsHeadline]:
        """해외뉴스 종합 제목 피드."""
        return overseas_reference_api.fetch_news(
            self._c.transport, country=country, exchange=exchange, symbol=symbol,
            date_=date_, time=time, category=category,
        )

    def breaking_news(
        self, *, symbol: str = "", title: str = "", date_: str | date | None = None, time: str = ""
    ) -> list[NewsHeadline]:
        """해외속보 제목 피드(최대 100건)."""
        return overseas_reference_api.fetch_breaking_news(
            self._c.transport, symbol=symbol, title=title, date_=date_, time=time
        )

    def rights(
        self, *, start: str | date, end: str | date, right_type: str = "%%",
        date_basis: str = "local_base", symbol: str = "", product_type: str = "",
    ) -> list[OverseasRight]:
        """기간별 해외증권 배당·증자·합병 등 권리."""
        return overseas_reference_api.fetch_period_rights(
            self._c.transport, start=start, end=end, right_type=right_type,
            date_basis=date_basis, symbol=symbol, product_type=product_type,
        )

    def corporate_actions(
        self, country: str, symbol: str, *, start: str | date | None = None,
        end: str | date | None = None,
    ) -> list[OverseasCorporateAction]:
        """해외종목 권리·기업행사 종합 일정."""
        return overseas_reference_api.fetch_corporate_actions(
            self._c.transport, country=country, symbol=symbol, start=start, end=end
        )

    # -- 하위 질의 네임스페이스 --
    @property
    def ranking(self) -> OverseasRankingQueries:
        """해외주식 시장 순위 질의 -- ``kis.overseas.ranking.by_volume(exchange="NAS")`` 등. 거래소별로
        조회한다(``exchange`` = NAS/NYS/HKS/...)."""
        return OverseasRankingQueries(self._c)


