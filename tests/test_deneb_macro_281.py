"""Research 281: immutable source parsing and pre-learning power boundaries."""

import io
import random
import zipfile
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest
from scripts.deneb_macro_281.account import Book, Dial, simulate, tax
from scripts.deneb_macro_281.data_audit import (
    assess,
    observation_bounds,
    parse_initial,
    vintage_dates,
)
from scripts.deneb_macro_281.models import fit_transform, training_rows
from scripts.deneb_macro_281.panel import index_growth, parse_snapshot_zip
from scripts.deneb_macro_281.power_audit import (
    label_summary,
    load_prefix,
    long_run_sd,
    month_differences,
    power_row,
)


def zipped(text: str) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("obs.csv", text)
    return out.getvalue()


@pytest.mark.leakage
def test_only_vintage_selector_and_cutoff_dates() -> None:
    html = (
        '<select id="other"><option value="1900-01-01"></select>'
        '<select id="form_selected_vintage_dates"><option value="2026-08-07">'
        '<option value="2026-09-04"><option value="1960-03-15"></select>'
    )
    assert vintage_dates(html) == ["1960-03-15", "2026-08-07"]
    with pytest.raises(ValueError, match="selector missing"):
        vintage_dates("<html>rate limited</html>")


@pytest.mark.leakage
def test_original_release_date_not_observation_date_controls_availability() -> None:
    data = (
        "period_start_date,UNRATE,realtime_start_date\n"
        "2026-07-01,4.1,2026-08-07\n2026-08-01,4.2,2026-09-04\n"
    )
    rows = parse_initial(zipped(data), "UNRATE")
    assert len(rows) == 1
    assert rows[0]["lag_days"] == 37
    assert rows[0]["released"] == "2026-08-07"


def test_archive_backfill_is_not_timely_history() -> None:
    rows = [
        {"observed": "1960-01-01", "released": "2014-01-27", "value": 1, "lag_days": 19750},
        {"observed": "2014-01-26", "released": "2014-01-27", "value": 2, "lag_days": 1},
    ]
    result = assess(rows, 7)
    assert result["timely_rows"] == 1
    assert result["archive_backfill_rows"] == 1
    assert result["first_timely_release"] == "2014-01-27"


def test_source_bounds_clamped_to_observation_history_and_sealed_cutoff() -> None:
    html = (
        '<input id="form_obs_start_date" value="1982-01-04">'
        '<input id="form_obs_end_date" value="2026-10-16">'
    )
    assert observation_bounds(html) == ("1982-01-04", "2026-08-31")
    early = (
        '<input id="form_obs_start_date" value="1959-01-01">'
        '<input id="form_obs_end_date" value="2026-08-01">'
    )
    assert observation_bounds(early) == ("1960-01-01", "2026-08-01")
    with pytest.raises(ValueError, match="bounds missing"):
        observation_bounds("<html>not a download form</html>")


@pytest.mark.parametrize("raw", ["nan", "inf", "-inf", "1000000000000000"])
def test_non_finite_macro_values_rejected(raw: str) -> None:
    with pytest.raises(ValueError, match="range"):
        parse_initial(
            zipped(f"period_start_date,UNRATE,realtime_start_date\n2020-01-01,{raw},2020-02-07\n"),
            "UNRATE",
        )


def test_duplicate_initial_releases_are_not_silently_selected() -> None:
    with pytest.raises(ValueError, match="Duplicate"):
        parse_initial(
            zipped(
                "period_start_date,UNRATE,realtime_start_date\n"
                "2020-01-01,3.5,2020-02-07\n2020-01-01,3.6,2020-03-07\n"
            ),
            "UNRATE",
        )


@pytest.mark.leakage
def test_sealed_price_value_not_parsed(tmp_path) -> None:
    path = tmp_path / "price.csv"
    path.write_text(
        "date,adjclose\n2026-08-28,100\n2026-08-31,101\n2026-09-01,NOT_A_NUMBER\n", encoding="utf-8"
    )
    before = load_prefix(path, "2026-08-31")
    path.write_text(
        "date,adjclose\n2026-08-28,100\n2026-08-31,101\n2026-09-01,999999\n", encoding="utf-8"
    )
    assert load_prefix(path, "2026-08-31") == before
    assert len(before[0]) == 2


