"""KISClient 수명 관리 회귀 -- close()/__enter__/__exit__ 가 주입 저장소의 단일-writer flock 을 푼다.

``with KISClient(..., store=OrderStore(path=...))`` 블록을 벗어나면 저장소 flock 이 풀려 같은 경로를
다시 열 수 있어야 한다(안 풀리면 재시작/재오픈이 KISError 로 막힌다). close() 중복 호출은 안전해야 한다.
"""

from __future__ import annotations

import pytest

from kis_trader import KISClient
from kis_trader.errors import KISError
from kis_trader.store import OrderStore


class _StubTransport:
    def request(self, **_kw):  # 네트워크 호출 없음 -- 수명만 검증
        raise AssertionError("lifecycle test must not hit the wire")


def _client(store):
    return KISClient(app_key="k", app_secret="s", account="12345678-01",
                     transport=_StubTransport(), store=store)


def test_with_block_releases_store_lock_so_path_can_reopen(tmp_path):
    path = tmp_path / "orders.json"
    with _client(OrderStore(path=path)):
        pass  # 블록을 벗어나면 __exit__ -> close() -> flock 해제
    # 같은 경로를 다시 열 수 있어야 한다(이전 세션이 락을 쥔 채면 KISError).
    reopened = OrderStore(path=path)
    reopened.close()


def test_double_close_does_not_raise(tmp_path):
    store = OrderStore(path=tmp_path / "orders.json")
    kis = _client(store)
    kis.close()
    kis.close()  # 두 번째 close 는 조용해야 한다


def test_open_while_locked_raises(tmp_path):
    # 전제 확인: 같은 경로를 두 번 열면(첫 세션이 락 보유) 단일-writer flock 이 거부한다.
    path = tmp_path / "orders.json"
    with _client(OrderStore(path=path)), pytest.raises(KISError):
        OrderStore(path=path)
