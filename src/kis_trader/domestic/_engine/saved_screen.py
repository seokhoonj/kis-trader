"""HTS 서버 저장 조건검색 조회(내부)."""

from __future__ import annotations

from collections.abc import Mapping

from ..._internal._datetime import _parse_kst_date, _parse_kst_time
from ..._internal._response import (
    _missing_block_error,
    _raise_if_error,
)
from ..._internal._wire import (
    _apply_change_sign,
    required_decimal,
    required_int,
)
from ...errors import KISUsageError
from ...transport import RawResponse, Transport
from ..entities.saved_screen import (
    SavedScreen,
    SavedScreenStock,
    Watchlist,
    WatchlistGroup,
    WatchlistStock,
)

_SCREENS_PATH = "/uapi/domestic-stock/v1/quotations/psearch-title"
_SCREENS_TR = "HHKST03900300"
_RESULTS_PATH = "/uapi/domestic-stock/v1/quotations/psearch-result"
_RESULTS_TR = "HHKST03900400"
_WATCHLIST_GROUPS_PATH = "/uapi/domestic-stock/v1/quotations/intstock-grouplist"
_WATCHLIST_GROUPS_TR = "HHKCM113004C7"
_WATCHLIST_STOCKS_PATH = "/uapi/domestic-stock/v1/quotations/intstock-stocklist-by-group"
_WATCHLIST_STOCKS_TR = "HHKCM113004C6"


def _required(value: str, name: str) -> str:
    result = value.strip()
    if not result:
        raise KISUsageError(f"{name} 이 필요하다.")
    return result


def fetch_saved_screens(transport: Transport, *, user_id: str) -> list[SavedScreen]:
    """HTS에 서버 저장된 종목검색 조건 목록."""
    user_id = _required(user_id, "user_id")
    resp = transport.request(
        method="GET", path=_SCREENS_PATH, tr_id=_SCREENS_TR,
        params={"user_id": user_id}, idempotent=True,
    )
    _raise_if_error(resp)
    rows = resp.body.get("output2")
    if not isinstance(rows, list) or not all(isinstance(row, Mapping) for row in rows):
        raise _missing_block_error("output2", resp)
    return [SavedScreen(
        user_id=str(row.get("user_id", "")).strip(), sequence=str(row.get("seq", "")).strip(),
        group_name=str(row.get("grp_nm", "")).strip(),
        condition_name=str(row.get("condition_nm", "")).strip(), _raw=row,
    ) for row in rows]


def fetch_saved_screen_stocks(
    transport: Transport, *, user_id: str, sequence: str
) -> list[SavedScreenStock]:
    """서버 저장 조건 하나에 일치하는 종목 시세(최대 100건)."""
    user_id, sequence = _required(user_id, "user_id"), _required(sequence, "sequence")
    resp = transport.request(
        method="GET", path=_RESULTS_PATH, tr_id=_RESULTS_TR,
        params={"user_id": user_id, "seq": sequence}, idempotent=True,
    )
    _raise_if_error(resp)
    rows = resp.body.get("output2")
    if not isinstance(rows, list) or not all(isinstance(row, Mapping) for row in rows):
        raise _missing_block_error("output2", resp)
    stocks = []
    for row in rows:
        sign, expected_sign = str(row.get("daebi", "")).strip(), str(row.get("expdaebi", "")).strip()
        stocks.append(SavedScreenStock(
            symbol=str(row.get("code", "")).strip(), name=str(row.get("name", "")).strip(),
            current_price=required_decimal(row.get("price"), "price"),
            price_change=_apply_change_sign(required_decimal(row.get("change"), "change"), sign),
            change_percent=_apply_change_sign(required_decimal(row.get("chgrate"), "chgrate"), sign),
            cumulative_volume=required_int(row.get("acml_vol"), "acml_vol"),
            trading_amount=required_decimal(row.get("trade_amt"), "trade_amt"),
            execution_strength=required_decimal(row.get("cttr"), "cttr"),
            open_price=required_decimal(row.get("open"), "open"),
            high_price=required_decimal(row.get("high"), "high"),
            low_price=required_decimal(row.get("low"), "low"),
            week_52_high=required_decimal(row.get("high52"), "high52"),
            week_52_low=required_decimal(row.get("low52"), "low52"),
            expected_price=required_decimal(row.get("expprice"), "expprice"),
            expected_change=_apply_change_sign(required_decimal(row.get("expchange"), "expchange"), expected_sign),
            expected_change_percent=_apply_change_sign(required_decimal(row.get("expchggrate"), "expchggrate"), expected_sign),
            expected_volume=required_int(row.get("expcvol"), "expcvol"),
            volume_change_percent=required_decimal(row.get("chgrate2"), "chgrate2"),
            base_price=required_decimal(row.get("recprice"), "recprice"),
            upper_limit=required_decimal(row.get("uplmtprice"), "uplmtprice"),
            lower_limit=required_decimal(row.get("dnlmtprice"), "dnlmtprice"),
            market_cap=required_decimal(row.get("stotprice"), "stotprice"), _raw=row,
        ))
    return stocks


