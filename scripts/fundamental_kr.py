"""한국 실적 개선형 주도주 — DART 자료로 분기 계열을 만들고 월별 커버리지를 센다 (수익 없음).

사용 예::

    python -m scripts.fundamental_kr coverage   # 월말마다 종목군 · 계산 가능 수 · 못 한 이유
    python -m scripts.fundamental_kr tr         # KOSPI 총수익 대리 지수 만들기 + 해마다 배당 수익률

* 종목군(초안 §4): 월말에 그날 거래한 유가증권시장 보통주 중 직전 63 거래일 평균 거래대금
  상위 200 (141 의 수익률 자격·시장 필터는 쓰지 않는다).
* 금융 서식: DART 재무 API 가 모든 보고서에 '자료 없음(013)' 을 주는 회사(은행·보험·증권 —
  일반 지주회사는 정상 재무제표가 나온다). 비금융 분모에서 빼고 따로 센다 (관문 문서 §3).
* 버전: 재무 API 가 준 버전은 JSON, 그 앞 버전은 XBRL 원본(있으면), 그 뒤 버전은 API 값을
  이어 쓴다(늦은 값일 뿐 미래 정보 아님). 가용 = 접수일 다음 거래일 종가 뒤.
"""

from __future__ import annotations

import bisect
import json
import re
import sys
import zipfile
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from scripts.fetch_kr_dart import ROOT, report_versions
from scripts.run_lt_kr import KrPanel, build_panel

from auto_trading.research.fundamental_kr import (
    Inputs,
    QuarterValue,
    ReportData,
    ReportHistory,
    inputs_at,
    parse_report,
    parse_xbrl,
    quarters,
    snapshot,
)

REPO = Path(__file__).resolve().parent.parent
FIRST_SIGNAL, LAST_SIGNAL = "2018-01-01", "2026-08-31"
UNIVERSE, LIQ_DAYS = 200, 63
STALE_DAYS = 180

Hist = dict[int, dict[str, ReportHistory]]


def _iso(d: str) -> str:
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}"


def avail_index(days: Sequence[str], rcept_dt: str) -> int:
    """접수일(YYYYMMDD 또는 YYYY-MM-DD) 다음 거래일 색인 — 그날 종가 뒤부터 안다."""
    d = rcept_dt if "-" in rcept_dt else _iso(rcept_dt)
    return bisect.bisect_right(days, d)


def _period_end(base: str) -> str | None:
    m = re.search(r"\((\d{4})\.(\d{2})\)", base)
    return f"{m.group(1)}-{m.group(2)}" if m else None


def _shift_year(end: str, k: int) -> str:
    return f"{int(end[:4]) + k}{end[4:]}"


def _load(path: Path) -> list[dict[str, Any]] | None:
    """재무 API 응답의 행 목록 — 정상(000)이 아니면 None."""
    if not path.exists():
        return None
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if str(body.get("status")) != "000":
        return None
    return list(body.get("list") or [])


def _xbrl_instance(path: Path) -> bytes | None:
    if not path.exists():
        return None
    try:
        with zipfile.ZipFile(path) as zf:
            name = next((n for n in zf.namelist() if n.endswith(".xbrl")), None)
            return zf.read(name) if name else None
    except (zipfile.BadZipFile, OSError):
        return None


def _versions(
    code: str,
    rc: str,
    basis: str,
    end: str,
    vs: Sequence[Mapping[str, Any]],
    api_at: int,
    api_data: ReportData,
    days: Sequence[str],
    root: Path,
    why: Counter[str],
) -> ReportHistory:
    out: list[tuple[int, ReportData | None]] = []
    for j, v in enumerate(vs):
        at = avail_index(days, str(v.get("rcept_dt")))
        if j == api_at:
            out.append((at, api_data))
        elif j > api_at:
            out.append((at, api_data))
            why["API 뒤 정정 — API 값 이어 씀"] += 1
        else:
            inst = _xbrl_instance(root / "xbrl" / code / f"{v.get('rcept_no')}.zip")
            got = parse_xbrl(inst, rc, basis, end) if inst is not None else None
            if got is not None and got.rev_cum is not None:
                out.append((at, got))
                why["XBRL 원본 복원"] += 1
            else:
                out.append((at, None))
                why["정정 전 원본 모름"] += 1
    return ReportHistory(end, tuple(out))


