# 재처리 시연 (README 4-5)

> 규칙을 바꿔 같은 Bronze로 Silver → Gold → PostgreSQL을 다시 만들고, **무엇이 바뀌었고 무엇이 그대로인지**를
> Delta 시간여행으로 행 단위로 증명한 뒤, `RESTORE`로 되돌리고 되돌린 것까지 증명했다.
> 코드: [`src/mdp/reprocess/`](../src/mdp/reprocess)

## 왜 필요한가

연구 216은 체결 원본 86GB를 집계한 뒤 지웠다. 감사 규칙을 바꾸려면 처음부터 다시 받아야 했다.
여기서는 Bronze에 원본이 남아 있으므로 **같은 명령으로 다시 만들 수 있다.** 다만 '다시 만들었다'만으로는
믿을 수 없어서, 바꾸기 전 · 후 버전을 나란히 열어 차이를 숫자로 남기고, 잘못이면 되돌릴 수 있어야 한다.

## 흐름

```text
1 before 버전 기록 ─▶ 2 같은 명령으로 다시 만들기 ─▶ 3 after 버전 기록 ─▶ 4 표마다 before ↔ after 비교 ─▶ 5 보고
   (되돌릴 곳)          Silver(바꾼 규칙) → Gold → 복사      (DuckDB 시간여행, 키로 맞대기)        (JSON + 실행 기록)
                                                                    │
                                    마음에 안 들면 ─▶ RESTORE(이전 버전 내용으로 새 커밋) ─▶ 다시 비교 = 모두 같음
```

```bash
python -m mdp.reprocess.main --symbol BTCUSDT --month 2024-03 --volume-tolerance-pct 0.0001
docker compose run --rm spark python -m mdp.reprocess.restore --report /lake/_reports/reprocess/<보고>.json
python -m mdp.reprocess.diff --report data/lake/_reports/reprocess/<보고>.json --against-latest   # 0 이면 원래대로
```

- 다시 만들기는 **평소와 같은 명령**(`mdp.silver.agg_trades` · `mdp.gold.bars_1m` · `mdp.serve.bars_1m`)을 부른다.
  재처리 전용 코드가 따로 있으면 평소 결과와 재처리 결과가 다른 코드에서 나올 수 있다.
- 비교는 표마다 정한 키(체결 번호 · 날짜 · 분 등)로 맞대어 **추가 · 삭제 · 변경**을 세고, 변경은 **칸마다** 센다.
  다시 만들면 늘 바뀌는 계보 칸(`run_id` · `silver_version`)은 뺀다.

## 시연 — BTCUSDT 2024-03, 날 대조 의심일 기준 0.5% → 0.0001%

**바꾼 규칙**: Silver `agg_trades_audit_daily`의 의심일 기준(그날 거래량이 공식 1분봉 합계와 이만큼 넘게 다르면 의심).

**재처리 결과** (18분, 보고 `BTCUSDT-2024-03-20261023T154939Z.json`):

| 표 | 버전 | 행 | 추가 · 삭제 | 변경 |
|---|---|---:|---|---|
| `silver/agg_trades` | v1 → v2 | 66,512,993 | 0 · 0 | **0** |
| `silver/agg_trades_audit_daily` | v1 → v2 | 31 | 0 · 0 | **10행, `suspect` 칸만** |
| `silver/agg_trades_audit_file` | v1 → v2 | 2 | 0 · 0 | 0 |
| `silver/agg_trades_gaps` · `quarantine` | 그대로 | 0 | 0 · 0 | 0 |
| `gold/bars_1m` | v0 → v1 (Silver v2에서) | 44,640 | 0 · 0 | 0 |
| `gold/bars_1m_kline_check` | v0 → v1 | 31 | 0 · 0 | 0 |
| PostgreSQL `gold_bars_1m` | 복사 | 44,640 | | 체크섬 같음 (`9883fca…`) |

→ 규칙 변경의 **영향 범위가 정확히 한 표의 한 칸, 10일**이라는 것이 숫자로 드러났다. 체결 · 봉 값은 하나도 바뀌지 않았다.

**되돌리기** — `RESTORE`로 표마다 재처리 전 버전의 내용으로 새 커밋을 만들었다(Silver v3 · Gold v2).
되돌린 뒤 '재처리 전 버전 ↔ 지금'을 다시 비교하니 **7개 표 모두 같음**(비교 도구 종료 코드 0), 의심일 다시 0일,
PostgreSQL 복사본도 같은 체크섬으로 다시 맞췄다. 재처리한 버전(Silver v2 · Gold v1)도 이력에 남아 언제든 다시 열 수 있다.

## 시험

- `tests/mdp/test_reprocess_diff.py`: 작은 표로 추가 · 삭제 · 칸별 변경 세기, 같은 내용 '같음', 실제 레이크의
  4-1 첫 판(v0) · 최적화 판(v1)이 같음.
- 재처리 전체 흐름은 위 시연이 실제 데이터로 한 번 돈 기록이다(실행 기록 `reprocess` · `silver.agg_trades` · `gold.bars_1m` · `serve.bars_1m`).
