# 자격증명과 프로필

세션을 열려면 KIS 개발자센터에서 발급한 **앱키·앱시크릿**이 필요합니다(발급은
[앱키 발급받기](appkey.md)). 주문·잔고 등 계좌 조회에는 **계좌번호**도 넣습니다. 가장 간단하게는
이 값을 그대로 넘겨 세션을 엽니다:

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

## 폴더 한눈에

패키지가 쓰는 폴더는 셋입니다 -- 내가 저장한 **설정**, 자동으로 만들어지는 **캐시**, 그리고 계좌별 CLI 세션이 사용하는 **상태**(주문 요청 ID별 중복방지 기록):

```
~/
├── .config/kis-trader/credentials.json    # [설정] 내가 저장한 앱키·시크릿·계좌 (프로필별)
├── .cache/kis-trader/
│   ├── tokens/     # [캐시] 접속 토큰 -- 자동 발급·재사용 (real/paper 별로 생김)
│   └── masters/    # [캐시] 종목마스터(이름↔코드) -- 처음 쓸 때 자동 다운로드
└── .local/state/kis-trader/orders/         # [상태] CLI 주문 요청 ID별 기록 -- 세션 구성 시 생길 수 있음, 지우지 마세요
```

- `.config` 는 **내가 넣은 값**입니다. `tokens/` 와 `masters/` 는 다시 만들 수 있는 캐시지만,
  이전 버전의 `orders/` 기록이 캐시 경로에 남아 있을 수 있으므로 캐시 폴더 전체를 지우지 마세요.
- CLI는 계좌가 있는 세션에 파일 기반 주문 저장소를 사용하며, 세션 구성 중 폴더·잠금 파일이 생길 수 있습니다.
  같은 `client_order_id` 의 재실행을 구별하는 기록이므로 **지우면 안 됩니다**. SDK와 MCP의 기본
  저장소는 메모리이며, SDK에서 재시작 후에도 보존하려면 파일 경로를 지정한 `OrderStore` 를 주입합니다.
