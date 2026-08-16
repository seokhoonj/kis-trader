"""자격증명을 프로필 단위로 저장(:class:`KISConfig`)하고 세션이 읽어들이는 자리.

두 방향이 있다. **쓰기**는 :class:`KISConfig` 에 값을 담아 :meth:`KISConfig.save` 로
``~/.config/kis-trader/credentials.json`` 에 기록한다(``aws configure`` 와 같은 자리). **읽기**는
:func:`resolve_credentials` 가 프로필로 저장분을 찾아 세션(:class:`~kis_trader.client.KISClient`)에
넘긴다 -- 보통 ``KISClient(profile=...)`` 가 내부에서 부른다.

**프로필은 사용자가 이름 붙이는 자유 문자열이다.** 앱키/시크릿은 종합계좌번호(CANO) 단위라 계좌마다
따로이고 같은 유형 계좌도 여럿일 수 있으므로(연금저축 2개 등), "계좌 하나 = 프로필 하나"로 다룬다.
각 프로필은 앱키·시크릿·계좌번호와 **접속 환경(실전/모의)**을 갖는다. ``main`` 은 기본 프로필 이름일
뿐이며, 환경은 이름이 아니라 프로필에 저장된 값이 정한다(기본 ``real``; 모의는 ``environment="paper"``).

``credentials.json`` 은 프로필별 중첩 객체다::

    {
      "main":      {"app_key": "...", "app_secret": "...", "account": "12345678-01", "environment": "real"},
      "pension_a": {"app_key": "...", "app_secret": "...", "account": "87654321-22", "environment": "real"},
      "paper":     {"app_key": "...", "app_secret": "...", "account": "...",         "environment": "paper"}
    }

읽기 순서: 환경변수 -> ``credentials.json``. 환경변수는 프로필 접두어를 붙인 평평한 키를 쓴다
(``main`` -> ``KIS_APP_KEY`` / ``KIS_ACCOUNT`` / ``KIS_ENVIRONMENT``; ``pension_a`` ->
``KIS_PENSION_A_APP_KEY`` ...). **앱키·시크릿 값은 어디에도 출력하지 않는다**(화면·로그·예외 모두);
누락 예외에는 변수 이름만 담는다. (형식 오류 진단을 위해 계좌·프로필 문자열은 예외 메시지에 담을 수
있다 -- 이들은 시크릿이 아니다.)

설정 위치는 XDG 규약을 따라 세 OS 공통이다: 편집 대상(``credentials.json`` 0600)은
``$XDG_CONFIG_HOME/kis-trader`` (없으면 ``~/.config/kis-trader``), 재생성 가능한 OAuth 토큰 캐시는
분리해 ``$XDG_CACHE_HOME/kis-trader/tokens``. (Windows 는 파일 권한을 POSIX 비트가 아니라 ACL 로
관리하므로 ``0600`` 이 그대로 강제되진 않고 사용자 프로필 폴더 접근권한에 의존한다.)
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ._internal._fsutil import atomic_write_bytes, xdg_cache_subdir, xdg_config_subdir
from .errors import KISUsageError

if TYPE_CHECKING:
    from .transport import Environment

#: XDG 하위 디렉터리 이름(``~/.config/kis-trader`` / ``~/.cache/kis-trader``).
_APP_DIR_NAME = "kis-trader"

#: 접속 환경. 프로필별로 저장한다(기본 real). 모의계좌만 paper.
_ENVIRONMENTS = ("real", "paper")

#: 프로필 이름 규칙 -- 소문자/숫자/밑줄. 환경변수 키(``KIS_<이름대문자>_APP_KEY``)로 안전히 올릴 수
#: 있고, 대문자로 접힐 때 서로 다른 이름이 같은 키로 aliasing 되지 않도록 소문자만 허용한다.
_PROFILE_NAME = re.compile(r"[a-z0-9_]+")


def _validate_profile_name(profile: str) -> None:
    if not _PROFILE_NAME.fullmatch(profile):
        raise KISUsageError(
            f"프로필 이름은 소문자/숫자/밑줄만 쓸 수 있다(환경변수 키로 쓰이기 때문): {profile!r}"
        )


def _env_var_prefix(profile: str) -> str:
    """프로필 -> 환경변수 키 접두어. 기본 프로필 ``main`` 은 접두어 없이(``KIS_``), 나머지는
    ``KIS_<이름대문자>_``. 예: ``pension_a`` -> ``KIS_PENSION_A_``."""
    return "KIS_" if profile == "main" else f"KIS_{profile.upper()}_"


def _config_dir_path(override: str | Path | None = None) -> Path:
    """``credentials.json`` 이 있는 디렉터리. ``override`` 가 있으면 그것(``~`` 확장),
    없으면 ``$XDG_CONFIG_HOME/kis-trader`` (else ``~/.config/kis-trader``)."""
    if override is not None:
        return Path(override).expanduser()
    return xdg_config_subdir(_APP_DIR_NAME)


def token_cache_path(override: str | Path | None = None) -> Path:
    """OAuth 토큰 캐시 디렉터리(재생성 가능). ``override`` 가 있으면 그 아래 ``tokens``, 없으면
    ``$XDG_CACHE_HOME/kis-trader/tokens``. 편집 설정(config)과 XDG 규약대로 분리한다. 세션이
    ``config_dir`` 을 줬을 때 토큰 캐시 위치를 이 규칙으로 맞추는 데 쓴다."""
    if override is not None:
        return Path(override).expanduser() / "tokens"
    return xdg_cache_subdir(_APP_DIR_NAME, "tokens")


def _split_account(account: str) -> tuple[str, str]:
    """``"12345678-01"`` -> (종합계좌번호 ``"12345678"``, 상품코드 ``"01"``). 정확히 하이픈 하나로
    나뉘지 않으면(빈 조각·여분 하이픈) fail-closed -- 저장분을 세션이 다시 못 읽는 일이 없도록.
    쓰기(:meth:`KISConfig.save`)와 읽기(세션)가 이 한 계약을 공유한다."""
    cano, _, product_code = account.strip().partition("-")   # 앞뒤 공백은 계좌번호가 아니다
    if not cano or not product_code or "-" in product_code:
        raise KISUsageError(f"계좌번호는 'CANO-상품코드'(예: 12345678-01) 형식이어야 한다: {account!r}")
    return cano, product_code


def _validate_account(account: str) -> None:
    """계좌 형식만 fail-closed 로 검증한다(실제 CANO/상품코드 분해는 세션이 :func:`_split_account` 로).
    분해 결과를 버리는 호출이 아니라 '검증'이라는 의도를 이름으로 드러내기 위한 얇은 래퍼."""
    _split_account(account)


# --- 읽기 (프로필 -> 저장분) --------------------------------------------------

def _scalar_or_raise(profile: str, field_name: str, raw_value: object) -> str | None:
    """파일 원값을 자격증명 문자열로 좁힌다. 문자열/정수만 허용하고 그 밖(불리언·배열 등)은
    형상 오류로 fail-closed -- 무검증 ``str()`` 이 쓰레기 시크릿을 만들지 않게(값은 담지 않는다)."""
    if raw_value is None:
        return None
    if isinstance(raw_value, bool) or not isinstance(raw_value, (str, int)):
        raise KISUsageError(f"credentials.json 의 {profile}.{field_name} 값이 문자열/정수가 아니다.")
    return str(raw_value)


def _load_credentials(directory: Path) -> dict[str, object]:
    """``directory/credentials.json`` 전체를 파싱해 돌려준다(프로필별 객체). 파일이 없으면 빈 dict.
    파일이 있는데 읽거나 파싱할 수 없으면 -- 있는 자격증명을 '없음'으로 오진하지 않도록 --
    :class:`KISUsageError`."""
    path = directory / "credentials.json"
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as err:
        raise KISUsageError(f"{path} 가 있으나 읽을 수 없다.") from err
    if not isinstance(parsed, dict):
        raise KISUsageError(f"{path} 의 최상위가 프로필 객체가 아니다.")
    return parsed


def _profile_section(directory: Path, profile: str) -> dict[str, object]:
    """한 프로필의 저장 섹션(없으면 빈 dict). 섹션이 객체가 아니면 fail-closed."""
    section = _load_credentials(directory).get(profile)
    if section is None:
        return {}
    if not isinstance(section, dict):
        raise KISUsageError(f"credentials.json 의 프로필 {profile!r} 항목이 객체가 아니다.")
    return section


def _resolve_profile_field(field_name: str, *, profile: str, section: dict[str, object]) -> str | None:
    """프로필의 한 필드를 환경변수 -> 파일 섹션 순으로 찾는다. 빈/공백 문자열은 유효한 자격증명이
    아니므로 '없음'으로 취급한다(fail-closed). ``section`` 은 파일을 한 번만 파싱해 넘긴 값."""
    value = os.environ.get(_env_var_prefix(profile) + field_name.upper())
    if value is None:
        value = _scalar_or_raise(profile, field_name, section.get(field_name))
    if value is not None and not value.strip():
        return None
    return value


def _require_profile_field(field_name: str, *, profile: str, section: dict[str, object]) -> str:
    value = _resolve_profile_field(field_name, profile=profile, section=section)
    if value is None:
        raise KISUsageError(
            f"{_env_var_prefix(profile)}{field_name.upper()} 를 찾을 수 없다 -- 환경변수 또는 "
            "credentials.json 에 설정하라(KISConfig(...).save() 로 저장)."
        )
    return value


@dataclass(frozen=True, slots=True)
class ResolvedCredentials:
    """한 프로필에서 읽어낸 자격증명(불변). 세션 생성에 쓰인다. 앱키/시크릿은 실제 값을 담으므로
    ``repr`` 에서 빼(로그/트레이스백에 시크릿이 새지 않게) -- :class:`KISConfig` 와 같은 규율."""

    app_key: str = field(repr=False)
    app_secret: str = field(repr=False)
    account: str | None
    environment: Environment


def resolve_credentials(profile: str = "main", *, config_dir: str | Path | None = None) -> ResolvedCredentials:
    """프로필의 저장된 자격증명을 읽는다(환경변수 -> credentials.json). 앱키/시크릿이 없으면
    :class:`KISUsageError`(변수 이름만 담는다). 계좌번호는 없으면 ``None``(시세만 볼 때). 환경은
    저장값(기본 ``real``)."""
    return _fill_credentials(profile, app_key=None, app_secret=None, account=None,
                             environment=None, config_dir=config_dir)


def _fill_credentials(
    profile: str, *, app_key: str | None, app_secret: str | None, account: str | None,
    environment: Environment | None, config_dir: str | Path | None,
) -> ResolvedCredentials:
    """명시된 값은 그대로 쓰고 ``None`` 인 것만 저장분에서 채운다(부분 해석). 세션이 일부 자격증명만
    직접 넘겼을 때, 실제로 빠진 항목만 파일/env 에서 읽어 -- 사용자가 준 항목을 '없다'고 오도하지
    않도록 -- 채우는 데 쓴다. 파일은 한 번만 파싱한다."""
    _validate_profile_name(profile)
    directory = _config_dir_path(config_dir)
    section = _profile_section(directory, profile)
    resolved_environment = environment if environment is not None \
        else _resolve_profile_field("environment", profile=profile, section=section) or "real"
    if resolved_environment not in _ENVIRONMENTS:
        raise KISUsageError(f"environment 는 {_ENVIRONMENTS} 중 하나여야 한다: {resolved_environment!r}")
    resolved_account = account
    if resolved_account is None:
        resolved_account = _resolve_profile_field("account", profile=profile, section=section)
        if resolved_account is not None:
            _validate_account(resolved_account)   # 형식 검증(fail-closed) -- 세션이 쪼갤 수 있게
    return ResolvedCredentials(
        app_key=app_key if app_key is not None
        else _require_profile_field("app_key", profile=profile, section=section),
        app_secret=app_secret if app_secret is not None
        else _require_profile_field("app_secret", profile=profile, section=section),
        account=resolved_account,
        environment=cast("Environment", resolved_environment),
    )


def resolve_environment(profile: str = "main", *, config_dir: str | Path | None = None) -> Environment:
    """프로필의 접속 환경(실전/모의)만 읽는다(기본 ``real``). 앱키/시크릿 없이도 되므로 CLI 가
    자격증명을 다 갖추기 전에 환경만 알아야 할 때(주문 게이트 등) 쓴다."""
    _validate_profile_name(profile)
    section = _profile_section(_config_dir_path(config_dir), profile)
    value = _resolve_profile_field("environment", profile=profile, section=section) or "real"
    if value not in _ENVIRONMENTS:
        raise KISUsageError(f"{_env_var_prefix(profile)}ENVIRONMENT 는 {_ENVIRONMENTS} 중 하나여야 한다: {value!r}")
    return cast("Environment", value)


# --- 쓰기 (KISConfig.save) ----------------------------------------------------

@dataclass(frozen=True, kw_only=True)
class KISConfig:
    """한 프로필의 자격증명을 담아 :meth:`save` 로 파일에 기록하는 값 객체.

    ``KISConfig(profile="pension_a", app_key=..., app_secret=..., account="87654321-22").save()`` 처럼
    쓴다(``aws configure`` 와 같은 자리). ``profile`` 은 사용자가 붙이는 자유 이름(계좌마다 하나;
    소문자·숫자·밑줄). ``app_key``/``app_secret`` 은 필수(비면 구성 시점에 거부). ``account`` 는
    ``"CANO-상품코드"`` 형식이며 생략 가능(시세만 볼 계좌). ``environment`` 는 실전/모의(기본 ``real``;
    모의는 ``"paper"``). ``config_dir`` 로 저장 위치를 바꾼다(테스트/특수 위치). 모든 인자는 키워드
    전용이라 시크릿을 위치로 잘못 넘길 수 없다. 세션을 열려면
    :class:`~kis_trader.client.KISClient` ``(profile=...)`` 를 쓴다 -- 이 객체는 저장만 한다.
    """

    # 시크릿은 필수이자 repr 제외 -- 이 객체가 값을 담으므로 repr(KISConfig(...))/로깅에 앱키가
    # 새면 안 된다. 빈/반쪽/형식오류는 save 가 아니라 구성 시점에 거부해 '저장은 됐는데 못 읽는'
    # 상태를 애초에 표현 불가능하게 한다.
    app_key: str = field(repr=False)
    app_secret: str = field(repr=False)
    profile: str = "main"
    account: str | None = None
    environment: Environment = "real"
    config_dir: str | Path | None = None

    def __post_init__(self) -> None:
        _validate_profile_name(self.profile)
        if not self.app_key.strip() or not self.app_secret.strip():
            raise KISUsageError("app_key 와 app_secret 은 비어 있을 수 없다.")
        if self.environment not in _ENVIRONMENTS:
            raise KISUsageError(f"environment 는 {_ENVIRONMENTS} 중 하나여야 한다: {self.environment!r}")
        if self.account is not None:
            _validate_account(self.account)

    def save(self) -> Path:
        """이 프로필의 자격증명을 ``credentials.json`` 에 기록하고 그 경로를 돌려준다.

        기존 파일의 다른 프로필은 보존하며 이 프로필 섹션만 갱신(병합)한다. 파일은 소유자만 읽게
        ``0600``, 원자적으로 쓴다(부분 파일/유출 방지).
        """
        entry: dict[str, str] = {
            "app_key": self.app_key, "app_secret": self.app_secret, "environment": self.environment,
        }
        if self.account is not None:
            entry["account"] = self.account

        directory = _config_dir_path(self.config_dir)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "credentials.json"
        data = _read_existing(path)
        data[self.profile] = entry
        atomic_write_bytes(
            path, (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8"), mode=0o600
        )
        return path


def _read_existing(path: Path) -> dict[str, dict[str, object]]:
    """기존 credentials.json 을 읽어 병합 바탕으로 쓴다. 없으면 빈 dict. 있는데 파손이거나 프로필
    섹션이 객체가 아니면 -- 덮어써 남의 프로필을 날리지 않도록 -- :class:`KISUsageError` 로 멈춘다."""
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as err:
        raise KISUsageError(f"{path} 가 있으나 읽을 수 없어 병합할 수 없다.") from err
    if not isinstance(parsed, dict):
        raise KISUsageError(f"{path} 의 최상위가 프로필 객체가 아니라 병합할 수 없다.")
    for name, section in parsed.items():
        if not isinstance(section, dict):
            raise KISUsageError(f"{path} 의 프로필 {name!r} 항목이 객체가 아니라 병합할 수 없다.")
    return parsed


__all__ = ["KISConfig", "ResolvedCredentials", "resolve_credentials", "resolve_environment"]
