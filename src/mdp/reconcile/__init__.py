"""스트림 · 배치 대사 — 같은 날의 스트림 1 분봉과 배치 1 분봉을 분마다 맞대고, 다르면 원인을 나눈다
(:mod:`mdp.reconcile.daily`).

읽고 쓰는 것: Delta ``gold/bars_1m`` · ``gold/stream_bars_1m`` · ``silver/agg_trades`` ·
Kafka 원문 토픽(읽기),
PostgreSQL ``reconcile_daily`` · ``reconcile_minutes`` (더하기만), 메울 때 ``gold/stream_bars_1m``.
"""
