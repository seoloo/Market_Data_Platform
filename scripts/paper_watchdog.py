"""Paper 원장 일일 점검·보고 (Phase 6 watchdog). 실행기와 독립이고 DB를 **읽기 전용**으로 연다.

사용 예::

    python scripts/paper_watchdog.py                 # 점검 → 로컬 기록 + (설정 시) 텔레그램
    python scripts/paper_watchdog.py --no-send       # 텔레그램 보내지 않고 화면·로컬 기록만
    python scripts/paper_watchdog.py --test-telegram # 텔레그램 설정 확인용 시험 메시지

윈도우 작업 스케줄러로 매일 21:20(KST) 실행한다 — T/v2(21:00)·O/v1(21:02)·O/v2(21:04)·O/v3(21:06)·
테스트넷 훈련(21:08)·그림자 운영(21:10) 뒤.
2026-09-24 사용자 결정으로 저녁 시각으로 옮겼다
(출근 때문에 아침에 PC 를 켤 수 없다).
테스트넷 훈련 결과(``reports/testnet/drill-*.json``)와
T/v2 그림자 운영(``reports/shadow/t2/``, docs/research/51),
데모 T/v2(55·57)·Elliott BTC 데모(56)도
한 줄씩 요약한다.
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

from auto_trading.backtest.overlay import O2_STOP, O3_2X
from auto_trading.config.secrets import load_telegram_credentials
from auto_trading.core.errors import AutoTradingError
from auto_trading.notify.channels import NotifyError, OutboxNotifier, TelegramNotifier
from auto_trading.ops.paper_watch import (
    Finding,
    LedgerSummary,
    check_demo_ew,
    check_demo_t2,
    check_o1,
    check_shadow,
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
SHADOW = REPO / "reports" / "shadow" / "t2"
DEMO_T2 = REPO / "reports" / "demo_t2"
DEMO_EW = REPO / "reports" / "demo_ew"
O2_LEDGER = "o2_stop"
"""O/v2 별도 원장 (docs/research/28). ``run_paper_o1.py --ledger o2``."""
_EXTRA_LEDGERS = (("O/v2", O2_LEDGER, O2_STOP), ("O/v3", "o3_2x", O3_2X))
"""O/v1 다음에 점검하는 원장 (O/v3 는 docs/research/31 한 달 관찰)."""
PHASE7_START_MS = int(datetime(2026, 9, 25, tzinfo=UTC).timestamp() * 1000)
OVERLAY_START_MS = int(datetime(2026, 9, 26, tzinfo=UTC).timestamp() * 1000)
"""O/v2 선물 실행기 테스트넷 운영 시작 (docs/research/54). 이전 기록은 개발 중 실행이다."""
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
        for name, strategy_id, cfg in _EXTRA_LEDGERS:
            try:
                rows = OverlayPaperDayRepository(conn).all(strategy_id)
            except sqlite3.Error as exc:
                findings.append(Finding("CRITICAL", name, f"원장 조회 실패: {exc}"))
                continue
            if rows:
                summary, found = check_o1(rows, now_ms, ledger=name, cfg=cfg)
                summaries.append(summary)
                findings.extend(found)
            else:  # 스케줄 등록 전 — 경고하지 않는다 (28 §4)
                findings.append(Finding("INFO", name, "기록 없음 — 스케줄 등록 전"))
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


def overlay_drill_status(now_ms: int) -> tuple[list[str], list[Finding]]:
    """O/v2 선물 실행기 테스트넷 훈련(``overlay-*.json``) 요약. 실패는 경고까지만."""
    reports, broken = [], []
    for path in sorted(DRILLS.glob("overlay-2*.json")):
        try:
            reports.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            broken.append(path.name)
    lines, findings = check_testnet_drill(
        reports,
        now_ms,
        since_ms=OVERLAY_START_MS,
        window_days=7,
        ledger="선물 실행기",
        label="선물 실행기 훈련",
    )
    if broken:
        findings.append(Finding("WARN", "선물 실행기", f"읽을 수 없는 기록 {broken}"))
    return lines, findings


def shadow_status(now_ms: int) -> tuple[list[str], list[Finding]]:
    """최신 그림자 운영 보고서 요약 (docs/research/51). 없으면 등록 전으로 본다."""
    paths = sorted(SHADOW.glob("*.json"))
    if not paths:
        return check_shadow(None, now_ms)
    try:
        report = json.loads(paths[-1].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], [Finding("WARN", "그림자", f"읽을 수 없는 보고서 {paths[-1].name}")]
    return check_shadow(report, now_ms)


def demo_t2_status(now_ms: int) -> tuple[list[str], list[Finding]]:
    """데모 T/v2 (실거래 1단계 실행기 예행연습, docs/research/55·57) 최신 보고서와 미해결 주문."""
    paths = sorted(DEMO_T2.glob("20*.json"))
    report = None
    if paths:
        try:
            report = json.loads(paths[-1].read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return [], [Finding("WARN", "데모 T/v2", f"읽을 수 없는 보고서 {paths[-1].name}")]
    unresolved = 0
    try:
        conn = connect_read_only(DB)
        try:
            unresolved = conn.execute(
                "SELECT COUNT(*) FROM spot_orders WHERE mode = 'demo'"
                " AND state IN ('PENDING', 'SUBMITTED', 'UNKNOWN', 'PARTIALLY_FILLED')"
            ).fetchone()[0]
        finally:
            conn.close()
    except (AutoTradingError, sqlite3.Error):
        pass  # DB 문제는 collect() 가 이미 CRITICAL 로 올린다
    return check_demo_t2(report, int(unresolved), now_ms)


def demo_ew_status(now_ms: int) -> tuple[list[str], list[Finding]]:
    """Elliott BTC 선물 데모 상주 실행기 (docs/research/56) 상태·사건."""
    state_path = DEMO_EW / "state.json"
    if not state_path.exists():
        return check_demo_ew(None, [], now_ms)
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        events_path = DEMO_EW / "events.jsonl"
        events = (
            [json.loads(x) for x in events_path.read_text(encoding="utf-8").splitlines() if x]
            if events_path.exists()
            else []
        )
    except (OSError, ValueError):
        return [], [Finding("WARN", "데모 Elliott", "상태·사건 기록을 읽을 수 없다")]
    return check_demo_ew(state, events, now_ms)


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
    overlay_lines, overlay_findings = overlay_drill_status(now_ms)
    drill_lines += overlay_lines
    findings += overlay_findings
    shadow_lines, shadow_findings = shadow_status(now_ms)
    drill_lines += shadow_lines
    findings += shadow_findings
    for status_fn in (demo_t2_status, demo_ew_status):
        demo_lines, demo_findings = status_fn(now_ms)
        drill_lines += demo_lines
        findings += demo_findings
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
