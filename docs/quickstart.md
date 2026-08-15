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

KIS 개발자센터에서 발급한 **앱키·앱시크릿**과 **계좌번호**가 필요합니다.

```python
from kis_trader import KISClient

kis = KISClient(
    app_key="YOUR_APP_KEY",
    app_secret="YOUR_APP_SECRET",
    account="12345678-01",         # 계좌번호 8자리-2자리
    environment="real",            # "real" 실전 / "paper" 모의투자
)
```

모의투자로 연습하려면 `environment="paper"` — 각 API 요청의 **TR-ID**(KIS가 요청 종류를
구분하는 거래 코드. 실전용과 모의용이 따로 있습니다)가 자동으로 모의용으로 바뀝니다.

## 자격증명 숨기기

코드에 키를 직접 쓰지 말고 프로필로 여세요. `KISConfig` 가 환경변수나 설정 파일
(`~/.config/kis-trader/credentials.json`)에서 프로필별로 읽습니다.

```python
from kis_trader import KISClient, KISConfig

kis = KISClient.from_config(KISConfig(profile="main"))    # 실전 주계좌(기본)
kis = KISClient.from_config(KISConfig(profile="paper"))   # 모의투자
```

프로필·파일 위치(Linux·macOS·Windows 공통)·해석 순서는 [설정과 자격증명](configuration.md)을
참고하세요. 환경변수로 직접 주고 싶으면 `KIS_APP_KEY` / `KIS_APP_SECRET` 등을 설정한 뒤 같은
`from_config` 를 쓰면 됩니다(환경변수가 파일보다 우선).

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
