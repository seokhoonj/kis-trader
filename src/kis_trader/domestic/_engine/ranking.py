"""국내주식 순위 조회 (내부) -- 시장 전체 순위를 :class:`RankedStock` 리스트로.

사용자면(:class:`~kis_trader.ranking.RankingQueries`)이 호출한다. 순위는 종목 단위가 아니라
시장 전체 대상이라 종목 핸들이 아닌 세션 네임스페이스(``kis.domestic.ranking.*``)에 달린다. 여러 순위가
서로 다른 KIS URL(등락률/시총은 ``/ranking/``, 거래량은 ``/quotations/``)에 흩어져 있지만
사용자에겐 하나의 "ranking" 개념으로 모은다. 각 순위는 한 페이지(대개 상위 30건)만 주고 다음
조회가 없다(KIS 명세 명시).

KIS URL/TR-ID/화면코드/코드표(KIS 명세 대조):
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
- 신용잔고: ``GET .../ranking/credit-balance`` ``FHKST17010000`` 화면 11701
  (``FID_RANK_SORT_CLS_CODE`` 0~4:융자 잔고비율/수량/금액/비율증가/비율감소, 5~9:대주 동일,
  ``FID_OPTION`` 증가율기간 2~999). 응답은 output1(헤더)+output2(목록) 중첩이라 output2를 파싱하고,
  순위 필드가 없어 응답 순서로 순위를 매겨 :class:`CreditBalanceRanking` 로 돌려준다.
- 신고/신저근접: ``GET .../ranking/near-new-highlow`` ``FHPST01870000`` 화면 20187
  (``FID_PRC_CLS_CODE`` 0:신고근접 1:신저근접). 순위 필드가 없어 응답 순서로 순위를 매기고,
  신 최고/최저가와 근접 비율을 담은 :class:`NearHighLowRanking` 로 돌려준다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from ..._internal._datetime import (
    _parse_kst_date,
    _to_yyyymmdd,
)
from ..._internal._response import (
    _missing_block_error,
    _raise_if_error,
    _require_mapping_rows,
)
from ..._internal._wire import (
    _apply_change_sign,
    optional_decimal,
    required_decimal,
    required_int,
)
from ...errors import KISUsageError
from ..entities.ranking import (
    AfterHoursBalanceRanking,
    CreditBalanceRanking,
    DividendRanking,
    NearHighLowRanking,
    OvertimeRanking,
    RankedStock,
    ShortSaleRanking,
    TopViewedStock,
)
from ...transport import Transport
from .market_data import _market_div

_FLUCTUATION_PATH = "/uapi/domestic-stock/v1/ranking/fluctuation"
_FLUCTUATION_TR = "FHPST01700000"
_FLUCTUATION_SCR = "20170"
#: 등락률 순위 정렬(KIS 코드표). gainers=상승율순(0), losers=하락율순(1).
_RANK_SORT = {"gainers": "0", "losers": "1"}

_VOLUME_PATH = "/uapi/domestic-stock/v1/quotations/volume-rank"
_VOLUME_TR = "FHPST01710000"
_VOLUME_SCR = "20171"
#: 거래량 계열 순위 기준(KIS FID_BLNG_CLS_CODE). 거래량 0 / 거래증가율 1 / 회전율 2 / 거래대금 3.
_VOLUME_BLNG = {"trading_volume": "0", "volume_growth": "1", "turnover": "2", "trading_value": "3"}

_MARKET_CAP_PATH = "/uapi/domestic-stock/v1/ranking/market-cap"
_MARKET_CAP_TR = "FHPST01740000"
_MARKET_CAP_SCR = "20174"

_DISPARITY_PATH = "/uapi/domestic-stock/v1/ranking/disparity"
_DISPARITY_TR = "FHPST01780000"
_DISPARITY_SCR = "20178"
#: 이격도 정렬(KIS 코드표). highest=이격도상위순(0), lowest=이격도하위순(1).
_DISPARITY_SORT = {"highest": "0", "lowest": "1"}
#: 이격도 기준 이동평균 일수(KIS 명세 FID_HOUR_CLS_CODE 허용값).
_DISPARITY_PERIODS = frozenset({5, 10, 20, 60, 120})

_QUOTE_BALANCE_PATH = "/uapi/domestic-stock/v1/ranking/quote-balance"
_QUOTE_BALANCE_TR = "FHPST01720000"
_QUOTE_BALANCE_SCR = "20172"
#: 호가잔량 정렬(KIS 코드표). 0:순매수잔량 1:순매도잔량 2:매수비율 3:매도비율.
_QUOTE_BALANCE_SORT = {"net_buy": "0", "net_sell": "1", "buy_ratio": "2", "sell_ratio": "3"}

_VOLUME_POWER_PATH = "/uapi/domestic-stock/v1/ranking/volume-power"
_VOLUME_POWER_TR = "FHPST01680000"
_VOLUME_POWER_SCR = "20168"

_BULK_TRADES_PATH = "/uapi/domestic-stock/v1/ranking/bulk-trans-num"
_BULK_TRADES_TR = "FHKST190900C0"
_BULK_TRADES_SCR = "11909"
#: 대량체결건수 정렬(KIS 코드표). buy=매수상위(0), sell=매도상위(1).
_BULK_TRADES_SORT = {"buy": "0", "sell": "1"}

_INTEREST_PATH = "/uapi/domestic-stock/v1/ranking/top-interest-stock"
_INTEREST_TR = "FHPST01800000"
_INTEREST_SCR = "20180"

_PREFERRED_DISPARITY_PATH = "/uapi/domestic-stock/v1/ranking/prefer-disparate-ratio"
_PREFERRED_DISPARITY_TR = "FHPST01770000"
_PREFERRED_DISPARITY_SCR = "20177"

#: 회계 분기(재무·가치 순위 공통, KIS 명세 FID_INPUT_OPTION_2). annual=결산.
_FISCAL_QUARTER = {"q1": "0", "h1": "1", "q3": "2", "annual": "3"}

_FINANCE_RATIO_PATH = "/uapi/domestic-stock/v1/ranking/finance-ratio"
_FINANCE_RATIO_TR = "FHPST01750000"
_FINANCE_RATIO_SCR = "20175"
#: 재무비율 분석 축(KIS 코드표).
_FINANCE_RATIO_ANALYSIS = {
    "profitability": "7", "stability": "11", "growth": "15", "activity": "20",
}

_VALUATION_PATH = "/uapi/domestic-stock/v1/ranking/market-value"
_VALUATION_TR = "FHPST01790000"
_VALUATION_SCR = "20179"
#: 시장가치(밸류에이션) 지표 축(KIS 코드표).
_VALUATION_METRIC = {
    "per": "23", "pbr": "24", "pcr": "25", "psr": "26", "eps": "27",
    "eva": "28", "ebitda": "29", "ev_ebitda": "30", "ebitda_ratio": "31",
}

_PROFIT_ASSET_PATH = "/uapi/domestic-stock/v1/ranking/profit-asset-index"
_PROFIT_ASSET_TR = "FHPST01730000"
_PROFIT_ASSET_SCR = "20173"
#: 수익자산지표 축(KIS 코드표).
_PROFIT_ASSET_METRIC = {
    "sales_profit": "0", "operating_profit": "1", "ordinary_profit": "2",
    "net_income": "3", "total_assets": "4", "total_liabilities": "5", "total_equity": "6",
}

_COMPANY_TRADES_PATH = "/uapi/domestic-stock/v1/ranking/traded-by-company"
_COMPANY_TRADES_TR = "FHPST01860000"
_COMPANY_TRADES_SCR = "20186"
#: 당사매매 정렬(KIS 코드표). buy=매수상위(1), sell=매도상위(0).
_COMPANY_TRADES_SORT = {"sell": "0", "buy": "1"}

_DIVIDEND_PATH = "/uapi/domestic-stock/v1/ranking/dividend-rate"
_DIVIDEND_TR = "HHKDB13470100"
#: 배당 종류(KIS 명세 GB3). cash=현금배당(2), stock=주식배당(1).
_DIVIDEND_KIND = {"cash": "2", "stock": "1"}
#: 시장(KIS 명세 GB1).
_DIVIDEND_MARKET = {"all": "0", "kospi": "1", "kospi200": "2", "kosdaq": "3"}
#: 결산/중간(KIS 명세 GB4).
_DIVIDEND_SETTLEMENT = {"all": "0", "final": "1", "interim": "2"}

_SHORT_SALE_PATH = "/uapi/domestic-stock/v1/ranking/short-sale"
_SHORT_SALE_TR = "FHPST04820000"
_SHORT_SALE_SCR = "20482"
#: 공매도 조회기간 -> (FID_PERIOD_DIV_CODE, FID_INPUT_CNT_1). KIS 코드표(D:일수 코드, M:개월).
_SHORT_SALE_WINDOW = {
    "1d": ("D", "0"), "2d": ("D", "1"), "3d": ("D", "2"), "4d": ("D", "3"),
    "1w": ("D", "4"), "2w": ("D", "9"), "3w": ("D", "14"),
    "1mo": ("M", "1"), "2mo": ("M", "2"), "3mo": ("M", "3"),
}

_CREDIT_BALANCE_PATH = "/uapi/domestic-stock/v1/ranking/credit-balance"
_CREDIT_BALANCE_TR = "FHKST17010000"
_CREDIT_BALANCE_SCR = "11701"
#: 신용잔고 정렬(KIS 코드표). margin=융자(0~4), loan=대주(5~9); ratio/shares/amount + 비율 증가/감소.
_CREDIT_BALANCE_SORT = {
    "margin_ratio": "0", "margin_shares": "1", "margin_amount": "2",
    "margin_ratio_increase": "3", "margin_ratio_decrease": "4",
    "loan_ratio": "5", "loan_shares": "6", "loan_amount": "7",
    "loan_ratio_increase": "8", "loan_ratio_decrease": "9",
}

_NEAR_HIGH_LOW_PATH = "/uapi/domestic-stock/v1/ranking/near-new-highlow"
_NEAR_HIGH_LOW_TR = "FHPST01870000"
_NEAR_HIGH_LOW_SCR = "20187"
#: 신고/신저 근접 방향(KIS 명세 FID_PRC_CLS_CODE). high=신고근접(0), low=신저근접(1).
_NEAR_HIGH_LOW_SIDE = {"high": "0", "low": "1"}


def fetch_fluctuation(transport: Transport, *, direction: str, market: str) -> list[RankedStock]:
    """등락률 순위. ``direction="gainers"`` 상승율순 / ``"losers"`` 하락율순. 최대 30건(다음조회 없음)."""
    params = {
        "FID_COND_MRKT_DIV_CODE": _market_div(market),
        "FID_COND_SCR_DIV_CODE": _FLUCTUATION_SCR,
        "FID_INPUT_ISCD": "0000",              # 전체
        "FID_RANK_SORT_CLS_CODE": _lookup(_RANK_SORT, key=direction, argname="direction"),
        "FID_INPUT_CNT_1": "0",
        # 대비 기준: "1"=전일대비(종가대비) 등락률. "0"=저가대비(반등률)이라 정렬이 전일대비
        # 등락률(우리가 표시하는 prdy_ctrt)과 어긋나므로 "1" 이어야 한다(실 API 검증).
        "FID_PRC_CLS_CODE": "1",
        "FID_INPUT_PRICE_1": "", "FID_INPUT_PRICE_2": "",   # 가격 전체
        "FID_VOL_CNT": "",                      # 거래량 전체
        "FID_TRGT_CLS_CODE": "0", "FID_TRGT_EXLS_CLS_CODE": "0",
        "FID_DIV_CLS_CODE": "0",
        "FID_RSFL_RATE1": "", "FID_RSFL_RATE2": "",
    }
    return _fetch_ranking(transport, path=_FLUCTUATION_PATH, tr=_FLUCTUATION_TR, params=params)


def fetch_volume(transport: Transport, *, metric: str = "trading_value", market: str) -> list[RankedStock]:
    """거래량 계열 순위. ``metric``: trading_volume 거래량 / trading_value 거래대금 /
    volume_growth 거래증가율 / turnover 회전율. 최대 30건(다음조회 없음)."""
    params = {
        "FID_COND_MRKT_DIV_CODE": _market_div(market),
        "FID_COND_SCR_DIV_CODE": _VOLUME_SCR,
        "FID_INPUT_ISCD": "0000",
        "FID_DIV_CLS_CODE": "0",
        "FID_BLNG_CLS_CODE": _lookup(_VOLUME_BLNG, key=metric, argname="metric"),
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
    transport: Transport, *, extreme: str, period: int, market: str
) -> list[RankedStock]:
    """이격도 순위. ``extreme="highest"`` 이격도상위 / ``"lowest"`` 하위. ``period`` 이동평균 일수
    (5/10/20/60/120). 이격도 값은 각 항목의 ``_raw['d{period}_dsrt']``(%). 최대 30건(다음조회 없음)."""
    if period not in _DISPARITY_PERIODS:
        raise KISUsageError(f"period 는 5/10/20/60/120 중 하나여야 한다: {period!r}")
    params = {
        "FID_COND_MRKT_DIV_CODE": _market_div(market),
        "FID_COND_SCR_DIV_CODE": _DISPARITY_SCR,
        "FID_DIV_CLS_CODE": "0",
        "FID_RANK_SORT_CLS_CODE": _lookup(_DISPARITY_SORT, key=extreme, argname="extreme"),
        "FID_HOUR_CLS_CODE": str(period),
        "FID_INPUT_ISCD": "0000",              # 전체
        "FID_TRGT_CLS_CODE": "0", "FID_TRGT_EXLS_CLS_CODE": "0",
        "FID_INPUT_PRICE_1": "", "FID_INPUT_PRICE_2": "",   # 가격 전체
        "FID_VOL_CNT": "",                      # 거래량 전체
    }
    return _fetch_ranking(transport, path=_DISPARITY_PATH, tr=_DISPARITY_TR, params=params)


def fetch_quote_balance(transport: Transport, *, metric: str, market: str) -> list[RankedStock]:
    """호가잔량 순위. ``metric`` = net_buy(순매수잔량)/net_sell(순매도잔량)/buy_ratio(매수비율)/
    sell_ratio(매도비율). 잔량 지표는 ``_raw`` (total_askp_rsqn/total_bidp_rsqn/...). 최대 30건."""
    params = {
        "FID_COND_MRKT_DIV_CODE": _market_div(market),
        "FID_COND_SCR_DIV_CODE": _QUOTE_BALANCE_SCR,
        "FID_INPUT_ISCD": "0000",
        "FID_RANK_SORT_CLS_CODE": _lookup(_QUOTE_BALANCE_SORT, key=metric, argname="metric"),
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


def fetch_bulk_trades(transport: Transport, *, side: str, market: str) -> list[RankedStock]:
    """대량체결건수 순위. ``side="buy"`` 매수상위 / ``"sell"`` 매도상위. 체결건수는 각 항목의
    ``_raw`` (shnu_cntg_csnu/seln_cntg_csnu/ntby_cnqn). 최대 30건(다음조회 없음)."""
    params = {
        "FID_COND_MRKT_DIV_CODE": _market_div(market),
        "FID_COND_SCR_DIV_CODE": _BULK_TRADES_SCR,
        "FID_INPUT_ISCD": "0000",              # 전체
        "FID_RANK_SORT_CLS_CODE": _lookup(_BULK_TRADES_SORT, key=side, argname="side"),
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
        sort=_lookup(_FINANCE_RATIO_ANALYSIS, key=analysis, argname="analysis"), year=year, quarter=quarter,
    )
    return _fetch_ranking(transport, path=_FINANCE_RATIO_PATH, tr=_FINANCE_RATIO_TR, params=params)


def fetch_valuation(
    transport: Transport, *, metric: str, year: int, quarter: str, market: str
) -> list[RankedStock]:
    """시장가치(밸류에이션) 순위. ``metric`` = per/pbr/pcr/psr/eps/eva/ebitda/ev_ebitda/ebitda_ratio.
    ``year`` 회계연도, ``quarter`` q1/h1/q3/annual. 지표값은 각 항목의 ``_raw`` (per/pbr/... )."""
    params = _fundamentals_params(
        market=market, scr=_VALUATION_SCR,
        sort=_lookup(_VALUATION_METRIC, key=metric, argname="metric"), year=year, quarter=quarter,
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
        sort=_lookup(_PROFIT_ASSET_METRIC, key=metric, argname="metric"), year=year, quarter=quarter,
    )
    return _fetch_ranking(transport, path=_PROFIT_ASSET_PATH, tr=_PROFIT_ASSET_TR, params=params)


def fetch_company_trades(
    transport: Transport, *, side: str, start: str | date, end: str | date, market: str
) -> list[RankedStock]:
    """당사매매종목 순위(기간). ``side="buy"`` 매수상위 / ``"sell"`` 매도상위. ``start``/``end`` 는
    조회 기간(YYYYMMDD 또는 date). 당사 매수/매도/순매수 수량은 각 항목의 ``_raw`` (shnu_cnqn_smtn/
    seln_cnqn_smtn/ntby_cnqn). 최대 30건(다음조회 없음)."""
    start_date = _to_yyyymmdd(start, "start")
    end_date = _to_yyyymmdd(end, "end")
    if start_date > end_date:                  # 뒤집힌 기간 -> I/O 전 fail-closed
        raise KISUsageError(f"start({start_date}) 가 end({end_date}) 보다 늦다.")
    params = {
        "FID_COND_MRKT_DIV_CODE": _market_div(market),
        "FID_COND_SCR_DIV_CODE": _COMPANY_TRADES_SCR,
        "FID_DIV_CLS_CODE": "0",
        "FID_RANK_SORT_CLS_CODE": _lookup(_COMPANY_TRADES_SORT, key=side, argname="side"),
        "FID_INPUT_DATE_1": start_date,
        "FID_INPUT_DATE_2": end_date,
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
    start_date = _to_yyyymmdd(start, "start")
    end_date = _to_yyyymmdd(end, "end")
    if start_date > end_date:                  # 뒤집힌 기간 -> I/O 전 fail-closed
        raise KISUsageError(f"start({start_date}) 가 end({end_date}) 보다 늦다.")
    params = {
        "CTS_AREA": "",
        "GB1": _lookup(_DIVIDEND_MARKET, key=market, argname="market"),
        "UPJONG": "0001",                      # 업종 종합(전체)
        "GB2": "0",                            # 보통주/우선주 전체
        "GB3": _lookup(_DIVIDEND_KIND, key=kind, argname="kind"),
        "F_DT": start_date,
        "T_DT": end_date,
        "GB4": _lookup(_DIVIDEND_SETTLEMENT, key=settlement, argname="settlement"),
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
        raise KISUsageError(f"window 는 {valid} 중 하나여야 한다: {window!r}") from None
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


def fetch_credit_balance(
    transport: Transport, *, metric: str, days: int, market: str
) -> list[CreditBalanceRanking]:
    """신용잔고 순위. ``metric`` = margin_*(융자) / loan_*(대주) x ratio/shares/amount/ratio_increase/
    ratio_decrease. ``days`` 는 증가율 계산 기간(2~999). 응답 output2를 :class:`CreditBalanceRanking`
    로 돌려준다(순위는 응답 순서). 최대 30건(다음조회 없음)."""
    if not 2 <= days <= 999:                   # KIS 명세 증가율기간 범위 -> I/O 전 fail-closed
        raise KISUsageError(f"days 는 2~999 범위여야 한다: {days!r}")
    params = {
        "FID_COND_SCR_DIV_CODE": _CREDIT_BALANCE_SCR,
        "FID_INPUT_ISCD": "0000",              # 전체
        "FID_OPTION": str(days),
        "FID_COND_MRKT_DIV_CODE": _market_div(market),
        "FID_RANK_SORT_CLS_CODE": _lookup(_CREDIT_BALANCE_SORT, key=metric, argname="metric"),
    }
    resp = transport.request(
        method="GET", path=_CREDIT_BALANCE_PATH, tr_id=_CREDIT_BALANCE_TR,
        params=params, idempotent=True,
    )
    _raise_if_error(resp)
    rows = resp.body.get("output2")            # output1=헤더, output2=종목 목록
    if not isinstance(rows, list):             # 성공 응답인데 목록 아님 -> fail-closed
        raise _missing_block_error("output2", resp)
    return _parse_credit_balance(rows)


def _parse_credit_balance(rows: Sequence[Mapping[str, Any]]) -> list[CreditBalanceRanking]:
    ranked: list[CreditBalanceRanking] = []
    for row in rows:
        symbol = str(row.get("mksc_shrn_iscd", "")).strip()
        if not symbol:                         # 빈 행 skip
            continue
        sign = str(row.get("prdy_vrss_sign", "")).strip()
        ranked.append(
            CreditBalanceRanking(
                rank=len(ranked) + 1,
                symbol=symbol,
                name=str(row.get("hts_kor_isnm", "")).strip(),
                price=required_decimal(row.get("stck_prpr"), "stck_prpr"),
                change=_apply_change_sign(required_decimal(row.get("prdy_vrss"), "prdy_vrss"), sign),
                change_percent=_apply_change_sign(
                    required_decimal(row.get("prdy_ctrt"), "prdy_ctrt"), sign
                ),
                volume=required_int(row.get("acml_vol"), "acml_vol"),
                margin_loan_shares=required_int(
                    row.get("whol_loan_rmnd_stcn"), "whol_loan_rmnd_stcn"
                ),
                margin_loan_amount=required_decimal(
                    row.get("whol_loan_rmnd_amt"), "whol_loan_rmnd_amt"
                ),
                margin_loan_ratio=required_decimal(
                    row.get("whol_loan_rmnd_rate"), "whol_loan_rmnd_rate"
                ),
                stock_loan_shares=required_int(
                    row.get("whol_stln_rmnd_stcn"), "whol_stln_rmnd_stcn"
                ),
                stock_loan_amount=required_decimal(
                    row.get("whol_stln_rmnd_amt"), "whol_stln_rmnd_amt"
                ),
                stock_loan_ratio=required_decimal(
                    row.get("whol_stln_rmnd_rate"), "whol_stln_rmnd_rate"
                ),
                _raw=row,
            )
        )
    return ranked


def fetch_near_high_low(
    transport: Transport, *, side: str, market: str
) -> list[NearHighLowRanking]:
    """신고/신저 근접 순위. ``side="high"`` 신고근접 / ``"low"`` 신저근접. 신 최고/최저가와 근접
    비율을 담은 :class:`NearHighLowRanking` 로 돌려준다(순위는 응답 순서). 최대 30건(다음조회 없음)."""
    params = {
        "FID_APLY_RANG_VOL": "0",              # 거래량 전체
        "FID_COND_MRKT_DIV_CODE": _market_div(market),
        "FID_COND_SCR_DIV_CODE": _NEAR_HIGH_LOW_SCR,
        "FID_DIV_CLS_CODE": "0",               # 전체
        "FID_INPUT_CNT_1": "", "FID_INPUT_CNT_2": "",   # 근접범위 전체
        "FID_PRC_CLS_CODE": _lookup(_NEAR_HIGH_LOW_SIDE, key=side, argname="side"),
        "FID_TRGT_CLS_CODE": "0", "FID_TRGT_EXLS_CLS_CODE": "0",
    }
    resp = transport.request(
        method="GET", path=_NEAR_HIGH_LOW_PATH, tr_id=_NEAR_HIGH_LOW_TR,
        params=params, idempotent=True,
    )
    _raise_if_error(resp)
    rows = resp.body.get("output")
    if not isinstance(rows, list):             # 성공 응답인데 목록 아님 -> fail-closed
        raise _missing_block_error("output", resp)
    return _parse_near_high_low(rows)


def _parse_near_high_low(rows: Sequence[Mapping[str, Any]]) -> list[NearHighLowRanking]:
    ranked: list[NearHighLowRanking] = []
    for row in rows:
        symbol = str(row.get("mksc_shrn_iscd", "")).strip()
        if not symbol:                         # 빈 행 skip
            continue
        sign = str(row.get("prdy_vrss_sign", "")).strip()
        ranked.append(
            NearHighLowRanking(
                rank=len(ranked) + 1,
                symbol=symbol,
                name=str(row.get("hts_kor_isnm", "")).strip(),
                price=required_decimal(row.get("stck_prpr"), "stck_prpr"),
                change=_apply_change_sign(required_decimal(row.get("prdy_vrss"), "prdy_vrss"), sign),
                change_percent=_apply_change_sign(
                    required_decimal(row.get("prdy_ctrt"), "prdy_ctrt"), sign
                ),
                volume=required_int(row.get("acml_vol"), "acml_vol"),
                new_high=required_decimal(row.get("new_hgpr"), "new_hgpr"),
                high_near_rate=required_decimal(row.get("hprc_near_rate"), "hprc_near_rate"),
                new_low=required_decimal(row.get("new_lwpr"), "new_lwpr"),
                low_near_rate=required_decimal(row.get("lwpr_near_rate"), "lwpr_near_rate"),
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
        "FID_INPUT_OPTION_2": _lookup(_FISCAL_QUARTER, key=quarter, argname="quarter"),
        "FID_RANK_SORT_CLS_CODE": sort,
        "FID_BLNG_CLS_CODE": "0",
        "FID_TRGT_EXLS_CLS_CODE": "0",
    }


def _fetch_rows(
    transport: Transport, *, path: str, tr_id: str, params: Mapping[str, str], output_key: str
) -> list[Mapping[str, Any]]:
    """GET 요청 + 봉투 오류 조기종료 + 행 배열 블록 검증(:func:`_require_mapping_rows`)까지 한 번에.
    순위 필드 없이 응답 순서로 순번을 매기는 조회들이 공유한다."""
    resp = transport.request(method="GET", path=path, tr_id=tr_id, params=params, idempotent=True)
    _raise_if_error(resp)
    return _require_mapping_rows(output_key, resp)


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
                trading_value=optional_decimal(row.get("acml_tr_pbmn"), "acml_tr_pbmn"),
                _raw=row,
            )
        )
    return ranked


def _lookup(table: Mapping[str, str], *, key: str, argname: str) -> str:
    """코드표에서 사용자 값 -> KIS 코드. 미지원 값은 유효 목록과 함께 :class:`KISUsageError`."""
    try:
        return table[key]
    except KeyError:
        valid = "/".join(f'"{k}"' for k in table)
        raise KISUsageError(f"{argname} 은 {valid} 중 하나여야 한다: {key!r}") from None


# --- 예상체결/시간외 순위 (마무리 6종) --------------------------------------
_EXP_UPDOWN_PATH = "/uapi/domestic-stock/v1/ranking/exp-trans-updown"
_EXP_UPDOWN_TR = "FHPST01820000"
#: 예상체결 상승/하락 정렬(FID_RANK_SORT_CLS_CODE).
_EXP_UPDOWN_TOP = {"up": "0", "down": "1"}
_EXPECTED_CLOSE_PATH = "/uapi/domestic-stock/v1/quotations/exp-closing-price"
_EXPECTED_CLOSE_TR = "FHKST117300C0"
_EXPECTED_CLOSE_FILTER = {
    "all": "0",
    "upper_limit": "1",
    "lower_limit": "2",
    "up": "3",
    "down": "4",
}
_EXPECTED_CLOSE_MARKET = {
    "all": "0000",
    "KOSPI": "0001",
    "KOSDAQ": "1001",
    "KOSPI200": "2001",
    "KRX100": "4001",
}


def fetch_expected_execution_change(
    transport: Transport, *, direction: str, market: str
) -> list[RankedStock]:
    """장 시작 전 예상체결 기준 상승/하락 상위. ``direction="up"`` 상승 / ``"down"`` 하락. 예상체결가를
    현재가로, 예상체결량(cntg_vol)을 거래량으로 담는다(:class:`RankedStock`, 순위는 응답 순서)."""
    params = {
        "FID_RANK_SORT_CLS_CODE": _lookup(_EXP_UPDOWN_TOP, key=direction, argname="direction"),
        "FID_COND_MRKT_DIV_CODE": _market_div(market),
        "FID_COND_SCR_DIV_CODE": "20182",
        "FID_INPUT_ISCD": "0000",
        "FID_DIV_CLS_CODE": "0",
        "FID_APLY_RANG_PRC_1": "", "FID_VOL_CNT": "", "FID_PBMN": "",
        "FID_BLNG_CLS_CODE": "0", "FID_MKOP_CLS_CODE": "0",
    }
    rows = _fetch_rows(
        transport, path=_EXP_UPDOWN_PATH, tr_id=_EXP_UPDOWN_TR, params=params, output_key="output"
    )
    ranked: list[RankedStock] = []
    for row in rows:
        symbol = str(row.get("stck_shrn_iscd", "")).strip()
        if not symbol:
            continue
        sign = str(row.get("prdy_vrss_sign", "")).strip()
        ranked.append(
            RankedStock(
                rank=len(ranked) + 1,
                symbol=symbol,
                name=str(row.get("hts_kor_isnm", "")).strip(),
                price=required_decimal(row.get("stck_prpr"), "stck_prpr"),
                change=_apply_change_sign(required_decimal(row.get("prdy_vrss"), "prdy_vrss"), sign),
                change_percent=_apply_change_sign(
                    required_decimal(row.get("prdy_ctrt"), "prdy_ctrt"), sign
                ),
                volume=required_int(row.get("cntg_vol"), "cntg_vol"),   # 예상체결량
                _raw=row,
            )
        )
    return ranked


def fetch_expected_close(
    transport: Transport,
    *,
    filter_: str,
    market: str,
    extended_range: bool,
) -> list[RankedStock]:
    """장마감 예상체결 종목 목록과 직전·기준가 대비."""
    filter_code = _lookup(_EXPECTED_CLOSE_FILTER, key=filter_, argname="filter")
    market_code = _lookup(_EXPECTED_CLOSE_MARKET, key=market, argname="market")
    rows = _fetch_rows(
        transport,
        path=_EXPECTED_CLOSE_PATH,
        tr_id=_EXPECTED_CLOSE_TR,
        params={
            "FID_RANK_SORT_CLS_CODE": filter_code,
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_COND_SCR_DIV_CODE": "11173",
            "FID_INPUT_ISCD": market_code,
            "FID_BLNG_CLS_CODE": "1" if extended_range else "0",
        },
        output_key="output1",
    )
    ranked: list[RankedStock] = []
    for row in rows:
        symbol = str(row.get("stck_shrn_iscd", "")).strip()
        if not symbol:
            continue
        sign = str(row.get("prdy_vrss_sign", "")).strip()
        ranked.append(
            RankedStock(
                rank=len(ranked) + 1,
                symbol=symbol,
                name=str(row.get("hts_kor_isnm", "")).strip(),
                price=required_decimal(row.get("stck_prpr"), "stck_prpr"),
                change=_apply_change_sign(
                    required_decimal(row.get("prdy_vrss"), "prdy_vrss"), sign
                ),
                change_percent=_apply_change_sign(
                    required_decimal(row.get("prdy_ctrt"), "prdy_ctrt"), sign
                ),
                volume=required_int(row.get("cntg_vol"), "cntg_vol"),
                _raw=row,
            )
        )
    return ranked


# 시간외 순위 3종: 블록/필드 키/파라미터가 조금씩 다르다(KIS 요청예시 대조).
#   등락률: SCR 20234, output2, ovtm_untp_prpr/prdy_vrss/vol, 코드 mksc_shrn_iscd, 정렬 FID_DIV_CLS_CODE
#   거래량: SCR 20235, output2, 같은 필드, 코드 stck_shrn_iscd, 정렬 FID_RANK_SORT_CLS_CODE
#   예상체결: SCR 11186, output(flat), ovtm_untp_antc_cnpr/cntg_vrss/cnqn, 코드 stck_shrn_iscd
_OVERTIME_CHANGE = {  # 시간외등락률순위 정렬(FID_DIV_CLS_CODE)
    "up": "2", "down": "3",
}


def _parse_overtime(
    rows: Sequence[Mapping[str, Any]], *,
    price_key: str, change_key: str, sign_key: str, ctrt_key: str, vol_key: str,
) -> list[OvertimeRanking]:
    ranked: list[OvertimeRanking] = []
    for row in rows:
        symbol = str(row.get("mksc_shrn_iscd") or row.get("stck_shrn_iscd") or "").strip()
        if not symbol:
            continue
        sign = str(row.get(sign_key, "")).strip()
        ranked.append(
            OvertimeRanking(
                rank=len(ranked) + 1,
                symbol=symbol,
                name=str(row.get("hts_kor_isnm", "")).strip(),
                overtime_price=required_decimal(row.get(price_key), price_key),
                overtime_change=_apply_change_sign(
                    required_decimal(row.get(change_key), change_key), sign
                ),
                overtime_change_percent=_apply_change_sign(
                    required_decimal(row.get(ctrt_key), ctrt_key), sign
                ),
                overtime_volume=required_int(row.get(vol_key), vol_key),
                _raw=row,
            )
        )
    return ranked


def fetch_overtime_change(
    transport: Transport, *, direction: str, market: str
) -> list[OvertimeRanking]:
    """시간외 단일가 등락률 순위. ``direction="up"`` 상승 / ``"down"`` 하락(:class:`OvertimeRanking`)."""
    params = {
        "FID_COND_MRKT_DIV_CODE": _market_div(market),
        "FID_MRKT_CLS_CODE": "",
        "FID_COND_SCR_DIV_CODE": "20234",
        "FID_INPUT_ISCD": "0000",
        "FID_DIV_CLS_CODE": _lookup(_OVERTIME_CHANGE, key=direction, argname="direction"),
        "FID_INPUT_PRICE_1": "", "FID_INPUT_PRICE_2": "",
        "FID_VOL_CNT": "", "FID_TRGT_CLS_CODE": "", "FID_TRGT_EXLS_CLS_CODE": "",
    }
    resp = transport.request(
        method="GET", path="/uapi/domestic-stock/v1/ranking/overtime-fluctuation",
        tr_id="FHPST02340000", params=params, idempotent=True,
    )
    _raise_if_error(resp)
    rows = resp.body.get("output2")
    if not isinstance(rows, list):
        raise _missing_block_error("output2", resp)
    return _parse_overtime(
        rows, price_key="ovtm_untp_prpr", change_key="ovtm_untp_prdy_vrss",
        sign_key="ovtm_untp_prdy_vrss_sign", ctrt_key="ovtm_untp_prdy_ctrt",
        vol_key="ovtm_untp_vol",
    )


def fetch_overtime_volume(transport: Transport, *, market: str) -> list[OvertimeRanking]:
    """시간외 단일가 거래량 순위(:class:`OvertimeRanking`)."""
    params = {
        "FID_COND_MRKT_DIV_CODE": _market_div(market),
        "FID_COND_SCR_DIV_CODE": "20235",
        "FID_INPUT_ISCD": "0000",
        "FID_RANK_SORT_CLS_CODE": "0",
        "FID_INPUT_PRICE_1": "", "FID_INPUT_PRICE_2": "",
        "FID_VOL_CNT": "", "FID_TRGT_CLS_CODE": "", "FID_TRGT_EXLS_CLS_CODE": "",
    }
    resp = transport.request(
        method="GET", path="/uapi/domestic-stock/v1/ranking/overtime-volume",
        tr_id="FHPST02350000", params=params, idempotent=True,
    )
    _raise_if_error(resp)
    rows = resp.body.get("output2")
    if not isinstance(rows, list):
        raise _missing_block_error("output2", resp)
    return _parse_overtime(
        rows, price_key="ovtm_untp_prpr", change_key="ovtm_untp_prdy_vrss",
        sign_key="ovtm_untp_prdy_vrss_sign", ctrt_key="ovtm_untp_prdy_ctrt",
        vol_key="ovtm_untp_vol",
    )


def fetch_overtime_expected_change(
    transport: Transport, *, direction: str, market: str
) -> list[OvertimeRanking]:
    """시간외 예상체결 등락률 순위. ``direction="up"`` 상승 / ``"down"`` 하락. 시간외 예상체결가·예상체결량
    을 담는다(:class:`OvertimeRanking`)."""
    params = {
        "FID_COND_MRKT_DIV_CODE": _market_div(market),
        "FID_COND_SCR_DIV_CODE": "11186",
        "FID_INPUT_ISCD": "0000",
        "FID_RANK_SORT_CLS_CODE": _lookup(_EXP_UPDOWN_TOP, key=direction, argname="direction"),
        "FID_DIV_CLS_CODE": "0",
        "FID_INPUT_PRICE_1": "", "FID_INPUT_PRICE_2": "", "FID_INPUT_VOL_1": "",
    }
    resp = transport.request(
        method="GET", path="/uapi/domestic-stock/v1/ranking/overtime-exp-trans-fluct",
        tr_id="FHKST11860000", params=params, idempotent=True,
    )
    _raise_if_error(resp)
    rows = resp.body.get("output")
    if not isinstance(rows, list):
        raise _missing_block_error("output", resp)
    return _parse_overtime(
        rows, price_key="ovtm_untp_antc_cnpr", change_key="ovtm_untp_antc_cntg_vrss",
        sign_key="ovtm_untp_antc_cntg_vrss_sign", ctrt_key="ovtm_untp_antc_cntg_ctrt",
        vol_key="ovtm_untp_antc_cnqn",
    )


_AFTER_HOUR_TOP = {"ask": "1", "bid": "2"}   # FID_RANK_SORT_CLS_CODE (매도잔량/매수잔량 상위)


def fetch_after_hour_balance(
    transport: Transport, *, side: str, market: str
) -> list[AfterHoursBalanceRanking]:
    """시간외 잔량 순위. ``side="ask"`` 매도잔량 상위 / ``"bid"`` 매수잔량 상위. 시간외 총 매도/매수
    잔량과 장전/장후 체결량을 담는다(:class:`AfterHoursBalanceRanking`)."""
    params = {
        "FID_INPUT_PRICE_1": "",
        "FID_COND_MRKT_DIV_CODE": _market_div(market),
        "FID_COND_SCR_DIV_CODE": "20176",
        "FID_RANK_SORT_CLS_CODE": _lookup(_AFTER_HOUR_TOP, key=side, argname="side"),
        "FID_DIV_CLS_CODE": "0",
        "FID_INPUT_ISCD": "0000",
        "FID_TRGT_EXLS_CLS_CODE": "0", "FID_TRGT_CLS_CODE": "0",
        "FID_VOL_CNT": "", "FID_INPUT_PRICE_2": "",
    }
    rows = _fetch_rows(
        transport, path="/uapi/domestic-stock/v1/ranking/after-hour-balance",
        tr_id="FHPST01760000", params=params, output_key="output",
    )
    ranked: list[AfterHoursBalanceRanking] = []
    for row in rows:
        symbol = str(row.get("stck_shrn_iscd", "")).strip()
        if not symbol:
            continue
        sign = str(row.get("prdy_vrss_sign", "")).strip()
        ranked.append(
            AfterHoursBalanceRanking(
                rank=len(ranked) + 1,
                symbol=symbol,
                name=str(row.get("hts_kor_isnm", "")).strip(),
                price=required_decimal(row.get("stck_prpr"), "stck_prpr"),
                change=_apply_change_sign(required_decimal(row.get("prdy_vrss"), "prdy_vrss"), sign),
                change_percent=_apply_change_sign(
                    required_decimal(row.get("prdy_ctrt"), "prdy_ctrt"), sign
                ),
                overtime_ask_residual=required_int(
                    row.get("ovtm_total_askp_rsqn"), "ovtm_total_askp_rsqn"
                ),
                overtime_bid_residual=required_int(
                    row.get("ovtm_total_bidp_rsqn"), "ovtm_total_bidp_rsqn"
                ),
                pre_market_volume=required_int(row.get("mkob_otcp_vol"), "mkob_otcp_vol"),
                post_market_volume=required_int(row.get("mkfa_otcp_vol"), "mkfa_otcp_vol"),
                _raw=row,
            )
        )
    return ranked


def fetch_most_viewed(transport: Transport) -> list[TopViewedStock]:
    """HTS 조회 상위 종목(관심 상위). 코드와 시장구분만 준다(:class:`TopViewedStock`). 파라미터 없음."""
    rows = _fetch_rows(
        transport, path="/uapi/domestic-stock/v1/ranking/hts-top-view",
        tr_id="HHMCM000100C0", params={}, output_key="output1",
    )
    ranked: list[TopViewedStock] = []
    for row in rows:
        symbol = str(row.get("mksc_shrn_iscd", "")).strip()
        if not symbol:
            continue
        ranked.append(
            TopViewedStock(
                rank=len(ranked) + 1,
                symbol=symbol,
                market=str(row.get("mrkt_div_cls_code", "")).strip(),
                _raw=row,
            )
        )
    return ranked
