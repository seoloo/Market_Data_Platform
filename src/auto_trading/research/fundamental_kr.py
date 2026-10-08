"""한국 실적 개선형 주도주 — Open DART 재무제표를 '그때 알 수 있던' 분기 계열로 (순수 함수).

초안: docs/research/FUNDAMENTAL_KR_PREREGISTRATION_DRAFT.md §3·§4, 관문: FUNDAMENTAL_KR_GATES.md.
SEC 용 :mod:`fundamental_pit` 과 필드 뜻이 달라(누적·3 개월 칸, 한 버전만 주는 API) 따로 둔다.

* 보고서 코드 11013(1분기) · 11012(반기) · 11014(3분기) · 11011(사업보고서).
* 매출·영업이익: 1~3분기 보고서의 3 개월 값, 4분기 = 1 년 − 3분기 누적. 영업현금흐름은
  누적뿐이라 앞 보고서 누적을 뺀다.
* 버전: 보고서마다 (가용 거래일, 값) 버전들을 두고, 월말 t 에는 **그때 보이던 버전**만 쓴다
  (:func:`snapshot`). 가용 = 그 버전 접수일 다음 거래일 종가 뒤. 값을 모르는 버전(정정 전 원본
  XBRL 이 없음)은 None → 그 분기를 쓰는 계산은 결측.
* 한 분기 값이 두 보고서로 만들어지면(4분기 · 현금흐름) 두 보고서의 그때 버전을 함께 쓴다.
* 연결(CFS)·별도(OFS) 를 한 계산 창에 섞지 않는다. 0 이하 매출 분모는 결측.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

__all__ = [
    "CODES",
    "MEASURES",
    "Inputs",
    "QuarterValue",
    "ReportData",
    "ReportHistory",
    "inputs_at",
    "parse_report",
    "parse_xbrl",
    "quarter_index",
    "quarters",
    "snapshot",
]

MEASURES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "revenue": (("ifrs-full_Revenue", "ifrs_Revenue"), ("매출액", "영업수익", "수익(매출액)")),
    "operating_income": (("dart_OperatingIncomeLoss",), ("영업이익", "영업이익(손실)")),
    "operating_cf": (
        (
            "ifrs-full_CashFlowsFromUsedInOperatingActivities",
            "ifrs_CashFlowsFromUsedInOperatingActivities",
        ),
        (
            "영업활동현금흐름",
            "영업활동으로인한현금흐름",
            "영업활동으로 인한 현금흐름",
            # 2026-10-09 커버리지 진단(수익 열람 전): 2023 보고서 일부가 합계 줄을 표준 코드 없이
            # 아래 이름으로 낸다(삼성전기 · 이마트 · 풍산 등). '영업활동에서 창출된 현금'(이자 ·
            # 법인세 전)은 다른 항목이라 넣지 않는다.
            "영업활동순현금흐름",
            "영업활동순현금흐름 합계",
            "영업활동현금흐름 합계",
            "영업활동으로 인한 순현금흐름",
        ),
    ),
}
"""경로 B 감사(``scripts.audit_dart_feasibility.MEASURES``)와 같은 표 — 결과 전에 고정.
영업현금흐름 이름 넷은 2026-10-09 전체 자료 커버리지 진단으로 더했다(전략 수익 보기 전)."""

_SHEETS = {"revenue": ("IS", "CIS"), "operating_income": ("IS", "CIS"), "operating_cf": ("CF",)}
CODES = ("11013", "11012", "11014", "11011")
"""회계연도 안의 순서: 1분기 · 반기 · 3분기 · 사업보고서."""


def _norm(name: str) -> str:
    return re.sub(r"\s+", "", name)


def _num(text: object) -> float | None:
    s = str(text or "").replace(",", "").strip()
    if not s or s in ("-", "None"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _pick(rows: Sequence[Mapping[str, Any]], key: str) -> Mapping[str, Any] | None:
    """계정 ID 가 맞는 행(표 순서상 첫 줄) → 없으면 이름이 정확히 맞는 행."""
    ids, names = MEASURES[key]
    wanted = {_norm(n) for n in names}
    sheets = _SHEETS[key]
    cand = [r for r in rows if str(r.get("sj_div")) in sheets]

    def order(r: Mapping[str, Any]) -> tuple[int, int]:
        o = str(r.get("ord") or "9999")
        return (sheets.index(str(r.get("sj_div"))), int(o) if o.isdigit() else 9999)

    by_id = sorted((r for r in cand if str(r.get("account_id")) in ids), key=order)
    if by_id:
        return by_id[0]
    by_name = sorted((r for r in cand if _norm(str(r.get("account_nm", ""))) in wanted), key=order)
    return by_name[0] if by_name else None


@dataclass(frozen=True, slots=True)
class ReportData:
    """보고서 한 버전에서 뽑은 값. 금액은 원."""

    code: str
    basis: str
    """"CFS" 또는 "OFS"."""
    rev_q: float | None
    rev_cum: float | None
    oi_q: float | None
    oi_cum: float | None
    cf_cum: float | None
    rcept_no: str


def parse_report(
    rows: Sequence[Mapping[str, Any]], code: str, basis: str, *, prior: bool = False
) -> ReportData | None:
    """``fnlttSinglAcntAll`` 의 ``list`` → 세 측정치. 원화가 아니면 None.

    ``prior`` 이면 같은 보고서의 **전년 같은 기간** 칸(``frmtrm_q_amount`` · ``frmtrm_add_amount``
    · ``frmtrm_amount``)을 읽는다 — DART 재무 API 에 없는 해(2015 년 1~3분기)를 다음 해
    보고서로 채우는 용도. 그 값은 다음 해 보고서가 가용해진 뒤에만 쓴다."""
    if not rows:
        return None
    if any(str(r.get("currency") or "KRW") != "KRW" for r in rows):
        return None
    rev, oi, cf = (_pick(rows, k) for k in ("revenue", "operating_income", "operating_cf"))
    q_key, cum_key = (
        ("frmtrm_q_amount", "frmtrm_add_amount")
        if prior
        else ("thstrm_amount", "thstrm_add_amount")
    )
    if prior and code == "11011":
        q_key = "frmtrm_amount"

    def q_and_cum(r: Mapping[str, Any] | None) -> tuple[float | None, float | None]:
        if r is None:
            return None, None
        q = _num(r.get(q_key))
        cum = q if code in ("11013", "11011") else _num(r.get(cum_key))
        return q, cum

    def cf_value(r: Mapping[str, Any] | None) -> float | None:
        if r is None:
            return None
        if not prior:
            return _num(r.get("thstrm_amount"))
        return _num(r.get("frmtrm_amount") or r.get("frmtrm_q_amount"))

    rq, rc = q_and_cum(rev)
    oq, oc = q_and_cum(oi)
    return ReportData(code, basis, rq, rc, oq, oc, cf_value(cf), str(rows[0].get("rcept_no", "")))


def quarter_index(year: int, month: int) -> int:
    """기간 끝 달 → 달력 분기 번호 (연 × 4 + 0..3). 결산월이 12 월이 아니어도 같은 축."""
    return year * 4 + (month - 1) // 3


@dataclass(frozen=True, slots=True)
class QuarterValue:
    qi: int
    """기간 끝 달력 분기 번호."""
    end: str
    """기간 끝 달 'YYYY-MM'."""
    basis: str
    revenue: float | None
    op_income: float | None
    op_cf: float | None
    unknown: bool = False
    """재료 보고서 중 그때 보이던 버전의 값을 모르는 것이 있다(정정 전 원본 미복원)."""


@dataclass(frozen=True, slots=True)
class ReportHistory:
    """보고서 하나의 버전들: (가용 거래일 색인, 값 — 모르면 None), 가용 순."""

    end: str
    versions: tuple[tuple[int, ReportData | None], ...]


def snapshot(
    hist: Mapping[int, Mapping[str, ReportHistory]], t: int
) -> dict[int, dict[str, tuple[ReportData | None, str]]]:
    """거래일 t 종가 뒤에 보이던 버전만 — 아직 공시 안 된 보고서는 빠진다."""
    out: dict[int, dict[str, tuple[ReportData | None, str]]] = {}
    for fy, reps in hist.items():
        for code, h in reps.items():
            seen = [d for at, d in h.versions if at <= t]
            if seen:
                out.setdefault(fy, {})[code] = (seen[-1], h.end)
    return out


def _sub(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None else a - b


def quarters(
    year_reports: Mapping[int, Mapping[str, tuple[ReportData | None, str]]],
) -> tuple[list[QuarterValue], list[str]]:
    """회계연도 → {보고서 코드: (그때 보이던 값 또는 None, 기간 끝 'YYYY-MM')} → 분기 값들과
    버린 이유. 한 회계연도 보고서들의 기간 끝이 연달아 이어지지 않으면 그 해를 버린다."""
    out: list[QuarterValue] = []
    why: list[str] = []
    for fy in sorted(year_reports):
        reps = year_reports[fy]
        idx = {c: quarter_index(int(reps[c][1][:4]), int(reps[c][1][5:7])) for c in reps}
        first = next(idx[c] - k for k, c in enumerate(CODES) if c in idx)
        if any(idx[c] != first + k for k, c in enumerate(CODES) if c in idx):
            why.append(f"{fy}: 기간 끝이 이어지지 않음")
            continue
        for k, c in enumerate(CODES):
            if c not in reps:
                continue
            data, end = reps[c]
            qi = quarter_index(int(end[:4]), int(end[5:7]))
            if data is None:
                out.append(QuarterValue(qi, end, "?", None, None, None, unknown=True))
                continue
            before = reps.get(CODES[k - 1]) if k > 0 else None
            prev = before[0] if before is not None else None
            unknown = before is not None and prev is None
            same = prev is not None and prev.basis == data.basis
            rev, oi = data.rev_q, data.oi_q
            if c == "11011":
                rev = _sub(data.rev_cum, prev.rev_cum) if same and prev else None
                oi = _sub(data.oi_cum, prev.oi_cum) if same and prev else None
            elif c != "11013" and (rev is None or oi is None) and same and prev:
                rev = rev if rev is not None else _sub(data.rev_cum, prev.rev_cum)
                oi = oi if oi is not None else _sub(data.oi_cum, prev.oi_cum)
            if c == "11013":
                cf = data.cf_cum
            else:
                cf = _sub(data.cf_cum, prev.cf_cum) if same and prev else None
            out.append(QuarterValue(qi, end, data.basis, rev, oi, cf, unknown=unknown))
    out.sort(key=lambda q: q.qi)
    return out, why


@dataclass(frozen=True, slots=True)
class Inputs:
    """월말에 알 수 있던 최신 분기 q 의 TTM 값과 세 요소."""

    qi: int
    end: str
    g: float
    accel: float
    margin_chg: float
    cf_chg: float
    cf_ttm: float
    op_ttm: float


def inputs_at(qs: Sequence[QuarterValue]) -> tuple[Inputs | None, str]:
    """그 시점 스냅숏의 분기들 → 최신 분기와 앞 11 분기로 계산. 모두 값이 있고(정정 사이
    아님), 같은 기준이며, 매출 분모 > 0 이어야 한다."""
    if not qs:
        return None, "공시 없음"
    last = qs[-1]
    by_qi = {q.qi: q for q in qs}
    window = [by_qi.get(last.qi - k) for k in range(12)]
    if any(w is None for w in window):
        return None, "12 분기 모자람"
    ws = [w for w in window if w is not None]
    if any(w.unknown for w in ws):
        return None, "정정 사이(원본 미복원)"
    if len({w.basis for w in ws}) > 1:
        return None, "연결·별도 섞임"
    if any(w.revenue is None or w.op_income is None or w.op_cf is None for w in ws):
        return None, "값 없음"

    def ttm(off: int, attr: str) -> float:
        return sum(float(getattr(ws[off + k], attr)) for k in range(4))

    r0, r4, r8 = ttm(0, "revenue"), ttm(4, "revenue"), ttm(8, "revenue")
    if min(r0, r4, r8) <= 0:
        return None, "매출 0 이하"
    o0, o4 = ttm(0, "op_income"), ttm(4, "op_income")
    c0, c4 = ttm(0, "op_cf"), ttm(4, "op_cf")
    g0, g4 = r0 / r4 - 1, r4 / r8 - 1
    return (
        Inputs(last.qi, last.end, g0, g0 - g4, o0 / r0 - o4 / r4, c0 / r0 - c4 / r4, c0, o0),
        "",
    )


# ---------------------------------------------------------------- XBRL 원본

_STD: dict[str, tuple[str, tuple[str, ...]]] = {
    "Revenue": ("rev", ("ifrs", "ifrs-full")),
    "OperatingIncomeLoss": ("oi", ("dart",)),
    "CashFlowsFromUsedInOperatingActivities": ("cf", ("ifrs", "ifrs-full")),
}
"""표준 태그 이름 → (측정치, 허용 이름공간 끝 조각). 회사 확장(entity…) 은 쓰지 않는다."""


def _last_day(end: str) -> str:
    import calendar

    y, m = int(end[:4]), int(end[5:7])
    return f"{end}-{calendar.monthrange(y, m)[1]:02d}"


def parse_xbrl(instance: bytes, code: str, basis: str, end: str) -> ReportData | None:
    """XBRL 인스턴스 → 기간 끝이 ``end``('YYYY-MM') 인 세 측정치.

    차원이 연결/별도 하나뿐인 값만. 누적 = 그 끝에서 가장 긴 기간(372 일 이하), 3 개월 =
    길이 80~100 일. 표준 태그가 없으면 그 값은 None(결측)."""
    from datetime import date
    from xml.etree import ElementTree as ET

    try:
        root = ET.fromstring(instance)
    except ET.ParseError:
        return None
    member = ":ConsolidatedMember" if basis == "CFS" else ":SeparateMember"
    last_day = date.fromisoformat(_last_day(end))
    ctx: dict[str, int] = {}
    for c in root:
        if not c.tag.endswith("}context"):
            continue
        dims = [m.text or "" for m in c.iter() if m.tag.endswith("explicitMember")]
        if len(dims) != 1 or not dims[0].strip().endswith(member):
            continue
        s, e = c.find("{*}period/{*}startDate"), c.find("{*}period/{*}endDate")
        if s is None or e is None or not s.text or not e.text:
            continue
        if date.fromisoformat(e.text.strip()) != last_day:
            continue
        days = (last_day - date.fromisoformat(s.text.strip())).days + 1
        if days <= 372:
            ctx[str(c.get("id"))] = days
    vals: dict[str, dict[int, float]] = {"rev": {}, "oi": {}, "cf": {}}
    for f in root:
        if not f.tag.startswith("{"):
            continue
        ns, local = f.tag[1:].split("}", 1)
        hit = _STD.get(local)
        if hit is None or "entity" in ns or ns.rstrip("/").split("/")[-1] not in hit[1]:
            continue
        span = ctx.get(str(f.get("contextRef")))
        v = _num(f.text)
        if span is not None and v is not None:
            vals[hit[0]].setdefault(span, v)

    def cum(key: str) -> float | None:
        return vals[key][max(vals[key])] if vals[key] else None

    def three(key: str) -> float | None:
        q = [d for d in vals[key] if 80 <= d <= 100]
        return vals[key][q[0]] if q else None

    if code in ("11013", "11011"):
        rq, oq = cum("rev"), cum("oi")
    else:
        rq, oq = three("rev"), three("oi")
    return ReportData(code, basis, rq, cum("rev"), oq, cum("oi"), cum("cf"), "xbrl")
