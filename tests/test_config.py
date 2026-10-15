"""자격증명 API -- KISConfig(값 저장) / resolve_credentials(읽기) / KISClient(profile=).

프로필은 자유 이름, credentials.json 은 프로필별 중첩 객체, 환경(실전/모의)은 프로필별 저장 필드.
"""
from __future__ import annotations

import json
import os

import pytest

from kis_trader import KISClient, KISConfig
from kis_trader.config import (
    _V2_TOP_LEVEL_ORDER,
    _detect_schema,
    _profile_exists,
    list_profiles,
    resolve_credentials,
)
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
    # v2: environment 바깥 블록 -> 프로필 묶음. 묶음 안에 environment 필드는 없다. 기본 환경은 paper.
    KISConfig(profile="main", app_key="AK", app_secret="SK",
              account="12345678-01", config_dir=tmp_path).save()
    assert _creds(tmp_path) == {
        "schema": 2,
        "paper": {"main": {"app_key": "AK", "app_secret": "SK", "account": "12345678-01"}},
    }


def test_save_environment_defaults_paper_and_real_is_explicit(tmp_path):
    KISConfig(profile="demo", app_key="PK", app_secret="PS", config_dir=tmp_path).save()
    KISConfig(profile="live", app_key="AK", app_secret="SK",
              environment="real", config_dir=tmp_path).save()
    data = _creds(tmp_path)
    assert "demo" in data["paper"]    # 기본 paper (fail-safe)
    assert "live" in data["real"]     # 명시해야 real


def test_save_multiple_same_type_profiles_coexist(tmp_path):
    # 연금저축 2개처럼 같은 유형 여러 계좌 -- 자유 프로필 이름으로 각각(기본 환경 paper 블록).
    KISConfig(profile="pension_a", app_key="A", app_secret="a",
              account="11111111-22", config_dir=tmp_path).save()
    KISConfig(profile="pension_b", app_key="B", app_secret="b",
              account="22222222-22", config_dir=tmp_path).save()
    data = _creds(tmp_path)["paper"]
    assert data["pension_a"]["account"] == "11111111-22"
    assert data["pension_b"]["account"] == "22222222-22"
    assert set(data) == {"pension_a", "pension_b"}


def test_save_merges_and_preserves_other_profiles(tmp_path):
    KISConfig(profile="main", app_key="MK", app_secret="MS", config_dir=tmp_path).save()
    KISConfig(profile="pension_a", app_key="PK", app_secret="PS", config_dir=tmp_path).save()
    data = _creds(tmp_path)["paper"]
    assert data["main"]["app_key"] == "MK" and data["pension_a"]["app_key"] == "PK"


def test_save_without_account_omits_the_field(tmp_path):
    KISConfig(profile="main", app_key="AK", app_secret="SK", config_dir=tmp_path).save()
    assert "account" not in _creds(tmp_path)["paper"]["main"]


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
        KISConfig(profile="demo", app_key="PK", app_secret="PS", config_dir=tmp_path).save()
    assert path.read_text(encoding="utf-8") == original


# --- resolve_credentials (읽기) -----------------------------------------------

def test_resolve_reads_saved_profile(tmp_path):
    # 기본 환경 paper 로 저장되고, 환경 미지정 해석도 paper 기본이라 같은 슬롯을 연다.
    KISConfig(profile="pension_a", app_key="AK", app_secret="SK",
              account="87654321-22", config_dir=tmp_path).save()
    resolved = resolve_credentials("pension_a", config_dir=tmp_path)
    assert resolved.app_key == "AK" and resolved.account == "87654321-22"
    assert resolved.environment == "paper"


def test_resolve_reads_paper_environment(tmp_path):
    KISConfig(profile="demo", app_key="AK", app_secret="SK",
              environment="paper", config_dir=tmp_path).save()
    assert resolve_credentials("demo", config_dir=tmp_path).environment == "paper"


def test_environment_variable_wins_over_file(tmp_path, monkeypatch):
    # v2 두 축 키(KIS_<P>_<E>_APP_KEY)가 저장된 묶음 값을 이긴다.
    KISConfig(profile="main", app_key="from_file", app_secret="s", config_dir=tmp_path).save()
    monkeypatch.setenv("KIS_MAIN_PAPER_APP_KEY", "from_env")
    assert resolve_credentials("main", config_dir=tmp_path).app_key == "from_env"


def test_resolve_account_none_when_absent(tmp_path):
    # 묶음에 account 가 없으면 resolved.account 는 None(시세전용 계좌).
    _write_v2(tmp_path, paper={"general": {"app_key": "k", "app_secret": "s"}})
    assert resolve_credentials("general", config_dir=tmp_path).account is None


def test_resolve_malformed_account_fails_closed(tmp_path):
    # 묶음의 account 형식이 틀리면 fail-closed(세션이 CANO-상품코드로 쪼갤 수 있게).
    _write_v2(tmp_path, paper={"general": {"app_key": "k", "app_secret": "s",
                                           "account": "12345678-01-02"}})   # 여분 하이픈
    with pytest.raises(KISUsageError):
        resolve_credentials("general", config_dir=tmp_path)


def test_resolve_bad_environment_fails_closed(tmp_path, monkeypatch):
    # 해석된 환경이 real/paper 가 아니면 자격증명 조립 전에 막는다.
    _write_v2(tmp_path, paper={"general": {"app_key": "k", "app_secret": "s"}})
    monkeypatch.setenv("KIS_DEFAULT_ENVIRONMENT", "prod")
    with pytest.raises(KISUsageError, match="environment"):
        resolve_credentials("general", config_dir=tmp_path)


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_value_is_absent(tmp_path, monkeypatch, blank):
    # 두 축 환경변수의 빈/공백 값은 유효한 자격증명이 아니라 '없음'으로 취급 -> app_key 누락으로 막힌다.
    _write_v2(tmp_path, paper={"general": {"app_secret": "s"}})
    monkeypatch.setenv("KIS_GENERAL_PAPER_APP_KEY", blank)
    with pytest.raises(KISUsageError, match="KIS_GENERAL_PAPER_APP_KEY"):
        resolve_credentials("general", config_dir=tmp_path)


