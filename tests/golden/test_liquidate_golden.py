"""리팩토링 4 단계 전후 비교 기준 — '전부 정리' 실행기.

[쓰임] Deneb 만 · Vega 만 · 자동 적립 만 · 겹쳐 시작을 가짜 거래소로 돌려 정지 파일 · 매수 취소 ·
       업비트 즉시 매도 · 토스 장 열린 뒤 매도 · 장부 반영 · 알림 · 정지 풀기를 기준 파일과
       글자까지 비교한다 (흐름은 tests/ops/test_liquidate_script.py).
[이유] 4 단계에서 ``scripts/liquidate.py`` · ``ops/liquidate.py`` 를 손대서,
       바꾸기 전 코드로 기준을 찍어 둔다.
"""

from __future__ import annotations

import itertools
import json
import re
from decimal import Decimal
from typing import Any

import pytest

from scripts import liquidate as liq
from tests.golden.test_live_golden import _check_golden, _json_text
from tests.ops.test_liquidate_script import FakeToss, FakeUpbit, _St
from tests.ops.test_liquidate_script import env as env  # 가짜 거래소 준비 (pytest fixture)


def _jobs_text() -> str:
    rows = []
    for job in liq.STORE.jobs():
        row = json.dumps(job.to_dict(), ensure_ascii=False, sort_keys=True)
        rows.append(re.sub(r'"(\w+_ms|at)": \d+', r'"\1": <NOW>', row))
    return "\n".join(rows)


def test_golden_liquidate_flows(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    tmp = env["tmp"]
    # 시계 고정 — 부를 때마다 61 초씩 간다. 작업 번호가 '시작한 초' 라 실제 시계로는 같은 초에
    # 시작한 두 작업이 겹쳐(앞 작업 기록을 덮어씀) 돌릴 때마다 결과가 달라졌다 (2026-10-21 발견).
    ticks = itertools.count()
    monkeypatch.setattr(liq, "_now_ms", lambda: 1_792_000_000_000 + next(ticks) * 61_000)
    out: list[str] = ["## Deneb 만 (장 닫힘)", liq.start("deneb", "텔레그램")]
    FakeToss.open_session = True
    try:
        liq.resume()
        FakeToss.orders["s0"] = _St("FILLED", Decimal(3), Decimal(81), Decimal("0.081"))
        FakeToss.orders["s1"] = _St("FILLED", Decimal(1), Decimal(27), Decimal("0.027"))
        liq.resume()
        liq.resume()
    finally:
        FakeToss.open_session = False
    out += ["## Deneb 장부", _json_text(env["m3"]), "## 토스 주문", repr(FakeToss.sent)]
    out += ["## 취소", repr(FakeToss.canceled)]
    out += ["## Vega 만", liq.start("vega", "오르빗"), repr(FakeUpbit.sent)]
    out += [liq.unhalt("vega", "텔레그램"), liq.unhalt("vega", "텔레그램")]
    out += ["## 자동 적립 만", liq.start("acc", "텔레그램")]
    out += ["## 겹쳐 시작", liq.start("deneb", "텔레그램")]
    out += ["## 작업", _jobs_text(), "## 알림", *env["sent"]]
    halts = sorted(path.name for path in tmp.glob("HALT*"))
    out += ["## 정지 파일", repr(halts)]
    # 작업 번호(시작 시각) · 작업 기록 시각만 실제 시계라 가린다
    text = re.sub(r"\d{8}T\d{6}", "<JOB>", "\n".join(out))
    text = re.sub(r"\d\d-\d\d \d\d:\d\d", "<MM-DD HH:MM>", text)
    _check_golden("liquidate", text, tmp)
