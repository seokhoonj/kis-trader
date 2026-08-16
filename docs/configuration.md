# 자격증명과 프로필

세션을 열려면 KIS 개발자센터에서 발급한 **앱키·앱시크릿**과 **계좌번호**가 필요합니다. 발급 과정은
[2. 앱키 발급받기](appkey.md)를 보고, 여기서는 그 값을 **프로필**로 저장·사용하는 법을 다룹니다.

## 프로필 = 계좌 하나

프로필은 한 계좌(앱키·앱시크릿·계좌번호 + 실전/모의)를 담은, **사용자가 이름 붙이는 묶음**입니다.
`KISClient(profile="pension")` 처럼 이름으로 엽니다.

| 항목 | 내용 |
|---|---|
| 이름 | 자유(소문자·숫자·밑줄). **계좌 하나 = 프로필 하나** -- `main`, `isa`, `pension`, `irp` … (같은 유형이 여럿이면 이름을 더 붙임) |
| 기본 | `main` (`KISClient()` 가 여는 이름, `KIS_PROFILE` 로 변경) |
| 환경 | 프로필에 저장(기본 `real`; 모의는 `environment="paper"`). **이름이 아니라 저장값이 정함** |
| 주문 가능 | 계좌의 **상품코드**가 결정(아래 표), 세션이 자동 판정 |

앱키·앱시크릿은 종합계좌번호(CANO) 단위라 계좌마다 다르고, 같은 유형 계좌도 여럿(연금저축 2개 등)일
수 있어 계좌 수만큼 프로필을 둡니다.

## 자격증명 넣기 (세 가지)

세션은 **환경변수 → `credentials.json`** 순으로 찾습니다. 아래 셋 중 **하나만** 쓰면 됩니다.

| 방법 | 언제 | 요약 |
|---|---|---|
| ① `KISConfig(...).save()` | **권장** | 정해진 위치에 안전 저장(나만 읽게 잠금·병합) |
| ② 환경변수 | CI·컨테이너 | `export KIS_APP_KEY=…`(명명 프로필은 `KIS_<이름대문자>_*`) |
| ③ 직접 전달 | 일회성 | `KISClient(app_key=…, …)`(파일 안 읽음) |

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

**③ 직접 전달** (파이썬 코드로 실행) -- 저장 없이 그때만 씁니다. (파일을 읽지 않음)

```python
from kis_trader import KISClient
kis = KISClient(app_key="...", app_secret="...", account="12345678-01")
```

::: {.callout-note}
CLI 에서도 `--profile` 로 계좌·환경을 고릅니다: `kis --profile pension account balance`
:::

## 계좌번호와 상품코드

계좌번호 = **종합계좌번호(8자리)-상품코드(2자리)** (예: `12345678-01`). 같은 종합계좌라도 거래 상품마다
상품코드가 다릅니다(해외 *주식*은 위탁 `01`, 해외 *선물옵션*은 `-08`).

| 상품코드 | 상품 | 주문 |
|:--:|---|:--:|
| `01` | 위탁 · ISA · RIA · 국내/해외 **주식** | O |
| `03` | 국내선물옵션 | O |
| `08` | 해외선물옵션 | O |
| `22` | 연금저축 | O |
| `29` | IRP(개인형퇴직연금) | 조회만 |
| `55` | DC가입자 | 이용 불가 |

IRP(`29`)는 세션이 주문을 자동으로 막고, DC가입자(`55`)는 세션 생성 자체가 거부됩니다.
(출처: 한국투자 Open API 공식 FAQ.)

## credentials.json

`~/.config/kis-trader/credentials.json` (Windows: `C:\Users\<사용자>\.config\kis-trader\`). 프로필별
객체를 한 파일에 담습니다. 이 파일은 **나(소유자)만 읽을 수 있게 잠급니다**(파일 권한 `0600`).
`KISConfig(...).save()` 가 이 형식으로 기록합니다(손 편집도 가능):

```json
{
  "main":      {"app_key": "...", "app_secret": "...", "account": "12345678-01", "environment": "real"},
  "pension":   {"app_key": "...", "app_secret": "...", "account": "87654321-22", "environment": "real"},
  "paper":     {"app_key": "...", "app_secret": "...", "account": "...",         "environment": "paper"}
}
```

## 파일 위치 · 보안

세 OS 공통 레이아웃(XDG; macOS도 `~/.config`). 편집 설정과 재생성 캐시를 분리합니다.

| 종류 | 경로(기본) | 오버라이드 |
|---|---|---|
| 편집 설정(`credentials.json`) | `~/.config/kis-trader/` | `$XDG_CONFIG_HOME/kis-trader/` |
| 재생성 캐시(토큰·종목마스터) | `~/.cache/kis-trader/` | `$XDG_CACHE_HOME/kis-trader/` |

- 캐시는 지워도 다음 실행에 다시 생깁니다(손대지 마세요). 테스트·격리는 `config_dir=` 로 한 경로에 몰 수 있습니다.
- 앱키·앱시크릿은 **명령줄 플래그로 받지 않습니다**(셸 히스토리·`ps` 노출 방지) -- 환경변수나 나만 읽게 잠근 설정 파일로만.
- `KISConfig`·해석결과의 `repr` 은 앱키·앱시크릿을 감춰, 값이 화면·로그·예외에 새지 않습니다.

## 안 될 때 (자주 겪는 문제)

| 증상 | 원인 | 해결 |
|---|---|---|
| `command not found: kis` | 설치 안 됨/PATH 밖 | [1. 빠른 시작](quickstart.md)의 설치. 임시로 `.venv/bin/kis …` |
| `자격증명이 없습니다 …` | 경로·키 이름 오타 | `~/.config/kis-trader/credentials.json` 경로·프로필 이름·필드명 확인 |
| `… 읽을 수 없다` | JSON 문법 오류(쉼표/따옴표) | 마지막 쉼표 제거, 모든 값 `"..."` 확인 |
| `status 500` | **호출 과속**(rate 제한) | 명령을 몰아 붙여넣지 말고 한 줄씩. 모의는 초당 1회 |
| 잔고가 가짜/이상 | 모의 계좌를 보고 있음 | 실전 프로필(`--profile main` 등)로 열기 |
| `주문 불가`(IRP 등) | 조회 전용 계좌(상품코드 `29`) | IRP는 조회만. 주문은 위탁/종합·ISA·연금저축에서 |
