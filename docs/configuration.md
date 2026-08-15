# 설정과 자격증명

세션을 열려면 KIS 개발자센터에서 발급한 **앱키·앱시크릿**과, 계좌 조회·주문에는 **계좌번호**가
필요합니다. 이 값들을 코드에 직접 쓰지 않고, 프로필 단위로 환경변수나 설정 파일에서 읽는 방법을
설명합니다.

## 자격증명 해석 순서

`KISConfig` 는 값을 다음 순서로 찾습니다. 앞에서 찾으면 뒤는 보지 않습니다.

1. **`resolver` 콜백** — 자기 시크릿 저장소를 쓰는 앱이 주입(선택).
2. **환경변수** — 예: `KIS_APP_KEY`.
3. **`credentials.json`** — 설정 디렉터리의 시크릿 파일.
4. **`config.toml`** — 설정 디렉터리의 낮은 우선순위 폴백. 주로 비밀이 아닌 설정을 두지만
   자격증명도 여기서 폴백으로 읽히므로, 값을 넣는다면 `credentials.json` 과 같이 `0600` 으로 보호한다.

환경변수가 파일보다 우선하므로, CI·컨테이너에서는 환경변수로 덮어쓰고 개인 머신에서는 파일을
쓰는 식으로 섞을 수 있습니다. 값은 화면·로그·예외 어디에도 출력되지 않습니다(누락 시 예외에는
변수 이름만 담깁니다).

## 프로필

**프로필**은 한 계좌 묶음(앱키·시크릿·계좌번호)과 그 접속 환경을 이름 하나로 가리킵니다. 각
프로필은 자기 접두어로 변수를 읽습니다.

| 프로필 | 환경변수 접두어 | 환경 |
|---|---|---|
| `main` (기본) | `KIS_` | 실전 |
| `paper` | `KIS_PAPER_` | 모의투자 |
| `isa` | `KIS_ISA_` | 실전 |
| `irp` | `KIS_IRP_` | 실전 |
| `pension` | `KIS_PENSION_` | 실전 |

각 프로필은 접두어에 `APP_KEY` / `APP_SECRET` / `CANO` / `ACNT_PRDT_CD` 를 붙인 네 값을 읽습니다.
예를 들어 `paper` 프로필은 `KIS_PAPER_APP_KEY` · `KIS_PAPER_APP_SECRET` · `KIS_PAPER_CANO` ·
`KIS_PAPER_ACNT_PRDT_CD` 를 읽고, 계좌번호는 `CANO-ACNT_PRDT_CD` 로 조립합니다. **환경(실전/모의)은
프로필이 정합니다** — 모의계좌만 모의, 나머지는 실전.

### 파이썬에서

```python
from kis_trader import KISClient, KISConfig

kis = KISClient.from_config(KISConfig(profile="paper"))   # 모의투자
kis = KISClient.from_config(KISConfig(profile="main"))    # 실전 주계좌
kis = KISClient.from_config(KISConfig(profile="isa"))     # 실전 ISA
```

앱키/시크릿/환경은 프로필이 정하므로 `from_config` 에 다시 줄 수 없습니다. 같은 앱키의 다른
하위계좌를 쓰려면 `account=` 만 덮어씁니다.

```python
kis = KISClient.from_config(KISConfig(profile="main"), account="87654321-02")
```

명시 인자로 직접 여는 저수준 경로도 그대로 있습니다(프로필/파일을 거치지 않음).

```python
kis = KISClient(app_key="...", app_secret="...", account="12345678-01", environment="real")
```

### CLI 에서

`--profile` 하나가 계좌와 환경을 모두 정합니다(별도 환경 플래그 없음).

```bash
kis --profile paper stock quote 005930     # 모의
kis --profile main  account balance         # 실전 주계좌
kis --profile isa   account positions       # 실전 ISA
```

기본 프로필은 안전을 위해 `paper` 입니다(`KIS_PROFILE` 환경변수로 기본값 변경). 실전은 항상
`--profile main` 처럼 명시해야 합니다.

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

테스트나 격리가 필요하면 `KISConfig(config_dir_override=...)` 로 설정 파일과 토큰 캐시를 한
경로로 돌릴 수 있습니다(그 경우 캐시는 그 경로 아래 `tokens/` 에 함께 둡니다 — '전부 한 곳'이
격리 계약입니다).

### `~/.config/kis-trader/` — 편집 설정

- **`credentials.json`** — 시크릿(앱키·시크릿·계좌번호). 프로필 접두어별 키를 한 파일에 담습니다.
  파일 권한은 `0600`(소유자만 읽기)로 두세요.

  ```json
  {
    "KIS_APP_KEY": "...",        "KIS_APP_SECRET": "...",
    "KIS_CANO": "12345678",      "KIS_ACNT_PRDT_CD": "01",
    "KIS_PAPER_APP_KEY": "...",  "KIS_PAPER_APP_SECRET": "...",
    "KIS_PAPER_CANO": "...",     "KIS_PAPER_ACNT_PRDT_CD": "01",
    "KIS_ISA_APP_KEY": "...",    "KIS_ISA_APP_SECRET": "...",
    "KIS_ISA_CANO": "...",       "KIS_ISA_ACNT_PRDT_CD": "..."
  }
  ```

- **`config.toml`** — 낮은 우선순위 폴백(주로 비밀 아닌 설정, 단 자격증명 폴백으로도 읽힘 →
  값을 넣으면 `0600`). 파일이 있는데 읽거나 파싱할 수 없으면, 있는
  자격증명을 '없음'으로 오진하지 않도록 오류로 닫습니다.

### `~/.cache/kis-trader/` — 재생성 캐시

지워도 다음 실행에서 다시 만들어지는 런타임 캐시입니다. 손으로 편집하지 마세요.

- **`tokens/real-<해시>.json`, `tokens/paper-<해시>.json`** — OAuth 접근 토큰 캐시. 파일명은
  `<환경>-<앱키의 SHA-256 앞 16자리>` 라서 프로필(앱키)마다·환경마다 파일이 분리됩니다. KIS 토큰은
  앱키 단위로 발급 한도가 있어, 프로세스 재시작 후에도 유효기간이 남은 토큰을 재사용합니다. 파일은
  `0600`, 디렉터리는 `0700` 으로 만듭니다. (옛 빌드는 모의 환경을 `demo-` 로 적었습니다 -- 현재는
  `paper-` 이며, 남아 있는 `demo-*.json` 은 지워도 됩니다.)
- **`masters/<시장>mst.cod`** — 종목 마스터(심볼->거래소·이름 해석용). 예: `nasmst.cod`,
  `hksmst.cod`. KIS 배포 서버에서 받아 캐시하며, 심볼 조회를 오프라인에서도 빠르게 합니다.

## 자격증명을 플래그로 받지 않는 이유

CLI 는 앱키·시크릿을 명령줄 플래그로 받지 않습니다. 셸 히스토리와 `ps` 출력이 노출 경로이기
때문입니다. 환경변수나 `0600` 설정 파일로만 주고, 값은 어디에도 echo 하지 않습니다.
