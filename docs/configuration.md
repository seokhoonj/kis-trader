# 자격증명과 프로필

세션을 열려면 KIS 개발자센터에서 발급한 **앱키·앱시크릿**과 **계좌번호**가 필요합니다(발급은
[2. 앱키 발급받기](appkey.md)). 가장 간단하게는 이 값을 그대로 넘겨 세션을 엽니다:

```python
from kis_trader import KISClient
kis = KISClient(app_key="YOUR_APP_KEY", app_secret="YOUR_APP_SECRET", account="12345678-01")
```

하지만 이러면 **열 때마다 키를 다시 적어야 하고, 코드에 키가 그대로 남습니다.** 그래서 보통은 값을 한 번
**프로필**로 저장해 두고, 다음부터는 이름만으로 엽니다:

```python
from kis_trader import KISConfig
KISConfig(profile="main", app_key="YOUR_APP_KEY", app_secret="YOUR_APP_SECRET", account="12345678-01").save()

KISClient(profile="main")   # 저장된 자격증명을 자동으로 읽어 세션을 엽니다
```

이 장은 그 **프로필**을 다룹니다 -- 프로필이 무엇인지, 자격증명을 어떻게 넣는지 차례로 봅니다.

## 프로필 = 계좌 하나

프로필은 **계좌 하나**를 통째로 담은, 사용자가 이름 붙이는 묶음입니다 -- 그 계좌의 앱키·앱시크릿·
계좌번호, 그리고 실전이냐 모의냐(환경)까지. 이름으로 엽니다:

```python
KISClient(profile="main")   # "main" 이라 저장해 둔 계좌로 세션을 엽니다
```

계좌마다 프로필을 따로 두는 이유 -- 앱키·앱시크릿이 종합계좌번호(CANO) 단위라 계좌마다 다르고, 같은
유형 계좌도 여럿(연금저축 2개 등)일 수 있기 때문입니다. 이름은 소문자·숫자·밑줄로 짓습니다. 흔한 예:

| 프로필 이름 (자유롭게) | 담는 계좌 |
|---|---|
| `main` | 메인계좌 |
| `isa` | ISA(개인종합자산관리) |
| `pension` | 연금저축 |
| `irp` | IRP(개인형퇴직연금, 조회 전용) |
| `paper` | 모의투자 |

### 이름을 지정하지 않으면 (기본 프로필)

`KISClient()` 처럼 이름 없이 열면 **기본 프로필**이 열립니다. 무엇이 기본인지는 이 순서로 정합니다:

1. `KIS_DEFAULT_PROFILE` **환경변수**가 있으면 그 이름
2. 없으면 -- `credentials.json` 에 **기본으로 지정해 둔 프로필**(아래 `KISConfig.set_default`)
3. 그것도 없으면 -- `credentials.json` 에 **맨 처음 저장한 프로필**

대부분은 **주로 쓰는 계좌를 맨 먼저 저장**해 두면 그게 기본이라 아무 설정도 필요 없습니다. 특정 프로필을
기본으로 못박고 싶을 때만 아래 둘 중 하나를 씁니다.

**파일에 영구 기록** (재시작해도 유지, 셸 설정 불필요) -- 저장된 프로필 하나를 기본으로 지정합니다:

```python
from kis_trader import KISConfig
KISConfig.set_default("isa")   # credentials.json 최상위에 "default_profile": "isa" 기록
```

**환경변수** (그 셸 세션에서만, 파일보다 우선) -- `KIS_DEFAULT_PROFILE` 은 OS 환경변수라 셸에서 정합니다:

```bash
export KIS_DEFAULT_PROFILE=isa           # Linux·macOS (영구히 하려면 ~/.bashrc·~/.zshrc 에 추가)
# $env:KIS_DEFAULT_PROFILE="isa"         # Windows PowerShell
```

### 이름은 라벨일 뿐

