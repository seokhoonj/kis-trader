"""라이브 해외주문 세션에서 드러난 세 수정의 회귀 테스트.

- #1 정정·취소가 '거래소 미접수'로 거부되면 재시도 가능한 하위 타입
  (:class:`OrderNotAcceptedYetError`)으로 매핑한다(``OrderRejectedError`` 하위 -> 하위호환).
- #2 ``config.order_store_path`` 와 CLI ``build_client`` 가 영속 :class:`OrderStore` 를 배선해
  프로세스가 바뀌어도 취소/재조회/dedup 이 가능하게 한다(라이브러리 기본은 인메모리 유지).
- #3+5 해외는 부분 취소·정정 메커니즘이 없어(라이브 확인), 잔량 전체가 아닌 변경은 와이어 전에
  :class:`KISUsageError` 로 거부한다(국내는 이 게이트에 안 걸린다).
"""

from __future__ import annotations

import threading
from argparse import Namespace

import pytest

from kis_trader import KISClient
from kis_trader.config import order_store_path
from kis_trader.errors import KISUsageError, OrderNotAcceptedYetError, OrderRejectedError
from kis_trader.transport import RawResponse

_ORDER_CASH = "/uapi/domestic-stock/v1/trading/order-cash"
_ORDER_CHANGE = "/uapi/domestic-stock/v1/trading/order-rvsecncl"
_OVERSEAS_ORDER = "/uapi/overseas-stock/v1/trading/order"
_OVERSEAS_CHANGE = "/uapi/overseas-stock/v1/trading/order-rvsecncl"


class FakeTransport:
    """경로별 순차 응답을 돌려주는 가짜 전송. ``request_count`` 로 와이어 접촉 횟수를 센다."""

    def __init__(self, *, by_path=None):
        self.by_path = by_path or {}
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    @property
    def request_count(self) -> int:
        return len(self.calls)

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent, tr_cont=""):
        with self._lock:
            self.calls.append({"method": method, "path": path, "tr_id": tr_id, "body": body,
                               "params": params, "idempotent": idempotent})
        outcome = self.by_path.get(path)
        if isinstance(outcome, list):
            outcome = outcome.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        assert outcome is not None, f"FakeTransport 에 {path} 응답을 줘야 한다"
        return outcome


def _ack(odno="0000117057"):
    return RawResponse(rt_cd="0", msg_cd="APBK0013", msg1="주문 전송 완료",
                       body={"output": {"KRX_FWDG_ORD_ORGNO": "01790", "ODNO": odno,
                                        "ORD_TMD": "121052"}})


def _client(transport, **kw):
    return KISClient(app_key="k", app_secret="s", account="12345678-01", transport=transport, **kw)


def _seeded_domestic(reject=None):
    """국내 매수를 접수시켜 ``cid-1`` 원주문을 심고, 정정취소 응답을 지정한 클라이언트."""
    change_resp = reject if reject is not None else _ack()
    fake = FakeTransport(by_path={_ORDER_CASH: _ack(), _ORDER_CHANGE: change_resp})
    kis = _client(fake)
    kis.domestic.stock("005930").buy(quantity=10, limit_price=70000, client_order_id="cid-1")
    fake.calls.clear()   # seed 이후의 와이어 접촉만 센다
    return kis, fake


def _seeded_overseas():
    """해외 매수(잔량 3)를 접수시켜 ``cid-1`` 원주문을 심은 클라이언트."""
    fake = FakeTransport(by_path={_OVERSEAS_ORDER: _ack("0000123456"),
                                  _OVERSEAS_CHANGE: _ack("0000123456")})
    kis = _client(fake)
    kis.overseas.stock("AAPL", exchange="NAS").buy(
        quantity=3, limit_price="150.00", client_order_id="cid-1")
    fake.calls.clear()
    return kis, fake


# --- #1 미접수 거부 -> 재시도 가능한 하위 타입 --------------------------------

def test_cancel_not_yet_accepted_maps_to_retryable_error():
    reject = RawResponse(rt_cd="1", msg_cd="APBK0919",
                         msg1="거래소 미접수로 정정취소주문이 불가합니다", body={})
    kis, _ = _seeded_domestic(reject=reject)
    with pytest.raises(OrderNotAcceptedYetError) as ei:
        kis.orders.cancel("cid-1", request_id="c-1")
    assert isinstance(ei.value, OrderRejectedError)   # 하위호환: 기존 except 가 그대로 잡는다


def test_cancel_other_rejection_stays_orderrejected():
    reject = RawResponse(rt_cd="1", msg_cd="APBK1234", msg1="주문가능금액 부족", body={})
    kis, _ = _seeded_domestic(reject=reject)
    with pytest.raises(OrderRejectedError) as ei:
        kis.orders.cancel("cid-1", request_id="c-1")
    assert not isinstance(ei.value, OrderNotAcceptedYetError)


# --- #3+5 해외 부분 취소·정정 거부(국내 미차단) ------------------------------

def test_overseas_partial_cancel_rejected():
    kis, fake = _seeded_overseas()
    with pytest.raises(KISUsageError, match="부분"):
        kis.orders.cancel("cid-1", quantity=1, request_id="x-1")
    assert fake.request_count == 0        # 와이어에 닿기 전에 거부


def test_overseas_partial_modify_rejected():
    kis, fake = _seeded_overseas()
    with pytest.raises(KISUsageError, match="부분"):
        kis.orders.modify("cid-1", limit_price="151.00", quantity=1, request_id="x-2")
    assert fake.request_count == 0


def test_overseas_full_cancel_still_works():
    kis, fake = _seeded_overseas()
    kis.orders.cancel("cid-1", request_id="x-1")   # quantity 생략 -> 전량, 전송된다
    assert fake.request_count == 1


def test_domestic_partial_cancel_not_blocked_by_this_gate():
    # 국내는 이 게이트에 안 걸린다(부분취소는 국내 슬라이스 소관, 여기선 통과해 와이어로 나간다).
    kis, fake = _seeded_domestic()
    kis.orders.cancel("cid-1", quantity=5, request_id="c-1")
    assert fake.request_count == 1


# --- #2 order_store_path + CLI 영속 store -------------------------------------

def test_order_store_path_is_per_env_and_account(tmp_path):
    p1 = order_store_path(account="12345678-01", environment="real", override=tmp_path)
    p2 = order_store_path(account="12345678-01", environment="paper", override=tmp_path)
    p3 = order_store_path(account="87654321-03", environment="real", override=tmp_path)
    assert p1 != p2 and p1 != p3
    assert "12345678" not in str(p1)      # 계정번호 노출 금지(해시)
    assert str(p1).endswith(".json")


def test_cli_build_client_uses_persistent_store(monkeypatch, tmp_path):
    from kis_trader.cli.context import build_client
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    for var in [name for name in __import__("os").environ if name.startswith("KIS_")]:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("KIS_PAPER_APP_KEY", "k")
    monkeypatch.setenv("KIS_PAPER_APP_SECRET", "s")
    monkeypatch.setenv("KIS_PAPER_ACCOUNT", "12345678-01")
    monkeypatch.setenv("KIS_PAPER_ENVIRONMENT", "paper")
    kis = build_client(Namespace(profile="paper", account=None))
    assert kis._store._path is not None   # 영속(디스크) 저장소가 주입됐다
