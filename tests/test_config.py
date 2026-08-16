"""자격증명 API -- KISConfig(값 저장) / resolve_credentials(읽기) / KISClient(profile=)."""
from __future__ import annotations

import json
import os

import pytest

from kis_trader import KISClient, KISConfig
from kis_trader.config import environment_for_profile, resolve_credentials
from kis_trader.errors import KISUsageError
from kis_trader.transport import RawResponse


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """실 사용자의 ~/.config/kis-trader 를 읽지 않도록 XDG 를 빈 임시 경로로 돌리고 KIS_* 를 지운다."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    for var in [name for name in os.environ if name.startswith("KIS_")]:
        monkeypatch.delenv(var, raising=False)


class _FakeTransport:
    """네트워크 없이 세션을 열게 하는 가짜 전송."""
    def request(self, **kwargs):
        return RawResponse(rt_cd="0", msg_cd="", msg1="")


def _read_saved_credentials(config_dir):
    return json.loads((config_dir / "credentials.json").read_text(encoding="utf-8"))


# --- 프로필 -> 환경 매핑 ------------------------------------------------------

@pytest.mark.parametrize("profile, environment", [
    ("main", "real"), ("paper", "paper"), ("isa", "real"), ("irp", "real"), ("pension", "real"),
])
def test_profile_maps_to_environment(profile, environment):
    assert environment_for_profile(profile) == environment


def test_unknown_profile_is_rejected():
    with pytest.raises(KISUsageError):
        environment_for_profile("bogus")  # type: ignore[arg-type]
    with pytest.raises(KISUsageError):
        KISConfig(profile="bogus", app_key="k", app_secret="s")  # type: ignore[arg-type]
    with pytest.raises(KISUsageError):
        resolve_credentials("bogus")  # type: ignore[arg-type]


# --- KISConfig.save() (쓰기) --------------------------------------------------

def test_save_writes_prefixed_keys_and_account(tmp_path):
    KISConfig(profile="main", app_key="AK", app_secret="SK",
              account="12345678-01", config_dir=tmp_path).save()
    saved = _read_saved_credentials(tmp_path)
    assert saved == {
        "KIS_APP_KEY": "AK", "KIS_APP_SECRET": "SK", "KIS_ACCOUNT": "12345678-01",
    }


def test_save_uses_profile_prefix(tmp_path):
    KISConfig(profile="irp", app_key="AK", app_secret="SK",
              account="87654321-29", config_dir=tmp_path).save()
    saved = _read_saved_credentials(tmp_path)
    assert saved["KIS_IRP_APP_KEY"] == "AK"
    assert saved["KIS_IRP_ACCOUNT"] == "87654321-29"


def test_save_merges_and_preserves_other_profiles(tmp_path):
    KISConfig(profile="main", app_key="MK", app_secret="MS", config_dir=tmp_path).save()
    KISConfig(profile="paper", app_key="PK", app_secret="PS", config_dir=tmp_path).save()
    saved = _read_saved_credentials(tmp_path)
    assert saved["KIS_APP_KEY"] == "MK" and saved["KIS_PAPER_APP_KEY"] == "PK"  # 둘 다 보존


def test_save_without_account_writes_only_keys(tmp_path):
    KISConfig(profile="main", app_key="AK", app_secret="SK", config_dir=tmp_path).save()
    saved = _read_saved_credentials(tmp_path)
    assert set(saved) == {"KIS_APP_KEY", "KIS_APP_SECRET"}


def test_secrets_are_hidden_from_repr():
    # KISConfig 가 값을 담으므로 repr/로그에 앱키·시크릿이 새면 안 된다.
    text = repr(KISConfig(profile="main", app_key="SECRETKEY", app_secret="SECRETSEC",
                          account="12345678-01"))
    assert "SECRETKEY" not in text and "SECRETSEC" not in text


def test_resolved_credentials_hides_secrets_from_repr(tmp_path):
    # 세션에 넘어가는 해석 결과도 실제 시크릿을 담으므로 repr 에 새면 안 된다.
    KISConfig(profile="main", app_key="SECRETKEY", app_secret="SECRETSEC",
              config_dir=tmp_path).save()
    text = repr(resolve_credentials("main", config_dir=tmp_path))
    assert "SECRETKEY" not in text and "SECRETSEC" not in text


def test_construction_requires_nonblank_app_key_and_secret():
    with pytest.raises(KISUsageError):
        KISConfig(profile="main", app_key="AK", app_secret="   ")  # 공백 시크릿
    with pytest.raises(TypeError):
        KISConfig(profile="main", app_key="AK")  # type: ignore[call-arg]  # secret 자체가 없음


def test_construction_rejects_malformed_account(tmp_path):
    with pytest.raises(KISUsageError):
        KISConfig(profile="main", app_key="AK", app_secret="SK",
                  account="12345678", config_dir=tmp_path)  # 상품코드 없음
    assert not (tmp_path / "credentials.json").exists()  # 파일도 안 남는다


def test_construction_rejects_account_with_multiple_separators(tmp_path):
    # 쓰기(save)와 읽기(session)가 같은 _split_account 계약을 공유하므로, 세션이 못 읽을
    # 형식은 저장 자체를 막는다(저장은 됐는데 못 읽는 상태 방지).
    with pytest.raises(KISUsageError):
        KISConfig(profile="main", app_key="AK", app_secret="SK",
                  account="12345678-01-02", config_dir=tmp_path)
    assert not (tmp_path / "credentials.json").exists()


def test_save_creates_missing_directory(tmp_path):
    target = tmp_path / "sub" / "dir"   # 아직 없음
    KISConfig(profile="main", app_key="AK", app_secret="SK", config_dir=target).save()
    assert (target / "credentials.json").exists()


def test_save_returns_path_and_file_is_owner_only(tmp_path):
    path = KISConfig(profile="main", app_key="AK", app_secret="SK", config_dir=tmp_path).save()
    assert path == tmp_path / "credentials.json"
    # POSIX 에서 0600 (Windows 는 ACL 이라 검증 생략)
    if os.name == "posix":
        assert (path.stat().st_mode & 0o777) == 0o600


# --- resolve_credentials (읽기) -----------------------------------------------

def test_resolve_reads_saved_credentials(tmp_path):
    KISConfig(profile="isa", app_key="AK", app_secret="SK",
              account="87654321-01", config_dir=tmp_path).save()
    resolved = resolve_credentials("isa", config_dir=tmp_path)
    assert resolved.app_key == "AK" and resolved.account == "87654321-01"
    assert resolved.environment == "real"


def test_environment_variable_wins_over_file(tmp_path, monkeypatch):
    KISConfig(profile="main", app_key="from_file", app_secret="s", config_dir=tmp_path).save()
    monkeypatch.setenv("KIS_APP_KEY", "from_env")
    assert resolve_credentials("main", config_dir=tmp_path).app_key == "from_env"


def test_environment_account_wins_over_saved_account(tmp_path, monkeypatch):
    # 앱키뿐 아니라 계좌도 env 가 파일을 이긴다.
    KISConfig(profile="main", app_key="AK", app_secret="SK",
              account="12345678-01", config_dir=tmp_path).save()
    monkeypatch.setenv("KIS_ACCOUNT", "87654321-02")
    assert resolve_credentials("main", config_dir=tmp_path).account == "87654321-02"


def test_save_does_not_clobber_malformed_credentials_file(tmp_path):
    # 병합 바탕이 손상돼 있으면 -- 남의 프로필을 덮어써 날리지 않도록 -- 저장을 멈춘다.
    path = tmp_path / "credentials.json"
    original = "{ this is not valid json"
    path.write_text(original, encoding="utf-8")
    with pytest.raises(KISUsageError):
        KISConfig(profile="paper", app_key="PK", app_secret="PS", config_dir=tmp_path).save()
    assert path.read_text(encoding="utf-8") == original  # 원본 그대로


def test_resolve_missing_credentials_names_the_variable():
    with pytest.raises(KISUsageError) as excinfo:
        resolve_credentials("paper")
    assert "KIS_PAPER_APP_KEY" in str(excinfo.value)


def test_resolve_account_none_when_absent(monkeypatch):
    monkeypatch.setenv("KIS_APP_KEY", "k")
    monkeypatch.setenv("KIS_APP_SECRET", "s")
    assert resolve_credentials("main").account is None


def test_resolve_malformed_account_fails_closed(monkeypatch):
    monkeypatch.setenv("KIS_APP_KEY", "k")
    monkeypatch.setenv("KIS_APP_SECRET", "s")
    monkeypatch.setenv("KIS_ACCOUNT", "12345678")  # 상품코드 없음(하이픈 누락)
    with pytest.raises(KISUsageError):
        resolve_credentials("main")


def test_resolve_account_with_multiple_separators_fails_closed(monkeypatch):
    # 읽기 경로도 여분 하이픈을 거부해야 한다(쓰기 경로와 대칭).
    monkeypatch.setenv("KIS_APP_KEY", "k")
    monkeypatch.setenv("KIS_APP_SECRET", "s")
    monkeypatch.setenv("KIS_ACCOUNT", "12345678-01-02")
    with pytest.raises(KISUsageError):
        resolve_credentials("main")


def test_resolve_legacy_split_keys_hint_migration(tmp_path):
    # 구형(분리 키)로만 저장된 파일은 조용히 '계좌 없음'이 아니라 이전하라는 오류를 낸다.
    (tmp_path / "credentials.json").write_text(
        json.dumps({"KIS_APP_KEY": "k", "KIS_APP_SECRET": "s",
                    "KIS_CANO": "12345678", "KIS_ACNT_PRDT_CD": "01"}),
        encoding="utf-8",
    )
    with pytest.raises(KISUsageError) as excinfo:
        resolve_credentials("main", config_dir=tmp_path)
    assert "KIS_ACCOUNT" in str(excinfo.value)


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_value_is_absent(monkeypatch, blank):
    monkeypatch.setenv("KIS_APP_KEY", blank)  # 빈/공백 시크릿은 유효하지 않다
    with pytest.raises(KISUsageError):
        resolve_credentials("main")


def test_malformed_credentials_file_raises(tmp_path):
    (tmp_path / "credentials.json").write_text("{", encoding="utf-8")
    with pytest.raises(KISUsageError):
        resolve_credentials("main", config_dir=tmp_path)


def test_config_toml_is_last_fallback(tmp_path):
    (tmp_path / "config.toml").write_text('KIS_APP_KEY = "tk"\nKIS_APP_SECRET = "ts"\n', encoding="utf-8")
    assert resolve_credentials("main", config_dir=tmp_path).app_key == "tk"


# --- KISClient(profile=) 세션 -------------------------------------------------

def test_client_reads_saved_profile(tmp_path):
    KISConfig(profile="main", app_key="AK", app_secret="SK",
              account="12345678-01", config_dir=tmp_path).save()
    kis = KISClient(profile="main", config_dir=tmp_path, transport=_FakeTransport())
    assert kis.environment == "real" and kis.account == "12345678-01"


def test_client_paper_profile_uses_paper_environment(tmp_path):
    KISConfig(profile="paper", app_key="AK", app_secret="SK", config_dir=tmp_path).save()
    kis = KISClient(profile="paper", config_dir=tmp_path, transport=_FakeTransport())
    assert kis.environment == "paper"


def test_client_explicit_keys_skip_file_lookup():
    # 값을 명시하면 파일/env 를 읽지 않는다(격리 환경에 자격증명이 없어도 열린다).
    kis = KISClient(app_key="k", app_secret="s", transport=_FakeTransport())
    assert kis.environment == "real"


def test_client_keeps_explicit_app_key_and_reads_missing_secret(tmp_path):
    # app_key 만 직접 넘기고 secret 은 저장분에서 채운다 -- 사용자가 준 app_key 를
    # '없다'고 오도하지 않고, 실제로 빠진 secret 만 읽어야 한다.
    KISConfig(profile="main", app_key="saved_key", app_secret="saved_secret",
              config_dir=tmp_path).save()
    kis = KISClient(app_key="explicit_key", profile="main", config_dir=tmp_path,
                    transport=_FakeTransport())
    assert kis._app_key == "explicit_key" and kis._app_secret == "saved_secret"


def test_client_missing_credentials_raises():
    with pytest.raises(KISUsageError):   # 저장분·env 모두 없음(격리)
        KISClient(profile="main", transport=_FakeTransport())


def test_client_account_override(monkeypatch):
    monkeypatch.setenv("KIS_APP_KEY", "k")
    monkeypatch.setenv("KIS_APP_SECRET", "s")
    monkeypatch.setenv("KIS_ACCOUNT", "12345678-01")
    kis = KISClient(profile="main", account="87654321-02", transport=_FakeTransport())
    assert kis.account == "87654321-02"


def test_save_then_client_round_trip(tmp_path):
    KISConfig(profile="isa", app_key="AK", app_secret="SK",
              account="11112222-01", config_dir=tmp_path).save()
    kis = KISClient(profile="isa", config_dir=tmp_path, transport=_FakeTransport())
    assert kis.account == "11112222-01" and kis.environment == "real"
