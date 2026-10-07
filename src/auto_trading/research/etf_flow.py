"""IDEAS 23·24 — 미국 현물 BTC ETF 흐름 신호와 1분봉 체결 모형 (순수).

docs/research/160-btc-etf-flow-preregistration.md. I/O 는 ``scripts/run_etf_flow_160.py`` 가 한다.

* 23 ETF-NET-LONG: 그 세션 전체 ETF 순유입 합 ``F_t`` 가 양수이고 직전 60 개 세션 F 의
  80 백분위보다 크면 BTCUSDT 무기한 LONG.
* 24 ETF-SHORT-FLOW: ETF 별 직전 종가로 달러 가중한 FINRA 공매도 거래량 비율 ``S_t`` 에서
  직전 20 세션 평균을 뺀 ``A_t`` 가 양수이고 직전 60 세션 A 의 90 백분위보다 크면 SHORT.
* 백분위: 직전 w 개 값을 정렬한 ``v[ceil(q·w) − 1]`` 보다 **크면**(같으면 아님).
* 체결: 지정가 IOC — 한도 = 직전 1분봉 종가 × (1 ± 0.3%), 그 분의 저가(고가)가 한도에 닿으면
  체결가 = min(한도, 시가) (매도는 max). 진입은 처음 + 다시 3 번, 청산은 닿을 때까지 매분 다시.
* 보호: 진입가 ± 10% 거래소 STOP_MARKET (AGENTS 시장가 예외 ①). 닿은 분의 시가가 이미 넘었으면
  시가, 아니면 스톱 값, 여기에 불리하게 0.1% 미끄러짐.
"""

from __future__ import annotations

import html
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime

MINUTE_MS = 60_000
HOUR_MS = 3_600_000
ENTRY_PAD = 0.003
ENTRY_RETRIES = 3
STOP_PCT = 0.10
STOP_SLIP = 0.001


@dataclass(frozen=True, slots=True)
class FlowRow:
    day: date
    cells: Mapping[str, float | None]
    """ETF 별 순유입(US$ 백만). ``None`` = 표에 '-' (미보고 · 미상장 · 휴장)."""
    total: float


def parse_num(text: str) -> float | None:
    """Farside 칸 → 숫자. ``(95.1)`` = −95.1, ``-`` 나 빈칸 = None (순수)."""
    t = text.strip().replace(",", "")
    if t in ("", "-", "–"):
        return None
    neg = t.startswith("(") and t.endswith(")")
    v = float(t.strip("()"))
    return -v if neg else v


def parse_farside(page: str) -> list[FlowRow]:
    """Farside 표(전체 · 최근 페이지 모두) → 날짜 행. 머리줄의 ETF 열 순서를 따른다 (순수)."""
    header: list[str] | None = None
    out: list[FlowRow] = []
    for tr in re.findall(r"<tr.*?</tr>", page, re.S):
        cells = [
            html.unescape(re.sub(r"<[^>]+>", "", c)).strip()
            for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)
        ]
        if len(cells) > 3 and cells[1] == "IBIT":
            header = cells[1:-1]
            continue
        if header is None or not cells or not re.fullmatch(r"\d{1,2} \w{3} 20\d\d", cells[0]):
            continue
        vals = cells[1 : 1 + len(header)]
        total = parse_num(cells[len(header) + 1]) if len(cells) > len(header) + 1 else None
        out.append(
            FlowRow(
                day=datetime.strptime(cells[0], "%d %b %Y").replace(tzinfo=UTC).date(),
                cells={k: parse_num(v) for k, v in zip(header, vals, strict=True)},
                total=total if total is not None else 0.0,
            )
        )
    return out


def is_session(row: FlowRow) -> bool:
    """ETF 칸이 하나라도 숫자면 거래 세션. 모두 '-' 면 휴장 행 (순수)."""
    return any(v is not None for v in row.cells.values())


def complete_vs(row: FlowRow, final: FlowRow) -> bool:
    """최종본에서 숫자인 칸이 이 버전에서도 모두 숫자인가 — 그 날 값이 다 들어왔는가 (순수)."""
    return all(row.cells.get(k) is not None for k, v in final.cells.items() if v is not None)


def threshold(prev: Sequence[float], q: float) -> float:
    """직전 값들의 q 분위 문턱 ``sorted(prev)[ceil(q·w) − 1]`` (순수)."""
    s = sorted(prev)
    return s[max(0, math.ceil(q * len(s)) - 1)]


