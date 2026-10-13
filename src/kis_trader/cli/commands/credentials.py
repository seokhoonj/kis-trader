"""``kis config`` -- 자격증명을 두 축(프로필 x 환경) 모델로 다루는 명령 그룹.

하위명령:

- ``setup`` (bare ``kis config`` 도 여기로): 대화형 저장(``aws configure`` 자리). 계좌 종류(프로필)
  하나에 대해 ``real``/``paper`` 를 차례로 묻고, 없는 환경은 APP KEY 를 비우고 Enter 로 건너뛴다
  (연금/IRP/ISA/해외선물옵션은 KIS 모의투자가 보통 없어 ``paper`` 에 참고 힌트를 보여주되,
  프롬프트는 그대로 띄운다 -- 하드 게이트가 아니라 이름 기준 힌트일 뿐).
- ``show``: 저장분을 마스킹해 보여준다(시크릿 비노출; 앱키는 끝 4자리만).
- ``set <profile>.<env>.<field> [VALUE]``: 한 필드 수정. 시크릿(app_key/app_secret/account_password)은
  명령줄 인자로 받지 않고(프로세스 목록/셸 기록 유출 방지) ``getpass`` 숨김 프롬프트로만 입력받는다.
- ``set-default``/``set-hts-id``: 최상위 메타(기본 프로필, HTS 로그인 아이디) 기록.
- ``migrate``: 이전 형식(평평한) ``credentials.json`` 을 두 축 형식(schema 2)으로 변환.

KIS 앱키/시크릿은 개발자포털에서 미리 발급받아 **붙여넣는** 정적 자격증명이라, 브라우저/OAuth 로
토큰을 발급받는 ``login`` 이 아니라 사용자가 가진 키를 받아 저장하는 ``configure`` 모양이다. 이 그룹은
클라이언트를 만들지 않는다 -- 자격증명이 아직 없는 상태가 정상 시작점이다. **앱키·시크릿·계좌비밀번호
값은 어디에도 출력하지 않는다**(화면·로그 모두).
"""
from __future__ import annotations

import argparse
import os
from getpass import getpass
from typing import cast

from ...config import (
    _ACCOUNT_PASSWORD_KEY,
    _BUNDLE_FIELDS,
    _DEFAULT_ENVIRONMENT_KEY,
    _DEFAULT_PROFILE_KEY,
    _ENVIRONMENTS,
    _HTS_ID_KEY,
    _RESERVED_TOP_LEVEL_KEYS,
    _SCHEMA_KEY,
    _SCHEMA_VERSION,
    KISConfig,
    _coerce_account_password,
    _config_dir_path,
    _credentials_path,
    _credentials_write_lock,
    _detect_schema,
    _env_var_prefix,
    _env_var_prefix_v2,
    _load_credentials,
    _profile_exists,
    _validate_account,
    _validate_profile_name_v2,
    _validate_v2_shape,
    _write_credentials,
)
from ...errors import KISUsageError
from ...transport import Environment
from ..errors import CliAborted

#: 이름만으로 KIS 모의투자가 보통 없는 계좌 종류 -- setup 에서 ``paper`` 슬롯에 참고 힌트를 보여준다.
#: (하드 게이트가 아니라 힌트다 -- 그래도 프롬프트는 띄우고, 없으면 사용자가 Enter 로 건너뛴다.)
_PAPER_UNSUPPORTED_PROFILES = frozenset({"isa", "pension", "irp", "overseas_derivatives"})

#: 시크릿 필드 -- 명령줄 인자로 받지 않고 getpass 로만 입력받는다(셸 기록/프로세스 목록 유출 방지).
_SECRET_FIELDS = frozenset({"app_key", "app_secret", _ACCOUNT_PASSWORD_KEY})
#: ``kis config set`` 으로 수정 가능한 묶음 필드.
_SETTABLE_FIELDS = frozenset(_BUNDLE_FIELDS)

