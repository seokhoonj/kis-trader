"""자격증명 API -- KISConfig(값 저장) / resolve_credentials(읽기) / KISClient(profile=).

프로필은 자유 이름, credentials.json 은 프로필별 중첩 객체, 환경(실전/모의)은 프로필별 저장 필드.
"""
from __future__ import annotations

import json
import os

import pytest

from kis_trader import KISClient, KISConfig
from kis_trader.config import resolve_credentials
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


def _creds(config_dir):
    return json.loads((config_dir / "credentials.json").read_text(encoding="utf-8"))


# --- KISConfig.save() (쓰기, 중첩 프로필) -------------------------------------

def test_save_writes_nested_profile_section(tmp_path):
    KISConfig(profile="main", app_key="AK", app_secret="SK",
              account="12345678-01", config_dir=tmp_path).save()
    assert _creds(tmp_path) == {
        "main": {"app_key": "AK", "app_secret": "SK", "environment": "real",
                 "account": "12345678-01"},
    }


def test_save_environment_defaults_real_and_paper_is_explicit(tmp_path):
    KISConfig(profile="live", app_key="AK", app_secret="SK", config_dir=tmp_path).save()
    KISConfig(profile="demo", app_key="PK", app_secret="PS",
              environment="paper", config_dir=tmp_path).save()
    data = _creds(tmp_path)
    assert data["live"]["environment"] == "real"    # 기본 real
    assert data["demo"]["environment"] == "paper"   # 명시해야 paper


def test_save_multiple_same_type_profiles_coexist(tmp_path):
    # 연금저축 2개처럼 같은 유형 여러 계좌 -- 자유 프로필 이름으로 각각.
    KISConfig(profile="pension_a", app_key="A", app_secret="a",
              account="11111111-22", config_dir=tmp_path).save()
    KISConfig(profile="pension_b", app_key="B", app_secret="b",
              account="22222222-22", config_dir=tmp_path).save()
    data = _creds(tmp_path)
    assert data["pension_a"]["account"] == "11111111-22"
    assert data["pension_b"]["account"] == "22222222-22"
    assert set(data) == {"pension_a", "pension_b"}


def test_save_merges_and_preserves_other_profiles(tmp_path):
    KISConfig(profile="main", app_key="MK", app_secret="MS", config_dir=tmp_path).save()
    KISConfig(profile="pension_a", app_key="PK", app_secret="PS", config_dir=tmp_path).save()
    data = _creds(tmp_path)
    assert data["main"]["app_key"] == "MK" and data["pension_a"]["app_key"] == "PK"


def test_save_without_account_omits_the_field(tmp_path):
    KISConfig(profile="main", app_key="AK", app_secret="SK", config_dir=tmp_path).save()
    assert "account" not in _creds(tmp_path)["main"]


def test_secrets_are_hidden_from_repr():
    text = repr(KISConfig(profile="main", app_key="SECRETKEY", app_secret="SECRETSEC",
                          account="12345678-01"))
    assert "SECRETKEY" not in text and "SECRETSEC" not in text


def test_resolved_credentials_hides_secrets_from_repr(tmp_path):
    KISConfig(profile="main", app_key="SECRETKEY", app_secret="SECRETSEC",
              config_dir=tmp_path).save()
    assert "SECRETKEY" not in repr(resolve_credentials("main", config_dir=tmp_path))


def test_construction_requires_nonblank_app_key_and_secret():
    with pytest.raises(KISUsageError):
        KISConfig(profile="main", app_key="AK", app_secret="   ")
    with pytest.raises(TypeError):
        KISConfig(profile="main", app_key="AK")  # type: ignore[call-arg]


def test_construction_rejects_bad_profile_name():
    # 대문자도 거부한다 -- 환경변수 키로 대문자 접힐 때 서로 다른 이름이 aliasing 되지 않도록.
    for bad in ["", "  ", "pension-a", "pen sion", "연금", "Main", "Pension_A"]:
        with pytest.raises(KISUsageError):
            KISConfig(profile=bad, app_key="k", app_secret="s")


def test_construction_rejects_bad_environment():
    with pytest.raises(KISUsageError):
        KISConfig(profile="main", app_key="k", app_secret="s", environment="prod")  # type: ignore[arg-type]


