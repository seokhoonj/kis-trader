"""MCP 런타임 계좌 상태 -- kis_trader.mcp._session (McpSession + OrderSnapshot).

전환(use_environment/use_profile)이 활성 슬롯을 바꾸고 슬롯 캐시를 재사용하는지, 지원 외/파생 프로필과
없는 조합을 거부하며 활성 상태를 불변으로 두는지(atomic), 직접-자격증명 기동이 전환을 거부하는지,
그리고 **안전 불변**(전환이 실주문 언락을 못 건드린다 -- S1/S2/S3/S10)을 네트워크 없이 가짜로 검증한다.
"""

from __future__ import annotations

import inspect

import pytest

from kis_trader.account import StockAccount
from kis_trader.errors import KISUsageError
from kis_trader.mcp._session import (
    McpSession,
    OrderSnapshot,
    _StartupAuthorization,
)


class _FakeStockAccount(StockAccount):
    """isinstance(.., StockAccount) 를 만족하는 가짜 주식 계좌 뷰(상품코드만 제어). 라이브러리 뷰를
    실제로 열지 않고 product_code 분기만 테스트한다(super().__init__ 건너뜀)."""

    def __init__(self, product_code: str = "01") -> None:
        self._product_code = product_code

    @property
    def product_code(self) -> str:
        return self._product_code


class _FakeClient:
    """KISClient 대역 -- 환경·계좌 마스킹·account 뷰만 흉내. 전환 슬롯마다 새 인스턴스를 만들어 캐시
    재사용(build 호출 횟수)을 관찰한다."""

    def __init__(self, environment: str, *, account: str = "12345678-01", product_code: str = "01") -> None:
        self.environment = environment
        self._account = account
        self._product_code = product_code

    @property
    def account(self) -> StockAccount:
        return _FakeStockAccount(self._product_code)


def _counting_build(product_by_profile: dict[str, str] | None = None):
    """(profile, environment) -> 새 _FakeClient 를 만드는 build 팩토리 + 호출 로그. 특정 조합을
    '없음'으로 만들려면 반환 전에 raise 하도록 absent 집합을 둔다."""
    calls: list[tuple[str, str]] = []
    product_by_profile = product_by_profile or {}
    absent: set[tuple[str, str]] = set()

    def build(profile: str, environment: str):
        calls.append((profile, environment))
        if (profile, environment) in absent:
            raise KISUsageError(f"'{environment}' 환경에 프로필 {profile!r} 이 없다.")
        return _FakeClient(environment, product_code=product_by_profile.get(profile, "01"))

    build.calls = calls           # type: ignore[attr-defined]
    build.absent = absent         # type: ignore[attr-defined]
    return build


def _session(*, profile="general", environment="paper", auth=None, build=None,
             product_by_profile=None):
    build = build if build is not None else _counting_build(product_by_profile)
    startup = _FakeClient(environment, product_code=(product_by_profile or {}).get(profile, "01"))
    auth = auth if auth is not None else _StartupAuthorization(allow_real=False, confirmed=False)
    return McpSession(profile=profile, environment=environment, startup_client=startup,
                      auth=auth, build=build), build


# --- 전환 상태 -------------------------------------------------------------
def test_use_environment_toggles_env_preserves_profile():
    session, _ = _session(profile="general", environment="paper")
    out = session.use_environment("real")
    assert out["environment"] == "real" and out["profile"] == "general"
    assert session.active_profile == "general"
    assert session.read_client().environment == "real"


def test_use_profile_changes_profile_preserves_env():
    session, _ = _session(profile="general", environment="paper")
    out = session.use_profile("pension")
    assert out["profile"] == "pension" and out["environment"] == "paper"
    assert session.active_profile == "pension"


def test_cache_hit_does_not_rebuild_same_slot():
    session, build = _session(profile="general", environment="paper")
    session.use_environment("real")      # build (general, real)
    session.use_environment("paper")     # 복귀 -- 기동 슬롯(캐시된) 재사용, build 안 함
    session.use_environment("real")      # 다시 real -- 캐시 hit
    # (general, real) 은 한 번만 build 됐다(캐시 비우지 않음)
    assert build.calls.count(("general", "real")) == 1       # type: ignore[attr-defined]


def test_invalid_environment_rejected_slot_unchanged():
    session, _ = _session(profile="general", environment="paper")
    with pytest.raises(KISUsageError):
        session.use_environment("demo")
    assert session.read_client().environment == "paper"      # 활성 상태 불변


@pytest.mark.parametrize("bad", ["GENERAL", "derivatives", "overseas_derivatives", "unknown"])
def test_use_profile_rejects_non_switchable(bad):
    session, _ = _session(profile="general", environment="paper")
    with pytest.raises(KISUsageError):
        session.use_profile(bad)
    assert session.active_profile == "general"               # 파생/지원외 거부, 상태 불변


def test_absent_target_slot_rejected_active_unchanged():
    build = _counting_build()
    build.absent.add(("pension", "paper"))                   # type: ignore[attr-defined]
    session, _ = _session(profile="general", environment="paper", build=build)
    with pytest.raises(KISUsageError):
        session.use_profile("pension")                       # 없는 조합 -- eager-build 가 raise
    assert session.active_profile == "general"               # atomic: 활성 상태 불변


