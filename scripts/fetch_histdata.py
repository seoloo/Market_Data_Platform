# ruff: noqa: E501 — 한국어 설명 · 표 문자열이 길다
"""HistData.com 무료 1분봉(ASCII · M1) 받기 — 문서 202 금 돌파 연구 자료 (사용자 승인 2026-10-11 "그러면 그걸로 해줘").

연구 자료 전용. 사이트의 다운로드 버튼과 같은 요청(페이지의 토큰 → ``/get.php`` POST)으로 zip 을 받아
``data/external/histdata/<PAIR>/<기간>.zip`` 에 두고 ``manifest.jsonl`` 에 크기 · SHA-256 을 남긴다. 이미 받은 것은 건너뛴다.

사용: ``python scripts/fetch_histdata.py XAUUSD 2015 2025 [2026-01 2026-09]``  (연도 범위 · 선택: 올해 월 범위)
파일 형식: ``YYYYMMDD HHMMSS;시가;고가;저가;종가;거래량`` — 시각은 **서머타임 없는 뉴욕 표준시(UTC−5)**, 거래량은 0.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_ROOT = REPO_ROOT / "data" / "external" / "histdata"
SITE = "https://www.histdata.com"
UA = "Mozilla/5.0 (auto-trading-research)"


def _page(pair: str, period: str) -> str:
    year, _, month = period.partition("-")
    path = (
        f"/download-free-forex-historical-data/?/ascii/1-minute-bar-quotes/{pair.lower()}/{year}"
        + (f"/{int(month)}" if month else "")
    )
    req = urllib.request.Request(SITE + path, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8", "replace"), SITE + path  # type: ignore[return-value]


def fetch(pair: str, period: str) -> tuple[Path, int]:
    out = OUT_ROOT / pair / f"{period}.zip"
    if out.exists():
        return out, -1
    html, referer = _page(pair, period)  # type: ignore[misc]
    form = re.search(r'<form id="file_down".*?</form>', html, re.S)
    if not form:
        raise RuntimeError(f"{period}: 다운로드 폼이 없다")
    fields = dict(re.findall(r'name="(\w+)" id="\w+" value="([^"]*)"', form.group(0)))
    req = urllib.request.Request(
        SITE + "/get.php",
        data=urllib.parse.urlencode(fields).encode(),
        headers={
            "User-Agent": UA,
            "Referer": referer,
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = resp.read()
    if body[:2] != b"PK":
        raise RuntimeError(f"{period}: zip 이 아니다 ({body[:80]!r})")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(body)
    with (OUT_ROOT / pair / "manifest.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "period": period,
                    "bytes": len(body),
                    "sha256": hashlib.sha256(body).hexdigest(),
                    "fields": {k: v for k, v in fields.items() if k != "tk"},
                }
            )
            + "\n"
        )
    return out, len(body)


def main(argv: list[str]) -> int:
    pair, y0, y1 = argv[0], int(argv[1]), int(argv[2])
    periods = [str(y) for y in range(y0, y1 + 1)]
    if len(argv) >= 5:
        a, b = argv[3], argv[4]
        y, m0, m1 = a[:4], int(a[5:]), int(b[5:])
        periods += [f"{y}-{m:02d}" for m in range(m0, m1 + 1)]
    total = 0
    for p in periods:
        path, n = fetch(pair, p)
        print(f"{path.name} {'있음' if n < 0 else f'{n:,} bytes'}", flush=True)
        total += max(n, 0)
        time.sleep(2)
    print(f"합계 {total:,} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