def signal_23(totals: Sequence[float], i: int, window: int = 60, q: float = 0.8) -> bool | None:
    """세션 i 의 23 신호. 직전 값이 window 개 미만이면 None (순수)."""
    if i < window:
        return None
    f = totals[i]
    return f > 0 and f > threshold(totals[i - window : i], q)


def short_ratio(
    rows: Sequence[tuple[str, float, float]], prev_close: Mapping[str, float]
) -> float | None:
    """(ETF, ShortVolume, TotalVolume) 행 → 달러 가중 공매도 비율.

    직전 종가가 없는 ETF 는 뺀다 (순수)."""
    num = den = 0.0
    for sym, sv, tv in rows:
        p = prev_close.get(sym)
        if p is None or p <= 0:
            continue
        num += sv * p
        den += tv * p
    return num / den if den > 0 else None


def abnormal(ratios: Sequence[float], base: int = 20) -> list[float | None]:
    """A_t = S_t − 직전 base 세션 S 평균. 앞 base 개는 None (순수)."""
    out: list[float | None] = []
    for i, s in enumerate(ratios):
        out.append(None if i < base else s - sum(ratios[i - base : i]) / base)
    return out


def signal_24(a: Sequence[float | None], i: int, window: int = 60, q: float = 0.9) -> bool | None:
    """세션 i 의 24 신호. 직전 A 가 window 개 모두 있어야 한다 (순수)."""
    if i < window or a[i] is None:
        return None
    prev = a[i - window : i]
    if any(x is None for x in prev):
        return None
    ai = a[i]
    assert ai is not None
    return ai > 0 and ai > threshold([x for x in prev if x is not None], q)


@dataclass(frozen=True, slots=True)
class Minutes:
    """1분봉 (분 단위 끊김 없음을 가정하지 않는다 — open_ms 로 찾는다)."""

    t: Sequence[int]
    o: Sequence[float]
    h: Sequence[float]
    low: Sequence[float]
    c: Sequence[float]


@dataclass(frozen=True, slots=True)
class Trade:
    side: int
    """+1 LONG, −1 SHORT."""
    fill_ms: int
    entry: float
    exit_ms: int
    exit: float
    stopped: bool


def _ioc(m: Minutes, k: int, side: int, pad: float) -> float | None:
    """k 번째 분에 지정가 IOC. 한도 = 직전 분 종가 × (1 + side·pad) (순수)."""
    if k <= 0 or k >= len(m.t) or m.t[k] - m.t[k - 1] != MINUTE_MS:
        return None
    lim = m.c[k - 1] * (1 + side * pad)
    if side > 0 and m.low[k] <= lim:
        return min(lim, m.o[k])
    if side < 0 and m.h[k] >= lim:
        return max(lim, m.o[k])
    return None


def simulate(m: Minutes, k0: int, side: int, hold_ms: int) -> Trade | None:
    """평가 분 k0(시가 시각 = 평가 시각)부터 진입 → hold_ms 뒤 청산.

    미체결 · 자료 끝 = None (순수)."""
    fill_k, entry = -1, math.nan
    for k in range(k0, min(k0 + 1 + ENTRY_RETRIES, len(m.t))):
        px = _ioc(m, k, side, ENTRY_PAD)
        if px is not None:
            fill_k, entry = k, px
            break
    if fill_k < 0:
        return None
    stop = entry * (1 - side * STOP_PCT)
    due = m.t[fill_k] + hold_ms
    k = fill_k + 1
    while k < len(m.t) and m.t[k] < due:
        if (side > 0 and m.low[k] <= stop) or (side < 0 and m.h[k] >= stop):
            gap = m.o[k] if (side > 0 and m.o[k] < stop) or (side < 0 and m.o[k] > stop) else stop
            return Trade(side, m.t[fill_k], entry, m.t[k], gap * (1 - side * STOP_SLIP), True)
        k += 1
    while k < len(m.t):
        px = _ioc(m, k, -side, ENTRY_PAD)
        if px is not None:
            return Trade(side, m.t[fill_k], entry, m.t[k], px, False)
        k += 1
    return None


def net_return(
    tr: Trade, funding: Sequence[tuple[int, float]], cost: float
) -> tuple[float, float, float]:
    """(순수익, 총수익, funding 비용). funding 은 (정산 ms, 요율) — 보유 중(fill < t ≤ exit) 정산만.
    LONG 은 양의 요율을 내고 SHORT 는 받는다. 비용은 편도 ``cost`` 를 진입 · 청산에 (순수)."""
    gross = tr.side * (tr.exit / tr.entry - 1)
    fund = tr.side * sum(r for t, r in funding if tr.fill_ms < t <= tr.exit_ms)
    return gross - 2 * cost - fund, gross, fund
