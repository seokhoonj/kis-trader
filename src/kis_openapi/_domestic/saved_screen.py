"""HTS 서버 저장 조건검색 조회(내부)."""

from __future__ import annotations

from collections.abc import Mapping

from .._wire import required_decimal, required_int
from ..errors import KISUsageError
from ..saved_screen import SavedScreen, SavedScreenStock
from ..transport import Transport
from .market_data import _apply_change_sign, _missing_block_error, _raise_if_error

_SCREENS_PATH = "/uapi/domestic-stock/v1/quotations/psearch-title"
_SCREENS_TR = "HHKST03900300"
_RESULTS_PATH = "/uapi/domestic-stock/v1/quotations/psearch-result"
_RESULTS_TR = "HHKST03900400"


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
            price=required_decimal(row.get("price"), "price"),
            change=_apply_change_sign(required_decimal(row.get("change"), "change"), sign),
            change_percent=_apply_change_sign(required_decimal(row.get("chgrate"), "chgrate"), sign),
            volume=required_int(row.get("acml_vol"), "acml_vol"),
            amount=required_decimal(row.get("trade_amt"), "trade_amt"),
            strength=required_decimal(row.get("cttr"), "cttr"),
            open=required_decimal(row.get("open"), "open"), high=required_decimal(row.get("high"), "high"),
            low=required_decimal(row.get("low"), "low"),
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
