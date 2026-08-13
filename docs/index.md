# 소개 {.unnumbered}

한국투자증권(KIS) Open API를 파이썬에서 쉽게 쓰기 위한 클라이언트다. 시세 조회부터
주문·계좌·순위까지, 증권사 URL을 외울 필요 없이 **자산군별로 정리된 메서드** 하나로 부른다.

```python
from kis_openapi import KISClient

kis = KISClient(app_key="…", app_secret="…", account="12345678-01")

kis.domestic.stock("005930").quote()           # 삼성전자 현재가
kis.overseas.stock("AAPL").quote()             # AAPL (거래소 자동)
kis.domestic.ranking.by_change(top="gainers")  # 오늘 상승률 순위
```

## 이 문서는

주식 하는 사람이 **실제로 하는 일** 위주로 코드와 함께 설명한다.

- [시작하기](quickstart.md) — 자격증명, 세션 만들기
- [시세 보기](quotes.md) — 현재가·차트·호가·체결
- [계좌·잔고·손익](account.md) — 예수금, 보유종목, 손익
- [주문](orders.md) — 매수·매도·정정·취소, 그리고 안전장치
- [순위·조건검색](screening.md) — 급등주 찾기, 스크리닝
- [시장·수급](market.md) — 외국인/기관 수급, 프로그램매매, 공매도, 캘린더
- [해외주식](overseas.md) · [퇴직연금](pension.md)

## 알아둘 것

- **대상**: 국내 KIS 사용자. 메서드 이름은 영어, 각 메서드 설명(파라미터·필드)은 한국어
  docstring에 있다 — 궁금하면 `help(kis.domestic.stock)` 처럼 바로 확인.
- **결과는 읽기전용 객체**다. `.현재가` 같은 매핑 필드로 꺼내 쓰고, 원본 응답 전체는 `._raw`에 있다.
- **주문은 안전 우선**이다. 오확정 금지·재시도 금지 등 안전장치는 [주문](orders.md) 참고.
