"""M3 지키기 — 매도 브레이크 · 배당 단계 · 텔레그램 제안과 답 (순수 계산, 아직 실거래에 안 붙임).

* **브레이크** (문서 183): 매일 전날 종가로 낙폭을 보고 −15% 면 TQQQ 절반, −30% 면 남은 전부를 판다.
  - 기준 H = max(평균 단위 원가, 최근 252 거래일 NAV 최고값), T = TQQQ 자체 고점.
  - 판 돈은 보관금으로 따로 두고 매달 비율 계산에서 뺀다.
  - QQQ 추세가 꺼졌다가 다시 켜진 날 보관금으로 TQQQ 를 되산다.
  - 팔 때 알리지 않고, 매달 M3 알림 맨 아래에 내역을 붙인다.
* **배당 단계**: SCHD 세후 배당이 월 10 · 30 · 50 · 100 만 원을 넘을 때마다
  TQQQ 목표를 10%p 씩 낮추자고 **묻는다**.
* **제안 · 답**: 봇이 먼저 낸 제안에만 답을 받는다.
  - 답이 곧 확인이고, 그날 주문 전(20:50)까지 '취소' 로 되돌린다.
  - 제안은 마감일까지 열려 있다. 답이 없으면 아무것도 하지 않는다.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Any

from auto_trading.notify.style import RULE, sub
from auto_trading.ops.mf_paper import LEV_MAX
from auto_trading.ops.us_capital_gains import SETTLE_HOLIDAYS, tax_settle

STAGE1, STAGE2 = 0.15, 0.30
STAGE_SHARE = {0: 1.0, 1: 0.5, 2: 0.0}
"""브레이크 단계별 매달 TQQQ 목표 배수 (183 · 184 와 같음)."""
HIGH_DAYS = 252
DIV_STEPS_KRW = (100_000.0, 300_000.0, 500_000.0, 1_000_000.0)
STEP_CUT = 0.10
CANCEL_UNTIL = "20:50"
"""답한 날 이 시각(KST)까지 '취소' 를 받는다. 21:00 실행이 주문을 낸다."""
REMIND_DAYS = 7
HARVEST_REMIND_BEFORE = 2
"""공제 실현은 답이 없을 때 마감 이 날수 전에 한 번만 다시 알린다."""


# ------------------------------------------------------------------ 브레이크


@dataclass(slots=True)
class Brake:
    ref: str = "H"
    """H(평단 · 12 개월 고점) 또는 T(TQQQ 고점)."""
    stage: int = 0
    units: float = 0.0
    paid_usd: float = 0.0
    """단위로 바꾼 누적 납입(달러)."""
    navs: list[list[Any]] = field(default_factory=list)
    """[날짜, NAV] — 마지막 재매수 뒤, 최근 252 개."""
    lev_peak: float = 0.0
    reserve_usd: float = 0.0
    off_seen: bool = False
    sales: list[dict[str, Any]] = field(default_factory=list)
    """이번 보관 동안의 매도 {date, dd, qty, usd}."""
    sold_total_usd: float = 0.0
    """브레이크로 판 누적(처음부터)."""
    last_day: str = ""
    """고점에 반영한 마지막 종가 날짜."""
    pending: dict[str, dict[str, Any]] = field(default_factory=dict)
    """접수한 브레이크 · 재매수 주문 {client_id: {kind, dd, prev_stage}} — 체결 확인 뒤 지운다."""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> Brake:
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


def nav(value_usd: float, b: Brake) -> float:
    return value_usd / b.units if b.units > 0 else 1.0


def add_deposit(b: Brake, usd: float, value_before_usd: float) -> None:
    """납입을 단위로 바꾼다 (납입 직전 계좌 가치 기준 NAV)."""
    b.units += usd / nav(value_before_usd, b)
    b.paid_usd += usd


def record_close(b: Brake, day: str, value_usd: float, lev_close: float) -> None:
    """그날 종가 기록 (NAV · TQQQ 최고가)."""
    if b.units <= 0:
        return
    if not b.navs or b.navs[-1][0] != day:
        b.navs.append([day, nav(value_usd, b)])
    b.navs = b.navs[-HIGH_DAYS:]
    b.lev_peak = max(b.lev_peak, lev_close)


def drawdown(b: Brake, lev_last: float) -> float | None:
    """마지막 기록 기준 낙폭. 기록이 없으면 None."""
    if b.ref == "T":
        return 1 - lev_last / b.lev_peak if b.lev_peak > 0 else None
    if not b.navs or b.units <= 0:
        return None
    base = max(b.paid_usd / b.units, max(float(v) for _, v in b.navs))
    return 1 - float(b.navs[-1][1]) / base


def brake_sell(b: Brake, dd: float | None, lev_qty: float) -> int:
    """판단 → 팔 TQQQ 주수 (단계를 올린다). 판 뒤 ``sold`` 로 장부에 넣는다."""
    if dd is None or lev_qty < 1 or b.stage >= 2:
        return 0
    if dd >= STAGE2:
        b.stage = 2
        return math.floor(lev_qty)
    if dd >= STAGE1 and b.stage == 0:
        b.stage = 1
        return max(1, math.floor(lev_qty / 2))
    return 0


def sold(b: Brake, day: str, dd: float, qty: float, usd_net: float) -> None:
    if not b.sales:
        b.off_seen = False
    b.sales.append({"date": day, "dd": round(dd, 4), "qty": qty, "usd": round(usd_net, 2)})
    b.reserve_usd += usd_net
    b.sold_total_usd += usd_net


def rebuy_due(b: Brake, trend_on: bool | None) -> bool:
    """보관 중 추세가 꺼진 적이 있고 지금 켜졌으면 True (꺼짐을 기억한다)."""
    if b.stage == 0 or not b.sales:
        return False
    if trend_on is False:
        b.off_seen = True
        return False
    return bool(trend_on and b.off_seen)


def rebought(b: Brake, day: str, value_usd: float, lev_close: float) -> None:
    """되산 뒤: 단계 0, 보관금 0, 고점 기록은 오늘부터 새로."""
    b.stage, b.reserve_usd, b.sales, b.off_seen = 0, 0.0, [], False
    b.navs = [[day, nav(value_usd, b)]]
    b.lev_peak = lev_close


def brake_lines(b: Brake) -> list[str]:
    """매달 M3 알림 맨 아래 — 보관 중일 때만."""
    if not b.sales:
        return []
    lines = ["", RULE, "🛑 브레이크 매도 내역"]
    for s in b.sales:
        lines.append(
            f"· {s['date']} 고점 대비 −{s['dd'] * 100:.1f}% → "
            f"TQQQ {s['qty']:g}주 매도 ${s['usd']:,.2f}"
        )
    lines.append(f"· 매도 총액 ${sum(s['usd'] for s in b.sales):,.2f}")
    lines.append(sub("매도한 금액은 그대로 보유하고 계세요."))
    lines.append("     다음 매수 신호(QQQ 추세 회복)가 오면 그 돈으로 다시 매수하겠습니다.")
    return lines


# ------------------------------------------------------------------ 배당 단계


def monthly_dividend_krw(dividends: Sequence[Sequence[Any]], today: str, fx: float) -> float:
    """최근 365 일 SCHD 세후 배당(달러) × 환율 ÷ 12. ``dividends`` 는 M3State.dividends."""
    since = (date.fromisoformat(today) - timedelta(days=365)).isoformat()
    usd = sum(float(r[4]) for r in dividends if str(r[0]) >= since and r[1] == "SCHD")
    return usd * fx / 12


def step_due(steps_done: int, monthly_krw: float) -> int | None:
    """다음 단계에 닿았으면 그 단계 번호(0 부터)."""
    if steps_done < len(DIV_STEPS_KRW) and monthly_krw >= DIV_STEPS_KRW[steps_done]:
        return steps_done
    return None


def lev_cap(steps_done: int) -> float:
    return max(LEV_MAX - STEP_CUT * steps_done, 0.0)


def shift_plan(
    lev_qty: float, lev_px: float, div_px: float, total_usd: float, new_cap: float
) -> tuple[int, int]:
    """TQQQ 를 새 목표(전체 × new_cap)까지 팔고 그 돈으로 살 SCHD 주수 (정수)."""
    over = lev_qty * lev_px - new_cap * total_usd
    sell = min(math.floor(lev_qty), max(math.ceil(over / lev_px), 0)) if over > 0 else 0
    return sell, math.floor(sell * lev_px / div_px) if div_px > 0 else 0


def added_monthly_krw(div_shares: int, dps_ttm: float, fx: float) -> float:
    """SCHD ``div_shares`` 주를 더 사면 늘어날 월 배당(세후, 원)."""
    return div_shares * dps_ttm * 0.85 * fx / 12


# ------------------------------------------------------------------ 제안 · 답


@dataclass(slots=True)
class Proposal:
    pid: str
    kind: str
    """div_step · harvest."""
    created: str
    deadline: str
    """이 날 밤 실행까지 답을 받는다 (KST 날짜)."""
    symbol: str
    max_qty: int
    plan: dict[str, Any] = field(default_factory=dict)
    status: str = "open"
    """open · accepted · declined · canceled · expired · done."""
    qty: int = 0
    answered: str = ""
    """답한 날 (KST)."""
    reminded: str = ""
    awaiting_qty: bool = False
    """[직접입력] 을 누른 뒤 주수를 기다리는 중."""
    message_id: int = 0
    """고정한 공지 메시지 번호 (0 = 보내지 못함)."""
    phase: str = ""
    """실행 단계: '' (대기) · selling (판 주문 접수) · buying (되사기 접수) · done."""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> Proposal:
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


_YES = ("네", "예", "응", "좋아", "진행", "ㅇㅇ", "yes", "ok", "오케이", "그래")
_NO = ("아니", "아뇨", "노", "안해", "안 해", "하지마", "no", "그대로", "패스")
_ALL = ("다팔", "전부", "전량", "모두", "다 팔")


def parse_reply(text: str, *, awaiting_qty: bool = False) -> tuple[str, int | None] | None:
    """답 → (yes · no · cancel · all · qty, 주수). 애매하면 None (실행하지 않고 되묻는다).

    ``awaiting_qty`` (직접입력 뒤)면 숫자만 보내도('2') 주수로 읽는다.
    """
    t = text.strip().lower()
    compact = t.replace(" ", "")
    if "취소" in compact:
        return ("cancel", None)
    m = re.search(r"(\d+)\s*주", t) or (re.fullmatch(r"(\d+)", compact) if awaiting_qty else None)
    if m:
        n = int(m.group(1))
        return ("qty", n) if n > 0 else ("no", None)
    if any(w.replace(" ", "") in compact for w in _ALL):
        return ("all", None)
    if any(w.replace(" ", "") in compact for w in _NO):
        return ("no", None)
    if any(compact.startswith(w) for w in _YES):
        return ("yes", None)
    return None


def answer(p: Proposal, reply: tuple[str, int | None] | None, today: str, hhmm: str) -> str:
    """답을 제안에 반영하고 봇이 보낼 답장을 돌려준다 (순수)."""
    if reply is None:
        return (
            f"죄송해요, 정확히 이해하지 못했어요. {p.symbol} {p.max_qty}주를 진행할까요?\n"
            + sub('"네" · "아니요" · "2주만" 처럼 답해 주세요')
        )
    kind, n = reply
    if kind == "cancel":
        if p.status == "accepted" and p.answered == today and hhmm < CANCEL_UNTIL:
            p.status, p.qty = "open", 0
            return (
                f"알겠습니다. 오늘 밤 {p.symbol} 주문을 취소했어요. "
                f"제안은 {p.deadline}까지 그대로 열려 있어요."
            )
        return (
            "취소할 주문이 없거나 이미 주문을 낸 뒤예요(21:00). "
            "다음 Glide 알림에서 결과를 알려 드릴게요."
        )
    if p.status not in ("open", "accepted"):
        return "이 제안은 이미 끝났어요. 새 제안이 생기면 다시 여쭤볼게요."
    if kind == "no":
        p.status, p.answered = "declined", today
        return _ack_no(p)
    qty = p.max_qty if kind in ("yes", "all") else min(int(n or 0), p.max_qty)
    if qty <= 0:
        return f"보유 범위 안에서 정해 주세요 (최대 {p.max_qty}주)."
    p.status, p.qty, p.answered = "accepted", qty, today
    night = "오늘 밤" if hhmm < "21:00" else "다음 거래일 밤"
    cancel = (
        f'바꾸시려면 {CANCEL_UNTIL} 전에 "취소"라고 보내 주세요.' if hhmm < CANCEL_UNTIL else ""
    )
    return _ack_yes(p, night, cancel)


def _ack_yes(p: Proposal, night: str, cancel: str) -> str:
    over = f" ({p.max_qty}주 중 {p.qty}주)" if p.qty < p.max_qty else ""
    if p.kind == "div_step":
        buy = math.floor(p.qty * float(p.plan.get("lev_px", 0)) / float(p.plan.get("div_px", 1)))
        body = (
            f"알겠습니다. {night} TQQQ {p.qty}주{over}를 팔고 SCHD {buy}주를 사서 "
            f"TQQQ 목표를 {p.plan.get('new_cap', 0) * 100:.0f}%로 낮추겠습니다."
        )
    else:
        body = (
            f"알겠습니다. {night} {p.symbol} {p.qty}주{over}를 팔고, "
            "체결을 확인한 다음 날 밤 같은 수량을 다시 사서 "
            "올해 공제를 쓰겠습니다."
        )
    tail = "체결되면 다음 Glide 알림에 결과를 적어 둘게요."
    return "\n".join(x for x in (body, tail, sub(cancel) if cancel else "") if x)


def _ack_no(p: Proposal) -> str:
    if p.kind == "div_step":
        nxt = p.plan.get("next_step_krw")
        later = (
            f"월 배당이 {nxt / 10_000:,.0f}만 원(다음 단계)에 닿으면 다시 여쭤볼게요."
            if nxt
            else ""
        )
        cap = float(p.plan.get("cap", 0))
        return f"알겠습니다. 이번에는 TQQQ {cap * 100:.0f}%를 그대로 두겠습니다. {later}".strip()
    return "알겠습니다. 올해는 공제 실현을 하지 않겠습니다. 내년 12월에 다시 여쭤볼게요."


def remind_due(p: Proposal, today: str) -> bool:
    """열린 제안의 다시 묻기: 배당 단계는 7 일마다, 공제 실현은 마감 2 일 전 한 번만."""
    if p.status != "open" or today > p.deadline:
        return False
    if p.kind == "harvest":
        last_call = date.fromisoformat(p.deadline) - timedelta(days=HARVEST_REMIND_BEFORE)
        return not p.reminded and today >= last_call.isoformat()
    last = p.reminded or p.created
    return (date.fromisoformat(today) - date.fromisoformat(last)).days >= REMIND_DAYS


def expire(p: Proposal, today: str) -> bool:
    if p.status == "open" and today > p.deadline:
        p.status = "expired"
        return True
    return False


def harvest_deadline(year: int) -> str:
    """공제 실현 마감 = 올해 결제로 잡히는 마지막 미국 거래일의 하루 앞 거래일 (여유 1 일).

    미국 거래일 D 의 주문은 한국 D 일 21:00 실행이 내므로 같은 날짜를 돌려준다.
    """
    days: list[str] = []
    d = date(year, 12, 31)
    while d.month == 12 and len(days) < 2:
        iso = d.isoformat()
        trading = d.weekday() < 5 and iso not in SETTLE_HOLIDAYS
        if trading and tax_settle(f"{iso}T16:00:00-05:00")[:4] == str(year):
            days.append(iso)
        d -= timedelta(days=1)
    return days[-1]


def div_step_message(
    *,
    monthly_krw: float,
    step_krw: float,
    cap: float,
    new_cap: float,
    sell: int,
    lev_px: float,
    buy: int,
    fx: float,
    added_krw: float,
    gain_krw: float | None,
    room_krw: float | None,
) -> str:
    usd = sell * lev_px
    lines = [
        f"💰 Glide · 배당 단계 도달 (월 {step_krw / 10_000:,.0f}만 원)",
        RULE,
        f"최근 12개월 SCHD 배당(세후)이 월 평균 {monthly_krw:,.0f}원이 됐어요.",
        "이제 공격 비중을 조금 줄여 배당을 키울 때예요.",
        "",
        f"제안: TQQQ 목표 {cap * 100:.0f}% → {new_cap * 100:.0f}%",
        f"· TQQQ {sell}주 매도 (약 ${usd:,.0f} · 약 {usd * fx:,.0f}원)",
        f"· SCHD {buy}주 매수",
        f"→ 월 배당 약 {monthly_krw:,.0f}원 → 약 {monthly_krw + added_krw:,.0f}원"
        f" (+{added_krw:,.0f}원)",
    ]
    if gain_krw is not None and room_krw is not None:
        tax = max(gain_krw - room_krw, 0) * 0.22
        note = "올해 공제 안이라 세금 0원" if tax <= 0 else f"예상 세금 약 {tax:,.0f}원"
        lines.append(sub(f"이번 매도 실현 손익 {gain_krw:+,.0f}원 · {note}"))
    lines += [
        "",
        "TQQQ를 매도하고 SCHD로 옮길까요?",
        sub('"네" — 오늘 밤 장 마감 지정가로 진행'),
        sub('"아니요" — 이번엔 그대로 (다음 단계에서 다시 여쭤요)'),
        sub(f'"{max(sell // 2, 1)}주만" — 일부만 진행'),
        sub(f"답이 없으면 진행하지 않고, {REMIND_DAYS}일 뒤 한 번 더 여쭤요"),
    ]
    return "\n".join(lines)


def harvest_message(
    *, year: int, room_krw: float, picks: Sequence[tuple[str, int, float]], deadline: str
) -> str:
    """12 월 공제 실현 제안. ``picks`` = (종목, 주수, 실현 이익 원)."""
    lines = [f"🧾 {year}년 해외주식 공제 남은 {room_krw:,.0f}원", RULE]
    lines.append("이익 난 종목을 팔고 다음 날 다시 사면, 세금 없이 단가를 올릴 수 있어요.")
    for sym, n, g in picks:
        lines.append(f"· 예: {sym} {n}주 (실현 약 {g:+,.0f}원)")
    lines += [
        "",
        f"진행할까요? {deadline} 밤 주문까지 답을 받아요.",
        sub('아래 버튼을 눌러 주세요 (글로 "2주만" 이라고 보내셔도 돼요)'),
        sub(f"답하신 날 {CANCEL_UNTIL} 전까지는 [취소] 로 되돌릴 수 있어요"),
        sub(f"답이 없으면 마감 {HARVEST_REMIND_BEFORE}일 전에 한 번만 더 알려 드려요"),
    ]
    return "\n".join(lines)


# ------------------------------------------------------------------ 버튼

ACTIONS = ("all", "input", "no", "cancel")
Buttons = list[list[dict[str, str]]]


def proposal_buttons(p: Proposal) -> Buttons:
    """텔레그램 인라인 버튼: [전부 진행] [직접입력] [이번엔 안 함]."""
    return [
        [
            {"text": "전부 진행", "callback_data": f"{p.pid}:all"},
            {"text": "직접입력", "callback_data": f"{p.pid}:input"},
            {"text": "이번엔 안 함", "callback_data": f"{p.pid}:no"},
        ]
    ]


def cancel_buttons(p: Proposal) -> Buttons:
    return [[{"text": "취소", "callback_data": f"{p.pid}:cancel"}]]


def parse_callback(data: str) -> tuple[str, str] | None:
    """'pid:action' → (pid, action). 모르는 값이면 None."""
    pid, _, action = data.rpartition(":")
    return (pid, action) if pid and action in ACTIONS else None


def ask_qty_message(p: Proposal) -> str:
    """[직접입력] 을 누르면 다시 보내는 알림 — 입력칸이 바로 열리게(ForceReply) 보낸다."""
    title = "공제 실현" if p.kind == "harvest" else "배당 단계"
    return "\n".join(
        [
            f"✏️ {title} · 직접 입력",
            RULE,
            f"{p.symbol}를 몇 주 팔까요? 숫자만 입력해 주세요 (최대 {p.max_qty}주).",
            sub("예: 2"),
            sub('그만두려면 "취소"'),
        ]
    )


def _with_cancel(p: Proposal, text: str, hhmm: str) -> tuple[str, Buttons | None]:
    show = p.status == "accepted" and hhmm < CANCEL_UNTIL
    return text, cancel_buttons(p) if show else None


def press(p: Proposal, action: str, today: str, hhmm: str) -> tuple[str, Buttons | None]:
    """버튼 → (봇 답장, 답장에 붙일 버튼). [직접입력] 은 주수를 묻고 기다린다 (순수)."""
    if action == "input" and p.status in ("open", "accepted"):
        p.awaiting_qty = True
        return ask_qty_message(p), None
    p.awaiting_qty = False
    kind = "all" if action == "input" else action
    return _with_cancel(p, answer(p, (kind, None), today, hhmm), hhmm)


def typed(p: Proposal, text: str, today: str, hhmm: str) -> tuple[str, Buttons | None]:
    """글 답 → (봇 답장, 버튼). 직접입력 대기 중이면 숫자만 보내도 된다 (순수)."""
    reply = parse_reply(text, awaiting_qty=p.awaiting_qty)
    if reply is not None:
        p.awaiting_qty = False
    return _with_cancel(p, answer(p, reply, today, hhmm), hhmm)


# ------------------------------------------------------------------ 공제 실현 고르기 · 상태 줄


def harvest_pick(
    qty: Mapping[str, float],
    cost_usd: Mapping[str, float],
    prices: Mapping[str, float],
    fx: float,
    room_krw: float,
) -> tuple[str, int, float] | None:
    """세금 없이 실현할 이익(``room_krw``) 안에서 팔 (종목, 주수, 실현 이익 원) (순수).

    실현 이익이 가장 큰 종목 하나를 고른다.

    주당 이익 = (지금 값 − 평균 원가) × 환율. 원가는 Glide 장부 체결 금액(수수료 포함) 기준이다.
    """
    best: tuple[str, int, float] | None = None
    for sym, q in qty.items():
        whole = math.floor(q)
        if whole < 1 or sym not in prices or cost_usd.get(sym, 0) <= 0:
            continue
        per = (prices[sym] - cost_usd[sym] / q) * fx
        if per <= 0:
            continue
        n = min(whole, math.floor(room_krw / per))
        if n >= 1 and (best is None or n * per > best[2]):
            best = (sym, n, n * per)
    return best


def status_line(p: Proposal) -> str:
    """고정 공지 맨 아래에 붙이는 지금 상태 한 줄."""
    if p.status == "accepted":
        word = {"selling": "매도 접수", "buying": "되사기 접수"}.get(p.phase, "예약")
        return f"✅ {p.symbol} {p.qty}주 {word} ({p.answered})"
    return {
        "open": "⏳ 답을 기다리는 중",
        "declined": "⏸ 이번엔 안 함",
        "expired": "⌛ 마감 — 진행하지 않음",
        "done": f"✅ 완료 — {p.symbol} {p.qty}주",
        "canceled": "↩️ 취소함",
    }.get(p.status, p.status)
