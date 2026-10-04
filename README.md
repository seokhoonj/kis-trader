# kis-trader

[![check](https://github.com/seokhoonj/kis-trader/actions/workflows/check.yml/badge.svg)](https://github.com/seokhoonj/kis-trader/actions/workflows/check.yml)
[![PyPI](https://img.shields.io/pypi/v/kis-trader)](https://pypi.org/project/kis-trader/)
[![Python](https://img.shields.io/pypi/pyversions/kis-trader)](https://pypi.org/project/kis-trader/)
[![License](https://img.shields.io/pypi/l/kis-trader)](https://github.com/seokhoonj/kis-trader/blob/main/LICENSE)

**한국어** | [English](README.en.md)

한국투자증권(한투) **KIS Open API**의 비공식 파이썬 클라이언트 — 타입이 붙은 깔끔한 API로 감쌌습니다.

- **넓은 범위** — 국내·해외 주식·지수·ETF·ETN·ELW·선물옵션·채권의 시세·재무·수급, 계좌 잔고·손익, 순위·조건검색, 시장·일정, 퇴직연금.
- **주문** — 매수·매도·정정·취소에 신용·예약·TWAP 분할까지.
- **안전 코어** — 멱등 주문 저장(중복 차단)·전송 전 리스크 한도(fat-finger 방지)·reconcile(재전송 대신 재확인)이 기본 동작, 신용거래는 기본 차단.
- **실시간** — 시세·호가·체결통보 WebSocket 구독.
- **세 가지 인터페이스** — 파이썬 API · 터미널 명령 `kis` · AI 에이전트용 MCP 서버/스킬.

## 1. 설치

```bash
pip install kis-trader
```

파이썬 3.11 이상이 필요합니다. 소스에서 개발 설치하려면 `uv pip install -e .` 를 쓰세요.

AI 에이전트(Claude Desktop 등)에 연결하는 MCP 서버까지 쓰려면 선택 설치를 더합니다:

```bash
pip install 'kis-trader[mcp]'
```

## 2. 빠른 시작

```python
from kis_trader import KISClient

kis = KISClient(app_key="…", app_secret="…", account="12345678-01")

kis.domestic.stock("005930").quote()                 # 삼성전자 현재가
kis.overseas.stock("AAPL").quote()                   # 애플 (거래소 자동 판별)
kis.domestic.ranking.by_change(direction="gainers")  # 오늘 상승률 순위
```

앱키와 계좌는 환경변수나 `KISConfig(...).save()` 로 한 번 저장해 두면 이후 인자 없이 열 수
있습니다. 실전 계좌와 모의투자 계좌를 모두 지원하므로, 먼저 모의투자로 주문 흐름을 확인한 뒤
실전으로 옮겨도 됩니다. 발급과 저장은 [자격증명과 프로필](https://seokhoonj.github.io/kis-trader/configuration.html)
문서를 참고하세요.

## 3. 구조

`KISClient` 아래로 자산군과 주문이 네임스페이스로 나뉩니다.

```python
kis.domestic   # 국내: 주식·지수·ETF·ELW·선물옵션·채권·계좌·순위·시장·일정
kis.overseas   # 해외: 주식·지수·선물옵션·계좌·순위
kis.account    # 계좌 조회 (상품코드 자동 분기; IRP는 .pension 퇴직연금 조회)
kis.orders     # 접수한 주문의 조회(미체결)·확인·정정·취소
```

종목과 계약은 핸들로 잡아 시세 조회부터 주문까지 이어갑니다.

```python
kis.domestic.stock("005930")     # 국내 주식 -- 현재가·차트·호가·매수·매도
kis.overseas.stock("AAPL")       # 해외 주식 -- 거래소 자동 판별
kis.domestic.futures("101W09")   # 지수선물
kis.domestic.option("201W09")    # 지수옵션
```

## 4. 주문

```python
r = kis.domestic.stock("005930").buy(quantity=10, limit_price=70000)  # 10주 매수
kis.orders.reconcile(r.client_order_id)   # 실제 접수 여부를 증권사에 재확인
kis.orders.cancel(r.client_order_id)      # 취소
```

주문은 되돌릴 수 없습니다. 중복 주문을 막는 안전장치가 기본으로 동작하고(신용거래는 기본
차단), 자세한 규칙은 [주문 문서](https://seokhoonj.github.io/kis-trader/orders.html)에 있습니다.

## 5. 명령줄 (`kis`)

설치하면 터미널 명령 `kis` 가 함께 깔립니다. 파이썬을 짜지 않고도 조회와 주문을 할 수 있습니다.
주문은 `--execute` 를 붙이기 전까지 실제로 전송되지 않습니다(dry-run).

```bash
kis stock quote 005930
kis search 삼성전자
kis ranking change --direction gainers
kis account balance
kis order buy 005930 10 --limit-price 70000
kis order buy 005930 10 --limit-price 70000 --execute paper --yes
```

채권·선물·옵션·해외·예약주문·TWAP 등 전체 명령과 옵션은
[명령줄 문서](https://seokhoonj.github.io/kis-trader/cli.html)를 보세요.

## 6. AI 코딩 에이전트에서 사용

Claude Code·Codex 같은 AI 도구로 계좌를 다루려면 `plugins/kis-trader/skills/kis-trader/` 스킬을 씁니다.
[Claude 스킬](https://seokhoonj.github.io/kis-trader/claude-skill.html) ·
[Codex 스킬](https://seokhoonj.github.io/kis-trader/codex-skill.html) 문서를 참고하세요.

MCP 호환 에이전트(Claude Desktop 등)에는 `kis_trader.mcp` 서버(`pip install 'kis-trader[mcp]'` 후
`kis-mcp`)로 계좌 조회·주문 미리보기 도구를 열 수 있습니다(실주문 전송은 노출하지 않음).
[MCP 서버](https://seokhoonj.github.io/kis-trader/mcp.html) 문서를 참고하세요.

## 7. 문서

전체 문서는 **<https://seokhoonj.github.io/kis-trader/>** 에 있습니다(소스: `docs/`).

| 파트 | 장 |
|------|----|
| **시작하기** | [빠른 시작](https://seokhoonj.github.io/kis-trader/quickstart.html) · [앱키 발급](https://seokhoonj.github.io/kis-trader/appkey.html) · [자격증명과 프로필](https://seokhoonj.github.io/kis-trader/configuration.html) |
| **국내 주식** | [시세](https://seokhoonj.github.io/kis-trader/quotes.html) · [재무·실적](https://seokhoonj.github.io/kis-trader/financials.html) · [종목 수급](https://seokhoonj.github.io/kis-trader/flows.html) · [계좌·손익](https://seokhoonj.github.io/kis-trader/account.html) · [주문](https://seokhoonj.github.io/kis-trader/orders.html) |
| **시장·검색** | [순위·조건검색](https://seokhoonj.github.io/kis-trader/screening.html) · [시장·지수](https://seokhoonj.github.io/kis-trader/market.html) · [기업행위·일정](https://seokhoonj.github.io/kis-trader/corporate-actions.html) |
| **해외·연금** | [해외주식](https://seokhoonj.github.io/kis-trader/overseas.html) · [퇴직연금](https://seokhoonj.github.io/kis-trader/pension.html) |
| **다른 상품** | [ETF·ETN](https://seokhoonj.github.io/kis-trader/etf.html) · [ELW](https://seokhoonj.github.io/kis-trader/elw.html) · [선물·옵션](https://seokhoonj.github.io/kis-trader/derivatives.html) · [채권](https://seokhoonj.github.io/kis-trader/bonds.html) |
| **명령줄·에이전트** | [Command Line](https://seokhoonj.github.io/kis-trader/cli.html) · [Claude Skill](https://seokhoonj.github.io/kis-trader/claude-skill.html) · [Codex Skill](https://seokhoonj.github.io/kis-trader/codex-skill.html) · [MCP Server](https://seokhoonj.github.io/kis-trader/mcp.html) |
| **참고** | [실시간(WebSocket)](https://seokhoonj.github.io/kis-trader/realtime.html) · [한계·미구현](https://seokhoonj.github.io/kis-trader/limits.html) |

각 메서드의 인자와 반환값은 `help(그_메서드)` 로 바로 볼 수 있습니다.

## 8. 라이선스

[MIT](LICENSE)

<!-- mcp-name: io.github.seokhoonj/kis-trader -->

