from __future__ import annotations

import pytest

from auto_trading.research.fundamental_kr import (
    QuarterValue,
    ReportData,
    ReportHistory,
    inputs_at,
    parse_report,
    parse_xbrl,
    quarter_index,
    quarters,
    snapshot,
)


def _row(sj: str, aid: str, nm: str, **amounts: str) -> dict[str, str]:
    return {
        "sj_div": sj,
        "account_id": aid,
        "account_nm": nm,
        "ord": "1",
        "rcept_no": "R1",
        **amounts,
    }


def test_parse_report_current_and_prior_columns() -> None:
    rows = [
        _row("CIS", "ifrs_Revenue", "매출액", thstrm_amount="30", thstrm_add_amount="70",
             frmtrm_q_amount="20", frmtrm_add_amount="50"),
        _row("CIS", "-표준계정코드 미사용-", "영업 이익", thstrm_amount="3", thstrm_add_amount="7",
             frmtrm_q_amount="2", frmtrm_add_amount="5"),
        _row("CF", "ifrs-full_CashFlowsFromUsedInOperatingActivities", "영업활동현금흐름",
             thstrm_amount="9", frmtrm_q_amount="6"),
    ]  # fmt: skip
    now = parse_report(rows, "11012", "CFS")
    assert now is not None
    assert (now.rev_q, now.rev_cum, now.oi_q, now.oi_cum, now.cf_cum) == (30, 70, 3, 7, 9)
    before = parse_report(rows, "11012", "CFS", prior=True)
    assert before is not None
    assert (before.rev_q, before.rev_cum, before.oi_cum, before.cf_cum) == (20, 50, 5, 6)
    assert parse_report([{**rows[0], "currency": "USD"}], "11012", "CFS") is None


def _rd(code: str, rq: float, rc: float, cf: float, basis: str = "CFS") -> ReportData:
    return ReportData(code, basis, rq, rc, rq / 10, rc / 10, cf, "R")


def _year(y: int, scale: float = 1.0, basis: str = "CFS") -> dict[str, tuple[ReportData, str]]:
    return {
        "11013": (_rd("11013", 10 * scale, 10 * scale, 1 * scale, basis), f"{y}-03"),
        "11012": (_rd("11012", 10 * scale, 20 * scale, 2 * scale, basis), f"{y}-06"),
        "11014": (_rd("11014", 10 * scale, 30 * scale, 3 * scale, basis), f"{y}-09"),
        "11011": (_rd("11011", 40 * scale, 40 * scale, 4 * scale, basis), f"{y}-12"),
    }


def test_quarters_derive_q4_and_cash_flow_differences() -> None:
    qs, why = quarters({2020: _year(2020)})
    assert why == []
    assert [q.revenue for q in qs] == [10, 10, 10, 10]  # 4분기 = 40 − 누적 30
    assert [q.op_cf for q in qs] == [1, 1, 1, 1]
    assert qs[0].qi == quarter_index(2020, 3)


def test_quarters_break_on_basis_switch_and_gaps() -> None:
    year = _year(2020)
    year["11012"] = (_rd("11012", 10, 20, 2, "OFS"), "2020-06")
    qs, _ = quarters({2020: year})
    assert qs[1].op_cf is None and qs[2].op_cf is None  # 기준이 바뀌면 누적 빼기 안 함
    bad = _year(2020)
    bad["11014"] = (bad["11014"][0], "2020-12")
    assert quarters({2020: bad})[1] == ["2020: 기간 끝이 이어지지 않음"]


def test_snapshot_uses_only_versions_seen_by_t() -> None:
    orig = _rd("11011", 40, 40, 4)
    fixed = _rd("11011", 80, 80, 8)
    hist = {2020: {"11011": ReportHistory("2020-12", ((10, None), (20, fixed)))}}
    assert snapshot(hist, 5) == {}
    assert snapshot(hist, 10) == {2020: {"11011": (None, "2020-12")}}
    assert snapshot(hist, 20)[2020]["11011"][0] == fixed
    hist2 = {2020: {"11011": ReportHistory("2020-12", ((10, orig), (20, fixed)))}}
    assert snapshot(hist2, 15)[2020]["11011"][0] == orig  # 정정 전엔 원본 값


