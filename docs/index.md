# kis-openapi

한국투자증권(KIS) Open API를 위한 깔끔한 파이썬 클라이언트.

```python
from kis_openapi import KISClient

kis = KISClient(app_key="…", app_secret="…", account="12345678-01")

kis.domestic.stock("005930").quote()          # 삼성전자 현재가
kis.overseas.stock("AAPL").quote()            # AAPL (거래소 자동 해석)
kis.domestic.ranking.by_change(top="gainers") # 등락률 순위
```

**대상: 국내 KIS 사용자.** 공개 **식별자**는 국제 표준 영어 용어를, 설명 **docstring**은
한국어(+ KIS URL·TR-id)를 쓴다. Python ≥ 3.11.

## API 모양

모든 것은 세션 객체 `KISClient` 하나에서 자산군별로 갈라진다.

| 최상위 | 무엇 |
|---|---|
| `kis.domestic` | 국내(KRX/NXT) 주식·지수·채권·ELW·지수파생·계좌·순위·시장·캘린더 |
| `kis.overseas` | 해외 주식·지수·파생·계좌·순위·참조데이터 |
| `kis.pension` | 퇴직연금 계좌 |
| `kis.orders` | 자산무관 주문 라이프사이클(`reconcile`/`cancel`/`modify`) |
| `kis.instrument` | 자산군 판별 *이전* 심볼 해석 |

**종목/계약 핸들**로 한 종목을 다룬다:

```python
kis.domestic.stock("005930")     # DomesticStock
kis.overseas.stock("AAPL")       # OverseasStock (거래소 자동)
kis.domestic.futures("101W09")   # FuturesContract (underlying_quote 있음)
kis.domestic.option("201W09")    # OptionContract  (없음 — 선물 전용)
```

## 안전한 주문 경로

주문은 클라이언트측 안전 커널을 거친다 — **오확정 금지 · write 무재시도 · 보수적 reconcile**.
고위험(신용/주문가능)은 기본 차단. 자세히는 [주문과 안전](orders-and-safety.md).

## 문서

- [빠른 시작](quickstart.md)
- [국내 `kis.domestic`](domestic.md)
- [해외 `kis.overseas`](overseas.md)
- [퇴직연금 `kis.pension`](pension.md)
- [주문과 안전 모델](orders-and-safety.md)
- [API 레퍼런스](reference/index.md) — docstring에서 자동 생성