def test_direct_cred_fixed_slot_rejects_switch():
    startup = _FakeClient("paper")
    session = McpSession(profile=None, environment="paper", startup_client=startup,
                         auth=_StartupAuthorization(False, False), build=None)
    with pytest.raises(KISUsageError):
        session.use_profile("pension")
    with pytest.raises(KISUsageError):
        session.use_environment("real")
    acct = session.current_account()
    assert acct["profile"] is None and acct["environment"] == "paper"
    assert acct["account"] == "****7801"                     # 마스킹(마지막 4자리)


def test_current_account_reports_product_code():
    session, _ = _session(profile="irp", environment="real", product_by_profile={"irp": "29"})
    out = session.current_account()
    assert out["profile"] == "irp" and out["environment"] == "real"
    assert out["product_code"] == "29" and out["account_type"] == "stock"


def test_startup_absent_profile_constructor_eager_fails():
    build = _counting_build()
    build.absent.add(("general", "paper"))                   # type: ignore[attr-defined]
    # 기동 슬롯은 main() 에서 eager-build 되므로, 없는 조합이면 거기서 깔끔히 실패한다(여기선 build 직접).
    with pytest.raises(KISUsageError):
        build("general", "paper")


# --- 안전 불변: 전환은 실주문 언락을 못 건드린다 -----------------------------
def test_s1_switch_to_real_does_not_unlock_without_startup_flags():
    # S1: allow_real/confirmed 미설정으로 기동 -> use_environment('real') -> 주문 게이트는 여전히 닫힘.
    session, _ = _session(profile="general", environment="paper",
                          auth=_StartupAuthorization(allow_real=False, confirmed=False))
    session.use_environment("real")
    snap = session.order_snapshot()
    assert snap.gate.is_real() is True
    with pytest.raises(KISUsageError, match="KIS_MCP_ALLOW_REAL"):
        snap.gate.require_executable()                       # 전환해도 언락 안 됨
    session.read_client()                                    # 읽기는 동작(real 읽기 맥락)


def test_s2_auth_identity_constant_across_switches():
    # S2: 임의의 전환 시퀀스 동안 _auth(언락 비트)의 객체 정체성이 변하지 않는다 + 플래그 세팅 시
    # 전후로 executable.
    auth = _StartupAuthorization(allow_real=True, confirmed=True)
    session, _ = _session(profile="general", environment="real", auth=auth)
    before = session.order_snapshot()
    before.gate.require_executable()                         # 기동 플래그로 executable
    session.use_profile("isa")
    session.use_environment("paper")
    session.use_environment("real")
    session.use_profile("general")
    after = session.order_snapshot()
    after.gate.require_executable()
    assert session._auth is auth                             # 전환이 언락 비트 객체를 바꾸지 않음


def test_s3_every_snapshot_gate_env_matches_client_env():
    # S3: 방문하는 모든 슬롯에서 snapshot.gate.environment == snapshot.client.environment.
    session, _ = _session(profile="general", environment="paper",
                          auth=_StartupAuthorization(True, True))
    for env in ("paper", "real", "paper", "real"):
        session.use_environment(env)
        snap = session.order_snapshot()
        assert snap.gate.environment == snap.client.environment == env


def test_s10_switch_methods_never_read_unlock_env_vars():
    # S10(소스 검사 pin): use_environment/use_profile 본문에 KIS_MCP_ALLOW_REAL/REAL_CONFIRM 참조가
    # 없다(전환이 언락 환경변수를 건드리는 경로가 구조적으로 없음).
    for method in (McpSession.use_environment, McpSession.use_profile):
        src = inspect.getsource(method)
        assert "KIS_MCP_ALLOW_REAL" not in src
        assert "KIS_MCP_REAL_CONFIRM" not in src


def test_order_snapshot_rejects_env_mismatch():
    # split-brain 가드: 활성 환경과 build 가 돌려준 클라이언트 환경이 어긋나면 order_snapshot 이 거부한다
    # (정상 경로는 못 만들지만, 어긋난 build 를 주입해 가드가 살아 있음을 고정).
    def wrong_build(profile: str, environment: str):
        return _FakeClient("paper")              # 요청 환경과 무관하게 항상 paper 클라이언트
    startup = _FakeClient("paper")
    session = McpSession(profile="general", environment="paper", startup_client=startup,
                         auth=_StartupAuthorization(True, True), build=wrong_build)
    session.use_environment("real")              # 활성 환경=real, 하지만 build 는 paper 클라이언트
    with pytest.raises(KISUsageError, match="split-brain"):
        session.order_snapshot()


def test_order_snapshot_freezes_profile_and_client():
    session, _ = _session(profile="general", environment="paper",
                          auth=_StartupAuthorization(False, False))
    snap = session.order_snapshot()
    assert isinstance(snap, OrderSnapshot)
    client = snap.client
    session.use_profile("pension")                           # 전환 후에도 스냅샷의 client 는 그대로
    assert snap.client is client and snap.profile == "general"