프로필 이름은 그저 라벨입니다. 그 계좌가 실전인지 모의인지, 주문이 되는지는 **이름이 아니라 저장된
값**이 정합니다 -- 환경은 프로필에 저장된 필드(기본 `real`, 모의는 `environment="paper"`), 주문 가능
여부는 계좌의 **상품코드**([상품코드 표](#계좌번호와-상품코드)). 그래서 `main` 이라는 이름에 모의
계좌를 담아도 되고, 연금저축을 담아도 됩니다.

## 자격증명 넣기 (세 가지)

세션은 **환경변수 → `credentials.json`** 순으로 찾습니다. 아래 셋 중 **하나만** 쓰면 됩니다.

| 방법 | 언제 | 요약 |
|---|---|---|
| ① `KISConfig(...).save()` | **권장** | 정해진 위치에 안전 저장(사용자만 읽게 잠금·병합) |
| ② 환경변수 | CI·컨테이너 | `export KIS_APP_KEY=…`(명명 프로필은 `KIS_<이름대문자>_*`) |
| ③ 직접 전달 | 일회성 | `KISClient(app_key=…, …)`(둘 다 직접 주면 파일 안 읽음) |

**① 파이썬에서 저장** (파이썬 코드로 실행) -- 한 번 저장해 두면 이후 `KISClient(profile="...")` 로 엽니다.

```python
from kis_trader import KISConfig
KISConfig(profile="main",    app_key="...", app_secret="...", account="12345678-01").save()
KISConfig(profile="pension", app_key="...", app_secret="...", account="87654321-22").save()
KISConfig(profile="paper",   app_key="...", app_secret="...", account="...", environment="paper").save()
```

**② 환경변수** (터미널/셸 프롬프트에서 실행) -- `export` 해두면 `KISClient()` 가 자동으로 읽습니다.

```bash
export KIS_APP_KEY=YOUR_APP_KEY
export KIS_APP_SECRET=YOUR_APP_SECRET
export KIS_ACCOUNT=12345678-01        # 종합계좌번호-상품코드 (시세만 볼 거면 생략 가능)
# export KIS_ENVIRONMENT=paper        # 모의 계좌면
```

어느 방법이든 **키 이름 규칙은 하나** -- 환경변수에서 `KIS_` 를 떼고 소문자로 바꾸면 파일 안 키입니다.

| 환경변수(셸) | `credentials.json` 안 | 뜻 |
|---|---|---|
| `KIS_APP_KEY` | `app_key` | 앱키 |
| `KIS_APP_SECRET` | `app_secret` | 앱시크릿 |
| `KIS_ACCOUNT` | `account` | 계좌번호 |
| `KIS_ENVIRONMENT` | `environment` | 실전/모의 |
| `KIS_DEFAULT_PROFILE` | `default_profile`(최상위) | 기본 프로필 |

접두어 없는 `KIS_APP_KEY` 는 `main` 프로필용이고, 다른 프로필은 이름을 끼웁니다 --
`KIS_ISA_APP_KEY` ↔ `credentials.json` 의 `isa.app_key`.

**③ 직접 전달** (파이썬 코드로 실행) -- 저장 없이 그때만 씁니다. (앱키·앱시크릿을 **둘 다** 주면 파일을 읽지 않음)

```python
from kis_trader import KISClient
kis = KISClient(app_key="...", app_secret="...", account="12345678-01")
```

::: {.callout-note}
CLI 에서도 `--profile` 로 계좌·환경을 고릅니다: `kis --profile isa account balance`
:::

## 계좌번호와 상품코드

계좌번호 = **종합계좌번호(8자리)-상품코드(2자리)** (예: `12345678-01`). 같은 종합계좌라도 거래 상품마다
상품코드가 다릅니다(해외 주식은 위탁 `01`, 해외 선물옵션은 `08`).

| 상품코드 | 상품 | 조회 | 주문 |
|:--:|---|:--:|:--:|
| `01` | 위탁 · ISA · RIA · 국내/해외 **주식** | O | O |
| `03` | 국내선물옵션 | O | O |
| `08` | 해외선물옵션 | O | O |
| `22` | 연금저축 | O | O |
| `29` | IRP(개인형퇴직연금) | O | X |
| `55` | DC가입자 | X | X |

IRP(`29`)는 세션이 주문을 자동으로 막고, DC가입자(`55`)는 세션 생성 자체가 거부됩니다.
(출처: 한국투자 Open API 공식 FAQ.)

## 파일은 어디에 있나

패키지는 홈 폴더 아래 두 곳을 씁니다 — **사용자가 만든 설정**(`.config`)과 **패키지가 자동으로 만드는
캐시**(`.cache`):

```
~/
├── .config/kis-trader/
│   └── credentials.json               # [설정] 사용자가 저장한 앱키·앱시크릿·계좌 (프로필별)
└── .cache/kis-trader/
    ├── tokens/                        # [캐시] 발급받은 접속 토큰 -- 유효한 동안 재사용
    │   ├── real-<app-key-hash>.json   #   실전 계좌 토큰
    │   └── paper-<app-key-hash>.json  #   모의 계좌 토큰
    └── masters/                       # [캐시] 종목마스터(이름↔코드) -- 처음 받아 저장
        ├── kospi_code.mst             #   국내 .mst (KOSPI·KOSDAQ)
        ├── kosdaq_code.mst
        ├── nasmst.cod                 #   해외 .cod (거래소별: NAS·NYS·AMS·도쿄·홍콩·상하이·선전 …)
        ├── nysmst.cod
        └── …
```

- **`.config/` — 설정**: 사용자가 만든 값. 지우면 다시 만들어야 합니다.
- **`.cache/` — 캐시**: 패키지가 알아서 만들고 재사용합니다. **지워도 안전** -- 다음 실행에 다시 만듭니다.
- Windows 는 `C:\Users\<사용자>\` 아래 같은 `.config` / `.cache` 구조입니다.

**왜 캐시가 필요한가**: KIS 는 조회·주문 전에 앱키·앱시크릿으로 **접속 토큰(access token)** 을
발급받아야 합니다. 이 토큰은 발급 후 일정 시간(하루 단위) 유효해서, 매번 새로 받지 않고 `tokens/` 에
저장해 두고 재사용합니다(재발급 과속으로 인한 `status 500` 도 예방). 종목 이름↔코드를 찾는
**종목마스터** 도 큰 파일이라 처음 받은 뒤 `masters/` 에 캐시합니다. 이 캐시 파일들은 설치(`pip`)에
들어있지 않고, **처음 필요할 때 KIS 배포 서버에서 자동으로 내려받습니다** — 그래서 설치 직후 첫 조회만
잠깐 느릴 수 있고, 그 뒤로는 캐시를 재사용합니다.

## credentials.json

설정 파일 하나에 프로필별 객체를 담습니다. `KISConfig(...).save()` 가 아래 형식으로 기록하고
(손 편집도 가능), 저장할 때 **파일 소유자만 읽을 수 있게 잠급니다**:

```json
{
  "main":      {"app_key": "...", "app_secret": "...", "account": "12345678-01", "environment": "real"},
  "pension":   {"app_key": "...", "app_secret": "...", "account": "87654321-22", "environment": "real"},
  "paper":     {"app_key": "...", "app_secret": "...", "account": "...",         "environment": "paper"}
}
```

| OS | `credentials.json` 위치 | 잠금 |
|---|---|---|
| Linux | `~/.config/kis-trader/` | `600` — 소유자만 (자동) |
| macOS | `~/.config/kis-trader/` | `600` — 소유자만 (자동) |
| Windows | `C:\Users\<사용자>\.config\kis-trader\` | 사용자 폴더 보호에 의존 |

이 파일에는 앱키·앱시크릿이 들어 있어, 패키지가 **사용자만 접근하게** 잠급니다(Linux·macOS는 자동, Windows는 폴더 보호에 의존 -- 아래).

- **파일 잠금** — Linux·macOS 는 `600` 권한(소유자만 읽기·쓰기)으로 잠급니다(`600` = 파이썬 `0o600`,
  터미널 `chmod 600` 과 같은 값). Windows 는 이 권한 비트가 없어 파일 자체엔 안 걸리는 대신,
  `C:\Users\<사용자>` 폴더가 계정별로 분리돼 있어 다른 사용자가 못 봅니다.
- **로그에 안 새기** — 키를 화면·로그·예외 메시지에 찍지 않습니다(`repr` 이 값을 가림).
- **명령줄로 안 받기** — 앱키·앱시크릿은 플래그(`--app-key ...`)로 받지 않습니다. 셸 기록과 `ps` 에
  남기 때문입니다. 저장 파일이나 환경변수로만 주세요.

::: {.callout-note}
**(고급)** 파일 위치를 바꾸려면 `XDG_CONFIG_HOME` / `XDG_CACHE_HOME` 환경변수를, 테스트·격리는
`config_dir=` 인자를 씁니다.
:::

## 안 될 때 (자주 겪는 문제)

증상 열은 실제 메시지에 **포함된 문구**입니다(CLI 는 앞에 `요청 실패:` 같은 접두어가 붙기도 함) -- 그대로 검색해 맞는 행을 찾으세요.

| 증상(실제 메시지 / 상황) | 원인 | 해결 |
|---|---|---|
| `command not found: kis` | 설치 안 됐거나 도구 경로가 PATH 밖 | `uv tool install kis-trader`(또는 `pipx install kis-trader`)로 설치. 이미 설치했는데 안 잡히면 셸을 새로 열기(`pipx` 는 `pipx ensurepath` 후) |
| `KIS_APP_KEY 를 찾을 수 없다 …` | 자격증명 미설정·키 이름 오타 | 프로필 이름·필드명 확인, 또는 `KISConfig(...).save()` 로 저장 |
| `… credentials.json 가 있으나 읽을 수 없다` | JSON 문법 오류(쉼표·따옴표) | 마지막 쉼표 제거, 모든 값 `"..."` 확인 |
| `KIS HTTP 요청에 실패했다 (status 500)` | **호출 과속**(rate 제한) | 몰아 붙여넣지 말고 한 줄씩. 모의는 초당 1회 |
| 잔고가 비거나 이상함 | 모의 계좌를 보고 있음 | 실전 프로필(`--profile main` 등)로 열기 |
| `이 계좌는 API 주문이 불가하다 …`(주문 시) | IRP(상품코드 `29`) — 조회만 가능 | 주문은 위탁·ISA·연금저축 계좌에서 |
| `상품계좌종류 55(DC가입자)는 … 이용이 불가하다`(세션 생성 시) | DC가입자(상품코드 `55`) | API 이용 자체 불가 |
