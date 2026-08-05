"""국내주식 계좌 조회 (내부) -- 잔고/보유종목/매수가능/매도가능.

사용자면(KISClient/Ticker)이 이 함수들을 호출해 통합 반환 타입을 받는다. 계좌 식별정보
(``cano``/``product_code``)와 환경(``environment``)은 세션에서 온다. KIS 원본 필드 매핑과
fail-closed 파싱은 여기 갇힌다.

KIS URL/TR-id:
- 잔고: ``GET .../trading/inquire-balance`` (실전 ``TTTC8434R`` / 모의 ``VTTC8434R``).
- 매수가능: ``GET .../trading/inquire-psbl-order`` (실전 ``TTTC8908R`` / 모의 ``VTTC8908R``).
- 매도가능수량: ``GET .../trading/inquire-psbl-sell`` (``TTTC8408R``, **모의 미지원**).
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from .._wire import format_wire_decimal, optional_decimal, required_decimal
from ..balance import Balance, Portfolio, Position
from ..errors import KISError, KISUsageError
from ..orderable import BuyableAmount, SellableQuantity
from ..transport import Environment, RawResponse, Transport

_BALANCE_PATH = "/uapi/domestic-stock/v1/trading/inquire-balance"
_BALANCE_TR = {"real": "TTTC8434R", "demo": "VTTC8434R"}
#: 잔고 종목배열 연속조회 페이지 상한. 여기 닿으면 부분 결과로 자르지 않고 fail-closed.
_MAX_BALANCE_PAGES = 100

_BUYABLE_PATH = "/uapi/domestic-stock/v1/trading/inquire-psbl-order"
_BUYABLE_TR = {"real": "TTTC8908R", "demo": "VTTC8908R"}
_SELLABLE_PATH = "/uapi/domestic-stock/v1/trading/inquire-psbl-sell"
_SELLABLE_TR = "TTTC8408R"  # 모의투자 미지원 -- demo TR 없음


# --- 잔고 / 보유종목 / 포트폴리오 -----------------------------------------
def fetch_balance(transport: Transport, *, cano: str, product_code: str, environment: Environment) -> Balance:
    """계좌 현금·자산 요약(1콜, output2). 요약은 계좌 단위라 첫 페이지로 완결."""
    resp = _fetch_balance_page(transport, cano, product_code, environment, "", "")
    _raise_if_error(resp)
    summary = _extract_summary(resp.body)
    if summary is None:
        raise KISError(
            "잔고 응답에 계좌 요약(output2)이 없다.",
            rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
        )
    return _parse_balance(summary)


def fetch_positions(transport: Transport, *, cano: str, product_code: str, environment: Environment) -> list[Position]:
    """보유 종목 전체(연속조회 소진까지). 0수량 잔여 lot 포함."""
    rows, _summary = _walk_holdings(transport, cano, product_code, environment)
    return _parse_positions(rows)


def fetch_portfolio(transport: Transport, *, cano: str, product_code: str, environment: Environment) -> Portfolio:
    """현금·자산 요약과 보유 종목을 한 번의 조회 순회로 함께."""
    rows, summary = _walk_holdings(transport, cano, product_code, environment)
    if summary is None:
        raise KISError("잔고 응답에 계좌 요약(output2)이 없다.")
    return Portfolio(balance=_parse_balance(summary), positions=tuple(_parse_positions(rows)))


def _walk_holdings(
    transport: Transport, cano: str, product_code: str, environment: Environment
) -> tuple[list[Mapping[str, Any]], Mapping[str, Any] | None]:
    rows: list[Mapping[str, Any]] = []
    summary: Mapping[str, Any] | None = None
    ctx_fk, ctx_nk = "", ""
    for _page in range(_MAX_BALANCE_PAGES):
        resp = _fetch_balance_page(transport, cano, product_code, environment, ctx_fk, ctx_nk)
        _raise_if_error(resp)
        if summary is None:  # 계좌 요약은 첫 페이지에서(계좌 단위라 페이지 불변)
            summary = _extract_summary(resp.body)
        page = resp.body.get("output1")
        if not isinstance(page, list):  # 빈 계좌도 output1 을 빈 배열로 준다 -> 부재/비배열은 손상
            raise KISError(
                "잔고 응답의 output1 이 종목 배열이 아니다.",
                rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
            )
        rows.extend(page)
        # JSON null 은 str(...) 로 "None"(truthy) 이 되니, None 을 먼저 ""로 눌러 종료 판정을 지킨다.
        ctx_nk = str(resp.body.get("ctx_area_nk100") or "").strip()
        ctx_fk = str(resp.body.get("ctx_area_fk100") or "").strip()
        if not ctx_nk:
            break
    else:
        raise KISError(
            f"잔고 조회가 {_MAX_BALANCE_PAGES}페이지 상한에 도달했으나 연속조회가 남아있다 "
            f"-- 부분 결과로 자르지 않는다. 재시도하거나 수동 확인하라."
        )
    return rows, summary


def _fetch_balance_page(
    transport: Transport, cano: str, product_code: str, environment: Environment, ctx_fk: str, ctx_nk: str
) -> RawResponse:
    params = {
        "CANO": cano, "ACNT_PRDT_CD": product_code,
        "AFHR_FLPR_YN": "N", "OFL_YN": "", "INQR_DVSN": "02",
        "UNPR_DVSN": "01", "FUND_STTL_ICLD_YN": "N", "FNCG_AMT_AUTO_RDPT_YN": "N",
        "PRCS_DVSN": "00", "CTX_AREA_FK100": ctx_fk, "CTX_AREA_NK100": ctx_nk,
    }
    return transport.request(
        method="GET", path=_BALANCE_PATH, tr_id=_BALANCE_TR[environment],
        params=params, idempotent=True,
    )


def _parse_positions(rows: list[Mapping[str, Any]]) -> list[Position]:
    positions: list[Position] = []
    for row in rows:
        symbol = str(row.get("pdno", "")).strip()
        if not symbol:  # 종목코드 없는 패딩 행 -- 건너뜀
            continue
        positions.append(
            # 종목별 수치는 빈 값을 0으로 읽는다 -- 정산 완료된 0수량 잔여 lot 등에서 일부 필드가
            # 빌 수 있는데, 그 한 종목 때문에 보유목록 전체 조회가 깨지면 안 된다(값이 있는데
            # 파싱 실패면 여전히 예외). 종목 식별은 위의 빈 pdno 가드가 이미 처리.
            Position(
                symbol=symbol,
                security_name=str(row.get("prdt_name", "")).strip(),
                currency="KRW",
                quantity=_decimal_or_zero(row.get("hldg_qty"), "hldg_qty"),
                sellable_quantity=_decimal_or_zero(row.get("ord_psbl_qty"), "ord_psbl_qty"),
                average_purchase_price=_decimal_or_zero(row.get("pchs_avg_pric"), "pchs_avg_pric"),
                purchase_amount=_decimal_or_zero(row.get("pchs_amt"), "pchs_amt"),
                current_price=_decimal_or_zero(row.get("prpr"), "prpr"),
                market_value=_decimal_or_zero(row.get("evlu_amt"), "evlu_amt"),
                unrealized_pnl=_decimal_or_zero(row.get("evlu_pfls_amt"), "evlu_pfls_amt"),
                unrealized_pnl_percent=_decimal_or_zero(row.get("evlu_pfls_rt"), "evlu_pfls_rt"),
                _raw=row,
            )
        )
    return positions


def _parse_balance(summary: Mapping[str, Any]) -> Balance:
    return Balance(
        currency="KRW",
        deposit=required_decimal(summary.get("dnca_tot_amt"), "dnca_tot_amt"),
        settlement_cash_d1=required_decimal(summary.get("nxdy_excc_amt"), "nxdy_excc_amt"),
        settlement_cash_d2=required_decimal(summary.get("prvs_rcdl_excc_amt"), "prvs_rcdl_excc_amt"),
        total_evaluation=required_decimal(summary.get("tot_evlu_amt"), "tot_evlu_amt"),
        net_asset=required_decimal(summary.get("nass_amt"), "nass_amt"),
        purchase_amount=required_decimal(summary.get("pchs_amt_smtl_amt"), "pchs_amt_smtl_amt"),
        market_value=required_decimal(summary.get("evlu_amt_smtl_amt"), "evlu_amt_smtl_amt"),
        unrealized_pnl=required_decimal(summary.get("evlu_pfls_smtl_amt"), "evlu_pfls_smtl_amt"),
        _raw=summary,
    )


def _extract_summary(body: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """잔고 요약(output2) -- KIS가 '길이 1 배열'로도, 단일 객체로도 준다. 비매핑이면 None."""
    summary = body.get("output2")
    if isinstance(summary, list):
        first = summary[0] if summary else None
        return first if isinstance(first, Mapping) else None
    if isinstance(summary, Mapping):
        return summary
    return None


# --- 매수가능 / 매도가능 ---------------------------------------------------
def fetch_buyable(
    transport: Transport, *, cano: str, product_code: str, environment: Environment,
    symbol: str | None = None, limit_price: object | None = None,
) -> BuyableAmount:
    """매수가능 여력. ``symbol`` 없으면 금액만(수량 0). ``limit_price`` 있으면 지정가 기준."""
    if symbol is None and limit_price is not None:
        raise KISUsageError("limit_price 는 symbol 과 함께 줘야 한다(금액만 조회엔 단가 무의미).")
    params = {
        "CANO": cano, "ACNT_PRDT_CD": product_code,
        "PDNO": symbol or "",
        "ORD_UNPR": _format_order_unit_price(limit_price),
        "ORD_DVSN": "00" if limit_price is not None else "01",  # 지정가/시장가
        "CMA_EVLU_AMT_ICLD_YN": "N", "OVRS_ICLD_YN": "N",
    }
    resp = transport.request(
        method="GET", path=_BUYABLE_PATH, tr_id=_BUYABLE_TR[environment], params=params, idempotent=True
    )
    _raise_if_error(resp)
    output = resp.body.get("output")
    if not isinstance(output, Mapping):
        raise KISError(
            "매수가능조회 응답에 output 이 없다.",
            rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
        )
    return _parse_buyable(output, symbol=symbol or "")


def fetch_sellable(
    transport: Transport, *, cano: str, product_code: str, environment: Environment, symbol: str
) -> SellableQuantity:
    """매도가능 수량. **모의투자 미지원**(demo면 사전 :class:`KISUsageError`)."""
    if environment == "demo":
        raise KISUsageError(
            "매도가능수량조회(inquire-psbl-sell)는 모의투자 미지원 -- 실전에서만. "
            "모의에선 잔고의 sellable_quantity 를 참고하라."
        )
    params = {"CANO": cano, "ACNT_PRDT_CD": product_code, "PDNO": symbol}
    resp = transport.request(
        method="GET", path=_SELLABLE_PATH, tr_id=_SELLABLE_TR, params=params, idempotent=True
    )
    _raise_if_error(resp)
    output1 = resp.body.get("output1")
    if not isinstance(output1, Mapping):
        raise KISError(
            "매도가능수량조회 응답에 output1 이 없다.",
            rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
        )
    return _parse_sellable(output1, symbol=symbol)


def _parse_buyable(output: Mapping[str, Any], *, symbol: str) -> BuyableAmount:
    return BuyableAmount(
        symbol=symbol,
        currency="KRW",
        orderable_cash=required_decimal(output.get("ord_psbl_cash"), "ord_psbl_cash"),
        reusable_cash=required_decimal(output.get("ruse_psbl_amt"), "ruse_psbl_amt"),
        cash_buyable_amount=_decimal_or_zero(output.get("nrcvb_buy_amt"), "nrcvb_buy_amt"),
        cash_buyable_quantity=_decimal_or_zero(output.get("nrcvb_buy_qty"), "nrcvb_buy_qty"),
        max_buyable_amount=_decimal_or_zero(output.get("max_buy_amt"), "max_buy_amt"),
        max_buyable_quantity=_decimal_or_zero(output.get("max_buy_qty"), "max_buy_qty"),
        _raw=output,
    )


def _parse_sellable(output1: Mapping[str, Any], *, symbol: str) -> SellableQuantity:
    return SellableQuantity(
        symbol=symbol,
        security_name=str(output1.get("prdt_name", "")).strip(),
        quantity=_decimal_or_zero(output1.get("cblc_qty"), "cblc_qty"),
        sellable_quantity=_decimal_or_zero(output1.get("ord_psbl_qty"), "ord_psbl_qty"),
        _raw=output1,
    )


# --- 공용 ------------------------------------------------------------------
def _decimal_or_zero(value: object, field_name: str) -> Decimal:
    """없으면 0, 있으면 Decimal(파싱 실패면 예외). '없음=0'인 수량·금액 필드용.

    부재(None)만 0으로 본다 -- 값 "0"도 Decimal(0)이라 결과는 같지만, 판정을 truthiness 가
    아니라 명시적 None 검사로 해 의도를 분명히 한다.
    """
    amount = optional_decimal(value, field_name)
    return Decimal(0) if amount is None else amount


def _format_order_unit_price(limit_price: object | None) -> str:
    """주문 단가를 KIS 와이어 정본으로 -- ``None`` 이면 빈 문자열. 유한 양수 아니면 거부."""
    if limit_price is None:
        return ""
    try:
        price = Decimal(str(limit_price))
    except (ArithmeticError, ValueError) as err:
        raise KISUsageError(f"limit_price 는 숫자여야 한다: {limit_price!r}") from err
    if not price.is_finite() or price <= 0:
        raise KISUsageError(f"limit_price 는 0보다 큰 유한값이어야 한다: {limit_price!r}")
    return format_wire_decimal(price)


def _raise_if_error(resp: RawResponse) -> None:
    if not resp.ok:
        raise KISError(
            f"KIS 조회 요청 실패: {resp.msg1}",
            rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
        )