#: 환경변수 이름 변경표에서 각 프로필 접두어 뒤에 붙는 필드 접미어(묶음 필드의 대문자형).
_RENAME_FIELDS = tuple(field.upper() for field in _BUNDLE_FIELDS)


def _prompt(label: str, default: str) -> str:
    """``label`` 로 한 줄 입력받는다. 빈 입력이면 ``default``. 기본값이 있으면 ``[기본]`` 으로 보여준다."""
    suffix = f" [{default}]" if default else ""
    return input(f"{label}{suffix}: ").strip() or default


def _confirm(question: str) -> bool:
    """``y``/``yes`` 만 참(기본 거부). 되돌리기 어려운 쓰기 전에 명시적 긍정을 요구한다."""
    return input(f"{question} [y/N]: ").strip().lower() in ("y", "yes")


def _mask(value: object) -> str:
    """시크릿을 끝 4자리만 남기고 가린다(화면 확인용). 4자리 이하는 전부 가린다."""
    if not isinstance(value, str) or not value:
        return "(없음)"
    return "****" + value[-4:] if len(value) > 4 else "****"


def _print_permissions_note() -> None:
    if os.name == "nt":
        print("  permissions: Windows 는 ACL 로 관리하여 0600 이 강제되지 않습니다.")
    else:
        print("  permissions: 0600 (소유자만 읽기)")


def _require_v2_or_fresh(loaded: dict[str, object]) -> None:
    """비어 있지 않은 레거시(변환 전) 파일이면 시크릿을 받기 전에 migrate 를 안내하고 중단한다 --
    입력시킨 뒤 저장 단계(``_merge_base_v2``)에서 거부당하는 막다른 길을 막기 위해서다. ``_merge_base_v2``
    가 비어 있지 않은 비-v2 파일을 전부 거부하므로, 여기서도 프로필 유무와 무관하게 비-빈 레거시 파일을
    모두 막는다(메타키만 있는 파일도 포함). 빈/없는 파일은 통과한다(새 v2 로 저장된다)."""
    if loaded and _detect_schema(loaded) == "legacy":
        raise CliAborted(
            "기존 credentials.json 이 이전 형식입니다 -- 먼저 'kis config migrate' 로 변환한 뒤 다시 실행하세요."
        )


# --- setup --------------------------------------------------------------------

