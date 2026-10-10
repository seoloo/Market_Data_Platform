# ruff: noqa: E501 — 한국어 설명 · 표 문자열이 길다
"""문서 202 — 금 돌파 사전 점검: 자료 품질 · 대용(PAXG) 추종 · 아시아 범위 크기. 전략 수익은 계산하지 않는다.

사용: ``python -m scripts.gold_breakout_data_check``
"""

from __future__ import annotations

import csv
import io
import statistics
import zipfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "data" / "external" / "binance_vision"


def load(market: str, symbol: str) -> dict[int, tuple[float, float, float, float, float]]:
    """open_ms → (open, high, low, close, quote volume). 1분봉."""
    out: dict[int, tuple[float, float, float, float, float]] = {}
    for z in sorted((ROOT / market / symbol / "1m").glob("*.zip")):
        with zipfile.ZipFile(z) as zf:
            for name in zf.namelist():
                for row in csv.reader(io.TextIOWrapper(zf.open(name), "utf-8")):
                    if not row or not row[0].isdigit():
                        continue
                    ms = int(row[0])
                    if ms > 4_102_444_800_000:  # 마이크로초 (2025 이후 현물)
                        ms //= 1000
                    out[ms] = (
                        float(row[1]),
                        float(row[2]),
                        float(row[3]),
                        float(row[4]),
                        float(row[7]),
                    )
    return out


def main() -> None:
    paxg = load("spot", "PAXGUSDT")
    xau = load("um", "XAUUSDT")
    print(f"PAXG 1분봉 {len(paxg):,} · XAUUSDT 1분봉 {len(xau):,}")
    # 연도별 평일 유동성
    by_year: dict[int, list[float]] = {}
    for ms, (_o, _h, _l, _c, qv) in paxg.items():
        t = datetime.fromtimestamp(ms / 1000, UTC)
        if t.weekday() < 5:
            by_year.setdefault(t.year, []).append(qv)
    print("\n연도 | 평일 분 수 | 거래 0 인 분 % | 분당 거래대금 중앙값 USD")
    for y, v in sorted(by_year.items()):
        zero = sum(1 for x in v if x == 0) / len(v) * 100
        print(f"{y} | {len(v):,} | {zero:.1f}% | {statistics.median(v):,.0f}")
    # 겹치는 기간: 5분 수익 상관 (평일 07~21 UTC)
    common = sorted(set(paxg) & set(xau))
    r_p, r_x = [], []
    for ms in common:
        t = datetime.fromtimestamp(ms / 1000, UTC)
        if t.weekday() >= 5 or not 7 <= t.hour < 21 or t.minute % 5:
            continue
        prev = ms - 300_000
        if prev in paxg and prev in xau:
            r_p.append(paxg[ms][3] / paxg[prev][3] - 1)
            r_x.append(xau[ms][3] / xau[prev][3] - 1)
    if r_p:
        corr = statistics.correlation(r_p, r_x)
        diff = [abs(a - b) * 1e4 for a, b in zip(r_p, r_x, strict=True)]
        print(
            f"\n겹침 {len(r_p):,} 개 5분 수익: 상관 {corr:.3f} · |차이| 중앙값 {statistics.median(diff):.2f}bp · p95 {sorted(diff)[int(0.95 * len(diff))]:.2f}bp"
        )
    # 아시아 범위(00~07 UTC) 크기 — 평일
    for label, data in (("PAXG", paxg), ("XAUUSDT", xau)):
        days: dict[str, list[float]] = {}
        for ms, (_o, h, low, _c, _qv) in data.items():
            t = datetime.fromtimestamp(ms / 1000, UTC)
            if t.weekday() < 5 and t.hour < 7:
                d = days.setdefault(t.date().isoformat(), [0.0, 1e18])
                d[0], d[1] = max(d[0], h), min(d[1], low)
        widths = sorted((hi / lo - 1) * 1e4 for hi, lo in days.values() if lo < 1e17)
        if widths:
            print(
                f"{label} 아시아 범위 폭: 중앙값 {statistics.median(widths):.1f}bp · p10 {widths[len(widths) // 10]:.1f} · p90 {widths[9 * len(widths) // 10]:.1f} · 일수 {len(widths)}"
            )


if __name__ == "__main__":
    main()
