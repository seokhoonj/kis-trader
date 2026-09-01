"""공유 KISClient 의 지연 로드 동시성 -- 마스터 인덱스 캐시가 락으로 보호돼 동시 첫 호출에도 로드가
한 번만 일어나는지 고정(더블체크 락). Session 전역 락도 동일 DCL 관용이라 여기선 인덱스 락만 검증한다.
"""

from __future__ import annotations

import threading

from kis_trader import InstrumentRecord, KISClient, MasterIndex


class _FakeTransport:
    environment = "real"

    def request(self, **_kw):  # 이 테스트는 마스터 로드만 보므로 전송은 호출되지 않는다
        raise AssertionError("transport 는 호출되지 않아야 한다")


def test_instrument_lazy_load_runs_once_under_concurrent_first_calls(monkeypatch):
    from kis_trader import client as client_module

    load_count = 0
    sentinel = MasterIndex(
        [InstrumentRecord("AAPL", "NAS", "USD", "stock", "애플", "APPLE", "NASAAPL")]
    )

    def counting_load(*, fetch):
        nonlocal load_count
        load_count += 1  # 락이 없으면 동시 진입으로 2 이상이 된다
        return sentinel

    monkeypatch.setattr(client_module, "load_overseas_index", counting_load)
    kis = KISClient(app_key="k", app_secret="s", account="12345678-01", transport=_FakeTransport())

    threads_n = 8
    start = threading.Barrier(threads_n)  # 모든 스레드를 동시에 instrument() 로 진입시켜 락을 경합시킨다
    results: list = []
    errors: list = []

    def worker():
        start.wait()
        try:
            results.append(kis.instrument("AAPL", exchange="NAS"))
        except Exception as exc:  # noqa: BLE001 -- 스레드 예외를 메인에서 재현하려고 수집
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(threads_n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert load_count == 1                       # 더블체크 락 덕에 마스터는 한 번만 로드
    assert len(results) == threads_n
    assert all(r.exchange == "NAS" for r in results)