def test_construction_rejects_malformed_account(tmp_path):
    with pytest.raises(KISUsageError):
        KISConfig(profile="main", app_key="AK", app_secret="SK",
                  account="12345678", config_dir=tmp_path)      # 상품코드 없음
    with pytest.raises(KISUsageError):
        KISConfig(profile="main", app_key="AK", app_secret="SK",
                  account="12345678-01-02", config_dir=tmp_path)  # 여분 하이픈
    assert not (tmp_path / "credentials.json").exists()


def test_save_returns_path_and_file_is_owner_only(tmp_path):
    path = KISConfig(profile="main", app_key="AK", app_secret="SK", config_dir=tmp_path).save()
    assert path == tmp_path / "credentials.json"
    if os.name == "posix":
        assert (path.stat().st_mode & 0o777) == 0o600


def test_save_does_not_clobber_malformed_credentials_file(tmp_path):
    path = tmp_path / "credentials.json"
    original = "{ not valid json"
    path.write_text(original, encoding="utf-8")
    with pytest.raises(KISUsageError):
        KISConfig(profile="paper", app_key="PK", app_secret="PS", config_dir=tmp_path).save()
    assert path.read_text(encoding="utf-8") == original


# --- resolve_credentials (읽기) -----------------------------------------------

def test_resolve_reads_saved_profile(tmp_path):
    KISConfig(profile="pension_a", app_key="AK", app_secret="SK",
              account="87654321-22", config_dir=tmp_path).save()
    resolved = resolve_credentials("pension_a", config_dir=tmp_path)
    assert resolved.app_key == "AK" and resolved.account == "87654321-22"
    assert resolved.environment == "real"


def test_resolve_reads_paper_environment(tmp_path):
    KISConfig(profile="demo", app_key="AK", app_secret="SK",
              environment="paper", config_dir=tmp_path).save()
    assert resolve_credentials("demo", config_dir=tmp_path).environment == "paper"


def test_environment_variable_wins_over_file(tmp_path, monkeypatch):
    KISConfig(profile="main", app_key="from_file", app_secret="s", config_dir=tmp_path).save()
    monkeypatch.setenv("KIS_APP_KEY", "from_env")
    assert resolve_credentials("main", config_dir=tmp_path).app_key == "from_env"


def test_environment_variables_use_profile_prefix(monkeypatch):
    monkeypatch.setenv("KIS_PENSION_A_APP_KEY", "k")
    monkeypatch.setenv("KIS_PENSION_A_APP_SECRET", "s")
    monkeypatch.setenv("KIS_PENSION_A_ACCOUNT", "87654321-22")
    monkeypatch.setenv("KIS_PENSION_A_ENVIRONMENT", "paper")
    resolved = resolve_credentials("pension_a")
    assert resolved.account == "87654321-22" and resolved.environment == "paper"


def test_main_and_named_profile_env_vars_do_not_collide(monkeypatch):
    # main 은 접두어 없이(KIS_APP_KEY), 명명 프로필은 KIS_<이름>_APP_KEY -- 서로 안 섞인다.
    monkeypatch.setenv("KIS_APP_KEY", "main_k")
    monkeypatch.setenv("KIS_APP_SECRET", "main_s")
    monkeypatch.setenv("KIS_PENSION_A_APP_KEY", "pa_k")
    monkeypatch.setenv("KIS_PENSION_A_APP_SECRET", "pa_s")
    assert resolve_credentials("main").app_key == "main_k"
    assert resolve_credentials("pension_a").app_key == "pa_k"


def test_resolve_missing_credentials_names_the_variable():
    with pytest.raises(KISUsageError) as excinfo:
        resolve_credentials("pension_a")
    assert "KIS_PENSION_A_APP_KEY" in str(excinfo.value)


def test_resolve_account_none_when_absent(monkeypatch):
    monkeypatch.setenv("KIS_APP_KEY", "k")
    monkeypatch.setenv("KIS_APP_SECRET", "s")
    assert resolve_credentials("main").account is None


def test_resolve_malformed_account_fails_closed(monkeypatch):
    monkeypatch.setenv("KIS_APP_KEY", "k")
    monkeypatch.setenv("KIS_APP_SECRET", "s")
    monkeypatch.setenv("KIS_ACCOUNT", "12345678-01-02")   # 여분 하이픈
    with pytest.raises(KISUsageError):
        resolve_credentials("main")