def test_malformed_credentials_file_raises(tmp_path):
    (tmp_path / "credentials.json").write_text("{", encoding="utf-8")
    with pytest.raises(KISUsageError):
        resolve_credentials("main", config_dir=tmp_path)


def test_non_object_profile_section_raises(tmp_path):
    (tmp_path / "credentials.json").write_text(
        json.dumps({"main": "not-an-object"}), encoding="utf-8")
    with pytest.raises(KISUsageError):
        resolve_credentials("main", config_dir=tmp_path)


def test_save_does_not_clobber_non_object_block(tmp_path):
    # v2 환경 블록이 객체가 아니면 병합을 멈춘다 -- 형상 이상한 파일을 날리지 않도록.
    path = tmp_path / "credentials.json"
    original = json.dumps({"schema": 2, "real": "not-an-object"})
    path.write_text(original, encoding="utf-8")
    with pytest.raises(KISUsageError):
        KISConfig(profile="main", app_key="AK", app_secret="SK", config_dir=tmp_path).save()
    assert path.read_text(encoding="utf-8") == original


# --- 환경 기본값(읽기 경로) ---------------------------------------------------

def test_legacy_file_does_not_auto_default_to_real(tmp_path):
    # 예전엔 environment 필드가 없는 평평한 섹션이 real 로 폴백했다 -- 이제는 조용히 real 로 열지 않고
    # 'kis config migrate' 안내 에러로 멈춘다(런타임은 두 축 schema 2 만 읽는다).
    (tmp_path / "credentials.json").write_text(
        '{"demo": {"app_key": "AK", "app_secret": "SK"}}', encoding="utf-8")
    with pytest.raises(KISUsageError, match="migrate"):
        resolve_credentials("demo", config_dir=tmp_path)


# --- KISClient(profile=) 세션 -------------------------------------------------

def test_client_reads_saved_profile(tmp_path):
    KISConfig(profile="main", app_key="AK", app_secret="SK",
              account="12345678-01", config_dir=tmp_path).save()
    kis = KISClient(profile="main", config_dir=tmp_path, transport=_FakeTransport())
    assert kis.environment == "paper" and kis._account == "12345678-01"


def test_client_reads_paper_environment_from_profile(tmp_path):
    KISConfig(profile="demo", app_key="AK", app_secret="SK",
              environment="paper", config_dir=tmp_path).save()
    kis = KISClient(profile="demo", config_dir=tmp_path, transport=_FakeTransport())
    assert kis.environment == "paper"


def test_client_explicit_keys_require_environment():
    # 직접 주입(app_key/app_secret)은 environment 를 명시해야 한다 -- 미명시면 생성 시점 raise.
    with pytest.raises(KISUsageError, match="environment 를 명시"):
        KISClient(app_key="k", app_secret="s", account="12345678-01", transport=_FakeTransport())


def test_client_explicit_keys_accept_both_environments():
    # environment 를 주면 paper/real 둘 다 성립한다.
    paper = KISClient(app_key="k", app_secret="s", environment="paper", transport=_FakeTransport())
    assert paper.environment == "paper"
    real = KISClient(app_key="k", app_secret="s", environment="real", transport=_FakeTransport())
    assert real.environment == "real"


def test_client_explicit_environment_overrides(tmp_path):
    kis = KISClient(app_key="k", app_secret="s", environment="paper", transport=_FakeTransport())
    assert kis.environment == "paper"


def test_client_environment_selects_the_matching_slot(tmp_path):
    # v2: environment 는 override 가 아니라 selector -- 같은 프로필의 real/paper 슬롯을 고른다.
    KISConfig(profile="demo", app_key="PK", app_secret="PS",
              environment="paper", config_dir=tmp_path).save()
    KISConfig(profile="demo", app_key="RK", app_secret="RS",
              environment="real", config_dir=tmp_path).save()
    kis = KISClient(profile="demo", environment="real", config_dir=tmp_path,
                    transport=_FakeTransport())
    assert kis.environment == "real" and kis._app_key == "RK"


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
    assert kis._account == "87654321-02"


def test_save_then_client_round_trip(tmp_path):
    KISConfig(profile="pension_b", app_key="AK", app_secret="SK",
              account="11112222-22", config_dir=tmp_path).save()
    kis = KISClient(profile="pension_b", config_dir=tmp_path, transport=_FakeTransport())
    assert kis._account == "11112222-22" and kis.environment == "paper"


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


def test_missing_store_errors_cleanly(monkeypatch):
    # 파일도 설정도 없으면(빈 저장소) 두 축 해석기가 기본 프로필을 못 정해 깔끔한 KISUsageError 로 막는다
    # (크래시 아님). 레거시의 무접두 KIS_APP_KEY->main 제로컨피그 폴백은 더 이상 없다.
    monkeypatch.setenv("KIS_APP_KEY", "main_k")                      # 레거시 무접두 키는 런타임이 무시
    monkeypatch.setenv("KIS_APP_SECRET", "main_s")
    with pytest.raises(KISUsageError):
        resolve_credentials()


