"""Research 281: immutable source parsing and pre-learning power boundaries."""

import io
import random
import zipfile

import pytest
from scripts.deneb_macro_281.data_audit import (
    assess,
    observation_bounds,
    parse_initial,
    vintage_dates,
)
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
