"""자격증명을 프로필 단위로 저장(:class:`KISConfig`)하고 세션이 읽어들이는 자리.

두 방향이 있다. **쓰기**는 :class:`KISConfig` 에 값을 담아 :meth:`KISConfig.save` 로
``~/.config/kis-trader/credentials.json`` 에 기록한다(``aws configure`` 와 같은 자리). **읽기**는
:func:`resolve_credentials` 가 프로필로 저장분을 찾아 세션(:class:`~kis_trader.client.KISClient`)에
넘긴다 -- 보통 ``KISClient(profile=...)`` 가 내부에서 부른다.

읽기 순서: 환경변수 -> ``credentials.json`` -> ``config.toml``. 값은 어디에도 출력하지 않고,
누락/형상 오류를 알리는 예외에는 **변수 이름만** 담는다.

**프로필**은 한 계좌 묶음(앱키/시크릿/계좌번호)과 그 접속 환경을 이름 하나로 가리킨다. 각 프로필은
자기 접두어로 변수를 읽고 쓴다 -- ``paper`` 는 ``KIS_PAPER_APP_KEY`` / ``KIS_PAPER_APP_SECRET`` /
``KIS_PAPER_CANO`` / ``KIS_PAPER_ACNT_PRDT_CD``. 환경(실전/모의)은 프로필이 정한다(모의계좌만 모의).

설정 위치는 XDG 규약을 따라 세 OS 공통이다: 편집 대상(``credentials.json`` 0600, ``config.toml``)은
``$XDG_CONFIG_HOME/kis-trader`` (없으면 ``~/.config/kis-trader``), 재생성 가능한 OAuth 토큰 캐시는
분리해 ``$XDG_CACHE_HOME/kis-trader/tokens``. (Windows 는 파일 권한을 POSIX 비트가 아니라 ACL 로
관리하므로 ``0600`` 이 그대로 강제되진 않고 사용자 프로필 폴더 접근권한에 의존한다.)
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal, get_args

from ._internal._fsutil import atomic_write_bytes, xdg_cache_subdir, xdg_config_subdir
from .errors import KISUsageError

if TYPE_CHECKING:
    from .transport import Environment

#: XDG 하위 디렉터리 이름(``~/.config/kis-trader`` / ``~/.cache/kis-trader``).
_APP_DIR_NAME = "kis-trader"

#: 프로필 이름 -- 자격증명 묶음 하나이자 그 접속 환경. ``main`` 은 접두어 없는 실전 주계좌(위탁/종합).
Profile = Literal["main", "paper", "isa", "irp", "pension"]

#: 프로필 -> 환경변수/파일 키 접두어. 접두어에 ``APP_KEY``/``APP_SECRET``/``CANO``/``ACNT_PRDT_CD`` 를 붙인다.
_PROFILE_PREFIX: dict[str, str] = {
    "main":    "KIS_",
    "paper":   "KIS_PAPER_",
    "isa":     "KIS_ISA_",
    "irp":     "KIS_IRP_",
    "pension": "KIS_PENSION_",
}

#: 모의(paper) 환경에 접속하는 프로필. 나머지는 전부 실전(real).
_PAPER_PROFILES: frozenset[str] = frozenset({"paper"})


def environment_for_profile(profile: Profile) -> Environment:
    """프로필의 접속 환경(실전/모의). 프로필->환경 규칙을 한 곳에서만 정의하기 위한 진입점.
    알 수 없는 프로필이면 :class:`KISUsageError`."""
    if profile not in _PROFILE_PREFIX:
        raise KISUsageError(
            f"알 수 없는 프로필: {profile!r}. 가능한 값: {', '.join(get_args(Profile))}"
        )
    return "paper" if profile in _PAPER_PROFILES else "real"


def _config_dir_path(override: str | Path | None = None) -> Path:
    """``credentials.json`` / ``config.toml`` 이 있는 디렉터리. ``override`` 가 있으면 그것(``~`` 확장),
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
    cano, _, product_code = account.partition("-")
    if not cano or not product_code or "-" in product_code:
        raise KISUsageError(f"계좌번호는 'CANO-상품코드'(예: 12345678-01) 형식이어야 한다: {account!r}")
    return cano, product_code


# --- 읽기 (프로필 -> 저장분) --------------------------------------------------

def _scalar_or_raise(path: Path, setting_name: str, raw_value: object) -> str | None:
    """설정 파일 원값을 자격증명 문자열로 좁힌다. 문자열/정수만 허용하고 그 밖(불리언·배열 등)은
    형상 오류로 fail-closed -- 무검증 ``str()`` 이 쓰레기 시크릿을 만들지 않게(값은 담지 않는다)."""
    if raw_value is None:
        return None
    if isinstance(raw_value, bool) or not isinstance(raw_value, (str, int)):
        raise KISUsageError(f"{path} 의 {setting_name} 값이 문자열/정수가 아니다.")
    return str(raw_value)


