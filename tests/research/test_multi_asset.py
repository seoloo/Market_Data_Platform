from __future__ import annotations

import io
import zipfile

import pytest

from auto_trading.research.multi_asset import (
    bond_return,
    cap_weights,
    portfolio_vol,
    read_xlsx_sheet,
    sharpe_diff_z,
    stats,
    trend_fraction,
    turnover,
)


def test_bond_return_par_and_rate_moves() -> None:
    assert bond_return(5.0, 5.0) == pytest.approx(0.05 / 12, abs=1e-9)
    assert bond_return(5.0, 6.0) < 0 < bond_return(5.0, 4.0)
    # 1%p 상승이면 듀레이션 약 7.8 → 약 −7%
    assert bond_return(5.0, 6.0) == pytest.approx(-0.07, abs=0.01)


def test_trend_fraction_ensemble_and_lag() -> None:
    up = [0.01] * 12
    assert trend_fraction(up) == 1.0
    assert trend_fraction([-0.01] * 12) == 0.0
    mixed = [0.02] * 11 + [-0.01]  # 1 개월만 − → (−1 + 1 + 1 + 1)/4
    assert trend_fraction(mixed) == 0.5
    assert trend_fraction(up[:11]) is None
    # 한 달 늦추면 마지막 달을 보지 않는다
    assert trend_fraction([0.01] * 12 + [-0.5], lag=1) == 1.0


def test_cap_weights_scales_down_only_when_needed() -> None:
    hist = [[0.05, -0.05], [-0.05, 0.05]] * 10
    w = [0.5, 0.5]
    assert portfolio_vol(w, hist) == pytest.approx(0.0, abs=1e-12)
    wild = [[0.2], [-0.2]] * 10
    capped = cap_weights([1.0], wild, 0.12)
    assert capped[0] < 1.0 and portfolio_vol(capped, wild) == pytest.approx(0.12, rel=1e-6)
    assert cap_weights([0.1], wild, 2.0) == [0.1]


def test_stats_and_sharpe_test() -> None:
    r = [0.01, -0.005] * 60
    rf = [0.001] * 120
    s = stats(r, rf)
    assert s.months == 120 and s.mdd == pytest.approx(0.005)
    d, z = sharpe_diff_z(r, r, rf)
    assert d == 0 and z == 0
    better = [x + 0.002 for x in r]
    d2, z2 = sharpe_diff_z(better, r, rf)
    assert d2 > 0 and z2 > 0


def test_turnover_and_xlsx_reader() -> None:
    assert turnover({"a": 0.5}, {"a": 0.2, "b": 0.3}) == pytest.approx(0.6)
    sheet = (
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
        '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="C1"><v>2.5</v></c></row>'
        "</sheetData></worksheet>"
    )
    wb = (
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="S" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    rels = (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>'
    )
    sst = (
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<si><t>Gold</t></si></sst>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/_rels/workbook.xml.rels", rels)
        z.writestr("xl/worksheets/sheet1.xml", sheet)
        z.writestr("xl/sharedStrings.xml", sst)
    assert read_xlsx_sheet(buf.getvalue(), "S") == [["Gold", "", "2.5"]]