- Windows 는 `C:\Users\나\` 아래 같은 구조입니다.
- **MCP(Claude Desktop)로 쓰면** Claude 쪽 설정 파일(`claude_desktop_config.json`)도 하나 더 씁니다 -- 이건
  kis-trader 가 아니라 **Claude Desktop 이** 쓰는 파일이라 위치가 다릅니다(아래 [파일은 어디에 있나](#파일은-어디에-있나)).

### 기존 주문 기록의 저장 위치 이전

기본 주문 기록은 `$XDG_STATE_HOME/kis-trader/orders` 에 보관합니다. POSIX 환경에서는 이전 버전의
`$XDG_CACHE_HOME/kis-trader/orders` 경로를 이 물리적 디렉터리로 연결하는 **디렉터리 별칭**을 둡니다.
기존 캐시 경로에 실제 주문 디렉터리가 있으면 일반 실행에서 자동으로 옮기지 않고 중단합니다.

Windows·macOS에서는 현재 이 이전 절차를 지원하지 않습니다. 지원되는 환경에서 이전이 필요하면
다음 순서로 진행하세요:

1. **모든 계좌**의 구·신 버전 프로그램과 작업을 종료하고 자동 재시작도 중지합니다.
   완료할 때까지 정지 상태를 유지해야 합니다. 아래 플래그 자체가 구 버전의 시작을 차단하지는 않습니다.
2. 기존 프로그램과 같은 `XDG_CACHE_HOME`·`XDG_STATE_HOME` 설정에서 실행합니다:

   ```bash
   kis config migrate-orders --offline
   ```

3. 성공하면 보존된 `.orders-backup-<작업 ID>` 디렉터리와 상태 경로를 확인한 뒤 다시 시작합니다.
   백업은 기존 캐시의 `orders` 와 같은 부모 아래에 남으며 자동 삭제되지 않습니다.

이 명령은 자격증명이나 계좌에 접속하지 않고 **기본 XDG 주문 디렉터리 전체**를 처리합니다.
`config_dir=`·CLI `--config-dir` 로 지정한 별도 저장소는 이 명령으로 이전하지 않습니다.
주문 내용·스키마·소유 계좌를 바꾸지 않으므로, 범위 정보가 없는 과거 기록은 별도의
[소유 범위 확인과 채택](orders.md#안전장치-왜-이렇게-만들었나)이 필요할 수 있습니다.

잠금이 사용 중이거나 양쪽에 독립 기록이 있으면 병합·덮어쓰기 없이 거부합니다. 중단됐다면 모든
프로그램의 정지를 유지하고 같은 명령으로 재개하세요. 기록된 단계와 파일을 검증할 수 없으면
재개도 거부하므로 원본·임시 디렉터리·백업을 그대로 보존하고 점검해야 합니다.

캐시 별칭을 삭제한 뒤에는 **구 버전을 실행하지 말고** 위 오프라인 명령으로 별칭을 먼저 복구하세요.
별칭이 사라진 상태에서 구 버전이 독립 기록을 만들면 자동으로 합칠 수 없습니다. 새 스키마를
읽지 못하는 구 버전은 공유 기록을 거부할 수 있으므로, 이 절차가 다운그레이드 호환성을 보장하지는 않습니다.

기본 Windows 배치는 디렉터리 별칭 없이 물리적 상태 경로를 사용합니다. 오프라인 이전은 플랫폼의
덮어쓰기 없는 디렉터리 설치 기능이 필요하며, 지원하지 않는 플랫폼·파일시스템에서는 원본을
보존하고 거부합니다. 현재 Windows·macOS에서는 기존 주문 디렉터리의 오프라인 이전을 지원하지 않습니다.
Windows에서는 기존 디렉터리의 이전이나 미완료 이전의 재개를 파일 변경 전에 명확히 거부합니다.
Windows의 새 저장소 초기화와 이미 준비된 state 저장소 사용은 지원하며, 기존 기록의 소유 범위 확인은 별도로 필요합니다.

각 파일의 자세한 설명과 전체 트리는 [파일은 어디에 있나](#파일은-어디에-있나) 에 있습니다.

## 두 축 -- 프로필(계좌 종류)과 환경(실전/모의)

세션은 **두 가지**로 정해집니다 -- **프로필**은 어떤 **계좌 종류**를 여는지, **환경**은 **실전이냐
모의냐**를 고릅니다. 둘은 서로 다른 축이라, 같은 프로필을 환경만 바꿔 실전·모의로 각각 엽니다:

```python
KISClient(profile="general", environment="real")    # 종합위탁 실계좌
KISClient(profile="general", environment="paper")   # 같은 종합위탁, 모의
```

프로필은 저장할 **자격증명 묶음의 이름**입니다. 각 `(프로필, 환경)` 슬롯에 앱키·앱시크릿·계좌번호를
둡니다. 같은 프로필 이름의 실전·모의 슬롯은 각각 다른 값을 가질 수 있고, 같은 유형의 계좌를 여러
프로필로 나눠 저장할 수도 있습니다. 앱키의 발급 단위나 연결 계좌는 KIS 신청 정보에서 확인해 입력하세요.
이름은 소문자·숫자·밑줄로 짓습니다. 계좌 용도를 이름에 담은 흔한 예:

| 프로필 이름 (자유롭게) | 담는 계좌 | 상품코드 | 실전 | 모의 | 주문 |
|---|---|:--:|:--:|:--:|:--:|
| `general` | 종합위탁(국내·해외 주식·채권) | 01 | O | O | O |
| `isa` | ISA(개인종합자산관리) | 01 | O | 확인 필요 | O |
| `pension` | 연금저축 | 22 | O | 확인 필요 | O |
| `irp` | IRP(개인형퇴직연금) | 29 | O | 확인 필요 | X |
| `derivatives` | 국내 선물옵션 | 03 | O | O | O |
| `overseas_derivatives` | 해외 선물옵션 | 08 | O | X | O |

- 표의 조회·주문 범위는 계좌 상품별 안내이며, 개별 API의 지원 조건도 확인해야 합니다.
  모의 칸이 **확인 필요**인 계좌는 KIS 공식 안내와 신청 화면에서 지원 여부를 확인한 뒤 해당 슬롯을 설정하세요.
  해외선물옵션의 모의투자 미지원은 [KIS 공식 FAQ](https://apiportal.koreainvestment.com/community/10000000-0000-0011-0000-000000000002/post/fa0abb8f-d55c-424f-8eb7-55f2f8ed7eae)를 참고하세요.
- **주문 X** 는 IRP(상품코드 `29`) -- 잔고·거래내역은 보되 주문은 막힙니다. DC가입자(`55`)는 세션 생성
  자체가 거부됩니다([상품코드 표](#계좌번호와-상품코드)).
- 실전·모의는 프로필 이름이 아니라 **환경**으로 고릅니다 -- 같은 프로필을 두 환경에 저장해 두고 환경만
  바꿔 엽니다.

### 환경을 지정하지 않으면 (기본 환경)

환경을 생략하면 아래 순서로 정합니다. 인자·환경변수·저장된 기본값이 모두 없을 때만
**모의(`paper`)** 입니다. 기본값을 `real` 로 지정했다면 환경 인자를 생략해도 실전으로 열립니다:

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
3. 그것도 없으면 -- **해석된 환경 블록에서 먼저 나오는 프로필**. 그 블록에 이름이 없으면 다른
   환경 블록의 이름도 확인합니다. 단, 선택한 환경에 해당 슬롯이 없으면 오류이며 다른 환경으로 바꾸지 않습니다.

예를 들어 기본 환경이 `paper` 이면 `paper` 블록의 이름을 먼저 확인합니다. 어느 프로필을 쓸지
분명히 하려면 아래 둘 중 하나로 **기본 프로필을 명시**하세요.

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

필드 값은 **환경변수 → `credentials.json`** 순으로 찾습니다. 환경변수로 값을 공급하더라도
`credentials.json` 에 선택할 프로필·환경의 슬롯이 있어야 합니다(아래 빈 슬롯 초기화 참고).
앱키와 앱시크릿을 호출 인자로 모두 직접 주는 방식은 파일을 읽지 않습니다.

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

::: {.callout-note}
`kis config` 는 **터미널에서** 치는 명령이라, 먼저 터미널을 열고 `kis` 도구가 깔려 있어야 합니다 --
터미널 여는 법과 설치(`uv tool install 'kis-trader[mcp]'`)는 [빠른 시작](quickstart.md) 또는
[MCP 서버](mcp.md)의 1~2단계를 참고하세요.
:::

| 하위명령 | 하는 일 |
|---|---|
| `kis config setup` | 자격증명을 대화형으로 저장(`kis config` 와 같음) |
| `kis config show` | 저장된 설정을 마스킹해 보여줌(시크릿 비노출) |
| `kis config list` | 저장된 프로필 이름을 `real`/`paper` 환경별로 정렬해 보여줌(시크릿 비노출) |
| `kis config set <프로필>.<환경>.<필드> [값]` | 한 필드 수정(`app_key`·`app_secret`·`account`·`account_password`; 비밀 아닌 `account` 만 값을 바로 줄 수 있고 나머지는 숨김 프롬프트) |
| `kis config default` | 파일에 저장된 기본 프로필·환경 마커 조회(환경변수로 덮어쓴 최종 해석값과 다를 수 있음) |
| `kis config default profile <프로필>` | 기본 프로필 지정(이름 없이 열 때 여는 프로필) |
| `kis config default environment real/paper` | 기본 환경 지정(생략 시 여는 환경; `env` alias 가능) |
| `kis config mcp` | Claude Desktop 설정 블록(`claude_desktop_config.json`) 출력 -- `--profile`/`--environment` 로 선택, 경로 자동 채움([MCP 서버](mcp.md)) |
| `kis config hts-id [hts_id]` | HTS 로그인 아이디 조회(인자 없음)·저장(조건검색·관심종목용) |
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

파이썬에서는 `kis_trader.config.list_profiles()` 로 저장된 프로필 이름만 읽을 수 있습니다. 환경을
생략하면 두 환경의 합집합, 지정하면 그 환경에 저장된 이름만 중복 없이 정렬해 돌려줍니다.

```python
from kis_trader.config import list_profiles

