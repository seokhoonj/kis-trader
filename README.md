# kis-trader

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

한국투자증권(KIS) Open API 파이썬 클라이언트. Python ≥ 3.11.

```python
from kis_trader import KISClient

kis = KISClient(app_key="…", app_secret="…", account="12345678-01")

kis.domestic.stock("005930").quote()           # 삼성전자 현재가
kis.overseas.stock("AAPL").quote()             # AAPL (거래소 자동)
kis.domestic.ranking.by_change(direction="gainers")  # 등락률 순위
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
기본 차단. → [`docs/orders.md`](docs/orders.md)

## 명령줄 (`kis`)

설치하면 `kis` 명령이 함께 깔린다. 주문은 **기본 dry-run**이고 `--execute` 로만 전송한다.

```bash
kis stock quote 005930
kis search 삼성전자
kis ranking change --direction gainers
kis account balance
kis order buy 005930 10 --limit-price 70000                      # dry-run (전송 안 됨)
kis order buy 005930 10 --limit-price 70000 --execute paper --yes  # 모의 전송
```

자격증명은 환경변수(`KIS_APP_KEY`/`KIS_APP_SECRET`/`KIS_CANO`/`KIS_ACNT_PRDT_CD`; 프로필별 접두어)나
`KISConfig(...).save()`. → [`docs/cli.md`](docs/cli.md)

Claude Code / Codex 로 계좌를 몰려면 `skills/kis-trader/` 스킬을 쓴다. →
[`docs/claude-skill.md`](docs/claude-skill.md) · [`docs/codex-skill.md`](docs/codex-skill.md)

## 문서

[빠른 시작](docs/quickstart.md) · [시세](docs/quotes.md) · [계좌·손익](docs/account.md) ·
[주문](docs/orders.md) · [순위·검색](docs/screening.md) · [시장·지수](docs/market.md) ·
[해외](docs/overseas.md) · [퇴직연금](docs/pension.md) · [실시간](docs/realtime.md) ·
[명령줄](docs/cli.md) · [Claude 스킬](docs/claude-skill.md) · [Codex 스킬](docs/codex-skill.md)

엔드포인트별 상세는 각 메서드 docstring에 (`help(...)` / IDE).

## 라이선스

[MIT](LICENSE) © seokhoonj
