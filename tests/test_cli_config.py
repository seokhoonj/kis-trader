"""``kis config`` -- 대화형 자격증명 저장 명령.

시크릿은 getpass 프롬프트 전용(플래그 금지), 비시크릿(profile/environment/account)은 플래그 또는
프롬프트, 저장은 기존 :meth:`KISConfig.save` 재사용. 이 명령은 클라이언트를 만들지 않는다(자격증명이
아직 없는 상태가 정상 시작점이므로).
"""
from __future__ import annotations

import json
import os

import pytest

import kis_trader.cli.app as cli_main


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """실 ``~/.config/kis-trader`` 를 건드리지 않게 XDG 를 임시 디렉터리로 돌리고 ``KIS_*`` 를 지운다."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    for var in [name for name in os.environ if name.startswith("KIS_")]:
        monkeypatch.delenv(var, raising=False)
    return tmp_path


def _creds(tmp_path):
    return json.loads((tmp_path / "config" / "kis-trader" / "credentials.json").read_text())


def _feed_secrets(monkeypatch, app_key="appkeyvalue", app_secret="appsecretvalue"):
    """getpass 두 번 호출(app_key, app_secret)을 차례로 돌려준다."""
    values = iter([app_key, app_secret])
    monkeypatch.setattr("kis_trader.cli.commands.credentials.getpass",
                        lambda prompt="": next(values))


def _answer(monkeypatch, mapping, default=""):
    """``input()`` 을 프롬프트 문구의 부분 문자열로 분기해 답한다(호출 순서에 무관)."""
    def _input(prompt=""):
        for needle, value in mapping.items():
            if needle in prompt:
                return value
        return default
    monkeypatch.setattr("builtins.input", _input)


def _no_client(args):
    raise AssertionError("kis config 는 클라이언트를 만들면 안 된다")


def test_config_writes_profile_without_building_a_client(tmp_path, monkeypatch):
    monkeypatch.setattr(cli_main, "build_client", _no_client)
    _feed_secrets(monkeypatch, "pk", "ps")

    code = cli_main.main(["config", "--profile", "paper", "--environment", "paper",
                          "--account", "50123456-01"])

    assert code == 0
    entry = _creds(tmp_path)["paper"]
    assert entry["app_key"] == "pk"
    assert entry["app_secret"] == "ps"
    assert entry["environment"] == "paper"
    assert entry["account"] == "50123456-01"


def test_config_prompts_when_flags_omitted(tmp_path, monkeypatch):
    monkeypatch.setattr(cli_main, "build_client", _no_client)
    _feed_secrets(monkeypatch, "pk", "ps")
    _answer(monkeypatch, {"profile": "irp", "environment": "paper", "account": "12345678-22"})

    code = cli_main.main(["config"])

    assert code == 0
    entry = _creds(tmp_path)["irp"]
    assert entry["environment"] == "paper"
    assert entry["account"] == "12345678-22"


def test_config_defaults_profile_main_and_environment_paper_on_empty_prompt(tmp_path, monkeypatch):
    monkeypatch.setattr(cli_main, "build_client", _no_client)
    _feed_secrets(monkeypatch, "pk", "ps")
    _answer(monkeypatch, {}, default="")  # 모든 프롬프트 빈 입력 -> 기본값

    code = cli_main.main(["config"])

    assert code == 0
    data = _creds(tmp_path)
    assert "main" in data
    assert data["main"]["environment"] == "paper"
    assert "account" not in data["main"]  # 빈 계좌 -> 저장 안 함(시세전용)


def _seed(tmp_path, monkeypatch, profile="paper", app_key="old_k", app_secret="old_s"):
    monkeypatch.setattr(cli_main, "build_client", _no_client)
    _feed_secrets(monkeypatch, app_key, app_secret)
    assert cli_main.main(["config", "--profile", profile, "--environment", "paper",
                          "--account", "50123456-01"]) == 0


@pytest.mark.parametrize("answer,code,expected", [
    # 거부 -> 기존 프로필 통째로 보존.
    ("n", 3, {"app_key": "old_k", "app_secret": "old_s",
              "environment": "paper", "account": "50123456-01"}),
    # 승인 -> 기존 프로필 통째로 교체(save 는 섹션 전체를 갈아끼운다).
    ("y", 0, {"app_key": "new_k", "app_secret": "new_s",
              "environment": "paper", "account": "87654321-02"}),
])
def test_config_overwrite_confirmation_controls_the_whole_profile(
        tmp_path, monkeypatch, answer, code, expected):
    _seed(tmp_path, monkeypatch, app_key="old_k", app_secret="old_s")
    _feed_secrets(monkeypatch, "new_k", "new_s")
    _answer(monkeypatch, {"덮어": answer})

    result = cli_main.main(["config", "--profile", "paper", "--environment", "paper",
                            "--account", "87654321-02"])

    assert result == code
    assert _creds(tmp_path)["paper"] == expected


def test_config_rejects_invalid_prompted_environment_before_reading_secrets(tmp_path, monkeypatch):
    monkeypatch.setattr(cli_main, "build_client", _no_client)
    _answer(monkeypatch, {"profile": "main", "environment": "production"})
    monkeypatch.setattr("kis_trader.cli.commands.credentials.getpass",
                        lambda prompt="": pytest.fail("invalid environment 는 시크릿 전에 거부돼야 한다"))

    code = cli_main.main(["config"])

    assert code == 3  # CliAborted
    assert not (tmp_path / "config" / "kis-trader" / "credentials.json").exists()


def test_config_never_echoes_secret_even_when_save_fails(tmp_path, monkeypatch, capsys):
    from kis_trader.errors import KISError

    monkeypatch.setattr(cli_main, "build_client", _no_client)
    _feed_secrets(monkeypatch, "SECRETKEY", "SECRETVAL")

    def _fail_save(self):
        raise KISError("자격증명 쓰기 실패")
    monkeypatch.setattr("kis_trader.cli.commands.credentials.KISConfig.save", _fail_save)

    code = cli_main.main(["config", "--profile", "paper", "--environment", "paper",
                          "--account", "50123456-01"])

    out, err = capsys.readouterr()
    assert code != 0
    assert "SECRETKEY" not in out and "SECRETKEY" not in err
    assert "SECRETVAL" not in out and "SECRETVAL" not in err


def test_config_real_environment_requires_affirmation(tmp_path, monkeypatch):
    monkeypatch.setattr(cli_main, "build_client", _no_client)
    _feed_secrets(monkeypatch, "rk", "rs")
    _answer(monkeypatch, {"실전": "n"})

    code = cli_main.main(["config", "--profile", "main", "--environment", "real",
                          "--account", "12345678-01"])

    assert code == 3
    assert not (tmp_path / "config" / "kis-trader" / "credentials.json").exists()


def test_config_real_environment_written_when_affirmed(tmp_path, monkeypatch):
    monkeypatch.setattr(cli_main, "build_client", _no_client)
    _feed_secrets(monkeypatch, "rk", "rs")
    _answer(monkeypatch, {"실전": "y"})

    code = cli_main.main(["config", "--profile", "main", "--environment", "real",
                          "--account", "12345678-01"])

    assert code == 0
    assert _creds(tmp_path)["main"]["environment"] == "real"


def test_config_summary_never_echoes_the_secret(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli_main, "build_client", _no_client)
    _feed_secrets(monkeypatch, "SECRETKEY", "SECRETVAL")

    code = cli_main.main(["config", "--profile", "paper", "--environment", "paper",
                          "--account", "50123456-01"])

    assert code == 0
    out = capsys.readouterr().out
    assert "paper" in out
    assert "50123456-01" in out
    assert "SECRETKEY" not in out
    assert "SECRETVAL" not in out


def test_config_preserves_sibling_profiles(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, profile="paper", app_key="pk")
    monkeypatch.setattr(cli_main, "build_client", _no_client)
    _feed_secrets(monkeypatch, "mk", "ms")
    _answer(monkeypatch, {"실전": "y"})
    assert cli_main.main(["config", "--profile", "main", "--environment", "real",
                          "--account", "12345678-01"]) == 0

    data = _creds(tmp_path)
    assert data["paper"]["app_key"] == "pk"
    assert data["main"]["app_key"] == "mk"


def test_config_set_default_marks_default_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(cli_main, "build_client", _no_client)
    _feed_secrets(monkeypatch, "pk", "ps")

    code = cli_main.main(["config", "--profile", "paper", "--environment", "paper",
                          "--account", "50123456-01", "--set-default"])

    assert code == 0
    assert _creds(tmp_path)["default_profile"] == "paper"


def test_config_rejects_secret_flags():
    for flag in ("--app-key", "--app-secret"):
        with pytest.raises(SystemExit) as exc:
            cli_main.main(["config", flag, "leak"])
        assert exc.value.code == 2  # argparse usage error


def test_version_and_config_help_exit_zero():
    with pytest.raises(SystemExit) as version_exit:
        cli_main.main(["--version"])
    assert version_exit.value.code == 0
    with pytest.raises(SystemExit) as help_exit:
        cli_main.main(["config", "--help"])
    assert help_exit.value.code == 0
