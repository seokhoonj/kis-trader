# kis-openapi

한국투자증권(KIS) Open API 파이썬 클라이언트. Python ≥ 3.11.

```python
from kis_openapi import KISClient

kis = KISClient(app_key="…", app_secret="…", account="12345678-01")

kis.domestic.stock("005930").quote()           # 삼성전자 현재가
kis.overseas.stock("AAPL").quote()             # AAPL (거래소 자동)
kis.domestic.ranking.by_change(top="gainers")  # 등락률 순위
```

세션 하나(`KISClient`)에서 자산군별로 갈라진다.

```python
kis.domestic     # 국내 주식·지수·채권·ELW·파생·계좌·순위·시장·캘린더
kis.overseas     # 해외 주식·지수·파생·계좌·순위
kis.pension      # 퇴직연금 계좌
kis.orders       # 주문 라이프사이클 (reconcile / cancel / modify)
```

종목 핸들로 한 종목을 다룬다.

```python
kis.domestic.stock("005930")     # DomesticStock
kis.overseas.stock("AAPL")       # OverseasStock (거래소 자동)
kis.domestic.futures("101W09")   # FuturesContract
kis.domestic.option("201W09")    # OptionContract
```

- [빠른 시작](quickstart.md) · [국내](domestic.md) · [해외](overseas.md) · [퇴직연금](pension.md) · [주문·안전](orders-and-safety.md)
- [API 레퍼런스](reference/index.md) — docstring 자동 생성
