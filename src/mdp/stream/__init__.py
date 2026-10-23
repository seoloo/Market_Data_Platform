"""스트림 — 체결 원문을 Kafka 토픽에 보내고(:mod:`mdp.stream.produce`), 1 분봉으로 묶는다
(:mod:`mdp.stream.bars_1m`, Spark Structured Streaming). 배치 봉과 빠르게 맞대 보기는
:mod:`mdp.stream.check`.

무엇: 원천은 바이낸스 실시간 체결(WebSocket, 키 없음) 또는 Bronze 에 받아 둔 체결의 재생.
읽고 쓰는 것: Kafka 토픽 ``raw.binance.um.aggtrade`` · Delta ``<lake>/gold/stream_bars_1m`` ·
실행 기록.
"""
