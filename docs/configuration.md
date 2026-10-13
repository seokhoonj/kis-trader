# 자격증명과 프로필

세션을 열려면 KIS 개발자센터에서 발급한 **앱키·앱시크릿**과 **계좌번호**가 필요합니다(발급은
[앱키 발급받기](appkey.md)). 가장 간단하게는 이 값을 그대로 넘겨 세션을 엽니다:

```python
from kis_trader import KISClient

kis = KISClient(app_key="YOUR_APP_KEY", app_secret="YOUR_APP_SECRET", account="12345678-01",
                environment="real")
```

하지만 이러면 **열 때마다 키를 다시 적어야 하고, 코드에 키가 그대로 남습니다.** 그래서 보통은 값을 한 번
**프로필**로 저장해 두고, 다음부터는 이름만으로 엽니다:

```python
from kis_trader import KISConfig

KISConfig(profile="general", app_key="YOUR_APP_KEY", app_secret="YOUR_APP_SECRET",
          account="12345678-01", environment="real").save()

KISClient(profile="general", environment="real")   # 저장된 자격증명을 자동으로 읽어 세션을 엽니다
```

이 장은 그 **프로필**과 **환경**을 다룹니다 -- 각각이 무엇인지, 자격증명을 어떻게 넣는지 차례로 봅니다.

## 두 축 -- 프로필(계좌 종류)과 환경(실전/모의)

세션은 **두 가지**로 정해집니다 -- **프로필**은 어떤 **계좌 종류**를 여는지, **환경**은 **실전이냐
모의냐**를 고릅니다. 둘은 서로 다른 축이라, 같은 프로필을 환경만 바꿔 실전·모의로 각각 엽니다:

```python
KISClient(profile="general", environment="real")    # 종합위탁 실계좌
KISClient(profile="general", environment="paper")   # 같은 종합위탁, 모의
```

프로필은 **계좌 하나**를 여는 데 필요한 값을 묶은 것입니다 -- 그 계좌의 앱키·앱시크릿·계좌번호. 계좌마다
프로필을 따로 두는 이유는 앱키·앱시크릿이 종합계좌번호(CANO) 단위라 계좌마다 다르고, 같은 유형 계좌도
여럿(연금저축 2개 등)일 수 있기 때문입니다. 이름은 계좌 종류를 나타내도록 소문자·숫자·밑줄로 짓습니다.
흔한 예:

| 프로필 이름 (자유롭게) | 담는 계좌 |
|---|---|
| `general` | 종합위탁(국내·해외 주식·채권) |
| `derivatives` | 국내 선물옵션 |
| `isa` | ISA(개인종합자산관리) |
| `pension` | 연금저축 |
| `irp` | IRP(개인형퇴직연금, 조회 전용) |
| `overseas_derivatives` | 해외 선물옵션 |

실전·모의는 프로필 이름이 아니라 **환경**으로 고릅니다. 모의가 없는 계좌(연금저축·ISA·IRP·해외선물옵션)는
모의 슬롯을 비워 두면 됩니다 -- 그 계좌는 실전으로만 엽니다.

### 환경을 지정하지 않으면 (기본 환경)

환경을 생략하면 이 순서로 정하며, 아무것도 없으면 **모의(`paper`)** 입니다 -- 실주문은 환경을 명시해야만
닿습니다(fail-safe):

1. 호출 인자 `environment=`
2. `KIS_DEFAULT_ENVIRONMENT` **환경변수**
3. `credentials.json` 에 기록한 `default_environment` 마커(`KISConfig.set_default_environment`)
4. 그래도 없으면 -- `paper`(모의)

```python
from kis_trader import KISConfig

KISConfig.set_default_environment("real")   # credentials.json 최상위에 "default_environment": "real" 기록
```

::: {.callout-note}
기본 환경이 모의면 환경을 생략한 세션은 모의 rate limit(초당 1회)로 돕니다. 실전(초당 15회)은
환경을 `real` 로 고를 때 적용됩니다.
:::

### 이름을 지정하지 않으면 (기본 프로필)

`KISClient()` 처럼 이름 없이 열면 **기본 프로필**이 열립니다. 무엇이 기본인지는 이 순서로 정합니다:

1. `KIS_DEFAULT_PROFILE` **환경변수**가 있으면 그 이름
2. 없으면 -- `credentials.json` 에 **기본으로 지정해 둔 프로필**(아래 `KISConfig.set_default_profile`)
3. 그것도 없으면 -- `credentials.json` 의 **해석된 환경 블록에서 먼저 나오는 프로필**(`real` 블록을
   먼저 병합하므로 `real` 쪽에 있는 이름이 앞섭니다)