def test_resolve_bad_environment_fails_closed(monkeypatch):
    monkeypatch.setenv("KIS_APP_KEY", "k")
    monkeypatch.setenv("KIS_APP_SECRET", "s")
    monkeypatch.setenv("KIS_ENVIRONMENT", "prod")
    with pytest.raises(KISUsageError):
        resolve_credentials("main")


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_value_is_absent(monkeypatch, blank):
    monkeypatch.setenv("KIS_APP_KEY", blank)
    with pytest.raises(KISUsageError):
        resolve_credentials("main")


def test_malformed_credentials_file_raises(tmp_path):
    (tmp_path / "credentials.json").write_text("{", encoding="utf-8")
    with pytest.raises(KISUsageError):
        resolve_credentials("main", config_dir=tmp_path)


def test_non_object_profile_section_raises(tmp_path):
    (tmp_path / "credentials.json").write_text(
        json.dumps({"main": "not-an-object"}), encoding="utf-8")
    with pytest.raises(KISUsageError):
        resolve_credentials("main", config_dir=tmp_path)


def test_save_does_not_clobber_non_object_profile_section(tmp_path):
    # 프로필 섹션이 객체가 아니면 병합을 멈춘다 -- 남의(형상 이상한) 프로필을 날리지 않도록.
    path = tmp_path / "credentials.json"
    original = json.dumps({"other": "not-an-object"})
    path.write_text(original, encoding="utf-8")
    with pytest.raises(KISUsageError):
        KISConfig(profile="main", app_key="AK", app_secret="SK", config_dir=tmp_path).save()
    assert path.read_text(encoding="utf-8") == original


# --- 환경 기본값(읽기 경로) ---------------------------------------------------

def test_resolve_credentials_environment_defaults_real_when_absent(tmp_path):
    # environment 필드가 없는 섹션(손 편집 등) -> real 로 폴백
    (tmp_path / "credentials.json").write_text(
        '{"demo": {"app_key": "AK", "app_secret": "SK"}}', encoding="utf-8")
    assert resolve_credentials("demo", config_dir=tmp_path).environment == "real"


# --- KISClient(profile=) 세션 -------------------------------------------------

def test_client_reads_saved_profile(tmp_path):
    KISConfig(profile="main", app_key="AK", app_secret="SK",
              account="12345678-01", config_dir=tmp_path).save()
    kis = KISClient(profile="main", config_dir=tmp_path, transport=_FakeTransport())
    assert kis.environment == "real" and kis.account == "12345678-01"


def test_client_reads_paper_environment_from_profile(tmp_path):
    KISConfig(profile="demo", app_key="AK", app_secret="SK",
              environment="paper", config_dir=tmp_path).save()
    kis = KISClient(profile="demo", config_dir=tmp_path, transport=_FakeTransport())
    assert kis.environment == "paper"


def test_client_explicit_keys_default_real_without_file():
    kis = KISClient(app_key="k", app_secret="s", transport=_FakeTransport())
    assert kis.environment == "real"


def test_client_explicit_environment_overrides(tmp_path):
    kis = KISClient(app_key="k", app_secret="s", environment="paper", transport=_FakeTransport())
    assert kis.environment == "paper"


def test_client_explicit_environment_overrides_saved_profile(tmp_path):
    # 저장된 프로필이 paper 여도, 명시한 environment 가 이긴다(빠진 키만 파일에서 채우는 경우에도).
    KISConfig(profile="demo", app_key="AK", app_secret="SK",
              environment="paper", config_dir=tmp_path).save()
    kis = KISClient(profile="demo", environment="real", config_dir=tmp_path,
                    transport=_FakeTransport())
    assert kis.environment == "real"


def test_client_rejects_bad_explicit_environment():
    with pytest.raises(KISUsageError):
        KISClient(app_key="k", app_secret="s", environment="prod", transport=_FakeTransport())  # type: ignore[arg-type]


def test_client_keeps_explicit_app_key_and_reads_missing_secret(tmp_path):
    KISConfig(profile="main", app_key="saved_key", app_secret="saved_secret",
              config_dir=tmp_path).save()
    kis = KISClient(app_key="explicit_key", profile="main", config_dir=tmp_path,
                    transport=_FakeTransport())
    assert kis._app_key == "explicit_key" and kis._app_secret == "saved_secret"


def test_client_missing_credentials_raises():
    with pytest.raises(KISUsageError):
        KISClient(profile="main", transport=_FakeTransport())