def _from_credentials(directory: Path, name: str) -> str | None:
    """``directory/credentials.json`` 의 ``name`` 값. 파일이 없거나 키가 없으면 ``None``. 파일이 있는데
    읽거나 파싱할 수 없으면 -- 있는 자격증명을 '없음'으로 오진하지 않도록 -- :class:`KISUsageError`."""
    path = directory / "credentials.json"
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as err:
        raise KISUsageError(f"{path} 가 있으나 읽을 수 없다.") from err
    if not isinstance(parsed, dict):
        raise KISUsageError(f"{path} 의 최상위가 JSON 객체가 아니다.")
    return _scalar_or_raise(path, name, parsed.get(name))


def _from_config_toml(directory: Path, name: str) -> str | None:
    """``directory/config.toml`` 의 ``name`` 값(낮은 우선순위 폴백). 없으면 ``None``, 파손이면 raise."""
    import tomllib  # Python 3.11+ 표준 라이브러리

    path = directory / "config.toml"
    try:
        with path.open("rb") as handle:
            parsed = tomllib.load(handle)
    except FileNotFoundError:
        return None
    except (OSError, tomllib.TOMLDecodeError) as err:
        raise KISUsageError(f"{path} 가 있으나 읽을 수 없다.") from err
    return _scalar_or_raise(path, name, parsed.get(name))


def _lookup(setting_name: str, *, directory: Path) -> str | None:
    """``setting_name`` 을 환경변수 -> credentials.json -> config.toml 순으로 찾는다. 빈/공백 문자열은
    유효한 자격증명이 아니므로 '없음'으로 취급한다(fail-closed)."""
    value = os.environ.get(setting_name)
    if value is None:
        value = _from_credentials(directory, setting_name)
    if value is None:
        value = _from_config_toml(directory, setting_name)
    if value is not None and not value.strip():
        return None
    return value


