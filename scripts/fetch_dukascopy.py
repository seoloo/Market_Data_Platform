# ruff: noqa: E501 — 한국어 설명 · 표 문자열이 길다
"""Dukascopy 공개 시세(datafeed.dukascopy.com) 일별 1분봉(BID) 받기 — 문서 202 금 돌파 연구 자료 (사용자 승인 2026-10-11).

연구 자료 전용. ``data/external/dukascopy/<SYMBOL>/<SIDE>/<YYYY>/<MM>-<DD>.bi5`` 에 원본 그대로 두고
``manifest.jsonl`` 에 날짜 · 크기 · SHA-256 을 남긴다. 이미 받은 날은 건너뛴다(이어 받기).
한 번에 하나씩 받고, 503 · 429 면 짧게 기다렸다 다시 시도한다(점점 길게).

사용: ``python scripts/fetch_dukascopy.py XAUUSD 2015-01-01 2026-09-30 [BID|ASK]``
파일 형식(LZMA): 레코드 24바이트 ``>IIIIIf`` = 하루 시작부터 초 · 시가 · 종가 · 저가 · 고가 · 거래량.
"""

from __future__ import annotations

import hashlib
import json
import lzma
import struct
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_ROOT = REPO_ROOT / "data" / "external" / "dukascopy"
BASE = "https://datafeed.dukascopy.com/datafeed"
UA = "Mozilla/5.0 (auto-trading-research)"
PAUSE = 0.5
"""요청 사이 쉬는 초."""
BACKOFF = (1, 2, 3, 5, 5, 10, 10, 20, 30, 60, 120, 300)
"""503 은 대개 서버 일부가 무작위로 거절하는 것이라(2026-10-11 관찰: 200 · 503 이 번갈아 나옴) 짧게 자주 다시 시도한다."""


def url(symbol: str, day: date, side: str = "BID") -> str:
    # 월은 0부터 센다 (1월 = 00)
    return (
        f"{BASE}/{symbol}/{day.year:04d}/{day.month - 1:02d}/{day.day:02d}/{side}_candles_min_1.bi5"
    )


def path_for(symbol: str, day: date, side: str = "BID") -> Path:
    return OUT_ROOT / symbol / side / f"{day.year:04d}" / f"{day.month:02d}-{day.day:02d}.bi5"


def decode(
    raw: bytes, day: date, divisor: float
) -> list[tuple[int, float, float, float, float, float]]:
    """bi5 → [(open_ms, open, high, low, close, volume)]. 빈 파일은 빈 목록."""
    if not raw:
        return []
    data = lzma.decompress(raw, format=lzma.FORMAT_ALONE)
    start = int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp() * 1000)
    out = []
    for i in range(len(data) // 24):
        sec, o, c, lo, hi, vol = struct.unpack(">IIIIIf", data[i * 24 : (i + 1) * 24])
        out.append(
            (start + sec * 1000, o / divisor, hi / divisor, lo / divisor, c / divisor, float(vol))
        )
    return out


def days(start: date, end: date) -> Iterator[date]:
    d = start
    while d <= end:
        if d.weekday() != 5:  # 토요일은 금 시장이 닫혀 자료가 없다
            yield d
        d += timedelta(days=1)


def _get(u: str) -> bytes | None:
    for wait in (*BACKOFF, None):
        try:
            req = urllib.request.Request(u, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as resp:
                return bytes(resp.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return b""
            if exc.code not in (429, 500, 502, 503, 504) or wait is None:
                raise
        except (urllib.error.URLError, TimeoutError):
            if wait is None:
                raise
        time.sleep(wait)
    return None


def main(argv: list[str]) -> int:
    symbol, start, end = argv[0], date.fromisoformat(argv[1]), date.fromisoformat(argv[2])
    side = argv[3] if len(argv) > 3 else "BID"
    manifest = OUT_ROOT / symbol / side / "manifest.jsonl"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    got = skipped = 0
    for day in days(start, end):
        p = path_for(symbol, day, side)
        if p.exists():
            skipped += 1
            continue
        raw = _get(url(symbol, day, side))
        if raw is None:
            print(f"{day} 실패 — 나중에 다시 실행하면 이어 받는다", flush=True)
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_bytes(raw)
        tmp.replace(p)
        with manifest.open("a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "day": day.isoformat(),
                        "bytes": len(raw),
                        "sha256": hashlib.sha256(raw).hexdigest(),
                    }
                )
                + "\n"
            )
        got += 1
        if got % 100 == 0:
            print(f"{day} 까지 · 새로 {got} · 건너뜀 {skipped}", flush=True)
        time.sleep(PAUSE)
    print(f"끝 · 새로 {got} · 건너뜀 {skipped}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
