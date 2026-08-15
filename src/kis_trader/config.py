"""자격증명·계좌·환경을 프로필 단위로 해석하는 설정 정책값.

세션은 두 가지로 열 수 있다. 저수준은 명시 인자
(:class:`~kis_trader.client.KISClient` ``(app_key=..., app_secret=...)``)이고,
편의 경로는 이 :class:`KISConfig` 를 :meth:`~kis_trader.client.KISClient.from_config`
에 넘겨 환경변수·설정파일에서 해석하는 것이다.

해석 순서: ``resolver`` 를 주면 그 콜백이 전권을 가진다(폴백 없음 -- 이름을 못 찾아 ``None``
(또는 빈 문자열)을 돌려주면 '없음'으로 확정한다). ``resolver`` 가 없으면 환경변수 ->
``credentials.json`` -> ``config.toml`` 순으로 찾는다. 빈/공백 문자열은 어느 출처에서 왔든 유효한
자격증명이 아니므로 '없음'으로 취급한다(fail-closed).

설정 파일은 XDG 규약을 따른다: 편집 대상(``credentials.json`` 0600, ``config.toml``)은
``$XDG_CONFIG_HOME/kis-trader`` (없으면 ``~/.config/kis-trader``), 재생성 가능한 OAuth 토큰
캐시는 그와 분리해 ``$XDG_CACHE_HOME/kis-trader/tokens`` 에 둔다.

**프로필**은 한 계좌 묶음(앱키/시크릿/계좌번호)과 그 접속 환경을 이름 하나로 가리킨다. 각
프로필은 자기 접두어로 변수를 읽는다 -- 예: ``paper`` 는 ``KIS_PAPER_APP_KEY`` /
``KIS_PAPER_APP_SECRET`` / ``KIS_PAPER_CANO`` / ``KIS_PAPER_ACNT_PRDT_CD``. 환경(실전/모의)은
프로필이 정한다(모의계좌는 모의, 나머지는 실전).

값은 어디에도 출력하지 않는다. 누락/형상 오류를 알리는 예외에는 **변수 이름만** 담고 값은
담지 않는다.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal, get_args

from ._internal._fsutil import xdg_cache_subdir, xdg_config_subdir
from .errors import KISUsageError

if TYPE_CHECKING:
    from .transport import Environment

#: XDG 하위 디렉터리 이름(``~/.config/kis-trader`` / ``~/.cache/kis-trader``).
_APP_DIR_NAME = "kis-trader"

#: 프로필 이름 -- 자격증명 묶음 하나이자 그 접속 환경. ``main`` 은 접두어 없는 실전 주계좌.
Profile = Literal["main", "paper", "isa", "irp", "pension"]

#: 프로필 -> 환경변수 접두어. 접두어에 ``APP_KEY``/``APP_SECRET``/``CANO``/``ACNT_PRDT_CD`` 를 붙인다.
_PROFILE_PREFIX: dict[str, str] = {
    "main":    "KIS_",
    "paper":   "KIS_PAPER_",
    "isa":     "KIS_ISA_",
    "irp":     "KIS_IRP_",
    "pension": "KIS_PENSION_",
}

#: 모의(paper) 환경에 접속하는 프로필. 나머지는 전부 실전(real). 환경을 표가 아니라 규칙으로 두어
#: 프로필 추가 시 접두어 표(위)만 늘리면 되고 두 표가 어긋날 여지를 없앤다.
_PAPER_PROFILES: frozenset[str] = frozenset({"paper"})


def environment_for_profile(profile: str) -> Environment:
    """프로필의 접속 환경(실전/모의). 프로필->환경 규칙을 한 곳에서만 정의하기 위한 진입점 --
    CLI 와 :class:`KISConfig` 가 이 함수를 공유한다."""
    if profile not in _PROFILE_PREFIX:
        raise KISUsageError(
            f"알 수 없는 프로필: {profile!r}. 가능한 값: {', '.join(get_args(Profile))}"
        )
    return "paper" if profile in _PAPER_PROFILES else "real"


def _scalar_or_raise(path: Path, name: str, value: object) -> str | None:
    """설정 파일에서 읽은 원값을 자격증명 문자열로 좁힌다. ``None`` 이면(키 없음) ``None``.
    문자열/정수는 문자열로, 그 밖(불리언·배열·테이블 등)은 형상 오류로 fail-closed -- 무검증
    ``str()`` 이 ``true`` -> ``"True"`` 같은 쓰레기 시크릿을 만들지 않게 한다(값은 담지 않는다)."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise KISUsageError(f"{path} 의 {name} 값이 문자열/정수가 아니다.")
    return str(value)


def _from_credentials(directory: Path, name: str) -> str | None:
    """``directory/credentials.json`` 의 ``name`` 값(문자열). 파일이 없거나 키가 없으면 ``None``.
    파일이 있는데 읽거나 파싱할 수 없으면 -- 있는 자격증명을 '없음'으로 오진하지 않도록 --
    :class:`KISUsageError` 로 닫는다."""
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
    """``directory/config.toml`` 의 ``name`` 값(문자열). 파일이 없거나 키가 없으면 ``None``.
    파일이 있는데 읽거나 파싱할 수 없으면 :class:`KISUsageError` 로 닫는다(위와 같은 이유)."""
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


