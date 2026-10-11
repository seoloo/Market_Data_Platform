# ruff: noqa: E501 — 한국어 설명이 길다
"""문서 216 합성 검산 — 체결 방향 · 10 초 경계 · id 빈 곳 · 과거 문턱 · 미래 정보 차단 · funding 부호 · 진입 시각."""

from __future__ import annotations

import zipfile
from array import array
from datetime import date
from pathlib import Path

from scripts.run_qb_dc_210 import WARM, Market
from scripts.run_qflow_216 import MIN, _ms, extract, extract_full
from scripts.run_qflow_216_stage1 import (
    COST,
    DAY,
    EVAL_START,
    N_MIN,
    Q15,
    event,
    pct,
    scores,
    signals,
    thresholds,
    window_imbalance,
)

W = _ms(date(2024, 3, 1)) + 15 * MIN  # 00:15:00


def _zip(tmp: Path, rows: list[tuple[int, float, int, str]], header: bool = True) -> Path:
    lines = (
        ["agg_trade_id,price,quantity,first_trade_id,last_trade_id,transact_time,is_buyer_maker"]
        if header
        else []
    )
    for i, q, ts, ibm in rows:
        lines.append(f"{i},100.0,{q},{i},{i},{ts},{ibm}")
    z = tmp / "X-aggTrades-2024-03.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("X-aggTrades-2024-03.csv", "\n".join(lines) + "\n")
    return z


def test_direction_and_10s_boundary(tmp_path: Path) -> None:
    rows = [
        (1, 5.0, W - 1, "false"),  # 창 밖 (앞)
        (2, 2.0, W, "false"),  # 공격 매수 (매수자 테이커)
        (3, 1.0, W + 9_999, "true"),  # 공격 매도 (매수자가 메이커)
        (4, 7.0, W + 10_000, "false"),  # 창 밖 (뒤) — [W, W+10초)
        (5, 1.0, W + 5 * MIN + 3, "true"),
    ]
    z = _zip(tmp_path, rows)
    for ex in (extract(z), extract_full(z)):
        assert ex.windows[W] == [2.0, 1.0, 2]
        assert ex.windows[W + 5 * MIN] == [0.0, 1.0, 1]
        assert not ex.gaps and ex.rows == 5 and ex.header


def test_id_gap_detected_and_marks_window(tmp_path: Path) -> None:
    rows = [(1, 1.0, W - 1000, "false"), (2, 1.0, W + 1000, "false"), (5, 1.0, W + 20_000, "true")]
    z = _zip(tmp_path, rows, header=False)
    ex = extract(z)
    assert ex.gaps == [(W + 1000, W + 20_000, 2)] and not ex.header
    have = bytearray(N_MIN)
    for k in range((W - WARM) // MIN - 10, (W - WARM) // MIN + 20):
        have[k] = 1
    agg = {"2024-03": {"first_id": 1, "last_id": 5, "gaps": ex.gaps, "windows": ex.windows}}
    wi, _why = window_imbalance(agg, have)
    assert wi[W] is None  # 빠진 체결이 W ~ W+10 초 안에 있었을 수 있다
    assert wi[W + 5 * MIN] == 0.0  # 완전 · 체결 없음 = 0
    assert wi[W - 5 * MIN] == 0.0
    assert wi[W - 3 * Q15] is None  # 1 분봉 없는 분은 불완전


def test_scores_use_only_completed_windows_and_past_klines() -> None:
    t = W + Q15 * 20
    wi: dict[int, float | None] = {t - k * Q15: 0.1 * (k % 3) for k in range(12)}
    wi[t + Q15] = 99.0  # 미래 창은 섞이면 안 됨
    have = bytearray(N_MIN)
    vol, tb = array("d", bytes(8 * N_MIN)), array("d", bytes(8 * N_MIN))
    i1 = (t - WARM) // MIN
    for i in range(i1 - 180, i1 + 5):
        have[i], vol[i], tb[i] = 1, 10.0, 7.0
    tb[i1] = 0.0  # 판단 분(T ~ T+1 분) 자료는 C 에 안 들어간다
    sc = scores(wi, have, vol, tb)
    assert abs(sc["A"][t] - sum(0.1 * (k % 3) for k in range(12)) / 12) < 1e-12  # type: ignore[operator]
    assert sc["A"][t - Q15] is None  # 12 개 중 하나라도 없으면 생략
    assert abs(sc["C"][t] - (2 * 7 - 10) / 10) < 1e-12  # type: ignore[operator]
    assert sc["B"].get(t + 5 * MIN) is None


def test_thresholds_use_previous_180_days_only() -> None:
    d0 = EVAL_START + 30 * DAY
    sc: dict[int, float | None] = {}
    for k in range(1, 181):
        for j in range(96):
            sc[d0 - k * DAY + j * Q15] = float(j)  # 0 ~ 95
    for j in range(96):
        sc[d0 + j * Q15] = 1000.0  # 당일 값은 당일 문턱에 들어가면 안 됨
    th = thresholds(sc)
    lo, hi = th[d0]  # type: ignore[misc]
    xs = sorted(float(j) for _ in range(180) for j in range(96))
    assert (lo, hi) == (pct(xs, 0.1), pct(xs, 0.9))
    assert th[d0 - DAY] is not None  # 179 일 × 96 = 90 % 이상 → 문턱 있음
    assert th[d0 - 20 * DAY] is None  # 160 일 × 96 < 90 % → 문턱 없음 → 거래 없음
    sig, _ = signals({d0 + 3 * Q15: hi + 1e-9, d0 + 4 * Q15: lo - 1e-9, d0 + 5 * Q15: hi}, th)
    assert sig == [(d0 + 3 * Q15, 1), (d0 + 4 * Q15, -1)]  # 초과 롱 · 미만 숏 · 같으면 없음


def _market(px_at: dict[int, float]) -> Market:
    o = array("d", [100.0]) * N_MIN
    for i, v in px_at.items():
        o[i] = v
    one = array("d", [1.0]) * N_MIN
    return Market(o, o, o, o, one, 0)


def test_event_entry_after_60s_and_funding_sign() -> None:
    t = W + Q15 * 40
    i = (t - WARM) // MIN
    m = _market({i: 50.0, i + 1: 100.0, i + 1 + 720: 110.0})  # T 분 가격 변화는 수익에 안 들어감
    f_ms = WARM + (i + 100) * MIN
    m.funding = [
        (f_ms, 0.001, 105.0),
        (WARM + (i + 1 + 720) * MIN, 0.5, 105.0),
    ]  # 청산 분 정산은 보유 밖
    ft = [f[0] for f in m.funding]
    lg = event(m, ft, t, 1, 1)
    sh = event(m, ft, t, -1, 1)
    assert lg is not None and sh is not None
    assert abs(lg["gross"] - 0.10) < 1e-12 and abs(sh["gross"] + 0.10) < 1e-12
    assert abs(lg["funding"] - 0.001 * 105 / 100) < 1e-12  # 롱은 양수 rate 를 냄
    assert abs(sh["funding"] + 0.001 * 105 / 100) < 1e-12  # 숏은 받음
    assert abs(lg["fee"] - COST * (1 + 1.10)) < 1e-12
    d5 = event(m, ft, t, 1, 5)
    assert d5 is not None and d5["p0"] == 100.0 and d5["p1"] == 100.0  # T+300초 진입은 T+5 분 시가
