# ruff: noqa: E501 — HTML · CSS 문자열이 길다
"""설정 화면 추가 쪽 — '연결 · 키' · '자동 실행' (2026-10-10 사용자). 스크립트 없이 폼만.

* 키 입력 칸은 비밀번호 칸(●●●). 저장한 값은 **다시 화면에 보내지 않는다** — '설정됨 · 저장 날짜' 만.
* 자동 실행은 이 PC 의 Windows 예약을 보여 주고, 고른 묶음을 켜고 끈다(이미 있는 예약은 덮어쓰지 않음).
* 큰 설정 폼 안에 폼을 둘 수 없어서, 이 쪽의 입력은 ``form="…"`` 로 바깥의 작은 폼에 붙는다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from html import escape
from typing import Any

from auto_trading.ops.env_keys import KEYS, SERVICES, TOSS_SWITCH
from auto_trading.ops.task_setup import GROUPS, TASKS, TaskState

__all__ = ["EXTRA_CSS", "keys_tab", "tasks_tab"]

EXTRA_CSS = """
.set-svc{border-bottom:1px solid var(--line);padding:14px 0}
.set-svc:last-of-type{border-bottom:0}
.set-svc-head{display:flex;flex-wrap:wrap;align-items:center;gap:10px;justify-content:space-between}
.set-svc-head h3{margin:0;font-size:16px}
.set-svc details{margin:6px 0 8px;font-size:13px;color:var(--muted)}
.set-svc summary{cursor:pointer;color:var(--blue);font-weight:700}
.set-key{display:grid;grid-template-columns:150px minmax(0,1fr) auto;align-items:center;gap:10px;
margin-top:8px}
.set-key .set-input{min-height:44px}
.set-key .set-input input{font-size:15px;font-weight:600;letter-spacing:.08em}
.set-state{font-size:12px;font-weight:800;padding:4px 10px;border-radius:8px;white-space:nowrap}
.set-state.on{background:#e3f6ee;color:#0b5c40}.set-state.off{background:var(--soft);color:var(--muted)}
.set-state.bad{background:#fdecee;color:#a3283c}.set-state.warn{background:#fff1d6;color:#8a5a00}
html[data-theme=dark] .set-state.on{background:#174d43;color:#73e9c9}
html[data-theme=dark] .set-state.bad{background:#4a1d27;color:#ff9db0}
html[data-theme=dark] .set-state.warn{background:#4a3510;color:#ffd28a}
.set-test{min-height:38px;padding:0 14px;border:1px solid var(--line);border-radius:10px;
background:var(--surface);color:var(--ink);font:inherit;font-weight:700;cursor:pointer}
.set-test-res{margin:6px 0 0;font-size:13px;font-weight:700}
.set-test-res.ok{color:#0b7a55}.set-test-res.bad{color:#c23a4f}
html[data-theme=dark] .set-test-res.ok{color:#57dfc0}
.set-actions{display:flex;flex-wrap:wrap;gap:8px;justify-content:flex-end;margin-top:14px}
.set-primary{min-height:46px;padding:0 20px;border:0;border-radius:12px;background:var(--blue);
color:#fff;font:inherit;font-weight:800;cursor:pointer}
html[data-theme=dark] .set-primary{color:#06122b}
.set-groups{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px 18px;margin:6px 0 12px}
.set-groups label{display:flex;gap:10px;align-items:flex-start;padding:10px 12px;border:1px solid var(--line);
border-radius:12px;cursor:pointer}
.set-groups input{margin-top:3px;width:18px;height:18px;accent-color:var(--blue)}
.set-groups small{display:block;color:var(--muted);font-size:12px}
.set-tasks{width:100%;border-collapse:collapse;font-size:13px}
.set-tasks th{text-align:left;font-weight:600;color:var(--muted);padding:6px 4px;border-bottom:1px solid var(--line)}
.set-tasks td{padding:8px 4px;border-bottom:1px solid var(--line);vertical-align:middle}
.set-tasks td small{display:block;color:var(--muted)}
@media(max-width:760px){.set-key{grid-template-columns:1fr}.set-groups{grid-template-columns:1fr}
.set-tasks th:nth-child(3),.set-tasks td:nth-child(3){display:none}}
"""

_RESULT = {0: "성공", 267009: "실행 중", 267011: "아직 안 돎", 267014: "멈춤(사용자)"}


def _hidden_token(token: str) -> str:
    return f'<input type="hidden" name="token" value="{escape(token)}">'


def keys_tab(
    status: Mapping[str, Mapping[str, Any]],
    *,
    token: str,
    errors: Mapping[str, str] | None = None,
    tests: Mapping[str, tuple[bool, str]] | None = None,
) -> tuple[str, str]:
    """(쪽 HTML, 큰 폼 밖에 둘 작은 폼들)."""
    errors, tests = errors or {}, tests or {}
    blocks = []
    test_forms = []
    for svc, title, howto in SERVICES:
        rows = []
        for k in (k for k in KEYS if k.service == svc):
            st = status.get(k.env) or {}
            on = bool(st.get("set"))
            saved = str(st.get("saved") or "")
            badge = (
                f'<span class="set-state on">설정됨{" · " + escape(saved) if saved else ""}</span>'
                if on
                else '<span class="set-state off">없음</span>'
            )
            err = errors.get(k.env)
            rows.append(
                f'<div class="set-key"><label for="key-{k.env}"><b>{escape(k.label)}</b>'
                f'<small style="display:block;color:var(--muted);font-size:11px">{k.env}</small></label>'
                f'<div class="set-input{" set-err-input" if err else ""}">'
                f'<input type="password" id="key-{k.env}" name="{k.env}" form="keys-f" value="" '
                'autocomplete="new-password" spellcheck="false" autocapitalize="off" '
                f'placeholder="{"새 값을 넣을 때만 · 비우면 그대로" if on else "붙여 넣기"}"></div>'
                f"{badge}</div>" + (f'<p class="set-err">{escape(err)}</p>' if err else "")
            )
        if svc == "toss":
            trading = bool((status.get(TOSS_SWITCH) or {}).get("set"))
            rows.append(
                '<div class="set-toggle"><label for="toss_trading"><b>토스 실제 주문 허용</b>'
                "<small>켜야 Glide · 토스 적립 주문이 실제로 나가요 (끄면 계획만)</small></label>"
                '<input type="hidden" name="toss_trading" value="off" form="keys-f">'
                f'<input type="checkbox" class="set-switch danger" id="toss_trading" name="toss_trading" '
                f'value="on" form="keys-f" role="switch"{" checked" if trading else ""}></div>'
            )
        test_btn = ""
        res = ""
        if svc in {"toss", "upbit", "telegram", "supabase"}:
            fid = f"test-{svc}-f"
            test_forms.append(
                f'<form id="{fid}" method="post" action="/settings/test">{_hidden_token(token)}'
                f'<input type="hidden" name="service" value="{svc}"></form>'
            )
            test_btn = f'<button type="submit" class="set-test" form="{fid}">연결 시험</button>'
            if svc in tests:
                ok, msg = tests[svc]
                res = (
                    f'<p class="set-test-res {"ok" if ok else "bad"}" role="status">'
                    f"{'✅' if ok else '❌'} {escape(msg)}</p>"
                )
        blocks.append(
            f'<div class="set-svc"><div class="set-svc-head"><h3>{escape(title)}</h3>{test_btn}</div>'
            f"<details><summary>발급 방법</summary><p>{escape(howto)}</p></details>"
            + "".join(rows)
            + res
            + "</div>"
        )
    note = (
        '<div class="set-note bad" role="alert">고칠 칸이 있어요 — 빨간 글씨를 확인해 주세요 '
        "(넣은 값은 보안상 지웠어요, 다시 붙여 넣어 주세요)</div>"
        if errors
        else ""
    )
    tab = (
        '<section class="set-card"><h2>연결 · 키</h2>'
        '<p class="set-sub">발급받은 키를 넣으면 이 PC 프로젝트 폴더의 <b>.env</b> 에 저장돼요. '
        "저장한 값은 화면에 다시 보이지 않아요(설정됨 · 날짜만). 키는 이 PC 밖으로 나가지 않아요.</p>"
        f"{note}{''.join(blocks)}"
        '<div class="set-actions"><button type="submit" class="set-primary" form="keys-f">키 저장</button>'
        "</div></section>"
    )
    forms = (
        f'<form id="keys-f" method="post" action="/settings/keys" autocomplete="off">'
        f"{_hidden_token(token)}</form>" + "".join(test_forms)
    )
    return tab, forms


def tasks_tab(
    states: Mapping[str, TaskState] | None,
    *,
    token: str,
    defaults: Sequence[str],
    note: str = "",
    error: str = "",
) -> tuple[str, str]:
    """(쪽 HTML, 작은 폼). ``states`` 가 None 이면 이 PC 에서 예약을 못 읽음(Windows 아님 등)."""
    picked = set(defaults)
    groups = "".join(
        f'<label><input type="checkbox" name="g_{g}" value="on" form="tasks-f"'
        f"{' checked' if g in picked else ''}><span><b>{escape(t)}</b><small>{escape(s)}</small>"
        "</span></label>"
        for g, t, s in GROUPS
    )
    rows = []
    gname = {g: t for g, t, _ in GROUPS}
    for t in TASKS:
        st = (states or {}).get(t.name)
        if states is None:
            badge = '<span class="set-state off">알 수 없음</span>'
        elif st is None:
            badge = '<span class="set-state off">없음</span>'
        elif st.state == "Disabled":
            badge = '<span class="set-state warn">꺼짐</span>'
        else:
            badge = (
                f'<span class="set-state on">켜짐</span>'
                f"<small>다음 {escape(st.next_run) if st.next_run else '—'}</small>"
            )
        last = ""
        if st is not None and st.last_result is not None:
            word = _RESULT.get(st.last_result, f"실패 {st.last_result}")
            last = f"{escape(word)}" + (
                f"<small>{escape(st.last_run)}</small>" if st.last_run else ""
            )
        rows.append(
            f"<tr><td><b>{escape(t.what)}</b><small>{escape(gname.get(t.group, t.group))}</small></td>"
            f"<td>{escape(t.when)}</td><td>{last}</td><td>{badge}</td></tr>"
        )
    msg = ""
    if note:
        msg = f'<div class="set-note ok" role="status">{escape(note)}</div>'
    if error:
        msg = f'<div class="set-note bad" role="alert">{escape(error)}</div>'
    if states is None:
        msg += (
            '<div class="set-note bad">이 PC 의 Windows 예약을 읽지 못했어요 '
            "(Windows 에서만 돼요)</div>"
        )
    confirm = (
        '<div popover id="tasks-on-ask" class="set-modal" role="alertdialog" '
        'aria-labelledby="tasks-on-t"><h3 id="tasks-on-t">정말 자동 실행을 켜시겠습니까?</h3>'
        "<p>고른 묶음에서 없는 예약은 새로 만들고, 꺼진 예약은 다시 켜요. 이미 있는 예약은 바꾸지 "
        "않아요. 켜진 뒤부터 <b>실제 주문이 정해진 시각에 나가요.</b></p>"
        '<div><button type="button" popovertarget="tasks-on-ask" popovertargetaction="hide">취소</button>'
        '<button type="submit" form="tasks-f" name="action" value="on" class="set-danger-btn">켜기</button>'
        "</div></div>"
        '<div popover id="tasks-off-ask" class="set-modal" role="alertdialog" '
        'aria-labelledby="tasks-off-t"><h3 id="tasks-off-t">정말 자동 실행을 끄시겠습니까?</h3>'
        "<p>고른 묶음의 예약을 '사용 안 함' 으로 바꿔요(지우지 않음). 꺼진 동안은 매수 · 알림이 "
        "나가지 않아요. 다시 켜기로 되돌릴 수 있어요.</p>"
        '<div><button type="button" popovertarget="tasks-off-ask" popovertargetaction="hide">취소</button>'
        '<button type="submit" form="tasks-f" name="action" value="off" class="set-danger-btn">끄기</button>'
        "</div></div>"
    )
    tab = (
        '<section class="set-card"><h2>자동 실행</h2>'
        '<p class="set-sub">쓸 묶음을 고르고 켜면 이 PC 의 Windows 작업 스케줄러에 예약이 들어가요 '
        "(PowerShell 로 자동 등록, 관리자 권한 필요 없음). PC 가 켜져 있고 로그인돼 있어야 돌아요.</p>"
        f'{msg}<div class="set-groups">{groups}</div>'
        '<div style="overflow-x:auto"><table class="set-tasks"><tr><th>하는 일</th><th>언제</th>'
        f"<th>마지막 결과</th><th>이 PC</th></tr>{''.join(rows)}</table></div>"
        '<div class="set-actions">'
        '<button type="button" class="set-del" popovertarget="tasks-off-ask">고른 묶음 끄기</button>'
        '<button type="button" class="set-primary" popovertarget="tasks-on-ask">고른 묶음 켜기</button>'
        f"</div>{confirm}</section>"
    )
    forms = (
        f'<form id="tasks-f" method="post" action="/settings/tasks">{_hidden_token(token)}</form>'
    )
    return tab, forms
