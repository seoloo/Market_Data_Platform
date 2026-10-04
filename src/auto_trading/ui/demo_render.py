"""데모·실거래 준비 화면 (순수 함수). 모든 동적 문자열은 이스케이프한다.

데모 자산은 **가짜 돈**이다 — Paper 원장·테스트넷 자산과 합산하지 않는다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from html import escape
from typing import Any

from auto_trading.ui.demo_data import DemoView

__all__ = ["demo_card", "demo_page"]

_KST = timedelta(hours=9)
_DAY_MS = 86_400_000
_EW_STALE_MS = 5 * 3_600_000
_MODEL_BPS, _ASSUMED_BPS = 10.23, 15.0
"""42 §7 모형(현물 L240 평균) · 백테스트 기준 가정(현물 0.15%)."""
_PHASE = {"IDLE": "대기", "PENDING": "예약 중", "OPEN": "보유 중"}
_EVENT = {
    "init": "시작",
    "trigger": "꼭짓점 확정",
    "placed": "예약",
    "entry_filled": "진입 체결",
    "entry_rejected": "예약 거절",
    "cancelled": "예약 취소",
    "closed": "청산",
    "time_exit": "30일 청산",
    "skip_passed": "건너뜀",
    "skip_small": "건너뜀",
    "setup_ignored_open": "보유 중 셋업",
    "orphan_position": "기록 밖 포지션",
    "error": "오류",
}


def _kst(ms: int, fmt: str = "%m-%d %H:%M") -> str:
    return (datetime.fromtimestamp(ms / 1000, tz=UTC) + _KST).strftime(fmt)


def _dot(level: str) -> str:
    cls = {"ok": "ok-bg", "warn": "warn-bg", "bad": "bad-bg"}.get(level, "mute-bg")
    return f'<span class="dot {cls}"></span>'


def _num(x: Any, digits: int = 2) -> str:
    try:
        return f"{float(x):,.{digits}f}"
    except (TypeError, ValueError):
        return escape(str(x))


def _qty(x: Any) -> str:
    try:
        return f"{float(x):,.6f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return escape(str(x))


# ---------------------------------------------------------------- 카드


def _t2_card(d: DemoView) -> str:
    if not d.t2_days:
        return _empty_card("데모 현물 T/v2", "var(--t2)", "기록 없음 — 매일 21:12 실행")
    last, first = d.t2_days[-1], d.t2_days[0]
    change = float(last.equity) / float(first.equity) - 1 if float(first.equity) else 0.0
    held = " · ".join(
        f"{escape(a)} {_qty(q)}" for a, q in sorted(last.holdings.items()) if a != "USDT"
    )
    cls = "pos" if change > 0 else ("neg" if change < 0 else "mute")
    return (
        '<div class="card" style="border-left-color:var(--t2)">'
        '<h3>현물 T/v2<span class="tag">DEMO</span></h3>'
        '<div class="sub">실거래 1단계 실행기 예행연습 · 지정가 L240</div>'
        f'<div class="big">{float(last.equity):,.2f} <span class="sub">USDT</span></div>'
        f'<div><span class="{cls}">{change * 100:+.2f}%</span> '
        f'<span class="mute">첫 기록 대비 · {len(d.t2_days)}일</span></div>'
        f'<div class="sub">{escape(last.status)}' + (f" · {held}" if held else "") + "</div></div>"
    )


def _o2_card(d: DemoView) -> str:
    r = d.o2_report
    if r is None:
        return _empty_card("선물 O/v2 추가분", "var(--o2)", "기록 없음 — 매일 21:14 실행")
    rep = r.get("report") or {}
    legs = rep.get("legs") or []
    state = str(rep.get("state", "실패" if r.get("error") else "?"))
    level = "ok" if state == "OK" else "warn"
    body = (
        " · ".join(
            f"{escape(str(g['symbol'])[:3])} {_qty(g['after'])} "
            f"<span class='mute'>스톱 {escape(str(g.get('stop') or '없음'))}</span>"
            for g in legs
        )
        or "포지션 없음"
    )
    plans = r.get("plans") or []
    weight = ""
    if plans:
        w = float(plans[0].get("weight") or 0)
        weight = f"T/v2 목표 {w:.1%} × 0.5 = 선물 {w * 0.5:.1%}"
    return (
        '<div class="card" style="border-left-color:var(--o2)">'
        '<h3>선물 O/v2 추가분<span class="tag">DEMO</span></h3>'
        '<div class="sub">실제 T/v2 목표 · −20% 비상 스톱 · ETH (BTC 10/08 뒤)</div>'
        f'<div class="big" style="font-size:17px">{body}</div>'
        f'<div class="sub">{_dot(level)}{escape(state)} · {escape(str(r.get("decision_date")))}'
        f" · {d.o2_days}일</div>"
        f'<div class="sub">{escape(weight)}</div></div>'
    )


def _ew_card(d: DemoView, now_ms: int) -> str:
    s = d.ew_state
    if s is None:
        return _empty_card("Elliott BTC", "var(--o3)", "기록 없음 — 상주 실행기")
    phase = str(s.get("phase", "IDLE"))
    last = int(s.get("last_scan_ms") or 0)
    alive = bool(last) and now_ms - last <= _EW_STALE_MS
    scan = f"{(now_ms - last) / 60_000:.0f}분 전 스캔" if last else "스캔 기록 없음"
    setups = sum(1 for e in d.ew_events if e.get("kind") == "trigger" and e.get("setup"))
    detail = ""
    if phase in ("PENDING", "OPEN"):
        detail = (
            f'<div class="sub">진입 {_num(s.get("entry"), 1)} · 손절 {_num(s.get("stop"), 1)}'
            f" · 목표 {_num(s.get('target'), 1)}</div>"
        )
    return (
        '<div class="card" style="border-left-color:var(--o3)">'
        '<h3>Elliott BTC 5x<span class="tag">DEMO</span></h3>'
        '<div class="sub">2·4파 되돌림 · 상위 10 중 6 합의 · BTCUSDC</div>'
        f'<div class="big" style="font-size:18px">{escape(_PHASE.get(phase, phase))}</div>'
        f"{detail}"
        f'<div class="sub">{_dot("ok" if alive else "warn")}{escape(scan)}'
        f" · 최근 셋업 {setups}</div></div>"
    )


def _shadow_card(d: DemoView) -> str:
    r = d.shadow
    if r is None:
        return _empty_card("그림자 T/v2", "var(--hold)", "기록 없음 — 매일 21:10 실행")
    findings = r.get("findings") or []
    crit = sum(1 for f in findings if f.get("level") == "CRITICAL")
    warn = sum(1 for f in findings if f.get("level") == "WARN")
    level = "bad" if crit else ("warn" if warn else "ok")
    value = r.get("account_value_usdt")
    return (
        '<div class="card" style="border-left-color:var(--hold)">'
        '<h3>그림자 T/v2<span class="tag">LIVE 읽기</span></h3>'
        '<div class="sub">실계좌 읽기만 · 주문 0 · 예정 금액 '
        f"{_num(r.get('allocation'), 0)} USDT</div>"
        f'<div class="big" style="font-size:18px">{escape(str(r.get("status")))}</div>'
        f'<div class="sub">{_dot(level)}CRITICAL {crit} · WARN {warn} · {d.shadow_days}일</div>'
        + (f'<div class="sub">실계좌 평가액 {_num(value)} USDT</div>' if value else "")
        + "</div>"
    )


def _empty_card(title: str, color: str, note: str) -> str:
    return (
        f'<div class="card" style="border-left-color:{color}">'
        f'<h3>{escape(title)}<span class="tag">DEMO</span></h3>'
        f'<div class="sub">{escape(note)}</div></div>'
    )


def demo_card(d: DemoView, now_ms: int) -> str:
    """운영 개요에 넣는 요약 카드 한 장."""
    t2 = f"현물 T/v2 {float(d.t2_days[-1].equity):,.0f}" if d.t2_days else "현물 T/v2 —"
    legs = ((d.o2_report or {}).get("report") or {}).get("legs") or []
    o2 = "선물 O/v2 " + (" · ".join(f"{g['symbol'][:3]} {_qty(g['after'])}" for g in legs) or "—")
    phase = _PHASE.get(str((d.ew_state or {}).get("phase", "")), "—")
    level = "bad" if d.unresolved or d.halt else "ok"
    return (
        '<a class="card" href="/demo" style="border-left-color:var(--t2);display:block">'
        '<h3>데모<span class="tag">가짜 돈</span></h3>'
        '<div class="sub">실제 전략 목표로 매일 체결 · 자세히 →</div>'
        f'<div style="margin-top:8px">{escape(t2)}</div><div>{escape(o2)}</div>'
        f"<div>Elliott BTC {escape(phase)}</div>"
        f'<div class="sub">{_dot(level)}미해결 주문 {d.unresolved}'
        + (f" · 긴급 정지: {escape(d.halt)}" if d.halt else "")
        + "</div></a>"
    )


# ---------------------------------------------------------------- 패널


def _readiness(d: DemoView, now_ms: int) -> str:
    rows: list[tuple[str, str, str]] = []
    rows.append(
        (
            "긴급 정지 스위치 (data/HALT)",
            _dot("warn" if d.halt else "ok") + ("켜짐" if d.halt else "꺼짐"),
            escape(d.halt or "새 주문 허용"),
        )
    )
    rows.append(
        (
            "데모 미해결 주문",
            _dot("bad" if d.unresolved else "ok") + f"{d.unresolved}건",
            "있으면 실행기가 새 주문을 내지 않는다 (fail-closed)",
        )
    )
    if d.t2_days:
        last = d.t2_days[-1].decision_ms
        behind = now_ms // _DAY_MS - (last + 1) // _DAY_MS
        rows.append(
            (
                "데모 T/v2 매일 실행",
                _dot("ok" if behind <= 1 else "warn") + f"{len(d.t2_days)}일 기록",
                f"마지막 결정일 {_kst(last + 1, '%Y-%m-%d')}",
            )
        )
    costs = [o.cost_bps for o in d.orders if o.cost_bps is not None]
    if costs:
        avg = sum(costs) / len(costs)
        level = "ok" if avg <= _ASSUMED_BPS else "warn"
        rows.append(
            (
                "데모 현물 체결 비용 (평균)",
                _dot(level) + f"{avg:.1f}bp · {len(costs)}건",
                f"모형 {_MODEL_BPS}bp · 백테스트 가정 {_ASSUMED_BPS:.0f}bp",
            )
        )
    if d.shadow is not None:
        crit = sum(1 for f in d.shadow.get("findings") or [] if f.get("level") == "CRITICAL")
        rows.append(
            (
                "그림자 운영 CRITICAL",
                _dot("bad" if crit else "ok") + f"{crit}건",
                "실계좌 키 권한·IP 제한·주문 0 점검",
            )
        )
    for item, note in (
        ("Phase 8 1단계 승인", "10/24 Paper·그림자·데모 결과를 보고 사용자가 정한다"),
        ("거래 권한 키 (현물 거래만·출금 금지·IP 제한)", "사용자가 Binance 에서 직접"),
        ("실계좌 현물 주문 어댑터", "승인 뒤 1 개만 추가 (데모 어댑터와 같은 모양)"),
    ):
        rows.append((item, _dot("mute") + "대기", note))
    body = "".join(
        f"<tr><td>{a}</td><td class='nowrap'>{b}</td><td class='mute'>{c}</td></tr>"
        for a, b, c in rows
    )
    return f'<div class="panel"><h2>실거래 1단계 준비 (문서 57 §4)</h2><table>{body}</table></div>'


def _orders_panel(d: DemoView) -> str:
    rows = []
    for o in d.orders:
        how = {True: "지정가", False: "시장가 추격", None: "—"}[o.maker]
        cost = "—" if o.cost_bps is None else f"{o.cost_bps:.1f}bp"
        side = "매수" if o.side == "BUY" else "매도"
        rows.append(
            f"<tr><td class='mute'>{_kst(o.created_ms)}</td><td>{escape(o.symbol)}</td>"
            f"<td>{side}</td><td class='num'>{_qty(o.qty)}</td><td>{escape(o.state)}</td>"
            f"<td>{how}</td><td class='num'>{_num(o.quote)}</td><td class='num'>{cost}</td></tr>"
        )
    table = "".join(rows) or "<tr><td colspan=8 class='mute'>주문 기록 없음</td></tr>"
    return (
        '<div class="panel"><h2>데모 현물 주문 (새것 먼저)</h2><table><tr><th>시각(KST)</th>'
        "<th>종목</th><th>방향</th><th class='num'>수량</th><th>상태</th><th>체결</th>"
        f"<th class='num'>명목(USDT)</th><th class='num'>비용</th></tr>{table}</table>"
        '<div class="foot">비용 = 주문 순간 중간가 대비 가격 차이 + 수수료. '
        "첫날(9/25) 주문은 옛 스크립트 기록(reports/demo_t2/days.jsonl)에서 읽는다.</div></div>"
    )


def _o2_panel(d: DemoView) -> str:
    r = d.o2_report
    if r is None:
        return ""
    rep = r.get("report") or {}
    lines = []
    for g in rep.get("legs") or []:
        orders = " → ".join(escape(str(x)) for x in g.get("orders") or []) or "주문 없음 (밴드 안)"
        lines.append(
            f"<tr><td>{escape(str(g.get('symbol')))}</td><td class='num'>{_qty(g.get('before'))}"
            f" → {_qty(g.get('after'))}</td><td>{escape(str(g.get('stop') or '—'))}</td>"
            f"<td class='mute'>{orders}</td></tr>"
        )
    fired = rep.get("fired_stops") or []
    notes = " · ".join(escape(str(n)) for n in [*(rep.get("notes") or []), *fired])
    return (
        '<div class="panel"><h2>데모 O/v2 선물 다리 (최근 실행)</h2><table><tr><th>종목</th>'
        f"<th class='num'>수량</th><th>스톱</th><th>주문</th></tr>{''.join(lines)}</table>"
        + (f'<div class="foot">{notes}</div>' if notes else "")
        + '<div class="foot">스톱 교체는 공백 없는 다리 방식(52 §4.1). '
        "일 손실 2%·계좌 정지 규칙은 데모에서 생략(문서 59 §1).</div></div>"
    )


def _ew_panel(d: DemoView) -> str:
    rows = []
    for e in d.ew_events[:15]:
        kind = str(e.get("kind"))
        detail = ""
        if kind == "trigger":
            s = e.get("setup")
            detail = f"시나리오 {e.get('scenarios')}개 · " + (
                f"셋업: {'롱' if int(s['direction']) > 0 else '숏'} "
                f"{s['agree']}/{s['top_n']} 합의 · 진입 {_num(s['entry'], 1)}"
                if s
                else "셋업 없음"
            )
        elif kind == "entry_filled":
            gap = int(e.get("protect_ms") or 0) / 1000
            detail = f"{_qty(e.get('qty'))} @{_num(e.get('price'), 1)} · 보호까지 {gap:.1f}초"
        elif kind == "closed":
            detail = f"{e.get('how')} @{_num(e.get('price'), 1)} · {e.get('r')}R"
        elif kind in ("cancelled", "error"):
            detail = str(e.get("why") or e.get("error") or "")
        rows.append(
            f"<tr><td class='mute'>{_kst(int(e.get('at_ms') or 0))}</td>"
            f"<td><span class='tag'>{escape(_EVENT.get(kind, kind))}</span></td>"
            f"<td>{escape(detail)}</td></tr>"
        )
    table = "".join(rows) or "<tr><td colspan=3 class='mute'>사건 없음</td></tr>"
    return (
        '<div class="panel"><h2>Elliott 데모 사건 (새것 먼저)</h2>'
        f"<table>{table}</table>"
        '<div class="foot">검증이 아닌 관찰용 데모(문서 56 §0) · '
        "셋업은 한 달 1~3번 정도</div></div>"
    )


def demo_page(d: DemoView, now_ms: int) -> str:
    cards = _t2_card(d) + _o2_card(d) + _ew_card(d, now_ms) + _shadow_card(d)
    return (
        '<div class="banner info">'
        "데모는 <b>가짜 돈</b>이다(Binance 데모 모드). Paper 원장·테스트넷 자산과 합산하지 않는다. "
        "목적은 실거래와 같은 코드로 주문 흐름·체결 비용을 매일 확인하는 것이다.</div>"
        f'<div class="cards">{cards}</div>'
        f'<div class="grid2"><div>{_orders_panel(d)}{_o2_panel(d)}{_ew_panel(d)}</div>'
        f"<div>{_readiness(d, now_ms)}</div></div>"
    )
