# Auto_Trading

BTC/USDT 무기한 선물 자동매매·연구 시스템 (Binance USD-M).

현재 Phase 0 — 문서 기준 재정리 완료, 다음 연구 선정 전입니다.
기존 데이터·전략 코어·백테스트 기반은 있으나 H1/H2/H3/R1은 기각됐고 실거래 기능은 완료되지 않았습니다.
목표는 비용 이후에도 재현 가능한 수익을 검증하고 위험과 판단 근거를 기록하는 것입니다. 수익을 보장하지 않습니다.

## 시작하기

- [현재 진행 상황](PROGRESS.md)
- [현재 시스템 명세](docs/current/CURRENT_SPEC.md): 여기서 관련 Current 문서로 이동합니다.
- [에이전트 작업 규약](AGENTS.md)
- [연구 결과 요약](docs/current/RESEARCH_HISTORY.md)
- [과거 문서 보관 색인](docs/archive/README.md): 필요할 때만 읽습니다.

D22는 중단된 미완성 초안입니다. 전략 코드는 현재 명세에 자동 승인된 수익 전략이 아닙니다.

## 개발 환경

```bash
python -m venv .venv
.venv/Scripts/activate
# Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install
```

검사 명령과 게이트는 [BACKTEST_AND_TESTING](docs/current/BACKTEST_AND_TESTING.md)을 따릅니다.
위험 규칙은 [TRADING_CORE](docs/current/TRADING_CORE.md), 보안·운영·저장 정책은 [OPERATIONS](docs/current/OPERATIONS.md)에 있습니다.
사용자 승인 없이 실거래하거나 데이터를 정리하지 않습니다.

## 라이선스

Proprietary. 무단 사용·배포 금지.
