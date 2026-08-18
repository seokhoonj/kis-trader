"""자격증명을 프로필 단위로 저장(:class:`KISConfig`)하고 세션이 읽어들이는 자리.

두 방향이 있다. **쓰기**는 :class:`KISConfig` 에 값을 담아 :meth:`KISConfig.save` 로
``~/.config/kis-trader/credentials.json`` 에 기록한다(``aws configure`` 와 같은 자리). **읽기**는
:func:`resolve_credentials` 가 프로필로 저장분을 찾아 세션(:class:`~kis_trader.client.KISClient`)에
넘긴다 -- 보통 ``KISClient(profile=...)`` 가 내부에서 부른다.

**프로필은 사용자가 이름 붙이는 자유 문자열이다.** 앱키/시크릿은 종합계좌번호(CANO) 단위라 계좌마다
따로이고 같은 유형 계좌도 여럿일 수 있으므로(연금저축 2개 등), "계좌 하나 = 프로필 하나"로 다룬다.
각 프로필은 앱키·시크릿·계좌번호와 **접속 환경(실전/모의)**을 갖는다. 이름을 지정하지 않으면 기본
프로필을 연다 -- ``KIS_DEFAULT_PROFILE`` 환경변수 > ``credentials.json`` 최상위 ``"default_profile"`` 마커
(:meth:`KISConfig.set_default`) > 첫 항목(삽입 순서) > ``"main"``(폴백) 순으로 정한다. 환경은 이름이
아니라 프로필에 저장된 값이 정한다(기본 ``real``; 모의는 ``environment="paper"``).

``credentials.json`` 은 공유 최상위 필드(``hts_id``/``default_profile``, 예약 키)와 프로필별 중첩
객체를 함께 담는다::

    {
      "hts_id":    "your_hts_id",
      "main":      {"app_key": "...", "app_secret": "...", "account": "12345678-01", "environment": "real"},
      "pension":   {"app_key": "...", "app_secret": "...", "account": "87654321-22", "environment": "real"},
      "paper":     {"app_key": "...", "app_secret": "...", "account": "...",         "environment": "paper"}
    }

``hts_id`` 는 조건검색·관심종목 조회의 ``user_id`` 로 쓰는 HTS 로그인 아이디다(사용자당 하나,
모든 프로필 공유; 인증엔 안 씀). :meth:`KISConfig.set_hts_id` 로 기록하거나 손 편집한다.

읽기 순서: 환경변수 -> ``credentials.json``. 환경변수는 프로필 접두어를 붙인 평평한 키를 쓴다
(``main`` -> ``KIS_APP_KEY`` / ``KIS_ACCOUNT`` / ``KIS_ENVIRONMENT``; ``pension`` ->
``KIS_PENSION_APP_KEY`` ...). **앱키·시크릿 값은 어디에도 출력하지 않는다**(화면·로그·예외 모두);
누락 예외에는 변수 이름만 담는다. (형식 오류 진단을 위해 계좌·프로필 문자열은 예외 메시지에 담을 수
있다 -- 이들은 시크릿이 아니다.)

설정 위치는 XDG 규약을 따라 세 OS 공통이다: 편집 대상(``credentials.json`` 0600)은
``$XDG_CONFIG_HOME/kis-trader`` (없으면 ``~/.config/kis-trader``), 재생성 가능한 OAuth 토큰 캐시는
분리해 ``$XDG_CACHE_HOME/kis-trader/tokens``. (Windows 는 파일 권한을 POSIX 비트가 아니라 ACL 로
관리하므로 ``0600`` 이 그대로 강제되진 않고 사용자 프로필 폴더 접근권한에 의존한다.)
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ._internal._fsutil import (
    atomic_write_bytes,
    xdg_cache_subdir,
    xdg_config_subdir,
    xdg_state_subdir,
)
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

#: profile 미지정 시 기본 프로필을 못박는 환경변수(빈/공백 값은 미설정 취급).
_DEFAULT_PROFILE_ENV_VAR = "KIS_DEFAULT_PROFILE"

#: credentials.json 최상위에서 기본 프로필 이름을 담는 예약 키(프로필 이름으로는 못 쓴다).
#: 프로필 섹션은 객체, 이 마커는 문자열이라 값 타입으로 구분한다.
_DEFAULT_MARKER_KEY = "default_profile"
#: HTS 로그인 아이디를 담는 예약 최상위 키. HTS 아이디는 사용자당 하나라 프로필별이 아니라
#: 최상위에 한 번 둔다(모든 프로필이 공유). 인증엔 안 쓰이고 조건검색·관심종목의 user_id 로 쓴다.
_HTS_ID_KEY = "hts_id"
_HTS_ID_ENV_VAR = "KIS_HTS_ID"
#: 예약 최상위 키를 파일에서 배치하는 순서(맨 위, 이 순서대로). 멤버십 검사는 아래 frozenset.
_RESERVED_TOP_LEVEL_ORDER = (_HTS_ID_KEY, _DEFAULT_MARKER_KEY)
#: 프로필 이름으로 쓸 수 없는 예약 최상위 키.
_RESERVED_TOP_LEVEL_KEYS = frozenset(_RESERVED_TOP_LEVEL_ORDER)


def _order_top_level(data: dict[str, object]) -> dict[str, object]:
    """예약 최상위 키(hts_id, default_profile)를 정해진 순서로 맨 위에 모으고, 그 뒤 프로필의
    삽입 순서를 보존한다 -- 파일 레이아웃을 예측 가능하게(마커가 프로필 사이에 흩어지지 않게)."""
    ordered = {k: data[k] for k in _RESERVED_TOP_LEVEL_ORDER if k in data}
    for name, value in data.items():
        if name not in ordered:
            ordered[name] = value
    return ordered


def _write_credentials(directory: Path, data: dict[str, object]) -> Path:
    """병합된 credentials 를 예약키 순서로 정렬해 ``directory/credentials.json`` 에 원자적(0600)으로
    쓰고 경로를 돌려준다. 세 writer(:meth:`KISConfig.save`/:meth:`KISConfig.set_default`/
    :meth:`KISConfig.set_hts_id`)가 공유한다 -- 병합(어느 키를 바꿀지)은 각 호출부가, 정렬·권한·
    원자성은 여기서 한 번만."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "credentials.json"
    atomic_write_bytes(
        path, (json.dumps(_order_top_level(data), ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        mode=0o600,
    )
    return path


def _validate_profile_name(profile: str) -> None:
    if not _PROFILE_NAME.fullmatch(profile):
        raise KISUsageError(
            f"프로필 이름은 소문자/숫자/밑줄만 쓸 수 있다(환경변수 키로 쓰이기 때문): {profile!r}"
        )
    if profile in _RESERVED_TOP_LEVEL_KEYS:
        raise KISUsageError(
            f"{profile!r} 은 예약된 최상위 키라 프로필 이름으로 쓸 수 없다."
        )


def _env_var_prefix(profile: str) -> str:
    """프로필 -> 환경변수 키 접두어. 기본 프로필 ``main`` 은 접두어 없이(``KIS_``), 나머지는
    ``KIS_<이름대문자>_``. 예: ``pension`` -> ``KIS_PENSION_``."""
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


def order_store_path(*, account: str, environment: Environment, override: str | Path | None = None) -> Path:
    """주문 dedup 저장소(:class:`~kis_trader.store.OrderStore`) 파일 경로. 이 저장소는 **재생성 불가한
    영속 상태**다 -- 잃으면 프로세스 교차 dedup 이 무너져 이중 제출 위험이 생기므로, 재생성 가능한 토큰
    캐시와 달리 XDG **state** 트리 아래 ``orders`` 에 둔다(캐시 청소가 이 상태를 지우지 않도록).
    **계좌·환경마다 파일을 가른다** -- 서로 다른 계좌/환경의 주문 dedup 이 한 파일에서 섞이지 않도록.
    ``override`` 가 있으면 그 아래 ``orders``, 없으면 ``$XDG_STATE_HOME/kis-trader/orders``
    (없으면 ``~/.local/state/kis-trader/orders``).

    파일명은 ``<환경>-<해시>.json`` 꼴이다. 계좌번호는 파일명에 그대로 노출하지 않고 SHA-256 앞
    16자리로 해시한다(파일 목록에 계좌번호가 드러나지 않게).

    이전 버전은 이 저장소를 캐시 트리(``$XDG_CACHE_HOME/kis-trader/orders``)에 뒀다. 기본 위치를
    쓸 때(``override`` 없음) 옛 캐시 위치에 같은 이름의 파일이 있고 새 state 위치엔 없으면, 처음 쓸 때
    새 위치로 옮겨(read-old-then-write-new) dedup 연속성을 지킨다 -- 기존 저장소를 잃지 않는다."""
    digest = hashlib.sha256(account.encode()).hexdigest()[:16]
    filename = f"{environment}-{digest}.json"
    if override is not None:
        return Path(override).expanduser() / "orders" / filename
    new_path = xdg_state_subdir(_APP_DIR_NAME, "orders") / filename
    old_path = xdg_cache_subdir(_APP_DIR_NAME, "orders") / filename
    if old_path.exists() and not new_path.exists():
        _migrate_order_store(old_path, new_path)
    return new_path


def _migrate_order_store(old_path: Path, new_path: Path) -> None:
    """옛 캐시 위치의 주문 dedup 저장소를 새 state 위치로 한 번 옮긴다. 재생성 불가한 영속 상태라
    위치 이전에서 잃으면 안 되므로 복제가 아니라 이동한다 -- 같은 파일이 두 곳에 남아 서로 다른
    프로세스가 갈라진 dedup 을 보지 않도록. 캐시와 state 가 다른 파일시스템이면 rename 대신 복사 후
    원본을 지운다(:func:`shutil.move` 가 두 경우를 모두 처리)."""
    new_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.move(str(old_path), str(new_path))
    except (FileNotFoundError, shutil.Error):
        # 동시 첫 실행에서 다른 프로세스가 이미 옮겼으면 원본이 사라지거나 대상이 먼저 생긴다.
        # 대상이 존재하면 이전은 완료된 것이니 조용히 넘어가 크래시를 막는다(중복 이동은 무해).
        if not new_path.exists():
            raise


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


def _select_profile_section(loaded: dict[str, object], profile: str) -> dict[str, object]:
    """이미 파싱한 credentials 에서 한 프로필의 섹션(없으면 빈 dict). 객체가 아니면 fail-closed.
    ``loaded`` 를 넘겨받아 resolution 당 파일을 한 번만 파싱하게 한다."""
    section = loaded.get(profile)
    if section is None:
        return {}
    if not isinstance(section, dict):
        raise KISUsageError(f"credentials.json 의 프로필 {profile!r} 항목이 객체가 아니다.")
    return cast("dict[str, object]", section)


def _resolve_default_profile_name(loaded: dict[str, object]) -> str:
    """``profile`` 미지정 시 열 기본 프로필 이름. 해석 순서: ``KIS_DEFAULT_PROFILE`` 환경변수
    (빈/공백은 미설정) > ``credentials.json`` 최상위 ``"default_profile"`` 마커(:meth:`KISConfig.set_default` 가
    기록) > 첫 프로필 항목(삽입 순서, ``"default_profile"`` 메타키는 제외) > ``"main"``(파일도 env 도 없을 때
    폴백 -- 접두어 없는 ``KIS_APP_KEY`` 로 여는 이름). 이름 형식 검증은 하위에서 한다."""
    override = os.environ.get(_DEFAULT_PROFILE_ENV_VAR)
    if override is not None and override.strip():
        return override.strip()
    marker = loaded.get(_DEFAULT_MARKER_KEY)
    if isinstance(marker, str) and marker.strip():
        return marker.strip()
    return next((name for name in loaded if name not in _RESERVED_TOP_LEVEL_KEYS), "main")


def _resolve_hts_id(loaded: dict[str, object]) -> str | None:
    """HTS 로그인 아이디. ``KIS_HTS_ID`` 환경변수 > ``credentials.json`` 최상위 ``"hts_id"`` 키.
    사용자당 하나라 프로필별이 아니라 최상위에서 한 번 읽는다(빈/공백은 미설정)."""
    env = os.environ.get(_HTS_ID_ENV_VAR)
    if env is not None and env.strip():
        return env.strip()
    value = loaded.get(_HTS_ID_KEY)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


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
    #: HTS 로그인 아이디 -- 인증엔 안 쓰이나 조건검색·관심종목(user_id 필요) 조회에 계정 식별로 쓴다.
    hts_id: str | None = None


def resolve_credentials(profile: str | None = None, *, config_dir: str | Path | None = None) -> ResolvedCredentials:
    """프로필의 저장된 자격증명을 읽는다(환경변수 -> credentials.json). ``profile`` 이 ``None`` 이면
    기본 프로필로 해석한다(:func:`_resolve_default_profile_name`). 앱키/시크릿이 없으면 :class:`KISUsageError`
    (변수 이름만 담는다). 계좌번호는 없으면 ``None``(시세만 볼 때). 환경은 저장값(기본 ``real``)."""
    return _fill_credentials(profile, app_key=None, app_secret=None, account=None,
                             environment=None, config_dir=config_dir)


def _fill_credentials(
    profile: str | None, *, app_key: str | None, app_secret: str | None, account: str | None,
    environment: Environment | None, config_dir: str | Path | None, hts_id: str | None = None,
) -> ResolvedCredentials:
    """명시된 값은 그대로 쓰고 ``None`` 인 것만 저장분에서 채운다(부분 해석). 세션이 일부 자격증명만
    직접 넘겼을 때, 실제로 빠진 항목만 파일/env 에서 읽어 -- 사용자가 준 항목을 '없다'고 오도하지
    않도록 -- 채우는 데 쓴다. 파일은 한 번만 파싱한다. ``profile`` 이 ``None`` 이면 기본 프로필로 해석한다."""
    if profile is not None:
        _validate_profile_name(profile)          # 명시 이름은 파일 읽기 전에 검증(기존 순서 보존)
    directory = _config_dir_path(config_dir)
    loaded = _load_credentials(directory)
    if profile is None:
        profile = _resolve_default_profile_name(loaded)
        _validate_profile_name(profile)
    section = _select_profile_section(loaded, profile)
    resolved_environment = environment if environment is not None \
        else _resolve_profile_field("environment", profile=profile, section=section) or "real"
    if resolved_environment not in _ENVIRONMENTS:
        raise KISUsageError(f"environment 는 {_ENVIRONMENTS} 중 하나여야 한다: {resolved_environment!r}")
    resolved_account = account
    if resolved_account is None:
        resolved_account = _resolve_profile_field("account", profile=profile, section=section)
        if resolved_account is not None:
            _validate_account(resolved_account)   # 형식 검증(fail-closed) -- 세션이 쪼갤 수 있게
    resolved_hts_id = hts_id if hts_id is not None else _resolve_hts_id(loaded)
    return ResolvedCredentials(
        app_key=app_key if app_key is not None
        else _require_profile_field("app_key", profile=profile, section=section),
        app_secret=app_secret if app_secret is not None
        else _require_profile_field("app_secret", profile=profile, section=section),
        account=resolved_account,
        environment=cast("Environment", resolved_environment),
        hts_id=resolved_hts_id,
    )


# --- 쓰기 (KISConfig.save) ----------------------------------------------------

@dataclass(frozen=True, kw_only=True)
class KISConfig:
    """한 프로필의 자격증명을 담아 :meth:`save` 로 파일에 기록하는 값 객체.

    ``KISConfig(profile="pension", app_key=..., app_secret=..., account="87654321-22").save()`` 처럼
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
        data = _read_existing(directory / "credentials.json")
        data[self.profile] = entry
        return _write_credentials(directory, data)

    @staticmethod
    def set_default(profile: str, *, config_dir: str | Path | None = None) -> Path:
        """이름 없이 :class:`~kis_trader.client.KISClient` 를 열 때 여는 **기본 프로필**을 못박는다.
        ``credentials.json`` 최상위 ``"default_profile"`` 마커에 이름을 기록하고 그 경로를 돌려준다.

        파일 순서·환경변수와 무관하며 재시작 후에도 유지된다(``KIS_DEFAULT_PROFILE`` 환경변수가 있으면
        그쪽이 우선). ``profile`` 은 이미 :meth:`save` 로 저장돼 있어야 한다 -- 없으면
        :class:`KISUsageError` (존재하지 않는 프로필을 기본으로 못박는 것을 막는다). 마커만 갱신하고
        다른 프로필은 보존하며, 파일은 소유자만 읽게 ``0600`` 으로 원자적으로 쓴다."""
        _validate_profile_name(profile)
        directory = _config_dir_path(config_dir)
        data = _read_existing(directory / "credentials.json")
        if not isinstance(data.get(profile), dict):
            raise KISUsageError(
                f"기본으로 지정할 프로필 {profile!r} 가 credentials.json 에 없다 -- "
                "먼저 KISConfig(...).save() 로 저장하라."
            )
        data[_DEFAULT_MARKER_KEY] = profile
        return _write_credentials(directory, data)

    @staticmethod
    def set_hts_id(hts_id: str, *, config_dir: str | Path | None = None) -> Path:
        """조건검색·관심종목 조회에 쓰는 **HTS 로그인 아이디**를 저장하고 그 경로를 돌려준다.

        HTS 아이디는 사용자당 하나라 프로필별이 아니라 ``credentials.json`` 최상위 ``"hts_id"``
        키에 한 번 기록한다(모든 프로필이 공유). 프로필과 무관한 공유 값이라 :meth:`set_default` 와
        같은 최상위 메타 writer 다(프로필 섹션은 :meth:`save`). 이후
        :class:`~kis_trader.client.KISClient` 조회에서 ``user_id`` 를 생략하면 이 값을 쓴다
        (``KIS_HTS_ID`` 환경변수가 있으면 그쪽이 우선). 인증엔 쓰이지 않는다. 다른 프로필·마커는
        보존하며, 파일은 소유자만 읽게 ``0600`` 으로 원자적으로 쓴다."""
        resolved = hts_id.strip()
        if not resolved:
            raise KISUsageError("hts_id 는 비어 있을 수 없다.")
        directory = _config_dir_path(config_dir)
        data = _read_existing(directory / "credentials.json")
        data[_HTS_ID_KEY] = resolved
        return _write_credentials(directory, data)


def _read_existing(path: Path) -> dict[str, object]:
    """기존 credentials.json 을 읽어 병합 바탕으로 쓴다. 없으면 빈 dict. 있는데 파손이거나 프로필
    섹션이 객체가 아니면 -- 덮어써 남의 프로필을 날리지 않도록 -- :class:`KISUsageError` 로 멈춘다.
    ``"default_profile"`` 메타키(기본 프로필 마커 문자열)는 프로필 섹션이 아니므로 검증에서 제외한다."""
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as err:
        raise KISUsageError(f"{path} 가 있으나 읽을 수 없어 병합할 수 없다.") from err
    if not isinstance(parsed, dict):
        raise KISUsageError(f"{path} 의 최상위가 프로필 객체가 아니라 병합할 수 없다.")
    for name, section in parsed.items():
        if name in _RESERVED_TOP_LEVEL_KEYS:   # 프로필이 아닌 최상위 메타키(문자열)
            continue
        if not isinstance(section, dict):
            raise KISUsageError(f"{path} 의 프로필 {name!r} 항목이 객체가 아니라 병합할 수 없다.")
    return parsed


__all__ = ["KISConfig", "ResolvedCredentials", "order_store_path", "resolve_credentials"]
