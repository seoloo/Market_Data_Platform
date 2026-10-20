"""🌟 Deneb 지키기 — 하락 방어(브레이크) · 배당 단계 · 텔레그램 제안과 답 (계산만).

언제 쓰나: 평일 21:00 ``scripts/toss_accumulate.py`` 가 브레이크를 보고 (``DENEB_BRAKE = True``,
           2026-10-09 사용자 '처음부터', 커밋 5b2af6f),
           09:00 아침 확인 · 텔레그램 대화가 제안을 다룬다.
읽고 쓰는 것: 없음 — 장부 ``data/toss/DENEB.json`` 의 ``brake`` · ``proposals`` 칸 모양과 계산만.

* **브레이크** (문서 183): 매일 전날 종가로 낙폭을 보고 −15% 면 TQQQ 절반, −30% 면 남은 전부를 판다.
  - 기준 H = max(평균 단위 원가, 최근 252 거래일 NAV 최고값), T = TQQQ 자체 고점 (실거래는 T).
  - 판 돈은 보관금으로 따로 두고 매달 비율 계산에서 뺀다.
  - QQQ 추세가 꺼졌다가 다시 켜진 날 보관금으로 TQQQ 를 되산다.
  - 팔 때 알리지 않고, 매달 Deneb 알림 맨 아래에 내역을 붙인다.
* **배당 단계**: SCHD 세후 배당이 월 10 · 30 · 50 · 100 만 원을 넘을 때마다
  TQQQ 목표를 10%p 씩 낮추자고 **묻는다** (금 섞기 켜짐이면 계좌 크기 기준).
* **12 월 공제 실현**: 해외주식 양도 공제(연 250 만 원) 안에서 이익 난 종목을 팔고
  다음 날 되산다 — 묻는다.
* **제안 · 답**: 봇이 먼저 낸 제안에만 답을 받는다.
  - 답이 곧 확인이고, 그날 주문 전(20:50)까지 '취소' 로 되돌린다.
  - 제안은 마감일까지 열려 있다. 답이 없으면 아무것도 하지 않는다.

용어: NAV = 1 단위 값(납입을 '단위' 로 바꿔 수익률만 보는 값), 낙폭 = 고점보다 얼마나 내려왔나.
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
"""브레이크 1 단계(−15%: TQQQ 절반 팔기) · 2 단계(−30%: 남은 전부 팔기) 낙폭."""
STAGE_SHARE = {0: 1.0, 1: 0.5, 2: 0.0}
"""브레이크 단계별 매달 TQQQ 목표 배수 (183 · 184 와 같음)."""
HIGH_DAYS = 252
"""H 기준 고점을 볼 기간 (거래일 ≈ 1 년)."""
DIV_STEPS_KRW = (100_000.0, 300_000.0, 500_000.0, 1_000_000.0)
"""배당 단계 — 최근 12 개월 SCHD 세후 배당의 월 평균(원)."""
ACCOUNT_STEPS_KRW = (120_000_000.0, 350_000_000.0, 600_000_000.0, 1_200_000_000.0)
"""금 섞기 켜짐일 때 단계 기준 — Deneb 계좌(원). SCHD 40% · 배당률 3% · 세후 85% 에서 위 배당 단계
(월 10 · 30 · 50 · 100 만 원)가 오는 계좌 크기와 비슷하게 (사용자 2026-10-19 '1번 진행').
금을 섞으면 SCHD 배당이 절반이라 배당 기준으로는 질문이 계좌가 두 배일 때 온다."""
STEP_CUT = 0.10
"""단계마다 TQQQ 목표를 낮추는 폭 (10%p)."""
CANCEL_UNTIL = "20:50"
"""답한 날 이 시각(KST)까지 '취소' 를 받는다. 21:00 실행이 주문을 낸다."""
REMIND_DAYS = 7
"""배당 단계 제안에 답이 없으면 이 날수마다 다시 묻는다."""
HARVEST_REMIND_BEFORE = 2
"""공제 실현은 답이 없을 때 마감 이 날수 전에 한 번만 다시 알린다."""


# ------------------------------------------------------------------ 브레이크


@dataclass(slots=True)
class Brake:
    """브레이크 상태 (장부 ``brake`` 칸 — 키 이름은 파일에 저장돼 바꾸지 않는다)."""

    ref: str = "H"
    """H(평단 · 12 개월 고점) 또는 T(TQQQ 고점)."""
    stage: int = 0
    """0 (평소) · 1 (절반 판 뒤) · 2 (전부 판 뒤)."""
    units: float = 0.0
    paid_usd: float = 0.0
    """단위로 바꾼 누적 납입(달러)."""
    navs: list[list[Any]] = field(default_factory=list)
    """[날짜, NAV] — 마지막 재매수 뒤, 최근 252 개."""
    lev_peak: float = 0.0
    """TQQQ 종가 최고값 (T 기준 낙폭의 고점)."""
    reserve_usd: float = 0.0
    """보관금 — 브레이크로 판 돈 (매달 비율 계산에서 빠짐, 되살 때 씀)."""
    off_seen: bool = False
    """보관 중 QQQ 추세가 꺼진 것을 봤나 (꺼졌다 다시 켜져야 되산다)."""
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
    def from_dict(cls, data: Mapping[str, Any]) -> Brake:
        """[쓰임] 장부 칸 → :class:`Brake`. 모르는 키는 버리고 없는 키는 기본값."""
        known = {name: data[name] for name in cls.__dataclass_fields__ if name in data}
        return cls(**known)


def nav(value_usd: float, brake: Brake) -> float:
    """[쓰임] 지금 1 단위 값 (단위가 없으면 1)."""
    return value_usd / brake.units if brake.units > 0 else 1.0


def add_deposit(brake: Brake, usd: float, value_before_usd: float) -> None:
    """[쓰임] 납입을 단위로 바꾼다 (납입 직전 계좌 가치 기준 NAV).
    [이유] 돈을 넣어 평가액이 커진 것을 '오른 것' 으로 착각하지 않게 단위로 센다."""
    brake.units += usd / nav(value_before_usd, brake)
    brake.paid_usd += usd


def record_close(brake: Brake, day: str, value_usd: float, lev_close: float) -> None:
    """[쓰임] 그날 종가 기록 (NAV · TQQQ 최고가). 같은 날은 한 번만, 최근 252 개만 둔다."""
    if brake.units <= 0:
        return
    if not brake.navs or brake.navs[-1][0] != day:
        brake.navs.append([day, nav(value_usd, brake)])
    brake.navs = brake.navs[-HIGH_DAYS:]
    brake.lev_peak = max(brake.lev_peak, lev_close)


def drawdown(brake: Brake, lev_last: float) -> float | None:
    """[쓰임] 마지막 기록 기준 낙폭 (0.2 = 고점보다 20% 아래). 기록이 없으면 None.

    T 기준: TQQQ 고점 대비. H 기준: max(평균 단위 원가, 최근 NAV 최고값) 대비."""
    if brake.ref == "T":
        return 1 - lev_last / brake.lev_peak if brake.lev_peak > 0 else None
    if not brake.navs or brake.units <= 0:
        return None
    best_nav = max(float(value) for _day, value in brake.navs)
    base = max(brake.paid_usd / brake.units, best_nav)
    return 1 - float(brake.navs[-1][1]) / base


def brake_sell(brake: Brake, dd: float | None, lev_qty: float) -> int:
    """[쓰임] 낙폭 ``dd`` 판단 → 팔 TQQQ 주수 (단계를 올린다). 판 뒤 :func:`sold` 로 장부에 넣는다.
    [이유] 1 단계는 한 번만(절반), 2 단계면 남은 전부 — 이미 2 단계면 더 팔 것이 없다."""
    if dd is None or lev_qty < 1 or brake.stage >= 2:
        return 0
    if dd >= STAGE2:
        brake.stage = 2
        return math.floor(lev_qty)
    if dd >= STAGE1 and brake.stage == 0:
        brake.stage = 1
        return max(1, math.floor(lev_qty / 2))
    return 0


def sold(brake: Brake, day: str, dd: float, qty: float, usd_net: float) -> None:
    """[쓰임] 브레이크 매도 체결을 기록 — 판 돈(수수료 뺀)은 보관금으로."""
    if not brake.sales:
        brake.off_seen = False
    brake.sales.append({"date": day, "dd": round(dd, 4), "qty": qty, "usd": round(usd_net, 2)})
    brake.reserve_usd += usd_net
    brake.sold_total_usd += usd_net


def rebuy_due(brake: Brake, trend_on: bool | None) -> bool:
    """[쓰임] 되살 때인가 — 보관 중 추세가 꺼진 적이 있고 지금 켜졌으면 True (꺼짐을 기억한다).
    [이유] 판 바로 다음 날 추세가 아직 켜져 있다고 되사면 브레이크가 뜻이 없어서,
           한 번 꺼짐을 본 뒤에만."""
    if brake.stage == 0 or not brake.sales:
        return False
    if trend_on is False:
        brake.off_seen = True
        return False
    return bool(trend_on and brake.off_seen)


def rebought(brake: Brake, day: str, value_usd: float, lev_close: float) -> None:
    """[쓰임] 되산 뒤 초기화 — 단계 0, 보관금 0, 고점 기록은 오늘부터 새로."""
    brake.stage = 0
    brake.reserve_usd = 0.0
    brake.sales = []
    brake.off_seen = False
    brake.navs = [[day, nav(value_usd, brake)]]
    brake.lev_peak = lev_close


def brake_lines(brake: Brake) -> list[str]:
    """[쓰임] 매달 Deneb 알림 맨 아래 '🛑 브레이크 매도 내역' — 보관 중일 때만."""
    if not brake.sales:
        return []
    lines = ["", RULE, "🛑 브레이크 매도 내역"]
    for sale in brake.sales:
        lines.append(
            f"· {sale['date']} 고점 대비 −{sale['dd'] * 100:.1f}% → "
            f"TQQQ {sale['qty']:g}주 매도 ${sale['usd']:,.2f}"
        )
    lines.append(f"· 매도 총액 ${sum(sale['usd'] for sale in brake.sales):,.2f}")
    lines.append(sub("매도한 금액은 그대로 보유하고 계세요."))
    lines.append("     다음 매수 신호(QQQ 추세 회복)가 오면 그 돈으로 다시 매수하겠습니다.")
    return lines


# ------------------------------------------------------------------ 배당 단계


def monthly_dividend_krw(dividends: Sequence[Sequence[Any]], today: str, fx: float) -> float:
    """[쓰임] 최근 365 일 SCHD 세후 배당(달러) × 환율 ÷ 12 (월 평균 원).

    ``dividends`` 는 DenebState.dividends."""
    since = (date.fromisoformat(today) - timedelta(days=365)).isoformat()
    usd = sum(float(row[4]) for row in dividends if str(row[0]) >= since and row[1] == "SCHD")
    return usd * fx / 12


def step_due(steps_done: int, monthly_krw: float) -> int | None:
    """[쓰임] 월 배당이 다음 단계에 닿았으면 그 단계 번호(0 부터), 아니면 None."""
    if steps_done < len(DIV_STEPS_KRW) and monthly_krw >= DIV_STEPS_KRW[steps_done]:
        return steps_done
    return None


def step_due_account(steps_done: int, account_krw: float) -> int | None:
    """[쓰임] 금 섞기 켜짐: 계좌(원)가 다음 단계(:data:`ACCOUNT_STEPS_KRW`)에 닿았으면 그 번호."""
    if steps_done < len(ACCOUNT_STEPS_KRW) and account_krw >= ACCOUNT_STEPS_KRW[steps_done]:
        return steps_done
    return None


def brake_ask_line(stage: int, gold_on: bool) -> str:
    """[쓰임] 입금 안내 알림에 붙일 브레이크 한 줄 (단계 0 이면 빈 글).

    사용자 2026-10-19 '2번 진행'."""
    safe = "SCHD · 금으" if gold_on else "SCHD"  # 뒤에 '로' 가 붙는다
    if stage >= 2:
        return (
            f"🛑 Deneb 브레이크 2단계 (TQQQ 전부 팔아 달러로 보관 중) — 이번 달 Deneb 돈은 {safe}"
            "로 가고, 나스닥 추세가 다시 켜지면 보관한 달러로 TQQQ 를 되사요"
        )
    if stage == 1:
        return (
            "🛑 Deneb 브레이크 1단계 (TQQQ 절반 팔아 달러로 보관 중) — 이번 달 TQQQ 목표는 평소의"
            f" 절반, 나머지는 {safe}로. 추세가 다시 켜지면 보관한 달러로 TQQQ 를 되사요"
        )
    return ""


def lev_cap(steps_done: int) -> float:
    """[쓰임] 단계를 ``steps_done`` 번 지난 뒤 TQQQ 목표 상한 (60% − 10%p × 단계, 최소 0)."""
    return max(LEV_MAX - STEP_CUT * steps_done, 0.0)


def shift_plan(
    lev_qty: float, lev_px: float, div_px: float, total_usd: float, new_cap: float
) -> tuple[int, int]:
    """[쓰임] TQQQ 를 새 목표(전체 × new_cap)까지 팔 주수와, 그 돈으로 살 SCHD 주수 (정수)."""
    over_usd = lev_qty * lev_px - new_cap * total_usd
    sell = 0
    if over_usd > 0:
        sell = min(math.floor(lev_qty), max(math.ceil(over_usd / lev_px), 0))
    buy = math.floor(sell * lev_px / div_px) if div_px > 0 else 0
    return sell, buy


def added_monthly_krw(div_shares: int, dps_ttm: float, fx: float) -> float:
    """[쓰임] SCHD ``div_shares`` 주를 더 사면 늘어날 월 배당(세후, 원)."""
    return div_shares * dps_ttm * 0.85 * fx / 12


# ------------------------------------------------------------------ 제안 · 답


@dataclass(slots=True)
class Proposal:
    """텔레그램 제안 하나 (장부 ``proposals`` 한 줄 — 키 이름은 파일에 저장돼 바꾸지 않는다)."""

    pid: str
    """제안 번호 (버튼 값 앞머리, 예: ``h2026``)."""
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
    def from_dict(cls, data: Mapping[str, Any]) -> Proposal:
        """[쓰임] 장부 줄 → :class:`Proposal`. 모르는 키는 버리고 없는 키는 기본값."""
        known = {name: data[name] for name in cls.__dataclass_fields__ if name in data}
        return cls(**known)


_YES = ("네", "예", "응", "좋아", "진행", "ㅇㅇ", "yes", "ok", "오케이", "그래")
_NO = ("아니", "아뇨", "노", "안해", "안 해", "하지마", "no", "그대로", "패스")
_ALL = ("다팔", "전부", "전량", "모두", "다 팔")


def parse_reply(text: str, *, awaiting_qty: bool = False) -> tuple[str, int | None] | None:
    """[쓰임] 글 답 → (yes · no · cancel · all · qty, 주수). 애매하면 None (실행하지 않고 되묻는다).
    [이유] 돈이 움직이는 답이라, 확실히 알아들은 말만 받는다 ('취소' 가 다른 말보다 먼저).

    ``awaiting_qty`` (직접입력 뒤)면 숫자만 보내도('2') 주수로 읽는다."""
    lowered = text.strip().lower()
    compact = lowered.replace(" ", "")
    if "취소" in compact:
        return ("cancel", None)
    number_match = re.search(r"(\d+)\s*주", lowered)
    if number_match is None and awaiting_qty:
        number_match = re.fullmatch(r"(\d+)", compact)
    if number_match:
        count = int(number_match.group(1))
        return ("qty", count) if count > 0 else ("no", None)
    if any(word.replace(" ", "") in compact for word in _ALL):
        return ("all", None)
    if any(word.replace(" ", "") in compact for word in _NO):
        return ("no", None)
    if any(compact.startswith(word) for word in _YES):
        return ("yes", None)
    return None


def answer(proposal: Proposal, reply: tuple[str, int | None] | None, today: str, hhmm: str) -> str:
    """[쓰임] 답을 제안에 반영하고 봇이 보낼 답장을 돌려준다 (계산만).
    [이유] 취소는 답한 그날 20:50 전까지만 — 21:00 실행이 주문을 낸 뒤엔 되돌릴 수 없다."""
    if reply is None:
        return (
            "죄송해요, 정확히 이해하지 못했어요. "
            f"{proposal.symbol} {proposal.max_qty}주를 진행할까요?\n"
            + sub('"네" · "아니요" · "2주만" 처럼 답해 주세요')
        )
    kind, count = reply
    if kind == "cancel":
        can_cancel = (
            proposal.status == "accepted" and proposal.answered == today and hhmm < CANCEL_UNTIL
        )
        if can_cancel:
            proposal.status = "open"
            proposal.qty = 0
            return (
                f"알겠습니다. 오늘 밤 {proposal.symbol} 주문을 취소했어요. "
                f"제안은 {proposal.deadline}까지 그대로 열려 있어요."
            )
        return (
            "취소할 주문이 없거나 이미 주문을 낸 뒤예요(21:00). "
            "다음 Deneb 알림에서 결과를 알려 드릴게요."
        )
    if proposal.status not in ("open", "accepted"):
        return "이 제안은 이미 끝났어요. 새 제안이 생기면 다시 여쭤볼게요."
    if kind == "no":
        proposal.status = "declined"
        proposal.answered = today
        return _ack_no(proposal)
    qty = proposal.max_qty if kind in ("yes", "all") else min(int(count or 0), proposal.max_qty)
    if qty <= 0:
        return f"보유 범위 안에서 정해 주세요 (최대 {proposal.max_qty}주)."
    proposal.status = "accepted"
    proposal.qty = qty
    proposal.answered = today
    night = "오늘 밤" if hhmm < "21:00" else "다음 거래일 밤"
    cancel = ""
    if hhmm < CANCEL_UNTIL:
        cancel = f'바꾸시려면 {CANCEL_UNTIL} 전에 "취소"라고 보내 주세요.'
    return _ack_yes(proposal, night, cancel)


def _ack_yes(proposal: Proposal, night: str, cancel: str) -> str:
    """[쓰임] '진행' 답장 — 언제 무엇을 사고파는지."""
    partial = ""
    if proposal.qty < proposal.max_qty:
        partial = f" ({proposal.max_qty}주 중 {proposal.qty}주)"
    if proposal.kind == "div_step":
        lev_px = float(proposal.plan.get("lev_px", 0))
        div_px = float(proposal.plan.get("div_px", 1))
        buy = math.floor(proposal.qty * lev_px / div_px)
        body = (
            f"알겠습니다. {night} TQQQ {proposal.qty}주{partial}를 팔고 SCHD {buy}주를 사서 "
            f"TQQQ 목표를 {proposal.plan.get('new_cap', 0) * 100:.0f}%로 낮추겠습니다."
        )
    else:
        body = (
            f"알겠습니다. {night} {proposal.symbol} {proposal.qty}주{partial}를 팔고, "
            "체결을 확인한 다음 날 밤 같은 수량을 다시 사서 "
            "올해 공제를 쓰겠습니다."
        )
    tail = "체결되면 다음 Deneb 알림에 결과를 적어 둘게요."
    parts = (body, tail, sub(cancel) if cancel else "")
    return "\n".join(part for part in parts if part)


def _ack_no(proposal: Proposal) -> str:
    """[쓰임] '이번엔 안 함' 답장 — 언제 다시 묻는지."""
    if proposal.kind == "div_step":
        next_step = proposal.plan.get("next_step_krw")
        later = ""
        if next_step:
            later = f"월 배당이 {next_step / 10_000:,.0f}만 원(다음 단계)에 닿으면 다시 여쭤볼게요."
        cap = float(proposal.plan.get("cap", 0))
        return f"알겠습니다. 이번에는 TQQQ {cap * 100:.0f}%를 그대로 두겠습니다. {later}".strip()
    return "알겠습니다. 올해는 공제 실현을 하지 않겠습니다. 내년 12월에 다시 여쭤볼게요."


def remind_due(proposal: Proposal, today: str) -> bool:
    """[쓰임] 열린 제안을 다시 물을 때인가.

    배당 단계는 7 일마다, 공제 실현은 마감 2 일 전 한 번만."""
    if proposal.status != "open" or today > proposal.deadline:
        return False
    if proposal.kind == "harvest":
        last_call = date.fromisoformat(proposal.deadline) - timedelta(days=HARVEST_REMIND_BEFORE)
        return not proposal.reminded and today >= last_call.isoformat()
    last = proposal.reminded or proposal.created
    return (date.fromisoformat(today) - date.fromisoformat(last)).days >= REMIND_DAYS


def expire(proposal: Proposal, today: str) -> bool:
    """[쓰임] 마감이 지난 열린 제안을 '마감' 으로 바꾼다. 바꿨으면 True."""
    if proposal.status == "open" and today > proposal.deadline:
        proposal.status = "expired"
        return True
    return False


def harvest_deadline(year: int) -> str:
    """[쓰임] 공제 실현 마감 = 올해 결제로 잡히는 마지막 미국 거래일의 하루 앞 거래일 (여유 1 일).
    [이유] 결제일이 해를 넘기면 올해 공제가 아니라 내년 것으로 잡힌다.

    미국 거래일 D 의 주문은 한국 D 일 21:00 실행이 내므로 같은 날짜를 돌려준다."""
    trading_days: list[str] = []
    day = date(year, 12, 31)
    while day.month == 12 and len(trading_days) < 2:
        iso = day.isoformat()
        trading = day.weekday() < 5 and iso not in SETTLE_HOLIDAYS
        if trading and tax_settle(f"{iso}T16:00:00-05:00")[:4] == str(year):
            trading_days.append(iso)
        day -= timedelta(days=1)
    return trading_days[-1]


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
    account_krw: float | None = None,
    buy_symbol: str = "SCHD",
) -> str:
    """[쓰임] 배당 단계(또는 계좌 단계) 제안 글.

    ``account_krw`` 가 있으면 계좌 단계(금 섞기 켜짐) 글, 없으면 배당 단계 글."""
    usd = sell * lev_px
    if account_krw is not None:
        lines = [
            f"💰 Deneb · 계좌 단계 도달 ({step_krw / 100_000_000:,.1f}억 원)",
            RULE,
            f"Deneb 계좌가 약 {account_krw:,.0f}원이 됐어요.",
            "이제 공격 비중을 조금 줄여 지킬 때예요.",
            "",
            f"제안: TQQQ 목표 {cap * 100:.0f}% → {new_cap * 100:.0f}%",
            f"· TQQQ {sell}주 매도 (약 ${usd:,.0f} · 약 {usd * fx:,.0f}원)",
            f"· {buy_symbol} 약 {buy}주 매수 (SCHD · 금 중 더 모자란 쪽, 나머지는 매달 맞추기로)",
        ]
    else:
        lines = [
            f"💰 Deneb · 배당 단계 도달 (월 {step_krw / 10_000:,.0f}만 원)",
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
    destination = "SCHD · 금으로" if account_krw is not None else "SCHD로"
    lines += [
        "",
        f"TQQQ를 매도하고 {destination} 옮길까요?",
        sub('"네" — 오늘 밤 장 마감 지정가로 진행'),
        sub('"아니요" — 이번엔 그대로 (다음 단계에서 다시 여쭤요)'),
        sub(f'"{max(sell // 2, 1)}주만" — 일부만 진행'),
        sub(f"답이 없으면 진행하지 않고, {REMIND_DAYS}일 뒤 한 번 더 여쭤요"),
    ]
    return "\n".join(lines)


def harvest_message(
    *, year: int, room_krw: float, picks: Sequence[tuple[str, int, float]], deadline: str
) -> str:
    """[쓰임] 12 월 공제 실현 제안 글. ``picks`` = (종목, 주수, 실현 이익 원)."""
    lines = [f"🧾 {year}년 해외주식 공제 남은 {room_krw:,.0f}원", RULE]
    lines.append("이익 난 종목을 팔고 다음 날 다시 사면, 세금 없이 단가를 올릴 수 있어요.")
    for symbol, qty, gain_krw in picks:
        lines.append(f"· 예: {symbol} {qty}주 (실현 약 {gain_krw:+,.0f}원)")
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
"""버튼 값 뒤쪽 (``<pid>:<action>``)."""
Buttons = list[list[dict[str, str]]]


def proposal_buttons(proposal: Proposal) -> Buttons:
    """[쓰임] 텔레그램 인라인 버튼: [전부 진행] [직접입력] [이번엔 안 함]."""
    return [
        [
            {"text": "전부 진행", "callback_data": f"{proposal.pid}:all"},
            {"text": "직접입력", "callback_data": f"{proposal.pid}:input"},
            {"text": "이번엔 안 함", "callback_data": f"{proposal.pid}:no"},
        ]
    ]


def cancel_buttons(proposal: Proposal) -> Buttons:
    """[쓰임] 답한 뒤 붙이는 [취소] 버튼."""
    return [[{"text": "취소", "callback_data": f"{proposal.pid}:cancel"}]]


def parse_callback(data: str) -> tuple[str, str] | None:
    """[쓰임] 버튼 값 'pid:action' → (pid, action). 모르는 값이면 None."""
    pid, _, action = data.rpartition(":")
    if pid and action in ACTIONS:
        return (pid, action)
    return None


def ask_qty_message(proposal: Proposal) -> str:
    """[쓰임] [직접입력] 을 누르면 다시 보내는 알림 — 입력칸이 바로 열리게(ForceReply) 보낸다."""
    title = "공제 실현" if proposal.kind == "harvest" else "배당 단계"
    return "\n".join(
        [
            f"✏️ {title} · 직접 입력",
            RULE,
            f"{proposal.symbol}를 몇 주 팔까요? 숫자만 입력해 주세요 (최대 {proposal.max_qty}주).",
            sub("예: 2"),
            sub('그만두려면 "취소"'),
        ]
    )


def _with_cancel(proposal: Proposal, text: str, hhmm: str) -> tuple[str, Buttons | None]:
    """[쓰임] 답장 + (진행 답이고 20:50 전이면) [취소] 버튼."""
    show = proposal.status == "accepted" and hhmm < CANCEL_UNTIL
    return text, cancel_buttons(proposal) if show else None


def press(proposal: Proposal, action: str, today: str, hhmm: str) -> tuple[str, Buttons | None]:
    """[쓰임] 버튼 → (봇 답장, 답장에 붙일 버튼). [직접입력] 은 주수를 묻고 기다린다 (계산만)."""
    if action == "input" and proposal.status in ("open", "accepted"):
        proposal.awaiting_qty = True
        return ask_qty_message(proposal), None
    proposal.awaiting_qty = False
    kind = "all" if action == "input" else action
    return _with_cancel(proposal, answer(proposal, (kind, None), today, hhmm), hhmm)


def typed(proposal: Proposal, text: str, today: str, hhmm: str) -> tuple[str, Buttons | None]:
    """[쓰임] 글 답 → (봇 답장, 버튼). 직접입력 대기 중이면 숫자만 보내도 된다 (계산만)."""
    reply = parse_reply(text, awaiting_qty=proposal.awaiting_qty)
    if reply is not None:
        proposal.awaiting_qty = False
    return _with_cancel(proposal, answer(proposal, reply, today, hhmm), hhmm)


# ------------------------------------------------------------------ 공제 실현 고르기 · 상태 줄


def harvest_pick(
    qty: Mapping[str, float],
    cost_usd: Mapping[str, float],
    prices: Mapping[str, float],
    fx: float,
    room_krw: float,
) -> tuple[str, int, float] | None:
    """[쓰임] 세금 없이 실현할 이익(``room_krw``) 안에서 팔 (종목, 주수, 실현 이익 원) (계산만).
    [이유] 한 종목만 고른다 — 실현 이익이 가장 큰 것 (주문 · 답을 단순하게).

    주당 이익 = (지금 값 − 평균 원가) × 환율. 원가는 Deneb 장부 체결 금액(수수료 포함) 기준이다."""
    best: tuple[str, int, float] | None = None
    for symbol, held in qty.items():
        whole = math.floor(held)
        if whole < 1 or symbol not in prices or cost_usd.get(symbol, 0) <= 0:
            continue
        gain_per_share_krw = (prices[symbol] - cost_usd[symbol] / held) * fx
        if gain_per_share_krw <= 0:
            continue
        count = min(whole, math.floor(room_krw / gain_per_share_krw))
        if count >= 1 and (best is None or count * gain_per_share_krw > best[2]):
            best = (symbol, count, count * gain_per_share_krw)
    return best


def status_line(proposal: Proposal) -> str:
    """[쓰임] 제안 공지 맨 아래에 붙이는 지금 상태 한 줄."""
    if proposal.status == "accepted":
        word = {"selling": "매도 접수", "buying": "되사기 접수"}.get(proposal.phase, "예약")
        return f"✅ {proposal.symbol} {proposal.qty}주 {word} ({proposal.answered})"
    return {
        "open": "⏳ 답을 기다리는 중",
        "declined": "⏸ 이번엔 안 함",
        "expired": "⌛ 마감 — 진행하지 않음",
        "done": f"✅ 완료 — {proposal.symbol} {proposal.qty}주",
        "canceled": "↩️ 취소함",
    }.get(proposal.status, proposal.status)