def cmd_config_setup(args: argparse.Namespace) -> None:
    """한 계좌 종류(프로필)의 자격증명을 ``real``/``paper`` 별로 대화형 저장한다. 없는 환경은 APP KEY 를
    비우고 Enter 로 건너뛴다. 레거시 파일이면 시크릿을 받기 전에 migrate 를 안내한다."""
    loaded = _load_credentials(_config_dir_path())
    _require_v2_or_fresh(loaded)                     # 레거시 파일이면 시크릿을 받기 전에 migrate 안내(막다른 길 방지)

    profile = getattr(args, "profile", None) or _prompt(
        "profile 계좌 종류 이름 (예: general, derivatives, pension)", "general")
    _validate_profile_name_v2(profile)               # v2 이름 규칙으로 프로필명 검증(시크릿 입력 전)

    print("앱키·시크릿은 KIS 개발자포털에서 발급받은 값입니다(입력해도 화면에 보이지 않습니다).")
    print("환경(real/paper)별로 차례로 묻습니다 -- 없는 환경은 APP KEY 를 비우고 Enter 로 건너뜁니다.")
    saved: list[tuple[str, str | None]] = []
    for environment in _ENVIRONMENTS:                # ("real", "paper")
        if environment == "real":
            # 되돌리기 어려운 실전 자격증명 저장이라 명시적 긍정을 먼저 요구한다(거부 시 real 만 생략).
            if not _confirm("[real] 실전 자격증명을 설정할까요?"):
                print("  real 건너뜀.")
                continue
        else:
            print("[paper] 모의투자 자격증명")
            if profile in _PAPER_UNSUPPORTED_PROFILES:
                # 하드 게이트가 아니라 참고 힌트다 -- 이름 기준 추정일 뿐이라 프롬프트는 그대로 띄우고,
                # 모의가 없으면 아래 APP KEY 를 비우고 Enter 로 건너뛰면 된다.
                print(f"  참고: {profile} 계좌는 KIS 모의투자가 없는 것이 보통입니다 "
                      "-- 없으면 APP KEY 를 비우고 Enter 로 건너뛰세요.")
        # 환경을 먼저 정한 뒤 그 슬롯의 존재만 확인(슬롯 정확 덮어쓰기 가드).
        if _profile_exists(profile, environment=cast(Environment, environment)) and not _confirm(
                f"  {environment}.{profile} 가 이미 있습니다. 덮어쓸까요?"):
            print("  건너뜀(기존 유지).")
            continue
        app_key = getpass(f"  {environment} APP KEY (없으면 Enter): ")
        if not app_key.strip():
            print(f"  {environment} 건너뜀(APP KEY 미입력).")
            continue
        app_secret = getpass(f"  {environment} APP SECRET: ")
        account = _prompt(f"  {environment} account 계좌번호 CANO-상품코드 (시세만 보면 Enter)", "") or None
        if account is not None:
            _validate_account(account)
        account_password = getpass(
            f"  {environment} account_password 계좌비밀번호 (보통 real 전용, 없으면 Enter): ") or None
        KISConfig(profile=profile, app_key=app_key, app_secret=app_secret, account=account,
                  environment=cast(Environment, environment), account_password=account_password).save()
        saved.append((environment, account))

    if not saved:
        raise CliAborted("저장된 자격증명이 없습니다.")

    set_default = getattr(args, "set_default", False)
    if set_default:
        KISConfig.set_default_profile(profile)

    print(f"저장됨: {_credentials_path()}")
    print(f"  profile: {profile}" + ("  (기본)" if set_default else ""))
    for environment, account in saved:
        print(f"  {environment}: account {account if account else '(없음 -- 시세전용)'}")
    _print_permissions_note()


# --- show ---------------------------------------------------------------------

def cmd_config_show(args: argparse.Namespace) -> None:
    """저장된 설정을 마스킹해 보여준다(시크릿 비노출; 앱키는 끝 4자리만). 레거시 파일이면 배너로
    안내한다. 계좌비밀번호는 값 대신 '(설정됨)' 으로만 표시한다."""
    path = _credentials_path()
    loaded = _load_credentials(_config_dir_path())
    if not loaded:
        print(f"설정된 자격증명이 없습니다 ({path}). 'kis config' 로 저장하세요.")
        return
    if _detect_schema(loaded) == "legacy":
        print(f"[레거시 형식] {path}")
        print("  이전 형식입니다 -- 'kis config migrate' 로 두 축 형식으로 변환하세요.")
        for name, section in loaded.items():
            if name in _RESERVED_TOP_LEVEL_KEYS or not isinstance(section, dict):
                continue
            environment = section.get("environment", "real")
            line = f"  {name} (environment={environment}): app_key {_mask(section.get('app_key'))}"
            if section.get("account"):
                line += f", account {section['account']}"
            print(line)
        return

    print(f"설정 파일: {path}  (schema {_SCHEMA_VERSION})")
    hts_id = loaded.get(_HTS_ID_KEY)
    if isinstance(hts_id, str) and hts_id:
        print(f"  hts_id: {hts_id}")
    if _ACCOUNT_PASSWORD_KEY in loaded:
        print("  account_password: (설정됨)")
    default_environment = loaded.get(_DEFAULT_ENVIRONMENT_KEY)
    if isinstance(default_environment, str):
        print(f"  default_environment: {default_environment}")
    default_profile = loaded.get(_DEFAULT_PROFILE_KEY)
    if isinstance(default_profile, str):
        print(f"  default_profile: {default_profile}")
    for environment in _ENVIRONMENTS:
        block = loaded.get(environment)
        if not isinstance(block, dict) or not block:
            continue
        print(f"  [{environment}]")
        for profile, bundle in block.items():
            if not isinstance(bundle, dict):
                continue
            line = f"    {profile}: app_key {_mask(bundle.get('app_key'))}"
            if bundle.get("account"):
                line += f", account {bundle['account']}"
            if _ACCOUNT_PASSWORD_KEY in bundle:
                line += ", account_password (설정됨)"
            print(line)