def corp_history(
    code: str, days: Sequence[str], root: Path = ROOT
) -> tuple[Hist, Counter[str], bool]:
    """한 종목 → {회계연도: {보고서 코드: 버전들}}, 센 것, 재무 API 정상 응답이 하나라도 있었나."""
    why: Counter[str] = Counter()
    hist: Hist = defaultdict(dict)
    listing = root / "filings" / f"{code}.json"
    if not listing.exists():
        why["공시 목록 없음"] += 1
        return hist, why, False
    rows = json.loads(listing.read_text(encoding="utf-8")).get("list") or []
    by_rcept: dict[str, tuple[str, list[dict[str, Any]]]] = {}
    for base, vs in report_versions(rows).items():
        for v in vs:
            by_rcept[str(v.get("rcept_no"))] = (base, vs)
    folder = root / "fin" / code
    any_ok = False
    later: list[tuple[int, str, list[dict[str, Any]], str, str, list[dict[str, Any]], int]] = []
    for path in sorted(folder.glob("*-CFS.json")) if folder.exists() else []:
        year_s, rc, _ = path.stem.split("-")
        year = int(year_s)
        got, basis = _load(path), "CFS"
        if got is None:
            got, basis = _load(path.with_name(path.name.replace("-CFS", "-OFS"))), "OFS"
        if got is None:
            continue
        any_ok = True
        data = parse_report(got, rc, basis)
        if data is None:
            why["원화 아님·빈 응답"] += 1
            continue
        hit = by_rcept.get(data.rcept_no)
        if hit is None:
            why["접수번호가 공시 목록에 없음"] += 1
            continue
        base, vs = hit
        end = _period_end(base)
        if end is None:
            why["기간 끝 없음"] += 1
            continue
        api_at = next(i for i, v in enumerate(vs) if str(v.get("rcept_no")) == data.rcept_no)
        hist[year][rc] = _versions(code, rc, basis, end, vs, api_at, data, days, root, why)
        later.append((year, rc, got, basis, end, vs, api_at))
    # DART 재무 API 에 없는 해(2015 년 1~3분기)는 다음 해 같은 보고서의 전년 칸으로.
    for year, rc, got, basis, end, vs, api_at in later:
        if rc in hist.get(year - 1, {}):
            continue
        prev = parse_report(got, rc, basis, prior=True)
        if prev is None or prev.rev_q is None:
            continue
        hist[year - 1][rc] = _versions(
            code, rc, basis, _shift_year(end, -1), vs, api_at, prev, days, root, why
        )
        why["전년 칸으로 채움"] += 1
    return hist, why, any_ok


def month_ends(days: Sequence[str]) -> list[int]:
    out = [
        t
        for t in range(len(days) - 1)
        if days[t + 1][:7] != days[t][:7] and FIRST_SIGNAL <= days[t] <= LAST_SIGNAL
    ]
    if days[-1][:7] == LAST_SIGNAL[:7] and days[-1] <= LAST_SIGNAL:
        out.append(len(days) - 1)
    return out


def liquid_top(panel: KrPanel, t: int, size: int = UNIVERSE) -> list[str]:
    """그날 거래했고 상장 뒤인 보통주 중 직전 63 거래일 평균 거래대금 상위 ``size``."""
    lo = max(0, t - LIQ_DAYS + 1)
    ranked = sorted(
        (-sum(panel.value[s][lo : t + 1]) / (t + 1 - lo), s)
        for s, xs in panel.close.items()
        if xs[t] is not None and t >= panel.listed_from[s]
    )
    return [s for _, s in ranked[:size]]


def stale(day: str, end: str) -> bool:
    """최신 분기 끝(그 달 말일)에서 월말 결정일까지 180 일 넘었나."""
    y, m = int(end[:4]), int(end[5:7])
    last = date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)
    return (date.fromisoformat(day) - last).days > STALE_DAYS


def computable(
    panel: KrPanel, t: int, hists: Mapping[str, Hist], financial: set[str]
) -> tuple[dict[str, Inputs], Counter[str], int, int]:
    """월말 t: (계산 가능 종목 → 값, 못 한 이유, 비금융 종목군 수, 금융 서식 수)."""
    pool = liquid_top(panel, t)
    rest = [s for s in pool if s not in financial]
    ok: dict[str, Inputs] = {}
    why: Counter[str] = Counter()
    for s in rest:
        qs: list[QuarterValue] = []
        if s in hists:
            qs, _ = quarters(snapshot(hists[s], t))
        x, reason = inputs_at(qs)
        if x is None:
            why[reason] += 1
        elif stale(panel.days[t], x.end):
            why["180 일 넘게 묵음"] += 1
        else:
            ok[s] = x
    return ok, why, len(rest), len(pool) - len(rest)


def load_all(panel: KrPanel) -> tuple[dict[str, Hist], set[str], Counter[str]]:
    hists: dict[str, Hist] = {}
    financial: set[str] = set()
    why: Counter[str] = Counter()
    for code in sorted(panel.close):
        hist, w, any_ok = corp_history(code, panel.days)
        why.update(w)
        if (ROOT / "fin" / code).exists() and not any_ok:
            financial.add(code)
        hists[code] = hist
    return hists, financial, why


