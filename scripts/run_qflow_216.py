# ruff: noqa: E501 — 한국어 설명이 길다
"""문서 216 — 15 분 경계 첫 10 초 선물 체결 불균형 → 12 시간 뒤 선물 수익. 후향 파일럿. 주문 없음.

언제 실행: 사람이 직접 (예약 작업 없음). 연구 216 은 2026-10-11 에 1 단계로 끝났다. 아래 추출 부분이
           체결 기록 감사(잘림 · 중복 · 빈 곳 찾기)의 핵심이다.
읽고 쓰는 것: 바이낸스 공개 아카이브 aggTrades zip (받아서 ``data/research/qflow_216/tmp`` 에 잠깐 둠) →
              집계 pickle ``data/research/qflow_216/agg*/`` · 보고서 ``reports/qflow_216/``.

자료: 바이낸스 공개 아카이브 USD-M aggTrades 월 파일(받으면 바로 집계하고 지움), USD-M 1 분봉(taker 매수량 포함),
210 캐시(1 분봉 · funding · mark).

사용::

    python -m scripts.run_qflow_216 pilot          # 표본 파일: 빠른 추출 = 전수 파싱 대조, 1 분봉 거래량 대조
    python -m scripts.run_qflow_216 fetch          # 2020-01 ~ 2026-08 aggTrades 받기 → 집계 → 원본 삭제 (이어받기 가능)
    python -m scripts.run_qflow_216 audit          # 집계 자료 품질 (손익 없음)
    python -m scripts.run_qflow_216 stage1         # 1 단계 비용 문턱 점검 (계약 커밋 뒤 한 번)

aggTrades CSV 한 줄 = ``agg_trade_id, price, quantity, first_trade_id, last_trade_id, transact_time, is_buyer_maker``
(오래된 파일은 머리줄이 없다). ``is_buyer_maker = true`` 는 매수자가 메이커 = **공격적 매도** 라는 뜻이다.
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
MIN = 60_000  # 1 분 (밀리초)
WIN = 10_000  # 첫 10 초
DAY = 86_400_000  # 하루 (밀리초)
FIRST_MONTH = "2020-01"
LAST_MONTH = "2026-08"  # 2026-09 이후는 받지도 열지도 않는다(보존)
CHUNK = 1 << 25  # 압축 푼 32 MB 씩
_HASH_BLOCK = 1 << 22  # 파일 해시를 잴 때 한 번에 읽는 크기 (4 MB)
_WINDOW_STEP = 5 * MIN  # 창은 매 5 분 경계마다
_HOUR_MS = 3_600_000  # 매시 00 분은 공식 1 분봉과 대조하는 분
_MICROSECOND_THRESHOLD = 10**14  # 시각이 이보다 크면 마이크로초 표기 (ms 는 2026 년에 약 1.8e12)


def _ms(d: date) -> int:
    """[쓰임] UTC 날짜 → 그날 00:00 의 epoch 밀리초."""
    return int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp() * 1000)


def months(a: str = FIRST_MONTH, b: str = LAST_MONTH) -> list[str]:
    """[쓰임] ``"YYYY-MM"`` 달 목록 — ``a`` 부터 ``b`` 까지 (양끝 포함). 기본은 연구 216 의 전 기간.
    [이유] 2026-09 이후(``LAST_MONTH`` 다음)는 전향 확인용으로 남겨 두어 받지도 열지도 않는다."""
    year, month = map(int, a.split("-"))
    month_list = []
    while f"{year:04d}-{month:02d}" <= b:
        month_list.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return month_list


# ------------------------------------------------------------------ aggTrades 추출


@dataclass
class Extract:
    """한 파일의 집계. 창 = 매 5 분(분 % 5 == 0)의 [분 시작, +10 초). 대조 분 = 매시 00 분 전체.

    ⚠️ 필드 이름은 pickle 로 저장된다(``asdict``) — 바꾸면 예전 집계 파일을 못 읽는다.
    """

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


def _parse_trade_line(line: bytes) -> tuple[int, float, int, bool]:
    """[쓰임] CSV 한 줄 → ``(agg 체결 번호, 수량, 체결 시각 ms, is_buyer_maker)``.
    [이유] 수십억 줄을 읽으므로 csv 모듈 대신 바이트 그대로 쪼갠다. 마이크로초 표기 시각은 ms 로 맞춘다.
           ``is_buyer_maker = true`` → 매수자가 메이커 = 공격적 매도."""
    columns = line.split(b",")
    trade_ms = int(columns[5])
    if trade_ms > _MICROSECOND_THRESHOLD:  # 마이크로초 표기면 ms 로
        trade_ms //= 1000
    return int(columns[0]), float(columns[2]), trade_ms, columns[6][:1] in (b"t", b"T")


def _line_time_ms(chunk: bytes, line_start: int) -> int:
    """[쓰임] ``line_start`` 에서 시작하는 줄의 체결 시각(ms)."""
    return _parse_trade_line(chunk[line_start : chunk.index(b"\n", line_start)])[2]


def _find_first_line_at_or_after(chunk: bytes, target_ms: int) -> int:
    """[쓰임] 체결 시각 ≥ ``target_ms`` 인 첫 줄의 시작 위치를 이분 탐색으로 찾는다.
    [이유] 덩어리 하나에 수십만 줄이 있어 창(10 초)마다 처음부터 훑으면 너무 느리다.
           ``chunk`` 는 완전한 줄들이고 시각이 줄어들지 않는다고 가정한다 (역행은 따로 센다).

    불변식: lo 앞에서 시작하는 줄은 모두 시각 < target, hi 이후에서 시작하는 줄은 모두 ≥ target.
    """
    lo, hi = 0, len(chunk)
    while lo < hi:
        mid = (lo + hi) // 2
        line_start = lo if mid <= lo else chunk.index(b"\n", mid - 1) + 1
        if line_start >= hi:  # [mid, hi) 에 줄 시작이 없다 → lo 줄을 직접 본다
            if _line_time_ms(chunk, lo) >= target_ms:
                return lo
            lo = chunk.index(b"\n", lo) + 1
            continue
        if _line_time_ms(chunk, line_start) < target_ms:
            lo = chunk.index(b"\n", line_start) + 1
        else:
            hi = line_start
    return lo


def _iter_lines(chunk: bytes, start: int) -> Iterator[tuple[int, bytes]]:
    """[쓰임] ``start`` 부터 한 줄씩 ``(다음 줄 시작 위치, 줄 바이트)`` 를 돌려준다 (줄바꿈 제외)."""
    chunk_length = len(chunk)
    while start < chunk_length:
        line_end = chunk.index(b"\n", start)
        yield line_end + 1, chunk[start:line_end]
        start = line_end + 1


def _scan_chunk_line_by_line(
    chunk: bytes, result: Extract, previous_trade: tuple[int, int] | None
) -> tuple[int, int]:
    """[쓰임] 체결 번호가 안 맞는 덩어리를 한 줄씩 훑어 빈 곳 · 중복 · 시각 역행을 모두 적고,
              마지막 체결 ``(번호, 시각)`` 을 돌려준다.
    [이유] 번호가 맞는 덩어리는 첫 줄 · 끝 줄만 보면 되지만(빠름), 안 맞으면 어디가 비었는지 정확한
           위치를 남겨야 나중에 그 날을 일 파일로 바꾸거나 뺄 수 있다."""
    last_trade = previous_trade
    for _, line in _iter_lines(chunk, 0):
        trade_id, _quantity, trade_ms, _is_buyer_maker = _parse_trade_line(line)
        if last_trade is not None:
            if trade_id != last_trade[0] + 1:
                result.gaps.append((last_trade[1], trade_ms, trade_id - last_trade[0] - 1))
            if trade_ms < last_trade[1]:
                result.nonmono += 1
        last_trade = (trade_id, trade_ms)
    assert last_trade is not None
    return last_trade


def _read_complete_line_chunks(file_handle: io.BufferedIOBase) -> Iterator[bytes]:
    """[쓰임] 압축 푼 내용을 약 ``CHUNK`` 바이트씩, **줄이 잘리지 않게** 끊어 돌려준다.
    [이유] 월 파일 하나가 압축 풀면 수 GB 라 통째로 메모리에 올릴 수 없다. 덩어리 끝의 덜 끝난 줄은
           다음 덩어리 앞에 붙이고, 마지막 줄에 줄바꿈이 없으면 붙여 준다."""
    leftover = b""
    while True:
        data = file_handle.read(CHUNK)
        if not data:
            if leftover.strip():
                yield leftover if leftover.endswith(b"\n") else leftover + b"\n"
            return
        data = leftover + data
        cut = data.rfind(b"\n") + 1
        leftover = data[cut:]
        if cut:
            yield data[:cut]


def _file_sha256(path: Path) -> str:
    """[쓰임] 파일 전체의 SHA-256 (4 MB 씩 읽는다) — 공식 ``.CHECKSUM`` 과 대조하는 값."""
    file_hash = hashlib.sha256()
    with path.open("rb") as raw_file:
        for block in iter(lambda: raw_file.read(_HASH_BLOCK), b""):
            file_hash.update(block)
    return file_hash.hexdigest()


def _add_chunk_windows(
    chunk: bytes, result: Extract, chunk_first_ms: int, chunk_last_ms: int
) -> None:
    """[쓰임] 덩어리 안에 걸친 5 분 경계마다 첫 10 초 창(공격 매수량 · 매도량 · 체결 수)을 더하고,
              매시 00 분이면 그 1 분 전체(거래량 · 공격 매수량 · 체결 수)도 더한다.
    [이유] 창이 두 덩어리에 걸쳐도 같은 칸(``setdefault``)에 더하므로 합계가 맞다. 창 안에서 시각이
           거꾸로 가면 역행으로 센다."""
    first_window_ms = (chunk_first_ms // _WINDOW_STEP) * _WINDOW_STEP
    for window_ms in range(first_window_ms, chunk_last_ms + 1, _WINDOW_STEP):
        if window_ms + WIN <= chunk_first_ms or window_ms > chunk_last_ms:
            continue
        start = _find_first_line_at_or_after(chunk, window_ms)
        is_check_minute = window_ms % _HOUR_MS == 0
        scan_end_ms = window_ms + (MIN if is_check_minute else WIN)
        window_totals = result.windows.setdefault(window_ms, [0.0, 0.0, 0])
        minute_totals = (
            result.check_min.setdefault(window_ms, [0.0, 0.0, 0]) if is_check_minute else None
        )
        previous_line_ms = -1
        for _, line in _iter_lines(chunk, start):
            _trade_id, quantity, trade_ms, is_buyer_maker = _parse_trade_line(line)
            if trade_ms >= scan_end_ms:
                break
            if trade_ms < previous_line_ms:
                result.nonmono += 1
            previous_line_ms = trade_ms
            if trade_ms < window_ms + WIN:
                window_totals[1 if is_buyer_maker else 0] += quantity
                window_totals[2] += 1
            if minute_totals is not None:
                minute_totals[0] += quantity
                minute_totals[1] += 0.0 if is_buyer_maker else quantity
                minute_totals[2] += 1


def extract(path: Path) -> Extract:
    """[쓰임] aggTrades zip 하나를 빠르게 집계한다 — 파일 해시, 행 수, 첫 · 끝 체결, 빈 곳 · 중복 · 역행,
              5 분 경계 첫 10 초 창, 매시 00 분 대조 분.
    [이유] 월 파일 160 개(약 86GB)를 받아 바로 집계하고 지우므로, 한 번 읽을 때 감사에 필요한 것을 다
           뽑아야 한다. 덩어리마다 "번호 차이 + 1 = 줄 수" 이고 앞 덩어리와 번호가 이어지면 깨끗한
           덩어리로 보고 첫 · 끝 줄만 본다. 아니면 한 줄씩 훑어 정확한 위치를 남긴다 (2020 BTC 월 파일
           2,000 만 행 잘림 · 2022-09 중복을 이렇게 찾았다)."""
    result = Extract()
    result.sha256 = _file_sha256(path)
    previous_trade: tuple[int, int] | None = None  # (마지막 id, 시각)
    with zipfile.ZipFile(path) as archive, archive.open(archive.namelist()[0]) as file_handle:
        is_first_chunk = True
        for chunk in _read_complete_line_chunks(file_handle):  # type: ignore[arg-type]
            # 1 단계: 첫 덩어리의 머리줄 떼기
            if is_first_chunk:
                is_first_chunk = False
                if not chunk[:1].isdigit():
                    result.header = True
                    chunk = chunk[chunk.index(b"\n") + 1 :]
                    if not chunk:
                        continue

            # 2 단계: 덩어리의 첫 줄 · 끝 줄로 행 수 · 번호 연속을 확인
            line_count = chunk.count(b"\n")
            first_line_end = chunk.index(b"\n")
            first_id, _quantity, first_ms, _is_buyer_maker = _parse_trade_line(
                chunk[:first_line_end]
            )
            last_line_start = chunk.rfind(b"\n", 0, len(chunk) - 1) + 1
            last_id, _quantity, last_ms, _is_buyer_maker = _parse_trade_line(
                chunk[last_line_start:-1]
            )
            if result.first_id < 0:
                result.first_id, result.first_ts = first_id, first_ms
                if int(chunk[:first_line_end].split(b",")[5]) > _MICROSECOND_THRESHOLD:
                    result.ts_unit = "us"
            result.rows += line_count
            chunk_is_clean = (
                (last_id - first_id + 1 == line_count)
                and (previous_trade is None or first_id == previous_trade[0] + 1)
                and last_ms >= first_ms
            )

            # 3 단계: 깨끗하지 않으면 한 줄씩 훑어 빈 곳 · 중복 · 역행 위치를 남긴다
            if not chunk_is_clean:
                previous_trade = _scan_chunk_line_by_line(chunk, result, previous_trade)
            else:
                if previous_trade is not None and first_ms < previous_trade[1]:
                    result.nonmono += 1
                previous_trade = (last_id, last_ms)

            # 4 단계: 창 · 대조 분 더하기
            _add_chunk_windows(chunk, result, first_ms, last_ms)
    result.last_id, result.last_ts = previous_trade if previous_trade else (-1, -1)
    return result


def extract_full(path: Path) -> Extract:
    """[쓰임] 검산용 전수 파싱 — 한 줄씩 전부 읽어 :func:`extract` 와 같은 집계를 만든다 (표본 파일만).
    [이유] 빠른 추출(이분 탐색 · 덩어리 단위 판단)이 맞는지 확인하는 정답지다 (느려서 표본만)."""
    result = Extract()
    last_trade: tuple[int, int] | None = None
    with zipfile.ZipFile(path) as archive, archive.open(archive.namelist()[0]) as file_handle:
        for raw_line in file_handle:
            if not raw_line[:1].isdigit():
                result.header = True
                continue
            trade_id, quantity, trade_ms, is_buyer_maker = _parse_trade_line(
                raw_line.rstrip(b"\r\n")
            )
            result.rows += 1
            if result.first_id < 0:
                result.first_id, result.first_ts = trade_id, trade_ms
            if last_trade is not None:
                if trade_id != last_trade[0] + 1:
                    result.gaps.append((last_trade[1], trade_ms, trade_id - last_trade[0] - 1))
                if trade_ms < last_trade[1]:
                    result.nonmono += 1
            last_trade = (trade_id, trade_ms)
            minute_ms = trade_ms // MIN * MIN
            if minute_ms % _WINDOW_STEP == 0 and trade_ms - minute_ms < WIN:
                window_totals = result.windows.setdefault(minute_ms, [0.0, 0.0, 0])
                window_totals[1 if is_buyer_maker else 0] += quantity
                window_totals[2] += 1
            if minute_ms % _HOUR_MS == 0:
                minute_totals = result.check_min.setdefault(minute_ms, [0.0, 0.0, 0])
                minute_totals[0] += quantity
                minute_totals[1] += 0.0 if is_buyer_maker else quantity
                minute_totals[2] += 1
    if last_trade:
        result.last_id, result.last_ts = last_trade
    return result


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
