"""문서 65 — 10/23·10/24 점검 보고서. DB 는 읽기 전용, 보고서·상태 파일은 읽기만 한다.

사용 예::

    python -m scripts.review_phase6                    # 마지막으로 끝난 UTC 날(결정일)까지
    python -m scripts.review_phase6 --until 2026-10-22 # 10/23 21:xx 실행분까지 (문서 65 §2)

결과: ``reports/review/<until>.md``·``.json``. 기준은 문서 65 에 고정돼 있다 — 여기서 바꾸지 않는다.
한 달 수익으로 전략을 고르지 않는다(§1). 수익은 같은 날들 비교로 **보고만** 한다.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from auto_trading.backtest.overlay import O2_STOP, O3_2X_LIMIT40, OverlayConfig
from auto_trading.execution.paper_overlay import verify_ledger
from auto_trading.ops.review import (
    DAY_MS,
    Criterion,
    LedgerReview,
    LedgerRow,
    demo_t2_review,
    ledger_review,
    normalize_demo_orders,
    shadow_review,
    watch_review,
)
from auto_trading.storage.db import connect_read_only
from auto_trading.storage.repositories.paper_days import PaperDayRepository
from auto_trading.storage.repositories.paper_overlay_days import OverlayPaperDayRepository
from auto_trading.storage.repositories.spot_orders import OPEN_STATES, SpotOrderRepository

REPO = Path(__file__).resolve().parent.parent
DB = REPO / "data" / "trading.db"
REPORTS = REPO / "reports"
OUT = REPORTS / "review"
OVERLAYS: tuple[tuple[str, str, OverlayConfig, float], ...] = (
    ("O/v1", "o1_overlay", OverlayConfig(), 0.25),
    ("O/v2", "o2_stop", O2_STOP, 0.25),
    # 2026-09-26 사용자 결정(문서 82 §4 B, 결과 보기 전): O/v3 는 계좌 정지 40% · 중단선 35%
    ("O/v3", "o3_2x", O3_2X_LIMIT40, 0.35),
)
FIXED_FACTS = (
    "O/v3(2 배)는 과거 백테스트 경로에서 2022-11-09 계좌 정지(봉 안 −30.3%)"
    " — 이전 보고의 CAGR 30.1%·MDD 29.9% 는 정지 경로의 숫자다 (문서 64 §6).",
    "선물부터 줄이기로도 2 배의 MDD 는 27.1% (> 25%)"
    " — 2 배는 지금 위험 한도 안에 들어오지 않는다 (문서 64).",
    "2026-09-26 사용자 결정(문서 82 §4 B): O/v3 는 10/23 에 끝내지 않고 계좌 정지 40% ·"
    " 중단선 35% 로 관찰을 이어 간다 — 과거 경로에서 필요했던 정지선은 31.1% 였다(문서 82 §2).",
    "한 달(4 주) 수익으로는 최소 검출 샤프가 수십이다"
    " — 원장 사이 수익 차이는 판단 근거가 아니다 (문서 31 §2).",
)


def _date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d")


def _json_dir(folder: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for path in sorted(folder.glob("20*.json")):
        try:
            out[path.stem] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            out[path.stem] = {"findings": [{"level": "WARN", "message": f"읽기 실패 {path.name}"}]}
    return out


def _ledgers(conn: Any, until_ms: int) -> list[LedgerReview]:
    out: list[LedgerReview] = []
    t2 = PaperDayRepository(conn).recent("t2_long", 2000)
    review = ledger_review(
        "T/v2",
        [LedgerRow(d.decision_ms, d.status, float(d.equity)) for d in t2],
        until_ms=until_ms,
        overlay=False,
        verify_problems=None,
    )
    if review:
        out.append(review)
    repo = OverlayPaperDayRepository(conn)
    for name, sid, cfg, mdd_stop in OVERLAYS:
        rows = [r for r in repo.all(sid) if r.decision_ms <= until_ms]
        problems = verify_ledger(rows, cfg=cfg) if rows else []
        review = ledger_review(
            name,
            [
                LedgerRow(
                    r.decision_ms,
                    r.status,
                    r.equity,
                    int(r.tally.get("liquidations") or 0),
                    r.tally.get("halted_day") is not None,
                    int(r.tally.get("stop_hits") or 0),
                )
                for r in rows
            ],
            until_ms=until_ms,
            overlay=True,
            verify_problems=len(problems),
            mdd_stop=mdd_stop,
        )
        if review:
            out.append(review)
    return out


def _same_days(ledgers: list[LedgerReview]) -> dict[str, Any]:
    """모든 원장이 기록을 가진 날들만으로 수익·낙폭 (보고만)."""
    if not ledgers:
        return {}
    common = set.intersection(*({d for d, _ in lv.equity} for lv in ledgers))
    if len(common) < 2:
        return {"days": len(common)}
    days = sorted(common)
    table: dict[str, Any] = {"from": days[0], "to": days[-1], "days": len(days)}
    for lv in ledgers:
        eq = dict(lv.equity)
        series = [eq[d] for d in days]
        peak, mdd = series[0], 0.0
        for v in series:
            peak = max(peak, v)
            mdd = max(mdd, 1 - v / peak)
        table[lv.name] = {"return": series[-1] / series[0] - 1, "mdd": mdd}
    return table


def _demo(conn: Any, until: str) -> dict[str, Any]:
    reports = {k: v for k, v in _json_dir(REPORTS / "demo_t2").items() if k <= until}
    legacy: list[dict[str, Any]] = []
    legacy_path = REPORTS / "demo_t2" / "days.jsonl"
    if legacy_path.exists():
        legacy = [json.loads(x) for x in legacy_path.read_text(encoding="utf-8").splitlines() if x]
    orders = normalize_demo_orders(list(reports.values()), legacy)
    unresolved = sum(
        1 for r in SpotOrderRepository(conn).all() if r.mode == "demo" and r.state in OPEN_STATES
    )
    degraded = [
        k for k, v in reports.items() if v.get("status") in ("DEGRADED", "HALTED", "DD_STOP")
    ]
    crit, stats = demo_t2_review(orders, unresolved=unresolved, degraded_days=degraded)
    events: list[dict[str, Any]] = []
    ew = REPORTS / "demo_ew" / "events.jsonl"
    if ew.exists():
        events = [json.loads(x) for x in ew.read_text(encoding="utf-8").splitlines() if x.strip()]
    kinds: dict[str, int] = {}
    for e in events:
        kinds[str(e.get("kind"))] = kinds.get(str(e.get("kind")), 0) + 1
    protect = [int(e["protect_ms"]) for e in events if e.get("protect_ms") is not None]
    o2 = {k: v for k, v in _json_dir(REPORTS / "demo_o2").items() if k <= until}
    o2_states = {
        k: (v.get("report") or {}).get("state", v.get("error", "?")) for k, v in o2.items()
    }
    return {
        "t2_criteria": [asdict(c) for c in crit],
        "t2_stats": stats,
        "elliott_events": kinds,
        "elliott_max_protect_ms": max(protect) if protect else None,
        "o2_days": len(o2),
        "o2_not_ok": {k: s for k, s in o2_states.items() if s != "OK"},
        "o2_fired_stops": sum(
            len((v.get("report") or {}).get("fired_stops") or []) for v in o2.values()
        ),
    }


def _line(c: Criterion | dict[str, Any]) -> str:
    d = asdict(c) if isinstance(c, Criterion) else c
    mark = {"PASS": "✅", "FAIL": "❌", "CHECK": "⚠ 확인", "NA": "—"}.get(d["status"], d["status"])
    return f"| {d['id']} | {mark} | {d['detail']} |"


def render(record: dict[str, Any]) -> str:
    out = [
        f"# 점검 보고 — {record['until']} 결정일까지 (문서 65 기준)",
        "",
        f"생성 {record['generated']} · DB 읽기 전용."
        " **판정 기준은 문서 65 에 고정 — 이 보고서가 바꾸지 않는다.**",
        "",
        "## 먼저 적는 사실",
        "",
        *[f"- {f}" for f in FIXED_FACTS],
        "",
        "## 1. Paper 원장 (Phase 6 — P1~P6)",
        "",
    ]
    for lv in record["ledgers"]:
        out += [
            f"### {lv['name']} — {lv['first']} ~ {lv['last']} · 기록 {lv['recorded']}일 · "
            f"수익 {lv['total_return']:+.2%} · 종가 MDD {lv['mdd']:.2%} · 스톱 {lv['stop_hits']}",
            "",
            "| # | 결과 | 내용 |",
            "|---|---|---|",
            *[_line(c) for c in lv["criteria"]],
            _line(record["watch"]["criterion"]),
            "",
        ]
    if record["watch"]["critical"]:
        out += [
            "일일 점검 CRITICAL (원인 기록 필요):",
            "",
            *[f"- {c}" for c in record["watch"]["critical"]],
            "",
        ]
    same = record["same_days"]
    if same.get("days", 0) >= 2:
        out += [
            f"## 2. 같은 날들 비교 (보고만, {same['from']} ~ {same['to']}, {same['days']}일)",
            "",
            "| 원장 | 수익 | 종가 MDD |",
            "|---|---|---|",
            *[
                f"| {k} | {v['return']:+.2%} | {v['mdd']:.2%} |"
                for k, v in same.items()
                if isinstance(v, dict)
            ],
            "",
        ]
    out += [
        "## 3. 그림자 T/v2 (문서 51 S1~S5)",
        "",
        "| # | 결과 | 내용 |",
        "|---|---|---|",
        *[_line(c) for c in record["shadow"]["criteria"]],
        "",
        "## 4. 데모 (문서 55 K1 필수 · K2~K4 측정 · 56 · 59 보고만)",
        "",
        "| # | 결과 | 내용 |",
        "|---|---|---|",
        *[_line(c) for c in record["demo"]["t2_criteria"]],
        "",
        f"- Elliott 데모 사건: {record['demo']['elliott_events']} · 최대 보호 공백 "
        f"{record['demo']['elliott_max_protect_ms']} ms",
        f"- O/v2 ETH 선물 데모: {record['demo']['o2_days']}일"
        f" · OK 아닌 날 {record['demo']['o2_not_ok']}"
        f" · 비상 스톱 발동 {record['demo']['o2_fired_stops']}",
        "",
        "## 5. 결정 메뉴 (문서 65 §7 — 사용자가 정한다)",
        "",
        f"- **D1 Phase 8 1단계 자격 (T/v2 현물 소액 실거래):** {record['d1']}",
        "- **D2 O 계열:** 한 달 수익으로 고르지 않는다. 문서 65 §7 의 권고를 본다.",
        "- 자격은 승인이 아니다. 승인하면 문서 57 §4 L2~L5 순서로 간다.",
        "",
    ]
    return "\n".join(out) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--until", help="마지막 결정일 YYYY-MM-DD (기본: 마지막으로 끝난 UTC 날)")
    args = parser.parse_args()
    now = int(time.time() * 1000)
    until_ms = (
        now - now % DAY_MS - 1
        if args.until is None
        else (
            int(datetime.strptime(args.until, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000)
            + DAY_MS
            - 1
        )
    )
    until = _date(until_ms)
    conn = connect_read_only(DB)
    try:
        ledgers = _ledgers(conn, until_ms)
        demo = _demo(conn, until)
    finally:
        conn.close()
    first_ms = (
        min(
            int(datetime.strptime(lv.first, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000)
            for lv in ledgers
        )
        if ledgers
        else until_ms
    )
    watch = _json_dir(REPORTS / "watchdog")
    watch_crit, watch_info = watch_review(watch, first_ms=first_ms, until_ms=until_ms)
    shadow_crit, shadow_info = shadow_review(
        _json_dir(REPORTS / "shadow" / "t2"), until_ms=until_ms
    )
    t2 = next((lv for lv in ledgers if lv.name == "T/v2"), None)
    t2_ok = t2 is not None and all(c.status in ("PASS", "NA") for c in t2.criteria)
    shadow_ok = all(c.status == "PASS" for c in shadow_crit)
    k1_ok = demo["t2_criteria"][0]["status"] == "PASS"
    watch_ok = watch_crit.status == "PASS"
    if t2_ok and shadow_ok and k1_ok and watch_ok:
        d1 = "자격 있음 — T/v2 P1~P6 · 그림자 S1~S5 · 데모 K1 모두 통과"
    else:
        d1 = "아직 자격 없음 또는 확인 필요 — FAIL·CHECK 항목의 원인을 먼저 기록한다"
    record = {
        "until": until,
        "generated": datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "ledgers": [asdict(lv) for lv in ledgers],
        "watch": {"criterion": asdict(watch_crit), **watch_info},
        "same_days": _same_days(ledgers),
        "shadow": {"criteria": [asdict(c) for c in shadow_crit], **shadow_info},
        "demo": demo,
        "d1": d1,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{until}.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=1, default=str) + "\n", encoding="utf-8"
    )
    text = render(record)
    (OUT / f"{until}.md").write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
