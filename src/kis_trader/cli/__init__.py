"""kis_trader 명령줄 인터페이스(``kis``). 공개 API 를 감싸는 consumer 계층이다.

import 시 부작용 없음 -- ``main`` 만 재노출한다.
"""
from __future__ import annotations

from .app import main

__all__ = ["main"]