def test_default_environment_defaults_to_paper(tmp_path):
    # v2: 환경은 별개 축이라 체인으로 정한다(인자/env/마커 없으면 paper). 프로필 순서와 무관.
    KISConfig(profile="demo", app_key="DK", app_secret="DS", config_dir=tmp_path).save()
    assert resolve_credentials(config_dir=tmp_path).environment == "paper"


def test_client_opens_default_profile_first_entry(tmp_path):
    KISConfig(profile="isa", app_key="IK", app_secret="IS", account="12345678-01",
              environment="paper", config_dir=tmp_path).save()
    kis = KISClient(config_dir=tmp_path, transport=_FakeTransport())  # profile 미지정
    assert kis.environment == "paper"        # 첫 항목 isa(paper) 를 열었다(main real 폴백 아님)
    assert kis._account == "12345678-01"  # 게이트가 판정하는 계좌도 첫 항목 것


def test_default_profile_env_nonexistent_fails_closed(tmp_path, monkeypatch):
    KISConfig(profile="isa", app_key="IK", app_secret="IS", config_dir=tmp_path).save()
    monkeypatch.setenv("KIS_DEFAULT_PROFILE", "missing")   # 저장 없는 프로필 지목
    # 첫 항목으로 새지 않고 fail-closed -- 이름 에코가 아니라 해석기의 "찾을 수 없다" + real/paper 나열
    with pytest.raises(KISUsageError, match="찾을 수 없다") as excinfo:
        resolve_credentials(config_dir=tmp_path)
    msg = str(excinfo.value)
    assert "missing" in msg and "paper:" in msg and "isa" in msg   # 지목 이름 + 있는 프로필 나열


@pytest.mark.parametrize("bad", ["Paper", "paper-name", "paper name"])
def test_default_profile_env_invalid_name_raises(tmp_path, monkeypatch, bad):
    KISConfig(profile="isa", app_key="IK", app_secret="IS", config_dir=tmp_path).save()
    monkeypatch.setenv("KIS_DEFAULT_PROFILE", bad)        # 형식오류 이름은 접두어로 새지 않고 거부
    with pytest.raises(KISUsageError, match="소문자/숫자/밑줄"):
        resolve_credentials(config_dir=tmp_path)


def test_default_first_entry_uses_its_profile_env_prefix(tmp_path, monkeypatch):
    KISConfig(profile="isa",     app_key="file_isa", app_secret="s", config_dir=tmp_path).save()
    KISConfig(profile="pension", app_key="file_pen", app_secret="s", config_dir=tmp_path).save()
    monkeypatch.setenv("KIS_ISA_PAPER_APP_KEY", "prefixed_isa")  # 첫 항목 isa + 기본 환경 paper 의 두 축 키
    monkeypatch.setenv("KIS_ISA_REAL_APP_KEY", "wrong_env")      # 틀린 환경 축 -- 무시돼야 한다(축 격리)
    monkeypatch.setenv("KIS_APP_KEY", "unprefixed")             # 무접두(레거시) 키 -- v2 에서 무시
    # 미지정 -> 첫 항목 isa + 기본 환경 paper -> KIS_ISA_PAPER_* 로 읽는다(틀린 축/무접두/파일값 아님)
    resolved = resolve_credentials(config_dir=tmp_path)
    assert resolved.app_key == "prefixed_isa"
    assert resolved.app_key not in ("wrong_env", "unprefixed", "file_isa")


def test_default_profile_env_strips_surrounding_whitespace(tmp_path, monkeypatch):
    KISConfig(profile="isa",     app_key="IK", app_secret="IS", config_dir=tmp_path).save()
    KISConfig(profile="pension", app_key="PK", app_secret="PS", config_dir=tmp_path).save()
    monkeypatch.setenv("KIS_DEFAULT_PROFILE", "  pension  ")   # 주변 공백 제거 후 pension
    assert resolve_credentials(config_dir=tmp_path).app_key == "PK"


def test_empty_credentials_file_errors_cleanly(tmp_path, monkeypatch):
    # 빈 객체 파일({})도 비어있는 저장소라 '예전처럼' 깔끔한 에러를 낸다 -- migrate 안내가 아니라
    # 두 축 해석기의 "프로필 없음" 에러(레거시 main 폴백 소멸).
    (tmp_path / "credentials.json").write_text("{}", encoding="utf-8")   # 빈 객체(파일 있음)
    monkeypatch.setenv("KIS_APP_KEY", "main_k")                          # 레거시 무접두 키는 런타임이 무시
    monkeypatch.setenv("KIS_APP_SECRET", "main_s")
    with pytest.raises(KISUsageError):
        resolve_credentials(config_dir=tmp_path)


# --- 기본 프로필 마커 (KISConfig.set_default_profile) -------------------------

def test_set_default_marks_and_resolution_uses_it(tmp_path):
    KISConfig(profile="main",    app_key="MK", app_secret="MS", config_dir=tmp_path).save()
    KISConfig(profile="pension", app_key="PK", app_secret="PS", config_dir=tmp_path).save()
    KISConfig.set_default_profile("pension", config_dir=tmp_path)
    # 첫 항목은 main 이지만 마커가 pension -> 미지정 해석은 pension
    assert resolve_credentials(config_dir=tmp_path).app_key == "PK"
    assert _creds(tmp_path)["default_profile"] == "pension"


def test_default_marker_loses_to_env_var(tmp_path, monkeypatch):
    KISConfig(profile="main", app_key="MK", app_secret="MS", config_dir=tmp_path).save()
    KISConfig(profile="isa",  app_key="IK", app_secret="IS", config_dir=tmp_path).save()
    KISConfig.set_default_profile("isa", config_dir=tmp_path)
    monkeypatch.setenv("KIS_DEFAULT_PROFILE", "main")   # env 가 마커보다 우선
    assert resolve_credentials(config_dir=tmp_path).app_key == "MK"


