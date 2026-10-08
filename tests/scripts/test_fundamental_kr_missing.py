from __future__ import annotations

from scripts.fundamental_kr_missing import Obs, judge_period


def _o(month: str, code: str, ok: bool, cap: float, sector: str = "A") -> Obs:
    return Obs(month, code, ok, cap, 1, sector, "" if ok else "값 없음")


def test_low_bias_when_missing_looks_like_covered() -> None:
    obs = [_o("2020-01", f"c{k}", k % 10 != 0, 100.0 + k) for k in range(40)]
    r = judge_period(obs)
    assert r["B1"] and r["B2"] and r["B3"] and r["low_bias"]


def test_big_missing_fails_size_and_capweight() -> None:
    obs = [_o("2020-01", f"c{k}", True, 10.0) for k in range(9)]
    obs.append(_o("2020-01", "big", False, 1000.0))
    r = judge_period(obs)
    assert not r["B1"] and not r["B2"]


def test_sector_gap_flags_sector() -> None:
    obs = [_o("2020-01", f"a{k}", True, 10.0, "A") for k in range(18)]
    obs += [_o("2020-01", f"b{k}", k == 0, 10.0, "B") for k in range(4)]
    r = judge_period(obs)
    assert r["B3_bad_sectors"] == ["B"] and not r["B3"]
