from __future__ import annotations

from scripts.audit_kr_flow_dataquality import clean_close


def test_clean_close_removes_only_the_impossible_jump() -> None:
    days = ["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07"]
    px: list[float | None] = [100.0, 101.0, 202.0, 204.0]  # 셋째 날 +100% (빠진 병합)
    out, fixes = clean_close(days, px)
    assert fixes == 1
    a, b, c, d = (float(x) for x in out if x is not None)
    assert c == 202.0 and d == 204.0  # 뒤 가격은 그대로
    assert abs(b / a - 1.01) < 1e-12  # 앞 수익은 그대로
    assert abs(c / b - 1) < 1e-12  # 점프만 0 이 된다


def test_clean_close_keeps_legal_moves_and_gaps() -> None:
    days = ["2014-01-02", "2014-01-03", "2014-01-06"]
    px: list[float | None] = [100.0, 114.0, None]  # 2015 전 제한폭 15% 안
    out, fixes = clean_close(days, px)
    assert fixes == 0 and out == px