def test_set_default_nonexistent_profile_rejected(tmp_path):
    KISConfig(profile="main", app_key="MK", app_secret="MS", config_dir=tmp_path).save()
    with pytest.raises(KISUsageError, match="없다"):
        KISConfig.set_default_profile("missing", config_dir=tmp_path)


def test_set_default_preserves_other_profiles_and_is_owner_only(tmp_path):
    KISConfig(profile="main", app_key="MK", app_secret="MS", config_dir=tmp_path).save()
    KISConfig(profile="isa",  app_key="IK", app_secret="IS", config_dir=tmp_path).save()
    path = KISConfig.set_default_profile("isa", config_dir=tmp_path)
    data = _creds(tmp_path)["paper"]
    assert data["main"]["app_key"] == "MK" and data["isa"]["app_key"] == "IK"   # 다른 슬롯 보존
    assert (path.stat().st_mode & 0o777) == 0o600


def test_profile_named_default_profile_is_reserved():
    with pytest.raises(KISUsageError, match="예약"):
        KISConfig(profile="default_profile", app_key="k", app_secret="s")


def test_first_entry_skips_default_meta_key(tmp_path):
    # "default_profile" 마커가 공백이라 무시될 때, 첫 항목 계산에서 "default_profile" 키 자체는 건너뛴다
    # (v2: 환경 블록 안의 프로필만 후보).
    _write_v2(tmp_path, default_profile="  ",
              paper={"isa": {"app_key": "IK", "app_secret": "IS"}})
    assert resolve_credentials(config_dir=tmp_path).app_key == "IK"


# --- 공유 HTS 아이디 (KISConfig.set_hts_id) -----------------------------------

def test_set_hts_id_writes_top_level_and_resolves(tmp_path):
    KISConfig(profile="main", app_key="MK", app_secret="MS", config_dir=tmp_path).save()
    KISConfig.set_hts_id("MY_HTS", config_dir=tmp_path)
    assert _creds(tmp_path)["hts_id"] == "MY_HTS"                       # 최상위에 기록
    assert resolve_credentials("main", config_dir=tmp_path).hts_id == "MY_HTS"  # 모든 프로필 공유


def test_set_hts_id_blank_rejected(tmp_path):
    with pytest.raises(KISUsageError, match="비어"):
        KISConfig.set_hts_id("   ", config_dir=tmp_path)


def test_set_hts_id_env_var_overrides_file(tmp_path, monkeypatch):
    KISConfig(profile="main", app_key="MK", app_secret="MS", config_dir=tmp_path).save()
    KISConfig.set_hts_id("FILE_HTS", config_dir=tmp_path)
    monkeypatch.setenv("KIS_HTS_ID", "ENV_HTS")                         # env 가 파일보다 우선
    assert resolve_credentials("main", config_dir=tmp_path).hts_id == "ENV_HTS"


# --- 공유 계좌비밀번호 (account_password, 예약 최상위 키; 야간 파생 잔고 등의 ACNT_PWD) --------
_AP_BUNDLE = {"app_key": "K", "app_secret": "S", "account": "12345678-01"}   # 묶음엔 environment 없음(v2)


def test_account_password_absent_is_none(tmp_path):
    _write_v2(tmp_path, paper={"main": _AP_BUNDLE})
    assert resolve_credentials("main", config_dir=tmp_path).account_password is None


def test_account_password_is_reserved_not_a_profile(tmp_path):
    # 최상위 account_password 는 예약 키라 기본(첫) 프로필로 뽑히지 않는다 -> main 이 해석된다.
    _write_v2(tmp_path, account_password="0000", paper={"main": _AP_BUNDLE})
    assert resolve_credentials(config_dir=tmp_path).app_key == "K"


def test_account_password_excluded_from_repr(tmp_path):
    _write_v2(tmp_path, account_password="SECRETPW", paper={"main": _AP_BUNDLE})
    assert "SECRETPW" not in repr(resolve_credentials("main", config_dir=tmp_path))


def test_require_account_password_raises_when_absent(tmp_path):
    _write_v2(tmp_path, paper={"main": _AP_BUNDLE})
    kis = KISClient(profile="main", config_dir=tmp_path, transport=_FakeTransport())
    with pytest.raises(KISUsageError, match="계좌비밀번호"):
        kis._require_account_password()


def test_require_account_password_returns_resolved_value(tmp_path):
    _write_v2(tmp_path, account_password="0000", paper={"main": _AP_BUNDLE})
    kis = KISClient(profile="main", config_dir=tmp_path, transport=_FakeTransport())
    assert kis._require_account_password() == "0000"


def test_set_hts_id_preserves_profiles_and_orders_reserved_first(tmp_path):
    KISConfig(profile="main", app_key="MK", app_secret="MS", config_dir=tmp_path).save()
    KISConfig(profile="isa",  app_key="IK", app_secret="IS", config_dir=tmp_path).save()
    KISConfig.set_default_profile("isa", config_dir=tmp_path)
    path = KISConfig.set_hts_id("MY_HTS", config_dir=tmp_path)
    data = _creds(tmp_path)
    assert data["paper"]["main"]["app_key"] == "MK" and data["paper"]["isa"]["app_key"] == "IK"
    assert list(data)[:3] == ["schema", "hts_id", "default_profile"]   # 예약 키가 맨 위, 정해진 순서
    assert (path.stat().st_mode & 0o777) == 0o600                      # 소유자만


