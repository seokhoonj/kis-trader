# 소개 {.unnumbered}

한국투자증권(KIS) Open API를 파이썬에서 쉽게 쓰기 위한 클라이언트입니다. 시세 조회부터
주문·계좌·순위까지 **자산군별로 정리된 메서드** 하나로 부릅니다.

```python
from kis_trader import KISClient

kis = KISClient(app_key="…", app_secret="…", account="12345678-01")

kis.domestic.stock("005930").quote()                 # 삼성전자 현재가
kis.overseas.stock("AAPL").quote()                   # AAPL (거래소 자동)
kis.domestic.ranking.by_change(direction="gainers")  # 오늘 상승률 순위
```

## 이 문서는

투자자가 **실제로 하는 일** 위주로 코드와 함께 설명합니다. 왼쪽 차례는 파트로 묶여 있습니다.

| 파트 | 장 |
|------|----|
| **시작하기** | [빠른 시작](quickstart.md) |
| **국내 주식** | [시세 보기](quotes.md) · [재무·실적](financials.md) · [종목 수급](flows.md) · [계좌·잔고·손익](account.md) · [주문](orders.md) |
| **시장·검색** | [순위·조건검색](screening.md) · [시장·지수](market.md) · [기업행위·일정](corporate-actions.md) |
| **해외·연금** | [해외주식](overseas.md) · [퇴직연금](pension.md) |
| **다른 상품** | [ETF·ETN](etf.md) · [ELW](elw.md) · [선물·옵션](derivatives.md) · [채권](bonds.md) |
| **명령줄·에이전트** | [Command Line](cli.md) · [Claude Code 스킬](skill.md) |
| **참고** | [실시간(WebSocket)](realtime.md) · [한계·미구현](limits.md) |

## 알아둘 것

- **대상**: 국내 KIS 사용자. 메서드 이름은 영어, 각 메서드 설명(파라미터·필드)은 한국어
  docstring에 있습니다 — 궁금하면 `help(kis.domestic.stock)` 처럼 바로 확인.
- **결과는 읽기전용 객체**입니다. `.현재가` 같은 매핑 필드로 꺼내 쓰고, 원본 응답 전체는 `._raw`에 있습니다.
- **주문은 안전 우선**입니다. 오확정 금지·재시도 금지 등 안전장치는 [주문](orders.md) 참고.

## 면책조항

- 이 프로젝트는 한국투자증권의 [공식 API 포털](https://apiportal.koreainvestment.com/apiservice)과
  [공식 저장소](https://github.com/koreainvestment)를 참조해 만든 **"비공식"** 오픈소스 클라이언트입니다.
- **모든 사용은 사용자 본인의 책임**입니다. 이 소프트웨어의 사용(주문 실행·조회 포함)으로 발생한 금전적
  손실, 주문 오류, 데이터 오류, API 변경으로 인한 오작동 등 어떠한 손해에 대해서도 제작자는
  책임지지 않습니다.
- 실거래 전에 반드시 모의투자(`environment="paper"`)로 충분히 검증하고, 주문 로직은 소액으로 확인하세요.
