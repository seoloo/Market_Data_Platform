"""합성 Bronze 만들기 — 바이낸스 아카이브와 같은 모양, 결함을 정해진 자리에, 정답지와 함께.

언제 쓰나: 키 · 큰 내려받기 없이 파이프라인을 돌려 볼 때 (보통 :mod:`mdp.sample.demo` 가 부른다)::

    python -m mdp.sample.generate --root data/sample

읽고 쓰는 것 (``--root`` 아래, 늘 같은 씨앗이라 몇 번 만들어도 같은 파일 · 같은 SHA-256):
- ``bronze/data/futures/um/daily/aggTrades/DEMOUSDT/DEMOUSDT-aggTrades-2024-01-0{1,2,3}.zip``
- ``bronze/data/futures/um/daily/klines/DEMOUSDT/1m/DEMOUSDT-1m-2024-01-0{1,2,3}.zip``
- ``bronze/manifest.csv`` — 체크섬 목록 (진짜 Bronze 와 같은 형식)
- ``expected.json`` — 정답지: 파이프라인이 찾아야 할 숫자

시장: 3 일 · 매분 체결 5 ~ 24 묶음(빈 분 없음), 가격은 0.1 단위로 걷는다. 1 분봉은
**결함을 넣기 전** 진짜 체결로 계산한다(바이낸스가 올린 공식 봉 역할).

결함 (2 일째 체결 파일에만 — 1 · 3 일째는 깨끗):

| 결함 | 자리 | 파이프라인이 해야 할 일 |
|---|---|---|
| 빈 곳 | 10:00 분의 3 ~ 7 번째 묶음 5 개를 뺌 | Silver 빈 곳 표에 (앞 번호, 뒤 번호, 5) |
| 같은 내용 중복 | 11:00 분 처음 3 개를 한 번씩 더 | 하나만 남김 (감사 표 중복 줄 수에 3) |
| 다른 내용 중복 | 15:00 분 4 번째를 가격만 바꿔 한 번 더 | 두 줄 다 격리 · 중복 줄 수에 1 |
| 못 읽는 줄 | 12:00 분 사이에 한 줄 | 격리 (``parse_error``) |
| 기간 밖 줄 | 파일 끝에 3 일째 첫 체결 | 격리 (``outside_period``) |

[이유] 진짜 데이터는 결함이 '거의 없어서' (2024-03 은 0 개) 감사 규칙이 일하는 것을 보여 줄 수 없다.
       정답을 아는 데이터를 만들어, 감사가 넣은 결함을 **빠짐없이 · 더하지도 않고** 찾는지 숫자로
       맞춘다. 이름을 ``DEMOUSDT`` 로 해 진짜 종목과 섞이지 않게 했다.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import random
import zipfile
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

from mdp.bronze.archive import ArchiveFile, Dataset, Granularity

__all__ = ["DAYS", "SYMBOL", "Trade", "generate", "main"]

SYMBOL: Final = "DEMOUSDT"
DAYS: Final = (date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3))
SEED: Final = 20240101
FIRST_AGG_ID: Final = 1_000_000
AGG_HEADER: Final = (
    "agg_trade_id,price,quantity,first_trade_id,last_trade_id,transact_time,is_buyer_maker"
)
KLINE_HEADER: Final = (
    "open_time,open,high,low,close,volume,close_time,quote_volume,count,"
    "taker_buy_volume,taker_buy_quote_volume,ignore"
)
STREAM_GAP: Final = (
    datetime(2024, 1, 1, 12, 0, 30, tzinfo=UTC),
    datetime(2024, 1, 1, 12, 5, 30, tzinfo=UTC),
)
"""스트림 시연(선택)에서 보내지 않을 구간 — 1 일째 12:00:30 ~ 12:05:30 (PC 잠듦 흉내)."""


@dataclass(frozen=True, slots=True)
class Trade:
    """[쓰임] 합성 체결 묶음 하나 (아카이브 CSV 한 줄)."""

    agg_id: int
    price: Decimal
    quantity: Decimal
    first_id: int
    last_id: int
    time_ms: int
    is_buyer_maker: bool

    def line(self, price: Decimal | None = None) -> str:
        """[쓰임] CSV 한 줄 (``price`` 를 주면 가격만 바꿔서 — 다른 내용 중복용)."""
        maker = "true" if self.is_buyer_maker else "false"
        shown = self.price if price is None else price
        ids = f"{self.first_id},{self.last_id}"
        return f"{self.agg_id},{shown},{self.quantity},{ids},{self.time_ms},{maker}"


def _ms(day: date, minute: int = 0) -> int:
    """[쓰임] 그날 0 시(UTC)부터 ``minute`` 분 뒤의 ms."""
    start = datetime(day.year, day.month, day.day, tzinfo=UTC) + timedelta(minutes=minute)
    return int(start.timestamp() * 1000)


def _market(rng: random.Random) -> dict[date, list[list[Trade]]]:
    """[쓰임] 진짜(결함 없는) 체결 — 날마다 1,440 분, 분마다 묶음 목록."""
    price, agg_id, trade_id = Decimal("42000.0"), FIRST_AGG_ID, 5_000_000
    market: dict[date, list[list[Trade]]] = {}
    for day in DAYS:
        minutes = []
        for minute in range(1440):
            count = 20 if minute in (600, 660, 900) else rng.randint(5, 24)  # 결함 넣을 분은 넉넉히
            offsets = sorted(rng.randrange(60_000) for _ in range(count))
            trades = []
            for offset in offsets:
                price += Decimal(rng.randint(-5, 5)) / 10
                fills = rng.choice((1, 1, 1, 2, 3))
                trades.append(
                    Trade(
                        agg_id,
                        price,
                        Decimal(rng.randint(1, 2000)) / 1000,
                        trade_id,
                        trade_id + fills - 1,
                        _ms(day, minute) + offset,
                        rng.random() < 0.5,
                    )
                )
                agg_id += 1
                trade_id += fills
            minutes.append(trades)
        market[day] = minutes
    return market


def _kline_rows(day: date, minutes: list[list[Trade]]) -> list[str]:
    """[쓰임] 진짜 체결 → 공식 1 분봉 줄 (바이낸스 칸 순서)."""
    rows = []
    for minute, trades in enumerate(minutes):
        start = _ms(day, minute)
        prices = [t.price for t in trades]
        volume = sum((t.quantity for t in trades), Decimal(0))
        quote = sum((t.price * t.quantity for t in trades), Decimal(0))
        taker = [t for t in trades if not t.is_buyer_maker]
        rows.append(
            ",".join(
                str(v)
                for v in (
                    start,
                    prices[0],
                    max(prices),
                    min(prices),
                    prices[-1],
                    volume,
                    start + 59_999,
                    quote,
                    sum(t.last_id - t.first_id + 1 for t in trades),
                    sum((t.quantity for t in taker), Decimal(0)),
                    sum((t.price * t.quantity for t in taker), Decimal(0)),
                    0,
                )
            )
        )
    return rows


def _defective_day(
    minutes: list[list[Trade]], next_day_first: Trade
) -> tuple[list[str], dict[str, Any]]:
    """[쓰임] 2 일째 체결 파일 줄(결함 넣음)과, 그 결함으로 정해지는 정답."""
    gap = minutes[600][2:7]
    removed = {t.agg_id for t in gap}
    duplicated = {t.agg_id for t in minutes[660][:3]}
    conflict = minutes[900][3]
    lines = [AGG_HEADER]
    for minute, trades in enumerate(minutes):
        for trade in trades:
            if trade.agg_id in removed:
                continue
            lines.append(trade.line())
            if trade.agg_id in duplicated:
                lines.append(trade.line())
            if trade.agg_id == conflict.agg_id:
                lines.append(trade.line(price=trade.price + 1))
        if minute == 720:
            lines.append("corrupt,line,from,the,sample")
    lines.append(next_day_first.line())
    lost = [*gap, conflict]  # 깨끗한 체결에서 빠지는 진짜 묶음
    return lines, {
        "gap": {
            "prev_id": min(removed) - 1,
            "next_id": max(removed) + 1,
            "missing_ids": len(removed),
        },
        "lost_agg_ids": sorted(t.agg_id for t in lost),
        "lost_minutes_ms": sorted({t.time_ms - t.time_ms % 60_000 for t in lost}),
        "lost_volume": str(sum((t.quantity for t in lost), Decimal(0))),
    }


def _zip(path: Path, lines: list[str]) -> tuple[int, str]:
    """[쓰임] CSV 줄 → zip 하나(안의 CSV 이름 = zip 이름). (크기, SHA-256)을 돌려준다.
    [이유] zip 안 파일 시각을 고정해야 몇 번 만들어도 같은 바이트 · 같은 체크섬이 나온다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        info = zipfile.ZipInfo(path.with_suffix(".csv").name, date_time=(2024, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(info, "\n".join(lines) + "\n")
    data = buffer.getvalue()
    path.write_bytes(data)
    return len(data), hashlib.sha256(data).hexdigest()


def generate(root: Path, seed: int = SEED) -> dict[str, Any]:
    """[쓰임] ``root`` 아래에 합성 Bronze · 체크섬 목록 · 정답지를 만들고 정답지를 돌려준다."""
    rng = random.Random(seed)
    market = _market(rng)
    bronze = root / "bronze"
    manifest: list[tuple[str, int, str]] = []
    defects: dict[str, Any] = {}
    for index, day in enumerate(DAYS):
        minutes = market[day]
        if index == 1:
            lines, defects = _defective_day(minutes, market[DAYS[2]][0][0])
        else:
            lines = [AGG_HEADER, *(t.line() for trades in minutes for t in trades)]
        for dataset, interval, rows in (
            (Dataset.AGG_TRADES, None, lines),
            (Dataset.KLINES, "1m", [KLINE_HEADER, *_kline_rows(day, minutes)]),
        ):
            file = ArchiveFile(dataset, SYMBOL, day, Granularity.DAILY, interval)
            size, sha256 = _zip(bronze.joinpath(*file.relative_path.split("/")), rows)
            manifest.append((file.relative_path, size, sha256))
    with (bronze / "manifest.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(["relative_path", "size_bytes", "sha256"])
        writer.writerows(sorted(manifest))

    true_trades = sum(len(trades) for day in DAYS for trades in market[day])
    gap_from, gap_to = (int(t.timestamp() * 1000) for t in STREAM_GAP)
    stream_lost = [
        t for trades in market[DAYS[0]] for t in trades if gap_from <= t.time_ms < gap_to
    ]
    expected: dict[str, Any] = {
        "symbol": SYMBOL,
        "month": "2024-01",
        "days": [f"{d:%Y-%m-%d}" for d in DAYS],
        "seed": seed,
        "true_agg_trades": true_trades,
        "silver": {
            "clean_rows": true_trades - len(defects["lost_agg_ids"]),
            "quarantine": {"parse_error": 1, "conflicting_duplicate": 2, "outside_period": 1},
            # 같은 번호가 또 나온 줄 = 같은 내용 3 + 다른 내용 1 (감사 표의 rows − distinct_ids)
            "duplicate_rows": 4,
            "gaps": [defects["gap"]],
            "time_reversed": 0,
            "suspect_days": [f"{DAYS[1]:%Y-%m-%d}"],
            "missing_ids_inside_day": {f"{DAYS[1]:%Y-%m-%d}": len(defects["lost_agg_ids"])},
        },
        "gold": {
            "bars": 1440 * len(DAYS),
            "kline_all_fields_equal": {
                f"{d:%Y-%m-%d}": 1440 - (len(defects["lost_minutes_ms"]) if i == 1 else 0)
                for i, d in enumerate(DAYS)
            },
            "lost_minutes_ms": defects["lost_minutes_ms"],
            "lost_volume": defects["lost_volume"],
        },
        "stream": {
            "date": f"{DAYS[0]:%Y-%m-%d}",
            "gap_from": STREAM_GAP[0].strftime("%Y-%m-%dT%H:%M:%S"),
            "gap_to": STREAM_GAP[1].strftime("%Y-%m-%dT%H:%M:%S"),
            "stream_gap_minutes": len({t.time_ms - t.time_ms % 60_000 for t in stream_lost}),
            "trades_not_received": len(stream_lost),
        },
    }
    (root / "expected.json").write_text(json.dumps(expected, indent=2) + "\n", encoding="utf-8")
    (root / "lake").mkdir(parents=True, exist_ok=True)
    return expected


def main(argv: list[str] | None = None) -> int:
    """[쓰임] 명령줄 실행 — 만든 파일 수와 정답지 요약을 찍는다."""
    parser = argparse.ArgumentParser(prog="python -m mdp.sample.generate")
    parser.add_argument("--root", type=Path, default=Path("data/sample"))
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args(argv)
    expected = generate(args.root, args.seed)
    summary = {k: expected[k] for k in ("symbol", "days", "true_agg_trades")}
    print(json.dumps({"event": "sample_generated", "root": str(args.root), **summary}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
