# ruff: noqa: E501 — 한국어 설명 · 표 문자열이 길다
"""문서 212 — 시장 공통 움직임(BTC beta)을 뺀 상대 과매도 회귀: 코인 롱 + BTC 숏 · 후향 파일럿. 주문 없음.

사용::

    python -m scripts.run_resid_rev_212 exinfo      # 거래소 정보(상품 구분 · 수량 단위) 저장
    python -m scripts.run_resid_rev_212 daily2019   # DB 일봉이 2020-01 부터인 종목의 2019-09 ~ 12 일봉
    python -m scripts.run_resid_rev_212 universe    # 월별 상위 20 (생존 편향 없음) → members.json
    python -m scripts.run_resid_rev_212 fetch       # 구성 종목 기간의 5분봉 · funding · mark 1시간봉
    python -m scripts.run_resid_rev_212 threshold   # ③ 비용 전 효과 · 비용 문턱 (문서 212 §6)
    python -m scripts.run_resid_rev_212 pilot       # ④ 실제 체결 · 위험 · funding 파일럿 (§5 · §7)

규칙은 문서 212 에 고정. 2026-09-01 이후 자료는 읽지 않는다.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import random
import sqlite3
import statistics
import sys
import urllib.error
import urllib.request
import zipfile
from collections import defaultdict
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any
from urllib.parse import quote

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "data" / "research" / "rr_212"
VISION = REPO / "data" / "external" / "binance_vision"
REPORT = REPO / "reports" / "resid_rev_212"
BASE = "https://data.binance.vision/data/futures/um/monthly"

DAY = 86_400_000
MIN = 60_000
M5 = 5 * MIN
END = int(datetime(2026, 9, 1, tzinfo=UTC).timestamp() * 1000)
EVAL_START = date(2020, 3, 1)
EVAL_END = date(2026, 8, 31)
TOP_N = 20
MIN_HISTORY_DAYS = 180
STABLE = {"USDC", "BUSD", "TUSD", "FDUSD", "USDP", "DAI", "USDE", "PYUSD", "EUR", "GBP", "AUD"}
ASSET_LINKED = {"PAXG", "XAUT", "WBTC", "WBETH", "BETH", "BNSOL"}


def _ms(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp() * 1000)


def _get(url: str) -> bytes | None:
    for _ in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "auto-trading-research"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                return bytes(resp.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
        except (urllib.error.URLError, TimeoutError):
            pass
    raise RuntimeError(f"받기 실패: {url}")


def _fetch(kind: str, symbol: str, month: str, interval: str = "") -> Path | None:
    """kind = klines · markPriceKlines · fundingRate. 체크섬 확인, 없으면(404) None."""
    if kind == "fundingRate":
        name = f"{symbol}-fundingRate-{month}.zip"
        url = f"{BASE}/fundingRate/{quote(symbol)}/{quote(name)}"
        target = VISION / "um_funding" / symbol / name
    else:
        name = f"{symbol}-{interval}-{month}.zip"
        url = (
            f"{BASE}/{kind}/{quote(symbol)}/{interval}/{quote(name)}"  # 영문 아닌 이름 코인도 있다
        )
        target = VISION / ("um" if kind == "klines" else "um_mark") / symbol / interval / name
    if target.is_file():
        return target
    chk = _get(url + ".CHECKSUM")
    if chk is None:
        return None
    payload = _get(url)
    if payload is None:
        return None
    if hashlib.sha256(payload).hexdigest() != chk.decode("utf-8").split()[0].lower():
        raise RuntimeError(f"체크섬 불일치: {name}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return target


# ------------------------------------------------------------------ 일봉 · 종목군


@dataclass(frozen=True, slots=True)
class DayBar:
    o: float
    h: float
    lo: float
    c: float
    qv: float


def load_daily() -> dict[str, dict[int, DayBar]]:
    """USD-M 일봉: DB(2020-01 ~) + 2019-09 ~ 12 공개 아카이브. 2026-09 이후 안 읽음."""
    db = sqlite3.connect(f"file:{REPO / 'data' / 'trading.db'}?mode=ro", uri=True)
    out: dict[str, dict[int, DayBar]] = defaultdict(dict)
    for s, t, o, h, lo, c, q in db.execute(
        "select symbol, open_ms, open, high, low, close, quote_volume from candles "
        "where exchange='binance_usdm' and timeframe='1d' and open_ms < ?",
        (END,),
    ):
        out[s][int(t)] = DayBar(float(o), float(h), float(lo), float(c), float(q))
    for z in (VISION / "um").glob("*/1d/*-1d-2019-*.zip"):
        s = z.parent.parent.name
        for row in _zip_rows(z):
            t = int(row[0])
            out[s].setdefault(
                t, DayBar(float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[7]))
            )
    return dict(out)


def _zip_rows(z: Path) -> Iterable[list[str]]:
    with zipfile.ZipFile(z) as zf:
        for row in csv.reader(io.TextIOWrapper(zf.open(zf.namelist()[0]), "utf-8")):
            if row and row[0].isdigit():
                yield row


def excluded(symbol: str, exinfo: dict[str, dict[str, Any]]) -> str | None:
    """종목군에서 뺄 이유 (없으면 None). 지금 거래소 정보에 없는(폐지) 종목은 코인으로 본다."""
    if symbol == "BTCUSDT":
        return "BTC(헤지 전용)"
    if not symbol.endswith("USDT"):
        return "USDT 아님"
    base = symbol[:-4]
    if base in STABLE:
        return "스테이블코인 · 법정화폐"
    if base in ASSET_LINKED:
        return "자산 연동"
    info = exinfo.get(symbol)
    if info is not None:
        if info.get("underlyingType") != "COIN":
            return f"기초자산 {info.get('underlyingType')}"
        if info.get("contractType") != "PERPETUAL":
            return f"계약 {info.get('contractType')}"
    return None


def month_starts() -> list[date]:
    out, y, m = [], EVAL_START.year, EVAL_START.month
    while (y, m) <= (EVAL_END.year, EVAL_END.month):
        out.append(date(y, m, 1))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def build_universe(
    daily: dict[str, dict[int, DayBar]], exinfo: dict[str, dict[str, Any]]
) -> dict[str, list[str]]:
    """매월 1일: 그 전 30 개 완성 UTC 일 거래대금 합 상위 20. 첫 일봉이 180 일 이상 전, 30 일 모두 자료가 있어야 함."""
    first = {s: min(d) for s, d in daily.items() if d}
    out: dict[str, list[str]] = {}
    for m in month_starts():
        t0 = _ms(m)
        cand = []
        for s, d in daily.items():
            if excluded(s, exinfo) or first[s] > t0 - MIN_HISTORY_DAYS * DAY:
                continue
            vols = [d.get(t0 - k * DAY) for k in range(1, 31)]
            if any(v is None for v in vols):
                continue
            cand.append((sum(v.qv for v in vols if v), s))
        cand.sort(key=lambda x: (-x[0], x[1]))
        out[m.isoformat()[:7]] = [s for _, s in cand[:TOP_N]]
    return out


# ------------------------------------------------------------------ 신호 (순수)


@dataclass(frozen=True, slots=True)
class Signal:
    day: str  # 신호 일봉 날짜 (그날 끝난 뒤 다음 00:00 에 진입)
    symbol: str
    z: float
    beta: float
    alpha: float
    qv_prev: float  # 직전 일 거래대금 (동률 처리)
    atr_coin: float
    atr_btc: float


def ols(x: Sequence[float], y: Sequence[float]) -> tuple[float, float]:
    mx, my = statistics.fmean(x), statistics.fmean(y)
    sxx = sum((a - mx) ** 2 for a in x)
    if sxx <= 0:
        return math.nan, math.nan
    beta = sum((a - mx) * (b - my) for a, b in zip(x, y, strict=True)) / sxx
    return my - beta * mx, beta


def residual_z(rc: Sequence[float], rb: Sequence[float]) -> tuple[float, float, float] | None:
    """rc · rb = 최근 63 일 일간 단순 수익(오래된 것부터). 앞 60 일로 회귀, 뒤 3 일 잔차 합의 z.

    z = (S − 추정 구간 안 연속 3 일 잔차 합들의 평균) / 그 표준편차. (겹치는 3 일 합 58 개 — 독립 아님)
    """
    if len(rc) != 63 or len(rb) != 63 or any(math.isnan(v) for v in (*rc, *rb)):
        return None
    xa, ya = rb[:60], rc[:60]
    alpha, beta = ols(xa, ya)
    if math.isnan(beta):
        return None
    res = [y - alpha - beta * x for x, y in zip(xa, ya, strict=True)]
    sums = [res[i] + res[i + 1] + res[i + 2] for i in range(58)]
    sd = statistics.stdev(sums)
    if sd <= 0:
        return None
    s = sum(c - alpha - beta * b for c, b in zip(rc[60:], rb[60:], strict=True))
    return (s - statistics.fmean(sums)) / sd, beta, alpha


def atr20(bars: Sequence[DayBar]) -> float:
    """마지막 20 일 true range 평균 (bars = 최근 21 일, 오래된 것부터)."""
    tr = [max(b.h - b.lo, abs(b.h - p.c), abs(b.lo - p.c)) for p, b in pairwise(bars)]
    return statistics.fmean(tr[-20:])


def signals(daily: dict[str, dict[int, DayBar]], members: dict[str, list[str]]) -> list[Signal]:
    """신호 일 t(그날 일봉 완성) — 진입 월은 t+1 의 달. 그 달 종목군만."""
    btc = daily["BTCUSDT"]
    out: list[Signal] = []
    d = EVAL_START - timedelta(days=1)
    while d <= EVAL_END - timedelta(days=1):
        entry_month = (d + timedelta(days=1)).isoformat()[:7]
        t = _ms(d)
        days = [t - k * DAY for k in range(63, -1, -1)]  # 64 개 일봉 → 63 개 수익
        if all(x in btc for x in days):
            rb = [btc[days[i]].c / btc[days[i - 1]].c - 1 for i in range(1, 64)]
            btc_bars = [btc[x] for x in days[-21:]]
            for s in members.get(entry_month, []):
                bars = daily.get(s, {})
                if not all(x in bars for x in days):
                    continue  # 결측이면 신호 없음 (보간 안 함)
                rc = [bars[days[i]].c / bars[days[i - 1]].c - 1 for i in range(1, 64)]
                r = residual_z(rc, rb)
                if r is None:
                    continue
                z, beta, alpha = r
                if z <= -2 and beta > 0:
                    out.append(
                        Signal(
                            d.isoformat(),
                            s,
                            z,
                            beta,
                            alpha,
                            bars[t].qv,
                            atr20([bars[x] for x in days[-21:]]),
                            atr20(btc_bars),
                        )
                    )
        d += timedelta(days=1)
    return out


# ------------------------------------------------------------------ 장중 자료 (5분봉 · funding · mark)


def _months_for(day: str, span_days: int = 4) -> list[str]:
    d = date.fromisoformat(day)
    return sorted({(d + timedelta(days=k)).isoformat()[:7] for k in range(span_days + 1)})


def needed(members: dict[str, list[str]]) -> dict[str, set[str]]:
    """종목 → 받을 달: 구성 달 + 다음 달(72 시간 보유가 달을 넘김). BTC 는 전 기간."""
    need: dict[str, set[str]] = defaultdict(set)
    for m, syms in members.items():
        y, mo = int(m[:4]), int(m[5:])
        nxt = f"{y + (mo == 12):04d}-{(mo % 12) + 1:02d}"
        for s in syms:
            need[s].add(m)
            if nxt <= "2026-08":
                need[s].add(nxt)
    need["BTCUSDT"] = {m.isoformat()[:7] for m in month_starts()}
    return need


def cmd_fetch() -> int:
    members = json.loads((OUT / "members.json").read_text(encoding="utf-8"))
    need = needed(members)
    jobs = [
        (k, s, m, iv)
        for s, ms in need.items()
        for m in sorted(ms)
        for k, iv in (("klines", "5m"), ("markPriceKlines", "1h"), ("fundingRate", ""))
    ]
    print(f"받을 파일 최대 {len(jobs):,} (종목 {len(need)})", flush=True)
    missing: list[tuple[str, str, str]] = []
    done = 0

    def one(job: tuple[str, str, str, str]) -> tuple[tuple[str, str, str, str], Path | None]:
        return job, _fetch(job[0], job[1], job[2], job[3])

    with ThreadPoolExecutor(max_workers=6) as ex:
        for job, path in ex.map(one, jobs):
            done += 1
            if path is None:
                missing.append((job[0], job[1], job[2]))
            if done % 500 == 0:
                print(f"{done:,} / {len(jobs):,} · 없음 {len(missing)}", flush=True)
    (OUT / "fetch_missing.json").write_text(json.dumps(missing), encoding="utf-8")
    print(f"끝 · 없음 {len(missing)} (상장 전 · 폐지 뒤 달은 정상적으로 없다)", flush=True)
    return 0


# ------------------------------------------------------------------ 명령


def cmd_exinfo() -> int:
    d = json.loads(_get("https://fapi.binance.com/fapi/v1/exchangeInfo") or b"{}")
    OUT.mkdir(parents=True, exist_ok=True)
    slim = {
        s["symbol"]: {
            "underlyingType": s.get("underlyingType"),
            "contractType": s.get("contractType"),
            "filters": [
                f
                for f in s.get("filters", [])
                if f.get("filterType") in ("LOT_SIZE", "MIN_NOTIONAL", "MARKET_LOT_SIZE")
            ],
        }
        for s in d.get("symbols", [])
    }
    (OUT / "exchange_info.json").write_text(json.dumps(slim, ensure_ascii=False), encoding="utf-8")
    print(f"거래소 정보 {len(slim)} 종목 (받은 시각 {datetime.now(UTC).isoformat()})")
    return 0


def cmd_daily2019() -> int:
    db = sqlite3.connect(f"file:{REPO / 'data' / 'trading.db'}?mode=ro", uri=True)
    first = db.execute(
        "select symbol, min(open_ms) from candles where exchange='binance_usdm' and timeframe='1d' group by symbol"
    ).fetchall()
    syms = sorted(s for s, t in first if int(t) == _ms(date(2020, 1, 1)))
    got = 0
    for s in syms:
        for m in ("2019-09", "2019-10", "2019-11", "2019-12"):
            if _fetch("klines", s, m, "1d") is not None:
                got += 1
    print(f"2020-01-01 에 시작하는 종목 {len(syms)} · 2019 일봉 파일 {got}")
    return 0


def cmd_universe() -> int:
    exinfo = json.loads((OUT / "exchange_info.json").read_text(encoding="utf-8"))
    daily = load_daily()
    members = build_universe(daily, exinfo)
    (OUT / "members.json").write_text(
        json.dumps(members, ensure_ascii=False, indent=0), encoding="utf-8"
    )
    uniq = sorted({s for v in members.values() for s in v})
    short = {m: len(v) for m, v in members.items() if len(v) < TOP_N}
    delisted = [s for s in uniq if s not in exinfo]
    print(
        f"달 {len(members)} · 구성 종목 {len(uniq)} · 20 개 미만 달 {short} · 지금 거래소 정보에 없는(폐지) 구성 종목 {len(delisted)}: {delisted[:40]}"
    )
    return 0


def main(argv: list[str]) -> int:
    cmds = {
        "exinfo": cmd_exinfo,
        "daily2019": cmd_daily2019,
        "universe": cmd_universe,
        "fetch": cmd_fetch,
    }
    if argv[:1] == ["pilot"]:
        from scripts.run_resid_rev_212_pilot import cmd_pilot

        return cmd_pilot()
    if argv[:1] == ["threshold"]:
        from scripts.run_resid_rev_212_threshold import cmd_threshold

        return cmd_threshold()
    if not argv or argv[0] not in cmds:
        print(__doc__)
        return 2
    return cmds[argv[0]]()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))


__all__ = ["DayBar", "Signal", "asdict", "atr20", "excluded", "random", "residual_z", "signals"]
