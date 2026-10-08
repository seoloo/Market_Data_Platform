"""문서 176 — 여러 자산 추세 추종 (A · B · C) 의 순수 계산.

엑셀 읽기 · 채권 수익 · 신호 · 비중 · 성과 · 샤프 차이 검정.

* 신호: 1 · 3 · 6 · 12 개월 누적 수익 부호의 평균 s(−1 ~ 1). 롱만이므로 보유 비율 f = max(s, 0).
* 월평균 가격 자료(World Bank 금 · 원자재)는 월말 정보가 섞이지 않게 신호를 한 달 더 늦춘다.
* 계좌 전체 위험 상한: 직전 36 개월(최소 12) 자산 공분산으로 목표 비중의 연 변동성을 어림해
  상한을 넘으면 위험 자산 비중 전체를 줄인다(86 의 교훈 — 종목별 변동성 목표를 쓰지 않는다).
"""

from __future__ import annotations

import io
import math
import re
import statistics
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_RNS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
LOOKBACKS_M = (1, 3, 6, 12)


def _col_index(ref: str) -> int:
    letters = re.match(r"[A-Z]+", ref)
    assert letters is not None
    total = 0
    for k, ch in enumerate(reversed(letters.group())):
        total += (ord(ch) - 64) * 26**k
    return total - 1


