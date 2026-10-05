"""주도주 '처음 잡힘' 알림 화면 (IDEAS 7) — 저장된 알림 보고서만 읽는다. 매매 아님.

출처: ``reports/leader_alert/latest.json`` (``python -m scripts.leader_alert``, 매일 21:35 KST).
모든 동적 문자열은 이스케이프한다.
"""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any

from auto_trading.ui.lt_render import _read

__all__ = ["leader_alert_page"]

_STATUS_CLASS = {"돌파": "pos", "형성 중": "warn", "아님": "mute", "자료 부족": "mute"}


def _section(sec: dict[str, Any]) -> str:
    rows = []
    for it in sec.get("new") or []:
        t = it.get("triangle") or {}
        gap = t.get("gap_to_resistance")
        status = str(t.get("status", "—"))
        name, code = str(it.get("name")), str(it.get("code"))
        code_tag = f" <span class='mute'>{escape(code)}</span>" if code != name else ""
        rows.append(
            f"<tr><td>{escape(str(it.get('list')))}</td>"
            f"<td>{escape(name)}{code_tag}</td>"
            f"<td class='{_STATUS_CLASS.get(status, 'mute')}'>{escape(status)}</td>"
            f"<td class='num'>{'—' if gap is None else f'{float(gap):+.1%}'}</td>"
            f"<td class='num'>{int(t.get('touches') or 0)}</td></tr>"
        )
    body = "".join(rows) or "<tr><td colspan=5 class='mute'>새로 들어온 종목 없음</td></tr>"
    few = (
        f" · 비교할 과거 기록 {int(sec.get('history') or 0)} 번 — 처음엔 거의 모두 새로 보임"
        if int(sec.get("history") or 0) < 3
        else ""
    )
    return (
        f'<div class="panel"><h2>{escape(str(sec.get("market")))} · '
        f"{escape(str(sec.get('date')))} 종가</h2>"
        f"<div class='sub'>새로 들어온 종목 {len(sec.get('new') or [])}{few}</div>"
        "<table><tr><th>목록</th><th>종목</th><th>삼각형</th><th class='num'>저항선 대비</th>"
        f"<th class='num'>고점 닿음</th></tr>{body}</table></div>"
    )


def leader_alert_page(data: Any) -> str:
    root = getattr(data, "root", None) or Path()
    latest = _read(root / "reports" / "leader_alert" / "latest.json")
    intro = (
        '<div class="panel"><h2>주도주 처음 잡힘 알림</h2>'
        '<p class="mute">미국 주도주 Paper(문서 115)와 국장 주도주 기록(문서 143)의 주도주 목록에 '
        "<b>최근 20 번의 기록에 없던 종목</b>을 매일 모아 보여 준다. 어센딩 트라이앵글 상태는 "
        "<b>검증하지 않은 참고 규칙</b>이다(IDEAS 6 연구 전). 매매 신호가 아니다.</p></div>"
    )
    if latest is None:
        return intro + '<div class="panel"><p class="mute">아직 알림 기록이 없다.</p></div>'
    sections = "".join(_section(s) for s in latest.get("sections") or [])
    rule = escape(str(latest.get("rule", "")))
    sent = "보냄" if latest.get("sent") else "안 보냄(새 종목 없음·텔레그램 미설정·--no-send)"
    foot = (
        f'<div class="panel"><div class="foot">삼각형 판정: {rule}<br>'
        f"생성 {escape(str(latest.get('generated_at')))} · 텔레그램 {escape(sent)} · "
        "국장은 종가만 있어 종가 기준.</div></div>"
    )
    return intro + sections + foot