def test_client_profile_session_picks_up_saved_hts_id(tmp_path):
    KISConfig(profile="main", app_key="MK", app_secret="MS", config_dir=tmp_path).save()
    KISConfig.set_hts_id("MY_HTS", config_dir=tmp_path)
    kis = KISClient(profile="main", config_dir=tmp_path, transport=_FakeTransport())
    assert kis.hts_id == "MY_HTS"


def test_profile_named_hts_id_is_reserved():
    with pytest.raises(KISUsageError, match="예약"):
        KISConfig(profile="hts_id", app_key="k", app_secret="s")


# ============================================================================
# schema v2 (two-axis / env-outer) -- 추가 커버리지
# ============================================================================

def _write_v2(tmp_path, **top):
    """v2 credentials.json 을 날것으로 쓴다(schema 마커 포함). top=최상위 키(real=, paper=, 등)."""
    data = {"schema": 2, **top}
    (tmp_path / "credentials.json").write_text(json.dumps(data), encoding="utf-8")


# --- 저장 프로필 목록 ------------------------------------------------

def test_list_profiles_returns_sorted_union_and_environment_members(tmp_path):
    _write_v2(
        tmp_path,
        hts_id="HTS_USER",
        account_password="SECRET_PASSWORD",
        default_environment="paper",
        default_profile="general",
        real={
            "pension": {"app_key": "RK2", "app_secret": "RS2"},
            "general": {"app_key": "RK1", "app_secret": "RS1"},
        },
        paper={
            "isa": {"app_key": "PK2", "app_secret": "PS2"},
            "general": {"app_key": "PK1", "app_secret": "PS1"},
        },
    )

    assert list_profiles(config_dir=tmp_path) == ["general", "isa", "pension"]
    assert list_profiles(environment="real", config_dir=tmp_path) == ["general", "pension"]
    assert list_profiles(environment="paper", config_dir=tmp_path) == ["general", "isa"]


@pytest.mark.parametrize("stored", [None, {}])
def test_list_profiles_empty_store_returns_empty_list(tmp_path, stored):
    if stored is not None:
        (tmp_path / "credentials.json").write_text(json.dumps(stored), encoding="utf-8")
    assert list_profiles(config_dir=tmp_path) == []


def test_list_profiles_legacy_store_raises_runtime_migration_error(tmp_path):
    (tmp_path / "credentials.json").write_text(
        json.dumps({"general": {"app_key": "RK", "app_secret": "RS"}}),
        encoding="utf-8",
    )
    with pytest.raises(KISUsageError, match="kis config migrate"):
        list_profiles(config_dir=tmp_path)


# --- 스키마 판별 -----------------------------------------------------

def test_detect_schema_empty_and_legacy_and_v2():
    assert _detect_schema({}) == "legacy"                                  # 빈 -> 레거시(제로컨피그 보존)
    assert _detect_schema({"main": {"app_key": "k"}}) == "legacy"          # schema 마커 없음
    assert _detect_schema({"schema": 2, "real": {}}) == "v2"


def test_detect_schema_dict_valued_schema_is_legacy():
    # "schema" 라는 레거시 프로필 섹션(객체)은 레거시로 본다(마커가 아니다).
    assert _detect_schema({"schema": {"app_key": "k", "app_secret": "s"}}) == "legacy"


@pytest.mark.parametrize("bad", [1, 3, "2", True, 2.0])
def test_detect_schema_unknown_version_fails_closed(bad):
    with pytest.raises(KISUsageError):
        _detect_schema({"schema": bad})


# --- 형상/이름 검증 --------------------------------------------------

def test_v2_non_object_block_rejected(tmp_path):
    _write_v2(tmp_path, real="not-an-object")
    with pytest.raises(KISUsageError, match="블록"):
        resolve_credentials("general", environment="real", config_dir=tmp_path)


def test_v2_non_object_bundle_rejected(tmp_path):
    _write_v2(tmp_path, paper={"general": "not-an-object"})
    with pytest.raises(KISUsageError, match="묶음"):
        resolve_credentials("general", config_dir=tmp_path)


def test_v2_environment_field_in_bundle_rejected_on_read(tmp_path):
    _write_v2(tmp_path, paper={"general": {"app_key": "k", "app_secret": "s", "environment": "paper"}})
    with pytest.raises(KISUsageError, match="environment"):
        resolve_credentials("general", config_dir=tmp_path)


def test_v2_environment_field_in_bundle_rejected_on_save(tmp_path):
    # 묶음에 environment 필드가 있는 v2 파일로는 save 가 병합을 거부한다(저장 경로에서도 차단).
    _write_v2(tmp_path, paper={"general": {"app_key": "k", "app_secret": "s", "environment": "paper"}})
    with pytest.raises(KISUsageError, match="environment"):
        KISConfig(profile="other", app_key="AK", app_secret="SK", config_dir=tmp_path).save()


def test_v2_stray_top_level_key_rejected(tmp_path):
    _write_v2(tmp_path, paper={"general": {"app_key": "k", "app_secret": "s"}}, bogus="x")
    with pytest.raises(KISUsageError, match="알 수 없는 최상위 키"):
        resolve_credentials("general", config_dir=tmp_path)


@pytest.mark.parametrize("bad", ["real", "paper", "schema", "default_environment",
                                 "general_real", "demo_paper"])
def test_v2_reserved_and_suffixed_names_rejected(bad):
    with pytest.raises(KISUsageError):
        KISConfig(profile=bad, app_key="k", app_secret="s")


def test_legacy_file_named_paper_profile_still_requires_migrate(tmp_path):
    # 레거시(schema 마커 없음) 파일은 런타임이 해석하지 않는다 -- 'paper' 라는 프로필 이름을 열어주던
    # 옛 동작 대신 'kis config migrate' 안내 에러로 멈춘다.
    (tmp_path / "credentials.json").write_text(
        json.dumps({"paper": {"app_key": "PK", "app_secret": "PS", "environment": "paper"}}),
        encoding="utf-8")
    with pytest.raises(KISUsageError, match="migrate"):
        resolve_credentials("paper", config_dir=tmp_path)


