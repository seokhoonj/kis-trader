"""통합잔고 -- kis.account.balance() (실전전용).

국내주식 잔고 + 장내채권 잔고 + 해외 체결기준현재잔고를 한 IntegratedBalance 로 합친다.
새 와이어는 없다(세 기존 엔진 콜의 합성). 채권/해외 현재잔고가 실전 전용이라 모의는 와이어를
타기 전에 KISUsageError 로 fail-closed 한다. 네트워크 없이 FakeTransport 로 검증한다.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_trader import KISClient
from kis_trader.domestic.entities.balance import Balance
from kis_trader.domestic.entities.integrated import CurrencyDeposit, IntegratedBalance
from kis_trader.errors import KISUsageError
from kis_trader.overseas.entities.balance import OverseasPresentBalance
from kis_trader.transport import RawResponse

_DOMESTIC_PATH = "/uapi/domestic-stock/v1/trading/inquire-balance"
_BOND_PATH = "/uapi/domestic-bond/v1/trading/inquire-balance"
_OVERSEAS_PATH = "/uapi/overseas-stock/v1/trading/inquire-present-balance"


class FakeTransport:
    def __init__(self, *, response=None, by_path=None, raises=None):
        self.response = response
        self.by_path = by_path or {}
        self.raises = raises
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent, tr_cont=""):
        with self._lock:
            self.calls.append({"method": method, "path": path, "tr_id": tr_id,
                               "params": params, "idempotent": idempotent, "tr_cont": tr_cont})
        if path in self.by_path:
            outcome = self.by_path[path]
            if isinstance(outcome, list):
                outcome = outcome.pop(0)
        elif self.raises is not None:
            outcome = self.raises
        else:
            outcome = self.response
        if isinstance(outcome, BaseException):
            raise outcome
        assert outcome is not None, "FakeTransport 에 응답을 줘야 한다"
        return outcome


def _client(transport, *, environment="real", account="12345678-01"):
    return KISClient(app_key="k", app_secret="s", account=account,
                     environment=environment, transport=transport)


# --- fixtures --------------------------------------------------------------
def _domestic_summary():
    return {
        "dnca_tot_amt": "5000000",          # deposit
        "nxdy_excc_amt": "0",
        "prvs_rcdl_excc_amt": "0",
        "tot_evlu_amt": "12000000",         # total_evaluation
        "nass_amt": "17000000",             # net_asset
        "pchs_amt_smtl_amt": "10000000",
        "evlu_amt_smtl_amt": "12000000",
        "evlu_pfls_smtl_amt": "2000000",    # unrealized_pnl
    }


def _domestic_resp():
    body = {"output1": [], "output2": _domestic_summary(),
            "ctx_area_nk100": "", "ctx_area_fk100": ""}
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body=body)


def _bond_position():
    return {"pdno": "KR2033022D33", "prdt_name": "국민주택1종20-05", "buy_dt": "20240215",
            "buy_sqno": "1", "cblc_qty": "1000", "agrx_qty": "1000", "sprx_qty": "0",
            "exdt": "20340215", "buy_erng_rt": "3.45", "buy_unpr": "9850",
            "buy_amt": "9850000", "ord_psbl_qty": "1000"}


def _bond_resp():
    body = {"output": [_bond_position()], "ctx_area_nk200": "", "ctx_area_fk200": ""}
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body=body)


def _overseas_resp():
    body = {
        "output1": [],
        "output2": [{"crcy_cd": "USD", "crcy_cd_name": "미국달러",
                     "frcr_dncl_amt_2": "1000.50", "frst_bltn_exrt": "1350.20"}],
        "output3": {"pchs_amt_smtl_amt": "2500000", "evlu_amt_smtl_amt": "2800000",
                    "tot_evlu_pfls_amt": "300000", "tot_asst_amt": "3000000"},
    }
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body=body)


def _all_paths_fake():
    return FakeTransport(by_path={
        _DOMESTIC_PATH: _domestic_resp(),
        _BOND_PATH: _bond_resp(),
        _OVERSEAS_PATH: _overseas_resp(),
    })


# --- tests -----------------------------------------------------------------
def test_integrated_balance_merges():
    fake = _all_paths_fake()
    result = _client(fake).account.balance()

    assert isinstance(result, IntegratedBalance)
    assert result.base_currency == "KRW"

    # 통화별 예수금: KRW(국내) + USD(해외)
    by_currency = {d.currency: d for d in result.deposits}
    assert set(by_currency) == {"KRW", "USD"}
    assert len(result.deposits) == len(by_currency)  # 통화별 유일(KRW 중복 행 없음)
    assert isinstance(result.deposits[0], CurrencyDeposit)
    krw = by_currency["KRW"]
    assert krw.cash == Decimal(5000000)
    assert krw.exchange_rate == Decimal(1)
    usd = by_currency["USD"]
    assert usd.cash == Decimal("1000.50")
    assert usd.exchange_rate == Decimal("1350.20")

    # 평가 롤업 = 겹치지 않는 국내·해외 보유의 순수 원화 합(현금 포함 단일 총자산은 이중계상이라 미노출)
    assert result.total_evaluation == Decimal(12000000) + Decimal(2800000)
    assert result.total_unrealized_pnl == Decimal(2000000) + Decimal(300000)
    assert not hasattr(result, "net_liquidation")

    # 도메인별 서브잔고(원본 스키마 유지)
    assert isinstance(result.domestic, Balance)
    assert isinstance(result.overseas, OverseasPresentBalance)
    assert len(result.bonds) == 1
    assert result.bonds[0].symbol == "KR2033022D33"


def test_integrated_balance_paper_fails_closed():
    fake = _all_paths_fake()
    with pytest.raises(KISUsageError):
        _client(fake, environment="paper").account.balance()
    assert fake.calls == []


def test_integrated_balance_import_guard():
    from kis_trader import CurrencyDeposit, IntegratedBalance  # noqa: F401