def _rows(value: object, block: str, resp: RawResponse) -> list[Mapping[str, object]]:
    if isinstance(value, Mapping):
        return [value]
    if isinstance(value, list) and all(isinstance(row, Mapping) for row in value):
        return value
    raise _missing_block_error(block, resp)


def fetch_watchlist_groups(
    transport: Transport, *, user_id: str
) -> list[WatchlistGroup]:
    """HTS 관심종목 그룹 목록."""
    user_id = _required(user_id, "user_id")
    resp = transport.request(
        method="GET", path=_WATCHLIST_GROUPS_PATH, tr_id=_WATCHLIST_GROUPS_TR,
        params={"TYPE": "1", "FID_ETC_CLS_CODE": "00", "USER_ID": user_id},
        idempotent=True,
    )
    _raise_if_error(resp)
    rows = _rows(resp.body.get("output2"), "output2", resp)
    return [WatchlistGroup(
        date=_parse_kst_date(str(row.get("date", "")).strip()),
        transmitted_at=_parse_kst_time(str(row.get("trnm_hour", "")).strip()),
        rank=required_int(row.get("data_rank"), "data_rank"),
        group_code=str(row.get("inter_grp_code", "")).strip(),
        name=str(row.get("inter_grp_name", "")).strip(),
        requested_count=required_int(row.get("ask_cnt"), "ask_cnt"), _raw=row,
    ) for row in rows]


def fetch_watchlist(
    transport: Transport, *, user_id: str, group_code: str
) -> Watchlist:
    """HTS 관심종목 그룹 하나의 요약과 구성 종목(최대 30개)."""
    user_id, group_code = _required(user_id, "user_id"), _required(group_code, "group_code")
    resp = transport.request(
        method="GET", path=_WATCHLIST_STOCKS_PATH, tr_id=_WATCHLIST_STOCKS_TR,
        params={"TYPE": "1", "USER_ID": user_id, "INTER_GRP_CODE": group_code,
                "FID_ETC_CLS_CODE": "4", "DATA_RANK": "", "INTER_GRP_NAME": "",
                "HTS_KOR_ISNM": "", "CNTG_CLS_CODE": ""}, idempotent=True,
    )
    _raise_if_error(resp)
    summary = resp.body.get("output1")
    if not isinstance(summary, Mapping):
        raise _missing_block_error("output1", resp)
    rows = _rows(resp.body.get("output2"), "output2", resp)
    return Watchlist(
        rank=required_int(summary.get("data_rank"), "data_rank"),
        name=str(summary.get("inter_grp_name", "")).strip(),
        stocks=tuple(WatchlistStock(
            market_code=str(row.get("fid_mrkt_cls_code", "")).strip(),
            rank=required_int(row.get("data_rank"), "data_rank"),
            exchange_code=str(row.get("exch_code", "")).strip(),
            symbol=str(row.get("jong_code", "")).strip(),
            color_code=str(row.get("color_code", "")).strip(), memo=str(row.get("memo", "")).strip(),
            name=str(row.get("hts_kor_isnm", "")).strip(),
            base_date_net_buy_volume=required_int(row.get("fxdt_ntby_qty"), "fxdt_ntby_qty"),
            execution_price=required_decimal(row.get("cntg_unpr"), "cntg_unpr"),
            execution_class_code=str(row.get("cntg_cls_code", "")).strip(), _raw=row,
        ) for row in rows),
    )
