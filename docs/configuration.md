# 자격증명과 프로필

세션을 열려면 KIS 개발자센터에서 발급한 **앱키·앱시크릿**과, 계좌 조회·주문에는 **계좌번호**가
필요합니다. 이 값들을 코드에 직접 쓰지 않고 **프로필** 단위로 관리합니다 -- 한 번 저장해 두면
`KISClient(profile=...)` 한 줄로 세션이 열립니다.

**처음이라면** [처음 설정하기](#처음-설정하기-한-번만)만 순서대로 따라 하면 됩니다. 나머지 절은
원리·고급 설정 참고용입니다.

## 프로필이란

**프로필**은 한 계좌 묶음(앱키·시크릿·계좌번호)과 그 **접속 환경**을 이름 하나로 가리킵니다.
`KISClient(profile="main")` 처럼 이름만 고르면 그 계좌의 자격증명과 환경(실전/모의)이 함께 정해집니다.

| `profile=` | 계좌 | 환경 | 조회 | 주문 |
|---|---|:--:|:--:|:--:|
| `"main"` (기본) | 주계좌(위탁/종합) | 실전 | O | O |
| `"paper"` | 모의투자 | 모의 | O | O |
| `"isa"` | ISA | 실전 | O | O |
| `"pension"` | 연금저축 | 실전 | O | O |
| `"irp"` | IRP | 실전 | O | X |

::: {.callout-note}
**주문 가능 여부는 프로필 이름이 아니라 계좌의 상품코드(`ACNT_PRDT_CD`)로 결정되며, 세션이 자동으로
판정합니다.** 위 표는 각 프로필에 그 종류의 계좌를 넣은 일반적인 경우입니다.

- **연금저축(상품코드 `22`)**: 주문 가능(IRP와 혼동 주의).
- **IRP(상품코드 `29`)**: 조회만 가능 -- KIS가 주문을 거부하므로 세션이 `orderable` 을 자동으로 끕니다.
- **DC가입자(상품코드 `55`)**: Open API 이용 자체가 불가 -- 세션 생성이 거부됩니다.

`orderable=False` 로 더 제한할 수는 있어도, 주문 불가 계좌를 강제로 켤 수는 없습니다. 조회 중에도
잔고·주문내역은 계좌번호가 필요하고, 시세만 볼 때는 계좌 없이도 됩니다.
:::

**환경(실전/모의)은 프로필이 정합니다** -- `paper` 만 모의, 나머지는 전부 실전. 각 프로필은 자기
접두어에 세 값(`APP_KEY` / `APP_SECRET` / `ACCOUNT`)을 붙여 읽습니다(예: `paper` 는 `KIS_PAPER_APP_KEY`
· `KIS_PAPER_APP_SECRET` · `KIS_PAPER_ACCOUNT`). `ACCOUNT` 는 `종합계좌번호-상품코드` 형식입니다.

| 프로필 | 접두어 | 예: 앱키 환경변수 |
|---|---|---|
| `main` (기본) | `KIS_` | `KIS_APP_KEY` |
| `paper` | `KIS_PAPER_` | `KIS_PAPER_APP_KEY` |
| `isa` | `KIS_ISA_` | `KIS_ISA_APP_KEY` |
| `pension` | `KIS_PENSION_` | `KIS_PENSION_APP_KEY` |
| `irp` | `KIS_IRP_` | `KIS_IRP_APP_KEY` |

## 자격증명 넣는 세 가지 방법

같은 자격증명을 세 가지로 줄 수 있습니다. 세션은 **환경변수 -> `credentials.json` -> `config.toml`**
순으로 찾습니다(뒤의 [해석 순서](#자격증명-해석-순서) 참고).

**방법 1 (권장) -- `KISConfig(...).save()`.** 손으로 파일을 짜지 말고 파이썬으로 저장하면 정해진
위치에 안전하게(`0600`·원자적) 기록되고, 다른 프로필은 보존하며 병합됩니다.

```python
from kis_trader import KISConfig

KISConfig(profile="main", app_key="...", app_secret="...", account="12345678-01").save()
KISConfig(profile="paper", app_key="...", app_secret="...", account="12345678-01").save()  # 모의도 쓰면
```

**방법 2 -- 환경변수(CI·컨테이너·노트북).** `export` 해두면 `KISClient()` 가 자동으로 읽습니다(해석
순서 1순위라 파일보다 우선). 손으로 `os.environ` 에서 꺼내 넘길 필요가 없습니다.

```bash
export KIS_APP_KEY=YOUR_APP_KEY
export KIS_APP_SECRET=YOUR_APP_SECRET
export KIS_ACCOUNT=12345678-01    # 종합계좌번호-상품코드 (시세만 볼 거면 생략 가능)
```

**방법 3 -- 값 직접 전달.** 파일·환경변수를 안 쓰고 코드에서 바로 넘깁니다(이 경우 파일을 읽지 않음).

```python
kis = KISClient(app_key="...", app_secret="...", account="12345678-01")
```

세션·CLI 여는 법은 [빠른 시작](quickstart.md) 참고. CLI는 `--profile` 하나가 계좌와 환경을 모두
정합니다(`kis --profile paper stock quote 005930`).

## 처음 설정하기 (한 번만)

컴퓨터를 잘 몰라도 이 순서대로 하면 됩니다.

### 1단계 -- KIS에서 앱키 발급받기

1. 한국투자증권 **실계좌**가 있어야 합니다(없으면 먼저 계좌 개설). 그리고 HTS/MTS 등에서 **API
   서비스 신청(사용 동의)**을 해야 앱키를 받을 수 있습니다.
2. KIS 개발자센터 <https://apiportal.koreainvestment.com/intro> 에 로그인 ->
   **App Key 발급** 메뉴에서 **APP KEY**와 **APP SECRET**을 받습니다.
   - **실전투자용**과 **모의투자용**은 앱키가 **따로 있습니다**. **처음이라면 모의투자용부터 받아
     연습을 먼저 하는 것을 권장합니다** -- 개발자센터에서 **모의투자 신청** 후 모의용 앱키를 받으세요.
   - 실전은 익숙해진 뒤에 실전용 앱키를 받아 쓰면 됩니다.
3. 이 두 값(APP KEY, APP SECRET)은 **비밀번호처럼 취급**하세요. 남에게 보이거나 깃허브에 올리면 안 됩니다.

### 2단계 -- 내 계좌번호 확인

계좌번호는 **종합계좌번호 8자리 + 상품코드 2자리**로 나뉩니다. 예: 계좌가 `12345678-01` 이면
앞 8자리(`12345678`)가 종합계좌번호(CANO), 뒤 2자리(`01`)가 상품코드(ACNT_PRDT_CD)입니다.
HTS/MTS 계좌 화면에서 확인할 수 있습니다.

**상품코드는 무엇을 거래하느냐에 따라 다릅니다.** 하나의 종합계좌번호(CANO) 아래 여러 상품이 있어,
같은 계좌라도 주식과 선물옵션은 상품코드가 다릅니다(예: 해외선물옵션은 `-01`이 아니라 `-08`).

| 상품코드 | 상품 | 주문 |
|:--:|---|:--:|
| `01` | 위탁 · ISA · RIA · 국내/해외 **주식** | O |
| `03` | 국내선물옵션 | O |
| `08` | 해외선물옵션 | O |
| `22` | 연금저축 | O |
| `29` | IRP(개인형퇴직연금) | 조회만 |
| `55` | DC가입자 | 이용 불가 |

(해외 *주식*은 위탁 `01`과 같은 종합계좌를 씁니다. 상품코드가 주문 가능 여부도 정합니다 --
IRP `29`는 조회 전용, DC가입자 `55`는 세션 생성 자체가 거부됩니다. 출처: 한국투자 Open API 공식 FAQ.)

### 3단계 -- 자격증명 저장

가장 쉬운 방법은 위 [방법 1](#자격증명-넣는-세-가지-방법)의 `KISConfig(...).save()` 입니다. 손으로
파일을 만들고 싶다면 아래처럼 합니다. 저장 위치는 **세 운영체제 공통**으로 홈 폴더 아래
`.config/kis-trader/` 입니다.

**Linux / macOS** (터미널):

```bash
mkdir -p ~/.config/kis-trader                       # 폴더 생성
nano ~/.config/kis-trader/credentials.json          # 편집기 열기(nano 없으면 vi/gedit 등)
# 아래 JSON을 붙여넣고 값 채우기 -> 저장(nano: Ctrl+O, Enter, Ctrl+X)
chmod 600 ~/.config/kis-trader/credentials.json     # 나만 읽게 권한 잠그기(중요)
```

**Windows** (PowerShell):

```powershell
mkdir $HOME\.config\kis-trader
notepad $HOME\.config\kis-trader\credentials.json    # 메모장 열림 -> 붙여넣고 저장
```

**붙여넣을 내용** -- 따옴표 안의 값을 1·2단계에서 받은 실제 값으로 바꾸세요:

```json
{
  "KIS_APP_KEY": "여기에_실전_APP_KEY",
  "KIS_APP_SECRET": "여기에_실전_APP_SECRET",
  "KIS_ACCOUNT": "12345678-01"
}
```

모의투자도 쓰려면 같은 파일에 `KIS_PAPER_APP_KEY` / `KIS_PAPER_APP_SECRET` / `KIS_PAPER_ACCOUNT` 를
**추가**하면 됩니다(아래 "파일 위치" 절의 전체 예시 참고).

::: {.callout-warning}
JSON은 **쉼표·따옴표**가 하나라도 틀리면 안 읽힙니다. 마지막 항목 뒤에는 쉼표를 붙이지 마세요.
값은 큰따옴표 `"..."` 로 감쌉니다.
:::

### 4단계 -- 첫 명령 실행

설치와 실행 방법은 [빠른 시작](quickstart.md)에 있습니다. 설치했다면:

```bash
kis account balance          # 내 실전 주계좌 잔고
kis stock quote 005930       # 삼성전자 현재가
```

`--profile` 을 안 붙이면 **실전 주계좌(main)**를 씁니다. 조회는 안전하고, **주문은 `--execute` 를
붙이기 전까지 절대 전송되지 않습니다**(dry-run).

### 5단계 (선택) -- 기본을 모의투자로 바꾸기

연습 위주라면 기본 프로필을 모의로 바꿔 매번 `--profile paper` 를 안 쳐도 되게 할 수 있습니다.

```bash
# Linux/macOS: 셸 설정에 한 줄 추가(한 번만)
echo 'export KIS_PROFILE=paper' >> ~/.bashrc && source ~/.bashrc
# 이제 kis account balance 는 모의투자 계좌를 봅니다. 실전은 그때만 --profile main.
```

Windows(PowerShell)는 `setx KIS_PROFILE paper` 후 새 창을 여세요.

## 자격증명 해석 순서

세션(`KISClient`)이 프로필의 자격증명을 다음 순서로 찾습니다. 앞에서 찾으면 뒤는 보지 않습니다.

1. **환경변수** -- 예: `KIS_APP_KEY`.
2. **`credentials.json`** -- 설정 디렉터리의 시크릿 파일(`KISConfig(...).save()` 가 기록).
3. **`config.toml`** -- 설정 디렉터리의 낮은 우선순위 폴백. 주로 비밀이 아닌 설정을 두지만
   자격증명도 여기서 폴백으로 읽히므로, 값을 넣는다면 `credentials.json` 과 같이 `0600` 으로 보호하세요.

환경변수가 파일보다 우선하므로, CI·컨테이너에서는 환경변수로 덮어쓰고 개인 머신에서는 파일을
쓰는 식으로 섞을 수 있습니다. 일부만 직접 넘기면 **빠진 값만** 저장분에서 채웁니다. 값은 화면·로그·
예외 어디에도 출력되지 않습니다(누락 시 예외에는 변수 이름만 담깁니다).

## 파일 위치 (Linux · macOS · Windows 공통)

설정과 캐시는 세 운영체제에서 **같은 레이아웃**을 씁니다 -- 홈 디렉터리 아래의 `.config` /
`.cache` 하위 경로입니다(XDG 규약). 편집 대상 설정과 재생성 가능한 캐시를 분리합니다.

| 종류 | 경로(기본) | 오버라이드 |
|---|---|---|
| 편집 설정 | `~/.config/kis-trader/` | `$XDG_CONFIG_HOME/kis-trader/` |
| 재생성 캐시 | `~/.cache/kis-trader/` | `$XDG_CACHE_HOME/kis-trader/` |

- **Linux**: `~/.config/kis-trader/`, `~/.cache/kis-trader/`.
- **macOS**: 동일하게 `~/.config/kis-trader/`, `~/.cache/kis-trader/` (macOS 표준 `~/Library/...`
  가 아니라 세 OS 공통 레이아웃).
- **Windows**: `~` 는 `C:\Users\<사용자>` 이므로 `C:\Users\<사용자>\.config\kis-trader\`,
  `C:\Users\<사용자>\.cache\kis-trader\`. `%XDG_CONFIG_HOME%` / `%XDG_CACHE_HOME%` 를 설정하면
  그 경로를 씁니다.

테스트나 격리가 필요하면 `config_dir=` (`KISConfig(config_dir=...)` 저장, `KISClient(config_dir=...)`
읽기)로 설정 파일과 토큰 캐시를 한 경로로 돌릴 수 있습니다(그 경우 캐시는 그 경로 아래 `tokens/` 에
함께 둡니다 -- '전부 한 곳'이 격리 계약입니다).

### `~/.config/kis-trader/` — 편집 설정

- **`credentials.json`** -- 시크릿(앱키·시크릿·계좌번호). 프로필 접두어별 키를 한 파일에 담습니다.
  파일 권한은 `0600`(소유자만 읽기)로 두세요.

  ```json
  {
    "KIS_APP_KEY": "...",        "KIS_APP_SECRET": "...",        "KIS_ACCOUNT": "12345678-01",
    "KIS_PAPER_APP_KEY": "...",  "KIS_PAPER_APP_SECRET": "...",  "KIS_PAPER_ACCOUNT": "12345678-01",
    "KIS_ISA_APP_KEY": "...",    "KIS_ISA_APP_SECRET": "...",    "KIS_ISA_ACCOUNT": "..."
  }
  ```

- **`config.toml`** -- 낮은 우선순위 폴백(주로 비밀 아닌 설정, 단 자격증명 폴백으로도 읽힘 ->
  값을 넣으면 `0600`). 파일이 있는데 읽거나 파싱할 수 없으면, 있는 자격증명을 '없음'으로 오진하지
  않도록 오류로 닫습니다.

### `~/.cache/kis-trader/` — 재생성 캐시

지워도 다음 실행에서 다시 만들어지는 런타임 캐시입니다. 손으로 편집하지 마세요.

- **`tokens/real-<해시>.json`, `tokens/paper-<해시>.json`** -- OAuth 접근 토큰 캐시. 파일명은
  `<환경>-<앱키의 SHA-256 앞 16자리>` 라서 프로필(앱키)마다·환경마다 파일이 분리됩니다. KIS 토큰은
  앱키 단위로 발급 한도가 있어, 프로세스 재시작 후에도 유효기간이 남은 토큰을 재사용합니다. 파일은
  `0600`, 디렉터리는 `0700` 으로 만듭니다.
- **`masters/<시장>mst.cod`** -- 종목 마스터(심볼->거래소·이름 해석용). 예: `nasmst.cod`,
  `hksmst.cod`. KIS 배포 서버에서 받아 캐시하며, 심볼 조회를 오프라인에서도 빠르게 합니다.

## 보안 -- 자격증명을 플래그로 받지 않는 이유

CLI 는 앱키·시크릿을 명령줄 플래그로 받지 않습니다. 셸 히스토리와 `ps` 출력이 노출 경로이기
때문입니다. 환경변수나 `0600` 설정 파일로만 주고, 값은 어디에도 echo 하지 않습니다. 파이썬에서도
`KISConfig` / 해석 결과 객체의 `repr` 은 앱키·시크릿을 감춥니다(로그·트레이스백에 새지 않게).

## 안 될 때 (자주 겪는 문제)

| 증상 | 원인 | 해결 |
|---|---|---|
| `command not found: kis` | 설치 안 됨/PATH 밖 | [빠른 시작](quickstart.md)의 설치. 임시로 `.venv/bin/kis …` |
| `자격증명이 없습니다 …` | 파일 경로·이름 오타, 키 이름 오타 | 경로가 정확히 `~/.config/kis-trader/credentials.json` 인지, 키 이름이 `KIS_APP_KEY` 등과 **정확히** 같은지 확인 |
| `… config 파일을 읽을 수 없다` | JSON 문법 오류(쉼표/따옴표) | 마지막 쉼표 제거, 모든 값 `"..."` 확인 |
| `status 500` | **호출 과속**(KIS의 rate 제한 신호) | 명령을 한 번에 몰아 붙여넣지 말고 한 줄씩. 모의는 초당 1회 |
| 잔고가 가짜다/`net_asset` 이 이상 | 모의투자 계좌를 보고 있음 | `--profile main` 또는 `KIS_PROFILE=main` |
| `주문 불가` (IRP 등) | 조회 전용 계좌(상품코드 `29`) | IRP는 조회만 가능. 주문은 위탁/종합·ISA·연금저축 계좌에서 |
