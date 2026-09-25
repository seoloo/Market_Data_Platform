"""데모 T/v2 현물 — 실거래 1단계 실행기 예행연습 (docs/research/55·57). **데모 모드 전용, 가짜 돈.**

실거래 1단계와 **같은 실행기**(:class:`LiveT2Runner`)를 데모 거래소에 붙여 매일 돌린다
(예정 21:12 KST).
기록은 메인 DB(``spot_orders`` mode='demo', ``paper_days`` strategy_id='t2_demo')와
``reports/demo_t2/<결정일>.json``. 알림은 텔레그램 + ``reports/demo_t2/outbox.log``.
긴급 정지 ``data/HALT`` 는 실거래와 똑같이 이 실행기도 멈춘다.

사용: ``python scripts/demo_t2_spot.py`` · ``python scripts/demo_t2_spot.py status``
"""

from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from auto_trading.config.secrets import (  # noqa: E402
    BinanceCredentials,
    load_telegram_credentials,
    read_env_file,
)
from auto_trading.exchange.binance_spot import BinanceSpotReadOnly  # noqa: E402
from auto_trading.exchange.binance_spot_demo import BinanceSpotDemo  # noqa: E402
from auto_trading.execution.live_t2 import LiveOptions, LiveT2Runner  # noqa: E402
from auto_trading.notify.channels import NotifyError, OutboxNotifier, TelegramNotifier  # noqa: E402
from auto_trading.storage.db import connect  # noqa: E402
from auto_trading.storage.migrations import apply_migrations  # noqa: E402
from auto_trading.storage.repositories.paper_days import PaperDay, PaperDayRepository  # noqa: E402
from auto_trading.storage.repositories.spot_orders import SpotOrderRepository  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        _reconfigure(encoding="utf-8", errors="replace")

OUT = REPO_ROOT / "reports" / "demo_t2"
DB = REPO_ROOT / "data" / "trading.db"
HALT = REPO_ROOT / "data" / "HALT"
OPTIONS = LiveOptions(mode="demo", strategy_id="t2_demo", tag="t2d")
LEGACY = OUT / "days.jsonl"
"""첫 실행(2026-09-25, 옛 스크립트)의 기록 — DB 로 한 번 옮긴다."""


def _date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d")


class Notify:
    def __init__(self) -> None:
        creds = load_telegram_credentials(REPO_ROOT / ".env")
        self._telegram = TelegramNotifier(creds) if creds else None
        self._outbox = OutboxNotifier(OUT / "outbox.log")

    def __call__(self, text: str) -> None:
        print(text, flush=True)
        self._outbox.send(text)
        if self._telegram is not None:
            try:
                self._telegram.send(text)
            except NotifyError as exc:
                self._outbox.send(f"(텔레그램 실패: {exc})")


def _import_legacy(days: PaperDayRepository) -> None:
    if not LEGACY.exists() or days.recent(OPTIONS.strategy_id, 1):
        return
    for line in LEGACY.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        days.insert(
            PaperDay(
                strategy_id=OPTIONS.strategy_id,
                decision_ms=int(row["decision_ms"]),
                status="OK",
                equity=Decimal(row["equity_before"]),
                holdings={},
                prices={},
                targets={s: t["weight"] for s, t in row["targets"].items()},
                executed=[
                    {k: str(v) for k, v in e.items() if k in ("symbol", "side", "qty", "avg_price")}
                    for e in row["executed"]
                ],
                note="옛 데모 스크립트 기록을 옮김 (2026-09-25 첫 실행)",
                created_ms=int(row["created_ms"]),
            )
        )


def run() -> int:
    env = read_env_file(REPO_ROOT / ".env")
    key, secret = env.get("BINANCE_TESTNET_API_KEY", ""), env.get("BINANCE_TESTNET_API_SECRET", "")
    if not key or not secret:
        raise SystemExit(".env 에 BINANCE_TESTNET_API_KEY / SECRET(데모 키)가 없다")
    conn = connect(DB)
    apply_migrations(conn)
    days = PaperDayRepository(conn)
    _import_legacy(days)
    runner = LiveT2Runner(
        BinanceSpotDemo(BinanceCredentials(key, secret)),
        BinanceSpotReadOnly(None),
        SpotOrderRepository(conn),
        days,
        options=OPTIONS,
        clock=lambda: int(time.time() * 1000),
        sleep=time.sleep,
        halt_path=HALT,
        notify=Notify(),
    )
    now = int(time.time() * 1000)
    day = runner.run_once(now)
    report: dict[str, Any] = {
        "decision_date": _date(day.decision_ms),
        "status": day.status,
        "note": day.note,
        "equity": str(day.equity),
        "targets": day.targets,
        "orders": [r.as_dict() for r in runner.reports],
        "at_ms": now,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{report['decision_date']}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    count = len(runner.reports)
    print(f"[{report['decision_date']}] 데모 T/v2 {day.status} · 주문 {count}건 · {path.name}")
    conn.close()
    return 0


def status() -> int:
    conn = connect(DB)
    apply_migrations(conn)
    for d in PaperDayRepository(conn).recent(OPTIONS.strategy_id, 10):
        print(_date(d.decision_ms), d.status, f"{d.equity:.2f}", d.targets, d.note or "")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(status() if sys.argv[1:] == ["status"] else run())