# --- v2 해석기(프로필/환경 체인, 선택 에러, no-auto-promote) ----------

def test_v2_profile_chain_env_beats_marker_beats_first(tmp_path, monkeypatch):
    _write_v2(tmp_path, default_profile="beta",
              paper={"alpha": {"app_key": "A", "app_secret": "s"},
                     "beta": {"app_key": "B", "app_secret": "s"},
                     "gamma": {"app_key": "G", "app_secret": "s"}})
    assert resolve_credentials(config_dir=tmp_path).app_key == "B"          # 마커 beta
    monkeypatch.setenv("KIS_DEFAULT_PROFILE", "gamma")
    assert resolve_credentials(config_dir=tmp_path).app_key == "G"          # env 가 마커를 이긴다


def test_v2_profile_first_is_union_real_then_paper(tmp_path):
    _write_v2(tmp_path, real={"only_real": {"app_key": "R", "app_secret": "s"}},
              paper={"only_paper": {"app_key": "P", "app_secret": "s"}})
    # 기본 프로필 = 합집합 첫 키(real 먼저) = only_real. 기본 환경 = paper -> only_real 는 paper 에 없다.
    with pytest.raises(KISUsageError, match="only_real"):
        resolve_credentials(config_dir=tmp_path)


def test_v2_no_main_fallback(tmp_path, monkeypatch):
    # v2 에는 'main' 폴백이 없다 -- 파일이 비어있으면(그러나 v2 마커가 있으면) 기본 프로필을 못 정한다.
    _write_v2(tmp_path)   # schema 2, 블록 없음
    monkeypatch.setenv("KIS_APP_KEY", "x")   # 레거시 무접두 키는 v2 에서 무의미
    monkeypatch.setenv("KIS_APP_SECRET", "y")
    with pytest.raises(KISUsageError, match="프로필"):
        resolve_credentials(config_dir=tmp_path)


def test_v2_environment_chain(tmp_path, monkeypatch):
    _write_v2(tmp_path,
              real={"general": {"app_key": "R", "app_secret": "s"}},
              paper={"general": {"app_key": "P", "app_secret": "s"}})
    assert resolve_credentials("general", config_dir=tmp_path).environment == "paper"   # 기본 paper
    monkeypatch.setenv("KIS_DEFAULT_ENVIRONMENT", "real")
    assert resolve_credentials("general", config_dir=tmp_path).environment == "real"    # env
    assert resolve_credentials("general", environment="paper",
                               config_dir=tmp_path).environment == "paper"              # 인자가 최우선


def test_v2_default_environment_marker(tmp_path):
    _write_v2(tmp_path, default_environment="real",
              real={"general": {"app_key": "R", "app_secret": "s"}})
    assert resolve_credentials("general", config_dir=tmp_path).environment == "real"


def test_v2_other_block_hint_error_no_auto_promote(tmp_path):
    # real 에만 있는 프로필을 기본 환경(paper)으로 열면 자동승격 없이 real 을 짚어주는 에러.
    _write_v2(tmp_path, real={"pension": {"app_key": "R", "app_secret": "s"}})
    with pytest.raises(KISUsageError) as exc:
        resolve_credentials("pension", config_dir=tmp_path)
    msg = str(exc.value)
    assert "pension" in msg and "real" in msg and "environment" in msg


def test_v2_unknown_profile_lists_available(tmp_path):
    _write_v2(tmp_path, paper={"general": {"app_key": "P", "app_secret": "s"}})
    with pytest.raises(KISUsageError, match="general"):
        resolve_credentials("nope", config_dir=tmp_path)


def test_v2_no_profiles_at_all_error(tmp_path):
    _write_v2(tmp_path, real={})
    with pytest.raises(KISUsageError, match="프로필이 하나도 없다"):
        resolve_credentials("general", environment="real", config_dir=tmp_path)


def test_v2_environment_is_block_key(tmp_path):
    _write_v2(tmp_path,
              real={"general": {"app_key": "R", "app_secret": "s"}},
              paper={"general": {"app_key": "P", "app_secret": "s"}})
    assert resolve_credentials("general", environment="real", config_dir=tmp_path).app_key == "R"
    assert resolve_credentials("general", environment="paper", config_dir=tmp_path).app_key == "P"


# --- 두 축 환경변수 --------------------------------------------------

def test_v2_two_axis_env_fills_only_that_slot(tmp_path, monkeypatch):
    _write_v2(tmp_path,
              real={"general": {"app_key": "RK", "app_secret": "RS"}},
              paper={"general": {"app_key": "PK", "app_secret": "PS"}})
    monkeypatch.setenv("KIS_GENERAL_PAPER_APP_KEY", "env_paper")
    assert resolve_credentials("general", environment="paper", config_dir=tmp_path).app_key == "env_paper"
    assert resolve_credentials("general", environment="real", config_dir=tmp_path).app_key == "RK"  # 영향 없음


def test_v2_bare_legacy_env_key_ignored(tmp_path, monkeypatch):
    _write_v2(tmp_path, paper={"main": {"app_key": "PK", "app_secret": "PS"}})
    monkeypatch.setenv("KIS_APP_KEY", "bare")           # 레거시 무접두 키는 v2 에서 무시
    monkeypatch.setenv("KIS_MAIN_APP_KEY", "oneaxis")   # 한 축 키도 v2 에서 무시
    assert resolve_credentials("main", config_dir=tmp_path).app_key == "PK"


