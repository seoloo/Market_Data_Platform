"""재처리 — 규칙을 바꿔 다시 만들고, 바뀐 것을 Delta 시간여행으로 증명하고, 필요하면 되돌린다.

무엇: 재처리 전 버전 기록(:mod:`mdp.reprocess.main`) → 다시 만들기 → 표마다 이전 · 새 버전 비교
      (:mod:`mdp.reprocess.diff`) → 보고. 되돌리기는 Delta RESTORE(:mod:`mdp.reprocess.restore`).
언제 쓰나: 감사 규칙 · 봉 규칙을 바꿨을 때, 원천 결함을 고쳐 다시 만들 때 (README 4-5).
읽고 쓰는 것: Silver · Gold Delta 표(버전) · 보고 ``<lake>/_reports/reprocess/*.json`` · 실행 기록.

[이유] Bronze 에 원본이 그대로 있어야만 할 수 있는 일이다 — 연구 216 은 원본을 지워 규칙을 바꾸려면
       86 GB 를 다시 받아야 했다. 여기서는 같은 명령으로 다시 만들고, '무엇이 바뀌었나' 를 행 단위
       숫자로 보이고, 마음에 들지 않으면 이전 버전으로 되돌린다.
"""
