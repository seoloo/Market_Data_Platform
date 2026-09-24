"""Paper 원장 일일 점검·보고 (Phase 6 watchdog). 실행기와 독립이고 DB를 **읽기 전용**으로 연다.

사용 예::

    python scripts/paper_watchdog.py                 # 점검 → 로컬 기록 + (설정 시) 텔레그램
    python scripts/paper_watchdog.py --no-send       # 텔레그램 보내지 않고 화면·로컬 기록만
    python scripts/paper_watchdog.py --test-telegram # 텔레그램 설정 확인용 시험 메시지

윈도우 작업 스케줄러로 매일 09:20(KST) 실행한다 — T/v2(09:05)·O/v1(09:10)·테스트넷 훈련(09:14) 뒤.
테스트넷 훈련 결과(``reports/testnet/drill-*.json``)도 한 줄로 요약한다.
훈련 실패는 경고(1)까지만 올린다.
종료 코드: 0 이상 없음 · 1 경고 · 2 조치 필요(중단 기준·실행 누락·원장 불일치·알림 실패).
보고서: ``reports/watchdog/<날짜>.txt``·``<날짜>.json``(대시보드용),
누적 기록 ``reports/watchdog/outbox.log``.
텔레그램은 ``.env``의 ``TELEGRAM_BOT_TOKEN``·``TELEGRAM_CHAT_ID``가 있을 때만 쓴다.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from auto_trading.backtest.overlay import O2_STOP
from auto_trading.config.secrets import load_telegram_credentials
from auto_trading.core.errors import AutoTradingError
from auto_trading.notify.channels import NotifyError, OutboxNotifier, TelegramNotifier
from auto_trading.ops.paper_watch import (
    Finding,
    LedgerSummary,
    check_o1,
    check_t2,
    check_testnet_drill,
    exit_code,
    expected_decision_ms,
    render_report,
)
from auto_trading.storage.db import connect_read_only
from auto_trading.storage.repositories.paper_days import PaperDayRepository
from auto_trading.storage.repositories.paper_overlay_days import OverlayPaperDayRepository

for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        _reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parent.parent
DB = REPO / "data" / "trading.db"
ENV = REPO / ".env"
OUT = REPO / "reports" / "watchdog"
DRILLS = REPO / "reports" / "testnet"
O2_LEDGER = "o2_stop"
"""O/v2 별도 원장 (docs/research/28). ``run_paper_o1.py --ledger o2``."""
PHASE7_START_MS = int(datetime(2026, 9, 25, tzinfo=UTC).timestamp() * 1000)
"""Phase 7 매일 훈련 운영 시작 (docs/research/26). 이전 기록은 개발 중 실행이다."""


def collect(now_ms: int) -> tuple[list[LedgerSummary | None], list[Finding]]:
    summaries: list[LedgerSummary | None] = []
    findings: list[Finding] = []
    try:
        conn = connect_read_only(DB)
    except AutoTradingError as exc:
        return [], [Finding("CRITICAL", "DB", f"열 수 없다: {exc}")]
    try:
        try:
            t2_rows = list(reversed(PaperDayRepository(conn).recent("t2_long", 1_000_000)))
        except sqlite3.Error as exc:
            findings.append(Finding("CRITICAL", "T/v2", f"원장 조회 실패: {exc}"))
        else:
            summary, found = check_t2(t2_rows, now_ms)
            summaries.append(summary)
            findings.extend(found)
        try:
            o1_rows = OverlayPaperDayRepository(conn).all("o1_overlay")
        except sqlite3.Error as exc:
            findings.append(Finding("CRITICAL", "O/v1", f"원장 조회 실패: {exc}"))
        else:
            summary, found = check_o1(o1_rows, now_ms)
            summaries.append(summary)
            findings.extend(found)
        try:
            o2_rows = OverlayPaperDayRepository(conn).all(O2_LEDGER)
        except sqlite3.Error as exc:
            findings.append(Finding("CRITICAL", "O/v2", f"원장 조회 실패: {exc}"))
        else:
            if o2_rows:
                summary, found = check_o1(o2_rows, now_ms, ledger="O/v2", cfg=O2_STOP)
                summaries.append(summary)
                findings.extend(found)
            else:  # 스케줄 등록 전 — 경고하지 않는다 (28 §4)
                findings.append(Finding("INFO", "O/v2", "기록 없음 — 스케줄 등록 전"))
    finally:
        conn.close()
    return summaries, findings


def drill_status(now_ms: int) -> tuple[list[str], list[Finding]]:
    """테스트넷 훈련 보고서를 읽어 요약한다. 손상된 파일은 건너뛰고 경고한다."""
    reports, broken = [], []
    for path in sorted(DRILLS.glob("drill-*.json")):
        try:
            reports.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            broken.append(path.name)
    lines, findings = check_testnet_drill(reports, now_ms, since_ms=PHASE7_START_MS)
    if broken:
        findings.append(Finding("WARN", "테스트넷", f"읽을 수 없는 훈련 기록 {broken}"))
    return lines, findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-send", action="store_true", help="텔레그램을 보내지 않는다")
    parser.add_argument("--test-telegram", action="store_true", help="시험 메시지만 보낸다")
    args = parser.parse_args()

    creds = load_telegram_credentials(ENV)
    if args.test_telegram:
        if creds is None:
            print("텔레그램 미설정 — .env 의 TELEGRAM_BOT_TOKEN·TELEGRAM_CHAT_ID 를 채운다")
            return 1
        telegram = TelegramNotifier(creds)
        try:
            telegram.send("Auto_Trading watchdog 시험 메시지 — 이 메시지가 보이면 설정 완료")
        except NotifyError as exc:
            print(f"실패: {exc}")
            return 2
        finally:
            telegram.close()
        print("시험 메시지 전송 완료")
        return 0

    now_ms = int(time.time() * 1000)
    summaries, findings = collect(now_ms)
    drill_lines, drill_findings = drill_status(now_ms)
    findings += drill_findings
    code = exit_code(findings)
    text = render_report(summaries, findings, now_ms, extra=drill_lines)

    date = datetime.fromtimestamp(expected_decision_ms(now_ms) / 1000, tz=UTC).strftime("%Y-%m-%d")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{date}.txt").write_text(text + "\n", encoding="utf-8", newline="\n")
    OutboxNotifier(OUT / "outbox.log").send(text)

    if creds is not None and not args.no_send:
        telegram = TelegramNotifier(creds)
        try:
            telegram.send(text)
        except NotifyError as exc:
            note = f"텔레그램 전송 실패 — 로컬 기록만 남음: {exc}"
            OutboxNotifier(OUT / "outbox.log").send(note)
            print(note)
            code = 2
        finally:
            telegram.close()
    elif creds is None:
        print("(텔레그램 미설정 — 로컬 기록만)")
    # 대시보드(ui/)가 읽는 구조화 기록 — 텍스트와 같은 내용, 알림 실패까지 반영한 종료 코드
    record = {
        "date": date,
        "at_ms": now_ms,
        "exit_code": code,
        "summaries": [asdict(s) for s in summaries if s is not None],
        "findings": [asdict(f) for f in findings],
        "extra": drill_lines,
    }
    (OUT / f"{date}.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n"
    )
    print(text)
    return code


if __name__ == "__main__":
    sys.exit(main())