def coverage() -> dict[str, Any]:
    panel, _ = build_panel()
    hists, financial, report_why = load_all(panel)
    rows = []
    reasons: Counter[str] = Counter()
    for t in month_ends(panel.days):
        ok, why, n, nfin = computable(panel, t, hists, financial)
        reasons.update(why)
        rows.append(
            {
                "date": panel.days[t],
                "universe_nonfin": n,
                "financial_format": nfin,
                "computable": len(ok),
                "share": round(len(ok) / n, 3) if n else 0.0,
                "eligible_all_positive": sum(
                    1
                    for x in ok.values()
                    if x.g > 0
                    and x.accel > 0
                    and x.margin_chg > 0
                    and x.cf_chg > 0
                    and x.cf_ttm > 0
                ),
            }
        )
    return {
        "stocks_with_fin_folder": sum(1 for c in panel.close if (ROOT / "fin" / c).exists()),
        "stocks_total": len(panel.close),
        "financial_format": len(financial),
        "report_notes": dict(report_why.most_common()),
        "month_reasons": dict(reasons.most_common()),
        "months": rows,
    }


KR_STOCKS = REPO / "data" / "research" / "kr_stocks"
KRX = REPO / "data" / "research" / "krx"
TR_OUT = KR_STOCKS / "kospi_tr.csv"


def _krx_day(day: str) -> tuple[float, dict[str, float]] | None:
    """KRX 유가증권 일별 파일 → (시가총액 합, 종목 → 상장주식수). 없으면 None."""
    import csv
    import gzip
    import io

    path = KRX / "stk" / f"{day}.csv.gz"
    if not path.exists():
        return None
    text = gzip.decompress(path.read_bytes()).decode("utf-8")
    rows = list(csv.DictReader(io.StringIO(text)))
    cap = sum(float(r["MKTCAP"] or 0) for r in rows)
    return cap, {r["ISU_CD"]: float(r["LIST_SHRS"] or 0) for r in rows}


def build_tr() -> dict[str, Any]:
    """KOSPI 총수익 대리 지수 (관문 문서 §6 — 사용자 결정으로 K4 판정 기준)."""
    from auto_trading.research.kr_total_return import (
        business_days,
        cum_index,
        total_return_index,
    )

    lines = (KR_STOCKS / "kospi.csv").read_text(encoding="utf-8").splitlines()[1:]
    pairs = [(x.split(",")[0], float(x.split(",")[1])) for x in lines]
    pairs = [p for p in pairs if p[0] >= "20150101"]
    days = [d for d, _ in pairs]
    price = [v for _, v in pairs]
    biz = business_days(days)
    caps: list[float] = []
    shares: list[dict[str, float]] = []
    missing = 0
    for d in days:
        got = _krx_day(d)
        if got is None:
            missing += 1
            got = (caps[-1] if caps else 0.0, shares[-1] if shares else {})
        caps.append(got[0])
        shares.append(got[1])
    divs: dict[int, float] = defaultdict(float)
    events = no_shares = 0
    for path in sorted((KR_STOCKS / "dividends").glob("*.json")):
        code = path.stem
        for r in json.loads(path.read_text(encoding="utf-8")):
            dps = float(r.get("dps") or 0)
            if dps <= 0:
                continue
            ci = cum_index(days, biz, r["record_date"])
            if ci is None or ci + 1 >= len(days):
                continue
            n = shares[ci].get(code, 0.0)
            if n <= 0:
                no_shares += 1
                continue
            divs[ci + 1] += dps * n
            events += 1
    tr = total_return_index(price, divs, caps)
    TR_OUT.write_text(
        "date,price,tr\n"
        + "".join(f"{d},{p},{v:.8f}\n" for d, p, v in zip(days, price, tr, strict=True)),
        encoding="utf-8",
    )
    yearly: dict[str, dict[str, float]] = {}
    for y in sorted({d[:4] for d in days}):
        idx = [i for i, d in enumerate(days) if d[:4] == y]
        lo = max(idx[0] - 1, 0)
        dy = sum(divs.get(i, 0.0) / caps[i - 1] for i in idx if i > 0 and caps[i - 1] > 0)
        yearly[y] = {
            "price": round(price[idx[-1]] / price[lo] - 1, 4),
            "total": round(tr[idx[-1]] / tr[lo] - 1, 4),
            "dividend_yield": round(dy, 4),
        }
    return {
        "days": len(days),
        "krx_missing_days": missing,
        "dividend_events": events,
        "events_without_shares": no_shares,
        "yearly": yearly,
        "out": str(TR_OUT.relative_to(REPO)),
    }


def main(argv: list[str]) -> int:
    stages = {"coverage": coverage, "tr": build_tr}
    if len(argv) != 1 or argv[0] not in stages:
        raise SystemExit("usage: python -m scripts.fundamental_kr coverage|tr")
    sys.stdout.write(json.dumps(stages[argv[0]](), ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
