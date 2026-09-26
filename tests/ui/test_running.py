"""실행 현황 화면 — 밀림·끝남·실패 판정, 점검 보고 요약 줄, 이스케이프."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from auto_trading.ui.data import load_dashboard
from auto_trading.ui.running import RunRow, load_running, running_page

H = 3_600_000
NOW = int(datetime(2026, 9, 27, 13, 0, tzinfo=UTC).timestamp() * 1000)  # 09-27 22:00 KST
EVIL = "<script>alert(1)</script>"


def _write(path: Path, data: object, at_ms: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.utime(path, (at_ms / 1000, at_ms / 1000))


def _root(tmp_path: Path) -> Path:
    r = tmp_path / "reports"
    _write(
        r / "testnet" / "drill-20260927T120800Z.json",
        {"at": "20260927T120800Z", "ok": False, "checks": [{"step": "a", "pass": False}]},
        NOW - 1 * H,
    )
    _write(
        r / "testnet" / "overlay-20260925T114500Z.json",
        {"at": "20260925T114500Z", "ok": True, "checks": [{"step": "a", "pass": True}]},
        NOW - 49 * H,
    )
    _write(
        r / "shadow" / "t2" / "2026-09-26.json",
        {"status": "OK", "findings": [{"level": "CRITICAL", "message": "x"}]},
        NOW - 2 * H,
    )
    _write(r / "demo_t2" / "2026-09-26.json", {"status": "OK", "findings": []}, NOW - 2 * H)
    _write(r / "demo_ew" / "state.json", {"phase": "IDLE", "last_scan_ms": NOW - 6 * H}, NOW)
    _write(
        r / "watchdog" / "2026-09-26.json",
        {
            "date": "2026-09-26",
            "at_ms": NOW - 40 * 60_000,
            "exit_code": 1,
            "findings": [{"level": "WARN", "ledger": "PC", "message": "m"}],
        },
        NOW,
    )
    (r / "watchdog" / "2026-09-26.txt").write_text(
        "[일일 점검 2026-09-26] ⚠ 경고\n"
        f"데모 T/v2: 2026-09-26 OK · 자본 5,000.00 USDT {EVIL}\n"
        "PC: 작업 11개 · 실패 0 · 밀림 0 · 비정상 종료 0회(24시간)\n",
        encoding="utf-8",
    )
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "HALT").write_text(f"점검 중 {EVIL}\n", encoding="utf-8")
    return tmp_path


def _rows(tmp_path: Path) -> tuple[dict[str, RunRow], str | None]:
    root = _root(tmp_path)
    rows, pc = load_running(root, load_dashboard(root, NOW))
    return {r.track.key: r for r in rows}, pc


def test_levels_follow_each_record(tmp_path: Path) -> None:
    rows, _ = _rows(tmp_path)
    assert rows["drill"].level == "bad"
    assert rows["shadow"].level == "bad"  # CRITICAL
    assert rows["demo_t2"].level == "ok"
    assert rows["demo_ew"].level == "warn"  # 상주 실행기 5시간 넘게 조용
    assert rows["overlay"].level == "warn"  # 49시간 밀림, 기간(~10/02) 안
    assert rows["watchdog"].level == "warn"
    assert rows["t2"].status == "기록 없음"  # DB 없음


def test_watch_summary_and_pc_line(tmp_path: Path) -> None:
    rows, pc = _rows(tmp_path)
    assert rows["demo_t2"].detail.startswith("2026-09-26 OK")
    assert str(pc).startswith("PC: 작업 11개")


def test_track_after_its_period_is_muted(tmp_path: Path) -> None:
    root = _root(tmp_path)
    later = int(datetime(2026, 10, 3, 13, 0, tzinfo=UTC).timestamp() * 1000)
    rows, _ = load_running(root, load_dashboard(root, later))
    overlay = next(r for r in rows if r.track.key == "overlay")
    assert overlay.level == "mute"
    assert overlay.status.startswith("끝남")


def test_page_escapes_and_shows_halt(tmp_path: Path) -> None:
    root = _root(tmp_path)
    html = running_page(load_dashboard(root, NOW))
    assert EVIL not in html
    assert "긴급 정지 켜짐" in html
    assert "D-11" in html  # 10/08 Phase 7 판정
