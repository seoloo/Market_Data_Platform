"""문서 157 — 생존 편향 없는 알트코인 종목군(폐지 코인 포함) 자료 받기. 바이낸스 공개 자료만.

사용 예::

    python -m scripts.fetch_alt_universe symbols    # 지금까지 있었던 모든 현물 USDT 페어(폐지 포함)
    python -m scripts.fetch_alt_universe daily      # 후보 페어의 일봉(거래대금 순위용)
    python -m scripts.fetch_alt_universe universe   # 월말마다 거래대금 상위 20 (다음 달 종목군)
    python -m scripts.fetch_alt_universe bars4h     # 종목군 페어 4시간봉 (첫 달 2 달 앞 ~ 끝)

* 목록: data.binance.vision 의 S3 목록(``data/spot/monthly/klines/``) — 지금 거래되지 않는
  페어(LUNA · FTT · SRM …)도 남아 있다. 파일마다 공식 체크섬을 확인한다.
* 빼는 것(결과 전 고정): 스테이블코인·법정화폐, 레버리지 토큰(X+UP/DOWN 에서 X 가 있는 페어,
  BULL/BEAR), 자산 연동(PAXG · XAUT · WBTC · WBETH), 그리고 **이미 KB 로 본 10 개**
  (BTC · ETH · 156 의 BNB · LTC · ADA · XRP · TRX · LINK · DOGE · SOL).
* 저장: ``data/external/binance_vision/spot/<SYM>/<1d|4h>/`` · ``data/research/alt_universe/``.
"""

from __future__ import annotations

import csv
import io
import json
import re
import sys
import zipfile
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VISION = REPO / "data" / "external" / "binance_vision" / "spot"
OUT = REPO / "data" / "research" / "alt_universe"
S3 = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
FIRST, LAST = "2017-12", "2026-08"
TOP, VOL_DAYS, MIN_DAYS = 20, 30, 60
STABLE = {
    "USDC", "BUSD", "TUSD", "USDP", "PAX", "DAI", "FDUSD", "USDS", "USDSB", "SUSD", "UST",
    "EUR", "GBP", "AUD", "EURI", "AEUR", "USD1", "XUSD", "PYUSD", "RLUSD", "USDE", "BFUSD",
    "IDRT", "BIDR", "BKRW", "BVND", "TRY", "BRL", "ZAR", "UAH", "NGN", "RUB",
}  # fmt: skip
PEGGED = {"PAXG", "XAUT", "WBTC", "WBETH", "BETH"}
SEEN = {"BTC", "ETH", "BNB", "LTC", "ADA", "XRP", "TRX", "LINK", "DOGE", "SOL"}
"""139 · 156 에서 KB 로 이미 본 종목 — 이번엔 안 본 자료만."""


def base_of(sym: str) -> str:
    return sym[: -len("USDT")]


def eligible(symbols: Iterable[str]) -> list[str]:
    """빼는 규칙을 적용한 USDT 페어 (순수)."""
    usdt = {
        s for s in symbols if re.fullmatch(r"[A-Z0-9]+USDT", s) and len(s) > 4
    }  # 한자 이름 페어 제외
    bases = {base_of(s) for s in usdt}
    out = []
    for s in sorted(usdt):
        b = base_of(s)
        if b in STABLE or b in PEGGED or b in SEEN:
            continue
        if b.endswith(("BULL", "BEAR")):
            continue
        lev = re.fullmatch(r"(.+?)(UP|DOWN)", b)
        if lev and lev.group(1) in bases:
            continue  # BTCUP · SXPDOWN … (JUP 은 J 페어가 없어 남는다)
        out.append(s)
    return out


def _list(prefix: str) -> list[str]:
    import httpx

    keys: list[str] = []
    marker = ""
    with httpx.Client(timeout=60) as client:
        while True:
            text = client.get(
                S3, params={"delimiter": "/", "prefix": prefix, "marker": marker}
            ).text
            found = re.findall(r"<Prefix>([^<]+)</Prefix>", text) + re.findall(
                r"<Key>([^<]+)</Key>", text
            )
            found = [f for f in found if f != prefix]
            keys += found
            if "<IsTruncated>true</IsTruncated>" not in text or not found:
                break
            marker = found[-1]
    return keys


def fetch_symbols() -> int:
    prefixes = _list("data/spot/monthly/klines/")
    syms = sorted({p.split("/")[-2] for p in prefixes if p.endswith("/")})
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "symbols.json").write_text(json.dumps(syms), encoding="utf-8")
    elig = eligible(syms)
    (OUT / "eligible.json").write_text(json.dumps(elig), encoding="utf-8")
    print(
        f"현물 페어 {len(syms)} · USDT {sum(s.endswith('USDT') for s in syms)} · 후보 {len(elig)}"
    )
    return 0


def _months_available(sym: str, interval: str) -> list[str]:
    keys = _list(f"data/spot/monthly/klines/{sym}/{interval}/")
    return sorted(
        m.group(1)
        for k in keys
        if (m := re.search(rf"{sym}-{interval}-(\d{{4}}-\d{{2}})\.zip$", k)) is not None
    )


