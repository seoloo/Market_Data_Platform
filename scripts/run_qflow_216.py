# ruff: noqa: E501 — 한국어 설명이 길다
"""문서 216 — 15 분 경계 첫 10 초 선물 체결 불균형 → 12 시간 뒤 선물 수익. 후향 파일럿. 주문 없음.

자료: 바이낸스 공개 아카이브 USD-M aggTrades 월 파일(받으면 바로 집계하고 지움), USD-M 1 분봉(taker 매수량 포함),
210 캐시(1 분봉 · funding · mark).

사용::

    python -m scripts.run_qflow_216 pilot          # 표본 파일: 빠른 추출 = 전수 파싱 대조, 1 분봉 거래량 대조
    python -m scripts.run_qflow_216 fetch          # 2020-01 ~ 2026-08 aggTrades 받기 → 집계 → 원본 삭제 (이어받기 가능)
    python -m scripts.run_qflow_216 audit          # 집계 자료 품질 (손익 없음)
    python -m scripts.run_qflow_216 stage1         # 1 단계 비용 문턱 점검 (계약 커밋 뒤 한 번)
"""

from __future__ import annotations

import hashlib
import io
import json
import pickle
import sys
import urllib.request
import zipfile
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
VISION = REPO / "data" / "external" / "binance_vision"
STORE = REPO / "data" / "research" / "qflow_216"
REPORT = REPO / "reports" / "qflow_216"
BASE_URL = "https://data.binance.vision/data/futures/um"
SYMBOLS = ("BTCUSDT", "ETHUSDT")
MIN = 60_000
WIN = 10_000  # 첫 10 초
DAY = 86_400_000
FIRST_MONTH = "2020-01"
LAST_MONTH = "2026-08"  # 2026-09 이후는 받지도 열지도 않는다(보존)
CHUNK = 1 << 25  # 압축 푼 32 MB 씩


def _ms(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp() * 1000)


def months(a: str = FIRST_MONTH, b: str = LAST_MONTH) -> list[str]:
    y, m = map(int, a.split("-"))
    out = []
    while f"{y:04d}-{m:02d}" <= b:
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


# ------------------------------------------------------------------ aggTrades 추출


@dataclass
class Extract:
    """한 파일의 집계. 창 = 매 5 분(분 % 5 == 0)의 [분 시작, +10 초). 대조 분 = 매시 00 분 전체."""

    windows: dict[int, list[float]] = field(
        default_factory=dict
    )  # W ms → [매수량, 매도량, 체결 수]
    check_min: dict[int, list[float]] = field(
        default_factory=dict
    )  # 분 ms → [전체량, 공격 매수량, 체결 수]
    gaps: list[tuple[int, int, int]] = field(
        default_factory=list
    )  # (앞 체결 ms, 뒤 체결 ms, 빠진 id 수 · 음수면 중복)
    rows: int = 0
    first_id: int = -1
    last_id: int = -1
    first_ts: int = -1
    last_ts: int = -1
    nonmono: int = 0  # 시각 역행 (검사한 줄에서)
    ts_unit: str = "ms"
    header: bool = False
    sha256: str = ""


def parse(line: bytes) -> tuple[int, float, int, bool]:
    """→ (agg id, 수량, 체결 ms, is_buyer_maker). is_buyer_maker=true → 매수자가 메이커 = 공격적 매도."""
    p = line.split(b",")
    ts = int(p[5])
    if ts > 10**14:  # 마이크로초 표기면 ms 로
        ts //= 1000
    return int(p[0]), float(p[2]), ts, p[6][:1] in (b"t", b"T")


def _ts_line(buf: bytes, pos: int) -> int:
    return parse(buf[pos : buf.index(b"\n", pos)])[2]


def _seek(buf: bytes, target: int) -> int:
    """시각 ≥ target 인 첫 줄 시작 위치 (buf 는 완전한 줄들, 시각 비감소 가정).

    불변식: lo 앞에서 시작하는 줄은 모두 시각 < target, hi 이후에서 시작하는 줄은 모두 ≥ target.
    """
    lo, hi = 0, len(buf)
    while lo < hi:
        mid = (lo + hi) // 2
        p = lo if mid <= lo else buf.index(b"\n", mid - 1) + 1
        if p >= hi:  # [mid, hi) 에 줄 시작이 없다 → lo 줄을 직접 본다
            if _ts_line(buf, lo) >= target:
                return lo
            lo = buf.index(b"\n", lo) + 1
            continue
        if _ts_line(buf, p) < target:
            lo = buf.index(b"\n", p) + 1
        else:
            hi = p
    return lo