def test_unknown_original_blocks_the_window() -> None:
    hist = {
        y: {c: ReportHistory(e, ((0, d),)) for c, (d, e) in _year(y, 1 + (y - 2017) * 0.1).items()}
        for y in (2017, 2018, 2019)
    }
    t = 0
    x, why = inputs_at(quarters(snapshot(hist, t))[0])
    assert why == "" and x is not None
    assert x.g == pytest.approx(1.2 / 1.1 - 1)
    assert x.accel == pytest.approx((1.2 / 1.1 - 1) - (1.1 / 1.0 - 1))
    assert x.margin_chg == pytest.approx(0.0)
    hist[2018]["11012"] = ReportHistory("2018-06", ((0, None),))
    assert inputs_at(quarters(snapshot(hist, t))[0])[1] == "정정 사이(원본 미복원)"


def test_inputs_need_twelve_quarters() -> None:
    qs = [QuarterValue(quarter_index(2020, m), f"2020-{m:02d}", "CFS", 1, 1, 1) for m in (3, 6)]
    assert inputs_at(qs) == (None, "12 분기 모자람")


_XBRL = b"""<?xml version="1.0"?>
<xbrl xmlns="http://www.xbrl.org/2003/instance" xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
 xmlns:ifrs="http://xbrl.iasb.org/taxonomy/2010-04-30/ifrs"
 xmlns:dart="http://dart.fss.or.kr/taxonomy/2017-10-01/ifrs/dart"
 xmlns:ent="http://dart.fss.or.kr/taxonomy/2011-05-17/entity001">
 <context id="H"><entity/><period><startDate>2020-01-01</startDate><endDate>2020-06-30</endDate>
  </period><scenario>
  <xbrldi:explicitMember dimension="a">ifrs:ConsolidatedMember</xbrldi:explicitMember>
  </scenario></context>
 <context id="Q"><entity/><period><startDate>2020-04-01</startDate><endDate>2020-06-30</endDate>
  </period><scenario>
  <xbrldi:explicitMember dimension="a">ifrs:ConsolidatedMember</xbrldi:explicitMember>
  </scenario></context>
 <context id="SEG"><entity/><period><startDate>2020-01-01</startDate><endDate>2020-06-30</endDate>
  </period><scenario>
  <xbrldi:explicitMember dimension="a">ifrs:ConsolidatedMember</xbrldi:explicitMember>
  <xbrldi:explicitMember dimension="b">ent:SegA</xbrldi:explicitMember></scenario></context>
 <ifrs:Revenue contextRef="SEG">5</ifrs:Revenue>
 <ifrs:Revenue contextRef="H">200</ifrs:Revenue>
 <ifrs:Revenue contextRef="Q">110</ifrs:Revenue>
 <dart:OperatingIncomeLoss contextRef="H">20</dart:OperatingIncomeLoss>
 <dart:OperatingIncomeLoss contextRef="Q">12</dart:OperatingIncomeLoss>
 <ent:CashFlowsFromUsedInOperatingActivities contextRef="H">9
 </ent:CashFlowsFromUsedInOperatingActivities>
 <ifrs:CashFlowsFromUsedInOperatingActivities contextRef="H">30
 </ifrs:CashFlowsFromUsedInOperatingActivities>
</xbrl>"""


def test_parse_xbrl_picks_standard_tags_single_dimension_and_periods() -> None:
    got = parse_xbrl(_XBRL, "11012", "CFS", "2020-06")
    assert got is not None
    assert (got.rev_q, got.rev_cum, got.oi_q, got.oi_cum, got.cf_cum) == (110, 200, 12, 20, 30)
    sep = parse_xbrl(_XBRL, "11012", "OFS", "2020-06")
    assert sep is not None and sep.rev_cum is None  # 별도 값 없음
    assert parse_xbrl(b"not xml", "11012", "CFS", "2020-06") is None


def test_operating_cf_total_name_variants_without_standard_code() -> None:
    from auto_trading.research.fundamental_kr import _pick

    def rows(name: str) -> list[dict[str, str]]:
        return [
            {
                "sj_div": "CF",
                "account_id": "-표준계정코드 미사용-",
                "account_nm": "영업활동에서 창출된 현금",
                "ord": "1",
            },
            {"sj_div": "CF", "account_id": "-표준계정코드 미사용-", "account_nm": name, "ord": "2"},
        ]

    for name in ("영업활동 순현금흐름유입", "Ⅰ. 영업활동현금흐름", "영업활동으로부터의 순현금유입"):
        got = _pick(rows(name), "operating_cf")
        assert got is not None and got["account_nm"] == name
    assert _pick(rows("영업활동으로 인한 자산·부채의 변동"), "operating_cf") is None