# --- set ----------------------------------------------------------------------

def _parse_set_address(address: str) -> tuple[str, str, str]:
    """``profile.environment.field`` 주소를 셋으로 가른다(정확히 점 둘)."""
    parts = address.split(".")
    if len(parts) != 3 or not all(parts):
        raise CliAborted(
            f"주소 형식은 profile.environment.field 입니다 (예: general.paper.app_key): {address!r}")
    return parts[0], parts[1], parts[2]


def _existing_bundle(loaded: dict[str, object], profile: str, environment: str) -> dict[str, str]:
    """저장분에서 한 (환경, 프로필) 묶음의 필드를 문자열로 모아 돌려준다(없으면 빈 dict). 한 필드만
    바꿔 다시 저장할 수 있게 기존 값을 읽어온다 -- 해석기(환경변수 레이어링)를 거치지 않고 파일 값만 본다."""
    block = loaded.get(environment)
    bundle = block.get(profile) if isinstance(block, dict) else None
    result: dict[str, str] = {}
    if isinstance(bundle, dict):
        for key in _BUNDLE_FIELDS:
            value = bundle.get(key)
            if isinstance(value, str):
                result[key] = value
            elif isinstance(value, int) and not isinstance(value, bool):
                result[key] = str(value)
    return result


def cmd_config_set(args: argparse.Namespace) -> None:
    """한 필드를 수정한다 -- ``kis config set general.paper.app_key`` 꼴. 시크릿은 argv 로 받지 않고
    숨김 프롬프트로만 입력받는다. 기존 묶음에서 그 필드만 바꿔 다시 저장한다(슬롯은 미리 있어야 함)."""
    profile, environment, field_name = _parse_set_address(args.address)
    _validate_profile_name_v2(profile)
    if environment not in _ENVIRONMENTS:
        raise CliAborted(f"environment 는 {'/'.join(_ENVIRONMENTS)} 중 하나여야 합니다: {environment!r}")
    if field_name not in _SETTABLE_FIELDS:
        raise CliAborted(
            f"수정할 수 있는 필드: {', '.join(sorted(_SETTABLE_FIELDS))} (받은 값: {field_name!r})")

    loaded = _load_credentials(_config_dir_path())
    _require_v2_or_fresh(loaded)
    merged = _existing_bundle(loaded, profile, environment)

    argv_value = getattr(args, "value", None)
    if field_name in _SECRET_FIELDS:
        if argv_value is not None:
            raise CliAborted(
                f"{field_name} 은 시크릿이라 명령줄 인자로 받지 않습니다(셸 기록 유출 방지) -- "
                "값 없이 실행하면 숨김 프롬프트로 입력받습니다.")
        new_value: str | None = getpass(f"{profile}.{environment}.{field_name}: ")
    else:                                            # account(비시크릿): argv 또는 프롬프트
        new_value = argv_value if argv_value is not None else _prompt(
            f"{profile}.{environment}.{field_name}", "")

    if field_name in ("account", _ACCOUNT_PASSWORD_KEY):
        if field_name == "account":
            new_value = (new_value or "").strip() or None   # 계좌번호는 공백을 다듬고 빈 값은 삭제
        else:
            new_value = new_value or None                    # 비밀번호는 다듬지 않는다(공백이 유효할 수 있어)
        if field_name == "account" and new_value is not None:
            _validate_account(new_value)
        if new_value is None:
            merged.pop(field_name, None)
        else:
            merged[field_name] = new_value
    else:                                            # app_key / app_secret: 비울 수 없다
        if new_value is None or not new_value.strip():
            raise CliAborted(f"{field_name} 은 비어 있을 수 없습니다.")
        merged[field_name] = new_value

    if "app_key" not in merged or "app_secret" not in merged:
        raise CliAborted(
            f"{profile}.{environment} 에 app_key/app_secret 이 아직 없습니다 -- "
            "먼저 'kis config' 로 슬롯을 만드세요.")
    KISConfig(profile=profile, app_key=merged["app_key"], app_secret=merged["app_secret"],
              account=merged.get("account"), environment=cast(Environment, environment),
              account_password=merged.get(_ACCOUNT_PASSWORD_KEY)).save()
    print(f"수정됨: {profile}.{environment}.{field_name}")