def _require(setting_name: str, *, directory: Path) -> str:
    value = _lookup(setting_name, directory=directory)
    if value is None:
        raise KISUsageError(
            f"{setting_name} 를 찾을 수 없다 -- 환경변수 또는 {directory} 의 "
            "credentials.json/config.toml 에 설정하라(KISConfig(...).save() 로 저장)."
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


def resolve_credentials(profile: Profile = "main", *, config_dir: str | Path | None = None) -> ResolvedCredentials:
    """프로필의 저장된 자격증명을 읽는다(환경변수 -> credentials.json -> config.toml). 앱키/시크릿이
    없으면 :class:`KISUsageError`(변수 이름만 담는다). 계좌번호는 없으면 ``None``(시세만 볼 때)."""
    return _fill_credentials(profile, app_key=None, app_secret=None, account=None, config_dir=config_dir)


def _fill_credentials(
    profile: Profile, *, app_key: str | None, app_secret: str | None, account: str | None,
    config_dir: str | Path | None,
) -> ResolvedCredentials:
    """명시된 값은 그대로 쓰고 ``None`` 인 것만 저장분에서 채운다(부분 해석). 세션이 일부 자격증명만
    직접 넘겼을 때, 실제로 빠진 항목만 파일/env 에서 읽어 -- 사용자가 준 항목을 '없다'고 오도하지
    않도록 -- 채우는 데 쓴다. 전부 명시했으면 파일을 읽지 않는다."""
    if profile not in _PROFILE_PREFIX:
        raise KISUsageError(
            f"알 수 없는 프로필: {profile!r}. 가능한 값: {', '.join(get_args(Profile))}"
        )
    prefix = _PROFILE_PREFIX[profile]
    directory = _config_dir_path(config_dir)
    return ResolvedCredentials(
        app_key=app_key if app_key is not None else _require(prefix + "APP_KEY", directory=directory),
        app_secret=app_secret if app_secret is not None else _require(prefix + "APP_SECRET", directory=directory),
        account=account if account is not None else _resolve_account(prefix, directory=directory),
        environment=environment_for_profile(profile),
    )


def _resolve_account(prefix: str, *, directory: Path) -> str | None:
    cano = _lookup(prefix + "CANO", directory=directory)
    product_code = _lookup(prefix + "ACNT_PRDT_CD", directory=directory)
    if cano is None:
        return None
    if product_code is None:
        raise KISUsageError(
            f"{prefix}CANO 는 있으나 {prefix}ACNT_PRDT_CD 가 없다 -- 상품계좌종류를 함께 설정하라."
        )
    return f"{cano}-{product_code}"


# --- 쓰기 (KISConfig.save) ----------------------------------------------------

@dataclass(frozen=True, kw_only=True)
class KISConfig:
    """한 프로필의 자격증명을 담아 :meth:`save` 로 파일에 기록하는 값 객체.

    ``KISConfig(profile="main", app_key=..., app_secret=..., account="12345678-01").save()`` 처럼 쓴다
    (``aws configure`` 와 같은 자리). ``app_key``/``app_secret`` 은 필수(비면 구성 시점에 거부).
    ``profile`` 이 어느 키 접두어로 저장할지 정한다. ``account`` 는 ``"CANO-상품코드"`` 형식이며 생략
    가능(시세만 볼 계좌). ``config_dir`` 로 저장 위치를 바꾼다(테스트/특수 위치; 기본은 표준 XDG 경로).
    모든 인자는 키워드 전용이라 시크릿을 위치로 잘못 넘길 수 없다. 세션을 열려면
    :class:`~kis_trader.client.KISClient` ``(profile=...)`` 를 쓴다 -- 이 객체는 자격증명을 저장할 뿐
    API 를 호출하지 않는다.
    """

    # 시크릿은 필수이자 repr 제외 -- 이 객체가 값을 담으므로 repr(KISConfig(...))/로깅에 앱키가
    # 새면 안 된다(예외도 이름만 담는 규율과 같은 취지). 빈/반쪽/형식오류는 save 가 아니라
    # 구성 시점에 거부해 '저장은 됐는데 못 읽는' 상태를 애초에 표현 불가능하게 한다.
    app_key: str = field(repr=False)
    app_secret: str = field(repr=False)
    profile: Profile = "main"
    account: str | None = None
    config_dir: str | Path | None = None

    def __post_init__(self) -> None:
        if self.profile not in _PROFILE_PREFIX:
            raise KISUsageError(
                f"알 수 없는 프로필: {self.profile!r}. 가능한 값: {', '.join(get_args(Profile))}"
            )
        if not self.app_key.strip() or not self.app_secret.strip():
            raise KISUsageError("app_key 와 app_secret 은 비어 있을 수 없다.")
        if self.account is not None:
            _split_account(self.account)   # 형식 검증(fail-closed) -- 세션이 읽을 수 있게

    def save(self) -> Path:
        """이 프로필의 자격증명을 ``credentials.json`` 에 기록하고 그 경로를 돌려준다.

        기존 파일의 다른 프로필 항목은 보존하며 이 프로필의 키만 갱신(병합)한다. 파일은 소유자만
        읽게 ``0600``, 원자적으로 쓴다(부분 파일/유출 방지). ``account`` 를 주면 ``CANO``/``ACNT_PRDT_CD``
        로 분해해 함께 기록한다(형식은 구성 시점에 검증됨).
        """
        prefix = _PROFILE_PREFIX[self.profile]
        entries = {prefix + "APP_KEY": self.app_key, prefix + "APP_SECRET": self.app_secret}
        if self.account is not None:
            cano, product_code = _split_account(self.account)
            entries[prefix + "CANO"] = cano
            entries[prefix + "ACNT_PRDT_CD"] = product_code

        directory = _config_dir_path(self.config_dir)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "credentials.json"
        merged = _read_existing(path)
        merged.update(entries)  # 이 프로필 키만 갱신 -- 다른 프로필은 보존
        atomic_write_bytes(
            path, (json.dumps(merged, ensure_ascii=False, indent=2) + "\n").encode("utf-8"), mode=0o600
        )
        return path


def _read_existing(path: Path) -> dict[str, str]:
    """기존 credentials.json 을 읽어 병합 바탕으로 쓴다. 없으면 빈 dict. 있는데 파손이면 -- 덮어써
    남의 프로필을 날리지 않도록 -- :class:`KISUsageError` 로 멈춘다."""
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as err:
        raise KISUsageError(f"{path} 가 있으나 읽을 수 없어 병합할 수 없다.") from err
    if not isinstance(parsed, dict):
        raise KISUsageError(f"{path} 의 최상위가 JSON 객체가 아니라 병합할 수 없다.")
    # 무검증 str() 대신 읽기 경로와 같은 좁힘을 적용 -- 손상된 남의 프로필 값을 조용히 쓰레기
    # 문자열로 바꿔 되쓰지 않도록 fail-closed(불리언/배열/null 이면 멈춘다).
    merged: dict[str, str] = {}
    for key, raw_value in parsed.items():
        value = _scalar_or_raise(path, str(key), raw_value)
        if value is None:
            raise KISUsageError(f"{path} 의 {key} 값이 비어 있어 병합할 수 없다.")
        merged[str(key)] = value
    return merged


__all__ = ["KISConfig", "Profile", "ResolvedCredentials", "environment_for_profile", "resolve_credentials"]
