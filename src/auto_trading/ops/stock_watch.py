"""일일 점검에 넣는 미국 주식 쪽 (2026-10-04) — 토스 실거래 적립 + QS (문서 118) · LT·QS·AG Paper.

저장된 보고서·상태 파일만 읽는다(주문·계좌 조회 없음). 문제는 WARN 으로만 올린다 — CRITICAL 은
코인 Paper 원장의 안전에 쓰는 등급이다.

토스 (월~금 21:00 KST 실행, ``reports/toss/<KST 날짜>.json``):

* 가장 최근 평일 밤 실행(21:15 이 지났으면 오늘)이 기록에 없으면 — PC 꺼짐·허용 IP·오류.
* 그 밤 종목 주문이 거절(REJECTED)이거나 결과 모름(UNKNOWN).
* QS 가 TQQQ 를 살(BUY)·팔(SELL) 차례로 이틀 연속 남아 있으면.

Paper: LT·QS(미국 거래일) 마지막 기록이 4 일, AG(코인 일봉 포함) 가 2 일 넘게 멈추면.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from auto_trading.ops.paper_watch import Finding

__all__ = ["check_stock_papers", "check_toss", "expected_toss_day"]

TOSS_SYMBOLS = ("QNDX", "BTQ", "WQTM", "QS")
"""적립 종목. QS 는 퀀텀스케이프다 — QS 전략 보고는 ``reports/toss/QS_SWITCH/``."""
_RUN_DONE = (21, 15)


def _read(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def expected_toss_day(now_kst: datetime) -> date:
    """끝났어야 할 가장 최근 토스 밤 실행의 날짜 (월~금, 21:15 기준)."""
    day = now_kst.date()
    if day.weekday() >= 5 or (now_kst.hour, now_kst.minute) < _RUN_DONE:
        day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def check_toss(root: Path, now_kst: datetime) -> tuple[list[str], list[Finding]]:
    states = root / "data" / "toss"
    starts = [
        str(s.get("start_date"))
        for sym in TOSS_SYMBOLS
        if (s := _read(states / f"{sym}.json")) and s.get("start_date")
    ]
    if not starts:
        return [], [Finding("INFO", "토스", "자동 적립 시작 전")]
    base = root / "reports" / "toss"
    runs = sorted(p.stem for p in base.glob("20*.json"))
    expected = expected_toss_day(now_kst).isoformat()
    findings: list[Finding] = []
    last = runs[-1] if runs else None
    if expected >= min(starts) and (last is None or last < expected):
        findings.append(
            Finding(
                "WARN",
                "토스",
                f"밤 실행 기록 없음 — 예정 {expected}, 마지막 {last or '없음'}"
                " (PC 꺼짐·허용 IP·오류: reports/tasklog 확인)",
            )
        )
    orders: list[str] = []
    if last is not None:
        for sym in TOSS_SYMBOLS:
            r = _read(base / sym / f"{last}.json") or {}
            o = r.get("order") or {}
            status = str(o.get("status") or "")
            if status:
                orders.append(f"{sym} {status}")
            if status == "REJECTED":
                findings.append(
                    Finding("WARN", "토스", f"{last} {sym} 주문 거절 {o.get('code', '')}".strip())
                )
            elif status == "UNKNOWN":
                findings.append(Finding("WARN", "토스", f"{last} {sym} 주문 전송 결과 모름"))
    qs_paths = sorted((base / "QS_SWITCH").glob("20*.json"))
    qs = [_read(p) or {} for p in qs_paths[-2:]]
    qs_line = ""
    if qs:
        now = qs[-1]
        qs_line = (
            f" · QS 추세 {'켜짐' if now.get('trend_on') else '꺼짐'}"
            f" · 할 일 {now.get('want', '?')} · QS TQQQ {now.get('strategy_tqqq_qty', 0):g}주"
        )
        order = now.get("order") or {}
        if order.get("status") == "REJECTED":
            findings.append(
                Finding("WARN", "토스 QS", f"TQQQ 주문 거절 {order.get('code', '')}".strip())
            )
        if len(qs) == 2 and qs[0].get("want") == now.get("want") in ("BUY", "SELL"):
            word = "살" if now.get("want") == "BUY" else "팔"
            findings.append(
                Finding("WARN", "토스 QS", f"TQQQ 를 {word} 차례인데 이틀째 체결되지 않았다")
            )
    line = (
        f"자동 적립 + QS: 마지막 실행 {last or '없음'}"
        + (f" · 주문 {', '.join(orders)}" if orders else "")
        + qs_line
    )
    return [line], findings


def check_stock_papers(root: Path, now_kst: datetime) -> tuple[list[str], list[Finding]]:
    today = now_kst.date()
    parts: list[str] = []
    findings: list[Finding] = []
    for name, folder, limit in (
        ("LT", "lt_paper", 4),
        ("QS", "qs_paper", 4),
        ("AG", "ag_paper", 2),
        ("NQ", "nq_paper", 4),
        ("SN20", "sn20_paper", 4),
        ("SN20C", "sn20c_paper", 4),
    ):
        latest = _read(root / "reports" / folder / "latest.json")
        if latest is None:
            continue
        day = str(latest.get("date") or "")
        parts.append(f"{name} {day[5:] or '?'}")
        try:
            lag = (today - date.fromisoformat(day)).days
        except ValueError:
            findings.append(Finding("WARN", f"{name} Paper", "기록 날짜를 읽지 못함"))
            continue
        if lag > limit:
            findings.append(Finding("WARN", f"{name} Paper", f"마지막 기록 {day} — {lag}일째 멈춤"))
    lines = [f"주식 Paper 마지막 기록: {' · '.join(parts)}"] if parts else []
    return lines, findings
