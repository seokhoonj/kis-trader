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

📖 **문서: <https://seokhoonj.github.io/kis-trader/>**

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
기본 차단. → [주문](https://seokhoonj.github.io/kis-trader/orders.html)

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

자격증명은 환경변수(`KIS_APP_KEY`/`KIS_APP_SECRET`/`KIS_ACCOUNT`; 프로필별 접두어)나
`KISConfig(...).save()`. → [자격증명과 프로필](https://seokhoonj.github.io/kis-trader/configuration.html)

Claude Code / Codex 로 계좌를 몰려면 `skills/kis-trader/` 스킬을 쓴다. →
[Claude 스킬](https://seokhoonj.github.io/kis-trader/claude-skill.html) · [Codex 스킬](https://seokhoonj.github.io/kis-trader/codex-skill.html)

## 문서

전체 문서는 **<https://seokhoonj.github.io/kis-trader/>** 에 있습니다(소스: `docs/`).

| 파트 | 장 |
|------|----|
| **시작하기** | [빠른 시작](https://seokhoonj.github.io/kis-trader/quickstart.html) · [앱키 발급](https://seokhoonj.github.io/kis-trader/appkey.html) · [자격증명과 프로필](https://seokhoonj.github.io/kis-trader/configuration.html) |
| **국내 주식** | [시세](https://seokhoonj.github.io/kis-trader/quotes.html) · [재무·실적](https://seokhoonj.github.io/kis-trader/financials.html) · [종목 수급](https://seokhoonj.github.io/kis-trader/flows.html) · [계좌·손익](https://seokhoonj.github.io/kis-trader/account.html) · [주문](https://seokhoonj.github.io/kis-trader/orders.html) |
| **시장·검색** | [순위·조건검색](https://seokhoonj.github.io/kis-trader/screening.html) · [시장·지수](https://seokhoonj.github.io/kis-trader/market.html) · [기업행위·일정](https://seokhoonj.github.io/kis-trader/corporate-actions.html) |
| **해외·연금** | [해외주식](https://seokhoonj.github.io/kis-trader/overseas.html) · [퇴직연금](https://seokhoonj.github.io/kis-trader/pension.html) |
| **다른 상품** | [ETF·ETN](https://seokhoonj.github.io/kis-trader/etf.html) · [ELW](https://seokhoonj.github.io/kis-trader/elw.html) · [선물·옵션](https://seokhoonj.github.io/kis-trader/derivatives.html) · [채권](https://seokhoonj.github.io/kis-trader/bonds.html) |
| **명령줄·에이전트** | [Command Line](https://seokhoonj.github.io/kis-trader/cli.html) · [Claude Skill](https://seokhoonj.github.io/kis-trader/claude-skill.html) · [Codex Skill](https://seokhoonj.github.io/kis-trader/codex-skill.html) |
| **참고** | [실시간(WebSocket)](https://seokhoonj.github.io/kis-trader/realtime.html) · [한계·미구현](https://seokhoonj.github.io/kis-trader/limits.html) |

엔드포인트별 상세는 각 메서드 docstring에 (`help(...)` / IDE).

## 라이선스

[MIT](LICENSE) © seokhoonj
