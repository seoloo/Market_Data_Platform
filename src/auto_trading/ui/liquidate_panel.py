"""설정 '비상 정지' 쪽의 **전부 정리** 칸 (사용자 2026-10-11).

버튼마다 '정말 …?' 확인 창을 거친다. 서버는 ``POST /settings/liquidate`` 를 받으면 정리 실행기
(``python -m scripts.liquidate start <범위> 오르빗``)를 따로 띄울 뿐 — 이 화면 · 서버는 키를 읽거나
주문하지 않는다. 그룹 정지 풀기(``POST /settings/unhalt``)는 정지 파일만 지운다.
휴대폰(Tailscale)에서는 설정이 보기 전용이라 텔레그램 '정리' 를 쓴다.
"""

from __future__ import annotations

from collections.abc import Mapping
from html import escape

from auto_trading.ops.kill_switch import GROUPS
from auto_trading.ops.liquidate import SCOPES

__all__ = ["liquidate_panel"]

_ASK = {
    "all": "Vega · Deneb · 자동 적립 · Altair 를 모두",
    "vega": "Vega(업비트 BTC · ETH · SOL)를",
    "deneb": "Deneb(토스 TQQQ · SCHD Deneb 몫)를",
    "acc": "자동 적립(미국 · 국내)을",
    "altair": "Altair(바이낸스 선물 BTC · ETH 포지션)를",
}


def liquidate_panel(
    token: str, halts: Mapping[str, str | None], last_job: str = ""
) -> tuple[str, str]:
    """(설정 안에 넣을 칸, 큰 폼 밖에 둘 작은 폼들)."""
    buttons = []
    modals = []
    forms = []
    for scope, label in SCOPES.items():
        fid, mid = f"liq-f-{scope}", f"liq-ask-{scope}"
        buttons.append(
            f'<button type="button" class="set-del" popovertarget="{mid}">{escape(label)}</button>'
        )
        modals.append(
            f'<div popover id="{mid}" class="set-modal" role="alertdialog" '
            f'aria-labelledby="{mid}-t"><h3 id="{mid}-t">'
            f"정말 {escape(_ASK[scope])} 정리할까요?</h3>"
            "<p>① 그 그룹의 새 매수를 멈추고 ② 토스 미체결 매수 주문을 취소한 뒤 ③ 보유를 "
            "지정가로 팔아요(시장가 안 씀). 업비트 · 바이낸스는 바로, 토스는 정규장에서 — "
            "닫혀 있으면 열린 뒤 팔아요. 결과는 텔레그램으로 와요. 되돌릴 수 없어요.</p>"
            f'<div><button type="button" popovertarget="{mid}" popovertargetaction="hide">취소'
            f'</button><button type="submit" form="{fid}" class="set-danger-btn">정리 시작'
            "</button></div></div>"
        )
        forms.append(
            f'<form id="{fid}" method="post" action="/settings/liquidate">'
            f'<input type="hidden" name="token" value="{escape(token)}">'
            f'<input type="hidden" name="scope" value="{scope}"></form>'
        )
    rows = []
    for g, name in GROUPS.items():
        reason = halts.get(g)
        if reason:
            fid = f"unh-f-{g}"
            rows.append(
                f'<p class="set-note bad">⛔ {escape(name)} 정지 중 — {escape(reason)} '
                f'<button type="submit" form="{fid}" class="set-del">풀기</button></p>'
            )
            forms.append(
                f'<form id="{fid}" method="post" action="/settings/unhalt">'
                f'<input type="hidden" name="token" value="{escape(token)}">'
                f'<input type="hidden" name="group" value="{g}"></form>'
            )
    state = "".join(rows) or '<p class="set-note ok">그룹 정지 없음</p>'
    last = (
        f'<pre class="set-help" style="white-space:pre-wrap">{escape(last_job)}</pre>'
        if last_job
        else ""
    )
    section = (
        '<section class="set-card set-danger" id="set-liquidate">'
        "<h2>전부 정리 (보유 팔기)</h2>"
        '<p class="set-sub">비상 정지는 새 주문만 막아요. '
        "이건 <b>갖고 있는 것을 파는</b> 버튼이에요. "
        "휴대폰에서는 텔레그램에 '정리' 를 보내세요. 풀면 각 전략이 자기 규칙대로 다시 사요.</p>"
        f'<div style="display:flex;flex-wrap:wrap;gap:8px">{"".join(buttons)}</div>'
        f"{''.join(modals)}{state}{last}</section>"
    )
    return section, "".join(forms)
