"""kis_trader MCP 서버 -- KIS 계좌를 안전커널 규율로 MCP 도구로 노출한다.

공식 KIS MCP 와 달리 이 서버는 ``kis_trader`` 공개 API(안전 코어)를 소비하는 얇은 계층이다.
도메인 계산을 하지 않고(숫자는 전부 패키지가 만든다), 노출하는 도구도 **안전한 부분집합**뿐이다:

- READ_ONLY 조회(시세·검색·순위·잔고·보유·미체결)
- 주문 **미리보기**(dry-run 티켓 -- 전송하지 않는다)
- reconcile(결과 불명 주문의 사후 확정)

실주문 전송(매수/매도/정정/취소)은 **의도적으로 노출하지 않는다**: SUBMIT_ORDER 는 그 턴의 신선한
사람 승인이 필요한데(플레이북 Sec.5), 헤드리스 MCP 는 그 승인을 받을 수 없다. 실주문은 사람이 파이썬
API 로 직접 낸다. 자세한 규율은 ``plugins/kis-trader/skills/kis-trader/SKILL.md``.

``mcp`` 패키지는 선택 의존성이라(``pip install 'kis-trader[mcp]'``) :mod:`kis_trader.mcp.handlers`
(순수 함수)는 ``mcp`` 없이도 import 되지만 :mod:`kis_trader.mcp.server` 는 ``mcp`` 를 요구한다.
"""

from __future__ import annotations

from . import handlers

__all__ = ["handlers"]
