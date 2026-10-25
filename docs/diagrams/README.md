# README 흐름도

저장소 첫 화면(README)의 그림 원본. 그림 하나 = HTML 하나, 모양은 `style.css` 하나로 맞춘다.

| 파일 | 그림 | 상태 표시 |
|---|---|---|
| `01_as_is.html` | 지금 구조 | 현재 상태 |
| `02_to_be.html` | 구현된 구조 (처음엔 목표 구조) | 구현됨 · 2026-10-24 |
| `03_trade_audit.html` | 체결 데이터 품질 감사 | 구현됨 · 연구 216 |
| `04_reconcile.html` | 스트림 · 배치 대사 | 구현됨 · 5-2 |

## 고치는 법

1. HTML 의 글자를 고친다. 숫자는 근거 문서(그림 아래쪽 '근거:')에 있는 것만 쓰고, 확인 못 한 값은 '확인 필요'.
2. 단계가 구현되면 오른쪽 위 상태 표시를 바꾼다(`계획 · 구현 전` → `구현됨`).
3. PNG 로 찍는다 — Chrome 이 HTML 을 2 배 해상도로 찍고, `trim.py` 가 아래 흰 여백을 자른다.

```bash
sh docs/diagrams/render.sh 02_to_be 1400
python docs/diagrams/trim.py docs/diagrams/02_to_be.png
```

- `render.sh <이름> <높이 px>`: 높이는 그림이 잘리지 않을 만큼 넉넉히 준다(남는 여백은 `trim.py` 가 자른다).
- Chrome 경로는 `render.sh` 안에 Windows 기본 설치 경로로 적혀 있다.
- 글꼴은 PC 에 설치된 Noto Sans KR(없으면 맑은 고딕)을 쓴다. 다른 PC 에서 찍으면 글꼴이 달라질 수 있다.
- `trim.py` 는 Pillow 가 필요하다.
