"""문서 120 확인 필터 — N 일 연속 켜져야 켜짐, 꺼짐은 바로."""

from __future__ import annotations

from scripts.run_qs_confirm import confirm


def test_confirm_needs_n_consecutive_on_days_and_turns_off_at_once() -> None:
    days = [f"d{i:02d}" for i in range(10)]
    raw = dict(
        zip(days, [None, True, True, False, True, True, True, True, False, True], strict=True)
    )
    assert confirm(days, raw, 1) == raw | {"d00": None}
    got = confirm(days, raw, 3)
    assert [got[d] for d in days] == [
        None,
        False,
        False,
        False,
        False,
        False,
        True,
        True,
        False,
        False,
    ]
