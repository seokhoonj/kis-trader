# kis-trader

한국투자증권(KIS) Open API 파이썬 클라이언트. Python ≥ 3.11.

```python
from kis_trader import KISClient

kis = KISClient(app_key="…", app_secret="…", account="12345678-01")

kis.domestic.stock("005930").quote()           # 삼성전자 현재가
kis.overseas.stock("AAPL").quote()             # AAPL (거래소 자동)
kis.domestic.ranking.by_change(top="gainers")  # 등락률 순위
```

공개 식별자는 영어, 설명 docstring은 한국어(+ KIS URL·TR-id).

## 설치

```bash
uv pip install -e .      # greenfield 0.0.0 (아직 PyPI 미배포)
```

## 구조

세션 하나(`KISClient`)에서 자산군별로 갈라진다.

```python
kis.domestic     # 국내 주식·지수·채권·ELW·파생·계좌·순위·시장·캘린더
kis.overseas     # 해외 주식·지수·파생·계좌·순위
kis.pension      # 퇴직연금
kis.orders       # 주문 라이프사이클 (reconcile / cancel / modify)
```

종목 핸들로 한 종목을 다룬다.

```python
kis.domestic.stock("005930")     # DomesticStock (quote/bars/order_book/buy/sell/…)
kis.overseas.stock("AAPL")       # OverseasStock (거래소 자동)
kis.domestic.futures("101W09")   # FuturesContract (underlying_quote 있음)
kis.domestic.option("201W09")    # OptionContract  (없음 — 선물 전용)
```

## 주문

```python
r = kis.domestic.stock("005930").buy(quantity=10, limit_price=70000)
kis.orders.reconcile(r.client_order_id)   # 브로커 대조 → 확정
kis.orders.cancel(r.client_order_id)
```

클라이언트측 안전 커널: **오확정 금지 · write 무재시도 · 보수적 reconcile**. 신용/주문가능은
기본 차단. → [`docs/orders-and-safety.md`](docs/orders-and-safety.md)

## 문서

[빠른 시작](docs/quickstart.md) · [국내](docs/domestic.md) · [해외](docs/overseas.md) ·
[퇴직연금](docs/pension.md) · [주문·안전](docs/orders-and-safety.md)

엔드포인트별 상세는 각 메서드 docstring에 (`help(...)` / IDE).
