"""국내주식 KIS 와이어 조회 계층 (내부, 사용자 비노출).

각 모듈은 KIS 엔드포인트/TR-ID/파라미터 매핑 + 호출 + 파싱을 담아, 사용자면
(:class:`~kis_trader.client.KISClient` / :class:`~kis_trader.stock.DomesticStock`)에 통합 반환
타입을 돌려준다. KIS URL 구조는 전부 이 안에 갇히고 바깥으로 새지 않는다.
"""
