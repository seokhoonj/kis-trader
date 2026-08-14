"""``python -m kis_trader.cli`` 지원 -- main() 에 위임."""
from __future__ import annotations

from .app import main

if __name__ == "__main__":
    raise SystemExit(main())
