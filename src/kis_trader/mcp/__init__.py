"""kis_trader MCP 서버 -- KIS 계좌를 안전커널 규율로 MCP 도구로 노출한다.

이 서버는 ``kis_trader`` 공개 API(안전 코어)를 소비하는 얇은 계층이다. 도메인 계산을 하지 않고
(숫자는 전부 패키지가 만든다), 노출하는 도구는 다음과 같다:

- 조회(시세·검색·순위·잔고·보유·미체결)
- 주문 **미리보기**(dry-run 티켓 -- 전송하지 않는다)
- reconcile(결과 불명 주문의 사후 확정)
- 실주문(매수·매도·정정·취소) -- **모의 기본**, 실전은 이중게이트 + RiskLimits + 종목 allowlist +
  사람 확인(elicitation) + 세션 서킷브레이커를 **모두** 통과해야 전송된다(하나라도 빠지면 거부).
  주문 파라미터는 사람이 직접 주고 확인한다 -- 조회 결과를 그대로 주문에 넣지 않는다(taint 경계).

``mcp`` 패키지는 선택 의존성이라(``pip install 'kis-trader[mcp]'``) :mod:`kis_trader.mcp.handlers`
(순수 함수)는 ``mcp`` 없이도 import 되지만 :mod:`kis_trader.mcp.server` 는 ``mcp`` 를 요구한다.
"""

from __future__ import annotations

from . import handlers

__all__ = ["handlers"]
