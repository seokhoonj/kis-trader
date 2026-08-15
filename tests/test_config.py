"""KISConfig -- 프로필별 자격증명/계좌/환경 해석과 KISClient.from_config 배선."""
from __future__ import annotations

import json

import pytest

from kis_trader import KISClient, KISConfig
from kis_trader.config import environment_for_profile
from kis_trader.errors import KISUsageError
from kis_trader.transport import RawResponse


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """실 사용자의 ~/.config/kis-trader 를 읽지 않도록 XDG 를 빈 임시 경로로 돌리고 KIS_* 를 지운다."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    import os
    for var in [name for name in os.environ if name.startswith("KIS_")]:
        monkeypatch.delenv(var, raising=False)


class _FakeTransport:
    """네트워크 없이 세션을 열게 하는 가짜 전송(요청 내용은 검증하지 않는다)."""
    def request(self, **kwargs):
        return RawResponse(rt_cd="0", msg_cd="", msg1="")


# --- 프로필 -> 환경 매핑 ------------------------------------------------------

@pytest.mark.parametrize("profile, environment", [
    ("main", "real"), ("paper", "paper"), ("isa", "real"),
    ("irp", "real"), ("pension", "real"),
])
def test_profile_maps_to_environment(profile, environment):
    assert KISConfig(profile=profile).environment == environment
    assert environment_for_profile(profile) == environment


def test_unknown_profile_is_rejected():
    with pytest.raises(KISUsageError):
        KISConfig(profile="bogus")  # type: ignore[arg-type]
    with pytest.raises(KISUsageError):
        environment_for_profile("bogus")


# --- 해석 순서: env -> credentials.json -> config.toml ------------------------

def test_environment_variable_is_read_for_profile(monkeypatch):
    monkeypatch.setenv("KIS_PAPER_APP_KEY", "pk")
    monkeypatch.setenv("KIS_PAPER_APP_SECRET", "ps")
    config = KISConfig(profile="paper")
    assert config.app_key() == "pk"
    assert config.app_secret() == "ps"


def test_credentials_json_read_when_env_absent(tmp_path):
    directory = tmp_path / "cfg"
    directory.mkdir()
    (directory / "credentials.json").write_text(json.dumps({
        "KIS_APP_KEY": "fk", "KIS_APP_SECRET": "fs",
        "KIS_CANO": "12345678", "KIS_ACNT_PRDT_CD": "01",
    }), encoding="utf-8")
    config = KISConfig(profile="main", config_dir_override=directory)
    assert config.app_key() == "fk"
    assert config.account() == "12345678-01"


def test_environment_wins_over_credentials_file(tmp_path, monkeypatch):
    directory = tmp_path / "cfg"
    directory.mkdir()
    (directory / "credentials.json").write_text(
        json.dumps({"KIS_APP_KEY": "from_file", "KIS_APP_SECRET": "s"}), encoding="utf-8")
    monkeypatch.setenv("KIS_APP_KEY", "from_env")
    config = KISConfig(profile="main", config_dir_override=directory)
    assert config.app_key() == "from_env"  # 환경변수가 파일보다 우선


def test_config_toml_is_last_fallback(tmp_path):
    directory = tmp_path / "cfg"
    directory.mkdir()
    (directory / "config.toml").write_text(
        'KIS_APP_KEY = "tk"\nKIS_APP_SECRET = "ts"\n', encoding="utf-8")
    config = KISConfig(profile="main", config_dir_override=directory)
    assert config.app_key() == "tk"


def test_resolver_hook_overrides_env_and_files(monkeypatch):
    monkeypatch.setenv("KIS_APP_KEY", "env_key")
    seen = {}
    def resolver(name):
        seen[name] = seen.get(name, 0) + 1
        return {"KIS_APP_KEY": "host_key", "KIS_APP_SECRET": "host_secret"}.get(name)
    config = KISConfig(profile="main", resolver=resolver)
    assert config.app_key() == "host_key"  # resolver 가 환경변수보다 우선
    assert "KIS_APP_KEY" in seen


# --- 계좌 조립 ---------------------------------------------------------------

def test_account_none_when_absent():
    assert KISConfig(profile="main").account() is None


def test_account_cano_without_product_code_fails_closed(monkeypatch):
    monkeypatch.setenv("KIS_CANO", "12345678")  # 상품코드 없음
    with pytest.raises(KISUsageError):
        KISConfig(profile="main").account()


# --- 누락 시 예외엔 값이 아니라 변수 이름만 --------------------------------------

def test_missing_secret_names_the_variable_not_a_value():
    with pytest.raises(KISUsageError) as excinfo:
        KISConfig(profile="paper").app_key()
    assert "KIS_PAPER_APP_KEY" in str(excinfo.value)


# --- 캐시/설정 디렉터리 ------------------------------------------------------

def test_config_dir_override_redirects_files_and_cache(tmp_path):
    config = KISConfig(profile="main", config_dir_override=tmp_path)
    assert config.config_dir() == tmp_path
    assert config.cache_dir() == tmp_path / "tokens"


def test_default_dirs_follow_xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "k"))
    config = KISConfig(profile="main")
    assert config.config_dir() == tmp_path / "c" / "kis-trader"
    assert config.cache_dir() == tmp_path / "k" / "kis-trader" / "tokens"


# --- KISClient.from_config ---------------------------------------------------

def test_from_config_opens_session_with_resolved_values(monkeypatch):
    monkeypatch.setenv("KIS_APP_KEY", "k")
    monkeypatch.setenv("KIS_APP_SECRET", "s")
    monkeypatch.setenv("KIS_CANO", "12345678")
    monkeypatch.setenv("KIS_ACNT_PRDT_CD", "01")
    kis = KISClient.from_config(KISConfig(profile="main"), transport=_FakeTransport())
    assert kis.environment == "real"


def test_from_config_paper_profile_uses_paper_environment(monkeypatch):
    monkeypatch.setenv("KIS_PAPER_APP_KEY", "k")
    monkeypatch.setenv("KIS_PAPER_APP_SECRET", "s")
    kis = KISClient.from_config(KISConfig(profile="paper"), transport=_FakeTransport())
    assert kis.environment == "paper"


def test_from_config_account_override_wins(monkeypatch):
    monkeypatch.setenv("KIS_APP_KEY", "k")
    monkeypatch.setenv("KIS_APP_SECRET", "s")
    monkeypatch.setenv("KIS_CANO", "12345678")
    monkeypatch.setenv("KIS_ACNT_PRDT_CD", "01")
    kis = KISClient.from_config(
        KISConfig(profile="main"), account="87654321-02", transport=_FakeTransport())
    assert kis.account == "87654321-02"


@pytest.mark.parametrize("reserved", ["app_key", "app_secret", "environment", "token_cache_dir"])
def test_from_config_rejects_profile_owned_kwargs(monkeypatch, reserved):
    monkeypatch.setenv("KIS_APP_KEY", "k")
    monkeypatch.setenv("KIS_APP_SECRET", "s")
    with pytest.raises(KISUsageError):
        KISClient.from_config(KISConfig(profile="main"), **{reserved: "x"})


def test_from_config_missing_credentials_raises_usage_error():
    with pytest.raises(KISUsageError):
        KISClient.from_config(KISConfig(profile="main"), transport=_FakeTransport())


# --- fail-closed: 형상/빈값 오류 (P1 회귀 방지) --------------------------------

@pytest.mark.parametrize(("filename", "contents"), [
    ("credentials.json", "{"),        # 깨진 JSON
    ("credentials.json", "[]"),       # 최상위가 객체가 아님
    ("config.toml", "invalid = ["),   # 깨진 TOML
])
def test_malformed_config_file_raises(tmp_path, filename, contents):
    (tmp_path / filename).write_text(contents, encoding="utf-8")
    with pytest.raises(KISUsageError):
        KISConfig(profile="main", config_dir_override=tmp_path).app_key()


@pytest.mark.parametrize("filename", ["credentials.json", "config.toml"])
def test_unreadable_config_file_raises(tmp_path, filename):
    (tmp_path / filename).mkdir()  # 파일 자리에 디렉터리 -> OSError
    with pytest.raises(KISUsageError):
        KISConfig(profile="main", config_dir_override=tmp_path).app_key()


def test_toml_boolean_value_is_not_stringified_into_garbage(tmp_path):
    # 무검증 str() 이면 true -> "True" 라는 쓰레기 시크릿이 됐다 -- 형상 오류로 닫아야 한다.
    (tmp_path / "config.toml").write_text('KIS_APP_KEY = true\n', encoding="utf-8")
    with pytest.raises(KISUsageError):
        KISConfig(profile="main", config_dir_override=tmp_path).app_key()


def test_json_nonscalar_value_raises(tmp_path):
    (tmp_path / "credentials.json").write_text(
        json.dumps({"KIS_APP_KEY": {"nested": 1}}), encoding="utf-8")
    with pytest.raises(KISUsageError):
        KISConfig(profile="main", config_dir_override=tmp_path).app_key()


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_required_value_is_absent(monkeypatch, blank):
    monkeypatch.setenv("KIS_APP_KEY", blank)  # 빈/공백 시크릿은 유효하지 않다
    with pytest.raises(KISUsageError):
        KISConfig(profile="main").app_key()


def test_blank_cano_is_treated_as_absent(monkeypatch):
    monkeypatch.setenv("KIS_CANO", "")
    monkeypatch.setenv("KIS_ACNT_PRDT_CD", "01")
    assert KISConfig(profile="main").account() is None


def test_blank_product_code_with_cano_fails_closed(monkeypatch):
    monkeypatch.setenv("KIS_CANO", "12345678")
    monkeypatch.setenv("KIS_ACNT_PRDT_CD", "   ")
    with pytest.raises(KISUsageError):
        KISConfig(profile="main").account()


# --- 해석 순서·resolver 계약 --------------------------------------------------

def test_credentials_json_wins_over_config_toml(tmp_path):
    (tmp_path / "credentials.json").write_text(
        json.dumps({"KIS_APP_KEY": "json_key"}), encoding="utf-8")
    (tmp_path / "config.toml").write_text('KIS_APP_KEY = "toml_key"\n', encoding="utf-8")
    assert KISConfig(profile="main", config_dir_override=tmp_path).app_key() == "json_key"


def test_resolver_returning_none_does_not_fall_back(monkeypatch):
    monkeypatch.setenv("KIS_APP_KEY", "env_key")  # resolver 가 전권 -> env 로 폴백하지 않는다
    config = KISConfig(profile="main", resolver=lambda name: None)
    with pytest.raises(KISUsageError):
        config.app_key()


def test_resolver_is_excluded_from_repr():
    # resolver 가 바인딩된 시크릿을 품을 수 있으므로 repr 에서 제외한다.
    config = KISConfig(profile="main", resolver=lambda name: "x")
    assert "resolver" not in repr(config)
