"""KIS REST 도메인 -- 실전/모의 베이스 URL."""

from __future__ import annotations

_DOMAIN = {
    "real": "https://openapi.koreainvestment.com:9443",
    "demo": "https://openapivts.koreainvestment.com:29443",
}


def base_url(environment: str) -> str:
    """실전(real)/모의(demo) REST 베이스 URL. 미지 환경은 KeyError(호출측 계약 오류)."""
    return _DOMAIN[environment]
