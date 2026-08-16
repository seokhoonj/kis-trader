# 빠른 시작

## 설치

**파이썬에서 `import`** 하려면:

```bash
uv pip install kis-trader     # 또는: pip install kis-trader
```

**명령줄 `kis` 도구**로 쓰려면:

```bash
uv tool install kis-trader    # kis 명령 설치
kis --version
```

## 세션 만들기

KIS 개발자센터에서 발급한 **앱키·앱시크릿**과 **계좌번호**로 세션을 엽니다:

```python
from kis_trader import KISClient

kis = KISClient(app_key="YOUR_APP_KEY", app_secret="YOUR_APP_SECRET", account="12345678-01")
```

매번 키를 넣기 번거로우면 **한 번 저장**해 두고 `KISClient(profile="main")` 한 줄로 열 수 있습니다.
자격증명 저장·환경변수·프로필(실전/모의)·CLI 사용법은 **[3. 자격증명과 프로필](configuration.md)**을 보세요.

## 첫 조회

```python
s = kis.domestic.stock("005930")  # 삼성전자
q = s.quote()

q.current_price   # 현재가
q.change          # 전일대비
q.change_percent  # 등락률(%)
q.volume          # 거래량
```

모든 결과는 **읽기전용**입니다. 필드 설명이 궁금하면 `help(type(q))`(한국어 설명 + KIS URL·TR-ID),
원본 응답 전체는 `q._raw`.

## 다음

- 자격증명·프로필 상세(저장·환경변수·실전/모의) → [3. 자격증명과 프로필](configuration.md)
- 시세·차트·호가 → [시세](quotes.md)
- 주문과 안전장치 → [주문](orders.md)