# --- set-default / set-hts-id -------------------------------------------------

def cmd_config_set_default(args: argparse.Namespace) -> None:
    """이름 없이 세션을 열 때 여는 기본 프로필을 지정한다(최상위 ``default_profile`` 마커). 대상 프로필은
    전역 ``--profile`` 과 섞이지 않게 별도 위치인자(``args.name``)로 받는다."""
    _validate_profile_name_v2(args.name)
    path = KISConfig.set_default_profile(args.name)
    print(f"기본 프로필: {args.name} ({path})")


def cmd_config_set_hts_id(args: argparse.Namespace) -> None:
    """조건검색·관심종목 조회에 쓰는 HTS 로그인 아이디를 저장한다(최상위 공유, 인증엔 안 씀)."""
    path = KISConfig.set_hts_id(args.hts_id)
    print(f"hts_id 저장됨: {path}")


# --- migrate (레거시 -> 두 축 schema 2) ---------------------------------------

def _legacy_environment(section: dict[str, object]) -> str:
    """레거시 프로필 섹션의 저장된 ``environment`` 필드(없거나 유효하지 않으면 레거시 기본 ``real``)."""
    environment = section.get("environment")
    if isinstance(environment, str) and environment in _ENVIRONMENTS:
        return environment
    return "real"


def _legacy_bundle(section: dict[str, object], top_password: str | None) -> dict[str, str]:
    """레거시 섹션에서 묶음(app_key/app_secret/account)을 추려 돌려준다. 섹션에 자체
    ``account_password`` 가 있고 최상위 공유값과 **다를 때만** 묶음별로 둔다(같으면 최상위 공유)."""
    bundle: dict[str, str] = {}
    for key in _BUNDLE_FIELDS[:3]:                   # account_password 는 아래에서 따로 처리
        value = section.get(key)
        if isinstance(value, str) and value.strip():
            bundle[key] = value
        elif isinstance(value, int) and not isinstance(value, bool):
            bundle[key] = str(value)
    per_bundle = _coerce_account_password(section.get(_ACCOUNT_PASSWORD_KEY))
    if per_bundle is not None and per_bundle != top_password:
        bundle[_ACCOUNT_PASSWORD_KEY] = per_bundle
    return bundle


def _print_env_rename_table(renames: list[tuple[str, str, str]]) -> None:
    """레거시 접두어 환경변수를 두 축 키로 바꾸는 표를 출력한다. 현재 셸/환경에 실제로 설정된 키만
    ``[현재 설정됨 WARN]`` 로 표시한다(값은 출력하지 않는다 -- 이름만)."""
    if not renames:
        return
    print("환경변수 이름 변경(설정해 둔 것이 있으면 아래 두 축 키로 바꾸세요):")
    any_live = False
    for old_name, new_name, environment in renames:
        old_prefix = _env_var_prefix(old_name)       # KIS_ (main) 또는 KIS_<NAME>_
        new_prefix = _env_var_prefix_v2(new_name, environment)   # KIS_<NAME>_<ENV>_
        for field in _RENAME_FIELDS:
            old_key, new_key = old_prefix + field, new_prefix + field
            live = old_key in os.environ
            any_live = any_live or live
            print(f"  {old_key} -> {new_key}" + ("  [현재 설정됨 ⚠]" if live else ""))
    if "KIS_ENVIRONMENT" in os.environ:              # 환경 선택용 레거시 키는 v2 에서 폐기
        any_live = True
        print("  KIS_ENVIRONMENT -> (제거; v2 는 --environment / KIS_DEFAULT_ENVIRONMENT 사용)"
              "  [현재 설정됨 ⚠]")
    if any_live:
        print("  ⚠ 로 표시된 키가 현재 환경에 설정돼 있습니다 -- 위 두 축 키로 교체하세요.")


