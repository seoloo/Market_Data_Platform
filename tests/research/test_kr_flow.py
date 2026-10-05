from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest
import scripts.run_kr_flow as kf
from scripts.run_kr_flow import Flows, flow_picker, flow_ratio, flow_scores, load_flows
from scripts.run_lt_kr import UNKNOWN, KrPanel, simulate_kr


def _days(n: int) -> list[str]:
    return [(date(2008, 1, 1) + timedelta(days=i)).isoformat() for i in range(n)]


def _panel(days: list[str], syms: list[str], sector: dict[str, str]) -> KrPanel:
    close: dict[str, list[float | None]] = {
        s: [100 * (1.002**t) for t in range(len(days))] for s in syms
    }
    return KrPanel(
        days=days,
        close=close,
        value={s: [1e9] * len(days) for s in syms},
        sector=sector,
        listed_from=dict.fromkeys(syms, 0),
        last={s: len(days) - 1 for s in syms},
    )


def test_flow_ratio_is_net_buying_over_turnover_in_the_window() -> None:
    f = Flows(net={"A": [0.0, 10.0, -5.0, 20.0]}, value={"A": [0.0, 100.0, 100.0, 100.0]})
    assert flow_ratio(f, "A", 3, 2) == pytest.approx(15 / 200)
    assert flow_ratio(f, "A", 3, 5) is None  # 창이 자료보다 길다
    assert flow_ratio(f, "B", 3, 2) is None
    assert flow_ratio(Flows({"A": [1.0]}, {"A": [0.0]}), "A", 0, 1) is None  # 거래대금 0


def test_scores_average_percentiles_of_both_windows() -> None:
    n = 60
    syms = [f"S{i:02d}" for i in range(n)]
    # 20 일 비율과 60 일 비율의 순위가 반대인 두 종목을 넣는다
    net = {s: [float(i)] * 60 for i, s in enumerate(syms)}
    net["S00"] = [100.0] * 40 + [-100.0] * 20  # 60 일은 높고 20 일은 가장 낮다
    f = Flows(net, {s: [1.0] * 60 for s in syms})
    score = flow_scores(f, 59, syms)
    assert score["S59"] == pytest.approx(1.0)
    assert 0.0 < score["S00"] < 1.0
    assert flow_scores(f, 59, syms[:10]) == {}  # 50 개 미만이면 고르지 않는다


def test_picker_takes_top_sectors_then_top_stocks_and_skips_unknown_sector() -> None:
    days = _days(80)
    syms = [f"S{i:02d}" for i in range(60)]
    sectors = ["IT", "금융", "소재", "산업재", "헬스케어", "에너지"]
    sector = {s: sectors[i % 6] for i, s in enumerate(syms)}
    sector["S59"] = UNKNOWN
    net = {s: [float(i)] * 80 for i, s in enumerate(syms)}  # 번호가 클수록 많이 산다
    f = Flows(net, {s: [1.0] * 80 for s in syms})
    picked = [s for s, _ in flow_picker(f)(_panel(days, syms, sector), 79, syms)]
    assert "S59" not in picked
    assert len(picked) == 10
    assert {sector[s] for s in picked} <= {"IT", "금융", "소재", "산업재", "헬스케어", "에너지"}
    assert picked[0] == "S58"


def test_simulation_uses_the_flow_picker(tmp_path: Path) -> None:
    days = _days(320)
    syms = [f"S{i:02d}" for i in range(60)]
    sector = {s: ("IT" if i >= 50 else "금융") for i, s in enumerate(syms)}
    net = {s: [float(i)] * 320 for i, s in enumerate(syms)}
    f = Flows(net, {s: [1.0] * 320 for s in syms})
    run = simulate_kr(_panel(days, syms, sector), [True] * 320, flow_picker(f))
    assert set(run.leader_log["2008-06-30"]) == {f"S{i}" for i in range(50, 60)}


def test_load_flows_converts_million_won_and_aligns_days(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "AAA.csv").write_text(
        "date,frgn_ntby_tr_pbmn,orgn_ntby_tr_pbmn,prsn_ntby_tr_pbmn,acml_tr_pbmn\n"
        "20080102,3,-1,-2,5000000\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(kf, "FLOWS", tmp_path)
    days = _days(5)
    f = load_flows(_panel(days, ["AAA"], {"AAA": "IT"}))
    assert f.net["AAA"][1] == pytest.approx(2e6) and f.value["AAA"][1] == 5_000_000
    assert f.net["AAA"][0] == 0.0