list_profiles()                    # 두 환경 합집합
list_profiles(environment="real") # 실전 환경에 저장된 프로필만
```

::: {.callout-note}
`save()` 는 **한 번만** 실행하면 됩니다 -- 값을 `credentials.json` 에 영구히 기록하므로,
그다음부터는 저 저장 코드 없이 `KISClient(profile="general", environment="real")` 한 줄로 엽니다.
:::

**② 환경변수** (터미널/셸 프롬프트에서 실행) -- 비밀값을 파일에 넣지 않고 환경변수로 공급할 수 있습니다.
먼저 설정 파일 위치(아래 [파일은 어디에 있나](#파일은-어디에-있나))의 `credentials.json` 에
프로필·환경의 **빈 슬롯**을 만듭니다. 새 파일이라면 다음 내용으로 시작합니다. 기존 파일이 있다면
덮어쓰지 말고 `real` 블록에 `general` 슬롯만 추가하세요.

```json
{
  "schema": 2,
  "real": {"general": {}},
  "paper": {}
}
```

이 파일에는 비밀값이 없습니다. Linux·macOS에서 직접 만든 파일은 `chmod 600` 으로 권한을 제한하세요.
이제 터미널에서 다음 변수를 설정합니다. 환경변수 키는 **프로필과 환경을 모두** 담습니다 --
`KIS_<프로필대문자>_<환경대문자>_<필드>`.

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
| `KIS_HTS_ID` | HTS 로그인 아이디(공유 설정값; 조건검색·관심종목용, 인증엔 안 씀) |
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

## 상황별 설정 레시피 (경우별 가이드)

자격증명을 넣는 방법은 두 가지이고, 아래의 경우마다 **두 방법을 나란히** 적어 두었습니다. 편한 쪽
하나만 따라 하면 됩니다.

- **(A) `kis config`(대화형)** -- 권장. 묻는 대로 입력하면 올바른 형식으로 저장하고, 파일을 소유자만
  읽게 잠그며, 기존 값과 안전하게 병합합니다. 앱키·시크릿은 화면에 보이지 않게 받습니다.
- **(B) `credentials.json` 직접 편집** -- 에디터로 파일을 직접 씁니다. 형식(`schema: 2`)만 지키면 됩니다.
  파일 위치는 [파일은 어디에 있나](#파일은-어디에-있나), 전체 형식은 [credentials.json](#credentials.json) 참고.

**내 경우 고르기** -- 아래에서 맞는 번호 하나만 따라 하세요.

| 내 상황 | 레시피 |
|---|:--:|
| 시세·검색만, 계좌번호 없음 | **1** |
| 계좌 하나, **실전만** (연금·ISA 등) | **2** |
| 계좌 하나, **모의만** (연습) | **3** |
| 한 계좌의 실전 + 모의 둘 다 | **4** |
| 계좌 종류 여러 개 | **5** |
| 이름·환경 없이 바로 열리게 | **6** |
| `schema` 표시가 없는 예전 파일 변환 | **7** |

직접 편집할 때 기본 구조는 항상 같습니다 -- `real`/`paper` 블록 **아래에 프로필(계좌 종류)** 을 두고,
프로필마다 `app_key`·`app_secret`·`account` 를 둡니다(묶음 안에 `environment` 는 넣지 않습니다):

```json
{
  "schema": 2,
  "real":  { "<프로필>": { "app_key": "...", "app_secret": "...", "account": "종합계좌-상품코드" } },
  "paper": { "<프로필>": { "app_key": "...", "app_secret": "...", "account": "..." } }
}
```

::: {.callout-important}
직접 편집 시: ① 맨 위 `"schema": 2` 는 **반드시** 있어야 합니다(없으면 옛 형식으로 보고 거부). ② 문법이
깨지지 않게(마지막 쉼표 금지, 모든 값 `"..."`). ③ 저장 후 권한을 `chmod 600 ~/.config/kis-trader/credentials.json`
로 잠그세요(Linux·macOS). ④ 앱키·시크릿이 든 파일이라 **깃·채팅·로그에 올리지 마세요**.
:::

### 실전(real)과 모의(paper)

계좌가 실전인지 모의인지는 **환경**이 정합니다. 실전과 모의는 KIS에서 앱키·시크릿부터 따로 발급받습니다.

| 구분 | 실전 (real) | 모의 (paper) |
|---|---|---|
| 무엇 | 실제 돈으로 매매 | 가상 자금으로 연습(체결·잔고 시뮬레이션) |
| 앱키·시크릿 | 실전용 발급 | **별도 발급** -- 실전 키로 모의 접속 불가 |
| 환경을 생략하면 | 환경변수나 저장된 기본 환경이 `real` 이면 열림 | 환경 기본값을 지정하지 않았을 때 `paper` |
| REST 호출 한도(앱키 단위 합산) | 공식 초당 20건, 패키지 기본 15건 | 공식 초당 2건, 패키지 기본 1건 |
| `credentials.json` 블록 | `"real": { … }` | `"paper": { … }` |

공식 한도는 [KIS 다계좌 API 호출 유량 FAQ](https://apiportal.koreainvestment.com/community/10000000-0000-0011-0000-000000000002/post/bcfb94ee-5aa3-4915-bca6-d63378c620c2)를 따릅니다. 패키지는 여유를 둔 기본값을 사용합니다.

아래 모의 설정 예시는 일반 주식 계좌를 사용합니다. 사용할 계좌와 API의 모의 지원 여부를 확인하고,
그 환경에 발급받은 값을 해당 슬롯에 저장하세요. 실전만 사용할 경우에는 아래 레시피에서 모의 단계를
건너뛰고 실전 슬롯만 저장하면 됩니다.

### 1. 계좌가 아예 없을 때 -- 시세·검색만

주문·잔고가 필요 없으면 **계좌번호를 넣지 않습니다**. 앱키·시크릿만 있으면 시세·검색·순위·종목정보가
열립니다.

- **(A) CLI** -- `account 계좌번호 ...:` 프롬프트에서 그냥 **Enter**(비움):
  ```bash
  kis config setup --profile general
  # real 앱키·시크릿 입력, account 는 Enter 로 비움
  ```
- **(B) 파일** -- 묶음에서 `account` 를 뺍니다:
  ```json
  { "schema": 2, "real": { "general": { "app_key": "...", "app_secret": "..." } } }
  ```

### 2. 계좌 하나만 -- 실전만 (모의 없음)

가장 흔한 경우입니다. 모의(`paper`)는 만들지 않습니다.

- **(A) CLI** -- 모의 단계에서 **APP KEY 를 비우고 Enter** 로 건너뜁니다:
  ```bash
  kis config setup --profile isa
  # [real]  실전 설정? → y, 앱키·시크릿·계좌번호 입력
  # [paper] ... APP KEY (없으면 Enter): → Enter (건너뜀)
  kis config default environment real    # 단일 실전 계좌를 인자 없이 열리게 (아래 6번)
  ```
- **(B) 파일** -- `real` 블록만 둡니다(`paper` 블록 자체를 넣지 않음):
  ```json
  {
    "schema": 2,
    "default_environment": "real",
    "real": { "isa": { "app_key": "...", "app_secret": "...", "account": "12345678-01" } }
  }
  ```

일반계좌는 `general`, 연금저축은 `pension`(계좌 `...-22`), IRP 는 `irp`(`...-29`, 조회 전용)로 바꾸면 됩니다.

### 3. 계좌 하나만 -- 모의만 (실전 없음)

연습용입니다. 환경변수나 파일의 기본 환경을 따로 지정하지 않았다면 기본이 모의이므로 **인자 없이 바로** 열립니다.

- **(A) CLI** -- 실전 단계에서 `N`(건너뜀), 모의만 입력:
  ```bash
  kis config setup --profile general
  # [real] 실전 ...? → N
  # [paper] 모의 앱키·시크릿·계좌 입력
  ```
- **(B) 파일** -- `paper` 블록만 둡니다(`default_environment` 불필요 -- 기본이 모의):
  ```json
  { "schema": 2, "paper": { "general": { "app_key": "...", "app_secret": "...", "account": "50123456-01" } } }
  ```

### 4. 한 계좌 종류의 실전 + 모의 둘 다

기본 환경을 따로 지정하지 않았다면 환경 생략 시 모의입니다. 실제 매매 환경은
`--environment real` 또는 `KIS_DEFAULT_ENVIRONMENT`/`default_environment` 의 `real` 값으로 고릅니다.
CLI 주문 전송에는 별도로 `--execute real` 과 확인 절차가 필요합니다.

- **(A) CLI** -- 두 단계 다 입력:
  ```bash
  kis config setup --profile general
  # [real]  → y, 실전 앱키·계좌
  # [paper] → 모의 앱키·계좌
  ```
- **(B) 파일** -- 두 블록에 같은 프로필 이름:
  ```json
  {
    "schema": 2,
    "real":  { "general": { "app_key": "...", "app_secret": "...", "account": "12345678-01" } },
    "paper": { "general": { "app_key": "...", "app_secret": "...", "account": "50123456-01" } }
  }
  ```

### 5. 계좌 종류 여러 개 (일반 + ISA + 연금 …)

사용할 계좌에 대해 발급받은 앱키·앱시크릿과 계좌번호를 각각의 프로필에 넣습니다.
프로필 이름 자체가 앱키의 발급 단위나 계좌 종류를 검증하지는 않습니다.

- **(A) CLI** -- 종류마다 한 번씩:
  ```bash
  kis config setup --profile general
  kis config setup --profile isa
  kis config setup --profile pension
  kis config default profile general    # 이름 없이 열 때 여는 기본 계좌
  ```
- **(B) 파일** -- 한 블록 안에 프로필을 나열:
  ```json
  {
    "schema": 2,
    "default_profile": "general",
    "real": {
      "general": { "app_key": "...", "app_secret": "...", "account": "12345678-01" },
      "isa":     { "app_key": "...", "app_secret": "...", "account": "12345678-01" },
      "pension": { "app_key": "...", "app_secret": "...", "account": "87654321-22" }
    }
  }
  ```

이 예시는 실전 슬롯만 있으므로 사용할 때 프로필과 환경을 지정합니다:
`kis --profile isa --environment real account balance`.

### 6. 이름·환경 없이 바로 열리게 (기본값 지정)

계좌가 하나뿐이어도 그 계좌가 **실전**이면, 실수로 실전 서버에 연결되지 않도록 환경을 한 번은
지정해야 합니다(모의만 있으면 기본이 모의라 그대로 열립니다).

- **(A) CLI**:
  ```bash
  kis config default profile general     # (여러 개일 때) 어느 걸 열지
  kis config default environment real    # 실전을 인자 없이 열려면 한 번
  kis config default                     # 파일에 저장된 기본값 확인
  ```
- **(B) 파일** -- 최상위에 마커 두 줄:
  ```json
  { "schema": 2, "default_profile": "general", "default_environment": "real", "real": { "general": { "…": "…" } } }
  ```
- **(C) 환경변수** -- `export KIS_DEFAULT_PROFILE=general KIS_DEFAULT_ENVIRONMENT=real` (환경변수가 마커보다 우선).

그러면 `kis account balance` / `KISClient()` 가 인자 없이 그 계좌로 열립니다.

### 7. `schema` 표시가 없는 예전 파일에서 올라온 경우

`schema` 표시가 없는 평평한 옛 파일은 런타임이 읽지 않고, 세션을 열 때 변환을 안내하며 멈춥니다. 한 번만
변환하세요. 손으로 고치는 것보다 변환기가 안전합니다(옛 형식을 읽어 블록 구조로 옮기고, 이름이 겹치면
덮어쓰지 않고 멈춤):

```bash
kis config migrate         # 대화형: 병합을 확인
kis config show            # 변환 결과 확인(비밀값 마스킹)
```

## 계좌번호와 상품코드

계좌번호 = **종합계좌번호(8자리)-상품코드(2자리)** (예: `12345678-01`). 같은 종합계좌라도 거래 상품마다
상품코드가 다릅니다(해외 주식은 위탁 `01`, 해외 선물옵션은 `08`).

**내 계좌번호 확인** -- 앞 8자리(종합계좌번호)는 HTS/MTS 또는 증권사 홈페이지의 계좌조회,
그리고 KIS 개발자센터 **신청현황**(앱키를 복사한 그 화면)에서 볼 수 있습니다. 뒤 2자리 상품코드는 아래
표에서 고릅니다(국내·해외 **주식**이면 `01`). 시세·검색만 할 거면 계좌번호는 없어도 됩니다.

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

패키지는 홈 폴더 아래 세 곳을 씁니다 — **사용자가 만든 설정**(`.config`), **패키지가 자동으로 만드는
캐시**(`.cache`), 그리고 **CLI 계좌 세션의 상태**(`.local/state`, 주문 요청 ID별 중복방지 기록):

```
~/
├── .config/kis-trader/
│   └── credentials.json               # [설정] 사용자가 저장한 앱키·앱시크릿·계좌 (프로필별)
├── .cache/kis-trader/
│   ├── tokens/                        # [캐시] 발급받은 접속 토큰 -- 유효한 동안 재사용
│   │   ├── real-<app-key-hash>.json   #   실전 계좌 토큰
│   │   └── paper-<app-key-hash>.json  #   모의 계좌 토큰
│   └── masters/                       # [캐시] 종목마스터(이름↔코드) -- 처음 받아 저장
│       ├── kospi_code.mst             #   국내 .mst -- 코스피(KOSPI)
│       ├── kosdaq_code.mst            #   국내 .mst -- 코스닥(KOSDAQ)
│       ├── nasmst.cod                 #   해외 .cod -- 나스닥(NASDAQ, 미국)
│       ├── nysmst.cod                 #   해외 .cod -- 뉴욕(NYSE, 미국)
│       ├── amsmst.cod                 #   해외 .cod -- 아멕스(AMEX, 미국)
│       ├── shsmst.cod                 #   해외 .cod -- 상하이 증권거래소
│       ├── shimst.cod                 #   해외 .cod -- 상하이 지수
│       ├── szsmst.cod                 #   해외 .cod -- 선전 증권거래소
│       ├── szimst.cod                 #   해외 .cod -- 선전 지수
│       ├── tsemst.cod                 #   해외 .cod -- 도쿄
│       ├── hksmst.cod                 #   해외 .cod -- 홍콩
│       ├── hnxmst.cod                 #   해외 .cod -- 하노이
│       └── hsxmst.cod                 #   해외 .cod -- 호치민
└── .local/state/kis-trader/
    └── orders/                        # [상태] CLI 주문 요청 ID별 기록 -- 세션 구성 시 생길 수 있음, 지우지 마세요
