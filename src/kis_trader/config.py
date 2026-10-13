"""자격증명을 프로필 단위로 저장(:class:`KISConfig`)하고 세션이 읽어들이는 자리.

두 방향이 있다. **쓰기**는 :class:`KISConfig` 에 값을 담아 :meth:`KISConfig.save` 로
``~/.config/kis-trader/credentials.json`` 에 기록한다(``aws configure`` 와 같은 자리). **읽기**는
:func:`resolve_credentials` 가 프로필로 저장분을 찾아 세션(:class:`~kis_trader.client.KISClient`)에
넘긴다 -- 보통 ``KISClient(profile=...)`` 가 내부에서 부른다.

**두 축 모델(schema 2, env-outer)**: 자격증명은 **(프로필, 환경)** 두 축으로 주소지정된다. 프로필은
계좌 종류 이름(사용자 자유 문자열 ``[a-z0-9_]+``; 앱키/시크릿은 종합계좌번호 단위라 "계좌 하나 =
프로필 하나", 같은 유형 여럿도 가능), 환경(``real``/``paper``)은 별개 축이다. 같은 프로필로 환경만
토글해 실/모의를 전환한다. 환경이 앱키·계좌·호스트·토큰캐시·rate limit 을 **한꺼번에** 고르므로
자격증명이 환경과 어긋난 상태가 표현 불가능하다.

``credentials.json`` 은 환경을 대분류(바깥 블록)로 두고, 그 안에 프로필별 묶음(bundle)을 담는다::

    {
      "schema": 2,
      "hts_id": "your_hts_id",
      "account_password": "0000",
      "default_environment": "paper",
      "default_profile": "general",
      "real":  {"general": {"app_key": "...", "app_secret": "...", "account": "12345678-01"},
                "pension": {"app_key": "...", "app_secret": "...", "account": "87654321-22"}},
      "paper": {"general": {"app_key": "...", "app_secret": "...", "account": "50001234-01"}}
    }

묶음 = ``{app_key, app_secret, account?, account_password?}`` -- **묶음 안에 ``environment`` 필드는 없다**
(환경의 유일한 출처는 바깥 블록 키; 읽기/저장 모두 거부). ``real``/``paper`` 는 예약 최상위 키라 프로필
이름으로 쓸 수 없다.

프로필 미지정 시 기본 프로필: ``KIS_DEFAULT_PROFILE`` 환경변수 > ``"default_profile"`` 마커
(:meth:`KISConfig.set_default_profile`) > 양 블록 키 합집합의 첫 삽입 키 > 에러. 환경 미지정 시: 인자 >
``KIS_DEFAULT_ENVIRONMENT`` 환경변수 > ``"default_environment"`` 마커(:meth:`KISConfig.set_default_environment`)
> ``paper``(fail-safe 기본 -- 실주문은 환경을 명시해야 닿는다; no-auto-promote). 선택 ``data[환경][프로필]``
조합이 없으면 깔끔한 :class:`KISUsageError`(있는 프로필 나열, 다른 블록에 있으면 그 환경을 짚어줌).

**런타임은 두 축 schema 2 만 읽는다**: ``schema`` 마커가 없는 옛(0.7.0 이전) 평평한 파일
(``{"main": {..., "environment": "real"}}``)은 런타임이 해석하지 않는다 -- 비어 있지 않은 그런 파일을
만나면 조용히 오해석하거나 생략된 환경을 real 로 자동 승격하지 않고, ``'kis config migrate'`` 로 두 축
형식(schema 2)으로 한 번 변환하라는 :class:`KISUsageError` 로 멈춘다. 변환기(``kis config migrate``)만
옛 형식을 읽어 schema 2 로 옮긴다.

``hts_id`` 는 조건검색·관심종목 조회의 ``user_id`` 로 쓰는 HTS 로그인 아이디다(사용자당 하나, 최상위
공유; 인증엔 안 씀). :meth:`KISConfig.set_hts_id` 로 기록하거나 손 편집한다.

읽기 순서: 환경변수 -> ``credentials.json``. v2 환경변수는 두 축 키를 쓴다
(``KIS_<PROFILE>_<ENV>_APP_KEY`` / ``_APP_SECRET`` / ``_ACCOUNT`` / ``_ACCOUNT_PASSWORD``; 예:
``KIS_GENERAL_PAPER_APP_KEY``), 공유값은 ``KIS_HTS_ID`` / ``KIS_ACCOUNT_PASSWORD``. **앱키·시크릿 값은
어디에도 출력하지 않는다**(화면·로그·예외 모두); 누락 예외에는 변수 이름만 담는다. (형식 오류 진단을
위해 계좌·프로필 문자열은 예외 메시지에 담을 수 있다 -- 이들은 시크릿이 아니다.)

설정 위치는 XDG 규약을 따라 세 OS 공통이다: 편집 대상(``credentials.json`` 0600)은
``$XDG_CONFIG_HOME/kis-trader`` (없으면 ``~/.config/kis-trader``), 재생성 가능한 OAuth 토큰 캐시는
분리해 ``$XDG_CACHE_HOME/kis-trader/tokens``. (Windows 는 파일 권한을 POSIX 비트가 아니라 ACL 로
관리하므로 ``0600`` 이 그대로 강제되진 않고 사용자 프로필 폴더 접근권한에 의존한다.)
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

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

#: 접속 환경. 자격증명은 환경 블록(바깥) 안에 프로필별로 중첩되고, 환경이 별개 축이다(기본 paper).
_ENVIRONMENTS = ("real", "paper")

#: 프로필 이름 규칙 -- 소문자/숫자/밑줄. 환경변수 키(``KIS_<이름대문자>_APP_KEY``)로 안전히 올릴 수
#: 있고, 대문자로 접힐 때 서로 다른 이름이 같은 키로 aliasing 되지 않도록 소문자만 허용한다.
_PROFILE_NAME = re.compile(r"[a-z0-9_]+")

#: profile 미지정 시 기본 프로필을 못박는 환경변수(빈/공백 값은 미설정 취급).
_DEFAULT_PROFILE_ENV_VAR = "KIS_DEFAULT_PROFILE"

#: credentials.json 최상위에서 기본 프로필 이름을 담는 예약 키(프로필 이름으로는 못 쓴다).
#: 프로필 섹션은 객체, 이 마커는 문자열이라 값 타입으로 구분한다.
_DEFAULT_PROFILE_KEY = "default_profile"
#: HTS 로그인 아이디를 담는 예약 최상위 키. HTS 아이디는 사용자당 하나라 프로필별이 아니라
#: 최상위에 한 번 둔다(모든 프로필이 공유). 인증엔 안 쓰이고 조건검색·관심종목의 user_id 로 쓴다.
_HTS_ID_KEY = "hts_id"
_HTS_ID_ENV_VAR = "KIS_HTS_ID"
#: 계좌비밀번호를 담는 예약 최상위 키. 사용자당 하나라 최상위에 한 번 둔다(모든 프로필 공유).
#: 일부 조회(야간 파생 잔고 등)의 ``ACNT_PWD`` 로만 쓰인다. **비밀값** -- repr/로그에 절대 노출하지 않는다.
_ACCOUNT_PASSWORD_KEY = "account_password"
_ACCOUNT_PASSWORD_ENV_VAR = "KIS_ACCOUNT_PASSWORD"
#: 프로필 이름으로 쓸 수 없는 예약 최상위 키(레거시). 레거시(schema 마커 없는) 파일의 형상 검증과
#: ``kis config migrate`` 의 읽기에서만 쓰는 3종이다 -- 이 집합을 넓히면 migrate 가 같은 이름의 레거시
#: 프로필을 메타키로 오인해 건너뛰므로 3종으로 고정한다. v2 의 예약 키 집합은 아래 ``_V2_*`` 를 쓴다.
_RESERVED_TOP_LEVEL_KEYS = frozenset({_HTS_ID_KEY, _ACCOUNT_PASSWORD_KEY, _DEFAULT_PROFILE_KEY})

#: v2(env-outer) 스키마 마커 키와 버전. 최상위 ``"schema": 2`` 가 있으면 v2, 없으면 레거시로 판별한다.
_SCHEMA_KEY = "schema"
_SCHEMA_VERSION = 2
#: 묶음(bundle) 안에 둘 수 없는 금지 필드. 환경의 유일한 출처는 바깥 블록 키이므로, 묶음에 이 필드가
#: 있으면 읽기/저장 모두 거부한다(어긋난 환경이 표현 불가능하도록).
_ENVIRONMENT_FIELD = "environment"
#: 한 (프로필, 환경) 묶음(bundle)을 이루는 필드 이름(저장 순서). 저장(:meth:`KISConfig.save`), v2 해석,
#: CLI(``kis config``)가 공유하는 단일 출처 -- 세 곳이 필드 목록을 따로 나열하지 않도록.
_BUNDLE_FIELDS = ("app_key", "app_secret", "account", _ACCOUNT_PASSWORD_KEY)
#: v2 최상위에서 기본 환경을 담는 예약 키 + 그 환경변수. profile 미지정 시 환경 해석 체인에 쓴다.
_DEFAULT_ENVIRONMENT_KEY = "default_environment"
_DEFAULT_ENVIRONMENT_ENV_VAR = "KIS_DEFAULT_ENVIRONMENT"
#: v2 최상위 키를 파일에서 배치하는 순서(env-outer: 환경 먼저, 프로필 다음).
#: schema -> hts_id -> account_password -> default_environment -> default_profile -> real -> paper.
_V2_TOP_LEVEL_ORDER = (
    _SCHEMA_KEY, _HTS_ID_KEY, _ACCOUNT_PASSWORD_KEY, _DEFAULT_ENVIRONMENT_KEY, _DEFAULT_PROFILE_KEY,
    *_ENVIRONMENTS,
)
#: v2 에서 프로필(계좌 종류) 이름으로 쓸 수 없는 예약 최상위 키(환경 블록 real/paper 포함).
_V2_RESERVED_TOP_LEVEL_KEYS = frozenset(_V2_TOP_LEVEL_ORDER)


def _order_top_level(data: dict[str, object]) -> dict[str, object]:
    """v2 예약 최상위 키(schema, hts_id, account_password, default_environment, default_profile, real,
    paper)를 정해진 순서로 맨 위에 모으고, 그 뒤 남은 키의 삽입 순서를 보존한다 -- 파일 레이아웃을
    예측 가능하게(env-outer: 환경 블록이 마커 뒤에 오게). writer 는 v2 데이터만 넘긴다."""
    ordered = {k: data[k] for k in _V2_TOP_LEVEL_ORDER if k in data}
    for name, value in data.items():
        if name not in ordered:
            ordered[name] = value
    return ordered


def _write_credentials(directory: Path, data: dict[str, object]) -> Path:
    """병합된 credentials 를 예약키 순서로 정렬해 ``directory/credentials.json`` 에 원자적(0600)으로
    쓰고 경로를 돌려준다. 세 writer(:meth:`KISConfig.save`/:meth:`KISConfig.set_default_profile`/
    :meth:`KISConfig.set_hts_id`)가 공유한다 -- 병합(어느 키를 바꿀지)은 각 호출부가, 정렬·권한·
    원자성은 여기서 한 번만."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "credentials.json"
    atomic_write_bytes(
        path, (json.dumps(_order_top_level(data), ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        mode=0o600,
    )
    return path


@contextlib.contextmanager
def _credentials_write_lock(directory: Path) -> Iterator[None]:
    """``credentials.json`` 의 read-modify-write 를 프로세스 간 직렬화한다 -- 두 프로세스가 서로 다른
    프로필을 동시에 저장하면 각자 전체 파일을 원자적으로 교체해 나중 writer 가 남의 프로필을 조용히
    날린다. fcntl 없는 플랫폼(Windows)에선 잠금 없이 진행한다(best-effort; 대화형 단일 사용자 경로)."""
    try:
        import fcntl
    except ImportError:  # pragma: no cover - 비-Unix
        yield
        return
    directory.mkdir(parents=True, exist_ok=True)
    fd = os.open(directory / "credentials.json.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _validate_profile_name(profile: str) -> None:
    if not _PROFILE_NAME.fullmatch(profile):
        raise KISUsageError(
            f"프로필 이름은 소문자/숫자/밑줄만 쓸 수 있다(환경변수 키로 쓰이기 때문): {profile!r}"
        )
    if profile in _RESERVED_TOP_LEVEL_KEYS:
        raise KISUsageError(
            f"{profile!r} 은 예약된 최상위 키라 프로필 이름으로 쓸 수 없다."
        )


def _validate_profile_name_v2(profile: str) -> None:
    """v2 프로필(계좌 종류) 이름 검증. 기본 형식(소문자/숫자/밑줄 + 레거시 예약 키 금지)에 더해
    v2 예약 최상위 키(schema/default_environment/real/paper 등)와 ``_real``/``_paper`` 접미사를 금지한다.
    접미사 금지는 환경변수 키(``KIS_<PROFILE>_<ENV>_...``)가 다른 (프로필, 환경) 조합으로 aliasing 되는
    유일한 경로를 막는다(예: 프로필 ``general_paper`` 가 ``KIS_GENERAL_PAPER_...`` 를 두고 (general, paper)와 충돌)."""
    _validate_profile_name(profile)          # 형식 + 레거시 예약 키
    if profile in _V2_RESERVED_TOP_LEVEL_KEYS:
        raise KISUsageError(
            f"{profile!r} 은 예약된 최상위 키라 프로필 이름으로 쓸 수 없다."
        )
    if profile.endswith(("_real", "_paper")):
        raise KISUsageError(
            f"프로필 이름은 '_real'/'_paper' 로 끝날 수 없다(환경변수 키 alias 방지): {profile!r}"
        )


def _env_var_prefix(profile: str) -> str:
    """프로필 -> 옛 환경변수 키 접두어. 기본 프로필 ``main`` 은 접두어 없이(``KIS_``), 나머지는
    ``KIS_<이름대문자>_``. 예: ``pension`` -> ``KIS_PENSION_``. 런타임 해석에는 쓰지 않고(런타임은 두
    축 키만 읽는다), ``kis config migrate`` 가 옛 접두어 환경변수를 두 축 키로 바꾸라고 안내하는 표에만 쓴다."""
    return "KIS_" if profile == "main" else f"KIS_{profile.upper()}_"


def _env_var_prefix_v2(profile: str, environment: str) -> str:
    """(프로필, 환경) -> 두 축 환경변수 키 접두어 ``KIS_<PROFILE>_<ENV>_``.
    예: (``general``, ``paper``) -> ``KIS_GENERAL_PAPER_`` (뒤에 ``APP_KEY`` 등이 붙는다).
    환경을 고정 중간 세그먼트로 두어 키가 슬롯을 못박으므로 축을 넘어 샐 수 없다."""
    return f"KIS_{profile.upper()}_{environment.upper()}_"


def _config_dir_path(override: str | Path | None = None) -> Path:
    """``credentials.json`` 이 있는 디렉터리. ``override`` 가 있으면 그것(``~`` 확장),
    없으면 ``$XDG_CONFIG_HOME/kis-trader`` (else ``~/.config/kis-trader``)."""
    if override is not None:
        return Path(override).expanduser()
    return xdg_config_subdir(_APP_DIR_NAME)


def _credentials_path(override: str | Path | None = None) -> Path:
    """``credentials.json`` 파일의 전체 경로를 돌려준다. 호출자가 디렉터리와 파일명을 직접 합치지
    않도록 모은 진입점이다(특히 모듈 밖 소비자 -- CLI 의 ``kis config`` 등)."""
    return _config_dir_path(override) / "credentials.json"


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


# --- 스키마 판별 + v2(env-outer) 형상 검증 ------------------------------------

def _detect_schema(loaded: dict[str, object]) -> Literal["v2", "legacy"]:
    """파싱한 credentials 의 스키마를 판별한다: ``"v2"`` 또는 ``"legacy"``. 판별자는 최상위 ``"schema"``
    마커 하나다 -- ``"schema": 2`` 가 있으면 v2, 없으면 레거시(레거시 파일엔 ``paper`` 라는 *프로필 이름*이
    있어 블록 유무로는 판별 불가). ``schema`` 가 객체면 ``schema`` 라는 *레거시 프로필*이라 레거시로 본다.
    알 수 없는 스키마 값(1/3/"2"/true/실수)은 -- 잘못 해석해 엉뚱한 자격증명을 열지 않도록 -- fail-closed."""
    if _SCHEMA_KEY not in loaded:
        return "legacy"
    value = loaded[_SCHEMA_KEY]
    if isinstance(value, dict):          # "schema" 라는 레거시 프로필 섹션
        return "legacy"
    if isinstance(value, int) and not isinstance(value, bool) and value == _SCHEMA_VERSION:
        return "v2"
    raise KISUsageError(
        f"credentials.json 의 {_SCHEMA_KEY!r} 값을 알 수 없다(지원 버전: {_SCHEMA_VERSION}): {value!r}"
    )


def _select_environment_block(loaded: dict[str, object], environment: str) -> dict[str, object]:
    """v2 파일에서 한 환경 블록(``real``/``paper``)을 돌려준다(없으면 빈 dict). 객체가 아니면 fail-closed."""
    block = loaded.get(environment)
    if block is None:
        return {}
    if not isinstance(block, dict):
        raise KISUsageError(f"credentials.json 의 {environment!r} 블록이 객체가 아니다.")
    return cast("dict[str, object]", block)


def _validate_bundle(*, environment: str, profile: str, bundle: object) -> dict[str, object]:
    """v2 묶음(bundle) 하나를 검증하고 돌려준다. 객체가 아니면 fail-closed. 묶음 안에 ``environment``
    필드가 있으면 거부한다 -- 환경의 유일한 출처는 바깥 블록 키이므로(읽기/저장 양쪽에서 막는다)."""
    if not isinstance(bundle, dict):
        raise KISUsageError(f"credentials.json 의 {environment!r}.{profile!r} 묶음이 객체가 아니다.")
    if _ENVIRONMENT_FIELD in bundle:
        raise KISUsageError(
            f"credentials.json 의 {environment!r}.{profile!r} 묶음에 {_ENVIRONMENT_FIELD!r} 필드를 둘 수 없다 "
            "(환경은 바깥 블록 키로만 정한다)."
        )
    return cast("dict[str, object]", bundle)


def _validate_v2_shape(loaded: dict[str, object]) -> None:
    """v2 파일 전체 형상을 검증한다(읽기/저장 공통). 알 수 없는 최상위 키(스키마 혼합)는 거부하고, 각
    환경 블록이 객체인지, 그 안의 프로필 이름이 v2 규칙을 지키는지, 묶음에 ``environment`` 필드가 없는지
    확인한다. ``schema``/마커 등 예약 키의 값 타입은 관대하게 둔다(해석기가 타입으로 걸러낸다)."""
    for key in loaded:
        if key not in _V2_RESERVED_TOP_LEVEL_KEYS:
            raise KISUsageError(
                f"credentials.json (schema {_SCHEMA_VERSION}) 에 알 수 없는 최상위 키 {key!r} 가 있다."
            )
    for env_key in _ENVIRONMENTS:
        block = _select_environment_block(loaded, env_key)
        for profile, bundle in block.items():
            _validate_profile_name_v2(profile)
            _validate_bundle(environment=env_key, profile=profile, bundle=bundle)


def _validate_legacy_shape(parsed: dict[str, object]) -> None:
    """레거시(schema 마커 없는) 파일의 형상을 검증한다. 예약 최상위 메타키(문자열)는 건너뛰고, 나머지
    프로필 섹션이 전부 객체인지 확인한다 -- 병합/해석이 비객체 섹션에서 깨지지 않도록 fail-closed."""
    for name, section in parsed.items():
        if name in _RESERVED_TOP_LEVEL_KEYS:   # 프로필이 아닌 최상위 메타키(문자열)
            continue
        if not isinstance(section, dict):
            raise KISUsageError(f"credentials.json 의 프로필 {name!r} 항목이 객체가 아니다.")


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


def _coerce_account_password(value: object) -> str | None:
    """파일 원값을 계좌비밀번호 문자열로 좁힌다(최상위·묶음별 공통). bool 은 비번이 아니므로 ``None``,
    따옴표 없이 쓴 숫자 비번은 문자열로(JSON 정수라 앞자리 0 손실 없음), 비어있지 않은 문자열은 원문
    그대로(패스워드는 공백도 유효할 수 있어 strip 하지 않는다). **비밀값이라 값을 어디에도 출력/로그하지 않는다.**"""
    if isinstance(value, bool):          # bool 은 int 하위형 -- 비밀번호가 아니다
        return None
    if isinstance(value, int):           # 따옴표 없이 쓴 숫자 비번 -> 문자열로(JSON 정수라 앞자리 0 손실 없음)
        return str(value)
    if isinstance(value, str) and value.strip():
        return value
    return None


# --- 읽기 v2(env-outer: 환경이 바깥 블록) -------------------------------------

def _resolve_profile_field_v2(field_name: str, *, profile: str, environment: str, bundle: dict[str, object]) -> str | None:
    """v2 묶음의 한 필드를 두 축 환경변수(``KIS_<P>_<E>_<FIELD>``) -> 묶음 순으로 찾는다. 빈/공백 문자열은
    유효한 자격증명이 아니므로 '없음'으로 취급한다(fail-closed). 키가 슬롯을 못박아 축을 넘어 샐 수 없다."""
    value = os.environ.get(_env_var_prefix_v2(profile, environment) + field_name.upper())
    if value is None:
        value = _scalar_or_raise(profile, field_name, bundle.get(field_name))
    if value is not None and not value.strip():
        return None
    return value


def _require_profile_field_v2(field_name: str, *, profile: str, environment: str, bundle: dict[str, object]) -> str:
    value = _resolve_profile_field_v2(field_name, profile=profile, environment=environment, bundle=bundle)
    if value is None:
        raise KISUsageError(
            f"{_env_var_prefix_v2(profile, environment)}{field_name.upper()} 를 찾을 수 없다 -- 환경변수 또는 "
            "credentials.json 에 설정하라('kis config' 로 저장)."
        )
    return value


def _resolve_account_password_v2(
    bundle: dict[str, object], loaded: dict[str, object], *, profile: str, environment: str,
) -> str | None:
    """v2 계좌비밀번호. 우선순위: 두 축 환경변수(``KIS_<P>_<E>_ACCOUNT_PASSWORD``) > 묶음별
    ``account_password`` > 최상위 환경변수(``KIS_ACCOUNT_PASSWORD``) > 최상위 파일 값. real/paper 는 다른
    계좌번호라 비번이 다를 수 있어 묶음별 override 를 최상위 공유값보다 앞에 둔다. **비밀값 -- 출력/로그 금지.**"""
    env = os.environ.get(_env_var_prefix_v2(profile, environment) + "ACCOUNT_PASSWORD")
    if env is not None and env.strip():
        return env
    per_bundle = _coerce_account_password(bundle.get(_ACCOUNT_PASSWORD_KEY))
    if per_bundle is not None:
        return per_bundle
    top_env = os.environ.get(_ACCOUNT_PASSWORD_ENV_VAR)
    if top_env is not None and top_env.strip():
        return top_env
    return _coerce_account_password(loaded.get(_ACCOUNT_PASSWORD_KEY))


def _resolve_environment_v2(environment: Environment | None, loaded: dict[str, object]) -> str:
    """v2 환경 해석 체인(환경은 자격증명 슬롯의 주소다): 인자 > ``KIS_DEFAULT_ENVIRONMENT`` 환경변수
    (빈/공백은 미설정) > 최상위 ``"default_environment"`` 마커 > ``"paper"`` (fail-safe 기본). 유효성(real/paper)
    검증은 호출부(:func:`_resolve_v2`)가 한다. no-auto-promote: 기본이 paper 라 실주문은 명시 없이는 불가."""
    if environment is not None:
        return environment
    env = os.environ.get(_DEFAULT_ENVIRONMENT_ENV_VAR)
    if env is not None and env.strip():
        return env.strip()
    marker = loaded.get(_DEFAULT_ENVIRONMENT_KEY)
    if isinstance(marker, str) and marker.strip():
        return marker.strip()
    return "paper"


def _profile_in_any_block(data: dict[str, object], profile: str) -> bool:
    """``profile`` 이 어느 환경 블록(real/paper)에든 묶음으로 존재하는지. v2 writer 의 존재 검사에 쓴다."""
    for env_key in _ENVIRONMENTS:
        block = data.get(env_key)
        if isinstance(block, dict) and isinstance(block.get(profile), dict):
            return True
    return False


def _resolve_default_profile_name_v2(loaded: dict[str, object], blocks: list[str]) -> str:
    """v2 기본 프로필 이름. 해석 순서: ``KIS_DEFAULT_PROFILE`` 환경변수(빈/공백은 미설정) > 최상위
    ``"default_profile"`` 마커 > 블록들의 키 합집합 중 첫 삽입 키(``blocks``) > 에러. v2 에는 레거시의
    ``"main"`` 폴백이 없다 -- 순수 환경변수 사용자는 ``KIS_DEFAULT_PROFILE`` 를 줘야 한다."""
    override = os.environ.get(_DEFAULT_PROFILE_ENV_VAR)
    if override is not None and override.strip():
        return override.strip()
    marker = loaded.get(_DEFAULT_PROFILE_KEY)
    if isinstance(marker, str) and marker.strip():
        return marker.strip()
    if blocks:
        return blocks[0]
    raise KISUsageError(
        "기본 프로필을 정할 수 없다 -- credentials.json 에 프로필이 없다. "
        "'kis config' 로 저장하거나 KIS_DEFAULT_PROFILE 환경변수를 설정하라."
    )


def _resolve_v2(
    profile: str | None, loaded: dict[str, object], *, app_key: str | None, app_secret: str | None,
    account: str | None, environment: Environment | None, hts_id: str | None, account_password: str | None,
) -> ResolvedCredentials:
    """v2(env-outer) 해석. 환경은 override 가 아니라 **selector** 다 -- (profile, environment) 가 슬롯을
    인덱싱한다. 없는 조합은 하드 에러(no-auto-promote: 한 환경에만 있는 프로필을 다른 환경으로 자동
    승격하지 않는다). ``.environment`` 의 유일한 출처는 선택된 바깥 블록 키다."""
    _validate_v2_shape(loaded)
    real_block = _select_environment_block(loaded, "real")
    paper_block = _select_environment_block(loaded, "paper")
    if profile is None:
        names = list(real_block)
        for name in paper_block:
            if name not in names:
                names.append(name)
        profile = _resolve_default_profile_name_v2(loaded, names)
        _validate_profile_name_v2(profile)    # 해석된 기본 이름 검증(env/마커에서 온 값) -- 쓰기 경로와 대칭
    resolved_environment = _resolve_environment_v2(environment, loaded)
    if resolved_environment not in _ENVIRONMENTS:
        raise KISUsageError(
            f"environment 는 {_ENVIRONMENTS} 중 하나여야 한다: {resolved_environment!r}"
        )
    selected = real_block if resolved_environment == "real" else paper_block
    other_env = "paper" if resolved_environment == "real" else "real"
    other = paper_block if resolved_environment == "real" else real_block
    bundle_obj = selected.get(profile)
    if not isinstance(bundle_obj, dict):
        if profile in other:                   # no-auto-promote: 다른 환경에만 있다
            here = (f"'{resolved_environment}' 에 있는 프로필: {', '.join(selected)}"
                    if selected else f"'{resolved_environment}' 환경에 등록된 프로필이 없다")
            raise KISUsageError(
                f"'{resolved_environment}' 환경에 프로필 {profile!r} 이(가) 없다 ({here}). "
                f"'{other_env}' 환경에는 있다 -- environment={other_env!r} 을(를) 명시하라."
            )
        if selected or other:                  # 어느 환경에도 그 이름이 없다(다른 프로필은 있다)
            real_names = ", ".join(real_block) if real_block else "(없음)"
            paper_names = ", ".join(paper_block) if paper_block else "(없음)"
            raise KISUsageError(
                f"프로필 {profile!r} 을(를) credentials.json 에서 찾을 수 없다 "
                f"(real: {real_names}; paper: {paper_names})."
            )
        raise KISUsageError(                    # 어느 환경에도 프로필이 하나도 없다
            f"credentials.json (schema {_SCHEMA_VERSION}) 에 프로필이 하나도 없다 -- 'kis config' 로 설정하라."
        )
    bundle = cast("dict[str, object]", bundle_obj)
    resolved_account = account
    if resolved_account is None:
        resolved_account = _resolve_profile_field_v2(
            "account", profile=profile, environment=resolved_environment, bundle=bundle)
        if resolved_account is not None:
            _validate_account(resolved_account)
    resolved_hts_id = hts_id if hts_id is not None else _resolve_hts_id(loaded)
    resolved_account_password = (
        account_password if account_password is not None
        else _resolve_account_password_v2(bundle, loaded, profile=profile, environment=resolved_environment)
    )
    return ResolvedCredentials(
        app_key=app_key if app_key is not None
        else _require_profile_field_v2("app_key", profile=profile, environment=resolved_environment, bundle=bundle),
        app_secret=app_secret if app_secret is not None
        else _require_profile_field_v2("app_secret", profile=profile, environment=resolved_environment, bundle=bundle),
        account=resolved_account,
        environment=cast("Environment", resolved_environment),
        hts_id=resolved_hts_id,
        account_password=resolved_account_password,
    )


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
    #: 계좌비밀번호 -- 야간 파생 잔고 등 일부 조회의 ACNT_PWD. **비밀값이라 repr 에서 뺀다**(시크릿 규율).
    account_password: str | None = field(default=None, repr=False)


def resolve_credentials(
    profile: str | None = None, *, config_dir: str | Path | None = None,
    environment: Environment | None = None,
) -> ResolvedCredentials:
    """프로필의 저장된 자격증명을 읽는다(환경변수 -> credentials.json). ``profile`` 이 ``None`` 이면
    기본 프로필로 해석한다. ``environment`` (``real``/``paper``)는 v2 에서 **selector** 다 -- (프로필, 환경)
    조합의 슬롯을 고른다. 생략하면 해석 체인(기본 ``paper``)으로 정한다. 런타임은 두 축 schema 2 만
    읽는다 -- 비어 있지 않은 옛 평평한 파일은 ``'kis config migrate'`` 안내 에러로 멈춘다. 앱키/시크릿이
    없으면 :class:`KISUsageError`(변수 이름만 담는다). 계좌번호는 없으면 ``None``."""
    return _fill_credentials(profile, app_key=None, app_secret=None, account=None,
                             environment=environment, config_dir=config_dir)


def _fill_credentials(
    profile: str | None, *, app_key: str | None, app_secret: str | None, account: str | None,
    environment: Environment | None, config_dir: str | Path | None, hts_id: str | None = None,
    account_password: str | None = None,
) -> ResolvedCredentials:
    """명시된 값은 그대로 쓰고 ``None`` 인 것만 저장분에서 채운다(부분 해석). 파일을 한 번만 파싱한다.
    런타임은 **두 축 schema 2 만** 해석한다: 비어 있지 않은 옛(평평한) 파일은 조용히 오해석하거나 생략된
    환경을 real 로 자동 승격하지 않고, ``'kis config migrate'`` 로 변환하라는 :class:`KISUsageError` 로
    멈춘다. 빈/없는 파일은 v2 로 해석한다(프로필을 못 정하면 v2 해석기가 깔끔한 에러를 낸다). ``profile``
    이 ``None`` 이면 v2 해석기가 기본 프로필로 해석한다. **키워드 시그니처·반환 형상 불변**(세션의 6필드
    언팩이 그대로 동작)."""
    directory = _config_dir_path(config_dir)
    loaded = _load_credentials(directory)
    if loaded and _detect_schema(loaded) == "legacy":   # 비어있지 않은 옛 평평한 파일 -> 변환 안내 후 멈춤
        raise KISUsageError(
            "credentials.json 이 옛(0.7.0 이전) 평평한 형식입니다 -- "
            "'kis config migrate' 로 두 축 형식(schema 2)으로 변환한 뒤 사용하세요."
        )
    if profile is not None:                       # 명시 이름을 v2 규칙으로 검증(쓰기 경로와 대칭):
        _validate_profile_name_v2(profile)        # _real/_paper 접미사 + v2 예약 키 금지
    return _resolve_v2(profile, loaded, app_key=app_key, app_secret=app_secret, account=account,
                       environment=environment, hts_id=hts_id, account_password=account_password)


# --- 쓰기 (KISConfig.save) ----------------------------------------------------

def _merge_base_v2(path: Path) -> dict[str, object]:
    """v2 쓰기의 병합 바탕을 돌려준다. 없거나 빈 파일 -> ``{schema: 2}`` 씨앗. 이미 v2 -> 그대로(형상
    검증 후). 레거시(schema 마커 없는) 파일 -> 거부(``'kis config migrate'`` 로 변환 후 저장) -- 어댑터로
    읽히는 레거시 파일을 writer 가 조용히 반쪽 v2 로 만들지 않도록. 파손/비객체 -> 덮어써 남의 프로필을
    날리지 않게 fail-closed."""
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {_SCHEMA_KEY: _SCHEMA_VERSION}
    except (OSError, json.JSONDecodeError) as err:
        raise KISUsageError(f"{path} 가 있으나 읽을 수 없어 병합할 수 없다.") from err
    if not isinstance(parsed, dict):
        raise KISUsageError(f"{path} 의 최상위가 객체가 아니라 병합할 수 없다.")
    if not parsed:
        return {_SCHEMA_KEY: _SCHEMA_VERSION}
    if _detect_schema(parsed) != "v2":
        raise KISUsageError(
            f"{path} 는 레거시 형식이라 그대로 저장할 수 없다 -- 'kis config migrate' 로 변환 후 저장하라."
        )
    _validate_v2_shape(parsed)
    return parsed


@dataclass(frozen=True, kw_only=True, slots=True)
class KISConfig:
    """한 (프로필, 환경) 슬롯의 자격증명을 담아 :meth:`save` 로 파일에 기록하는 값 객체.

    ``KISConfig(profile="pension", app_key=..., app_secret=..., account="87654321-22", environment="real").save()``
    처럼 쓴다(``aws configure`` 와 같은 자리). ``profile`` 은 계좌 종류 이름(사용자 자유 문자열; 소문자·숫자·
    밑줄; 예약 키/``_real``/``_paper`` 금지). ``app_key``/``app_secret`` 은 필수(비면 구성 시점에 거부).
    ``account`` 는 ``"CANO-상품코드"`` 형식이며 생략 가능(시세만 볼 계좌). ``environment`` 는 실전/모의
    (기본 ``paper`` -- fail-safe; 실주문 환경은 명시해야 한다). ``account_password`` 는 묶음별 선택 override
    (일부 조회의 ``ACNT_PWD``; **비밀값이라 repr 제외**). ``config_dir`` 로 저장 위치를 바꾼다. 모든 인자는
    키워드 전용이라 시크릿을 위치로 잘못 넘길 수 없다. 세션을 열려면
    :class:`~kis_trader.client.KISClient` ``(profile=..., environment=...)`` 를 쓴다 -- 이 객체는 저장만 한다.
    """

    # 시크릿은 필수이자 repr 제외 -- 이 객체가 값을 담으므로 repr(KISConfig(...))/로깅에 앱키가
    # 새면 안 된다. 빈/반쪽/형식오류는 save 가 아니라 구성 시점에 거부해 '저장은 됐는데 못 읽는'
    # 상태를 애초에 표현 불가능하게 한다.
    app_key: str = field(repr=False)
    app_secret: str = field(repr=False)
    profile: str = "main"
    account: str | None = None
    environment: Environment = "paper"
    #: 묶음별 계좌비밀번호 override -- **비밀값이라 repr 에서 뺀다**(최상위 공유값과 다를 때만 묶음에 둔다).
    account_password: str | None = field(default=None, repr=False)
    config_dir: str | Path | None = None

    def __post_init__(self) -> None:
        _validate_profile_name_v2(self.profile)
        if not self.app_key.strip() or not self.app_secret.strip():
            raise KISUsageError("app_key 와 app_secret 은 비어 있을 수 없다.")
        if self.environment not in _ENVIRONMENTS:
            raise KISUsageError(f"environment 는 {_ENVIRONMENTS} 중 하나여야 한다: {self.environment!r}")
        if self.account is not None:
            _validate_account(self.account)

    def save(self) -> Path:
        """이 (프로필, 환경) 슬롯의 자격증명을 ``credentials.json`` (v2) 에 기록하고 그 경로를 돌려준다.

        ``data[environment][profile]`` 묶음(``{app_key, app_secret, account?, account_password?}`` -- 묶음
        안에 ``environment`` 필드는 없다)만 갱신하고 다른 슬롯·블록·최상위 키는 보존(병합)한다. 레거시
        파일에는 저장하지 않는다(``_merge_base_v2`` 가 거부). 파일은 소유자만 읽게 ``0600``, 원자적으로
        쓴다(부분 파일/유출 방지).
        """
        # 묶음 필드를 단일 출처(_BUNDLE_FIELDS)로 순회한다. app_key/app_secret 은 필수라 항상 채워지고
        # account/account_password 는 None 이면 건너뛴다(기존 동작 그대로).
        bundle: dict[str, str] = {}
        for name in _BUNDLE_FIELDS:
            value = getattr(self, name)
            if value is not None:
                bundle[name] = value
        directory = _config_dir_path(self.config_dir)
        with _credentials_write_lock(directory):   # 동시 writer 가 남의 슬롯을 날리지 않게 RMW 직렬화
            data = _merge_base_v2(directory / "credentials.json")
            block = data.get(self.environment)
            if not isinstance(block, dict):
                block = {}
                data[self.environment] = block
            cast("dict[str, object]", block)[self.profile] = bundle
            return _write_credentials(directory, data)

    @staticmethod
    def set_default_profile(profile: str, *, config_dir: str | Path | None = None) -> Path:
        """이름 없이 :class:`~kis_trader.client.KISClient` 를 열 때 여는 **기본 프로필**을 못박는다.
        ``credentials.json`` 최상위 ``"default_profile"`` 마커에 이름을 기록하고 그 경로를 돌려준다.

        파일 순서·환경변수와 무관하며 재시작 후에도 유지된다(``KIS_DEFAULT_PROFILE`` 환경변수가 있으면
        그쪽이 우선). ``profile`` 은 어느 환경 블록(real/paper)에든 이미 :meth:`save` 로 저장돼 있어야 한다
        -- 없으면 :class:`KISUsageError`. 마커만 갱신하고 다른 슬롯은 보존하며, 파일은 소유자만 읽게
        ``0600`` 으로 원자적으로 쓴다."""
        _validate_profile_name_v2(profile)
        directory = _config_dir_path(config_dir)
        with _credentials_write_lock(directory):
            data = _merge_base_v2(directory / "credentials.json")
            if not _profile_in_any_block(data, profile):
                raise KISUsageError(
                    f"기본으로 지정할 프로필 {profile!r} 가 credentials.json 에 없다 -- "
                    "먼저 'kis config' 로 저장하라."
                )
            data[_DEFAULT_PROFILE_KEY] = profile
            return _write_credentials(directory, data)

    @staticmethod
    def set_default_environment(environment: Environment, *, config_dir: str | Path | None = None) -> Path:
        """``environment`` 미지정 시 쓸 **기본 환경**(real/paper)을 최상위 ``"default_environment"`` 마커에
        못박고 그 경로를 돌려준다. 해석 체인에서 인자·``KIS_DEFAULT_ENVIRONMENT`` 환경변수 다음, 하드코딩
        기본 ``paper`` 앞에 온다. 다른 슬롯·마커는 보존하며, 파일은 소유자만 읽게 ``0600`` 으로 원자적으로
        쓴다. 레거시 파일에는 저장하지 않는다(``_merge_base_v2`` 가 거부)."""
        if environment not in _ENVIRONMENTS:
            raise KISUsageError(f"environment 는 {_ENVIRONMENTS} 중 하나여야 한다: {environment!r}")
        directory = _config_dir_path(config_dir)
        with _credentials_write_lock(directory):
            data = _merge_base_v2(directory / "credentials.json")
            data[_DEFAULT_ENVIRONMENT_KEY] = environment
            return _write_credentials(directory, data)

    @staticmethod
    def set_hts_id(hts_id: str, *, config_dir: str | Path | None = None) -> Path:
        """조건검색·관심종목 조회에 쓰는 **HTS 로그인 아이디**를 저장하고 그 경로를 돌려준다.

        HTS 아이디는 사용자당 하나라 프로필별이 아니라 ``credentials.json`` 최상위 ``"hts_id"``
        키에 한 번 기록한다(모든 슬롯이 공유). 프로필과 무관한 공유 값이라 :meth:`set_default_profile` 와
        같은 최상위 메타 writer 다(슬롯은 :meth:`save`). 이후
        :class:`~kis_trader.client.KISClient` 조회에서 ``user_id`` 를 생략하면 이 값을 쓴다
        (``KIS_HTS_ID`` 환경변수가 있으면 그쪽이 우선). 인증엔 쓰이지 않는다. 다른 슬롯·마커는
        보존하며, 파일은 소유자만 읽게 ``0600`` 으로 원자적으로 쓴다. 레거시 파일에는 저장하지 않는다."""
        resolved = hts_id.strip()
        if not resolved:
            raise KISUsageError("hts_id 는 비어 있을 수 없다.")
        directory = _config_dir_path(config_dir)
        with _credentials_write_lock(directory):
            data = _merge_base_v2(directory / "credentials.json")
            data[_HTS_ID_KEY] = resolved
            return _write_credentials(directory, data)


def is_legacy_store(*, config_dir: str | Path | None = None) -> bool:
    """저장된 ``credentials.json`` 이 스키마 마커 없는 **레거시** 저장소이면서 프로필이 하나 이상
    들어 있는지. 레거시 저장소는 환경 블록(real/paper)이 분리돼 있지 않아, 실행 중 환경을 바꾸면
    저장된 자격증명이 아니라 환경 오버라이드로 해석돼 엉뚱한 환경에 걸릴 수 있다 -- 그 경계를
    호출자가 미리 알리도록 하는 판별기다. 빈/없는 파일은 프로필이 없으므로 ``False``(설정 전).

    파일을 읽을 수 없거나 ``schema`` 값이 알 수 없는 버전이면 :class:`KISUsageError`."""
    data = _load_credentials(_config_dir_path(config_dir))
    if _detect_schema(data) != "legacy":
        return False
    return any(
        name not in _RESERVED_TOP_LEVEL_KEYS and isinstance(section, dict)
        for name, section in data.items()
    )


def _profile_exists(profile: str, *, environment: Environment | None = None,
                    config_dir: str | Path | None = None) -> bool:
    """``profile`` 이 이미 저장돼 있는지. ``environment`` 를 주면 그 환경 블록의 묶음 존재를
    (``data[environment][profile]``), 생략하면 어느 환경 블록에든 있는지를 본다. 덮어쓰기 확인처럼 저장 전에
    존재 여부만 알고 싶을 때 쓴다 -- 저장소 레이아웃을 호출자에 노출하지 않는다. 파일이 파손이거나 형상이
    어긋나면 -- 있는 자격증명을 오해하지 않도록 -- :class:`KISUsageError` 로 멈춘다."""
    data = _load_credentials(_config_dir_path(config_dir))
    if _detect_schema(data) == "v2":
        _validate_v2_shape(data)
    else:
        _validate_legacy_shape(data)
    if environment is None:
        return _profile_in_any_block(data, profile)
    block = data.get(environment)
    return isinstance(block, dict) and isinstance(block.get(profile), dict)


__all__ = ["KISConfig", "ResolvedCredentials", "is_legacy_store", "order_store_path", "resolve_credentials"]
