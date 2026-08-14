"""자산군별 실시간 파서. 이 패키지를 import 하면 각 모듈이 TR 레지스트리에 등록된다."""

from __future__ import annotations

from . import domestic_stock  # noqa: F401  (import 부작용: 레지스트리 등록)
