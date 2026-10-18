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

## 최초 시도와 교정

34a943e의 최초실행은 정답표 단계에서 중단했다. BTC85/ETH62 시점 정답미확정,
모델 학습0회·계좌성과0회다. 중단 attempt.json/dataset.npz/dataset_audit.json을 보존한다.
사전등록의 '정답불명 제외'를 계좌기회 삭제로 쓰지 않도록 입력행을 모두 보존하고
불명 label=-1을 학습/분류에서만 제외했다. scaler는 TRAIN 전체 입력만 사용한다.
미종결잔량은 신규진입 중단 후 실제funding·기존stop·기말MTM·비용준비금까지 추적한다.
전략수치 변경이나 모델 재학습 구제가 아니다. v2 실행 식별자를 따로 둔다.

독립환경 검사는10개 통과(최초6+정답불명행 보존/잔량보존/기말계좌/확률동점 추가4).
순수24h 미래가격방향도 별도 진단으로 기록하되 주 학습목표나 입력으로 사용하지 않는다.
교정 후 전체pytest4,272개(238.01초)·leakage142개 재통과. 전체mypy 기존7건,
ruff 기존16건/포맷3파일, 계층5개 유지. 새 연구 및 그림생성기 ruff/format 정상.
