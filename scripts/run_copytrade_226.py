"""문서 226 — 바이낸스 카피 트레이딩 수익 상위 10 명 거래 기록 받기 · 패턴 계산. 주문 없음.

사용 예::

    python -m scripts.run_copytrade_226 fetch     # data/research/copytrade_226/ 원자료(키 없음)
    python -m scripts.run_copytrade_226 analyze   # reports/copytrade_226/result.json · 화면

웹 화면이 쓰는 공개 주소라 요청 사이를 넉넉히 쉬고, '바쁨' 답이면 기다렸다 다시 묻는다.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

REPO = Path(__file__).resolve().parent.parent
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from auto_trading.research.copytrade_226 import (  # noqa: E402
    DAY_MS,
    entry_move,
    pick_top,
    summarize_trader,
    trend_on,
)

RAW = REPO / "data" / "research" / "copytrade_226"
OUT = REPO / "reports" / "copytrade_226"
BAPI = "https://www.binance.com/bapi/futures/v1/friendly/future/copy-trade"
FAPI = "https://fapi.binance.com/fapi/v1/klines"
UA = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"}
PAUSE = 1.5
WINDOW_D = 180


def _post(c: httpx.Client, path: str, body: dict[str, Any]) -> dict[str, Any]:
    for attempt in range(6):
        time.sleep(PAUSE)
        r = c.post(f"{BAPI}/{path}", json=body, headers=UA)
        data = r.json() if r.status_code == 200 else {}
        if data.get("data") is not None:
            return dict(data["data"])
        time.sleep(10 * (attempt + 1))  # 바쁨 · 제한 — 기다렸다 다시
    raise RuntimeError(f"받기 실패: {path} {body.get('portfolioId', '')}")


def _get(c: httpx.Client, path: str) -> dict[str, Any]:
    for attempt in range(6):
        time.sleep(PAUSE)
        r = c.get(f"{BAPI}/{path}", headers=UA)
        data = r.json() if r.status_code == 200 else {}
        if data.get("data") is not None:
            return dict(data["data"])
        time.sleep(10 * (attempt + 1))
    raise RuntimeError(f"받기 실패: {path}")


def _list(c: httpx.Client, time_range: str, data_type: str, pages: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for pg in range(1, pages + 1):
        d = _post(
            c,
            "home-page/query-list",
            {
                "pageNumber": pg,
                "pageSize": 20,
                "timeRange": time_range,
                "dataType": data_type,
                "favoriteOnly": False,
                "hideFull": False,
                "nickname": "",
                "order": "DESC",
                "userAsset": 0,
                "portfolioType": "PUBLIC",
            },
        )
        out += list(d.get("list") or [])
    return out


def _positions(c: httpx.Client, pid: str, since_ms: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    pg = 1
    while True:
        d = _post(
            c,
            "lead-portfolio/position-history",
            {"pageNumber": pg, "pageSize": 100, "portfolioId": pid},
        )
        got = list(d.get("list") or [])
        rows = [r for r in got if r.get("closed")]  # 아직 열린(부분 청산) 포지션은 뺀다
        out += rows
        if (
            not got
            or pg * 100 >= int(d.get("total") or 0)
            or (rows and min(int(r["closed"]) for r in rows) < since_ms)
        ):
            return out
        pg += 1


def _fills(c: httpx.Client, pid: str, since_ms: int, cap: int = 10) -> list[dict[str, Any]]:
    """체결 기록 — 최근 것부터 (바이낸스가 짧은 기간만 줄 수 있다). 최대 cap 쪽."""
    out: list[dict[str, Any]] = []
    idx: str | None = None
    for pg in range(1, cap + 1):
        body: dict[str, Any] = {"pageNumber": pg, "pageSize": 100, "portfolioId": pid}
        if idx:
            body["indexValue"] = idx
        d = _post(c, "lead-portfolio/trade-history", body)
        rows = list(d.get("list") or [])
        out += rows
        idx = d.get("indexValue")
        if not rows or not idx or len(out) >= int(d.get("total") or 0):
            break
        if min(int(r["time"]) for r in rows) < since_ms:
            break
    return out


def _klines(c: httpx.Client, symbol: str, interval: str, start: int, end: int) -> list[list[Any]]:
    out: list[list[Any]] = []
    t = start
    while t < end:
        time.sleep(0.2)
        r = c.get(
            FAPI,
            params={"symbol": symbol, "interval": interval, "startTime": t, "limit": 1500},
        )
        if r.status_code != 200:
            return out
        rows = r.json()
        if not rows:
            break
        out += rows
        t = int(rows[-1][0]) + 1
        if len(rows) < 1500:
            break
    return out


def fetch() -> int:
    RAW.mkdir(parents=True, exist_ok=True)
    now = int(time.time() * 1000)
    since = now - WINDOW_D * DAY_MS
    with httpx.Client(timeout=30.0) as c:
        top_pnl = _list(c, "180D", "PNL", 3)
        top_roi = _list(c, "90D", "ROI", 1)
        (RAW / "list_180d_pnl.json").write_text(json.dumps(top_pnl), encoding="utf-8")
        (RAW / "list_90d_roi.json").write_text(json.dumps(top_roi), encoding="utf-8")
        counts: dict[str, int] = {}
        chosen: list[dict[str, Any]] = []
        for r in top_pnl:
            pid = str(r["leadPortfolioId"])
            det = _get(c, f"lead-portfolio/detail?portfolioId={pid}")
            r["status"] = det.get("status", "?")
            if r["status"] != "ACTIVE":
                continue
            cached = RAW / f"pos_{pid}.json"
            pos = (
                json.loads(cached.read_text(encoding="utf-8"))
                if cached.exists()
                else _positions(c, pid, since)
            )  # 같은 날 다시 돌리면 받은 것을 쓴다
            counts[pid] = sum(1 for p in pos if int(p["closed"]) >= since)
            (RAW / f"pos_{pid}.json").write_text(json.dumps(pos), encoding="utf-8")
            chosen = pick_top(top_pnl, counts)
            print(f"{r['nickname']}: 포지션 {counts[pid]} · 고름 {len(chosen)}", flush=True)
            if len(chosen) == 10:
                break
        meta = {
            "fetched_ms": now,
            "since_ms": since,
            "chosen": [str(r["leadPortfolioId"]) for r in chosen],
            "status": {
                str(r["leadPortfolioId"]): r.get("status") for r in top_pnl if "status" in r
            },
            "counts": counts,
        }
        symbols: set[str] = set()
        for pid in meta["chosen"]:
            fpath = RAW / f"fills_{pid}.json"
            fills = (
                json.loads(fpath.read_text(encoding="utf-8"))
                if fpath.exists()
                else _fills(c, pid, since)
            )
            (RAW / f"fills_{pid}.json").write_text(json.dumps(fills), encoding="utf-8")
            pos = json.loads((RAW / f"pos_{pid}.json").read_text(encoding="utf-8"))
            symbols |= {p["symbol"] for p in pos if int(p["closed"]) >= since}
            print(f"체결 {pid}: {len(fills)}", flush=True)
        kl: dict[str, list[list[Any]]] = {}
        for s in sorted(symbols):
            kl[s] = _klines(c, s, "1h", since - 2 * DAY_MS, now)
        (RAW / "klines_1h.json").write_text(json.dumps(kl), encoding="utf-8")
        btc = _klines(c, "BTCUSDT", "1d", since - 200 * DAY_MS, now)
        (RAW / "btc_1d.json").write_text(json.dumps(btc), encoding="utf-8")
        (RAW / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    print(f"고른 {len(meta['chosen'])} 명 · 종목 {len(symbols)}")
    return 0


def analyze() -> int:
    meta = json.loads((RAW / "meta.json").read_text(encoding="utf-8"))
    since = int(meta["since_ms"])
    split = since + (int(meta["fetched_ms"]) - since) // 2
    lst = {
        str(r["leadPortfolioId"]): r
        for r in json.loads((RAW / "list_180d_pnl.json").read_text(encoding="utf-8"))
    }
    kl = json.loads((RAW / "klines_1h.json").read_text(encoding="utf-8"))
    closes = {s: [(int(k[0]), float(k[4])) for k in v] for s, v in kl.items()}
    btc = [
        (int(k[0]), float(k[4]))
        for k in json.loads((RAW / "btc_1d.json").read_text(encoding="utf-8"))
    ]
    out: dict[str, Any] = {"since": since, "split": split, "traders": []}
    for pid in meta["chosen"]:
        pos = json.loads((RAW / f"pos_{pid}.json").read_text(encoding="utf-8"))
        fills = json.loads((RAW / f"fills_{pid}.json").read_text(encoding="utf-8"))
        moves = [entry_move(closes.get(p["symbol"], []), int(p["opened"])) for p in pos]
        trends = [trend_on(btc, int(p["opened"])) for p in pos]
        s = summarize_trader(pos, fills, moves=moves, trends=trends, since_ms=since, split_ms=split)
        r = lst.get(pid, {})
        s |= {
            "id": pid,
            "nickname": r.get("nickname"),
            "list_pnl_180d": r.get("pnl"),
            "list_roi_180d": r.get("roi"),
            "list_mdd": r.get("mdd"),
            "aum": r.get("aum"),
            "copiers": r.get("currentCopyCount"),
            "fills": len(fills),
            "fills_from": datetime.fromtimestamp(
                min(int(f["time"]) for f in fills) / 1000, UTC
            ).isoformat()
            if fills
            else None,
        }
        out["traders"].append(s)
    roi = json.loads((RAW / "list_90d_roi.json").read_text(encoding="utf-8"))[:10]
    out["roi90_top10(참고)"] = [
        {k: r.get(k) for k in ("nickname", "roi", "pnl", "mdd", "winRate", "aum")} for r in roi
    ]
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "result.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
    )
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    return 0


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else ""
    if cmd == "fetch":
        return fetch()
    if cmd == "analyze":
        return analyze()
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
