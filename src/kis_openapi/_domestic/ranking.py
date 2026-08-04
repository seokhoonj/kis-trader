"""국내주식 순위 조회 (내부) -- 시장 전체 순위를 :class:`RankedStock` 리스트로.

사용자면(:class:`~kis_openapi.ranking.RankingQueries`)이 호출한다. 순위는 종목 단위가 아니라
시장 전체 대상이라 ``Ticker`` 가 아닌 세션 네임스페이스(``kis.ranking.*``)에 달린다. 여러 순위가
서로 다른 KIS URL(등락률/시총은 ``/ranking/``, 거래량은 ``/quotations/``)에 흩어져 있지만
사용자에겐 하나의 "ranking" 개념으로 모은다. 각 순위는 한 페이지(대개 상위 30건)만 주고 다음
조회가 없다(원장 명시).

KIS URL/TR-id/화면코드/코드표(원장 대조):
- 등락률: ``GET .../ranking/fluctuation`` ``FHPST01700000`` 화면 20170.
  ``FID_RANK_SORT_CLS_CODE`` 0:상승율순 1:하락율순 2:시가대비상승 3:시가대비하락 4:변동율.
- 거래량: ``GET .../quotations/volume-rank`` ``FHPST01710000`` 화면 20171
  (``FID_BLNG_CLS_CODE`` 0:평균거래량 1:거래증가율 2:회전율 3:거래금액 4:금액회전율).
- 시가총액: ``GET .../ranking/market-cap`` ``FHPST01740000`` 화면 20174.
- 이격도: ``GET .../ranking/disparity`` ``FHPST01780000`` 화면 20178
  (``FID_RANK_SORT_CLS_CODE`` 0:이격도상위 1:이격도하위, ``FID_HOUR_CLS_CODE`` 5/10/20/60/120일).
- 호가잔량: ``GET .../ranking/quote-balance`` ``FHPST01720000`` 화면 20172
  (``FID_RANK_SORT_CLS_CODE`` 0:순매수잔량 1:순매도잔량 2:매수비율 3:매도비율).
- 체결강도: ``GET .../ranking/volume-power`` ``FHPST01680000`` 화면 20168 (정렬 없음).
- 대량체결건수: ``GET .../ranking/bulk-trans-num`` ``FHKST190900C0`` 화면 11909
  (``FID_RANK_SORT_CLS_CODE`` 0:매수상위 1:매도상위).
- 관심종목 등록상위: ``GET .../ranking/top-interest-stock`` ``FHPST01800000`` 화면 20180 (정렬 없음).
- 우선주 괴리율: ``GET .../ranking/prefer-disparate-ratio`` ``FHPST01770000`` 화면 20177 (정렬 없음).
- 재무비율: ``GET .../ranking/finance-ratio`` ``FHPST01750000`` 화면 20175
  (``FID_RANK_SORT_CLS_CODE`` 7:수익성 11:안정성 15:성장성 20:활동성).
- 시장가치: ``GET .../ranking/market-value`` ``FHPST01790000`` 화면 20179
  (``FID_RANK_SORT_CLS_CODE`` 23:PER 24:PBR 25:PCR 26:PSR 27:EPS 28:EVA 29:EBITDA 30:EV/EBITDA 31:EBITDA/금융비율).
- 수익자산지표: ``GET .../ranking/profit-asset-index`` ``FHPST01730000`` 화면 20173
  (``FID_RANK_SORT_CLS_CODE`` 0:매출이익 1:영업이익 2:경상이익 3:당기순이익 4:자산총계 5:부채총계 6:자본총계).
  이 셋은 회계연도(``FID_INPUT_OPTION_1``)+분기(``FID_INPUT_OPTION_2`` 0:1Q 1:반기 2:3Q 3:결산)를 함께 받는다.
- 당사매매종목: ``GET .../ranking/traded-by-company`` ``FHPST01860000`` 화면 20186
  (``FID_RANK_SORT_CLS_CODE`` 0:매도상위 1:매수상위, 기간 ``FID_INPUT_DATE_1``~``FID_INPUT_DATE_2``).
- 배당률: ``GET .../ranking/dividend-rate`` ``HHKDB13470100`` (파라미터가 FID_ 계열이 아니라
  ``GB1`` 시장 / ``GB3`` 1:주식배당 2:현금배당 / ``F_DT``~``T_DT`` 기준일 / ``GB4`` 0:전체 1:결산 2:중간;
  시세가 없어 :class:`DividendRanking` 전용 항목으로 돌려준다).
- 공매도: ``GET .../ranking/short-sale`` ``FHPST04820000`` 화면 20482
  (``FID_PERIOD_DIV_CODE`` D:일/M:월 + ``FID_INPUT_CNT_1`` 조회기간; 순위 필드가 없어 응답 순서로
  순위를 매기고, 공매도 지표를 포함한 :class:`ShortSaleRanking` 전용 항목으로 돌려준다).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from .._wire import required_decimal, required_int
from ..errors import KisUsageError
from ..ranking_items import DividendRanking, RankedStock, ShortSaleRanking
from ..transport import Transport
from .market_data import (
    _apply_change_sign,
    _market_div,
    _missing_block_error,
    _parse_kst_date,
    _raise_if_error,
    _to_yyyymmdd,
)

_FLUCTUATION_PATH = "/uapi/domestic-stock/v1/ranking/fluctuation"
_FLUCTUATION_TR = "FHPST01700000"
_FLUCTUATION_SCR = "20170"
#: 등락률 순위 정렬(원장 코드표). gainers=상승율순(0), losers=하락율순(1).
_RANK_SORT = {"gainers": "0", "losers": "1"}

_VOLUME_PATH = "/uapi/domestic-stock/v1/quotations/volume-rank"
_VOLUME_TR = "FHPST01710000"
_VOLUME_SCR = "20171"

_MARKET_CAP_PATH = "/uapi/domestic-stock/v1/ranking/market-cap"
_MARKET_CAP_TR = "FHPST01740000"
_MARKET_CAP_SCR = "20174"

_DISPARITY_PATH = "/uapi/domestic-stock/v1/ranking/disparity"
_DISPARITY_TR = "FHPST01780000"
_DISPARITY_SCR = "20178"
#: 이격도 정렬(원장 코드표). highest=이격도상위순(0), lowest=이격도하위순(1).
_DISPARITY_SORT = {"highest": "0", "lowest": "1"}
#: 이격도 기준 이동평균 일수(원장 FID_HOUR_CLS_CODE 허용값).
_DISPARITY_PERIODS = frozenset({5, 10, 20, 60, 120})

_QUOTE_BALANCE_PATH = "/uapi/domestic-stock/v1/ranking/quote-balance"
_QUOTE_BALANCE_TR = "FHPST01720000"
_QUOTE_BALANCE_SCR = "20172"
#: 호가잔량 정렬(원장 코드표). 0:순매수잔량 1:순매도잔량 2:매수비율 3:매도비율.
_QUOTE_BALANCE_SORT = {"net_buy": "0", "net_sell": "1", "buy_ratio": "2", "sell_ratio": "3"}

_VOLUME_POWER_PATH = "/uapi/domestic-stock/v1/ranking/volume-power"
_VOLUME_POWER_TR = "FHPST01680000"
_VOLUME_POWER_SCR = "20168"

_BULK_TRADES_PATH = "/uapi/domestic-stock/v1/ranking/bulk-trans-num"
_BULK_TRADES_TR = "FHKST190900C0"
_BULK_TRADES_SCR = "11909"
#: 대량체결건수 정렬(원장 코드표). buy=매수상위(0), sell=매도상위(1).
_BULK_TRADES_SORT = {"buy": "0", "sell": "1"}

_INTEREST_PATH = "/uapi/domestic-stock/v1/ranking/top-interest-stock"
_INTEREST_TR = "FHPST01800000"
_INTEREST_SCR = "20180"

_PREFERRED_DISPARITY_PATH = "/uapi/domestic-stock/v1/ranking/prefer-disparate-ratio"
_PREFERRED_DISPARITY_TR = "FHPST01770000"
_PREFERRED_DISPARITY_SCR = "20177"

#: 회계 분기(재무·가치 순위 공통, 원장 FID_INPUT_OPTION_2). annual=결산.
_FISCAL_QUARTER = {"q1": "0", "h1": "1", "q3": "2", "annual": "3"}

_FINANCE_RATIO_PATH = "/uapi/domestic-stock/v1/ranking/finance-ratio"
_FINANCE_RATIO_TR = "FHPST01750000"
_FINANCE_RATIO_SCR = "20175"
#: 재무비율 분석 축(원장 코드표).
_FINANCE_RATIO_ANALYSIS = {
    "profitability": "7", "stability": "11", "growth": "15", "activity": "20",
}

_VALUATION_PATH = "/uapi/domestic-stock/v1/ranking/market-value"
_VALUATION_TR = "FHPST01790000"
_VALUATION_SCR = "20179"
#: 시장가치(밸류에이션) 지표 축(원장 코드표).
_VALUATION_METRIC = {
    "per": "23", "pbr": "24", "pcr": "25", "psr": "26", "eps": "27",
    "eva": "28", "ebitda": "29", "ev_ebitda": "30", "ebitda_ratio": "31",
}

_PROFIT_ASSET_PATH = "/uapi/domestic-stock/v1/ranking/profit-asset-index"
_PROFIT_ASSET_TR = "FHPST01730000"
_PROFIT_ASSET_SCR = "20173"
#: 수익자산지표 축(원장 코드표).
_PROFIT_ASSET_METRIC = {
    "sales_profit": "0", "operating_profit": "1", "ordinary_profit": "2",
    "net_income": "3", "total_assets": "4", "total_liabilities": "5", "total_equity": "6",
}

_COMPANY_TRADES_PATH = "/uapi/domestic-stock/v1/ranking/traded-by-company"
_COMPANY_TRADES_TR = "FHPST01860000"
_COMPANY_TRADES_SCR = "20186"
#: 당사매매 정렬(원장 코드표). buy=매수상위(1), sell=매도상위(0).
_COMPANY_TRADES_SORT = {"sell": "0", "buy": "1"}

_DIVIDEND_PATH = "/uapi/domestic-stock/v1/ranking/dividend-rate"
_DIVIDEND_TR = "HHKDB13470100"
#: 배당 종류(원장 GB3). cash=현금배당(2), stock=주식배당(1).
_DIVIDEND_KIND = {"cash": "2", "stock": "1"}
#: 시장(원장 GB1).
_DIVIDEND_MARKET = {"all": "0", "kospi": "1", "kospi200": "2", "kosdaq": "3"}
#: 결산/중간(원장 GB4).
_DIVIDEND_SETTLEMENT = {"all": "0", "final": "1", "interim": "2"}

_SHORT_SALE_PATH = "/uapi/domestic-stock/v1/ranking/short-sale"
_SHORT_SALE_TR = "FHPST04820000"
_SHORT_SALE_SCR = "20482"
#: 공매도 조회기간 -> (FID_PERIOD_DIV_CODE, FID_INPUT_CNT_1). 원장 코드표(D:일수 코드, M:개월).
_SHORT_SALE_WINDOW = {
    "1d": ("D", "0"), "2d": ("D", "1"), "3d": ("D", "2"), "4d": ("D", "3"),
    "1w": ("D", "4"), "2w": ("D", "9"), "3w": ("D", "14"),
    "1mo": ("M", "1"), "2mo": ("M", "2"), "3mo": ("M", "3"),
}


def fetch_fluctuation(transport: Transport, *, top: str, market: str) -> list[RankedStock]:
    """등락률 순위. ``top="gainers"`` 상승율순 / ``"losers"`` 하락율순. 최대 30건(다음조회 없음)."""
    params = {
        "FID_COND_MRKT_DIV_CODE": _market_div(market),
        "FID_COND_SCR_DIV_CODE": _FLUCTUATION_SCR,
        "FID_INPUT_ISCD": "0000",              # 전체
        "FID_RANK_SORT_CLS_CODE": _lookup(_RANK_SORT, top, "top"),
        "FID_INPUT_CNT_1": "0",
        "FID_PRC_CLS_CODE": "0",
        "FID_INPUT_PRICE_1": "", "FID_INPUT_PRICE_2": "",   # 가격 전체
        "FID_VOL_CNT": "",                      # 거래량 전체
        "FID_TRGT_CLS_CODE": "0", "FID_TRGT_EXLS_CLS_CODE": "0",
        "FID_DIV_CLS_CODE": "0",
        "FID_RSFL_RATE1": "", "FID_RSFL_RATE2": "",
    }
    return _fetch_ranking(transport, path=_FLUCTUATION_PATH, tr=_FLUCTUATION_TR, params=params)


def fetch_volume_rank(transport: Transport, *, market: str) -> list[RankedStock]:
    """거래량 순위(평균거래량 기준). 최대 30건(다음조회 없음)."""
    params = {
        "FID_COND_MRKT_DIV_CODE": _market_div(market),
        "FID_COND_SCR_DIV_CODE": _VOLUME_SCR,
        "FID_INPUT_ISCD": "0000",
        "FID_DIV_CLS_CODE": "0",
        "FID_BLNG_CLS_CODE": "0",               # 평균거래량
        "FID_TRGT_CLS_CODE": "111111111",       # 증거금 전 구간 포함
        "FID_TRGT_EXLS_CLS_CODE": "0000000000",  # 제외 없음
        "FID_INPUT_PRICE_1": "", "FID_INPUT_PRICE_2": "",
        "FID_VOL_CNT": "",
        "FID_INPUT_DATE_1": "",
    }
    return _fetch_ranking(transport, path=_VOLUME_PATH, tr=_VOLUME_TR, params=params)


def fetch_market_cap(transport: Transport, *, market: str) -> list[RankedStock]:
    """시가총액 순위. 최대 30건(다음조회 없음). 시가총액 값은 항목의 ``_raw['stck_avls']``."""
    params = {
        "FID_COND_MRKT_DIV_CODE": _market_div(market),
        "FID_COND_SCR_DIV_CODE": _MARKET_CAP_SCR,
        "FID_DIV_CLS_CODE": "0",
        "FID_INPUT_ISCD": "0000",
        "FID_TRGT_CLS_CODE": "0", "FID_TRGT_EXLS_CLS_CODE": "0",
        "FID_INPUT_PRICE_1": "", "FID_INPUT_PRICE_2": "",
        "FID_VOL_CNT": "",
    }
    return _fetch_ranking(transport, path=_MARKET_CAP_PATH, tr=_MARKET_CAP_TR, params=params)


def fetch_disparity(
    transport: Transport, *, top: str, period: int, market: str
) -> list[RankedStock]:
    """이격도 순위. ``top="highest"`` 이격도상위 / ``"lowest"`` 하위. ``period`` 이동평균 일수
    (5/10/20/60/120). 이격도 값은 각 항목의 ``_raw['d{period}_dsrt']``(%). 최대 30건(다음조회 없음)."""
    if period not in _DISPARITY_PERIODS:
        raise KisUsageError(f"period 는 5/10/20/60/120 중 하나여야 한다: {period!r}")
    params = {
        "FID_COND_MRKT_DIV_CODE": _market_div(market),
        "FID_COND_SCR_DIV_CODE": _DISPARITY_SCR,
        "FID_DIV_CLS_CODE": "0",
        "FID_RANK_SORT_CLS_CODE": _lookup(_DISPARITY_SORT, top, "top"),
        "FID_HOUR_CLS_CODE": str(period),
        "FID_INPUT_ISCD": "0000",              # 전체
        "FID_TRGT_CLS_CODE": "0", "FID_TRGT_EXLS_CLS_CODE": "0",
        "FID_INPUT_PRICE_1": "", "FID_INPUT_PRICE_2": "",   # 가격 전체
        "FID_VOL_CNT": "",                      # 거래량 전체
    }
    return _fetch_ranking(transport, path=_DISPARITY_PATH, tr=_DISPARITY_TR, params=params)


def fetch_quote_balance(transport: Transport, *, top: str, market: str) -> list[RankedStock]:
    """호가잔량 순위. ``top`` = net_buy(순매수잔량)/net_sell(순매도잔량)/buy_ratio(매수비율)/
    sell_ratio(매도비율). 잔량 지표는 ``_raw`` (total_askp_rsqn/total_bidp_rsqn/...). 최대 30건."""
    params = {
        "FID_COND_MRKT_DIV_CODE": _market_div(market),
        "FID_COND_SCR_DIV_CODE": _QUOTE_BALANCE_SCR,
        "FID_INPUT_ISCD": "0000",
        "FID_RANK_SORT_CLS_CODE": _lookup(_QUOTE_BALANCE_SORT, top, "top"),
        "FID_DIV_CLS_CODE": "0",
        "FID_TRGT_CLS_CODE": "0", "FID_TRGT_EXLS_CLS_CODE": "0",
        "FID_INPUT_PRICE_1": "", "FID_INPUT_PRICE_2": "",
        "FID_VOL_CNT": "",
    }
    return _fetch_ranking(transport, path=_QUOTE_BALANCE_PATH, tr=_QUOTE_BALANCE_TR, params=params)


def fetch_volume_power(transport: Transport, *, market: str) -> list[RankedStock]:
    """체결강도 순위(정렬 없음). 당일 체결강도는 각 항목의 ``_raw['tday_rltv']``. 최대 30건."""
    params = {
        "FID_COND_MRKT_DIV_CODE": _market_div(market),
        "FID_COND_SCR_DIV_CODE": _VOLUME_POWER_SCR,
        "FID_INPUT_ISCD": "0000",
        "FID_DIV_CLS_CODE": "0",                # 전체
        "FID_TRGT_CLS_CODE": "0", "FID_TRGT_EXLS_CLS_CODE": "0",
        "FID_INPUT_PRICE_1": "", "FID_INPUT_PRICE_2": "",
        "FID_VOL_CNT": "",
    }
    return _fetch_ranking(transport, path=_VOLUME_POWER_PATH, tr=_VOLUME_POWER_TR, params=params)


def fetch_bulk_trades(transport: Transport, *, top: str, market: str) -> list[RankedStock]:
    """대량체결건수 순위. ``top="buy"`` 매수상위 / ``"sell"`` 매도상위. 체결건수는 각 항목의
    ``_raw`` (shnu_cntg_csnu/seln_cntg_csnu/ntby_cnqn). 최대 30건(다음조회 없음)."""
    params = {
        "FID_COND_MRKT_DIV_CODE": _market_div(market),
        "FID_COND_SCR_DIV_CODE": _BULK_TRADES_SCR,
        "FID_INPUT_ISCD": "0000",              # 전체
        "FID_RANK_SORT_CLS_CODE": _lookup(_BULK_TRADES_SORT, top, "top"),
        "FID_DIV_CLS_CODE": "0",
        "FID_INPUT_ISCD_2": "",
        "FID_TRGT_CLS_CODE": "0", "FID_TRGT_EXLS_CLS_CODE": "0",
        "FID_INPUT_PRICE_1": "",                # 가격 전체
        "FID_APLY_RANG_PRC_1": "", "FID_APLY_RANG_PRC_2": "",
        "FID_VOL_CNT": "",                      # 거래량 전체
    }
    return _fetch_ranking(transport, path=_BULK_TRADES_PATH, tr=_BULK_TRADES_TR, params=params)


def fetch_interest(transport: Transport, *, market: str) -> list[RankedStock]:
    """관심종목 등록상위 순위(정렬 없음). 관심등록 건수는 각 항목의 ``_raw['inter_issu_reg_csnu']``.
    최대 30건(다음조회 없음)."""
    params = {
        "FID_COND_MRKT_DIV_CODE": _market_div(market),
        "FID_COND_SCR_DIV_CODE": _INTEREST_SCR,
        "FID_INPUT_ISCD": "0000",
        "FID_INPUT_ISCD_2": "000000",          # 관심그룹 전체
        "FID_DIV_CLS_CODE": "0",
        "FID_TRGT_CLS_CODE": "0", "FID_TRGT_EXLS_CLS_CODE": "0",
        "FID_INPUT_PRICE_1": "", "FID_INPUT_PRICE_2": "",
        "FID_VOL_CNT": "",
        "FID_INPUT_CNT_1": "1",                # 1위부터
    }
    return _fetch_ranking(transport, path=_INTEREST_PATH, tr=_INTEREST_TR, params=params)


def fetch_preferred_disparity(transport: Transport, *, market: str) -> list[RankedStock]:
    """우선주 괴리율 순위(정렬 없음). 공통필드는 본주 기준, 짝이 되는 우선주와 괴리율은 각 항목의
    ``_raw`` (prst_* 우선주 시세, diff_prpr 가격차, dprt 괴리율). 최대 30건(다음조회 없음)."""
    params = {
        "FID_COND_MRKT_DIV_CODE": _market_div(market),
        "FID_COND_SCR_DIV_CODE": _PREFERRED_DISPARITY_SCR,
        "FID_DIV_CLS_CODE": "0",
        "FID_INPUT_ISCD": "0000",
        "FID_TRGT_CLS_CODE": "0", "FID_TRGT_EXLS_CLS_CODE": "0",
        "FID_INPUT_PRICE_1": "", "FID_INPUT_PRICE_2": "",
        "FID_VOL_CNT": "",
    }
    return _fetch_ranking(
        transport, path=_PREFERRED_DISPARITY_PATH, tr=_PREFERRED_DISPARITY_TR, params=params
    )


def fetch_finance_ratio(
    transport: Transport, *, analysis: str, year: int, quarter: str, market: str
) -> list[RankedStock]:
    """재무비율 순위. ``analysis`` = profitability(수익성)/stability(안정성)/growth(성장성)/
    activity(활동성). ``year`` 회계연도, ``quarter`` q1/h1/q3/annual. 비율값은 각 항목의 ``_raw``."""
    params = _fundamentals_params(
        market=market, scr=_FINANCE_RATIO_SCR,
        sort=_lookup(_FINANCE_RATIO_ANALYSIS, analysis, "analysis"), year=year, quarter=quarter,
    )
    return _fetch_ranking(transport, path=_FINANCE_RATIO_PATH, tr=_FINANCE_RATIO_TR, params=params)


def fetch_valuation(
    transport: Transport, *, metric: str, year: int, quarter: str, market: str
) -> list[RankedStock]:
    """시장가치(밸류에이션) 순위. ``metric`` = per/pbr/pcr/psr/eps/eva/ebitda/ev_ebitda/ebitda_ratio.
    ``year`` 회계연도, ``quarter`` q1/h1/q3/annual. 지표값은 각 항목의 ``_raw`` (per/pbr/... )."""
    params = _fundamentals_params(
        market=market, scr=_VALUATION_SCR,
        sort=_lookup(_VALUATION_METRIC, metric, "metric"), year=year, quarter=quarter,
    )
    return _fetch_ranking(transport, path=_VALUATION_PATH, tr=_VALUATION_TR, params=params)


def fetch_profit_asset(
    transport: Transport, *, metric: str, year: int, quarter: str, market: str
) -> list[RankedStock]:
    """수익자산지표 순위. ``metric`` = sales_profit/operating_profit/ordinary_profit/net_income/
    total_assets/total_liabilities/total_equity. ``year`` 회계연도, ``quarter`` q1/h1/q3/annual.
    금액은 각 항목의 ``_raw`` (sale_totl_prfi/op_prfi/total_aset 등)."""
    params = _fundamentals_params(
        market=market, scr=_PROFIT_ASSET_SCR,
        sort=_lookup(_PROFIT_ASSET_METRIC, metric, "metric"), year=year, quarter=quarter,
    )
    return _fetch_ranking(transport, path=_PROFIT_ASSET_PATH, tr=_PROFIT_ASSET_TR, params=params)


def fetch_company_trades(
    transport: Transport, *, top: str, start: str | date, end: str | date, market: str
) -> list[RankedStock]:
    """당사매매종목 순위(기간). ``top="buy"`` 매수상위 / ``"sell"`` 매도상위. ``start``/``end`` 는
    조회 기간(YYYYMMDD 또는 date). 당사 매수/매도/순매수 수량은 각 항목의 ``_raw`` (shnu_cnqn_smtn/
    seln_cnqn_smtn/ntby_cnqn). 최대 30건(다음조회 없음)."""
    params = {
        "FID_COND_MRKT_DIV_CODE": _market_div(market),
        "FID_COND_SCR_DIV_CODE": _COMPANY_TRADES_SCR,
        "FID_DIV_CLS_CODE": "0",
        "FID_RANK_SORT_CLS_CODE": _lookup(_COMPANY_TRADES_SORT, top, "top"),
        "FID_INPUT_DATE_1": _to_yyyymmdd(start, "start"),
        "FID_INPUT_DATE_2": _to_yyyymmdd(end, "end"),
        "FID_INPUT_ISCD": "0000",              # 전체
        "FID_TRGT_CLS_CODE": "0", "FID_TRGT_EXLS_CLS_CODE": "0",
        "FID_APLY_RANG_VOL": "0",              # 거래량 전체
        "FID_APLY_RANG_PRC_1": "", "FID_APLY_RANG_PRC_2": "",   # 가격 전체
    }
    return _fetch_ranking(
        transport, path=_COMPANY_TRADES_PATH, tr=_COMPANY_TRADES_TR, params=params
    )


def fetch_dividend(
    transport: Transport, *, kind: str, start: str | date, end: str | date,
    market: str, settlement: str,
) -> list[DividendRanking]:
    """배당률 순위. ``kind="cash"`` 현금배당 / ``"stock"`` 주식배당. ``start``/``end`` 는 배당 기준일
    범위(YYYYMMDD 또는 date). ``market`` all/kospi/kospi200/kosdaq, ``settlement`` all/final/interim.
    시세가 없어 :class:`DividendRanking` 로 돌려준다. 최대 30건(다음조회 없음)."""
    params = {
        "CTS_AREA": "",
        "GB1": _lookup(_DIVIDEND_MARKET, market, "market"),
        "UPJONG": "0001",                      # 업종 종합(전체)
        "GB2": "0",                            # 보통주/우선주 전체
        "GB3": _lookup(_DIVIDEND_KIND, kind, "kind"),
        "F_DT": _to_yyyymmdd(start, "start"),
        "T_DT": _to_yyyymmdd(end, "end"),
        "GB4": _lookup(_DIVIDEND_SETTLEMENT, settlement, "settlement"),
    }
    resp = transport.request(
        method="GET", path=_DIVIDEND_PATH, tr_id=_DIVIDEND_TR, params=params, idempotent=True
    )
    _raise_if_error(resp)
    rows = resp.body.get("output")
    if not isinstance(rows, list):             # 성공 응답인데 배열 아님 -> fail-closed
        raise _missing_block_error("output", resp)
    return _parse_dividend(rows)


def _parse_dividend(rows: Sequence[Mapping[str, Any]]) -> list[DividendRanking]:
    ranked: list[DividendRanking] = []
    for row in rows:
        symbol = str(row.get("sht_cd", "")).strip()
        rank_text = str(row.get("rank", "")).strip()
        if not symbol or not rank_text:        # 빈 행 skip
            continue
        ranked.append(
            DividendRanking(
                rank=required_int(rank_text, "rank"),
                symbol=symbol,
                name=str(row.get("isin_name", "")).strip(),
                record_date=_parse_kst_date(str(row.get("record_date", "")).strip()),
                dividend_per_share=required_decimal(
                    row.get("per_sto_divi_amt"), "per_sto_divi_amt"
                ),
                dividend_rate=required_decimal(row.get("divi_rate"), "divi_rate"),
                dividend_kind=str(row.get("divi_kind", "")).strip(),
                _raw=row,
            )
        )
    return ranked


def fetch_short_sale(transport: Transport, *, window: str, market: str) -> list[ShortSaleRanking]:
    """공매도 순위. ``window`` 는 조회기간 1d/2d/3d/4d/1w/2w/3w/1mo/2mo/3mo. 공매도 지표를 담은
    :class:`ShortSaleRanking` 로 돌려준다(순위는 응답 순서). 최대 30건(다음조회 없음)."""
    try:
        period_code, count_code = _SHORT_SALE_WINDOW[window]
    except KeyError:
        valid = "/".join(_SHORT_SALE_WINDOW)
        raise KisUsageError(f"window 는 {valid} 중 하나여야 한다: {window!r}") from None
    params = {
        "FID_APLY_RANG_VOL": "",               # 거래량 전체
        "FID_COND_MRKT_DIV_CODE": _market_div(market),
        "FID_COND_SCR_DIV_CODE": _SHORT_SALE_SCR,
        "FID_INPUT_ISCD": "0000",              # 전체
        "FID_PERIOD_DIV_CODE": period_code,
        "FID_INPUT_CNT_1": count_code,
        "FID_TRGT_EXLS_CLS_CODE": "", "FID_TRGT_CLS_CODE": "",
        "FID_APLY_RANG_PRC_1": "", "FID_APLY_RANG_PRC_2": "",   # 가격 전체
    }
    resp = transport.request(
        method="GET", path=_SHORT_SALE_PATH, tr_id=_SHORT_SALE_TR, params=params, idempotent=True
    )
    _raise_if_error(resp)
    rows = resp.body.get("output")
    if not isinstance(rows, list):             # 성공 응답인데 배열 아님 -> fail-closed
        raise _missing_block_error("output", resp)
    return _parse_short_sale(rows)


def _parse_short_sale(rows: Sequence[Mapping[str, Any]]) -> list[ShortSaleRanking]:
    """공매도 행 -> ShortSaleRanking. 순위 필드가 없어 살아남은 행에 1부터 순번을 매긴다."""
    ranked: list[ShortSaleRanking] = []
    for row in rows:
        symbol = str(row.get("mksc_shrn_iscd", "")).strip()
        if not symbol:                         # 빈 행 skip
            continue
        sign = str(row.get("prdy_vrss_sign", "")).strip()
        ranked.append(
            ShortSaleRanking(
                rank=len(ranked) + 1,
                symbol=symbol,
                name=str(row.get("hts_kor_isnm", "")).strip(),
                price=required_decimal(row.get("stck_prpr"), "stck_prpr"),
                change=_apply_change_sign(required_decimal(row.get("prdy_vrss"), "prdy_vrss"), sign),
                change_percent=_apply_change_sign(
                    required_decimal(row.get("prdy_ctrt"), "prdy_ctrt"), sign
                ),
                volume=required_int(row.get("acml_vol"), "acml_vol"),
                short_volume=required_int(row.get("ssts_cntg_qty"), "ssts_cntg_qty"),
                short_volume_ratio=required_decimal(row.get("ssts_vol_rlim"), "ssts_vol_rlim"),
                short_value=required_decimal(row.get("ssts_tr_pbmn"), "ssts_tr_pbmn"),
                short_value_ratio=required_decimal(
                    row.get("ssts_tr_pbmn_rlim"), "ssts_tr_pbmn_rlim"
                ),
                average_price=required_decimal(row.get("avrg_prc"), "avrg_prc"),
                _raw=row,
            )
        )
    return ranked


def _fundamentals_params(
    *, market: str, scr: str, sort: str, year: int, quarter: str
) -> dict[str, str]:
    """재무·가치 순위 공통 파라미터(회계연도+분기+지표 축). 세 순위가 이 모양을 공유한다."""
    return {
        "FID_TRGT_CLS_CODE": "0",
        "FID_COND_MRKT_DIV_CODE": _market_div(market),
        "FID_COND_SCR_DIV_CODE": scr,
        "FID_INPUT_ISCD": "0000",              # 전체
        "FID_DIV_CLS_CODE": "0",
        "FID_INPUT_PRICE_1": "", "FID_INPUT_PRICE_2": "",   # 가격 전체
        "FID_VOL_CNT": "",                      # 거래량 전체
        "FID_INPUT_OPTION_1": str(year),        # 회계연도
        "FID_INPUT_OPTION_2": _lookup(_FISCAL_QUARTER, quarter, "quarter"),
        "FID_RANK_SORT_CLS_CODE": sort,
        "FID_BLNG_CLS_CODE": "0",
        "FID_TRGT_EXLS_CLS_CODE": "0",
    }


def _fetch_ranking(
    transport: Transport, *, path: str, tr: str, params: dict[str, str]
) -> list[RankedStock]:
    resp = transport.request(method="GET", path=path, tr_id=tr, params=params, idempotent=True)
    _raise_if_error(resp)
    rows = resp.body.get("output")
    if not isinstance(rows, list):  # 성공 응답인데 순위 배열 아님 -> fail-closed
        raise _missing_block_error("output", resp)
    return _parse_ranked(rows)


def _parse_ranked(rows: Sequence[Mapping[str, Any]]) -> list[RankedStock]:
    """순위 행 -> RankedStock. 종목코드 필드는 순위마다 다름(mksc_shrn_iscd/stck_shrn_iscd) -> 흡수."""
    ranked: list[RankedStock] = []
    for row in rows:
        symbol = str(row.get("mksc_shrn_iscd") or row.get("stck_shrn_iscd") or "").strip()
        rank_text = str(row.get("data_rank", "")).strip()
        if not symbol or not rank_text:  # 빈 행 skip
            continue
        sign = str(row.get("prdy_vrss_sign", "")).strip()
        ranked.append(
            RankedStock(
                rank=required_int(rank_text, "data_rank"),
                symbol=symbol,
                name=str(row.get("hts_kor_isnm", "")).strip(),
                price=required_decimal(row.get("stck_prpr"), "stck_prpr"),
                change=_apply_change_sign(required_decimal(row.get("prdy_vrss"), "prdy_vrss"), sign),
                change_percent=_apply_change_sign(
                    required_decimal(row.get("prdy_ctrt"), "prdy_ctrt"), sign
                ),
                volume=required_int(row.get("acml_vol"), "acml_vol"),
                _raw=row,
            )
        )
    return ranked


def _lookup(table: Mapping[str, str], key: str, argname: str) -> str:
    """코드표에서 사용자 값 -> KIS 코드. 미지원 값은 유효 목록과 함께 :class:`KisUsageError`."""
    try:
        return table[key]
    except KeyError:
        valid = "/".join(f'"{k}"' for k in table)
        raise KisUsageError(f"{argname} 은 {valid} 중 하나여야 한다: {key!r}") from None