def test_client_account_override(tmp_path):
    KISConfig(profile="main", app_key="AK", app_secret="SK",
              account="12345678-01", config_dir=tmp_path).save()
    kis = KISClient(profile="main", account="87654321-02", config_dir=tmp_path,
                    transport=_FakeTransport())
    assert kis.account == "87654321-02"


def test_save_then_client_round_trip(tmp_path):
    KISConfig(profile="pension_b", app_key="AK", app_secret="SK",
              account="11112222-22", config_dir=tmp_path).save()
    kis = KISClient(profile="pension_b", config_dir=tmp_path, transport=_FakeTransport())
    assert kis.account == "11112222-22" and kis.environment == "real"


# --- 기본 프로필 해석 (profile 미지정) ---------------------------------------

def test_default_profile_uses_first_entry(tmp_path):
    KISConfig(profile="isa",     app_key="IK", app_secret="IS", config_dir=tmp_path).save()
    KISConfig(profile="pension", app_key="PK", app_secret="PS", config_dir=tmp_path).save()
    assert resolve_credentials(config_dir=tmp_path).app_key == "IK"   # 첫 항목 isa


def test_default_profile_env_overrides_first_entry(tmp_path, monkeypatch):
    KISConfig(profile="isa",     app_key="IK", app_secret="IS", config_dir=tmp_path).save()
    KISConfig(profile="pension", app_key="PK", app_secret="PS", config_dir=tmp_path).save()
    monkeypatch.setenv("KIS_DEFAULT_PROFILE", "pension")
    assert resolve_credentials(config_dir=tmp_path).app_key == "PK"   # env 가 첫 항목을 덮음


def test_default_profile_blank_env_is_ignored(tmp_path, monkeypatch):
    KISConfig(profile="isa", app_key="IK", app_secret="IS", config_dir=tmp_path).save()
    monkeypatch.setenv("KIS_DEFAULT_PROFILE", "   ")                  # 공백 = 미설정
    assert resolve_credentials(config_dir=tmp_path).app_key == "IK"


def test_default_profile_falls_back_to_main_env_only(monkeypatch):
    monkeypatch.setenv("KIS_APP_KEY", "main_k")                       # 파일 없음 + 접두어 없는 키
    monkeypatch.setenv("KIS_APP_SECRET", "main_s")
    assert resolve_credentials().app_key == "main_k"                 # -> main 폴백


def test_default_environment_uses_first_entry(tmp_path):
    KISConfig(profile="demo", app_key="DK", app_secret="DS",
              environment="paper", config_dir=tmp_path).save()
    KISConfig(profile="live", app_key="LK", app_secret="LS", config_dir=tmp_path).save()
    assert resolve_credentials(config_dir=tmp_path).environment == "paper"   # 첫 항목 demo = 모의


def test_client_opens_default_profile_first_entry(tmp_path):
    KISConfig(profile="isa", app_key="IK", app_secret="IS", account="12345678-01",
              environment="paper", config_dir=tmp_path).save()
    kis = KISClient(config_dir=tmp_path, transport=_FakeTransport())  # profile 미지정
    assert kis.environment == "paper"        # 첫 항목 isa(paper) 를 열었다(main real 폴백 아님)
    assert kis.account == "12345678-01"      # 게이트가 판정하는 계좌도 첫 항목 것


def test_default_profile_env_nonexistent_fails_closed(tmp_path, monkeypatch):
    KISConfig(profile="isa", app_key="IK", app_secret="IS", config_dir=tmp_path).save()
    monkeypatch.setenv("KIS_DEFAULT_PROFILE", "missing")   # 저장 없는 프로필 지목
    # 첫 항목으로 새지 않고 fail-closed -- 지목한 프로필의 변수 이름을 짚는다
    with pytest.raises(KISUsageError, match="KIS_MISSING_APP_KEY"):
        resolve_credentials(config_dir=tmp_path)


@pytest.mark.parametrize("bad", ["Paper", "paper-name", "paper name"])
def test_default_profile_env_invalid_name_raises(tmp_path, monkeypatch, bad):
    KISConfig(profile="isa", app_key="IK", app_secret="IS", config_dir=tmp_path).save()
    monkeypatch.setenv("KIS_DEFAULT_PROFILE", bad)        # 형식오류 이름은 접두어로 새지 않고 거부
    with pytest.raises(KISUsageError, match="소문자/숫자/밑줄"):
        resolve_credentials(config_dir=tmp_path)


