"""벤치마크 — 같은 체결 월 파일을 연구 스크립트 · DuckDB · Spark 로 처리해 시간 · 메모리 비교.

무엇: 세 방법이 **똑같은 과제**(:mod:`mdp.bench.result` 머리말)를 풀게 하고, 결과가 서로 같은지
      확인한 뒤 걸린 시간과 최대 메모리를 잰다(README 4-3).
언제 쓰나: 엔진 선택의 근거가 필요할 때 · 코드를 크게 바꾼 뒤. ``python -m mdp.bench.main`` 한 번.
읽고 쓰는 것: Bronze 월 zip(읽기) · 임시 CSV(``<lake>/_staging/bench``, 끝나면 지움) → 보고
              ``<lake>/_reports/bench/*.json``.
"""
