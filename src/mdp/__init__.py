"""Market Data Platform — 시장 데이터 레이크하우스 파이프라인 (Bronze → Silver → Gold).

무엇: 바이낸스 공개 아카이브를 받은 그대로 보관하고(Bronze), 정제 · 감사(Silver) · 연구용 표(Gold)를
      언제든 다시 만드는 데이터 엔지니어링 코드.
언제 쓰나: README 진행 표 3 단계부터의 새 코드는 모두 여기에 둔다.
읽고 쓰는 것: 하위 모듈마다 머리말에 적는다.

[이유] 원본 연구 · 실거래 코드(``auto_trading``)와 섞지 않으려고 패키지를 따로 뒀다 — 그쪽은
       리팩토링 때 '동작 변경 0' 을 지켰고, 실거래 부분은 공개 전에 걷어
       낸다(docs/LIVE_REMOVAL_PLAN.md). 이 패키지는 ``auto_trading`` 을 import 하지 않는다.
"""
