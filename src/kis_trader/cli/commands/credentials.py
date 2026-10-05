"""``kis config`` -- 자격증명을 프로필로 저장하는 대화형 명령(``aws configure`` 와 같은 자리).

KIS 앱키/시크릿은 개발자포털에서 미리 발급받아 **붙여넣는** 정적 자격증명이라, 브라우저/OAuth 로
토큰을 발급받는 ``login`` 이 아니라 사용자가 가진 키를 받아 저장하는 ``configure`` 모양이다. 시크릿은
명령줄 인자로 받지 않고(프로세스 목록/셸 기록 유출 방지) ``getpass`` 숨김 프롬프트로만 입력받으며,
비시크릿(프로필 이름/환경/계좌)은 플래그 또는 프롬프트로 받아 :meth:`KISConfig.save` 로 기록한다.
클라이언트를 만들지 않는다 -- 자격증명이 아직 없는 상태가 이 명령의 정상 시작점이다.
"""
from __future__ import annotations

import argparse
import os
from getpass import getpass
from typing import cast, get_args

from ...config import (
    KISConfig,
    _config_dir_path,
    _read_existing,
    _validate_account,
    _validate_profile_name,
)
from ...transport import Environment
from ..errors import CliAborted

#: 유효 환경값 -- 패키지의 ``Environment`` Literal 에서 유도한다(소비자가 분류를 재기술하지 않는다).
_ENVIRONMENTS = get_args(Environment)


def _prompt(label: str, default: str) -> str:
    """``label`` 로 한 줄 입력받는다. 빈 입력이면 ``default``. 기본값이 있으면 ``[기본]`` 으로 보여준다."""
    suffix = f" [{default}]" if default else ""
    return input(f"{label}{suffix}: ").strip() or default


def _confirm(question: str) -> bool:
    """``y``/``yes`` 만 참(기본 거부). 되돌리기 어려운 쓰기 전에 명시적 긍정을 요구한다."""
    return input(f"{question} [y/N]: ").strip().lower() in ("y", "yes")


def cmd_config(args: argparse.Namespace) -> None:
    profile = getattr(args, "profile", None) or _prompt("profile (예: main, paper, pension)", "main")
    _validate_profile_name(profile)  # 시크릿을 받기 전에 형식을 거부(헛되이 입력시키지 않는다)

    # 덮어쓰기 가드: 같은 프로필이 이미 있으면(save 는 조용히 교체하므로) 시크릿을 받기 전에 확인한다.
    creds_path = _config_dir_path(None) / "credentials.json"
    if isinstance(_read_existing(creds_path).get(profile), dict) and not _confirm(
            f"프로필 {profile!r} 가 이미 있습니다 ({creds_path}). 덮어쓸까요?"):
        raise CliAborted("취소 -- 저장하지 않았습니다.")

    environment = args.environment or _prompt(f"environment ({'/'.join(_ENVIRONMENTS)})", "paper")
    if environment not in _ENVIRONMENTS:
        raise CliAborted(f"환경은 {'/'.join(_ENVIRONMENTS)} 중 하나여야 합니다: {environment!r}")
    if environment == "real" and not _confirm("실전(real) 자격증명을 저장합니다. 계속할까요?"):
        raise CliAborted("취소 -- 저장하지 않았습니다.")

    account_flag = getattr(args, "account", None)
    account = (account_flag if account_flag is not None
               else _prompt("account (예: 50123456-01, 시세만 보면 Enter)", ""))
    account = account or None
    if account is not None:
        _validate_account(account)  # 시크릿을 받기 전에 형식을 거부

    # 앱키/시크릿은 KIS 개발자포털 발급값이며, getpass 라 입력해도 화면에 보이지 않는다(정상).
    print("앱키·시크릿은 KIS 개발자포털에서 발급받은 값입니다(입력해도 화면에 보이지 않습니다).")
    app_key = getpass("APP KEY: ")
    app_secret = getpass("APP SECRET: ")
    path = KISConfig(profile=profile, app_key=app_key, app_secret=app_secret,
                     account=account, environment=cast(Environment, environment)).save()
    if getattr(args, "set_default", False):
        KISConfig.set_default(profile)

    # 요약: 시크릿은 절대 출력하지 않는다(경로/프로필/환경/계좌만).
    print(f"저장됨: {path}")
    print(f"  profile: {profile}" + ("  (기본)" if getattr(args, "set_default", False) else ""))
    print(f"  environment: {environment}")
    print(f"  account: {account if account else '(없음 -- 시세전용)'}")
    if os.name == "nt":
        print("  permissions: Windows 는 ACL 로 관리하여 0600 이 강제되지 않습니다.")
    else:
        print("  permissions: 0600 (소유자만 읽기)")