def test_v2_missing_field_message_names_two_axis_var(tmp_path):
    _write_v2(tmp_path, paper={"general": {"app_secret": "PS"}})   # app_key 없음
    with pytest.raises(KISUsageError, match="KIS_GENERAL_PAPER_APP_KEY"):
        resolve_credentials("general", config_dir=tmp_path)


# --- account_password ------------------------------------------------

def test_v2_account_password_top_level_unquoted_zero_preserved(tmp_path):
    _write_v2(tmp_path, account_password="0000",
              paper={"general": {"app_key": "k", "app_secret": "s"}})
    assert resolve_credentials("general", config_dir=tmp_path).account_password == "0000"


def test_v2_account_password_per_bundle_zero_preserved(tmp_path):
    _write_v2(tmp_path,
              paper={"general": {"app_key": "k", "app_secret": "s", "account_password": "0000"}})
    assert resolve_credentials("general", config_dir=tmp_path).account_password == "0000"


def test_v2_account_password_bool_is_none(tmp_path):
    _write_v2(tmp_path, account_password=True,
              paper={"general": {"app_key": "k", "app_secret": "s"}})
    assert resolve_credentials("general", config_dir=tmp_path).account_password is None


def test_v2_account_password_top_level_int_coerced(tmp_path):
    # v2 경로에서도 따옴표 없는 숫자 비번(JSON int)은 문자열로 강제된다(int-coercion 분기).
    _write_v2(tmp_path, account_password=1234,
              paper={"general": {"app_key": "k", "app_secret": "s"}})
    assert resolve_credentials("general", config_dir=tmp_path).account_password == "1234"


def test_v2_account_password_per_bundle_bool_is_none(tmp_path):
    # 묶음별 account_password 가 bool 이면 비번이 아니므로 None (bool 은 int 하위형이라 명시 분기).
    _write_v2(tmp_path,
              paper={"general": {"app_key": "k", "app_secret": "s", "account_password": True}})
    assert resolve_credentials("general", config_dir=tmp_path).account_password is None


def test_v2_account_password_per_bundle_beats_top_level(tmp_path):
    _write_v2(tmp_path, account_password="TOP",
              paper={"general": {"app_key": "k", "app_secret": "s", "account_password": "BUNDLE"}})
    assert resolve_credentials("general", config_dir=tmp_path).account_password == "BUNDLE"


def test_v2_account_password_two_axis_env_beats_per_bundle(tmp_path, monkeypatch):
    _write_v2(tmp_path,
              paper={"general": {"app_key": "k", "app_secret": "s", "account_password": "BUNDLE"}})
    monkeypatch.setenv("KIS_GENERAL_PAPER_ACCOUNT_PASSWORD", "ENV")
    assert resolve_credentials("general", config_dir=tmp_path).account_password == "ENV"


def test_v2_account_password_top_level_env_tier(tmp_path, monkeypatch):
    # tier 3: 두 축 env·묶음별 값 없이 최상위 KIS_ACCOUNT_PASSWORD 만 설정 -> 그 값으로 해석된다
    # (_resolve_account_password_v2 의 최상위-env 단계).
    _write_v2(tmp_path, paper={"general": {"app_key": "k", "app_secret": "s"}})
    monkeypatch.setenv("KIS_ACCOUNT_PASSWORD", "TOPENV")
    assert resolve_credentials("general", config_dir=tmp_path).account_password == "TOPENV"


# --- writer ----------------------------------------------------------

def test_v2_save_bundle_field_order(tmp_path):
    KISConfig(profile="general", app_key="AK", app_secret="SK", account="12345678-01",
              account_password="0000", environment="real", config_dir=tmp_path).save()
    bundle = _creds(tmp_path)["real"]["general"]
    assert list(bundle) == ["app_key", "app_secret", "account", "account_password"]


def test_v2_save_seeds_schema_marker(tmp_path):
    KISConfig(profile="general", app_key="AK", app_secret="SK", config_dir=tmp_path).save()
    assert _creds(tmp_path)["schema"] == 2


def test_v2_top_level_write_order(tmp_path):
    KISConfig(profile="general", app_key="AK", app_secret="SK",
              environment="paper", config_dir=tmp_path).save()
    KISConfig(profile="general", app_key="RK", app_secret="RS",
              environment="real", config_dir=tmp_path).save()
    KISConfig.set_hts_id("HTS", config_dir=tmp_path)
    KISConfig.set_default_profile("general", config_dir=tmp_path)
    KISConfig.set_default_environment("real", config_dir=tmp_path)
    keys = list(_creds(tmp_path))
    assert keys == [k for k in _V2_TOP_LEVEL_ORDER if k in keys]


def test_v2_save_preserves_sibling_block(tmp_path):
    KISConfig(profile="general", app_key="PK", app_secret="PS",
              environment="paper", config_dir=tmp_path).save()
    KISConfig(profile="derivatives", app_key="RK", app_secret="RS",
              environment="real", config_dir=tmp_path).save()
    data = _creds(tmp_path)
    assert data["paper"]["general"]["app_key"] == "PK"
    assert data["real"]["derivatives"]["app_key"] == "RK"


def test_v2_set_default_finds_profile_in_either_block(tmp_path):
    KISConfig(profile="general", app_key="RK", app_secret="RS",
              environment="real", config_dir=tmp_path).save()
    KISConfig.set_default_profile("general", config_dir=tmp_path)   # real 블록에만 있어도 OK
    assert _creds(tmp_path)["default_profile"] == "general"


