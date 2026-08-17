# 빠른 시작

## 설치

**파이썬에서 `import`** 하려면:

```bash
uv pip install kis-trader     # 또는: pip install kis-trader
```

**명령줄 `kis` 도구**로 쓰려면:

```bash
uv tool install kis-trader    # kis 명령 설치 (또는: pipx install kis-trader)
kis --version
```

## 세션 만들기

KIS 개발자센터에서 발급한 **앱키·앱시크릿**과 **계좌번호**로 세션을 엽니다:

```python
from kis_trader import KISClient

kis = KISClient(app_key="YOUR_APP_KEY", app_secret="YOUR_APP_SECRET", account="12345678-01")
```

매번 키를 넣기 번거로우면 **한 번 저장**해 두고 `KISClient(profile="main")` 한 줄로 열 수 있습니다.
자격증명 저장·환경변수·프로필(실전/모의)·CLI 사용법은 **[자격증명과 프로필](configuration.md)**을 보세요.

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

## 첫 주문

매수·매도는 종목 핸들에서 바로 냅니다. 낸 주문은 `client_order_id` 로 지목해 확인·정정·취소합니다:

```python
r = kis.domestic.stock("005930").buy(quantity=10, limit_price=70000)  # 10주 지정가 매수

r.client_order_id   # 이 주문의 고유 키 (정정·취소할 때 지목)
r.status            # 주문 상태

kis.orders.reconcile(r.client_order_id)   # 실제 접수됐는지 증권사에 확인
kis.orders.cancel(r.client_order_id)      # 취소
```

::: {.callout-warning}
조회와 달리 주문은 **실제로 돈이 오가는 실거래**입니다. `buy()`/`sell()` 은 즉시 전송되고 되돌릴 수
없으니, 처음엔 **모의투자(`paper`) 프로필**로 연습하세요. 중복주문 방지·정정/취소·예약주문 같은
안전장치는 **[주문](orders.md)** 에서 자세히 다룹니다(신용거래는 기본으로 막혀 있습니다).
:::

## 다음

- 자격증명·프로필 상세(저장·환경변수·실전/모의) → [자격증명과 프로필](configuration.md)
- 시세·차트·호가 → [시세](quotes.md)
- 주문과 안전장치 → [주문](orders.md)
