# kis-trader

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)

한국투자증권 KIS Open API **"비공식"** 파이썬 클라이언트.

```python
from kis_trader import KISClient

kis = KISClient(app_key="…", app_secret="…", account="12345678-01")

kis.domestic.stock("005930").quote()                 # 삼성전자 현재가
kis.overseas.stock("AAPL").quote()                   # 애플 (거래소 자동)
kis.domestic.ranking.by_change(direction="gainers")  # 오늘 상승률 순위
```

📖 **문서: <https://seokhoonj.github.io/kis-trader/>**

## 설치

```bash
uv pip install -e .      # 아직 PyPI 에 올리기 전이라, 받은 폴더에서 바로 설치
```

## 이렇게 생겼습니다

`KISClient` 를 하나 만들면, 그 아래가 **국내·해외·연금·주문**으로 나뉩니다.

```python
kis.domestic     # 국내 주식·지수·채권·ELW·선물옵션·계좌·순위·시장·일정
kis.overseas     # 해외 주식·지수·선물옵션·계좌·순위
kis.pension      # 퇴직연금
kis.orders       # 낸 주문 확인·정정·취소
```

한 종목은 `stock("코드")` 로 잡아서, 거기서 시세를 보거나 주문합니다.

```python
kis.domestic.stock("005930")     # 국내 주식 (현재가·차트·호가·매수·매도 …)
kis.overseas.stock("AAPL")       # 해외 주식 (거래소 자동)
kis.domestic.futures("101W09")   # 지수선물
kis.domestic.option("201W09")    # 지수옵션
```

## 주문

```python
r = kis.domestic.stock("005930").buy(quantity=10, limit_price=70000)  # 10주 매수
kis.orders.reconcile(r.client_order_id)   # 주문이 실제로 들어갔는지 증권사에 확인
kis.orders.cancel(r.client_order_id)      # 취소
```

주문은 되돌릴 수 없어서, 실수로 **두 번 나가지 않도록** 안전장치가 들어 있습니다(신용거래는 기본으로
막아 둠). → [주문 설명](https://seokhoonj.github.io/kis-trader/orders.html)

## 명령줄 (`kis`)

설치하면 터미널 명령 `kis` 도 함께 깔립니다. 파이썬을 짜지 않고도 조회·주문을 할 수 있습니다.
주문은 **`--execute` 를 붙이기 전엔 실제로 안 나갑니다** — 미리 확인만 하는 **실행 안 함(dry-run)** 상태입니다.

```bash
kis stock quote 005930
kis search 삼성전자
kis ranking change --direction gainers
kis account balance
kis order buy 005930 10 --limit-price 70000                       # 실행 안 함(dry-run), 전송 안 됨
kis order buy 005930 10 --limit-price 70000 --execute paper --yes  # 모의투자로 실제 전송
```

앱키·계좌는 환경변수나 `KISConfig(...).save()` 로 한 번 저장해 둡니다. →
[자격증명과 프로필](https://seokhoonj.github.io/kis-trader/configuration.html)

Claude Code / Codex 같은 AI 도구로 계좌를 다루려면 `skills/kis-trader/` 스킬을 씁니다. →
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

각 메서드의 자세한 설명(넣는 값·나오는 값)은 `help(그_메서드)` 로 바로 볼 수 있습니다.

## 라이선스

[MIT](LICENSE) © seokhoonj
