"""Bronze 계층 — 원천 파일을 받은 그대로 보관한다.

무엇: 바이낸스 공개 아카이브(data.binance.vision) zip 과 그 ``.CHECKSUM`` 을 원천과 같은 경로로
      저장한다.
언제 쓰나: 파이프라인 맨 앞(README 3-1). Silver 는 여기 있는 파일만 읽는다.
읽고 쓰는 것: 원천 HTTP(읽기) → Bronze 폴더(쓰기, 한 번 쓴 파일은 바꾸지 않음) · 체크섬 목록
              CSV(``manifests/bronze/``).
"""