```

- **`.config/` — 설정**: 사용자가 만든 값. 지우면 다시 만들어야 합니다.
- **`.cache/` — 캐시**: `tokens/` 와 `masters/` 는 다시 만들 수 있습니다. 이전 버전의
  `orders/` 기록이 남아 있을 수 있으므로 캐시 폴더 전체를 삭제하지 마세요. 주문 기록을 직접 옮기거나 지우지 마세요.
- **`.local/state/.../orders/` — 상태**: CLI의 계좌별 **주문 요청 ID 기록**. 세션 구성 시 생길 수 있습니다.
  SDK와 MCP는 기본적으로 메모리 저장소를 사용합니다. 같은 `client_order_id` 로 재시도·조회할 기록을
  보존해야 하므로 파일 기반 주문 저장소는 **지우면 안 됩니다**.
- Windows 는 `C:\Users\나\` 아래 같은 `.config` / `.cache` / `.local\state` 구조입니다.

**MCP(Claude Desktop)를 쓰면** 설정 파일이 하나 더 있습니다 -- 이건 kis-trader 가 아니라 **Claude Desktop 이**
쓰는 파일이라 위치가 다릅니다([MCP 서버](mcp.md) 의 "Claude Desktop 에 연결" 단계에서 다룹니다):

```
# macOS
~/Library/Application Support/Claude/claude_desktop_config.json   # Claude Desktop 이 읽는 MCP 설정

