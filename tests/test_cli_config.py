"""``kis config`` -- 두 축(프로필 x 환경) 자격증명 설정 명령 그룹.

``setup``(대화형 저장) / ``show``(마스킹 조회) / ``set``(한 필드 수정) / ``default`` /
``set-hts-id`` / ``migrate``(레거시 -> schema 2 변환). 시크릿은 getpass 전용(플래그 금지), 비시크릿은
플래그 또는 프롬프트, 저장은 :meth:`KISConfig.save` 재사용. 이 그룹은 클라이언트를 만들지 않는다.
"""
from __future__ import annotations

import json
import os

import pytest

import kis_trader.cli.app as cli_main
import kis_trader.cli.commands.credentials as cred
from kis_trader.config import resolve_credentials


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """실 ``~/.config/kis-trader`` 를 건드리지 않게 XDG 를 임시 디렉터리로 돌리고 ``KIS_*`` 를 지운다."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    for var in [name for name in os.environ if name.startswith("KIS_")]:
        monkeypatch.delenv(var, raising=False)
    return tmp_path


def _cfg_dir(tmp_path):
    return tmp_path / "config" / "kis-trader"


def _creds(tmp_path):
    return json.loads((_cfg_dir(tmp_path) / "credentials.json").read_text())


def _write_raw(tmp_path, data):
    directory = _cfg_dir(tmp_path)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "credentials.json").write_text(json.dumps(data, ensure_ascii=False))


def _getpass(monkeypatch, mapping, default=""):
    """``getpass`` 를 프롬프트 문구의 부분 문자열로 분기해 답한다(호출 순서 무관)."""
    def _g(prompt=""):
        for needle, value in mapping.items():
            if needle in prompt:
                return value
        return default
    monkeypatch.setattr("kis_trader.cli.commands.credentials.getpass", _g)


def _answer(monkeypatch, mapping, default=""):
    """``input()`` 을 프롬프트 문구의 부분 문자열로 분기해 답한다(호출 순서 무관)."""
    def _input(prompt=""):
        for needle, value in mapping.items():
            if needle in prompt:
                return value
        return default
    monkeypatch.setattr("builtins.input", _input)


def _no_client(args):
    raise AssertionError("kis config 는 클라이언트를 만들면 안 된다")


# --- 그룹 구조 ----------------------------------------------------------------

def test_bare_config_routes_to_setup_and_needs_no_client():
    args = cli_main.build_parser().parse_args(["config"])
    assert args.func is cred.cmd_config_setup
    assert args.needs_client is False


@pytest.mark.parametrize("argv,func", [
    (["config", "setup"], cred.cmd_config_setup),
    (["config", "show"], cred.cmd_config_show),
    (["config", "set", "general.paper.account", "1"], cred.cmd_config_set),
    (["config", "default"], cred.cmd_config_default),
    (["config", "default", "profile", "general"], cred.cmd_config_default_profile),
    (["config", "default", "environment", "real"], cred.cmd_config_default_environment),
    (["config", "default", "env", "paper"], cred.cmd_config_default_environment),
    (["config", "set-hts-id", "myid"], cred.cmd_config_set_hts_id),
    (["config", "migrate"], cred.cmd_config_migrate),
])
def test_config_leaves_wire_to_handlers_without_client(argv, func):
    args = cli_main.build_parser().parse_args(argv)
    assert args.func is func
    assert args.needs_client is False


# --- setup --------------------------------------------------------------------

def test_setup_writes_paper_only_when_real_declined(tmp_path, monkeypatch):
    monkeypatch.setattr(cli_main, "build_client", _no_client)
    _getpass(monkeypatch, {"paper APP KEY": "pk", "paper APP SECRET": "ps"})  # paper account_password -> ""
    _answer(monkeypatch, {"실전": "n", "account 계좌번호": "50001234-01"})

    code = cli_main.main(["config", "setup", "--profile", "demo"])

    assert code == 0
    data = _creds(tmp_path)
    assert "real" not in data                       # real 거부 -> 블록 없음
    entry = data["paper"]["demo"]
    assert entry == {"app_key": "pk", "app_secret": "ps", "account": "50001234-01"}
    assert "environment" not in entry               # 환경은 바깥 블록 키로만


def test_setup_writes_both_real_and_paper(tmp_path, monkeypatch):
    monkeypatch.setattr(cli_main, "build_client", _no_client)
    _getpass(monkeypatch, {"real APP KEY": "rk", "real APP SECRET": "rs",
                           "paper APP KEY": "pk", "paper APP SECRET": "ps"})
    _answer(monkeypatch, {"실전": "y", "real account 계좌번호": "12345678-01",
                          "paper account 계좌번호": "50001234-01"})

    code = cli_main.main(["config", "setup", "--profile", "demo"])

    assert code == 0
    data = _creds(tmp_path)
    assert data["real"]["demo"]["app_key"] == "rk"
    assert data["real"]["demo"]["account"] == "12345678-01"
    assert data["paper"]["demo"]["app_key"] == "pk"
    assert data["paper"]["demo"]["account"] == "50001234-01"


def test_setup_stores_account_password_per_bundle(tmp_path, monkeypatch):
    monkeypatch.setattr(cli_main, "build_client", _no_client)
    _getpass(monkeypatch, {"real APP KEY": "rk", "real APP SECRET": "rs",
                           "real account_password": "1111"})
    _answer(monkeypatch, {"실전": "y", "account 계좌번호": "12345678-22"})

    assert cli_main.main(["config", "setup", "--profile", "pension"]) == 0
    assert _creds(tmp_path)["real"]["pension"]["account_password"] == "1111"


def test_setup_soft_hints_paper_for_pension_but_still_prompts(tmp_path, monkeypatch, capsys):
    # paper 미지원 추정은 하드 게이트가 아니라 힌트다 -- 프롬프트는 그대로 띄우고, 사용자가 APP KEY 를
    # 비우고 Enter 로 건너뛴다(여기선 빈 입력 -> paper 블록 없음). 힌트 문구와 실제 프롬프트 둘 다 나온다.
    monkeypatch.setattr(cli_main, "build_client", _no_client)
    seen = []

    def _g(prompt=""):
        seen.append(prompt)
        return {"real APP KEY": "rk", "real APP SECRET": "rs"}.get(
            next((n for n in ("real APP KEY", "real APP SECRET") if n in prompt), ""), "")
    monkeypatch.setattr("kis_trader.cli.commands.credentials.getpass", _g)
    _answer(monkeypatch, {"실전": "y", "account 계좌번호": "87654321-22"})

    code = cli_main.main(["config", "setup", "--profile", "pension"])

    assert code == 0
    data = _creds(tmp_path)
    assert "pension" in data["real"]
    assert "paper" not in data                       # paper APP KEY 빈 입력 -> 블록 없음
    assert any("paper APP KEY" in p for p in seen)   # 하드 생략 아님: paper 프롬프트는 떴다
    out = capsys.readouterr().out
    assert "모의투자가 없는 것이 보통입니다" in out      # 힌트는 보여준다
    assert "모의투자에 없어 건너뜁니다" not in out       # 옛 하드 생략 문구는 없다


def test_setup_real_affirmation_required(tmp_path, monkeypatch):
    # real 거부 + paper APP KEY 미입력 -> 저장된 것이 없어 중단(130/3), 파일 없음.
    monkeypatch.setattr(cli_main, "build_client", _no_client)
    _getpass(monkeypatch, {}, default="")            # 모든 getpass 빈 입력
    _answer(monkeypatch, {"실전": "n"}, default="")

    code = cli_main.main(["config", "setup", "--profile", "demo"])

    assert code == 3                                 # CliAborted("저장된 자격증명이 없습니다.")
    assert not (_cfg_dir(tmp_path) / "credentials.json").exists()


def test_setup_eoferror_aborts_cleanly_without_writing(tmp_path, monkeypatch):
    monkeypatch.setattr(cli_main, "build_client", _no_client)

    def _eof(prompt=""):
        raise EOFError
    monkeypatch.setattr("builtins.input", _eof)
    monkeypatch.setattr("kis_trader.cli.commands.credentials.getpass", _eof)

    code = cli_main.main(["config", "setup", "--profile", "demo"])

    assert code == 130
    assert not (_cfg_dir(tmp_path) / "credentials.json").exists()


def test_setup_rejects_invalid_profile_name_before_secrets(tmp_path, monkeypatch):
    # '_paper' 접미사는 v2 이름 규칙 위반 -> 시크릿 전에 거부(getpass 호출되면 실패).
    monkeypatch.setattr(cli_main, "build_client", _no_client)
    _answer(monkeypatch, {})
    monkeypatch.setattr("kis_trader.cli.commands.credentials.getpass",
                        lambda prompt="": pytest.fail("이름 검증이 시크릿보다 먼저여야 한다"))

    code = cli_main.main(["config", "setup", "--profile", "general_paper"])

    assert code == 4                                 # KISUsageError -> not_sent
    assert not (_cfg_dir(tmp_path) / "credentials.json").exists()


def test_setup_tells_migrate_first_on_legacy_file(tmp_path, monkeypatch, capsys):
    # 레거시 파일이면 시크릿을 받기 전에 migrate 안내(getpass 호출되면 실패).
    monkeypatch.setattr(cli_main, "build_client", _no_client)
    _write_raw(tmp_path, {"main": {"app_key": "k", "app_secret": "s", "environment": "real"}})
    _answer(monkeypatch, {})
    monkeypatch.setattr("kis_trader.cli.commands.credentials.getpass",
                        lambda prompt="": pytest.fail("레거시 안내가 시크릿보다 먼저여야 한다"))

    code = cli_main.main(["config", "setup", "--profile", "general"])

    assert code == 3                                 # CliAborted
    assert "migrate" in capsys.readouterr().err


def test_setup_overwrite_guard_is_slot_accurate(tmp_path, monkeypatch):
    # real.demo 를 미리 저장한 뒤 setup: real 덮어쓰기 거부 -> real 유지, paper.demo 는 신규 저장.
    _write_raw(tmp_path, {"schema": 2,
                          "real": {"demo": {"app_key": "OLD_RK", "app_secret": "OLD_RS",
                                            "account": "12345678-01"}}})
    monkeypatch.setattr(cli_main, "build_client", _no_client)
    _getpass(monkeypatch, {"paper APP KEY": "new_pk", "paper APP SECRET": "new_ps"})
    _answer(monkeypatch, {"실전": "y", "덮어쓸까요": "n", "paper account 계좌번호": "50001234-01"})

    code = cli_main.main(["config", "setup", "--profile", "demo"])

    assert code == 0
    data = _creds(tmp_path)
    assert data["real"]["demo"]["app_key"] == "OLD_RK"   # real 슬롯 보존(덮어쓰기 거부)
    assert data["paper"]["demo"]["app_key"] == "new_pk"  # paper 슬롯만 신규


def test_setup_never_echoes_secret_even_when_save_fails(tmp_path, monkeypatch, capsys):
    from kis_trader.errors import KISError

    monkeypatch.setattr(cli_main, "build_client", _no_client)
    _getpass(monkeypatch, {"paper APP KEY": "SECRETKEY", "paper APP SECRET": "SECRETVAL"})
    _answer(monkeypatch, {"실전": "n", "account 계좌번호": "50001234-01"})

    def _fail_save(self):
        raise KISError("자격증명 쓰기 실패")
    monkeypatch.setattr("kis_trader.cli.commands.credentials.KISConfig.save", _fail_save)

    code = cli_main.main(["config", "setup", "--profile", "demo"])

    out, err = capsys.readouterr()
    assert code != 0
    assert "SECRETKEY" not in out and "SECRETKEY" not in err
    assert "SECRETVAL" not in out and "SECRETVAL" not in err


def test_setup_summary_never_echoes_the_secret(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli_main, "build_client", _no_client)
    _getpass(monkeypatch, {"paper APP KEY": "SECRETKEY", "paper APP SECRET": "SECRETVAL"})
    _answer(monkeypatch, {"실전": "n", "account 계좌번호": "50001234-01"})

    code = cli_main.main(["config", "setup", "--profile", "demo"])

    assert code == 0
    out = capsys.readouterr().out
    assert "paper" in out and "50001234-01" in out
    assert "SECRETKEY" not in out and "SECRETVAL" not in out


def test_setup_set_default_marks_default_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(cli_main, "build_client", _no_client)
    _getpass(monkeypatch, {"paper APP KEY": "pk", "paper APP SECRET": "ps"})
    _answer(monkeypatch, {"실전": "n", "account 계좌번호": "50001234-01"})

    code = cli_main.main(["config", "setup", "--profile", "demo", "--set-default"])

    assert code == 0
    assert _creds(tmp_path)["default_profile"] == "demo"


def test_setup_rejects_secret_flags():
    for flag in ("--app-key", "--app-secret"):
        with pytest.raises(SystemExit) as exc:
            cli_main.main(["config", "setup", flag, "leak"])
        assert exc.value.code == 2                   # argparse usage error


def test_setup_overwrite_approved_replaces_slot(tmp_path, monkeypatch):
    # real.demo 를 미리 저장한 뒤 setup: 덮어쓰기 'y' -> 그 슬롯이 새 값으로 바뀐다.
    _write_raw(tmp_path, {"schema": 2,
                          "real": {"demo": {"app_key": "OLD_RK", "app_secret": "OLD_RS",
                                            "account": "12345678-01"}}})
    monkeypatch.setattr(cli_main, "build_client", _no_client)
    _getpass(monkeypatch, {"real APP KEY": "NEW_RK", "real APP SECRET": "NEW_RS"})
    _answer(monkeypatch, {"실전": "y", "덮어쓸까요": "y", "real account 계좌번호": "99998888-02"})

    code = cli_main.main(["config", "setup", "--profile", "demo"])

    assert code == 0
    bundle = _creds(tmp_path)["real"]["demo"]
    assert bundle["app_key"] == "NEW_RK"             # 덮어쓰기 승인 -> 새 값
    assert bundle["account"] == "99998888-02"


def test_setup_prompts_for_profile_name_when_omitted(tmp_path, monkeypatch):
    # --profile 생략 -> 이름을 프롬프트, 빈 입력이면 기본 'general' 로 저장된다.
    monkeypatch.setattr(cli_main, "build_client", _no_client)
    _getpass(monkeypatch, {"paper APP KEY": "pk", "paper APP SECRET": "ps"})
    _answer(monkeypatch, {"profile 계좌 종류": "", "실전": "n", "account 계좌번호": "50001234-01"})

    code = cli_main.main(["config", "setup"])

    assert code == 0
    assert "general" in _creds(tmp_path)["paper"]


def test_setup_quote_only_profile_omits_account_and_notes_quote_only(tmp_path, monkeypatch, capsys):
    # 계좌 빈 입력(시세전용) -> 묶음에 account 키 없음 + 요약에 '시세전용' 주석.
    monkeypatch.setattr(cli_main, "build_client", _no_client)
    _getpass(monkeypatch, {"paper APP KEY": "pk", "paper APP SECRET": "ps"})
    _answer(monkeypatch, {"실전": "n", "account 계좌번호": ""})

    code = cli_main.main(["config", "setup", "--profile", "demo"])

    assert code == 0
    assert "account" not in _creds(tmp_path)["paper"]["demo"]
    assert "시세전용" in capsys.readouterr().out


def test_setup_tells_migrate_first_on_meta_only_legacy_file(tmp_path, monkeypatch, capsys):
    # 스키마 없는 메타 전용 레거시 파일({"hts_id": ...})도 시크릿 전에 migrate 안내로 막는다
    # (가드와 writer 가 어긋나 시크릿 입력 후 저장 거부되는 막다른 길 방지).
    monkeypatch.setattr(cli_main, "build_client", _no_client)
    _write_raw(tmp_path, {"hts_id": "x"})
    _answer(monkeypatch, {})
    monkeypatch.setattr("kis_trader.cli.commands.credentials.getpass",
                        lambda prompt="": pytest.fail("레거시 안내가 시크릿보다 먼저여야 한다"))

    code = cli_main.main(["config", "setup", "--profile", "general"])

    assert code == 3                                 # CliAborted
    assert "migrate" in capsys.readouterr().err


def test_setup_then_resolve_round_trip(tmp_path, monkeypatch):
    # setup 으로 real/paper 슬롯을 저장한 뒤 각 환경을 해석하면 넣은 키·계좌가 그대로 돌아와야 한다
    # (setup -> 저장 -> resolve 봉합; 환경이 슬롯을 정확히 인덱싱하는지).
    monkeypatch.setattr(cli_main, "build_client", _no_client)
    _getpass(monkeypatch, {"real APP KEY": "RK", "real APP SECRET": "RS",
                           "paper APP KEY": "PK", "paper APP SECRET": "PS"})
    _answer(monkeypatch, {"실전": "y", "real account 계좌번호": "11111111-01",
                          "paper account 계좌번호": "22222222-02"})

    assert cli_main.main(["config", "setup", "--profile", "general"]) == 0

    real = resolve_credentials("general", environment="real", config_dir=_cfg_dir(tmp_path))
    assert (real.app_key, real.app_secret, real.account) == ("RK", "RS", "11111111-01")
    paper = resolve_credentials("general", environment="paper", config_dir=_cfg_dir(tmp_path))
    assert (paper.app_key, paper.app_secret, paper.account) == ("PK", "PS", "22222222-02")


# --- show ---------------------------------------------------------------------

def test_show_masks_app_key_to_last_four(tmp_path, monkeypatch, capsys):
    _write_raw(tmp_path, {"schema": 2, "account_password": "0000",
                          "real": {"general": {"app_key": "SUPERSECRETKEY9999",
                                               "app_secret": "SECRET", "account": "12345678-01",
                                               "account_password": "1111"}}})
    cli_main.main(["config", "show"])

    out = capsys.readouterr().out
    assert "****9999" in out                         # 끝 4자리만
    assert "SUPERSECRETKEY9999" not in out           # 전체 앱키 비노출
    assert "SECRET" not in out                       # 시크릿 비노출
    assert "account_password: (설정됨)" in out        # 최상위 비번은 값 대신 (설정됨)
    assert "account_password (설정됨)" in out          # 묶음별 비번도 값 대신 (설정됨)
    assert "1111" not in out


def test_show_legacy_banner_points_to_migrate(tmp_path, capsys):
    _write_raw(tmp_path, {"main": {"app_key": "LEGACYKEY1234", "app_secret": "s",
                                   "account": "12345678-01", "environment": "real"}})
    cli_main.main(["config", "show"])

    out = capsys.readouterr().out
    assert "레거시 형식" in out and "migrate" in out
    assert "****1234" in out and "LEGACYKEY1234" not in out


def test_show_reports_empty_config(tmp_path, capsys):
    cli_main.main(["config", "show"])
    assert "설정된 자격증명이 없습니다" in capsys.readouterr().out


# --- set ----------------------------------------------------------------------

def _seed_v2(tmp_path):
    _write_raw(tmp_path, {"schema": 2,
                          "real": {"general": {"app_key": "RK", "app_secret": "RS",
                                               "account": "12345678-01"}},
                          "paper": {"general": {"app_key": "PK", "app_secret": "PS",
                                                "account": "50001234-01"}}})


def test_set_rejects_secret_value_in_argv(tmp_path, monkeypatch):
    _seed_v2(tmp_path)
    monkeypatch.setattr("kis_trader.cli.commands.credentials.getpass",
                        lambda prompt="": pytest.fail("시크릿은 argv 를 거부해야 한다(getpass 미호출)"))

    code = cli_main.main(["config", "set", "general.paper.app_key", "LEAKED_ON_CLI"])

    assert code == 3                                 # CliAborted
    assert _creds(tmp_path)["paper"]["general"]["app_key"] == "PK"   # 변경 없음


def test_set_app_key_via_getpass_preserves_other_fields(tmp_path, monkeypatch):
    _seed_v2(tmp_path)
    _getpass(monkeypatch, {"general.paper.app_key": "NEWKEY"})

    code = cli_main.main(["config", "set", "general.paper.app_key"])

    assert code == 0
    bundle = _creds(tmp_path)["paper"]["general"]
    assert bundle["app_key"] == "NEWKEY"
    assert bundle["app_secret"] == "PS"              # 나머지 필드 보존
    assert bundle["account"] == "50001234-01"


def test_set_account_accepts_argv_value(tmp_path):
    _seed_v2(tmp_path)
    code = cli_main.main(["config", "set", "general.real.account", "99998888-02"])
    assert code == 0
    assert _creds(tmp_path)["real"]["general"]["account"] == "99998888-02"


def test_set_rejects_malformed_address(tmp_path):
    _seed_v2(tmp_path)
    assert cli_main.main(["config", "set", "general.paper"]) == 3


def test_set_rejects_unknown_field(tmp_path):
    _seed_v2(tmp_path)
    assert cli_main.main(["config", "set", "general.paper.secret_sauce", "x"]) == 3


def test_set_on_missing_slot_errors(tmp_path, monkeypatch):
    _seed_v2(tmp_path)
    _getpass(monkeypatch, {"absent.real.app_key": "K"})
    code = cli_main.main(["config", "set", "absent.real.app_key"])
    assert code == 3                                 # app_secret 없는 슬롯 -> 안내


def test_set_tells_migrate_first_on_legacy_file(tmp_path, monkeypatch):
    _write_raw(tmp_path, {"main": {"app_key": "k", "app_secret": "s", "environment": "real"}})
    monkeypatch.setattr("kis_trader.cli.commands.credentials.getpass",
                        lambda prompt="": pytest.fail("레거시면 migrate 안내가 먼저여야 한다"))
    assert cli_main.main(["config", "set", "main.real.app_key"]) == 3


def test_set_rejects_invalid_environment(tmp_path, monkeypatch):
    _seed_v2(tmp_path)
    monkeypatch.setattr("kis_trader.cli.commands.credentials.getpass",
                        lambda prompt="": pytest.fail("환경 검증이 시크릿보다 먼저여야 한다"))
    assert cli_main.main(["config", "set", "general.badenv.app_key"]) == 3   # CliAborted


def test_set_account_password_via_getpass(tmp_path, monkeypatch):
    _seed_v2(tmp_path)
    _getpass(monkeypatch, {"general.real.account_password": "4321"})
    code = cli_main.main(["config", "set", "general.real.account_password"])
    assert code == 0
    assert _creds(tmp_path)["real"]["general"]["account_password"] == "4321"


def test_set_clears_account_with_empty_input(tmp_path, monkeypatch):
    _seed_v2(tmp_path)
    _answer(monkeypatch, {"general.real.account": ""})   # 빈 입력 -> account 제거
    code = cli_main.main(["config", "set", "general.real.account"])
    assert code == 0
    bundle = _creds(tmp_path)["real"]["general"]
    assert "account" not in bundle                        # 비운 필드는 묶음에서 사라진다
    assert bundle["app_key"] == "RK"                      # 나머지 보존


def test_mask_short_key_full_mask_and_absent():
    assert cred._mask("abcde") == "****bcde"              # >4: 끝 4자리만
    assert cred._mask("abcd") == "****"                   # <=4: 전부 가림
    assert cred._mask(None) == "(없음)"                   # 없는 값
    assert cred._mask("") == "(없음)"                     # 빈 값


# --- default (기본 프로필/환경) / set-hts-id ----------------------------------

def test_default_profile_marks_existing_profile(tmp_path):
    _seed_v2(tmp_path)
    assert cli_main.main(["config", "default", "profile", "general"]) == 0
    assert _creds(tmp_path)["default_profile"] == "general"


def test_default_profile_rejects_unknown_profile(tmp_path):
    _seed_v2(tmp_path)
    assert cli_main.main(["config", "default", "profile", "ghost"]) == 4   # KISUsageError


def test_default_environment_writes_marker(tmp_path):
    _seed_v2(tmp_path)
    assert cli_main.main(["config", "default", "environment", "real"]) == 0
    assert _creds(tmp_path)["default_environment"] == "real"


def test_default_environment_env_alias_writes_marker(tmp_path):
    _seed_v2(tmp_path)
    assert cli_main.main(["config", "default", "env", "paper"]) == 0
    assert _creds(tmp_path)["default_environment"] == "paper"


def test_default_environment_rejects_invalid_value(tmp_path):
    _seed_v2(tmp_path)
    # argparse choices 가 real/paper 외 값을 거부(비정상 종료).
    with pytest.raises(SystemExit):
        cli_main.build_parser().parse_args(["config", "default", "environment", "demo"])


def test_default_bare_shows_current_defaults(tmp_path, capsys):
    _seed_v2(tmp_path)
    cli_main.main(["config", "default", "environment", "real"])
    cli_main.main(["config", "default", "profile", "general"])
    capsys.readouterr()                      # setter 출력을 비워 bare 조회 출력만 단언한다
    assert cli_main.main(["config", "default"]) == 0
    out = capsys.readouterr().out
    assert "기본 프로필: general" in out      # bare 조회만 찍는 라인 -- setter 출력과 안 섞인다
    assert "기본 환경" in out and "real" in out


def test_default_bare_on_legacy_store_points_to_migrate(tmp_path, capsys):
    _write_raw(tmp_path, {"general": {"app_key": "k", "app_secret": "s", "environment": "real"}})
    assert cli_main.main(["config", "default"]) == 0
    out = capsys.readouterr().out
    assert "레거시" in out and "migrate" in out     # v2 마커 폴백을 "기본값"처럼 보이지 않게


def test_set_hts_id_writes_top_level(tmp_path):
    _seed_v2(tmp_path)
    assert cli_main.main(["config", "set-hts-id", "myhtsid"]) == 0
    assert _creds(tmp_path)["hts_id"] == "myhtsid"


# --- migrate (레거시 -> schema 2) ---------------------------------------------

def _legacy(tmp_path, **extra):
    data = {"hts_id": "myhts", "account_password": "0000",
            "main": {"app_key": "MAINKEY1234", "app_secret": "MAINSEC",
                     "account": "12345678-01", "environment": "real"},
            "paper": {"app_key": "PAPERKEY5678", "app_secret": "PAPERSEC",
                      "account": "50001234-01", "environment": "paper"}}
    data.update(extra)
    _write_raw(tmp_path, data)


def test_migrate_yes_merges_main_and_paper_into_general(tmp_path):
    _legacy(tmp_path)
    assert cli_main.main(["config", "migrate", "--yes"]) == 0
    data = _creds(tmp_path)
    assert data["schema"] == 2
    assert data["real"]["general"]["app_key"] == "MAINKEY1234"
    assert data["paper"]["general"]["app_key"] == "PAPERKEY5678"
    assert "main" not in data and "paper" not in data.get("real", {})
    assert "environment" not in data["real"]["general"]   # 묶음에 environment 필드 없음


def test_migrate_writes_default_markers_explicitly(tmp_path):
    _legacy(tmp_path)
    cli_main.main(["config", "migrate", "--yes"])
    data = _creds(tmp_path)
    assert data["default_environment"] == "paper"     # fail-safe 기본 명시
    assert data["default_profile"] == "general"
    assert data["hts_id"] == "myhts"                  # 공유 최상위 이동
    assert data["account_password"] == "0000"         # 선행 0 보존(문자열)


def test_migrate_then_resolve_round_trip(tmp_path):
    # 레거시 main(real)+paper(paper) 를 migrate 로 general 에 합친 뒤, 각 환경을 해석하면 원래 레거시
    # 자격증명이 그대로 돌아와야 한다(legacy -> migrate -> v2 -> resolve; migrate 가 다른 writer 와 달리
    # 왕복 테스트가 없던 유일 writer 라 여기서 닫는다).
    _legacy(tmp_path)
    assert cli_main.main(["config", "migrate", "--yes"]) == 0

    real = resolve_credentials("general", environment="real", config_dir=_cfg_dir(tmp_path))
    assert real.app_key == "MAINKEY1234"            # 구 main(real) 자격증명
    assert real.account == "12345678-01"
    paper = resolve_credentials("general", environment="paper", config_dir=_cfg_dir(tmp_path))
    assert paper.app_key == "PAPERKEY5678"          # 구 paper(paper) 자격증명
    assert paper.account == "50001234-01"


def test_migrate_interactive_merge_yes(tmp_path, monkeypatch):
    _legacy(tmp_path)
    _answer(monkeypatch, {"합칠까요": "y", "합친 프로필 이름": "general"})
    assert cli_main.main(["config", "migrate"]) == 0
    assert "general" in _creds(tmp_path)["real"]


def test_migrate_interactive_merge_declined_renames_paper(tmp_path, monkeypatch):
    # 병합 거부 -> main(real) 은 자기 이름 유지, 'paper'(두 축 예약어) 는 개명을 묻는다.
    _legacy(tmp_path)
    _answer(monkeypatch, {"합칠까요": "n", "새 이름": "sim"})
    assert cli_main.main(["config", "migrate"]) == 0
    data = _creds(tmp_path)
    assert data["real"]["main"]["app_key"] == "MAINKEY1234"
    assert data["paper"]["sim"]["app_key"] == "PAPERKEY5678"


def test_migrate_yes_refuses_when_rename_needed(tmp_path):
    # 'paper' 만 있고 main 이 없으면 병합 분기 없음 -> 'paper' 는 개명 필요한데 --yes 는 못 물어본다.
    _write_raw(tmp_path, {"paper": {"app_key": "K", "app_secret": "S",
                                    "account": "50001234-01", "environment": "paper"}})
    assert cli_main.main(["config", "migrate", "--yes"]) == 3   # CliAborted
    assert "paper" in _creds(tmp_path)                 # 원본 그대로(변환 안 함)


def test_migrate_moves_account_password_per_bundle_only_when_differs(tmp_path):
    # 최상위 비번 0000, pension 이 자기 비번 1111(다름) -> pension 묶음에 per-bundle, 최상위 유지.
    _legacy(tmp_path, pension={"app_key": "PENKEY", "app_secret": "PENSEC",
                               "account": "87654321-22", "environment": "real",
                               "account_password": "1111"})
    cli_main.main(["config", "migrate", "--yes"])
    data = _creds(tmp_path)
    assert data["account_password"] == "0000"
    assert data["real"]["pension"]["account_password"] == "1111"
    assert "account_password" not in data["real"]["general"]   # 최상위와 같으면 묶음에 안 둠


def test_migrate_rename_table_flags_only_live_keys(tmp_path, monkeypatch, capsys):
    _legacy(tmp_path)
    monkeypatch.setenv("KIS_APP_KEY", "liveval")       # 살아있는 bare 키 하나
    cli_main.main(["config", "migrate", "--yes"])

    out = capsys.readouterr().out
    lines = out.splitlines()
    live = next(line for line in lines if "KIS_APP_KEY ->" in line and "KIS_APP_SECRET" not in line)
    secret = next(line for line in lines if "KIS_APP_SECRET ->" in line)
    assert "현재 설정됨" in live                        # 설정된 키만 표시
    assert "현재 설정됨" not in secret                  # 설정 안 된 키는 플래그 없음


def test_migrate_yes_refuses_name_collision_without_losing_profiles(tmp_path):
    # P0: 레거시 main(real)+paper(paper)+general(real). --yes 는 main+paper 를 'general' 로 합치는데
    # 같은 이름의 독립 'general'(real) 이 있어, 막지 않으면 main 의 real 자격증명이 사라진다 -> 거부.
    raw = {"main": {"app_key": "MAINKEY", "app_secret": "MAINSEC", "account": "11111111-01",
                    "environment": "real"},
           "paper": {"app_key": "PAPERKEY", "app_secret": "PAPERSEC", "account": "50001234-01",
                     "environment": "paper"},
           "general": {"app_key": "GENKEY", "app_secret": "GENSEC", "account": "22222222-01",
                       "environment": "real"}}
    _write_raw(tmp_path, raw)
    path = _cfg_dir(tmp_path) / "credentials.json"
    before = _creds(tmp_path)
    before_bytes = path.read_bytes()                            # T3: 중단 시 파일 바이트 불변 고정
    assert cli_main.main(["config", "migrate", "--yes"]) == 3   # CliAborted
    assert _creds(tmp_path) == before                           # 원본 그대로(아무것도 쓰지 않음)
    assert path.read_bytes() == before_bytes                    # 바이트 단위로도 미변경(abort 원자성)


def test_migrate_interactive_rename_avoids_collision(tmp_path, monkeypatch):
    # 같은 충돌 시나리오를 대화형으로: 합친 이름을 'merged' 로 지정하면 독립 'general' 과 안 겹쳐 성공.
    _write_raw(tmp_path, {"main": {"app_key": "MAINKEY", "app_secret": "MAINSEC",
                                   "account": "11111111-01", "environment": "real"},
                          "paper": {"app_key": "PAPERKEY", "app_secret": "PAPERSEC",
                                    "account": "50001234-01", "environment": "paper"},
                          "general": {"app_key": "GENKEY", "app_secret": "GENSEC",
                                      "account": "22222222-01", "environment": "real"}})
    _answer(monkeypatch, {"합칠까요": "y", "합친 프로필 이름": "merged"})

    assert cli_main.main(["config", "migrate"]) == 0
    data = _creds(tmp_path)
    assert data["real"]["merged"]["app_key"] == "MAINKEY"       # 합친 real 슬롯
    assert data["paper"]["merged"]["app_key"] == "PAPERKEY"     # 합친 paper 슬롯
    assert data["real"]["general"]["app_key"] == "GENKEY"       # 독립 general 보존(유실 없음)


def test_migrate_seeds_v2_for_meta_only_legacy_file(tmp_path):
    # P2: 프로필 없이 메타키만 있는 레거시 파일은 막다른 길이 아니라 그 메타를 넘긴 schema 2 로 씨를 뿌린다.
    _write_raw(tmp_path, {"hts_id": "myhts", "account_password": "0000"})
    assert cli_main.main(["config", "migrate", "--yes"]) == 0
    data = _creds(tmp_path)
    assert data["schema"] == 2
    assert data["hts_id"] == "myhts"
    assert data["account_password"] == "0000"
    assert data["default_environment"] == "paper"


def test_migrate_honors_surviving_default_profile_marker(tmp_path):
    # 기존 default_profile 마커가 변환 후에도 존재하면(여기선 pension) 그대로 존중한다.
    _legacy(tmp_path, default_profile="pension",
            pension={"app_key": "PENKEY", "app_secret": "PENSEC",
                     "account": "87654321-22", "environment": "real"})
    cli_main.main(["config", "migrate", "--yes"])
    assert _creds(tmp_path)["default_profile"] == "pension"


def test_migrate_default_profile_marker_falls_back_when_renamed(tmp_path):
    # 마커가 변환 후 사라진 이름(main -> general 로 합쳐짐)을 가리키면 fallback(합친 이름)으로 간다.
    _legacy(tmp_path, default_profile="main")
    cli_main.main(["config", "migrate", "--yes"])
    assert _creds(tmp_path)["default_profile"] == "general"


def test_migrate_yes_merges_same_type_real_paper_pairs(tmp_path):
    # 일반화: main+paper -> general 뿐 아니라 '<X>'(real)+'paper_<X>'(paper) 쌍도 한 프로필 '<X>' 로.
    # 실제 저장소 모양(계좌 종류 여럿, 모의는 derivatives 만)을 그대로 변환.
    raw = {"hts_id": "myhts", "account_password": "0000",
           "main": {"app_key": "MAINKEY", "app_secret": "MS", "account": "11111111-01",
                    "environment": "real"},
           "paper": {"app_key": "PAPERKEY", "app_secret": "PS", "account": "50001234-01",
                     "environment": "paper"},
           "isa": {"app_key": "ISAKEY", "app_secret": "IS", "account": "22222222-01",
                   "environment": "real"},
           "pension": {"app_key": "PENKEY", "app_secret": "PNS", "account": "33333333-22",
                       "environment": "real"},
           "irp": {"app_key": "IRPKEY", "app_secret": "IRS", "account": "44444444-22",
                   "environment": "real"},
           "derivatives": {"app_key": "DERIVKEY", "app_secret": "DS", "account": "55555555-03",
                           "environment": "real"},
           "overseas_derivatives": {"app_key": "OVDKEY", "app_secret": "OVS",
                                     "account": "66666666-08", "environment": "real"},
           "paper_derivatives": {"app_key": "PDERIVKEY", "app_secret": "PDS",
                                 "account": "50009999-03", "environment": "paper"}}
    _write_raw(tmp_path, raw)
    assert cli_main.main(["config", "migrate", "--yes"]) == 0
    data = _creds(tmp_path)
    assert set(data["real"]) == {"general", "isa", "pension", "irp", "derivatives",
                                 "overseas_derivatives"}
    assert set(data["paper"]) == {"general", "derivatives"}
    assert data["real"]["general"]["app_key"] == "MAINKEY"
    assert data["paper"]["general"]["app_key"] == "PAPERKEY"
    assert data["real"]["derivatives"]["app_key"] == "DERIVKEY"     # real 슬롯 <- 레거시 derivatives
    assert data["paper"]["derivatives"]["app_key"] == "PDERIVKEY"   # paper 슬롯 <- 레거시 paper_derivatives
    assert "paper_derivatives" not in data["paper"]                 # 접두어 이름은 남지 않는다
    assert data["default_environment"] == "paper"
    assert data["default_profile"] == "general"
    # 합쳐진 derivatives 를 두 환경으로 해석하면 각 레거시 자격증명이 그대로 돌아온다.
    real = resolve_credentials("derivatives", environment="real", config_dir=_cfg_dir(tmp_path))
    paper = resolve_credentials("derivatives", environment="paper", config_dir=_cfg_dir(tmp_path))
    assert real.app_key == "DERIVKEY" and paper.app_key == "PDERIVKEY"


def test_migrate_paper_prefix_without_base_stays_put(tmp_path):
    # 'paper_foo' 인데 짝 'foo' 가 없으면 쌍이 아니다 -> 자기 이름으로 paper 블록에 그대로(크래시 없음).
    # paper_foo 는 '_real'/'_paper' 로 끝나지 않아 두 축 이름으로 유효하므로 개명을 묻지 않는다.
    _write_raw(tmp_path, {"paper_foo": {"app_key": "K", "app_secret": "S",
                                        "account": "50001234-01", "environment": "paper"}})
    assert cli_main.main(["config", "migrate", "--yes"]) == 0
    data = _creds(tmp_path)
    assert data["paper"]["paper_foo"]["app_key"] == "K"
    assert "real" not in data


def test_migrate_pair_base_env_mismatch_is_not_a_pair(tmp_path):
    # 'paper_x' 의 environment 가 paper 가 아니면(여기선 real) 같은 종류로 볼 수 없어 쌍이 아니다 ->
    # 두 프로필 각자 real 블록에 자기 이름으로 남는다(강제로 합치지 않는다).
    _write_raw(tmp_path, {"x": {"app_key": "XK", "app_secret": "XS", "account": "11111111-01",
                                "environment": "real"},
                          "paper_x": {"app_key": "PXK", "app_secret": "PXS",
                                      "account": "22222222-01", "environment": "real"}})
    assert cli_main.main(["config", "migrate", "--yes"]) == 0
    data = _creds(tmp_path)
    assert data["real"]["x"]["app_key"] == "XK"
    assert data["real"]["paper_x"]["app_key"] == "PXK"   # 합쳐지지 않고 각자 유지
    assert "paper" not in data


def test_migrate_interactive_pair_merge_accepts_default_yes(tmp_path, monkeypatch):
    # 대화형에서 쌍 질문의 기본값은 yes -- Enter(빈 입력)만으로 합쳐진다.
    _write_raw(tmp_path, {"derivatives": {"app_key": "DK", "app_secret": "DS",
                                          "account": "55555555-03", "environment": "real"},
                          "paper_derivatives": {"app_key": "PDK", "app_secret": "PDS",
                                                "account": "50009999-03", "environment": "paper"}})
    _answer(monkeypatch, {}, default="")   # 모든 프롬프트에 빈 입력(Enter)
    assert cli_main.main(["config", "migrate"]) == 0
    data = _creds(tmp_path)
    assert data["real"]["derivatives"]["app_key"] == "DK"
    assert data["paper"]["derivatives"]["app_key"] == "PDK"


def test_migrate_interactive_pair_decline_keeps_separate(tmp_path, monkeypatch):
    # 쌍 병합을 거부하면 두 프로필이 각자 이름/환경으로 남는다(paper_derivatives 는 유효한 두 축 이름).
    _write_raw(tmp_path, {"derivatives": {"app_key": "DK", "app_secret": "DS",
                                          "account": "55555555-03", "environment": "real"},
                          "paper_derivatives": {"app_key": "PDK", "app_secret": "PDS",
                                                "account": "50009999-03", "environment": "paper"}})
    _answer(monkeypatch, {"합칠까요": "n"})
    assert cli_main.main(["config", "migrate"]) == 0
    data = _creds(tmp_path)
    assert data["real"]["derivatives"]["app_key"] == "DK"
    assert data["paper"]["paper_derivatives"]["app_key"] == "PDK"   # 접두어 이름 그대로, 따로 유지
    assert "derivatives" not in data.get("paper", {})


def test_migrate_pair_present_still_aborts_collision_byte_unchanged(tmp_path):
    # P0 회귀(일반화 후에도): main+paper 를 '--yes' 로 'general' 로 합치는데 독립 'general'(real) 이 있어
    # 그대로 두면 main 의 real 자격증명이 사라진다 -> 거부. 쌍(derivatives/paper_derivatives)이 함께
    # 있어도 변환 전체가 중단되고 파일은 바이트 단위로 불변이어야 한다.
    raw = {"main": {"app_key": "MK", "app_secret": "MS", "account": "11111111-01",
                    "environment": "real"},
           "paper": {"app_key": "PK", "app_secret": "PS", "account": "50001234-01",
                     "environment": "paper"},
           "general": {"app_key": "GK", "app_secret": "GS", "account": "22222222-01",
                       "environment": "real"},
           "derivatives": {"app_key": "DK", "app_secret": "DS", "account": "55555555-03",
                           "environment": "real"},
           "paper_derivatives": {"app_key": "PDK", "app_secret": "PDS", "account": "50009999-03",
                                 "environment": "paper"}}
    _write_raw(tmp_path, raw)
    path = _cfg_dir(tmp_path) / "credentials.json"
    before = _creds(tmp_path)
    before_bytes = path.read_bytes()
    assert cli_main.main(["config", "migrate", "--yes"]) == 3   # CliAborted
    assert _creds(tmp_path) == before
    assert path.read_bytes() == before_bytes                    # abort 원자성: 바이트 불변


def test_migrate_idempotent_on_v2_file(tmp_path, capsys):
    _seed_v2(tmp_path)
    before = _creds(tmp_path)
    assert cli_main.main(["config", "migrate", "--yes"]) == 0
    assert "이미 두 축 형식" in capsys.readouterr().out
    assert _creds(tmp_path) == before                  # 변경 없음


def test_migrate_empty_config_reports_nothing_to_do(tmp_path, capsys):
    assert cli_main.main(["config", "migrate", "--yes"]) == 0
    assert "변환할 내용이 없습니다" in capsys.readouterr().out


# --- 버전/도움말 --------------------------------------------------------------

def test_version_and_config_help_exit_zero():
    with pytest.raises(SystemExit) as version_exit:
        cli_main.main(["--version"])
    assert version_exit.value.code == 0
    with pytest.raises(SystemExit) as help_exit:
        cli_main.main(["config", "--help"])
    assert help_exit.value.code == 0


def test_concurrent_saves_preserve_all_profiles(tmp_path):
    # 동시에 서로 다른 프로필을 저장해도 하나도 유실되면 안 된다 -- save() 의 read-modify-write 가
    # 프로세스/스레드 간 락으로 직렬화되는지 검증(락이 없으면 last-writer 가 남의 프로필을 날린다).
    import threading

    from kis_trader import KISConfig

    cfg_dir = tmp_path / "cfg"
    barrier = threading.Barrier(8)

    def save(i: int) -> None:
        barrier.wait()   # 최대 경합 유도
        KISConfig(profile=f"prof{i}", app_key="appkeyvalue", app_secret="appsecretvalue",
                  environment="paper", config_dir=cfg_dir).save()

    threads = [threading.Thread(target=save, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5.0)
    data = json.loads((cfg_dir / "credentials.json").read_text())["paper"]
    for i in range(8):
        assert f"prof{i}" in data, f"prof{i} 가 유실됨(동시 저장 레이스)"