def test_v2_set_default_environment_marker_and_resolution(tmp_path):
    KISConfig(profile="general", app_key="RK", app_secret="RS",
              environment="real", config_dir=tmp_path).save()
    KISConfig.set_default_environment("real", config_dir=tmp_path)
    assert _creds(tmp_path)["default_environment"] == "real"
    assert resolve_credentials("general", config_dir=tmp_path).environment == "real"


@pytest.mark.parametrize("writer", [
    lambda d: KISConfig(profile="general", app_key="AK", app_secret="SK", config_dir=d).save(),
    lambda d: KISConfig.set_default_profile("general", config_dir=d),
    lambda d: KISConfig.set_hts_id("HTS", config_dir=d),
    lambda d: KISConfig.set_default_environment("real", config_dir=d),
])
def test_v2_writers_refuse_legacy_file(tmp_path, writer):
    (tmp_path / "credentials.json").write_text(
        json.dumps({"main": {"app_key": "K", "app_secret": "S", "environment": "real"}}),
        encoding="utf-8")
    with pytest.raises(KISUsageError, match="migrate"):
        writer(tmp_path)


def test_v2_profile_exists_by_block_and_any(tmp_path):
    KISConfig(profile="general", app_key="RK", app_secret="RS",
              environment="real", config_dir=tmp_path).save()
    assert _profile_exists("general", environment="real", config_dir=tmp_path) is True
    assert _profile_exists("general", environment="paper", config_dir=tmp_path) is False
    assert _profile_exists("general", config_dir=tmp_path) is True          # 어느 블록에든
    assert _profile_exists("nope", config_dir=tmp_path) is False


# --- 레거시 파일은 런타임이 해석하지 않고 migrate 로 안내한다 ---------------

def test_legacy_file_raises_migrate_error(tmp_path):
    # 옛(평평한) 파일은 런타임이 바이트 그대로 해석하던 동작을 버리고, 변환하라는 명확한 에러로 막는다
    # (조용한 오해석/자동 real 승격 없음; 정확한 명령 'kis config migrate' 를 짚어준다).
    (tmp_path / "credentials.json").write_text(json.dumps({
        "main": {"app_key": "MK", "app_secret": "MS", "account": "12345678-01", "environment": "real"},
        "demo": {"app_key": "DK", "app_secret": "DS", "environment": "paper"},
    }), encoding="utf-8")
    for profile in ("main", "demo", None):
        with pytest.raises(KISUsageError, match="migrate"):
            resolve_credentials(profile, config_dir=tmp_path)


def test_legacy_env_vars_do_not_rescue_a_legacy_file(tmp_path, monkeypatch):
    # 옛 접두어 환경변수가 설정돼 있어도 비어있지 않은 레거시 파일은 조용히 해석되지 않고 migrate 로 막힌다.
    (tmp_path / "credentials.json").write_text(json.dumps({
        "main": {"app_key": "from_file", "app_secret": "s",
                 "account": "12345678-01", "environment": "real"},
    }), encoding="utf-8")
    monkeypatch.setenv("KIS_APP_KEY", "from_env")        # 옛 무접두 키 -- 런타임이 무시
    monkeypatch.setenv("KIS_ACCOUNT", "87654321-02")
    with pytest.raises(KISUsageError, match="migrate"):
        resolve_credentials("main", config_dir=tmp_path)


def test_legacy_file_environment_arg_does_not_bypass_migrate(tmp_path):
    # environment 인자를 줘도 레거시 파일은 열리지 않는다 -- 저장 real 을 paper 로 덮던 옛 override 없음.
    (tmp_path / "credentials.json").write_text(json.dumps({
        "main": {"app_key": "MK", "app_secret": "MS", "environment": "real"},
    }), encoding="utf-8")
    with pytest.raises(KISUsageError, match="migrate"):
        resolve_credentials("main", environment="paper", config_dir=tmp_path)


def test_v2_absent_combo_is_clean_error_not_500(tmp_path):
    # footgun 소멸: 실전키/모의서버 조립 전에 깔끔한 KISUsageError (v2 케이스).
    # 없는 (general, paper) 조합은 HTTP 500 이 아니라 다른 환경을 짚어주는 메시지로 막힌다.
    _write_v2(tmp_path, real={"general": {"app_key": "RK", "app_secret": "RS"}})
    with pytest.raises(KISUsageError, match="environment='real'"):
        resolve_credentials("general", environment="paper", config_dir=tmp_path)


def test_runtime_requires_schema2_v2_resolves_legacy_raises_empty_errors(tmp_path):
    # 세 경우를 한 자리에서 못박는다: v2 는 그대로 해석, 레거시는 migrate 안내 에러, 빈 저장소는
    # 예전처럼 깔끔한 에러(크래시/조용한 오해석 없음).
    path = tmp_path / "credentials.json"
    # (1) v2 저장소 -> 그대로 해석된다
    KISConfig(profile="general", app_key="AK", app_secret="SK", config_dir=tmp_path).save()
    assert resolve_credentials("general", config_dir=tmp_path).app_key == "AK"
    # (2) 레거시(평평한) 저장소 -> 'kis config migrate' 를 짚어주는 에러로 막힌다(real 로 자동 해석 안 함)
    path.write_text(json.dumps({"main": {"app_key": "MK", "app_secret": "MS"}}), encoding="utf-8")
    with pytest.raises(KISUsageError, match="migrate"):
        resolve_credentials("main", config_dir=tmp_path)
    # (3) 빈/없는 저장소 -> 예전처럼 깔끔한 KISUsageError(migrate 안내가 아니라 "프로필 없음")
    path.unlink()
    with pytest.raises(KISUsageError) as excinfo:
        resolve_credentials("general", config_dir=tmp_path)
    assert "migrate" not in str(excinfo.value)
