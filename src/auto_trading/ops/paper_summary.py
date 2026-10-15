"""텔레그램 'Paper' 명령 — 가상 장부(Paper) 한눈에 보기 (읽기 전용, 주문 없음).

사용자 (2026-10-15): "텔레그램으로 볼 수 있게 메뉴에 추가해줘" — 휴대폰에서 VPN 없이 Paper 를 본다.
저장된 장부 · 보고서만 읽는다
(코인 Paper DB, ``reports/*/latest.json``, ``data/mf_paper/state.json``).
읽지 못한 장부는 건너뛰고, 아무것도 없으면 그렇게 적는다.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

__all__ = ["DAILY_AT", "daily_due", "paper_text"]

DAILY_AT = (21, 30)
"""매일 자동 요약 시각(KST) — 코인 Paper(21:00 ~ 21:06) · 주식 Paper(21:10 ~ 21:17) 뒤."""


def daily_due(now_kst_hm: tuple[int, int], today: str, last_sent: str | None) -> bool:
    """오늘 21:30 이 지났고 오늘 아직 안 보냈으면 True (늦게 켜져도 그날 안에는 보낸다)."""
    return now_kst_hm >= DAILY_AT and last_sent != today


_MF = (
    ("GBG", "Glide·채권·금"),
    ("NBG", "나스닥·채권·금"),
    ("MIX", "나스닥·추세 ETF"),
    ("LD_M3", "Glide 비교 · 변동성"),
)


def _read(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _pct(x: Any) -> str:
    try:
        return f"{float(x) * 100:+.1f}%"
    except (TypeError, ValueError):
        return "—"


def _usd(x: Any) -> str:
    try:
        return f"${float(x):,.0f}"
    except (TypeError, ValueError):
        return "—"


def _won(x: Any) -> str:
    try:
        return f"{float(x):,.0f}원"
    except (TypeError, ValueError):
        return "—"


CoinReader = Callable[[], list[tuple[str, float, float]]]
"""(이름, 시작 평가액, 지금 평가액) 목록 — 코인 Paper DB 를 읽는 함수(시험에서 바꿔 끼운다)."""


def coin_ledgers(root: Path) -> list[tuple[str, float, float]]:
    """코인 Paper DB(T/v2 · O 계열)의 시작 · 마지막 평가액."""
    from auto_trading.storage.db import connect_read_only
    from auto_trading.storage.repositories.paper_days import PaperDayRepository
    from auto_trading.storage.repositories.paper_overlay_days import OverlayPaperDayRepository

    out: list[tuple[str, float, float]] = []
    try:
        conn = connect_read_only(root / "data" / "trading.db")
    except Exception:  # DB 없음 · 잠김 — 코인 칸만 비운다
        return out
    try:
        days = PaperDayRepository(conn)
        for sid, name in (
            ("t2_long", "T/v2"),
            ("t2_shadow_10000", "T/v2 그림자"),
            ("t2_upbit", "T/v2 업비트"),
        ):
            rows = list(reversed(days.recent(sid, 1_000_000)))
            if rows:
                out.append((name, float(rows[0].equity), float(rows[-1].equity)))
        overlay = OverlayPaperDayRepository(conn)
        for sid, name in (("o1_overlay", "O/v1"), ("o2_stop", "O/v2"), ("o3_2x", "O/v3")):
            ov = overlay.all(sid)
            if ov:
                out.append((name, float(ov[0].equity), float(ov[-1].equity)))
    finally:
        conn.close()
    return out


def paper_text(root: Path, coins: CoinReader | None = None) -> str:
    """휴대폰에서 읽기 쉬운 Paper 요약 (한 줄에 장부 하나)."""
    lines = ["📒 Paper 요약 (가상 장부 · 주문 없음)"]

    coin_rows = coins() if coins is not None else coin_ledgers(root)
    if coin_rows:
        lines += ["", "🪙 코인 (시작 1 만, 업비트는 1,000 만 원)"]
        for name, first, last in coin_rows:
            shown = _won(last) if "업비트" in name else f"{last:,.0f}"
            lines.append(f"• {name} {shown} ({_pct(last / first - 1 if first else None)})")

    ag = _read(root / "reports" / "ag_paper" / "latest.json")
    ags = _read(root / "reports" / "ags_paper" / "latest.json")
    if ag or ags:
        lines += ["", "⚡ AG · AGS (시작 $1,000)"]
        for key, b in ((ag or {}).get("books") or {}).items():
            lines.append(f"• {key} {_usd(b.get('value_usd'))} ({_pct(b.get('return'))})")
        for key, b in ((ags or {}).get("books") or {}).items():
            ret = b.get("return_since_start")
            shown = "시작 전" if ret is None else _pct(ret)
            lev = f"{float(b.get('short_leverage') or 1):g}"
            lines.append(f"• {key} ({b.get('long', 'AG-CR')} + 숏 {lev} 배) {shown}")

    us: list[str] = []
    for folder, name in (
        ("lt_paper", "LT 주도주"),
        ("nq_paper", "NQ"),
        ("sn20_paper", "SN20"),
        ("sn20c_paper", "SN20-C"),
    ):
        r = _read(root / "reports" / folder / "latest.json")
        if r:
            us.append(f"• {name} {_usd(r.get('equity_usd'))} ({_pct(r.get('return_since_start'))})")
    qs = _read(root / "reports" / "qs_paper" / "latest.json")
    for key, b in ((qs or {}).get("books") or {}).items():
        if key in ("QS", "A"):
            us.append(f"• {key} {_usd(b.get('value_usd'))} ({_pct(b.get('return_on_invested'))})")
    mf = (_read(root / "data" / "mf_paper" / "state.json") or {}).get("books") or {}
    for key, name in _MF:
        hist = (mf.get(key) or {}).get("history") or []
        if hist:
            last = float(hist[-1][1])
            us.append(f"• {name} {_usd(last)} ({_pct(last / 1000 - 1)})")
    if us:
        lines += ["", "🇺🇸 미국 주식 (시작 $1,000)", *us]

    kr = _read(root / "reports" / "kr_shadow" / "latest.json")
    paper = (kr or {}).get("paper") or {}
    if paper:
        lines += ["", f"🇰🇷 국장 주도주 (시작 1,000 만 원 · {kr.get('date') if kr else ''})"]
        for key, name in (
            ("price_rule", "① 가격"),
            ("flow_rule", "② 수급"),
            ("foreign_rule", "④ 외국인"),
        ):
            rule = paper.get(key) or {}
            if rule:
                held = len(rule.get("positions") or [])
                lines.append(f"• {name} {_won(rule.get('value_krw'))} · 보유 {held} 종목")
        lines.append(f"• 고르게 들기 {_won(paper.get('equal_weight_krw'))}")
        above = (kr or {}).get("kospi_above_200d")
        lines.append(f"  KOSPI 200 일선 {'위 — 살 수 있음' if above else '아래 — 새로 안 삼'}")
    demo = _read(root / "data" / "ag4s2_testnet" / "state.json")
    dh = (demo or {}).get("history") or []
    if dh:
        v, base = float(dh[-1][1]), float((demo or {}).get("alloc") or 1000)
        lines.append(
            f"• Falcon 선물 데모(가짜 돈, 시작 {base:,.0f}) {v:,.0f} USDC ({_pct(v / base - 1)})"
        )
    namu = _read(root / "data" / "namu_follow" / "ledger.json")
    hist = (namu or {}).get("history") or []
    if hist:
        last = float(hist[-1][1])
        lines.append(
            f"• 나무 모의(② 따라가기, 시작 700 만) {_won(last)} ({_pct(last / 7_000_000 - 1)})"
        )

    if len(lines) == 1:
        lines.append("저장된 Paper 기록이 아직 없어요.")
    lines += ["", "자세히: 오르빗 → Paper"]
    return "\n".join(lines)