def read_xlsx_sheet(data: bytes, name: str) -> list[list[str]]:
    """XLSX 바이트 → 이름이 ``name`` 인 시트의 칸 문자열 (순수, 표준 라이브러리만)."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(_NS + "si"):
                shared.append("".join(t.text or "" for t in si.iter(_NS + "t")))
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rid = next(s.get(_RNS + "id") for s in wb.iter(_NS + "sheet") if s.get("name") == name)
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        target = next(r.get("Target") for r in rels if r.get("Id") == rid)
        assert target is not None
        sheet = ET.fromstring(z.read("xl/" + target.lstrip("/").removeprefix("xl/")))
    rows: list[list[str]] = []
    for row in sheet.iter(_NS + "row"):
        vals: dict[int, str] = {}
        for c in row.iter(_NS + "c"):
            ref = c.get("r") or "A1"
            v = c.find(_NS + "v")
            if v is None:
                inline = c.find(_NS + "is")
                txt = (
                    "".join(x.text or "" for x in inline.iter(_NS + "t"))
                    if inline is not None
                    else ""
                )
            else:
                txt = shared[int(v.text or 0)] if c.get("t") == "s" else (v.text or "")
            vals[_col_index(ref)] = txt
        width = max(vals, default=-1) + 1
        rows.append([vals.get(i, "") for i in range(width)])
    return rows


def bond_return(y_prev: float, y_now: float) -> float:
    """10 년 만기 액면 채권 한 달 총수익 (순수).

    쿠폰 = 지난달 말 수익률(반년 이자), 이번 달 말 수익률로 다시 매긴다.

    수익률은 % 단위. 한 달 경과로 만기가 줄어드는 효과는 무시한다(교과서 근사)."""
    c, y = y_prev / 100, y_now / 100
    if y <= 1e-9:  # 수익률 0: 할인 없이 쿠폰 20 번 + 원금
        price = 1 + c * 10
    else:
        h = y / 2
        price = c / 2 * (1 - (1 + h) ** -20) / h + (1 + h) ** -20
    return price - 1 + c / 12


def trend_fraction(returns: Sequence[float], lag: int = 0) -> float | None:
    """월 수익 목록(오래된 → 최근)의 마지막(또는 ``lag`` 달 앞)까지로 보유 비율 max(s, 0) (순수).

    이력이 12 + lag 개월보다 짧으면 None."""
    end = len(returns) - lag
    if end < max(LOOKBACKS_M):
        return None
    signs = []
    for k in LOOKBACKS_M:
        cum = math.prod(1 + r for r in returns[end - k : end]) - 1
        signs.append(1.0 if cum > 0 else -1.0)
    return max(statistics.fmean(signs), 0.0)


def portfolio_vol(weights: Sequence[float], history: Sequence[Sequence[float]]) -> float:
    """자산별 월 수익 이력(행 = 달, 열 = 자산)으로 비중의 연 변동성 (순수)."""
    n = len(weights)
    t = len(history)
    if t < 2:
        return 0.0
    means = [statistics.fmean(row[i] for row in history) for i in range(n)]
    var = 0.0
    for i in range(n):
        for j in range(n):
            cov = sum((row[i] - means[i]) * (row[j] - means[j]) for row in history) / (t - 1)
            var += weights[i] * weights[j] * cov
    return math.sqrt(max(var, 0.0) * 12)


def cap_weights(
    weights: Sequence[float], history: Sequence[Sequence[float]], cap: float
) -> list[float]:
    """연 변동성이 ``cap`` 을 넘으면 모든 위험 비중을 같은 비율로 줄인다 (순수)."""
    vol = portfolio_vol(weights, history)
    scale = min(1.0, cap / vol) if vol > 0 else 1.0
    return [w * scale for w in weights]


@dataclass(frozen=True, slots=True)
class Stats:
    months: int
    cagr: float
    vol: float
    sharpe: float
    mdd: float
    worst12: float


def stats(returns: Sequence[float], rf: Sequence[float]) -> Stats:
    """연 수익 · 연 변동성 · 샤프(초과) · 최대 낙폭 · 최악 12 개월 (순수).

    월 수익과 월 무위험 수익으로 계산한다."""
    eq, peak, mdd = 1.0, 1.0, 0.0
    path = [1.0]
    for r in returns:
        eq *= 1 + r
        path.append(eq)
        peak = max(peak, eq)
        mdd = max(mdd, 1 - eq / peak)
    ex = [r - f for r, f in zip(returns, rf, strict=True)]
    sd = statistics.pstdev(ex) * math.sqrt(12)
    worst = min((path[i + 12] / path[i] - 1 for i in range(len(path) - 12)), default=0.0)
    years = len(returns) / 12
    return Stats(
        months=len(returns),
        cagr=eq ** (1 / years) - 1 if years > 0 else 0.0,
        vol=statistics.pstdev(returns) * math.sqrt(12),
        sharpe=statistics.fmean(ex) * 12 / sd if sd > 0 else 0.0,
        mdd=mdd,
        worst12=worst,
    )


def sharpe_diff_z(
    a: Sequence[float], b: Sequence[float], rf: Sequence[float]
) -> tuple[float, float]:
    """두 전략 월 초과수익의 샤프 차이(월 단위)와 Jobson–Korkie(Memmel 보정) z (순수)."""
    ea = [x - f for x, f in zip(a, rf, strict=True)]
    eb = [x - f for x, f in zip(b, rf, strict=True)]
    t = len(ea)
    ma, mb = statistics.fmean(ea), statistics.fmean(eb)
    sa, sb = statistics.pstdev(ea), statistics.pstdev(eb)
    if sa == 0 or sb == 0:
        return 0.0, 0.0
    rho = sum((x - ma) * (y - mb) for x, y in zip(ea, eb, strict=True)) / (t * sa * sb)
    sha, shb = ma / sa, mb / sb
    var = (2 * (1 - rho) + 0.5 * (sha**2 + shb**2 - 2 * sha * shb * rho**2)) / t
    diff = sha - shb
    return diff, diff / math.sqrt(var) if var > 0 else 0.0


def turnover(old: Mapping[str, float], new: Mapping[str, float]) -> float:
    """사고판 금액 합 Σ|새 − 옛| (비용 = 이것 × 편도 비용률) (순수)."""
    keys = set(old) | set(new)
    return sum(abs(new.get(k, 0.0) - old.get(k, 0.0)) for k in keys)
