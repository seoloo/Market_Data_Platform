#!/usr/bin/env bash
# CI 를 이 PC 에서 그대로 — GitHub Actions(.github/workflows/ci.yml)와 같은 조건 · 같은 단계 (README C-2).
#
#   bash scripts/ci_local.sh            # 6 단계 모두 (처음 한 번은 설치에 몇 분)
#   bash scripts/ci_local.sh pytest     # 한 단계만: ruff · format · mypy · imports · pytest · leakage
#
# 왜: CI 를 시험 도구로 쓰지 않는다(AGENTS.md §6 — Actions 사용 시간 절약). push 전에 같은 조건으로 돌린다.
# 어떻게:
#   - Docker `python:3.11-slim` (CI 와 같은 파이썬). CI 가 받는 것과 같은 파일만 넘긴다 — git 이 아는 파일 +
#     아직 커밋 안 한 새 파일(.gitignore 대상 제외)을 tar 로 흘려 보내 컨테이너 안에서 `pip install -e ".[dev]"`.
#     (폴더째 붙이면 .gitignore 된 연구 산출물까지 검사돼 CI 와 결과가 달라진다 — 처음 만들 때 겪음.)
#   - pip 내려받기는 이름 있는 볼륨 `mdp_ci_pip` 에 남겨 두 번째부터 빠르다.
#   - 하나라도 실패하면 종료 코드 1, 단계마다 결과를 마지막에 모아 보여 준다.
# 무엇을 하지 않나: Spark 시험(`tests/mdp/spark`)은 Spark 이미지에서 따로 돈다(C-3 · ci.yml 의 spark 작업).
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && (pwd -W 2>/dev/null || pwd))"  # Windows Git Bash 는 C:/... 모양
ONLY="${1:-all}"

cd "$REPO"
git ls-files -z --cached --others --exclude-standard | tar --null -T - -cf - 2>/dev/null |
MSYS_NO_PATHCONV=1 docker run --rm -i \
  -v mdp_ci_pip:/root/.cache/pip \
  -e ONLY="$ONLY" \
  python:3.11-slim bash -c '
set -u
mkdir -p /work && tar -xf - -C /work
cd /work
pip install -q --disable-pip-version-check -e ".[dev]" >/tmp/pip.log 2>&1 || { tail -20 /tmp/pip.log; exit 1; }
declare -A RESULT
run() {  # run <이름> <명령...>
  local name="$1"; shift
  if [ "$ONLY" != all ] && [ "$ONLY" != "$name" ]; then return; fi
  echo "::: $name — $*"
  if "$@"; then RESULT[$name]=통과; else RESULT[$name]=실패; fi
}
run ruff ruff check .
run format ruff format --check .
run mypy mypy
run imports lint-imports
run pytest pytest -q -p no:cacheprovider
run leakage pytest -m leakage -q -p no:cacheprovider
echo
failed=0
for name in ruff format mypy imports pytest leakage; do
  [ -n "${RESULT[$name]:-}" ] || continue
  echo "$name: ${RESULT[$name]}"
  [ "${RESULT[$name]}" = 통과 ] || failed=1
done
exit $failed
'