def _lines(buf: bytes, pos: int) -> Iterator[tuple[int, bytes]]:
    n = len(buf)
    while pos < n:
        e = buf.index(b"\n", pos)
        yield e + 1, buf[pos:e]
        pos = e + 1


def _full_chunk(buf: bytes, ex: Extract, prev: tuple[int, int] | None) -> tuple[int, int]:
    """id 가 안 맞는 덩어리: 전수로 빈 곳 · 중복 · 역행 위치를 찾는다."""
    last = prev
    for _, ln in _lines(buf, 0):
        i, _q, ts, _b = parse(ln)
        if last is not None:
            if i != last[0] + 1:
                ex.gaps.append((last[1], ts, i - last[0] - 1))
            if ts < last[1]:
                ex.nonmono += 1
        last = (i, ts)
    assert last is not None
    return last


def _chunks(fh: io.BufferedIOBase) -> Iterator[bytes]:
    rest = b""
    while True:
        data = fh.read(CHUNK)
        if not data:
            if rest.strip():
                yield rest if rest.endswith(b"\n") else rest + b"\n"
            return
        data = rest + data
        cut = data.rfind(b"\n") + 1
        rest = data[cut:]
        if cut:
            yield data[:cut]


def extract(path: Path) -> Extract:
    ex = Extract()
    h = hashlib.sha256()
    with path.open("rb") as raw:
        for blk in iter(lambda: raw.read(1 << 22), b""):
            h.update(blk)
    ex.sha256 = h.hexdigest()
    prev: tuple[int, int] | None = None  # (마지막 id, 시각)
    with zipfile.ZipFile(path) as zf, zf.open(zf.namelist()[0]) as fh:
        first = True
        for buf in _chunks(fh):  # type: ignore[arg-type]
            if first:
                first = False
                if not buf[:1].isdigit():
                    ex.header = True
                    buf = buf[buf.index(b"\n") + 1 :]
                    if not buf:
                        continue
            n = buf.count(b"\n")
            a_end = buf.index(b"\n")
            a_id, _q, a_ts, _b = parse(buf[:a_end])
            z_start = buf.rfind(b"\n", 0, len(buf) - 1) + 1
            z_id, _q, z_ts, _b = parse(buf[z_start:-1])
            if ex.first_id < 0:
                ex.first_id, ex.first_ts = a_id, a_ts
                if int(buf[:a_end].split(b",")[5]) > 10**14:
                    ex.ts_unit = "us"
            ex.rows += n
            ok = (z_id - a_id + 1 == n) and (prev is None or a_id == prev[0] + 1) and z_ts >= a_ts
            if not ok:
                prev = _full_chunk(buf, ex, prev)
            else:
                if prev is not None and a_ts < prev[1]:
                    ex.nonmono += 1
                prev = (z_id, z_ts)
            # 창 · 대조 분
            w0 = (a_ts // (5 * MIN)) * 5 * MIN
            for w in range(w0, z_ts + 1, 5 * MIN):
                if w + WIN <= a_ts or w > z_ts:
                    continue
                pos = _seek(buf, w)
                is_check = w % 3_600_000 == 0
                stop = w + (MIN if is_check else WIN)
                acc = ex.windows.setdefault(w, [0.0, 0.0, 0])
                chk = ex.check_min.setdefault(w, [0.0, 0.0, 0]) if is_check else None
                last_ts = -1
                for _e, ln in _lines(buf, pos):
                    _i, q, ts, ibm = parse(ln)
                    if ts >= stop:
                        break
                    if ts < last_ts:
                        ex.nonmono += 1
                    last_ts = ts
                    if ts < w + WIN:
                        acc[1 if ibm else 0] += q
                        acc[2] += 1
                    if chk is not None:
                        chk[0] += q
                        chk[1] += 0.0 if ibm else q
                        chk[2] += 1
    ex.last_id, ex.last_ts = prev if prev else (-1, -1)
    return ex


def extract_full(path: Path) -> Extract:
    """검산용 전수 파싱 (표본 파일만)."""
    ex = Extract()
    last: tuple[int, int] | None = None
    with zipfile.ZipFile(path) as zf, zf.open(zf.namelist()[0]) as fh:
        for raw in fh:
            if not raw[:1].isdigit():
                ex.header = True
                continue
            i, q, ts, ibm = parse(raw.rstrip(b"\r\n"))
            ex.rows += 1
            if ex.first_id < 0:
                ex.first_id, ex.first_ts = i, ts
            if last is not None:
                if i != last[0] + 1:
                    ex.gaps.append((last[1], ts, i - last[0] - 1))
                if ts < last[1]:
                    ex.nonmono += 1
            last = (i, ts)
            m = ts // MIN * MIN
            if m % (5 * MIN) == 0 and ts - m < WIN:
                acc = ex.windows.setdefault(m, [0.0, 0.0, 0])
                acc[1 if ibm else 0] += q
                acc[2] += 1
            if m % 3_600_000 == 0:
                chk = ex.check_min.setdefault(m, [0.0, 0.0, 0])
                chk[0] += q
                chk[1] += 0.0 if ibm else q
                chk[2] += 1
    if last:
        ex.last_id, ex.last_ts = last
    return ex


# ------------------------------------------------------------------ 1 분봉 (taker 매수량)


def klines_1m(symbol: str) -> dict[int, tuple[float, float, float]]:
    """분 ms → (시가, 거래량, taker 매수량). 공개 아카이브 USD-M 1 분봉 월 파일."""
    out: dict[int, tuple[float, float, float]] = {}
    d = VISION / "um" / symbol / "1m"
    for z in sorted(d.glob("*.zip")):
        if z.stem.split("-", 2)[2] > LAST_MONTH:
            continue
        with zipfile.ZipFile(z) as zf:
            for line in io.TextIOWrapper(zf.open(zf.namelist()[0]), "utf-8"):
                p = line.split(",")
                if not p[0].isdigit():
                    continue
                out[int(p[0])] = (float(p[1]), float(p[5]), float(p[9]))
    return out


def _get(url: str, dest: Path, tries: int = 5) -> None:
    import time

    for k in range(tries):
        try:
            tmp = dest.with_suffix(".part")
            with urllib.request.urlopen(url, timeout=120) as r, tmp.open("wb") as fh:
                while blk := r.read(1 << 20):
                    fh.write(blk)
            tmp.replace(dest)
            return
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(10 * (k + 1))


def fetch_klines(symbol: str) -> None:
    d = VISION / "um" / symbol / "1m"
    d.mkdir(parents=True, exist_ok=True)
    for mo in months():
        f = d / f"{symbol}-1m-{mo}.zip"
        if not f.exists():
            _get(f"{BASE_URL}/monthly/klines/{symbol}/1m/{f.name}", f)


def _checksum(url: str) -> str:
    with urllib.request.urlopen(url + ".CHECKSUM", timeout=60) as r:
        return str(r.read().decode("utf-8").split()[0])


def fetch_one(symbol: str, mo: str) -> str:
    out = STORE / "agg" / f"{symbol}-{mo}.pkl"
    if out.exists():
        return f"{symbol} {mo} 있음"
    tmpdir = STORE / "tmp"
    tmpdir.mkdir(parents=True, exist_ok=True)
    name = f"{symbol}-aggTrades-{mo}.zip"
    url = f"{BASE_URL}/monthly/aggTrades/{symbol}/{name}"
    z = tmpdir / name
    _get(url, z)
    want = _checksum(url)
    ex = extract(z)
    if ex.sha256 != want:
        z.unlink()
        raise RuntimeError(f"{name} checksum 불일치")
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("wb") as fh:
        pickle.dump(asdict(ex), fh)  # 일반 dict 로 (모듈 경로와 무관하게 읽히도록)
    z.unlink()
    return f"{symbol} {mo} rows {ex.rows:,} gaps {len(ex.gaps)} 창 {len(ex.windows)}"


def fetch(workers: int = 4) -> int:
    from concurrent.futures import ProcessPoolExecutor, as_completed

    for s in SYMBOLS:
        fetch_klines(s)
    jobs = [(s, mo) for mo in months() for s in SYMBOLS]
    with ProcessPoolExecutor(workers) as pool:
        futs = {pool.submit(fetch_one, s, mo): (s, mo) for s, mo in jobs}
        for f in as_completed(futs):
            try:
                print(f.result(), flush=True)
            except Exception as exc:
                print("실패", futs[f], exc, flush=True)
    return 0


def patch_one(symbol: str, day: str) -> str:
    """월 파일 검증 실패한 날을 일 파일로 다시 집계 (같은 기준으로 검증)."""
    from scripts.run_qflow_216_stage1 import DAY_TOL, day_check, kline_arrays

    out = STORE / "agg_daily" / f"{symbol}-{day}.pkl"
    if out.exists():
        return f"{symbol} {day} 있음"
    tmpdir = STORE / "tmp"
    tmpdir.mkdir(parents=True, exist_ok=True)
    name = f"{symbol}-aggTrades-{day}.zip"
    url = f"{BASE_URL}/daily/aggTrades/{symbol}/{name}"
    z = tmpdir / name
    try:
        _get(url, z)
    except Exception as exc:
        rec = {"verified": False, "error": str(exc)[:200], "gaps": [], "windows": {}}
    else:
        want = _checksum(url)
        ex = extract(z)
        z.unlink()
        if ex.sha256 != want:
            raise RuntimeError(f"{name} checksum 불일치")
        rec = asdict(ex)
        have, vol, _tb = _KL.setdefault(symbol, kline_arrays(symbol))
        rel = day_check(rec, have, vol)
        d_ms = _ms(date.fromisoformat(day))
        day_rel = rel.get(d_ms)
        rec["day_rel"] = day_rel
        rec["verified"] = not ex.gaps and day_rel is not None and day_rel <= DAY_TOL
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("wb") as fh:
        pickle.dump(rec, fh)
    gaps_n = len(rec["gaps"]) if isinstance(rec["gaps"], list) else 0
    return f"{symbol} {day} 검증 {rec['verified']} 차 {rec.get('day_rel')} gaps {gaps_n}"


_KL: dict[str, Any] = {}


def patch() -> int:
    from scripts.run_qflow_216_stage1 import kline_arrays, load_agg, suspect_days

    for s in SYMBOLS:
        have, vol, tb = kline_arrays(s)
        _KL[s] = (have, vol, tb)
        days = suspect_days(load_agg(s), have, vol)
        print(s, "의심일", len(days), flush=True)
        for d in days:
            print(
                patch_one(s, datetime.fromtimestamp(d / 1000, UTC).date().isoformat()), flush=True
            )
    return 0


# ------------------------------------------------------------------ 표본 검산


def pilot() -> int:
    d = VISION / "um_agg_pilot"
    res: dict[str, Any] = {}
    for z in sorted(d.glob("*aggTrades*.zip")):
        fast, full = extract(z), extract_full(z)
        wdiff = max(
            (
                max(abs(a - b) for a, b in zip(fast.windows.get(k, [0, 0, 0]), v, strict=True))
                for k, v in full.windows.items()
            ),
            default=0.0,
        )
        extra_keys = [k for k, v in fast.windows.items() if k not in full.windows and v[2] > 0]
        cdiff = max(
            (
                max(abs(a - b) for a, b in zip(fast.check_min.get(k, [0, 0, 0]), v, strict=True))
                for k, v in full.check_min.items()
            ),
            default=0.0,
        )
        res[z.name] = {
            "rows_fast_full": [fast.rows, full.rows],
            "ids": [full.first_id, full.last_id],
            "id_span_eq_rows": full.last_id - full.first_id + 1 == full.rows,
            "gaps_fast_full": [len(fast.gaps), len(full.gaps)],
            "nonmono_full": full.nonmono,
            "windows_fast_full": [len(fast.windows), len(full.windows)],
            "window_max_abs_diff": wdiff,
            "fast_only_nonempty_windows": len(extra_keys),
            "check_max_abs_diff": cdiff,
            "header": full.header,
            "first_ts": datetime.fromtimestamp(full.first_ts / 1000, UTC).isoformat(),
            "last_ts": datetime.fromtimestamp(full.last_ts / 1000, UTC).isoformat(),
        }
        print(z.name, res[z.name], flush=True)
    return 0


def main(argv: list[str]) -> int:
    cmd = argv[:1]
    if cmd == ["pilot"]:
        return pilot()
    if cmd == ["fetch"]:
        return fetch(int(argv[1]) if len(argv) > 1 else 4)
    if cmd == ["patch"]:
        return patch()
    if cmd == ["audit"]:
        from scripts.run_qflow_216_stage1 import audit

        r = audit()
        REPORT.mkdir(parents=True, exist_ok=True)
        (REPORT / "audit.json").write_text(
            json.dumps(r, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
        )
        print(json.dumps(r, ensure_ascii=False, indent=1, default=str))
        return 0
    if cmd == ["stage1"]:
        from scripts.run_qflow_216_stage1 import stage1

        r = stage1()
        print(
            json.dumps(
                {
                    k: r[k]
                    for k in ("drops", "diffs_mean_net_bp", "expand_to_stage2", "conclusion_stage1")
                },
                ensure_ascii=False,
                indent=1,
            )
        )
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))


__all__ = ["Extract", "extract", "json"]
