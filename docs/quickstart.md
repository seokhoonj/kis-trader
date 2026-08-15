# 빠른 시작

## 설치

먼저 [uv](https://docs.astral.sh/uv/)(파이썬 패키지 도구)가 필요합니다. 없으면:
`curl -LsSf https://astral.sh/uv/install.sh | sh` (Windows는 uv 문서의 PowerShell 명령).

**명령줄 `kis` 를 어디서나 쓰려면** — 리포 폴더에서 한 번:

```bash
uv tool install .            # `kis` 명령이 ~/.local/bin 에 설치됨. 코드 갱신 시 --reinstall
kis --version                # kis 0.0.0 나오면 성공
```

`~/.local/bin` 이 PATH에 없다는 경고가 나오면 `uv tool update-shell` 후 새 터미널을 여세요.

**설치 없이 리포 폴더에서만** 쓰려면 `uv run kis …` 또는 `.venv/bin/kis …` 도 됩니다.
**파이썬에서 import** 하려면 `uv pip install -e .` (아직 PyPI 미배포, 0.0.0).

다음으로 앱키·계좌를 설정합니다 → [설정과 자격증명](configuration.md)의 "처음 설정하기"를
그대로 따라 하세요(초보자용 단계별). 설정이 끝나면:

```bash
kis account balance          # 실전 주계좌 잔고(기본 프로필 main)
kis stock quote 005930       # 삼성전자 현재가
```

## 세션 만들기 (파이썬)

[설정과 자격증명](configuration.md)의 "처음 설정하기"로 `credentials.json` 을 한 번 만들어 두면,
**`KISClient()` 한 줄로 열립니다** — 저장된 자격증명을 알아서 읽습니다.

```python
from kis_trader import KISClient

kis = KISClient()                       # 실전 주계좌(저장분 자동 로드)
kis = KISClient(environment="paper")    # 모의투자(KIS_PAPER_* 사용)
```

`environment="paper"` 면 모의 프로필을 읽고, 각 API 요청의 **TR-ID**(KIS가 요청 종류를 구분하는
거래 코드. 실전용과 모의용이 따로 있습니다)도 자동으로 모의용으로 바뀝니다.

ISA·IRP·연금 등 다른 계좌 프로필이나 격리가 필요하면 프로필을 명시합니다:

```python
from kis_trader import KISClient, KISConfig

kis = KISClient.from_config(KISConfig(profile="isa"))     # 실전 ISA
kis = KISClient.from_config(KISConfig(profile="pension")) # 퇴직연금(조회 전용)
```

## 값을 직접 주기 (파일 안 읽음)

설정 파일을 쓰지 않고 앱키를 코드/환경변수로 직접 넘길 수도 있습니다(이 경우 파일을 읽지 않습니다):

```python
kis = KISClient(
    app_key="YOUR_APP_KEY",
    app_secret="YOUR_APP_SECRET",
    account="12345678-01",         # 계좌번호 8자리-2자리
    environment="real",
)
```

해석 순서(환경변수 → `credentials.json` → `config.toml`)·프로필·파일 위치(Linux·macOS·Windows
공통)는 [설정과 자격증명](configuration.md)을 참고하세요.

## 첫 조회

```python
s = kis.domestic.stock("005930")  # 삼성전자
q = s.quote()

q.current_price                   # 현재가
q.change                          # 전일대비
q.change_percent                  # 등락률(%)
q.volume                          # 거래량
```

## 안전 스위치 (주문할 때)

```python
kis = KISClient(
    …,
    orderable=True,      # 주문 가능 여부 (계좌 유형에서 자동 판단)
    allow_credit=False,  # 신용주문은 명시적으로 켜야 함
    throttle=True,       # 초당 호출 제한 자동 준수 (실전 15 / 모의 1)
)
```

조회만 할 거면 신경 안 써도 됩니다. 주문의 안전장치는 [주문](orders.md)에서 자세히.

## 결과 객체 다루기

모든 결과는 **읽기전용**. 필드로 꺼내 쓰고, 필드 설명이 궁금하면 `help`.

```python
q = kis.domestic.stock("005930").quote()

q.current_price                           # 매핑된 값 (Decimal)
q._raw                                    # KIS 원본 응답 전체
help(type(q))                             # 이 결과의 필드 설명(한국어) + KIS URL·TR-ID
```
