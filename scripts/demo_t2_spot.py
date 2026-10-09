"""데모 T/v2 현물 — 실거래 1단계 실행기 예행연습 (docs/research/55·57). **데모 모드 전용, 가짜 돈.**

실거래 1단계와 **같은 실행기**(:class:`LiveT2Runner`)를 데모 거래소에 붙여 매일 돌린다
(예정 21:12 KST).
기록은 메인 DB(``spot_orders`` mode='demo', ``paper_days`` strategy_id='t2_demo')와
``reports/demo_t2/<결정일>.json``. 알림은 텔레그램 + ``reports/demo_t2/outbox.log``.
긴급 정지 ``data/HALT`` 는 실거래와 똑같이 이 실행기도 멈춘다.

사용::

    python scripts/demo_t2_spot.py                    # 하루 실행 (예약 작업)
    python scripts/demo_t2_spot.py status             # 최근 기록
    python scripts/demo_t2_spot.py plan               # 계획만 — 주문·기록·알림 없음 (첫날 절차)
    python scripts/demo_t2_spot.py release-dd "사유"  # 계좌 낙폭 30% 정지 해제 (자동 해제 없음)
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
from auto_trading.storage.repositories.risk_events import RiskEventRepository  # noqa: E402
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


TELEGRAM = False
"""2026-10-09 사용자 "이제 지금 paper 테스트 중인 것들 알림을 보내지 않아도 돼" → 텔레그램 끔.
기록(``outbox.log`` · DB · 보고서)은 그대로 남는다 — 대시보드 · 에이전트에게 물어서 본다."""


class Notify:
    def __init__(self) -> None:
        creds = load_telegram_credentials(REPO_ROOT / ".env") if TELEGRAM else None
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


def _runner(conn: Any, notify: Any) -> LiveT2Runner:
    env = read_env_file(REPO_ROOT / ".env")
    key, secret = env.get("BINANCE_TESTNET_API_KEY", ""), env.get("BINANCE_TESTNET_API_SECRET", "")
    if not key or not secret:
        raise SystemExit(".env 에 BINANCE_TESTNET_API_KEY / SECRET(데모 키)가 없다")
    return LiveT2Runner(
        BinanceSpotDemo(BinanceCredentials(key, secret)),
        BinanceSpotReadOnly(None),
        SpotOrderRepository(conn),
        PaperDayRepository(conn),
        options=OPTIONS,
        clock=lambda: int(time.time() * 1000),
        sleep=time.sleep,
        halt_path=HALT,
        notify=notify,
        risk_events=RiskEventRepository(conn),
    )


def run() -> int:
    conn = connect(DB)
    apply_migrations(conn)
    _import_legacy(PaperDayRepository(conn))
    runner = _runner(conn, Notify())
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


PAPER = "t2_long"
"""같은 규칙의 Paper 원장 — 계획의 목표가 이것과 같아야 한다 (85 §4 3)."""


def plan() -> int:
    """계획만 — 주문·기록·알림·복구 조회 없음. 거래소에서는 잔고·가격만 읽는다."""
    conn = connect(DB)
    apply_migrations(conn)
    runner = _runner(conn, print)
    now = int(time.time() * 1000)
    day_plan = runner.plan(now)
    if day_plan is None:
        print("오늘 결정은 이미 실행·기록됐다 — status 로 본다")
        conn.close()
        return 0
    print(day_plan.text(OPTIONS.mode))
    if day_plan.prev is not None:
        paper = PaperDayRepository(conn).get(PAPER, day_plan.prev.decision_ms)
        if paper is None:
            print(f"Paper {PAPER} 같은 결정일 기록 없음 — 대조 못 함")
        else:
            same = paper.targets == day_plan.prev.targets
            print(
                f"Paper {PAPER} 같은 결정일 목표: {paper.targets} → "
                + ("같다 ✓" if same else "⚠ 다르다 — 보내기 전에 원인 확인")
            )
    conn.close()
    return 0


def release_dd(reason: str) -> int:
    conn = connect(DB)
    apply_migrations(conn)
    try:
        _runner(conn, Notify()).release_drawdown(reason)
    except ValueError as exc:
        print(f"해제 안 함: {exc}")
        return 1
    finally:
        conn.close()
    print("해제했다 — 다음 실행부터 새 매수 허용, 고점은 지금부터 다시 잰다")
    return 0


def main(argv: list[str]) -> int:
    if argv == ["status"]:
        return status()
    if argv == ["plan"]:
        return plan()
    if argv[:1] == ["release-dd"]:
        if len(argv) != 2:
            raise SystemExit('사용: release-dd "사유"')
        return release_dd(argv[1])
    if argv:
        raise SystemExit(f"모르는 명령: {argv}")
    return run()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