def _fetch_months(sym: str, interval: str, months: Sequence[str]) -> int:
    from scripts.fetch_binance_vision_klines import fetch

    n = 0
    for m in months:
        fetch(sym, interval, m, "spot")
        n += 1
    return n


def fetch_daily() -> int:
    elig = json.loads((OUT / "eligible.json").read_text(encoding="utf-8"))

    def one(sym: str) -> tuple[str, int]:
        months = [m for m in _months_available(sym, "1d") if FIRST <= m <= LAST]
        months = [m for m in months if not (VISION / sym / "1d" / f"{sym}-1d-{m}.zip").exists()]
        return sym, _fetch_months(sym, "1d", months)

    total = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        for k, (sym, n) in enumerate(pool.map(one, elig), 1):
            total += n
            if k % 50 == 0:
                print(f"[{k}/{len(elig)}] {sym} {n}", flush=True)
    print(f"일봉 파일 {total} 받음 · 페어 {len(elig)}")
    return 0


def daily_quote_volume(sym: str) -> dict[str, float]:
    """{YYYY-MM-DD: 거래대금(USDT)}."""
    out: dict[str, float] = {}
    for p in sorted((VISION / sym / "1d").glob("*.zip")):
        with zipfile.ZipFile(p) as zf:
            text = zf.read(zf.namelist()[0]).decode("ascii")
        for row in csv.reader(io.StringIO(text)):
            if not row or not row[0].isdigit():
                continue
            ms = int(row[0])
            ms = ms // 1000 if ms > 10**14 else ms
            out[datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d")] = float(row[7])
    return out


def monthly_top(
    vols: Mapping[str, Mapping[str, float]], month_ends: Sequence[str], top: int = TOP
) -> dict[str, list[str]]:
    """월말마다 일봉 60 일 이상인 페어 중 지난 30 일 평균 거래대금 상위 ``top`` (순수)."""
    import bisect

    order = {sym: sorted(v) for sym, v in vols.items()}
    out: dict[str, list[str]] = {}
    for me in month_ends:
        ranked = []
        for sym, v in vols.items():
            days = order[sym]
            k = bisect.bisect_right(days, me)
            if k < MIN_DAYS or days[k - 1] < me:
                continue  # 60 일 이상 · 그달 말일에 거래 중이어야 한다
            last = days[k - VOL_DAYS : k]
            ranked.append((-sum(v[d] for d in last) / len(last), sym))
        out[me] = [s for _, s in sorted(ranked)[:top]]
    return out


def _month_ends() -> list[str]:
    import calendar

    out = []
    y, m = int(FIRST[:4]), int(FIRST[5:])
    while f"{y:04d}-{m:02d}" < LAST:
        out.append(f"{y:04d}-{m:02d}-{calendar.monthrange(y, m)[1]:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def build_universe() -> int:
    elig = json.loads((OUT / "eligible.json").read_text(encoding="utf-8"))
    vols = {s: daily_quote_volume(s) for s in elig}
    vols = {s: v for s, v in vols.items() if v}
    members = monthly_top(vols, _month_ends())
    (OUT / "members.json").write_text(json.dumps(members, indent=1), encoding="utf-8")
    union = sorted({s for v in members.values() for s in v})
    last_day = {s: max(vols[s]) for s in union}
    gone = [s for s in union if last_day[s] < "2026-08-31"]
    (OUT / "union.json").write_text(
        json.dumps({s: {"last_day": last_day[s]} for s in union}, indent=1), encoding="utf-8"
    )
    print(f"월말 {len(members)} · 든 페어 {len(union)} · 그중 거래가 끝난 페어 {len(gone)}")
    return 0


def fetch_bars4h() -> int:
    members = json.loads((OUT / "members.json").read_text(encoding="utf-8"))
    first: dict[str, str] = {}
    for me in sorted(members):
        for s in members[me]:
            first.setdefault(s, me[:7])
    jobs: dict[str, list[str]] = defaultdict(list)
    for s, f in first.items():
        y, m = int(f[:4]), int(f[5:])
        y, m = (y, m - 2) if m > 2 else (y - 1, m + 10)
        start = f"{y:04d}-{m:02d}"
        jobs[s] = [x for x in _months_available(s, "4h") if start <= x <= LAST]

    def one(item: tuple[str, list[str]]) -> int:
        sym, months = item
        todo = [m for m in months if not (VISION / sym / "4h" / f"{sym}-4h-{m}.zip").exists()]
        return _fetch_months(sym, "4h", todo)

    with ThreadPoolExecutor(max_workers=8) as pool:
        total = sum(pool.map(one, sorted(jobs.items())))
    print(f"4시간봉 파일 {total} 받음 · 페어 {len(jobs)}")
    return 0


def main(argv: list[str]) -> int:
    stages = {
        "symbols": fetch_symbols,
        "daily": fetch_daily,
        "universe": build_universe,
        "bars4h": fetch_bars4h,
    }
    if len(argv) != 1 or argv[0] not in stages:
        raise SystemExit(
            "usage: python -m scripts.fetch_alt_universe symbols|daily|universe|bars4h"
        )
    return stages[argv[0]]()


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