@dataclass(frozen=True, kw_only=True)
class KISConfig:
    """자격증명·계좌·환경 해석 정책(불변). :meth:`~kis_trader.client.KISClient.from_config` 에 넘긴다.

    ``KISConfig()`` 는 단독으로 동작한다 -- 환경변수, 그다음 :meth:`config_dir` 아래의
    ``credentials.json`` / ``config.toml`` 순으로 읽는다(패키지가 컨슈머를 몰라도 됨). ``resolver``
    를 주면 모든 조회를 호스트 콜백으로 넘긴다(자기 시크릿 저장소를 쓰는 앱; 이때 폴백 없음).
    ``config_dir_override`` 를 주면 설정 파일과 토큰 캐시를 그 한 경로로 리다이렉트한다(테스트/격리).
    ``profile`` 이 어느 변수 접두어와 환경을 쓸지 정한다. 값은 불변이라 정책을 바꾸려면 새로 만든다.
    """

    profile: Profile = "main"
    # 호스트 콜백은 repr 에서 뺀다 -- 소비자가 partial(lookup, token="...") 같은 걸 넘기면
    # 바인딩된 시크릿이 repr(KISConfig(...)) 로 샐 수 있다(방어적).
    resolver: Callable[[str], str | None] | None = field(default=None, repr=False)
    config_dir_override: str | Path | None = None

    def __post_init__(self) -> None:
        if self.profile not in _PROFILE_PREFIX:
            raise KISUsageError(
                f"알 수 없는 프로필: {self.profile!r}. 가능한 값: {', '.join(get_args(Profile))}"
            )

    @property
    def environment(self) -> Environment:
        """이 프로필의 접속 환경(실전/모의)."""
        return environment_for_profile(self.profile)

    def config_dir(self) -> Path:
        """``credentials.json`` / ``config.toml`` 이 있는 디렉터리: ``config_dir_override`` 가
        있으면 그것(``~`` 확장), 없으면 ``$XDG_CONFIG_HOME/kis-trader`` (else ``~/.config/kis-trader``)."""
        if self.config_dir_override is not None:
            return Path(self.config_dir_override).expanduser()
        return xdg_config_subdir(_APP_DIR_NAME)

    def cache_dir(self) -> Path:
        """OAuth 토큰 캐시 디렉터리(재생성 가능): ``config_dir_override`` 가 있으면 그 아래
        ``tokens``, 없으면 ``$XDG_CACHE_HOME/kis-trader/tokens`` (else ``~/.cache/kis-trader/tokens``).
        기본 경로는 XDG 규약대로 편집 설정(config)과 재생성 캐시(cache)를 분리한다. ``config_dir_override``
        는 '전부 이 한 경로로'라는 격리 계약이라 그 아래에 캐시를 함께 두는 것이 의도된 동작이다."""
        if self.config_dir_override is not None:
            return Path(self.config_dir_override).expanduser() / "tokens"
        return xdg_cache_subdir(_APP_DIR_NAME, "tokens")

    def _lookup(self, setting_name: str) -> str | None:
        """``setting_name`` 을 resolver -> 환경변수 -> credentials.json -> config.toml 순으로 찾는다.
        resolver 가 있으면 그것이 전권(폴백 없음). 빈/공백 문자열은 '없음'으로 취급한다."""
        if self.resolver is not None:
            value = self.resolver(setting_name)
        else:
            value = os.environ.get(setting_name)
            if value is None:
                directory = self.config_dir()
                value = _from_credentials(directory, setting_name)
                if value is None:
                    value = _from_config_toml(directory, setting_name)
        # 빈/공백 문자열 자격증명은 유효하지 않으므로 '없음'으로 확정(fail-closed).
        if value is not None and not value.strip():
            return None
        return value

    def _require(self, setting_name: str) -> str:
        value = self._lookup(setting_name)
        if value is None:
            raise KISUsageError(
                f"{setting_name} 를 찾을 수 없다 -- 환경변수 또는 {self.config_dir()} 의 "
                "credentials.json/config.toml 에 설정하라."
            )
        return value

    def app_key(self) -> str:
        """이 프로필의 앱키. 없으면 :class:`KISUsageError`(변수 이름만 담는다)."""
        return self._require(_PROFILE_PREFIX[self.profile] + "APP_KEY")

    def app_secret(self) -> str:
        """이 프로필의 앱시크릿. 없으면 :class:`KISUsageError`."""
        return self._require(_PROFILE_PREFIX[self.profile] + "APP_SECRET")

    def account(self) -> str | None:
        """이 프로필의 계좌번호 ``CANO-ACNT_PRDT_CD`` 또는 ``None``(계좌 없이 시세만 볼 때).

        둘 다 없으면 ``None``. 종합계좌(CANO)만 있고 상품코드가 없으면 형상 오류로 보고
        fail-closed(:class:`KISUsageError`) -- 상품코드를 임의값으로 지어내지 않는다."""
        prefix = _PROFILE_PREFIX[self.profile]
        cano = self._lookup(prefix + "CANO")
        product_code = self._lookup(prefix + "ACNT_PRDT_CD")
        if cano is None:
            return None
        if product_code is None:
            raise KISUsageError(
                f"{prefix}CANO 는 있으나 {prefix}ACNT_PRDT_CD 가 없다 -- 상품계좌종류를 함께 설정하라."
            )
        return f"{cano}-{product_code}"


__all__ = ["KISConfig", "Profile", "environment_for_profile"]
