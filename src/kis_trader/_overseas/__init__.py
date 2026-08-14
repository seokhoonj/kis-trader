"""해외주식 조회/주문 내부 엔진 (KIS ``overseas-price`` / ``overseas-stock`` 세그먼트).

국내(:mod:`kis_trader._domestic`)와 대칭. 사용자면은 해외 거래소로 만든 종목 핸들
(:class:`~kis_trader.overseas.stock.OverseasStock`, ``kis.overseas.stock("AAPL", exchange="NAS")``)이 호출한다.
"""