# Windows
%APPDATA%\Claude\claude_desktop_config.json
```

여기에 `kis-mcp` 를 연결하는 블록을 넣습니다 -- 그 블록은 `kis config mcp` 가 경로까지 채워 만들어 줍니다
(앱키·시크릿은 이 파일에 안 들어가고, `.config/kis-trader/credentials.json` 의 프로필을 이름으로만 가리킵니다).

**왜 캐시가 필요한가** — 유효한 토큰과 종목마스터를 재사용해 불필요한 요청을 줄입니다:

| 캐시 | 무엇 | 왜 저장하나 |
|---|---|---|
| `tokens/` | **접속 토큰** — 앱키·앱시크릿으로 발급, 하루 단위 유효 | 조회·주문마다 새로 받지 않고 재사용 (재발급 과속으로 인한 `status 500` 예방) |
| `masters/` | **종목마스터** — 이름↔코드 대응표 | 캐시가 없거나 오래되면 다시 다운로드(기본 유효기간 24시간) |

둘 다 설치(`pip`)에 포함되지 않습니다. 토큰은 인증 API에서 발급받아 유효한 동안 재사용하고,
종목마스터는 KIS 배포 서버에서 받습니다. 마스터 인덱스를 불러올 때 캐시가 없거나 오래되면 다시
받으며, 이미 클라이언트에 로드한 인덱스는 그 클라이언트에서 재사용합니다.

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
오직 바깥 블록(`real`/`paper`) 키가 정합니다. `paper` 슬롯을 저장하지 않았다면 그 프로필의
저장값을 모의 환경으로 선택할 수 없습니다. 슬롯의 존재는 실제 브로커 지원 여부를 검사하지 않습니다.
최상위 `hts_id` 는 조건검색·관심종목 조회의 기본 `user_id`(HTS 로그인 아이디)로 모든 프로필이
공유하며, 호출에 `user_id` 를 직접 주면 그 값을 사용합니다(인증엔 안 씀). `KISConfig.set_hts_id("your_hts_id")`
로 기록하거나 손 편집합니다. 런타임은 두 축 schema-2 형식만 읽습니다. 예전 형식(평평한, `schema`
표시가 없는) `credentials.json` 은 `kis config migrate` 로 한 번 변환해야 하며, 변환 전까지는 세션을
열 때 변환을 안내하는 오류로 멈춥니다(조용히 열리거나 생략된 환경이 실전(`real`)으로 잡히지 않습니다).
환경 인자·환경변수·저장된 기본 환경이 모두 없으면 `paper` 입니다. `KIS_DEFAULT_ENVIRONMENT` 나
`default_environment` 를 `real` 로 정했다면 환경 인자를 생략해도 실전으로 열립니다.

| OS | `credentials.json` 위치 | 파일 권한 |
|---|---|---|
| Linux | `~/.config/kis-trader/` | `600` — 소유자만 (자동) |
| macOS | `~/.config/kis-trader/` | `600` — 소유자만 (자동) |
| Windows | `C:\Users\나\.config\kis-trader\` | 사용자 폴더 보호에 의존 |

이 파일에는 앱키·앱시크릿이 들어 있어, 패키지가 **사용자만 접근하게** 잠급니다(Linux·macOS는 자동, Windows는 폴더 보호에 의존 -- 아래).

- **파일 접근 권한** — Linux·macOS 는 `600` 권한(소유자만 읽기·쓰기)으로 저장합니다(`600` = 파이썬 `0o600`,
  터미널 `chmod 600` 과 같은 값). Windows 는 이 권한 비트가 없어 파일 자체엔 안 걸리는 대신,
  실제 접근 제한은 저장 폴더와 파일의 Windows 접근 권한 설정을 확인해야 합니다.
- **표시할 때 가리기** — 자격증명 객체의 `repr` 과 `kis config show` 는 시크릿을 가립니다.
  직접 키를 출력하거나 외부 로깅에 넘기지 마세요.
- **명령줄로 안 받기** — 앱키·앱시크릿은 플래그(`--app-key ...`)로 받지 않습니다. 셸 기록과 `ps` 에
  남기 때문입니다. 저장 파일이나 환경변수로만 주세요.

::: {.callout-note}
**(고급)** 파일 위치를 바꾸려면 `XDG_CONFIG_HOME` / `XDG_CACHE_HOME` / `XDG_STATE_HOME` 환경변수를, 테스트·격리는
`config_dir=` 인자를 씁니다.
:::

## 안 될 때 (자주 겪는 문제)

증상 열은 실제 메시지에 **포함된 문구**입니다(CLI 는 앞에 `요청 실패:` 같은 접두어가 붙기도 함) -- 그대로 검색해 맞는 행을 찾으세요.

| 증상(실제 메시지 / 상황) | 원인 | 해결 |
|---|---|---|
| `command not found: kis` | 설치 안 됐거나 도구 경로가 PATH 밖 | `uv tool install kis-trader`(또는 `pipx install kis-trader`)로 설치. 이미 설치했는데 안 잡히면 셸을 새로 열기(`pipx` 는 `pipx ensurepath` 후) |
| `… APP_KEY 를 찾을 수 없다 …`(예 `KIS_GENERAL_REAL_APP_KEY …`) | 자격증명 미설정·키 이름 오타 | 프로필·환경·필드명 확인, 또는 `kis config` / `KISConfig(...).save()` 로 저장 |
| `… credentials.json 가 있으나 읽을 수 없다` | JSON 문법 오류, UTF-8 인코딩 오류 또는 파일 읽기 실패 | UTF-8 저장 여부와 쉼표·따옴표, 파일 읽기 권한 확인 |
| `KIS HTTP 요청에 실패했다 (status 500)` | 호출 한도 초과 또는 다른 서버 오류 | 함께 반환된 KIS 오류 메시지 확인. 패키지 기본 속도는 실전 초당 15회·모의 초당 1회이며, 시간초과된 주문을 재전송하지 않음 |
| 잔고가 비거나 이상함 | 환경·계좌·상품 또는 조회 조건이 의도와 다를 수 있음 | 선택된 환경·계좌와 조회 조건을 먼저 확인. 실전 조회가 목적일 때만 `--environment real` 지정 |
| `이 계좌는 API 주문이 불가하다 …`(주문 시) | IRP(상품코드 `29`) — 조회만 가능 | 주문은 위탁·ISA·연금저축 계좌에서 |
| `상품계좌종류 55(DC가입자)는 … 이용이 불가하다`(세션 생성 시) | DC가입자(상품코드 `55`) | API 이용 자체 불가 |
