"""장내채권 시세 조회 (내부) -- 채권 현재가를 :class:`BondQuote` 로.

사용자면은 채권 핸들(:class:`~kis_trader.bond.Bond`, ``kis.domestic.bond(code)``)이다. 채권은 시장구분 ``B`` +
표준코드(ISIN, 예: KR2033022D33)로 조회한다.

KIS URL/TR-ID (KIS 명세 대조):
- 채권 현재가: ``GET .../domestic-bond/v1/quotations/inquire-price`` ``FHKBJ773400C0``.
- 채권 호가: ``GET .../domestic-bond/v1/quotations/inquire-asking-price`` ``FHKBJ773401C0``.
- 채권 체결: ``GET .../domestic-bond/v1/quotations/inquire-ccnl`` ``FHKBJ773403C0``.
- 채권 일봉: ``GET .../domestic-bond/v1/quotations/inquire-daily-itemchartprice``
  ``FHKBJ773701C0``.
  (모두 ``FID_COND_MRKT_DIV_CODE=B`` + ``FID_INPUT_ISCD=표준코드``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any, NamedTuple

from .._bars import _parse_bar_timestamp
from .._internal._datetime import (
    _KST,
    _parse_intraday_timestamp,
    _parse_kst_date,
    _to_yyyymmdd,
    parse_optional_kst_date,
)
from .._depth import _price_levels
from .._internal._response import (
    _missing_block_error,
    _raise_if_error,
    _require_mapping_rows,
)
from .._internal._wire import (
    _apply_change_sign,
    optional_decimal,
    optional_int,
    required_decimal,
    required_int,
)
from ..bar import Bar, Interval
from ..bond_items import (
    BondDailyPrice,
    BondIssuance,
    BondProfile,
    BondQuote,
    BondValuation,
)
from ..errors import KISError, KISUsageError
from ..order_book import OrderBook
from ..trade import Trade
from ..transport import Transport

_QUOTE_PATH = "/uapi/domestic-bond/v1/quotations/inquire-price"
_QUOTE_TR = "FHKBJ773400C0"
_ORDER_BOOK_PATH = "/uapi/domestic-bond/v1/quotations/inquire-asking-price"
_ORDER_BOOK_TR = "FHKBJ773401C0"
_TRADES_PATH = "/uapi/domestic-bond/v1/quotations/inquire-ccnl"
_TRADES_TR = "FHKBJ773403C0"
_BARS_PATH = "/uapi/domestic-bond/v1/quotations/inquire-daily-itemchartprice"
_BARS_TR = "FHKBJ773701C0"
_DAILY_PRICES_PATH = "/uapi/domestic-bond/v1/quotations/inquire-daily-price"
_DAILY_PRICES_TR = "FHKBJ773404C0"
_MAX_DAILY_PRICE_PAGES = 50
_VALUATIONS_PATH = "/uapi/domestic-bond/v1/quotations/avg-unit"
_VALUATIONS_TR = "CTPF2005R"
_ISSUANCE_PATH = "/uapi/domestic-bond/v1/quotations/issue-info"
_ISSUANCE_TR = "CTPF1101R"
#: 채권 조회의 시장구분 코드(KIS 코드표: 채권 B).
_MARKET_DIV = "B"
#: 상품유형코드(KIS 코드표: 채권 302). PRDT_TYPE_CD 파라미터에 쓴다.
_BOND_PRODUCT_TYPE_CODE = "302"


class _ValuationAgencyFields(NamedTuple):
    """평가기관 하나의 응답 필드명 묶음(단가 / 수익률 / 신용등급 / 무위험단가)."""

    price: str
    yield_rate: str
    rating: str
    risk_free: str


#: 평가기관 -> 그 기관의 단가/수익률/신용등급/무위험단가 응답 필드명. FNP 는 무위험단가 필드가 없어 빈 문자열.
_VALUATION_FIELDS_BY_AGENCY = {
    "KIS": _ValuationAgencyFields("kis_unpr", "kis_erng_rt", "kis_crdt_grad_text", "kis_rf_unpr"),
    "KBP": _ValuationAgencyFields("kbp_unpr", "kbp_erng_rt", "kbp_crdt_grad_text", "kbp_rf_unpr"),
    "NICE": _ValuationAgencyFields(
        "nice_evlu_unpr", "nice_evlu_erng_rt", "nice_crdt_grad_text", "nice_evlu_rf_unpr"
    ),
    "FNP": _ValuationAgencyFields("fnp_unpr", "fnp_erng_rt", "fnp_crdt_grad_text", ""),
}


_INFO_PATH = "/uapi/domestic-bond/v1/quotations/search-bond-info"
_INFO_TR = "CTPF1114R"


def fetch_quote(transport: Transport, *, code: str) -> BondQuote:
    """채권 현재가 스냅샷. ``code`` 는 표준코드(ISIN)."""
    params = {"FID_COND_MRKT_DIV_CODE": _MARKET_DIV, "FID_INPUT_ISCD": code}
    resp = transport.request(
        method="GET", path=_QUOTE_PATH, tr_id=_QUOTE_TR, params=params, idempotent=True
    )
    _raise_if_error(resp)
    output = resp.body.get("output")
    if not isinstance(output, Mapping):        # 성공 응답인데 객체 아님 -> fail-closed
        raise _missing_block_error("output", resp)
    return _parse_quote(output, code=code, as_of=datetime.now(_KST))


def fetch_order_book(transport: Transport, *, code: str) -> OrderBook:
    """채권 호가창(5단계 매수/매도 심도). ``code`` 는 표준코드(ISIN).

    채권 호가는 종목 :meth:`~kis_trader.stock.DomesticStock.order_book` 과 같은 :class:`OrderBook`
    로 돌려주되, 채권은 5단계(주식은 10단계)다. 가격 키는 ``bond_askp``/``bond_bidp``, 잔량 키는
    ``askp_rsqn``/``bidp_rsqn`` -- 빈/0 단계는 건너뛴다.
    """
    params = {"FID_COND_MRKT_DIV_CODE": _MARKET_DIV, "FID_INPUT_ISCD": code}
    resp = transport.request(
        method="GET", path=_ORDER_BOOK_PATH, tr_id=_ORDER_BOOK_TR, params=params, idempotent=True
    )
    _raise_if_error(resp)
    output = resp.body.get("output")
    if not isinstance(output, Mapping):        # 성공 응답인데 객체 아님 -> fail-closed
        raise _missing_block_error("output", resp)
    return OrderBook(
        symbol=code,
        market=_MARKET_DIV,
        bids=_price_levels(output, price_key="bond_bidp", quantity_key="bidp_rsqn"),
        asks=_price_levels(output, price_key="bond_askp", quantity_key="askp_rsqn"),
        total_bid_quantity=optional_int(output.get("total_bidp_rsqn"), "total_bidp_rsqn") or 0,
        total_ask_quantity=optional_int(output.get("total_askp_rsqn"), "total_askp_rsqn") or 0,
        as_of=datetime.now(_KST),
        _raw=output,
    )


def fetch_trades(transport: Transport, *, code: str) -> list[Trade]:
    """채권의 최근 체결 목록(최신순). ``code`` 는 표준코드(ISIN).

    종목 :meth:`~kis_trader.stock.DomesticStock.trades` 와 같은 :class:`Trade` 로 돌려준다. 체결가는
    채권가(``bond_prpr``), 시각은 조회일 날짜를 붙인 KST-aware(장 밖 조회면 직전 세션 체결이 조회일
    날짜로 찍힐 수 있다).
    """
    params = {"FID_COND_MRKT_DIV_CODE": _MARKET_DIV, "FID_INPUT_ISCD": code}
    resp = transport.request(
        method="GET", path=_TRADES_PATH, tr_id=_TRADES_TR, params=params, idempotent=True
    )
    _raise_if_error(resp)
    rows = resp.body.get("output")
    if not isinstance(rows, list):             # 성공 응답인데 체결 배열 아님 -> fail-closed
        raise _missing_block_error("output", resp)
    return _parse_trades(rows, code=code, as_of=datetime.now(_KST))


def fetch_bars(transport: Transport, *, code: str, interval: Interval = "1d") -> list[Bar]:
    """채권 일별 OHLCV(과거->현재). KIS 명세상 기간·연속조회 파라미터가 없어 단일 응답을 반환한다."""
    if interval != "1d":
        raise KISUsageError(f"채권 bars 는 interval='1d' 만 지원한다: {interval!r}")
    params = {"FID_COND_MRKT_DIV_CODE": _MARKET_DIV, "FID_INPUT_ISCD": code}
    resp = transport.request(
        method="GET", path=_BARS_PATH, tr_id=_BARS_TR, params=params, idempotent=True
    )
    _raise_if_error(resp)
    rows = _require_mapping_rows("output", resp)
    bars: list[Bar] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise _missing_block_error("output[]", resp)
        date_text = str(row.get("stck_bsop_date", "")).strip()
        close_text = str(row.get("bond_prpr", "")).strip()
        if not date_text or not close_text:
            continue
        bars.append(
            Bar(
                symbol=code,
                timestamp=_parse_bar_timestamp(date_text),
                open=required_decimal(row.get("bond_oprc"), "bond_oprc"),
                high=required_decimal(row.get("bond_hgpr"), "bond_hgpr"),
                low=required_decimal(row.get("bond_lwpr"), "bond_lwpr"),
                close=required_decimal(close_text, "bond_prpr"),
                volume=required_int(row.get("acml_vol"), "acml_vol"),
                _raw=row,
            )
        )
    bars.sort(key=lambda bar: bar.timestamp)
    return bars


def fetch_daily_prices(transport: Transport, *, code: str) -> list[BondDailyPrice]:
    """채권의 날짜별 현재가·등락·OHLCV를 과거->현재 순으로 조회한다."""
    prices_by_date: dict[date, BondDailyPrice] = {}
    tr_cont = ""
    for _page in range(_MAX_DAILY_PRICE_PAGES):
        resp = transport.request(
            method="GET",
            path=_DAILY_PRICES_PATH,
            tr_id=_DAILY_PRICES_TR,
            params={"FID_COND_MRKT_DIV_CODE": _MARKET_DIV, "FID_INPUT_ISCD": code},
            idempotent=True,
            tr_cont=tr_cont,
        )
        _raise_if_error(resp)
        rows = resp.body.get("output")
        if not isinstance(rows, list) or not all(isinstance(row, Mapping) for row in rows):
            raise _missing_block_error("output", resp)
        new_date_count = 0
        for row in rows:
            date_text = str(row.get("stck_bsop_date", "")).strip()
            price_text = str(row.get("bond_prpr", "")).strip()
            if not date_text or not price_text:
                continue
            day = _parse_bar_timestamp(date_text).date()
            change_sign_code = str(row.get("prdy_vrss_sign", "")).strip()
            price = BondDailyPrice(
                trading_date=day,
                code=code,
                price=required_decimal(price_text, "bond_prpr"),
                open=required_decimal(row.get("bond_oprc"), "bond_oprc"),
                high=required_decimal(row.get("bond_hgpr"), "bond_hgpr"),
                low=required_decimal(row.get("bond_lwpr"), "bond_lwpr"),
                change=_apply_change_sign(
                    required_decimal(row.get("bond_prdy_vrss"), "bond_prdy_vrss"), change_sign_code
                ),
                change_percent=_apply_change_sign(
                    required_decimal(row.get("prdy_ctrt"), "prdy_ctrt"), change_sign_code
                ),
                volume=required_int(row.get("acml_vol"), "acml_vol"),
                _raw=row,
            )
            if day not in prices_by_date:
                new_date_count += 1
            prices_by_date[day] = price
        if resp.tr_cont not in {"F", "M"}:
            break
        if new_date_count == 0:
            raise KISError("채권 일별 현재가 연속조회가 새 날짜 없이 반복됐다.")
        tr_cont = "N"
    else:
        raise KISError(
            f"채권 일별 현재가가 {_MAX_DAILY_PRICE_PAGES}페이지 상한에 도달했다."
        )
    return [prices_by_date[day] for day in sorted(prices_by_date)]


def fetch_valuations(
    transport: Transport, *, code: str, start: str | date, end: str | date
) -> list[BondValuation]:
    """평가기관별 채권 단가·수익률의 일별 시계열(과거->현재)."""
    start_date = _to_yyyymmdd(start, "start")
    end_date = _to_yyyymmdd(end, "end")
    if start_date > end_date:
        raise KISUsageError(f"start({start_date}) 가 end({end_date}) 보다 늦다.")
    params = {
        "INQR_STRT_DT": start_date,
        "INQR_END_DT": end_date,
        "PDNO": code,
        "PRDT_TYPE_CD": _BOND_PRODUCT_TYPE_CODE,
        "VRFC_KIND_CD": "00",
        "CTX_AREA_NK30": "",
        "CTX_AREA_FK100": "",
    }
    resp = transport.request(
        method="GET", path=_VALUATIONS_PATH, tr_id=_VALUATIONS_TR,
        params=params, idempotent=True,
    )
    _raise_if_error(resp)
    rows = _require_mapping_rows("output1", resp)
    valuations: list[BondValuation] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise _missing_block_error("output1[]", resp)
        date_text = str(row.get("evlu_dt", "")).strip()
        average_price_text = str(row.get("avg_evlu_unpr", "")).strip()
        if not date_text or not average_price_text:
            continue
        valuations.append(_parse_valuation(row, fallback_code=code, date_text=date_text))
    valuations.sort(key=lambda valuation: valuation.valuation_date)
    return valuations


def fetch_profile(transport: Transport, *, code: str) -> BondProfile:
    """채권 기본/발행 정보(발행일·만기·표면금리·만기수익률·통화). ``code`` 는 표준코드(ISIN)."""
    params = {"PDNO": code, "PRDT_TYPE_CD": _BOND_PRODUCT_TYPE_CODE}
    resp = transport.request(
        method="GET", path=_INFO_PATH, tr_id=_INFO_TR, params=params, idempotent=True
    )
    _raise_if_error(resp)
    output = resp.body.get("output")
    if not isinstance(output, Mapping):
        raise _missing_block_error("output", resp)
    return BondProfile(
        code=code,
        name=str(output.get("ksd_bond_item_name", "")).strip(),
        english_name=str(output.get("ksd_bond_item_eng_name", "")).strip(),
        currency=str(output.get("iso_crcy_cd", "")).strip(),
        issue_date=parse_optional_kst_date(output.get("issu_dt")),
        maturity_date=parse_optional_kst_date(output.get("rdpt_dt")),
        listing_date=parse_optional_kst_date(output.get("lstg_dt")),
        coupon_rate=optional_decimal(output.get("ksd_rcvg_bond_srfc_inrt"),
                                     "ksd_rcvg_bond_srfc_inrt"),
        discount_rate=optional_decimal(output.get("ksd_rcvg_bond_dsct_rt"),
                                       "ksd_rcvg_bond_dsct_rt"),
        redemption_rate=optional_decimal(output.get("bond_expd_rdpt_rt"), "bond_expd_rdpt_rt"),
        yield_to_maturity=optional_decimal(output.get("bond_expd_asrc_erng_rt"),
                                           "bond_expd_asrc_erng_rt"),
        interest_period_months=optional_int(output.get("int_caltm_mcnt"), "int_caltm_mcnt"),
        _raw=output,
    )


def fetch_issuance(transport: Transport, *, code: str) -> BondIssuance:
    """채권의 상세 발행 조건·기관·상태. ``code`` 는 표준코드(ISIN)."""
    params = {"PDNO": code, "PRDT_TYPE_CD": _BOND_PRODUCT_TYPE_CODE}
    resp = transport.request(
        method="GET", path=_ISSUANCE_PATH, tr_id=_ISSUANCE_TR,
        params=params, idempotent=True,
    )
    _raise_if_error(resp)
    output = resp.body.get("output")
    if not isinstance(output, Mapping):
        raise _missing_block_error("output", resp)
    rating_fields = {
        "KIS": "kis_crdt_grad_text",
        "KBP": "kbp_crdt_grad_text",
        "NICE": "nice_crdt_grad_text",
        "FNP": "fnp_crdt_grad_text",
    }
    credit_ratings = {
        agency: rating
        for agency, field in rating_fields.items()
        if (rating := str(output.get(field, "")).strip())
    }
    return BondIssuance(
        code=str(output.get("pdno", "")).strip() or code,
        name=str(output.get("prdt_name", "")).strip(),
        english_name=str(output.get("prdt_eng_name", "")).strip(),
        classification=str(output.get("bond_clsf_kor_name", "")).strip(),
        face_value=required_decimal(output.get("papr"), "papr"),
        issue_amount=required_decimal(output.get("issu_amt"), "issu_amt"),
        outstanding_amount=required_decimal(output.get("lstg_rmnd"), "lstg_rmnd"),
        issuer_name=str(output.get("issu_istt_name", "")).strip(),
        interest_payment_months=required_int(output.get("int_dfrm_mcnt"), "int_dfrm_mcnt"),
        coupon_rate=required_decimal(output.get("srfc_inrt"), "srfc_inrt"),
        discount_rate=required_decimal(output.get("dsct_ec_rt"), "dsct_ec_rt"),
        redemption_rate=required_decimal(output.get("expd_rdpt_rt"), "expd_rdpt_rt"),
        yield_to_maturity=required_decimal(
            output.get("expd_asrc_erng_rt"), "expd_asrc_erng_rt"
        ),
        issue_date=parse_optional_kst_date(output.get("issu_dt")),
        listing_date=parse_optional_kst_date(output.get("lstg_dt")),
        maturity_date=parse_optional_kst_date(output.get("expd_dt")),
        redemption_date=parse_optional_kst_date(output.get("rdpt_dt")),
        previous_interest_date=parse_optional_kst_date(output.get("rgbf_int_dfrm_dt")),
        next_interest_date=parse_optional_kst_date(output.get("nxtm_int_dfrm_dt")),
        credit_ratings=credit_ratings,
        is_inflation_linked=str(output.get("prcm_idx_bond_yn", "")).strip() == "Y",
        is_trade_suspended=str(output.get("bond_tr_stop_dvsn_cd", "")).strip() == "Y",
        is_electronic=str(output.get("elec_scty_yn", "")).strip() == "Y",
        _raw=output,
    )


def _parse_quote(output: Mapping[str, Any], *, code: str, as_of: datetime) -> BondQuote:
    change_sign_code = str(output.get("prdy_vrss_sign", "")).strip()
    return BondQuote(
        code=code,
        name=str(output.get("hts_kor_isnm", "")).strip(),
        price=required_decimal(output.get("bond_prpr"), "bond_prpr"),
        open=required_decimal(output.get("bond_oprc"), "bond_oprc"),
        high=required_decimal(output.get("bond_hgpr"), "bond_hgpr"),
        low=required_decimal(output.get("bond_lwpr"), "bond_lwpr"),
        previous_close=required_decimal(output.get("bond_prdy_clpr"), "bond_prdy_clpr"),
        change=_apply_change_sign(
            required_decimal(output.get("bond_prdy_vrss"), "bond_prdy_vrss"), change_sign_code
        ),
        change_percent=_apply_change_sign(
            required_decimal(output.get("prdy_ctrt"), "prdy_ctrt"), change_sign_code
        ),
        volume=required_int(output.get("acml_vol"), "acml_vol"),
        yield_rate=optional_decimal(output.get("ernn_rate"), "ernn_rate"),
        as_of=as_of,
        _raw=output,
    )


def _parse_trades(
    rows: Sequence[Mapping[str, Any]], *, code: str, as_of: datetime
) -> list[Trade]:
    trades: list[Trade] = []
    for row in rows:
        time_text = str(row.get("stck_cntg_hour", "")).strip()
        price_text = str(row.get("bond_prpr", "")).strip()
        if not time_text or not price_text:    # 빈 행 건너뜀
            continue
        change_sign_code = str(row.get("prdy_vrss_sign", "")).strip()
        trades.append(
            Trade(
                symbol=code,
                timestamp=_parse_intraday_timestamp(time_text, as_of),
                price=required_decimal(price_text, "bond_prpr"),
                quantity=required_int(row.get("cntg_vol"), "cntg_vol"),
                change=_apply_change_sign(
                    required_decimal(row.get("bond_prdy_vrss"), "bond_prdy_vrss"), change_sign_code
                ),
                change_percent=_apply_change_sign(
                    required_decimal(row.get("prdy_ctrt"), "prdy_ctrt"), change_sign_code
                ),
                _raw=row,
            )
        )
    return trades


def _parse_valuation(
    row: Mapping[str, Any], *, fallback_code: str, date_text: str
) -> BondValuation:
    agency_prices: dict[str, Decimal] = {}
    agency_yields: dict[str, Decimal] = {}
    credit_ratings: dict[str, str] = {}
    risk_free_prices: dict[str, Decimal] = {}
    for agency, fields in _VALUATION_FIELDS_BY_AGENCY.items():
        price = optional_decimal(row.get(fields.price), fields.price)
        yield_rate = optional_decimal(row.get(fields.yield_rate), fields.yield_rate)
        rating = str(row.get(fields.rating, "")).strip()
        risk_free_price = (
            optional_decimal(row.get(fields.risk_free), fields.risk_free)
            if fields.risk_free
            else None
        )
        if price is not None:
            agency_prices[agency] = price
        if yield_rate is not None:
            agency_yields[agency] = yield_rate
        if rating:
            credit_ratings[agency] = rating
        if risk_free_price is not None:
            risk_free_prices[agency] = risk_free_price
    return BondValuation(
        valuation_date=_parse_kst_date(date_text),
        code=str(row.get("pdno", "")).strip() or fallback_code,
        name=str(row.get("prdt_name", "")).strip(),
        average_price=required_decimal(row.get("avg_evlu_unpr"), "avg_evlu_unpr"),
        average_yield=required_decimal(row.get("avg_evlu_erng_rt"), "avg_evlu_erng_rt"),
        agency_prices=agency_prices,
        agency_yields=agency_yields,
        credit_ratings=credit_ratings,
        risk_free_prices=risk_free_prices,
        has_valuation_changed=str(row.get("chng_yn", "")).strip() == "Y",
        _raw=row,
    )