def test_duplicate_price_dates_rejected(tmp_path) -> None:
    path = tmp_path / "price.csv"
    path.write_text("date,adjclose\n2020-01-01,100\n2020-01-01,101\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Non-increasing"):
        load_prefix(path, "2020-12-31")


def test_proxy_repeatability_and_not_a_fit() -> None:
    days = [f"2020-01-{i:02d}" for i in range(1, 25)]
    a = month_differences(days, [0.01] * 24, 0.2, 126, random.Random(281))
    b = month_differences(days, [0.01] * 24, 0.2, 126, random.Random(281))
    assert a == b and len(a) == 1


def test_power_units_and_zero_samples_fail_closed() -> None:
    row = power_row(0.02, 12, 6)
    assert row["required_samples"] > 12
    assert row["annual_mde_pct_points"] > 3
    assert power_row(0.02, 0, 6)["status"] == "UNDETERMINED"
    with pytest.raises(ValueError, match="Not enough"):
        long_run_sd([1, 2, 3])


def test_overlapping_positive_labels_do_not_become_independent_crises() -> None:
    days = [f"2020-01-{i:02d}" for i in range(1, 8)]
    prices = dict(zip(days, [100, 100, 80, 80, 100, 100, 75], strict=True))
    result = label_summary(days, prices, [0, 1, 4], horizon=2)
    assert result["positive_months"] == 3
    assert result["overlapping_positive_window_groups"] == 2
    with pytest.raises(ValueError, match="Incomplete"):
        label_summary(days, prices, [5], horizon=2)


@pytest.mark.leakage
def test_train_labels_must_mature_strictly_before_fit_cutoff() -> None:
    rows: list[dict[str, Any]] = [
        {"label_end": "2016-12-27", "label": 1},
        {"label_end": "2016-12-28", "label": 0},
        {"label_end": "2017-01-01", "label": 1},
        {"label_end": None, "label": None},
    ]
    assert training_rows(rows, "2016-12-28") == rows[:1]
    rows[-1] = {"label_end": "2026-08-31", "label": 1}
    assert training_rows(rows, "2016-12-28") == rows[:1]


def test_same_vintage_index_rebasing_not_stitched() -> None:
    blob = zipped(
        "observation_date,INDPRO_20150116,INDPRO_20160115\n2014-01-01,100,200\n2014-04-01,101,202\n"
    )
    rows = parse_snapshot_zip(blob, "INDPRO")
    assert rows["2015-01-16"]["2014-04-01"] / rows["2015-01-16"]["2014-01-01"] == 1.01
    assert rows["2016-01-15"]["2014-04-01"] / rows["2016-01-15"]["2014-01-01"] == 1.01


def test_decimal_book_fees_and_cost_basis() -> None:
    b = Book(Decimal("0.0015"))
    b.buy("L", Decimal(10_000), Decimal(100), "2020-01-02", "TEST")
    assert b.qty["L"] == Decimal("99.85")
    proceeds = b.sell("L", b.qty["L"], Decimal(110), "2020-02-03", "TEST")
    assert proceeds == Decimal("10967.02475")
    assert b.gains["2020"] == Decimal("967.02475")
    assert b.fees == Decimal("31.47525")
    assert tax(Decimal(2_500_000)) == 0
    assert tax(Decimal(3_500_000)) == 220_000


def test_price_return_rearms_only_after_macro_clear() -> None:
    dial = Dial()
    high = {"probabilities": {"LR": 0.8}, "base": 0.1, "macro_missing": 0}
    low = {"probabilities": {"LR": 0.05}, "base": 0.1, "macro_missing": 0}
    assert dial.update(high, False) == "MACRO_ENTER"
    assert dial.multiplier == Decimal("0.5")
    assert dial.update(None, True) == "PRICE_RETURN"
    assert (dial.multiplier, dial.armed) == (Decimal(1), False)
    assert dial.update(high, True) is None
    dial.update(low, True)
    assert dial.armed
    assert dial.update(high, True) == "MACRO_ENTER"


