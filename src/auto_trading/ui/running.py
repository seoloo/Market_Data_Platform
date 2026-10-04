"""실행 현황 화면 — 지금 돌고 있는 관찰·훈련·데모를 한 표로 (읽기만).

각 줄의 출처는 그 작업이 남기는 기록이다: Paper 원장(DB, :mod:`data` 가 이미 읽음),
``reports/`` 아래 보고서 파일, 데모 Elliott 상태 파일, 21:20 일일 점검 보고.
"마지막 기록"은 보고서 파일이 바뀐 시각이다. 작업이 제때 돌았는지를 본다.

import 는 core·storage·ui 만 한다(UI 는 매매 코드를 import 하지 않는다).
모든 동적 문자열은 이스케이프한다.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html import escape
from pathlib import Path
from typing import Any

from auto_trading.ui.catchup import catchup_panel
from auto_trading.ui.data import DashboardData, LedgerView

__all__ = ["MILESTONES", "TRACKS", "RunRow", "Track", "load_running", "running_page"]

_KST = timedelta(hours=9)
_HOUR_MS = 3_600_000
_DAY_MS = 86_400_000
_DAILY_GRACE_MS = 26 * _HOUR_MS
_WEEKDAY_GRACE_MS = 74 * _HOUR_MS
_RESIDENT_GRACE_MS = 5 * _HOUR_MS


@dataclass(frozen=True, slots=True)
class Track:
    key: str
    name: str
    env: str
    """PAPER · TESTNET · DEMO · 실계좌 읽기 · 점검."""
    what: str
    schedule: str
    """KST."""
    first: str
    last: str | None
    """관찰·훈련이 끝나는 날(포함). None 이면 계속."""
    source: str
    """``ledger:<key>`` · ``glob:<reports 아래 패턴>`` · ``ew`` · ``watchdog``."""
    watch_prefix: str | None = None
    """21:20 점검 보고에서 이 작업의 한 줄 요약이 시작하는 글자."""
    weekdays_only: bool = False
    """월~금만 도는 작업 — 주말을 건너 74 시간까지는 밀림이 아니다."""


TRACKS: tuple[Track, ...] = (
    Track(
        "t2",
        "T/v2",
        "PAPER",
        "현물 추세 롱 · 10/24 점검 대상",
        "21:00",
        "2026-09-22",
        None,
        "ledger:t2",
        "T/v2",
    ),
    Track(
        "o1",
        "O/v1",
        "PAPER",
        "현물 + 선물 추가 노출 1.5배",
        "21:02",
        "2026-09-23",
        None,
        "ledger:o1",
        "O/v1",
    ),
    Track(
        "o2",
        "O/v2",
        "PAPER",
        "O/v1 + 비상 스톱 −20% · 2단계 첫 후보",
        "21:04",
        "2026-09-23",
        None,
        "ledger:o2",
        "O/v2",
    ),
    Track(
        "o3",
        "O/v3",
        "PAPER",
        "2배 · 계좌 정지 40% · 중단선 35% (결정 B)",
        "21:06",
        "2026-09-23",
        None,
        "ledger:o3",
        "O/v3",
    ),
    Track(
        "drill",
        "Phase 7 매일 훈련",
        "TESTNET",
        "선물 주문 경로 20개 판정 (BTCUSDT)",
        "21:08",
        "2026-09-25",
        "2026-10-08",
        "glob:testnet/drill-*.json",
        "테스트넷 훈련",
    ),
    Track(
        "overlay",
        "선물 실행기 훈련 V4",
        "TESTNET",
        "O/v2 실행기 · 다리 스톱 (SOL·BNB)",
        "20:45",
        "2026-09-26",
        "2026-10-02",
        "glob:testnet/overlay-2*.json",
        "선물 실행기 훈련",
    ),
    Track(
        "shadow",
        "그림자 T/v2",
        "실계좌 읽기",
        "주문 0건 · 키 권한·잔고·호가 미끄러짐",
        "21:10",
        "2026-09-25",
        None,
        "glob:shadow/t2/20*.json",
        "그림자 운영",
    ),
    Track(
        "demo_t2",
        "데모 T/v2",
        "DEMO",
        "실거래 1단계 실행기 예행연습 (지정가)",
        "21:12",
        "2026-09-25",
        None,
        "glob:demo_t2/20*.json",
        "데모 T/v2",
    ),
    Track(
        "demo_o2",
        "데모 O/v2 선물",
        "DEMO",
        "선물 추가분 · 거래소 스톱 (ETHUSDT)",
        "21:14",
        "2026-09-25",
        None,
        "glob:demo_o2/20*.json",
        "데모 O/v2",
    ),
    Track(
        "demo_ew",
        "데모 Elliott",
        "DEMO",
        "BTCUSDC 상주 실행기",
        "상주 · 매시 확인",
        "2026-09-24",
        None,
        "ew",
        "데모 Elliott",
    ),
    Track(
        "toss",
        "토스 적립 + QS (QNDX·BTQ·WQTM·QS)",
        "실계좌 (토스증권)",
        "매일 가상 적립 · 1주 값이 모이면 장 마감 지정가 1주 · QQQ 추세로 QS (문서 118)",
        "21:00 월~금 · 환전 알림 09·12·15시",
        "2026-10-04",
        None,
        "glob:toss/20*.json",
        weekdays_only=True,
    ),
    Track(
        "watchdog",
        "일일 점검",
        "점검",
        "모든 원장·훈련·PC 상태 → 텔레그램",
        "21:20",
        "2026-09-22",
        None,
        "watchdog",
    ),
)

MILESTONES: tuple[tuple[str, str], ...] = (
    ("2026-10-02", "선물 실행기 훈련 V4 끝 — --flatten · 보고"),
    ("2026-10-08", "Phase 7 테스트넷 14일 판정 — review_phase7 --until 2026-10-08"),
    ("2026-10-23", "O/v3 한 달 보고 — review_phase6 --until 2026-10-22"),
    ("2026-10-24", "4주 점검 · 실거래 1단계 자격 — review_phase6 --until 2026-10-23"),
)


@dataclass(frozen=True, slots=True)
class RunRow:
    track: Track
    level: str
    """ok · warn · bad · mute."""
    status: str
    last_ms: int | None
    detail: str
    days: int


def _kst_date(ms: int) -> str:
    return (datetime.fromtimestamp(ms / 1000, UTC) + _KST).strftime("%Y-%m-%d")


def _kst(ms: int, fmt: str = "%m-%d %H:%M") -> str:
    return (datetime.fromtimestamp(ms / 1000, UTC) + _KST).strftime(fmt)


def _read(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _content(track: Track, report: dict[str, Any] | None) -> tuple[str, str]:
    """보고서 내용으로 본 (level, 상태 글자)."""
    if report is None:
        return "warn", "보고서 읽기 실패"
    if track.key in {"drill", "overlay"}:
        checks = report.get("checks") or []
        passed = sum(bool(c.get("pass")) for c in checks)
        return ("ok" if report.get("ok") else "bad"), f"{passed}/{len(checks)} " + (
            "✓" if report.get("ok") else "✗"
        )
    levels = {str(f.get("level")) for f in report.get("findings") or []}
    status = str(report.get("status") or "기록")
    if "CRITICAL" in levels:
        return "bad", f"{status} · CRITICAL"
    if "WARN" in levels:
        return "warn", f"{status} · 경고"
    return "ok", status


def _file_row(track: Track, root: Path, now_ms: int, watch: dict[str, str]) -> RunRow:
    pattern = track.source.removeprefix("glob:")
    paths = sorted((root / "reports").glob(pattern))
    detail = watch.get(track.key, "")
    if not paths:
        return RunRow(track, "mute", "기록 없음", None, detail, 0)
    last_ms = int(paths[-1].stat().st_mtime * 1000)
    days = len(
        {
            _kst_date(int(p.stat().st_mtime * 1000))
            for p in paths
            if _kst_date(int(p.stat().st_mtime * 1000)) >= track.first
        }
    )
    level, status = _content(track, _read(paths[-1]))
    grace = _WEEKDAY_GRACE_MS if track.weekdays_only else _DAILY_GRACE_MS
    return _timed(track, now_ms, last_ms, level, status, detail, days, grace)


def _timed(
    track: Track,
    now_ms: int,
    last_ms: int,
    level: str,
    status: str,
    detail: str,
    days: int,
    grace: int,
) -> RunRow:
    today = _kst_date(now_ms)
    if track.last is not None and today > track.last:
        return RunRow(track, "mute", f"끝남 · {status}", last_ms, detail, days)
    if today < track.first:
        return RunRow(track, "mute", f"기간 전 · {status}", last_ms, detail, days)
    if now_ms - last_ms > grace:
        hours = (now_ms - last_ms) // _HOUR_MS
        return RunRow(track, "warn", f"밀림 {hours}시간 · {status}", last_ms, detail, days)
    return RunRow(track, level, status, last_ms, detail, days)


def _ledger_row(
    track: Track, ledgers: Sequence[LedgerView], now_ms: int, watch: dict[str, str]
) -> RunRow:
    key = track.source.removeprefix("ledger:")
    lg = next((x for x in ledgers if x.key == key), None)
    if lg is None or not lg.points:
        return RunRow(track, "mute", "기록 없음", None, watch.get(track.key, ""), 0)
    last_day = lg.points[-1].day_ms
    # 결정일 d 는 d+1 21:0x KST(= d+1 UTC 12:00 쯤)에 기록된다.
    # 거기서 26 시간 넘게 새 기록이 없으면 밀림.
    written = last_day + _DAY_MS + 12 * _HOUR_MS
    level = "bad" if lg.halted or lg.liquidations else ("ok" if lg.status == "OK" else "warn")
    change = lg.change
    status = f"{lg.status} · {lg.equity:,.2f}" + (f" ({change * 100:+.2f}%)" if change else "")
    return _timed(
        track,
        now_ms,
        written,
        level,
        status,
        watch.get(track.key, ""),
        len(lg.points),
        _DAILY_GRACE_MS,
    )


def _ew_row(track: Track, root: Path, now_ms: int, watch: dict[str, str]) -> RunRow:
    state = _read(root / "reports" / "demo_ew" / "state.json")
    detail = watch.get(track.key, "")
    if state is None or not state.get("last_scan_ms"):
        return RunRow(track, "mute", "기록 없음", None, detail, 0)
    last_ms = int(state["last_scan_ms"])
    phase = {"IDLE": "대기", "PENDING": "예약 중", "OPEN": "보유 중"}.get(
        str(state.get("phase")), str(state.get("phase"))
    )
    return _timed(track, now_ms, last_ms, "ok", phase, detail, 0, _RESIDENT_GRACE_MS)


def _watch_row(track: Track, data: DashboardData, now_ms: int) -> RunRow:
    if not data.watch:
        return RunRow(track, "mute", "기록 없음", None, "", 0)
    w = data.watch[0]
    level = {0: "ok", 1: "warn"}.get(w.exit_code or 0, "bad")
    status = {"ok": "이상 없음", "warn": "경고", "bad": "조치 필요"}[level]
    counts: dict[str, int] = {}
    for lv, _, _ in w.findings:
        counts[lv] = counts.get(lv, 0) + 1
    detail = " · ".join(f"{k} {v}" for k, v in sorted(counts.items()) if k != "INFO")
    if w.at_ms is None:
        return RunRow(track, level, status, None, detail, len(data.watch))
    return _timed(track, now_ms, w.at_ms, level, status, detail, len(data.watch), _DAILY_GRACE_MS)


def _watch_lines(data: DashboardData) -> tuple[dict[str, str], str | None]:
    """최근 점검 보고의 줄에서 작업별 한 줄 요약과 PC 줄을 찾는다."""
    if not data.watch:
        return {}, None
    lines = [ln.strip() for ln in data.watch[0].lines]
    out: dict[str, str] = {}
    for track in TRACKS:
        if track.watch_prefix is None:
            continue
        for i, ln in enumerate(lines):
            if ln.startswith(f"{track.watch_prefix}:") or ln.startswith(f"{track.watch_prefix} "):
                text = ln.split(":", 1)[1].strip() if ":" in ln else ln
                nxt = lines[i + 1] if i + 1 < len(lines) else ""
                if track.source.startswith("ledger:") and nxt and ":" not in nxt.split(" ")[0]:
                    text = nxt  # Paper 원장은 둘째 줄(체결·노출)이 더 쓸모 있다
                out[track.key] = text
                break
    pc = next((ln for ln in lines if ln.startswith("PC:")), None)
    return out, pc


def load_running(root: Path, data: DashboardData) -> tuple[list[RunRow], str | None]:
    """(작업별 줄, PC 상태 줄). 읽기만 한다."""
    watch, pc = _watch_lines(data)
    rows: list[RunRow] = []
    for track in TRACKS:
        if track.source.startswith("ledger:"):
            rows.append(_ledger_row(track, data.ledgers, data.now_ms, watch))
        elif track.source.startswith("glob:"):
            rows.append(_file_row(track, root, data.now_ms, watch))
        elif track.source == "ew":
            rows.append(_ew_row(track, root, data.now_ms, watch))
        else:
            rows.append(_watch_row(track, data, data.now_ms))
    return rows, pc


def _dot(level: str) -> str:
    return f'<span class="dot {level}-bg"></span>'


def running_page(data: DashboardData) -> str:
    rows, pc = load_running(data.root or Path(), data)
    counts = {lv: sum(1 for r in rows if r.level == lv) for lv in ("ok", "warn", "bad", "mute")}
    cards = (
        f'<div class="card" style="border-left-color:var(--ok)"><h3>{counts["ok"]}</h3>'
        '<div class="sub">정상</div></div>'
        f'<div class="card" style="border-left-color:var(--warn)"><h3>{counts["warn"]}</h3>'
        '<div class="sub">경고 · 밀림</div></div>'
        f'<div class="card" style="border-left-color:var(--bad)"><h3>{counts["bad"]}</h3>'
        '<div class="sub">실패 · CRITICAL</div></div>'
        f'<div class="card"><h3>{counts["mute"]}</h3>'
        '<div class="sub">끝남 · 기간 전 · 기록 없음</div></div>'
    )
    halt = data.demo.halt
    banner = f'<div class="errors">긴급 정지 켜짐 (data/HALT): {escape(halt)}</div>' if halt else ""
    body = "".join(
        f"<tr><td>{_dot(r.level)}<b>{escape(r.track.name)}</b>"
        f"<span class='tag'>{escape(r.track.env)}</span>"
        f"<div class='mute'>{escape(r.track.what)}</div></td>"
        f"<td class='nowrap'>{escape(r.track.schedule)}</td>"
        f"<td class='nowrap mute'>{escape(r.track.first[5:])} ~ "
        f"{escape(r.track.last[5:]) if r.track.last else '계속'}</td>"
        f"<td class='num'>{r.days or ''}</td>"
        f"<td class='nowrap'>{_kst(r.last_ms) if r.last_ms else '—'}</td>"
        f"<td>{escape(r.status)}<div class='mute'>{escape(r.detail)}</div></td></tr>"
        for r in rows
    )
    table = (
        '<div class="panel"><h2>지금 돌고 있는 것</h2><table class="runs"><tr>'
        '<th style="min-width:170px">작업</th><th>시각(KST)</th><th>기간</th>'
        '<th class="num nowrap">일수</th><th>마지막 기록</th><th>상태 · 최근 요약</th></tr>'
        f"{body}</table>"
        '<div class="foot">마지막 기록 = 보고서가 쓰인 시각(원장은 결정일 다음 날 21시 기준). '
        "매일 작업은 26시간, 상주 실행기는 5시간 넘게 새 기록이 없으면 밀림. "
        "최근 요약은 마지막 21:20 점검 보고에서 가져온다.</div></div>"
    )
    today = _kst_date(data.now_ms)
    ms_rows = (
        "".join(
            f"<tr><td class='nowrap'>{escape(day)}</td><td class='num nowrap'>"
            f"{_dday(today, day)}</td><td>{escape(text)}</td></tr>"
            for day, text in MILESTONES
            if day >= today
        )
        or "<tr><td colspan=3 class='mute'>남은 일정 없음</td></tr>"
    )
    side = (
        catchup_panel(data.catchup) + '<div class="panel"><h2>PC 상태</h2>'
        f"<div>{escape(pc) if pc else '<span class=mute>점검 보고에 PC 줄 없음</span>'}</div>"
        '<div class="foot">21:20 점검이 읽은 예약 작업·비정상 종료 (문서 84)</div></div>'
        f'<div class="panel"><h2>다가오는 일정</h2><table>{ms_rows}</table></div>'
    )
    return (
        f'{banner}<div class="cards">{cards}</div>'
        f'<div class="grid2"><div>{table}</div><div>{side}</div></div>'
    )


def _dday(today: str, day: str) -> str:
    d0 = datetime.strptime(today, "%Y-%m-%d").replace(tzinfo=UTC)
    d1 = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC)
    n = (d1 - d0).days
    return "오늘" if n == 0 else f"D-{n}"
