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
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .._wire import required_decimal, required_int
from ..errors import KisUsageError
from ..ranked_stock import RankedStock
from ..transport import Transport
from .market_data import (
    _apply_change_sign,
    _market_div,
    _missing_block_error,
    _raise_if_error,
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


def fetch_fluctuation(transport: Transport, *, top: str, market: str) -> list[RankedStock]:
    """등락률 순위. ``top="gainers"`` 상승율순 / ``"losers"`` 하락율순. 최대 30건(다음조회 없음)."""
    params = {
        "FID_COND_MRKT_DIV_CODE": _market_div(market),
        "FID_COND_SCR_DIV_CODE": _FLUCTUATION_SCR,
        "FID_INPUT_ISCD": "0000",              # 전체
        "FID_RANK_SORT_CLS_CODE": _sort_code(top),
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


def _sort_code(top: str) -> str:
    try:
        return _RANK_SORT[top]
    except KeyError:
        raise KisUsageError(f'top 은 "gainers"/"losers" 중 하나여야 한다: {top!r}') from None