def toy_data() -> dict[str, Any]:
    import math

    from scripts.run_brake_183 import signals

    dates = []
    day = date(2009, 12, 31)
    while day <= date(2013, 7, 31):
        if day.weekday() < 5:
            dates.append(day.isoformat())
        day += timedelta(days=1)
    lev = {d: 100 * math.exp(0.0008 * i + 0.35 * math.sin(i / 25)) for i, d in enumerate(dates)}
    div = {d: 100 * 1.0001**i for i, d in enumerate(dates)}
    qqq = {d: 100 * math.exp(0.0002 * i + 0.05 * math.sin(i / 35)) for i, d in enumerate(dates)}
    vol, trend = signals(dates, qqq)
    return {
        "days": dates,
        "lev": lev,
        "div": div,
        "qqq": qqq,
        "vol": vol,
        "trend": trend,
        "start": "2011-01-01",
    }


@pytest.mark.parametrize("monthly", [100_000, 10_000_000])
def test_baseline_matches_184_synthetic_golden_including_tax(monthly: int) -> None:
    from scripts.run_brake_184 import simulate as old_simulate

    data = toy_data()
    old = old_simulate(data, data["qqq"], ref="T", park="cash", monthly_krw=monthly)
    new = simulate(
        data, [], start="2011-01-01", end="2013-07-31", mode="NONE", constant=1, monthly_krw=monthly
    )
    assert new["nav_days"] == old["nav_days"]
    assert new["navs"] == pytest.approx(old["navs"], rel=1e-10)
    assert float(new["after_tax_hold_krw"]) == pytest.approx(old["after_tax_hold"], abs=0.001)
    assert float(new["legacy_after_tax_liquidation_krw"]) == pytest.approx(
        old["after_tax_liq"], abs=0.001
    )
    assert max(abs(Decimal(r["reconciliation_error"])) for r in new["ledger"]) < Decimal("0.000001")


def test_macro_cash_does_not_recursively_shrink_equity() -> None:
    days = ["2019-12-31", "2020-01-02", "2020-01-31", "2020-02-03", "2020-02-28", "2020-03-02"]
    data = {
        "days": days,
        "lev": dict.fromkeys(days, 100),
        "div": dict.fromkeys(days, 100),
        "vol": dict.fromkeys(days, 0.1),
        "trend": dict.fromkeys(days, True),
    }
    signal = {
        "execute": "2020-01-02",
        "probabilities": {"LR": 0.8},
        "base": 0.1,
        "macro_missing": 0,
    }
    result = simulate(
        data,
        [signal],
        start="2020-01-01",
        end="2020-03-02",
        mode="MACRO",
        constant=1,
        fee="0",
        monthly_krw=10_000,
    )
    assert [r["exposure"] for r in result["ledger"]] == pytest.approx([0.3] * 5)
    assert Decimal(result["ledger"][-1]["macro_cash"]) == 9_000


def test_incremental_contribution_ledger_reconciles() -> None:
    from scripts.deneb_macro_281.run import attribution

    data = toy_data()
    signal = {
        "execute": "2011-01-03",
        "probabilities": {"LR": 0.8},
        "base": 0.1,
        "macro_missing": 0,
    }
    a = simulate(data, [], start="2011-01-01", end="2013-07-31", mode="NONE", constant=1)
    c = simulate(data, [signal], start="2011-01-01", end="2013-07-31", mode="MACRO", constant=1)
    assert abs(Decimal(attribution(a, c)["reconciliation_error_krw"])) < Decimal("0.00001")


@pytest.mark.leakage
def test_preprocessing_is_fitted_only_on_training_values() -> None:
    train: list[list[float | None]] = [[1.0, None], [2.0, 5.0], [3.0, 5.0]]
    at, bt, meta = fit_transform(train, [[100_000, None]])
    at2, _, meta2 = fit_transform(train, [[-100_000, 900_000]])
    assert at == at2 and meta == meta2
    assert meta["median"] == [2.0, 5.0]
    assert all(v == v for v in bt[0])


@pytest.mark.leakage
def test_later_index_revisions_do_not_change_earlier_feature() -> None:
    vintages = {
        "2015-01-16": {"2014-09-01": 100.0, "2014-12-01": 101.0},
        "2016-01-15": {"2014-09-01": 200.0, "2014-12-01": 900.0},
    }
    before = index_growth(vintages, "2015-01-30", False)
    vintages["2016-01-15"]["2014-12-01"] = 900_000
    assert index_growth(vintages, "2015-01-30", False) == before
    assert before == pytest.approx(0.01)
    with pytest.raises(ValueError, match="newer"):
        parse_snapshot_zip(zipped("observation_date,INDPRO_20150116\n2016-01-01,100\n"), "INDPRO")
