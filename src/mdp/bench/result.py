"""벤치마크 공통 과제와 결과 비교.

언제 쓰나: 세 엔진(:mod:`mdp.bench.engines`)이 결과를 이 모양으로 내고, :func:`compare` 로 서로 맞대
           본다.
읽고 쓰는 것: 없음 (사전만 다룬다).

과제 — 체결 월 파일 하나에서 (연구 216 추출기가 뽑던 값 그대로):
- ``rows`` · ``first_id`` · ``last_id`` · ``first_ms`` · ``last_ms``
- ``gaps``: 번호 순으로 이웃한 두 체결 사이 빠진 번호가 있는 곳
  ``[앞 시각, 뒤 시각, 빠진 수]`` (시각 순 정렬)
- ``duplicate_rows``: 같은 번호가 한 번 넘게 나온 줄 수
- ``time_reversed``: 번호 순으로 볼 때 시각이 앞 체결보다 이른 줄 수
- ``check_minutes``: 매시 00 분(분 시작이 1 시간의 배수) ``{분 ms: [거래량, 공격 매수량, 체결 수]}``
- ``windows``: 5 분 경계마다 첫 10 초 ``{창 ms: [공격 매수량, 공격 매도량, 체결 수]}``

비교 규칙: 정수는 정확히 같아야 하고, 거래량 같은 소수는 상대 오차 1e-9 안이면 같다고 본다
(연구 스크립트는 float 로 더하고, DuckDB · Spark 는 decimal 로 더한다).

[이유] 엔진마다 하는 일이 다르면 시간 비교가 의미 없다. 연구 스크립트가 하던 일을 기준 과제로 못
       박아 '같은 일을 누가 더 빨리 · 적은 메모리로' 를 잰다.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

__all__ = ["HOUR_MS", "MINUTE_MS", "RELATIVE_TOLERANCE", "WINDOW_MS", "WINDOW_STEP_MS", "compare"]

MINUTE_MS: Final = 60_000
HOUR_MS: Final = 3_600_000
WINDOW_STEP_MS: Final = 300_000
WINDOW_MS: Final = 10_000
RELATIVE_TOLERANCE: Final = 1e-9
SCALAR_KEYS: Final = (
    "rows",
    "first_id",
    "last_id",
    "first_ms",
    "last_ms",
    "duplicate_rows",
    "time_reversed",
)


def _close(left: float, right: float) -> bool:
    """[쓰임] 두 소수가 상대 오차 안에서 같은가."""
    scale = max(abs(left), abs(right), 1e-12)
    return abs(left - right) / scale <= RELATIVE_TOLERANCE


def _compare_buckets(
    name: str, left: Mapping[str, Any], right: Mapping[str, Any], differences: list[str]
) -> None:
    """[쓰임] ``{칸: [소수, 소수, 정수]}`` 두 개를 칸마다 비교해 다른 칸을 ``differences`` 에
    적는다(최대 5 개)."""
    keys = set(left) | set(right)
    found = 0
    for key in sorted(keys, key=int):
        a, b = left.get(key), right.get(key)
        same = (
            a is not None
            and b is not None
            and int(a[2]) == int(b[2])
            and _close(float(a[0]), float(b[0]))
            and _close(float(a[1]), float(b[1]))
        )
        if not same:
            found += 1
            if found <= 5:
                differences.append(f"{name}[{key}]: {a} != {b}")
    if found > 5:
        differences.append(f"{name}: 그 밖에 {found - 5} 칸 더 다름")


def compare(left: Mapping[str, Any], right: Mapping[str, Any]) -> list[str]:
    """[쓰임] 두 엔진의 결과를 비교해 다른 점 목록을 돌려준다(빈 목록이면 같음).
    [이유] '어디가 얼마나 다른가' 까지 보여야 버그인지 정의 차이인지 판단할 수 있다."""
    differences: list[str] = []
    for key in SCALAR_KEYS:
        if left.get(key) is None or right.get(key) is None:
            continue  # 한쪽이 그 값을 계산하지 않는 엔진 (연구 스크립트의 duplicate_rows)
        if left.get(key) != right.get(key):
            differences.append(f"{key}: {left.get(key)} != {right.get(key)}")
    left_gaps = [tuple(int(v) for v in gap) for gap in left.get("gaps", [])]
    right_gaps = [tuple(int(v) for v in gap) for gap in right.get("gaps", [])]
    if sorted(left_gaps) != sorted(right_gaps):
        differences.append(f"gaps: {len(left_gaps)} 곳 != {len(right_gaps)} 곳")
    _compare_buckets(
        "check_minutes", left.get("check_minutes", {}), right.get("check_minutes", {}), differences
    )
    _compare_buckets("windows", left.get("windows", {}), right.get("windows", {}), differences)
    return differences