def test_default_first_entry_uses_its_profile_env_prefix(tmp_path, monkeypatch):
    KISConfig(profile="isa",     app_key="file_isa", app_secret="s", config_dir=tmp_path).save()
    KISConfig(profile="pension", app_key="file_pen", app_secret="s", config_dir=tmp_path).save()
    monkeypatch.setenv("KIS_APP_KEY", "unprefixed")       # main 접두어(첫 항목 아님)
    monkeypatch.setenv("KIS_ISA_APP_KEY", "prefixed_isa") # 첫 항목 isa 의 접두어
    # 미지정 -> 첫 항목 isa -> KIS_ISA_* 로 읽는다(KIS_APP_KEY 도 파일값도 아님)
    assert resolve_credentials(config_dir=tmp_path).app_key == "prefixed_isa"


def test_default_profile_env_strips_surrounding_whitespace(tmp_path, monkeypatch):
    KISConfig(profile="isa",     app_key="IK", app_secret="IS", config_dir=tmp_path).save()
    KISConfig(profile="pension", app_key="PK", app_secret="PS", config_dir=tmp_path).save()
    monkeypatch.setenv("KIS_DEFAULT_PROFILE", "  pension  ")   # 주변 공백 제거 후 pension
    assert resolve_credentials(config_dir=tmp_path).app_key == "PK"


def test_empty_credentials_file_falls_back_to_main(tmp_path, monkeypatch):
    (tmp_path / "credentials.json").write_text("{}", encoding="utf-8")   # 빈 객체(파일 있음)
    monkeypatch.setenv("KIS_APP_KEY", "main_k")
    monkeypatch.setenv("KIS_APP_SECRET", "main_s")
    assert resolve_credentials(config_dir=tmp_path).app_key == "main_k"   # -> main 폴백


# --- 기본 프로필 마커 (KISConfig.set_default) ---------------------------------

def test_set_default_marks_and_resolution_uses_it(tmp_path):
    KISConfig(profile="main",    app_key="MK", app_secret="MS", config_dir=tmp_path).save()
    KISConfig(profile="pension", app_key="PK", app_secret="PS", config_dir=tmp_path).save()
    KISConfig.set_default("pension", config_dir=tmp_path)
    # 첫 항목은 main 이지만 마커가 pension -> 미지정 해석은 pension
    assert resolve_credentials(config_dir=tmp_path).app_key == "PK"
    assert _creds(tmp_path)["default"] == "pension"


def test_default_marker_loses_to_env_var(tmp_path, monkeypatch):
    KISConfig(profile="main", app_key="MK", app_secret="MS", config_dir=tmp_path).save()
    KISConfig(profile="isa",  app_key="IK", app_secret="IS", config_dir=tmp_path).save()
    KISConfig.set_default("isa", config_dir=tmp_path)
    monkeypatch.setenv("KIS_DEFAULT_PROFILE", "main")   # env 가 마커보다 우선
    assert resolve_credentials(config_dir=tmp_path).app_key == "MK"


def test_set_default_nonexistent_profile_rejected(tmp_path):
    KISConfig(profile="main", app_key="MK", app_secret="MS", config_dir=tmp_path).save()
    with pytest.raises(KISUsageError, match="없다"):
        KISConfig.set_default("missing", config_dir=tmp_path)


def test_set_default_preserves_other_profiles_and_is_owner_only(tmp_path):
    KISConfig(profile="main", app_key="MK", app_secret="MS", config_dir=tmp_path).save()
    KISConfig(profile="isa",  app_key="IK", app_secret="IS", config_dir=tmp_path).save()
    path = KISConfig.set_default("isa", config_dir=tmp_path)
    data = _creds(tmp_path)
    assert data["main"]["app_key"] == "MK" and data["isa"]["app_key"] == "IK"   # 다른 프로필 보존
    assert (path.stat().st_mode & 0o777) == 0o600


def test_profile_named_default_is_reserved():
    with pytest.raises(KISUsageError, match="예약"):
        KISConfig(profile="default", app_key="k", app_secret="s")


def test_first_entry_skips_default_meta_key(tmp_path):
    # "default" 마커가 공백이라 무시될 때, 첫 항목 계산에서 "default" 키 자체는 건너뛴다
    (tmp_path / "credentials.json").write_text(
        '{"default": "  ", "isa": {"app_key": "IK", "app_secret": "IS"}}', encoding="utf-8")
    assert resolve_credentials(config_dir=tmp_path).app_key == "IK"
