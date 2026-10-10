"""오르빗 설정 화면 (사용자 2026-10-10 시안 확정). 스크립트 없이 폼만 쓴다.

흐름: ``GET /settings`` → [저장…] ``POST /settings/review`` (확인 창: 이전 → 이후 · 적용 시점)
→ [저장] ``POST /settings/save`` → ``/settings?saved=N``. 값 검사는 두 번(확인 · 저장) 한다.
매매 규칙(연구로 정한 값)은 잠금으로 보이기만 한다. '준비 중' 칸은 아직 실행기에 연결 전이다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from html import escape
from typing import Any

from auto_trading.ops.user_settings import ACC_SYMBOLS, Change, show

_CSS = """
.set-wrap{max-width:1180px;margin:0 auto;padding:8px 0 40px}
.set-head h1{margin:0 0 4px;font-size:30px;letter-spacing:-.02em}
.set-head p{margin:0 0 18px;color:var(--muted)}
.set-row{display:flex;flex-wrap:wrap;gap:22px;align-items:flex-start}
.set-menu{flex:1 1 200px;max-width:240px;display:flex;flex-direction:column;gap:4px;
background:var(--surface);border:1px solid var(--line);border-radius:20px;padding:10px}
.set-menu a{display:flex;align-items:center;min-height:46px;padding:0 14px;border-radius:14px;
color:var(--ink);text-decoration:none;font-weight:600}
.set-menu a:hover{background:var(--soft);color:var(--blue)}
.set-body{flex:999 1 560px;min-width:0;display:flex;flex-direction:column;gap:16px}
.set-card{background:var(--surface);border:1px solid var(--line);border-radius:20px;
padding:20px 22px;scroll-margin-top:90px}
.set-card h2{margin:0 0 4px;font-size:19px}
.set-card>p.set-sub{margin:0 0 14px;color:var(--muted);font-size:14px}
.set-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}
.set-field label,.set-field .set-label{display:block;font-weight:700;margin-bottom:6px}
.set-input{display:flex;align-items:center;gap:8px;border:1px solid var(--line);border-radius:12px;
padding:0 14px;min-height:48px;background:var(--surface)}
.set-input input,.set-input select{border:0;outline:none;flex:1;min-width:0;font:inherit;
font-size:17px;font-weight:700;color:var(--ink);background:transparent}
.set-input span{color:var(--muted)}
.set-help{margin:6px 0 0;font-size:13px;color:var(--muted)}
.set-err{margin:6px 0 0;font-size:13px;color:#c23a4f;font-weight:700}
.set-err-input{border-color:#c23a4f}
.set-toggle{display:flex;align-items:center;justify-content:space-between;gap:14px;padding:13px 0;
border-bottom:1px solid var(--line)}
.set-toggle:last-child{border-bottom:0}
.set-toggle b{display:block}.set-toggle small{color:var(--muted);font-size:13px}
.set-switch{appearance:none;-webkit-appearance:none;width:52px;height:30px;border-radius:15px;
background:#c3d3e8;position:relative;cursor:pointer;flex-shrink:0;margin:0}
.set-switch::after{content:"";position:absolute;top:3px;left:3px;width:24px;height:24px;
border-radius:12px;background:#fff;box-shadow:0 1px 3px rgba(16,27,73,.3);transition:left .15s}
.set-switch:checked{background:var(--blue)}.set-switch:checked::after{left:25px}
.set-switch.danger:checked{background:#c23a4f}
.set-switch:disabled{opacity:.5;cursor:not-allowed}
.set-switch:focus-visible{outline:3px solid var(--blue);outline-offset:2px}
.set-lock{background:var(--soft);border:1px dashed var(--line);border-radius:20px;padding:16px 20px}
.set-lock h3{margin:0 0 8px;font-size:15px}
.set-lock div{display:flex;justify-content:space-between;gap:12px;padding:6px 0;font-size:14px}
.set-lock span{color:var(--muted)}
.set-soon{display:inline-block;font-size:11px;font-weight:800;padding:2px 8px;border-radius:8px;
background:var(--soft);color:var(--muted);margin-left:6px;vertical-align:middle}
.set-acc{display:flex;flex-wrap:wrap;align-items:center;gap:12px;padding:12px 0;
border-bottom:1px solid var(--line)}
.set-badge{font-size:12px;font-weight:800;padding:4px 10px;border-radius:8px;background:#e6f0ff;
color:#12509c}
.set-badge.kr{background:#fff1e0;color:#8a4b00}
.set-acc .set-input{min-height:44px;width:130px}
.set-acc b{min-width:64px}
.set-acc small{flex:1 1 160px;color:var(--muted);font-size:13px}
.set-danger{border:2px solid #f0c4cb}
.set-danger h2{color:#a3283c}
.set-bar{position:sticky;bottom:12px;display:flex;flex-wrap:wrap;align-items:center;
justify-content:space-between;gap:12px;background:#101b49;color:#fff;border-radius:18px;
padding:14px 18px 14px 22px;box-shadow:0 10px 30px rgba(16,27,73,.25)}
.set-bar small{display:block;color:#c9d6f2}
.set-bar a,.set-bar button{min-height:46px;padding:0 20px;border-radius:12px;font:inherit;
font-weight:800;display:inline-flex;align-items:center;text-decoration:none;cursor:pointer}
.set-bar a{border:1px solid #5a6a99;color:#fff}
.set-bar button{border:0;background:#4fa8ff;color:#06143a}
.set-note{padding:12px 16px;border-radius:14px;font-weight:700}
.set-note.ok{background:#e3f6ee;color:#0b5c40}.set-note.bad{background:#fdecee;color:#a3283c}
.set-log div{display:flex;gap:14px;padding:9px 0;border-bottom:1px solid var(--line);font-size:14px}
.set-log time{color:var(--blue);white-space:nowrap;font-variant-numeric:tabular-nums}
.set-log span{flex:1}.set-log em{color:var(--muted);font-style:normal;white-space:nowrap}
.set-review{max-width:640px;margin:0 auto;display:flex;flex-direction:column;gap:12px}
.set-ch{background:var(--surface);border:1px solid var(--line);border-radius:16px;padding:14px 16px}
.set-ch .set-was{color:var(--muted);text-decoration:line-through}
.set-ch .set-now{color:var(--blue);font-weight:800}
.set-ch small{display:block;color:var(--muted);margin-top:4px;font-size:13px}
@media(max-width:760px){.set-menu{max-width:none;flex-direction:row;overflow-x:auto;flex-basis:100%}
.set-menu a{white-space:nowrap}.set-grid{grid-template-columns:1fr}.set-acc .set-input{width:110px}}
"""

SECTIONS = (
    ("goal", "목표 · 보고"),
    ("glide", "Glide"),
    ("cruise", "Cruise"),
    ("toss", "토스 적립"),
    ("alerts", "알림"),
    ("screen", "화면"),
    ("safety", "안전 · 기록"),
)
_ACC_NOTE = {
    "BTQ": "BTQ Technologies",
    "WQTM": "양자컴퓨팅 ETF",
    "QS": "QuantumScape",
}


def _num(key: str, label: str, value: Any, unit: str, help_: str, errors: Mapping[str, str]) -> str:
    err = errors.get(key)
    shown = f"{value:,.0f}" if isinstance(value, int) else f"{value:g}"
    return (
        '<div class="set-field">'
        f'<label for="{key}">{escape(label)}</label>'
        f'<div class="set-input{" set-err-input" if err else ""}">'
        f'<input id="{key}" name="{key}" value="{escape(shown)}" inputmode="decimal">'
        f"<span>{unit}</span></div>"
        + (f'<p class="set-err">{escape(err)}</p>' if err else "")
        + (f'<p class="set-help">{escape(help_)}</p>' if help_ else "")
        + "</div>"
    )


def _switch(key: str, on: bool, *, danger: bool = False, disabled: bool = False) -> str:
    return (
        f'<input type="checkbox" class="set-switch{" danger" if danger else ""}" name="{key}" '
        f'id="{key}" value="on"{" checked" if on else ""}{" disabled" if disabled else ""} '
        f'role="switch">'
    )


def _toggle(key: str, label: str, sub: str, on: bool, **kw: Any) -> str:
    return (
        f'<div class="set-toggle"><label for="{key}"><b>{escape(label)}</b>'
        f"<small>{escape(sub)}</small></label>{_switch(key, on, **kw)}</div>"
    )


def _soon(label: str, sub: str, on: bool = True) -> str:
    return (
        f'<div class="set-toggle"><div><b>{escape(label)}<span class="set-soon">준비 중</span></b>'
        f"<small>{escape(sub)}</small></div>"
        f'<input type="checkbox" class="set-switch" {"checked " if on else ""}disabled '
        f'aria-label="{escape(label)} (준비 중)"></div>'
    )


def _locked(title: str, rows: Sequence[tuple[str, str]], note: str) -> str:
    body = "".join(f"<div><span>{escape(k)}</span><b>{escape(v)}</b></div>" for k, v in rows)
    return (
        f'<div class="set-lock"><h3>🔒 {escape(title)}</h3>{body}'
        f'<p class="set-help">{escape(note)}</p></div>'
    )


def render_settings(
    v: Mapping[str, Any],
    *,
    token: str,
    errors: Mapping[str, str] | None = None,
    notice: str = "",
    log: Sequence[Mapping[str, str]] = (),
    holdings: Mapping[str, str] | None = None,
) -> str:
    errors = errors or {}
    holdings = holdings or {}
    menu = "".join(f'<a href="#set-{k}">{escape(t)}</a>' for k, t in SECTIONS)
    note = ""
    if notice:
        note = f'<div class="set-note ok" role="status">{escape(notice)}</div>'
    if errors:
        note = (
            '<div class="set-note bad" role="alert">'
            "고칠 칸이 있어요 — 빨간 글씨를 확인해 주세요</div>"
        )
    goal = (
        '<section class="set-card" id="set-goal"><h2>목표 · 보고</h2>'
        "<p class=\"set-sub\">매달 진행 보고와 텔레그램 '현황' 끝 줄이 이 목표로 계산돼요.</p>"
        '<div class="set-grid">'
        + _num("fire.target_krw", "파이어 목표", v["fire.target_krw"], "원", "", errors)
        + '<div class="set-field"><span class="set-label">진행 보고 시각'
        '<span class="set-soon">준비 중</span></span>'
        '<div class="set-input">'
        '<input value="매달 1일 09:15" disabled aria-label="보고 시각"></div>'
        '<p class="set-help">PC가 꺼져 있었으면 켜질 때 보내요</p></div></div>'
        '<div class="set-grid" '
        'style="grid-template-columns:repeat(3,minmax(0,1fr));margin-top:14px">'
        + _num("fire.rate_good", "좋을 때", v["fire.rate_good"], "%", "", errors)
        + _num("fire.rate_mid", "보통", v["fire.rate_mid"], "%", "", errors)
        + _num("fire.rate_bad", "나쁠 때", v["fire.rate_bad"], "%", "", errors)
        + '</div><p class="set-help">도달 예상에 쓰는 연수익 가정이에요 (예측 아님)</p>'
        + _toggle(
            "fire.report_on",
            "매달 파이어 진행 보고",
            "투자 봇 · 지난 보고 대비 · 도달 예상",
            v["fire.report_on"],
        )
        + "</section>"
    )
    glide = (
        '<section class="set-card" id="set-glide"><h2>Glide · 토스</h2>'
        '<p class="set-sub">TQQQ + SCHD, 매달 첫 거래일 밤 장 마감 지정가</p><div class="set-grid">'
        + _num(
            "glide.monthly_krw",
            "기본 월 납입",
            v["glide.monthly_krw"],
            "원",
            "텔레그램 답이 없을 때 쓰는 금액 · 답하면 그 달만 바뀌어요",
            errors,
        )
        + _num(
            "glide.max_krw",
            "한 달 상한",
            v["glide.max_krw"],
            "원",
            "이보다 큰 답은 잘못 친 걸로 보고 다시 물어요",
            errors,
        )
        + "</div>"
        + _toggle(
            "glide.ask_on",
            "매수 전날 금액 묻기",
            "끄면 기본 월 납입으로 바로 사요",
            v["glide.ask_on"],
        )
        + _toggle(
            "glide.harvest_on",
            "12월 양도세 공제 실현",
            "12월 공지 · 버튼으로 진행",
            v["glide.harvest_on"],
        )
        + '<div class="set-toggle"><div><b>매수 · 체결 알림</b><small>실거래라 항상 켜져 있어요'
        '</small></div><input type="checkbox" class="set-switch" checked disabled '
        'aria-label="Glide 알림 (항상 켜짐)"></div></section>'
        + _locked(
            "Glide 규칙 · 잠김",
            (
                ("TQQQ 최대 비중", "60%"),
                ("기준 변동성 (QQQ 20일)", "20%"),
                ("T 브레이크 (TQQQ 고점 대비)", "−15% 절반 · −30% 전부"),
                ("되사기 추세", "20 · 60 · 120일"),
                ("배당", "재투자"),
            ),
            "연구 182 · 183 ~ 189 로 정한 값이에요. 바꾸려면 새 연구로 확인한 뒤에.",
        )
    )
    cruise = (
        '<section class="set-card" id="set-cruise"><h2>Cruise · 업비트</h2>'
        '<p class="set-sub">BTC · ETH · SOL 추세 비중, 매일 지정가</p><div class="set-grid">'
        '<div class="set-field"><span class="set-label">매일 실행 시각'
        '<span class="set-soon">준비 중</span></span>'
        '<div class="set-input"><input value="09:05" disabled aria-label="실행 시각"></div>'
        '<p class="set-help">09:00 일봉 마감 뒤 ~ 12:00 사이</p></div>'
        '<div class="set-field"><span class="set-label">낙폭 정지</span>'
        '<p class="set-help">계좌 단위 가치가 고점보다 30% 내리면 새 매수만 멈춰요. '
        "풀기는 텔레그램 안내의 명령으로 사람이 해요.</p></div></div>"
        + _toggle(
            "alerts.cruise_trade",
            "매매한 날 알림",
            "비중 바뀐 이유 · 코인별 손익",
            v["alerts.cruise_trade"],
        )
        + _toggle(
            "alerts.cruise_weekly",
            "토요일 주간 요약",
            "토 09:30 · 김프 한 줄",
            v["alerts.cruise_weekly"],
        )
        + "</section>"
        + _locked(
            "Cruise 규칙 · 잠김",
            (
                ("코인", "BTC · ETH · SOL"),
                ("변동성 목표 · 코인당 최대", "15% · 50%"),
                ("비중 바꾸는 폭 (밴드)", "5%p"),
                ("낙폭 정지", "30% · 사람이 풂"),
            ),
            "T/v2 규칙(연구 16 · 190) 그대로예요.",
        )
    )
    rows = []
    for s in ACC_SYMBOLS:
        err = errors.get(f"acc.{s}.usd")
        rows.append(
            '<div class="set-acc"><span class="set-badge">미국</span>'
            f"<b>{s}</b><small>{escape(_ACC_NOTE[s])}"
            f"{' · ' + escape(holdings[s]) if s in holdings else ''}</small>"
            f'<div class="set-input{" set-err-input" if err else ""}"><span>하루 $</span>'
            f'<input name="acc.{s}.usd" value="{float(v[f"acc.{s}.usd"]):g}" inputmode="decimal" '
            f'aria-label="{s} 하루 금액"></div>'
            f"{_switch(f'acc.{s}.on', bool(v[f'acc.{s}.on']))}"
            + (f'<p class="set-err" style="flex-basis:100%">{escape(err)}</p>' if err else "")
            + "</div>"
        )
    rows.append(
        '<div class="set-acc"><span class="set-badge">미국</span><b>QNDX</b>'
        "<small>나스닥 100 · 적립 중단(보유 유지) · QS 전략과 얽혀 다시 켜기는 대화로</small>"
        '<input type="checkbox" class="set-switch" disabled aria-label="QNDX (대화로만)"></div>'
    )
    toss = (
        '<section class="set-card" id="set-toss"><h2>토스 매일 적립</h2>'
        '<p class="set-sub">쌓인 적립금이 1주 값이 되면 장 마감 지정가로 1주 사요. '
        "바꾼 금액 · 켜기는 <b>내일 적립분부터</b>예요 (지난 날 다시 계산 안 함).</p>"
        + "".join(rows)
        + '<div class="set-acc"><span class="set-badge">미국</span>'
        '<span class="set-badge kr">국내</span><b>종목 추가</b>'
        "<small>새 미국 종목 · 국내 주식 추가는 토스 종목 확인 · 국내 주문 시험 뒤 열어요</small>"
        '<span class="set-soon">준비 중</span></div>'
        + _toggle(
            "alerts.fx", "환전 알림", "평일 09 · 12 · 15시, 달러가 모자랄 때만", v["alerts.fx"]
        )
        + "</section>"
    )
    alerts = (
        '<section class="set-card" id="set-alerts"><h2>알림</h2>'
        '<p class="set-sub">투자 봇 = 실거래 · 정보 봇 = 테마 · 브리핑 · 뉴스</p>'
        + _toggle(
            "alerts.bot_down",
            "투자 봇 · 봇 멈춤",
            "현황 · 정보 봇이 15분 넘게 조용할 때",
            v["alerts.bot_down"],
        )
        + _toggle(
            "alerts.theme", "정보 봇 · 주간 테마", "토 09:00 · 한국 · 미국", v["alerts.theme"]
        )
        + _toggle("alerts.brief", "정보 봇 · 시장 브리핑", "장 마감 뒤", v["alerts.brief"])
        + _toggle(
            "alerts.news",
            "정보 봇 · 종목 뉴스 답",
            "종목 이름을 보내면 최근 7일 요약",
            v["alerts.news"],
        )
        + _soon("조용한 시간 23:00 ~ 07:00", "위험 알림은 이때도 와요", on=False)
        + '<p class="set-help">Glide · Cruise 매매, 환전, 파이어 보고 알림은 각 메뉴에서 바꿔요. '
        "정지 · 실패 같은 위험 알림은 끌 수 없어요.</p></section>"
    )
    theme_opts = "".join(
        f'<option value="{k}"{" selected" if v["screen.theme"] == k else ""}>{t}</option>'
        for k, t in (("system", "시스템"), ("light", "라이트"), ("dark", "다크"))
    )
    screen = (
        '<section class="set-card" id="set-screen"><h2>화면</h2><div class="set-grid">'
        '<div class="set-field"><label for="screen.theme">테마</label>'
        f'<div class="set-input"><select id="screen.theme" name="screen.theme">{theme_opts}'
        '</select></div><p class="set-help">'
        "위 해 · 달 버튼으로 바꾼 값이 있으면 그게 먼저예요</p></div>"
        '<div class="set-field"><span class="set-label">처음 여는 화면'
        '<span class="set-soon">준비 중</span></span>'
        '<div class="set-input">'
        '<input value="실거래 홈" disabled aria-label="처음 여는 화면"></div>'
        "</div></div>"
        + _toggle(
            "screen.hide_amounts",
            "금액 가리고 시작",
            "열 때마다 금액을 ●●● 로",
            v["screen.hide_amounts"],
        )
        + '<p class="set-help"><a href="/live?onboard=again">처음 안내 다시 보기</a></p></section>'
    )
    log_rows = (
        "".join(
            f"<div><time>{escape(r.get('at', ''))}</time><span>{escape(r.get('what', ''))}</span>"
            f"<em>{escape(r.get('by', ''))}</em></div>"
            for r in log
        )
        or '<p class="set-help">아직 바꾼 기록이 없어요</p>'
    )
    safety = (
        '<section class="set-card set-danger" id="set-safety"><h2>비상 정지</h2>'
        + _toggle(
            "safety.halt",
            "새 주문 모두 멈추기",
            "Glide · Cruise · 토스 적립의 새 주문을 멈춰요. 이미 낸 주문과 보유는 그대로예요.",
            v["safety.halt"],
            danger=True,
        )
        + "</section>"
        '<section class="set-card"><h2>변경 기록</h2>'
        f'<div class="set-log">{log_rows}</div></section>'
    )
    return (
        f"<style>{_CSS}</style>"
        '<div class="set-wrap"><div class="set-head"><h1>설정</h1>'
        "<p>바꾼 값은 확인 창을 거쳐 저장되고, 저장한 뒤 <b>다음 실행부터</b> 적용돼요. "
        "연구로 정한 매매 규칙은 잠겨 있어요.</p></div>"
        f"{note}"
        '<form method="post" action="/settings/review">'
        f'<input type="hidden" name="token" value="{escape(token)}">'
        f'<div class="set-row"><nav class="set-menu" aria-label="설정 메뉴">{menu}</nav>'
        f'<div class="set-body">{goal}{glide}{cruise}{toss}{alerts}{screen}{safety}'
        '<div class="set-bar"><div><b>저장 전에 바뀌는 값을 한 번 더 보여 드려요</b>'
        "<small>매매 규칙 · 보유는 바뀌지 않아요</small></div>"
        '<div style="display:flex;gap:8px"><a href="/settings">되돌리기</a>'
        '<button type="submit">저장…</button></div></div>'
        "</div></div></form></div>"
    )


def render_review(changes: Sequence[Change], form: Mapping[str, str], *, token: str) -> str:
    if not changes:
        return (
            f"<style>{_CSS}</style>"
            '<div class="set-wrap"><div class="set-review"><h1>바뀐 값이 없어요</h1>'
            '<p><a href="/settings">설정으로 돌아가기</a></p></div></div>'
        )
    hidden = "".join(
        f'<input type="hidden" name="{escape(k)}" value="{escape(val)}">'
        for k, val in form.items()
        if k != "token"
    )
    items = "".join(
        f'<div class="set-ch"><b>{escape(c.label)}</b><div>'
        f'<span class="set-was">{escape(show(c.key, c.before))}</span> → '
        f'<span class="set-now">{escape(show(c.key, c.after))}</span></div>'
        f"<small>적용: {escape(c.applies)}</small></div>"
        for c in changes
    )
    halt = any(c.key == "safety.halt" and c.after for c in changes)
    warn = (
        '<div class="set-note bad">비상 정지를 켜면 data/HALT 를 만들어 모든 새 주문을 멈춰요</div>'
        if halt
        else ""
    )
    return (
        f"<style>{_CSS}</style>"
        '<div class="set-wrap"><form class="set-review" method="post" action="/settings/save">'
        f"<h1>바뀐 값 {len(changes)}개를 저장할까요?</h1>"
        '<p class="set-help">저장하면 적용 시점부터 쓰이고, 변경 기록에 남아요.</p>'
        f"{warn}{items}"
        '<div class="set-lock"><div><span>🔒 매매 규칙(TQQQ 비중 · 브레이크 · 코인 · 낙폭 정지)은 '
        "바뀌지 않아요. 지금 들고 있는 것을 사고팔지도 않아요.</span></div></div>"
        f'<input type="hidden" name="token" value="{escape(token)}">{hidden}'
        '<div class="set-bar"><b>확인</b><div style="display:flex;gap:8px">'
        '<a href="/settings">돌아가서 고치기</a><button type="submit">저장</button></div></div>'
        "</form></div>"
    )
