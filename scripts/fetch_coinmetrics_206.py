"""문서 206 — Coin Metrics Community API(무료)의 BTC 거래소 흐름 · 가격과 과거 공개본(빈티지).

연구 자료 전용. ``data/research/onchain_206/`` 에 원 JSON 페이지 · 합친 CSV · 빈티지(SHA-256 기록).

- 무료 등급에서 받을 수 있는 것: 거래소 입금 ``FlowInExNtv`` · 출금 ``FlowOutExNtv`` · 보유량
  ``SplyExNtv`` (일별, UTC 하루 합) · ``PriceUSD``. **현재 라벨** 기준(주소가 거래소로 밝혀지면
  과거 전체에 소급) — 시점 기준(Pit) · 거래소별 지표는 유료(403).
- 과거 공개본: 인터넷 아카이브에 남은 Coin Metrics CSV(2024-05-25 · 2026-01-20 · 2026-09-21).
  라벨 소급이 과거 값을 얼마나 바꾸는지 재는 데만 쓴다.

사용: ``python -m scripts.fetch_coinmetrics_206``
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "data" / "research" / "onchain_206"
BASE = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
DAILY = ["FlowInExNtv", "FlowOutExNtv", "SplyExNtv", "PriceUSD", "SplyCur"]
WAYBACK = {
    "20240525": "raw.githubusercontent.com/coinmetrics/data/master/csv/btc.csv",
    "20260120": "raw.githubusercontent.com/coinmetrics/data/master/csv/btc.csv",
    "20260921": "raw.githubusercontent.com/coinmetrics/data/master/csv/btc.csv",
}
START = "2010-07-18"
END = "2026-10-09"


def _get(params: dict[str, str]) -> dict[str, Any]:
    url = BASE + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "auto-trading-research"})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return dict(json.loads(resp.read()))
        except OSError:
            time.sleep(5 * (attempt + 1))
    raise SystemExit(f"요청 실패: {url[:200]}")


def fetch(name: str, metrics: list[str], frequency: str) -> list[dict[str, Any]]:
    params = {
        "assets": "btc",
        "metrics": ",".join(metrics),
        "frequency": frequency,
        "start_time": START,
        "end_time": END,
        "page_size": "10000",
        "paging_from": "start",
    }
    rows: list[dict[str, Any]] = []
    page = 0
    token: str | None = None
    while True:
        if token:
            params["next_page_token"] = token
        body = _get(params)
        raw = json.dumps(body, sort_keys=True).encode()
        (OUT / "raw").mkdir(parents=True, exist_ok=True)
        (OUT / "raw" / f"{name}-{page:03d}.json").write_bytes(raw)
        rows += body.get("data", [])
        token = body.get("next_page_token")
        page += 1
        if not token:
            break
        time.sleep(0.7)
    return rows


def _write(path: Path, rows: list[dict[str, Any]]) -> str:
    cols = sorted({k for r in rows for k in r if k != "asset"} - {"time"})
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["time", *cols])
        for r in rows:
            w.writerow([r["time"], *(r.get(c, "") for c in cols)])
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _raw(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "auto-trading-research"})
    with urllib.request.urlopen(req, timeout=300) as resp:
        return bytes(resp.read())


def vintages() -> dict[str, str]:
    """인터넷 아카이브의 과거 CSV 를 받는다 (gzip 이면 풀어서)."""
    out = {}
    (OUT / "vintages").mkdir(parents=True, exist_ok=True)
    for day, target in WAYBACK.items():
        cdx = (
            "http://web.archive.org/cdx/search/cdx?output=json&fl=timestamp"
            f"&url={target}&from={day}&to={day}"
        )
        stamp = json.loads(_raw(cdx))[1][0]
        body = _raw(f"http://web.archive.org/web/{stamp}id_/https://{target}")
        if body[:2] == bytes((0x1F, 0x8B)):  # gzip
            body = gzip.decompress(body)
        path = OUT / "vintages" / f"btc_{day}.csv"
        path.write_bytes(body)
        out[f"{day} ({stamp})"] = hashlib.sha256(body).hexdigest()
        time.sleep(1.0)
    return out


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    daily = fetch("daily", DAILY, "1d")
    manifest = {
        "source": BASE,
        "fetched_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "daily_rows": len(daily),
        "daily_sha256": _write(OUT / "btc_daily.csv", daily),
        "vintages_sha256": vintages(),
        "raw_pages": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((OUT / "raw").glob("*.json"))
        },
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    sys.stdout.write(json.dumps({k: v for k, v in manifest.items() if k != "raw_pages"}) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
