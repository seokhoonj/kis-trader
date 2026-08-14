"""횡단 배관(auth/http/wire/datetime/freeze/fsutil/endpoints/ratelimit/response/masters).

자산군에 속하지 않는 세션 전역 사설 인프라. 이 패키지는 위(엔티티/자산군)를 절대 import 하지 않는다
(errors/transport 만 상위에서 끌어온다) -- 그래서 import 그래프의 최하층이다.
"""

from __future__ import annotations
