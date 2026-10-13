"""자격증명 파일(v2) -> KISClient -> 주문 와이어 봉합 테스트.

저장된 (프로필, 환경) 슬롯이 KISClient 를 거쳐 주문 와이어의 TR-id/계좌(CANO)로 정확히 이어지는지
검증한다 -- paper 슬롯을 열면 모의 TR-id 와 모의 계좌가, real 슬롯을 열면 실전 TR-id 와 실전 계좌가
나가야 한다(환경이 주소 selector 라는 속성이 v2 파일 저장부터 리스크 게이트를 지나 와이어까지
일관되게 관통하는지). 네트워크 없이 가짜 전송으로 확인한다.
"""
from __future__ import annotations

import os
import threading

import pytest

from kis_trader import KISClient, KISConfig
from kis_trader.transport import RawResponse

_ORDER_CASH = "/uapi/domestic-stock/v1/trading/order-cash"

_ACCEPTED = RawResponse(
    rt_cd="0", msg_cd="APBK0013", msg1="주문 전송 완료",
    body={"output": {"KRX_FWDG_ORD_ORGNO": "01790", "ODNO": "0000117057", "ORD_TMD": "121052"}},
)


class _FakeTransport:
    """경로/바디/TR-id 를 기록하고 접수 응답을 돌려주는 가짜 전송(환경 미표명 -> 주입 가드 통과)."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent, tr_cont=""):
        with self._lock:
            self.calls.append({"path": path, "tr_id": tr_id, "body": body})
        return _ACCEPTED


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """환경변수가 파일 해석을 가리지 않게 KIS_* 를 지운다(config_dir 로 XDG 는 이미 우회)."""
    for var in [name for name in os.environ if name.startswith("KIS_")]:
        monkeypatch.delenv(var, raising=False)


def _save_both(config_dir) -> None:
    """한 프로필(general)의 real/paper 슬롯을 서로 다른 계좌로 저장한다(계좌로 슬롯을 식별하려고)."""
    KISConfig(profile="general", app_key="REALKEY", app_secret="REALSEC",
              account="11111111-01", environment="real", config_dir=config_dir).save()
    KISConfig(profile="general", app_key="PAPERKEY", app_secret="PAPERSEC",
              account="22222222-01", environment="paper", config_dir=config_dir).save()


def test_paper_slot_resolves_to_paper_tr_and_account(tmp_path):
    _save_both(tmp_path)
    fake = _FakeTransport()
    kis = KISClient(profile="general", environment="paper", config_dir=tmp_path, transport=fake)
    kis.domestic.stock("005930").buy(quantity=10, limit_price=70000)

    call = fake.calls[0]
    assert call["path"] == _ORDER_CASH
    assert call["tr_id"] == "VTTC0012U"            # 모의 매수 TR
    assert call["body"]["CANO"] == "22222222"      # 모의 슬롯 계좌(real 로 새지 않음)
    assert call["body"]["ACNT_PRDT_CD"] == "01"


def test_real_slot_resolves_to_real_tr_and_account(tmp_path):
    _save_both(tmp_path)
    fake = _FakeTransport()
    kis = KISClient(profile="general", environment="real", config_dir=tmp_path, transport=fake)
    kis.domestic.stock("005930").buy(quantity=10, limit_price=70000)

    call = fake.calls[0]
    assert call["path"] == _ORDER_CASH
    assert call["tr_id"] == "TTTC0012U"            # 실전 매수 TR
    assert call["body"]["CANO"] == "11111111"      # 실전 슬롯 계좌(paper 로 새지 않음)
    assert call["body"]["ACNT_PRDT_CD"] == "01"


def test_app_key_and_secret_isolated_between_real_and_paper(tmp_path, monkeypatch):
    """real 슬롯과 paper 슬롯의 app_key/app_secret 이 서로 격리된다 -- auth 경계(TokenManager)에
    닿는 자격증명을 기록하는 가짜를 주입해, real 세션이 쥔 app_key 가 paper 와 다르고(양방향) 그 반대도
    참임을 확인한다. 시크릿 '값' 은 어디에도 출력하지 않고 동등/비동등만 단언한다."""
    captured: dict[str, tuple[str, str]] = {}

    class _RecordingTokenManager:
        def __init__(self, *, app_key, app_secret, environment, cache_dir=None):
            captured[environment] = (app_key, app_secret)

    # auth 경계만 가짜로 -- 토큰은 app_key/app_secret 으로 발급되므로 여기서 짝을 기록한다(네트워크 없음).
    monkeypatch.setattr("kis_trader._internal._auth.TokenManager", _RecordingTokenManager)
    _save_both(tmp_path)
    KISClient(profile="general", environment="real", config_dir=tmp_path)
    KISClient(profile="general", environment="paper", config_dir=tmp_path)

    assert set(captured) == {"real", "paper"}
    real_key, real_secret = captured["real"]
    paper_key, paper_secret = captured["paper"]
    assert real_key != paper_key                     # app_key 격리(real != paper)
    assert real_secret != paper_secret               # app_secret 격리
    assert paper_key != real_key                     # 반대 방향(paper 가 real 로 새지 않는다)
    assert captured["real"] != captured["paper"]      # 짝 전체가 슬롯별로 분리
