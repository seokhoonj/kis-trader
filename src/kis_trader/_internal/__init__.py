"""횡단 배관(auth/http/wire/datetime/freeze/fsutil/endpoints/ratelimit/response/masters).

자산군에 속하지 않는 세션 전역 사설 인프라. 자산군/엔티티 계층은 import 하지 않고, 상위에서 끌어오는
의존은 횡단 타입에 한정된다 -- errors, transport, 그리고 와이어 어휘인 order(Side)/instrument
(마스터 레코드/거래소 타입). 이들 사이에 순환은 없다.
"""

from __future__ import annotations
