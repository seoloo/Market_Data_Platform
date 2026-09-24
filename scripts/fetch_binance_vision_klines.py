"""Binance 공개 데이터(data.binance.vision) USD-M 선물 월별 kline 을 내려받아 SHA-256 을 확인한다.

연구 자료 전용. 메인 DB 에 쓰지 않고
``data/external/binance_vision/um/<SYMBOL>/<interval>/`` 에 zip 을 둔다.
사용: ``python scripts/fetch_binance_vision_klines.py ETHUSDT 1m 2020-01 2026-08``
"""

from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BASE = "https://data.binance.vision/data/futures/um/monthly/klines"
OUT_ROOT = REPO_ROOT / "data" / "external" / "binance_vision" / "um"


def months(start: str, end: str) -> list[str]:
    y, m = map(int, start.split("-"))
    ey, em = map(int, end.split("-"))
    out: list[str] = []
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "auto-trading-research"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return bytes(response.read())


def fetch(symbol: str, interval: str, month: str) -> tuple[Path, int]:
    name = f"{symbol}-{interval}-{month}.zip"
    out_dir = OUT_ROOT / symbol / interval
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / name
    url = f"{BASE}/{symbol}/{interval}/{name}"
    expected = _get(url + ".CHECKSUM").decode("ascii").split()[0].lower()
    if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == expected:
        return target, target.stat().st_size
    payload = _get(url)
    actual = hashlib.sha256(payload).hexdigest()
    if actual != expected:
        raise SystemExit(f"체크섬 불일치: {name} {actual} != {expected}")
    target.write_bytes(payload)
    return target, len(payload)


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        raise SystemExit("usage: fetch_binance_vision_klines.py SYMBOL INTERVAL YYYY-MM YYYY-MM")
    symbol, interval, start, end = argv
    total = 0
    for month in months(start, end):
        path, size = fetch(symbol, interval, month)
        total += size
        sys.stdout.write(f"{path.name} {size} OK\n")
        sys.stdout.flush()
    sys.stdout.write(f"합계 {total} bytes\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
