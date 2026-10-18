# TCN 실험 검증 기록

2026-10-18 · 성과 전 기록. 설계 동결 `25b51b8`.

- BTC/ETH 각 3,287,520분·13,698개 재집계4h, 분봉 gap0.
- 각 funding6,849개, 정산mark3,107개·hour시가대리3,742개. 2020-06~2026-08만 열람.
- 운영 .venv를 수정하지 않고 `build/tcn-venv` 구성. torch2.11.0+cu128,
  numpy2.5.4, sklearn1.9.1. RTX3060 12GB 확인.
- 신규 pytest13개와 독립환경 unittest6개 통과. 특징 미래불변/가격스케일불변,
  gap워밍업/purge/정답동점/지정가한도/수량내림/부분체결/미종결/stop갭/
  stop분 funding순서/공유계좌동시노출/Decimal원장/causal TCN을 포함한다.
- 전체 pytest 4,272개 통과(244.12초). leakage142개 통과(27.20초).
- 신규 코드 ruff/format 정상, 특징·체결 핵심 strict mypy 정상. 계층계약5개 유지.
- 전체 mypy는 기존 파일의 오류7건이 남는다: wallet_grid_data_20261013·
  run_ag_short_leverage_20261013 attr-defined각1, test_v_exec_267 index2,
  run_alt_short_230 arg-type1, test_shadow_272 unused-ignore1,
  test_altair_live unreachable1. 신규 execution numpy 선택의존성 오류는 교정했다.
- 전체 ruff는 기존 run_deneb1x_262·run_qqq_combo_263·run_split_264 오류16개,
  같은3파일 포맷불일치가 있다. HEAD 원본의 ruff 오류 재현을 확인했다.
  해당 기존 파일은 수정하지 않았으며 전체 정적검사 통과라고 하지 않는다.

성과 열람 전 교정: 보고서문자열 포맷, numpy 선택의존성 타입표시,
정산시각이 stop분 시가보다 조금 늦을 때 debit누락 방지,
비용분해·잔량 감사필드 추가. 입력/정답문턱/모델/손절/기간 수치는 바꾸지 않았다.

명목10%는 진입시 1배 증거금 상한이다. 보유 중 가격변화로 평가명목 비율은 달라진다.
MDD는 분봉 종가 MTM과 실제 모형 stop가격 반영 기준이며 모든 tick 극값 보장은 아니다.
결과·가중치·scaler·예측·거래원장·해시는 Git 밖 reports/tcn_chart_20261018에 보존한다.