즉 `paper` 에 먼저 저장한 뒤 다른 프로필을 `real` 에 저장하면 그 `real` 프로필이 기본이 됩니다. 어느
프로필이 기본인지 분명히 해 두려면 아래 둘 중 하나로 **기본 프로필을 명시**하세요.

**파일에 영구 기록** (재시작해도 유지, 셸 설정 불필요) -- 저장된 프로필 하나를 기본으로 지정합니다:

```python
from kis_trader import KISConfig

KISConfig.set_default_profile("general")   # credentials.json 최상위에 "default_profile": "general" 기록
```

**환경변수** (그 셸 세션에서만, 파일보다 우선) -- `KIS_DEFAULT_PROFILE` 은 OS 환경변수라 셸에서 정합니다:

```bash
export KIS_DEFAULT_PROFILE=general       # Linux·macOS (영구히 하려면 ~/.bashrc·~/.zshrc 에 추가)
# $env:KIS_DEFAULT_PROFILE="general"     # Windows PowerShell
```

### 이름은 계좌 종류, 환경은 별도

프로필 이름은 **계좌 종류**를 가리키는 라벨입니다. 그 계좌가 실전인지 모의인지는 이름이 아니라
**환경**이 정하고, 주문 가능 여부는 계좌의 **상품코드**([상품코드 표](#계좌번호와-상품코드))가 정합니다.
같은 프로필을 실전·모의 양쪽에 저장해 두고 환경만 바꿔 쓰면 되므로, 실전과 모의를 별도 이름으로
나눌 필요가 없습니다.

## 자격증명 넣기 (세 가지)

세션은 **환경변수 → `credentials.json`** 순으로 찾습니다. 아래 셋 중 **하나만** 쓰면 됩니다.

| 방법 | 언제 | 요약 |
|---|---|---|
| ① 파일에 저장 | **권장** | 터미널에서 `kis config`(대화형) 또는 코드로 `KISConfig(...).save()` -- 정해진 위치에 안전 저장(사용자만 읽게 잠금·병합) |
| ② 환경변수 | CI·컨테이너 | `export KIS_<프로필>_<환경>_APP_KEY=…`(두 축 키) |
| ③ 직접 전달 | 일회성 | `KISClient(app_key=…, environment=…, …)`(둘 다 직접 주면 파일 안 읽음) |

**① 파일에 저장** (권장) -- 한 번 저장해 두면 이후 `KISClient(profile="...", environment="...")` 로 엽니다.
터미널에서 대화형으로 넣는 방법과 파이썬 코드로 넣는 방법이 있습니다.

`kis config` 는 자격증명을 다루는 명령 묶음입니다. 아무 하위명령 없이 `kis config` 를 실행하면
대화형 저장(`setup`)이 시작돼, 계좌 종류별로 실전·모의 앱키를 차례로 묻습니다(없는 환경은 Enter 로
건너뜀). 앱키·앱시크릿은 화면에 보이지 않게 입력받습니다(파이썬을 쓰지 않아도 됩니다).

| 하위명령 | 하는 일 |
|---|---|
| `kis config setup` | 자격증명을 대화형으로 저장(`kis config` 와 같음) |
| `kis config show` | 저장된 설정을 마스킹해 보여줌(시크릿 비노출) |
| `kis config set <프로필>.<환경>.<필드> [값]` | 한 필드 수정(`app_key`·`app_secret`·`account`·`account_password`; 비밀 아닌 `account` 만 값을 바로 줄 수 있고 나머지는 숨김 프롬프트) |
| `kis config default` | 현재 기본 프로필·환경 조회 |
| `kis config default profile <프로필>` | 기본 프로필 지정(이름 없이 열 때 여는 프로필) |
| `kis config default environment <real\|paper>` | 기본 환경 지정(생략 시 여는 환경; `env` alias 가능) |
| `kis config set-hts-id <hts_id>` | HTS 로그인 아이디 저장(조건검색·관심종목용) |
| `kis config migrate [--yes]` | 이전 형식(평평한) `credentials.json` 을 두 축 형식으로 변환(`--yes` 는 비대화형으로 기본 병합 자동 승인; 이름 충돌이면 거부) |

실전(real) 슬롯은 저장 전에 한 번 더 확인하고, 같은 자리가 이미 있으면 덮어쓸지 묻습니다.
`setup` 에 `--profile` 을 주면 그 항목은 묻지 않으며, `--set-default` 를 붙이면 저장 후 기본
프로필로 지정합니다.

여러 프로필을 한꺼번에 저장하거나 스크립트에 넣을 때는 파이썬 코드가 편합니다. `environment` 로
실전·모의 슬롯을 고릅니다(생략 시 모의).

```python
from kis_trader import KISConfig

KISConfig(profile="general", app_key="...", app_secret="...", account="12345678-01", environment="real").save()
KISConfig(profile="general", app_key="...", app_secret="...", account="50123456-01", environment="paper").save()
KISConfig(profile="pension", app_key="...", app_secret="...", account="87654321-22", environment="real").save()
```

::: {.callout-note}
`save()` 는 **한 번만** 실행하면 됩니다 -- 값을 `credentials.json` 에 영구히 기록하므로,
그다음부터는 저 저장 코드 없이 `KISClient(profile="general", environment="real")` 한 줄로 엽니다.
:::

**② 환경변수** (터미널/셸 프롬프트에서 실행) -- `export` 해두면 세션이 자동으로 읽습니다. 환경변수 키는
**프로필과 환경을 모두** 담습니다 -- `KIS_<프로필대문자>_<환경대문자>_<필드>`.

```bash
export KIS_GENERAL_REAL_APP_KEY=YOUR_APP_KEY
export KIS_GENERAL_REAL_APP_SECRET=YOUR_APP_SECRET
export KIS_GENERAL_REAL_ACCOUNT=12345678-01   # 종합계좌번호-상품코드 (시세만 볼 거면 생략 가능)
export KIS_DEFAULT_PROFILE=general            # 환경변수만으로 열 때는 기본 프로필을 지정
export KIS_DEFAULT_ENVIRONMENT=real           # (생략 시 모의)
```

필드별 키의 모양과 공유·기본값 키:

| 환경변수(셸) | 뜻 |
|---|---|
| `KIS_<프로필>_<환경>_APP_KEY` | 앱키 (예 `KIS_GENERAL_PAPER_APP_KEY`) |
| `KIS_<프로필>_<환경>_APP_SECRET` | 앱시크릿 |
| `KIS_<프로필>_<환경>_ACCOUNT` | 계좌번호 |
| `KIS_<프로필>_<환경>_ACCOUNT_PASSWORD` | 계좌비밀번호(해당 슬롯) |
| `KIS_HTS_ID` | HTS 로그인 아이디(공유·사용자당 하나; 조건검색·관심종목용, 인증엔 안 씀) |
| `KIS_ACCOUNT_PASSWORD` | 계좌비밀번호 공유 fallback (일부 조회의 `ACNT_PWD`; 로그·`_raw` 에 안 담김) |
| `KIS_DEFAULT_PROFILE` | 기본 프로필 |
| `KIS_DEFAULT_ENVIRONMENT` | 기본 환경(real/paper) |

필드 하나하나가 독립이라 파일에 둔 슬롯의 한 값만 환경변수로 덮어쓸 수 있고, 키 이름이 슬롯을
고정하므로 다른 (프로필, 환경) 조합으로 샐 수 없습니다.

**③ 직접 전달** (파이썬 코드로 실행) -- 저장 없이 그때만 씁니다. 앱키·앱시크릿을 **둘 다** 주면 파일을
읽지 않으며, 이때는 어느 서버로 가는지 분명히 하도록 `environment` 를 **반드시** 줍니다.

```python
from kis_trader import KISClient

kis = KISClient(app_key="...", app_secret="...", account="12345678-01", environment="real")
```

::: {.callout-note}
CLI 에서는 `--profile` 로 계좌 종류를, `--environment` 로 실전/모의를 고릅니다:
`kis --profile isa --environment real account balance`
:::

## 계좌번호와 상품코드

계좌번호 = **종합계좌번호(8자리)-상품코드(2자리)** (예: `12345678-01`). 같은 종합계좌라도 거래 상품마다
상품코드가 다릅니다(해외 주식은 위탁 `01`, 해외 선물옵션은 `08`).

| 상품코드 | 상품 | 조회 | 주문 |
|:--:|---|:--:|:--:|
| `01` | 위탁 · ISA · 국내/해외 **주식** | O | O |
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
        ├── kospi_code.mst             #   국내 .mst -- 코스피(KOSPI)
        ├── kosdaq_code.mst            #   국내 .mst -- 코스닥(KOSDAQ)
        ├── nasmst.cod                 #   해외 .cod -- 나스닥(NASDAQ, 미국)
        ├── nysmst.cod                 #   해외 .cod -- 뉴욕(NYSE, 미국)
        ├── amsmst.cod                 #   해외 .cod -- 아멕스(AMEX, 미국)
        ├── shsmst.cod                 #   해외 .cod -- 상하이 증권거래소
        ├── shimst.cod                 #   해외 .cod -- 상하이 지수
        ├── szsmst.cod                 #   해외 .cod -- 선전 증권거래소
        ├── szimst.cod                 #   해외 .cod -- 선전 지수
        ├── tsemst.cod                 #   해외 .cod -- 도쿄
        ├── hksmst.cod                 #   해외 .cod -- 홍콩
        ├── hnxmst.cod                 #   해외 .cod -- 하노이
        └── hsxmst.cod                 #   해외 .cod -- 호치민
```

- **`.config/` — 설정**: 사용자가 만든 값. 지우면 다시 만들어야 합니다.
- **`.cache/` — 캐시**: 패키지가 알아서 만들고 재사용합니다. **지워도 안전** -- 다음 실행에 다시 만듭니다.
- Windows 는 `C:\Users\<사용자>\` 아래 같은 `.config` / `.cache` 구조입니다.

**왜 캐시가 필요한가** — 두 가지를 처음 한 번만 받아 재사용합니다:

| 캐시 | 무엇 | 왜 저장하나 |
|---|---|---|
| `tokens/` | **접속 토큰** — 앱키·앱시크릿으로 발급, 하루 단위 유효 | 조회·주문마다 새로 받지 않고 재사용 (재발급 과속으로 인한 `status 500` 예방) |
| `masters/` | **종목마스터** — 이름↔코드 대응표 | 큰 파일이라 처음 한 번만 받아 재사용 |

둘 다 설치(`pip`)에는 들어있지 않고, **처음 필요할 때 KIS 배포 서버에서 자동으로 내려받습니다** — 그래서
설치 직후 첫 조회만 잠깐 느릴 수 있고, 그 뒤로는 캐시를 재사용합니다.

## credentials.json

설정 파일 하나에 공유 최상위 필드와 환경별·프로필별 객체를 함께 담습니다. 자격증명은 **환경(`real`/
`paper`) 아래에 프로필**로 묶여, 프로필이 계좌 종류를, 바깥 블록 키가 환경을 정합니다. `KISConfig(...).save()`
가 아래 형식으로 기록하고(손 편집도 가능), 저장할 때 **파일 소유자만 읽을 수 있게 잠급니다**:

```json
{
  "schema": 2,
  "hts_id": "your_hts_id",
  "account_password": "****",
  "default_environment": "paper",
  "default_profile": "general",
  "real": {
    "general": {"app_key": "...", "app_secret": "...", "account": "12345678-01"},
    "pension": {"app_key": "...", "app_secret": "...", "account": "87654321-22"}
  },
  "paper": {
    "general": {"app_key": "...", "app_secret": "...", "account": "50123456-01"}
  }
}
```

`schema: 2` 표시가 이 형식을 나타냅니다. 묶음 안에는 `environment` 필드를 두지 않습니다 -- 환경은
오직 바깥 블록(`real`/`paper`) 키가 정합니다. 모의가 없는 계좌는 `paper` 블록에 넣지 않으면 됩니다
(없음 = "그 계좌는 모의로 열 수 없음"). 최상위 `hts_id` 는 조건검색·관심종목 조회의 `user_id`(HTS
로그인 아이디)로, 사용자당 하나라 모든 프로필이 공유합니다(인증엔 안 씀). `KISConfig.set_hts_id("your_hts_id")`
로 기록하거나 손 편집합니다. 런타임은 두 축 schema-2 형식만 읽습니다. 예전 형식(평평한, `schema`
표시가 없는) `credentials.json` 은 `kis config migrate` 로 한 번 변환해야 하며, 변환 전까지는 세션을
열 때 변환을 안내하는 오류로 멈춥니다(조용히 열리거나 생략된 환경이 실전(`real`)으로 잡히지 않습니다).
환경을 생략하면 schema-2 파일은 모의(`paper`)가 기본입니다 -- 실전(`real`)은 명시해야 닿습니다.

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
| `… APP_KEY 를 찾을 수 없다 …`(예 `KIS_GENERAL_REAL_APP_KEY …`) | 자격증명 미설정·키 이름 오타 | 프로필·환경·필드명 확인, 또는 `kis config` / `KISConfig(...).save()` 로 저장 |
| `… credentials.json 가 있으나 읽을 수 없다` | JSON 문법 오류(쉼표·따옴표) | 마지막 쉼표 제거, 모든 값 `"..."` 확인 |
| `KIS HTTP 요청에 실패했다 (status 500)` | **호출 과속**(rate 제한) | 몰아 붙여넣지 말고 한 줄씩. 모의는 초당 1회 |
| 잔고가 비거나 이상함 | 모의 환경을 보고 있음 | 실전 환경(`--environment real`)으로 열기 |
| `이 계좌는 API 주문이 불가하다 …`(주문 시) | IRP(상품코드 `29`) — 조회만 가능 | 주문은 위탁·ISA·연금저축 계좌에서 |
| `상품계좌종류 55(DC가입자)는 … 이용이 불가하다`(세션 생성 시) | DC가입자(상품코드 `55`) | API 이용 자체 불가 |
