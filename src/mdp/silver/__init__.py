"""Silver 계층 — Bronze 원본을 정제 · 감사한 Delta 표.

무엇: Bronze zip 을 읽어 형식을 맞추고(타입 · 시각 단위), 결함(빈 곳 · 중복 · 역행 · 기간 밖)을 찾아
      깨끗한 줄은 Silver 표로, 못 쓰는 줄은 격리 표로, 찾은 것은 감사 표로 나눠 쓴다.
언제 쓰나: Bronze 받기(3 단계) 뒤. Spark 이미지(``docker/spark``) 안에서 돈다.
읽고 쓰는 것: Bronze(읽기만) · 체크섬 목록 → ``<lake>/silver/*`` Delta 표 · 실행 기록.
"""