def _pick_default_profile(
    legacy_default: object, blocks: dict[str, dict[str, dict[str, str]]], fallback: str | None,
) -> str | None:
    """변환 후 기본 프로필 이름. 기존 ``default_profile`` 마커가 변환 후에도 존재하면 존중, 아니면
    ``fallback``(합친 이름 또는 첫 프로필)."""
    if isinstance(legacy_default, str) and legacy_default.strip():
        name = legacy_default.strip()
        if any(name in blocks[environment] for environment in _ENVIRONMENTS):
            return name
    return fallback


def cmd_config_migrate(args: argparse.Namespace) -> None:
    """이전 형식(평평한) ``credentials.json`` 을 두 축 형식(schema 2)으로 변환한다. 구 ``main``(real)과
    구 ``paper``(paper)를 한 계좌 종류로 합칠지 **물어본다**(사람 지식이라 자동 추론하지 않는다).
    ``default_environment`` 는 fail-safe 로 ``paper`` 를, ``default_profile`` 은 합친/첫 프로필을 명시
    기록한다. 두 레거시 프로필이 같은 (환경, 이름) 슬롯으로 접히면(예: 합친 이름이 이미 독립 프로필)
    한쪽이 덮여 사라지므로 쓰기 전에 **거부**하고 해결법을 안내한다. 프로필 없이 메타키만 있는 레거시
    파일도 막다른 길이 아니라 그 메타를 넘긴 ``schema 2`` 파일로 씨를 뿌린다. 원자적(기존 write-lock)이고,
    이미 두 축 형식이면 아무것도 바꾸지 않는다."""
    directory = _config_dir_path()
    path = _credentials_path()
    with _credentials_write_lock(directory):   # RMW: 읽기-판단-쓰기를 한 락 아래서 직렬화(동시 writer 의 쓰기 유실 방지)
        loaded = _load_credentials(directory)
        if not loaded or _detect_schema(loaded) == "v2":
            print(f"이미 두 축 형식이거나 변환할 내용이 없습니다 ({path}).")
            return
        profiles = {name: section for name, section in loaded.items()
                    if name not in _RESERVED_TOP_LEVEL_KEYS and isinstance(section, dict)}

        yes = getattr(args, "yes", False)
        top_password = _coerce_account_password(loaded.get(_ACCOUNT_PASSWORD_KEY))
        blocks: dict[str, dict[str, dict[str, str]]] = {"real": {}, "paper": {}}
        consumed: set[str] = set()
        renames: list[tuple[str, str, str]] = []         # (old_name, new_name, environment)
        default_profile_name: str | None = None
        #: (환경, 대상이름) -> 그 슬롯을 차지한 레거시 프로필 이름. 충돌(두 프로필이 같은 슬롯)을 잡아,
        #: 쓰기 전에 한쪽 자격증명이 조용히 사라지는 것을 막는다.
        slot_source: dict[tuple[str, str], str] = {}

        def _claim(environment: str, target: str, source: str) -> None:
            """``(environment, target)`` 슬롯을 ``source`` 레거시 프로필로 선점하고 그 묶음을 채운다.
            이미 다른 프로필이 같은 슬롯에 매핑돼 있으면 거부한다 -- 그대로 두면 한쪽이 덮여 사라진다."""
            key = (environment, target)
            occupant = slot_source.get(key)
            if occupant is not None:
                raise CliAborted(
                    f"변환 충돌: 레거시 프로필 '{occupant}' 와 '{source}' 가 모두 {environment}.{target} "
                    "슬롯에 매핑됩니다 -- 그대로 두면 한쪽 자격증명이 사라집니다. 한쪽 이름을 바꾸거나 "
                    "'--yes' 없이 대화형으로 실행해 다른 이름을 지정하세요.")
            slot_source[key] = source
            blocks[environment][target] = _legacy_bundle(profiles[source], top_password)

        # 병합: 구 main(real) + 구 paper(paper) -> 한 계좌 종류. 같은 종류인지는 사람 지식이라 물어본다.
        if "main" in profiles and "paper" in profiles:
            if yes:
                do_merge, merged_name = True, "general"
            elif _confirm("'general' 프로필로 합칠까요? real 슬롯 = 기존 'main', paper 슬롯 = 기존 'paper'"):
                do_merge, merged_name = True, _prompt("합친 프로필 이름", "general")
            else:
                do_merge, merged_name = False, ""
            if do_merge:
                _validate_profile_name_v2(merged_name)
                # 합친 이름이 이미 독립 레거시 프로필이면 합치는 순간 그 자격증명이 사라진다 -- 미리 막는다.
                if merged_name in profiles and merged_name not in ("main", "paper"):
                    raise CliAborted(
                        f"병합 이름 '{merged_name}' 이 이미 레거시 프로필로 있어 합치면 그 자격증명이 사라집니다 "
                        "-- 다른 이름을 쓰거나 '--yes' 없이 실행해 지정하세요.")
                _claim("real", merged_name, "main")
                _claim("paper", merged_name, "paper")
                consumed |= {"main", "paper"}
                renames += [("main", merged_name, "real"), ("paper", merged_name, "paper")]
                default_profile_name = merged_name

        # 나머지 프로필: 저장된 환경 블록에 자기 이름으로. 두 축에서 못 쓰는 이름(paper 등)은 개명을 묻는다.
        for name, section in profiles.items():
            if name in consumed:
                continue
            environment = _legacy_environment(section)
            target = name
            try:
                _validate_profile_name_v2(target)
            except KISUsageError:
                if yes:
                    raise CliAborted(
                        f"'{name}' 은 두 축 형식에서 프로필 이름으로 쓸 수 없어 비대화형으로 변환할 수 없습니다 "
                        "-- --yes 없이 실행해 새 이름을 지정하세요.") from None
                target = _prompt(f"'{name}' 은 두 축 프로필 이름으로 쓸 수 없습니다. 새 이름", "")
                _validate_profile_name_v2(target)
            _claim(environment, target, name)
            renames.append((name, target, environment))
            if default_profile_name is None:
                default_profile_name = target

        data: dict[str, object] = {_SCHEMA_KEY: _SCHEMA_VERSION}
        if isinstance(loaded.get(_HTS_ID_KEY), str) and cast(str, loaded[_HTS_ID_KEY]).strip():
            data[_HTS_ID_KEY] = cast(str, loaded[_HTS_ID_KEY]).strip()
        if top_password is not None:
            data[_ACCOUNT_PASSWORD_KEY] = top_password
        data[_DEFAULT_ENVIRONMENT_KEY] = "paper"         # fail-safe: 실주문은 명시해야 닿는다
        chosen_default = _pick_default_profile(loaded.get(_DEFAULT_PROFILE_KEY), blocks, default_profile_name)
        if chosen_default is not None:
            data[_DEFAULT_PROFILE_KEY] = chosen_default
        if blocks["real"]:
            data["real"] = blocks["real"]
        if blocks["paper"]:
            data["paper"] = blocks["paper"]

        _validate_v2_shape(data)                         # 쓰기 전에 형상 확인(묶음에 environment 없음 등)
        _write_credentials(directory, data)

    print(f"변환됨: {path}")
    print(f"  schema: {_SCHEMA_VERSION}")
    print("  default_environment: paper (fail-safe -- 실주문은 --environment real 로 명시)")
    if chosen_default is not None:
        print(f"  default_profile: {chosen_default}")
    for environment in _ENVIRONMENTS:
        names = list(blocks[environment])
        if names:
            print(f"  [{environment}] {', '.join(names)}")
    _print_env_rename_table(renames)
