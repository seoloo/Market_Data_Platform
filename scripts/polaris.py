"""🧭 Polaris — 나무증권 안정 적립 실행.

예약 ``AutoTrading_Polaris`` 매일 09:00 · 00:20 · 01:20 KST.

    python -m scripts.polaris daily      # 예약이 부름: 회차 버튼 보내기 · 누른 회차 정규장 매수
    python -m scripts.polaris status     # 장부 · 지금 평가 (조회만)
    python -m scripts.polaris test-buy   # SPYM 1 주 원화 결제 시험 (정규장, 첫 회차 돈에서 뺌)

* 규칙 · 계산은 :mod:`auto_trading.ops.polaris`. 장부 ``data/polaris/state.json``.
* 회차 버튼: 그달(한국 날짜) 평일 09:00 에 한 번 — [🛒 매수] [이번 달 건너뛰기]. 그달 끝까지 유효.
* 매수: 누른 회차를 미국 정규장(뉴욕 10:00 ~ 15:30, 시세 거래일 = 뉴욕 오늘)에서. 지정가(매도
  1 호가 + 0.2%) → 최대 3 분 기다림 → 안 찬 것 취소. 남으면 다음 확인 때 다시, 3 번이면 끝.
* **12 월 맞추기**: 4 회차를 다 채운 뒤 매년 12 월 첫 평일 09:00 에 카드
  [✅ 맞추기] [올해 건너뛰기]. 누르면 정규장에 목표보다 1 주 넘게 많은 종목을
  지정가(매수 1 호가 − 0.2%)로 팔고, 판 달러(달러 증거금)로 모자란 종목을 산다.
  안 차면 다음 확인 때 다시, 3 번이면 끝. 그해 12 월 안에만.
* 원화 증거금으로 산다 — 나무 '원화로 살 수 있는 돈' 이 회차 돈보다 적으면 사지 않고 알린다
  (사용자가 직접 산 개별주 몫을 쓰지 않는다).
* 주문 결과를 모르면(:class:`OrderStateUnknownError`) 장부에 멈춤을 적고 알린다 — 새 주문 없음.
* 설정 ``polaris.on`` · ``polaris.monthly_krw``. 비상 정지 ``data/HALT`` · ``data/HALT_polaris``.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parent.parent
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from auto_trading.config.secrets import (  # noqa: E402
    load_namu_credentials,
    load_telegram_credentials,
)
from auto_trading.core.errors import AutoTradingError, OrderStateUnknownError  # noqa: E402
from auto_trading.exchange.namu import LIVE_URL, NamuTokenCache  # noqa: E402
from auto_trading.exchange.namu_global import (  # noqa: E402
    USD_MARGIN,
    NamuGlobal,
    NamuQuote,
)
from auto_trading.notify.channels import (  # noqa: E402
    NotifyError,
    OutboxNotifier,
    TelegramNotifier,
)
from auto_trading.ops import user_settings  # noqa: E402
from auto_trading.ops.polaris import (  # noqa: E402
    FEE,
    MAX_TRIES,
    ROUNDS,
    SYMBOLS,
    PolarisState,
    Rebal,
    Round,
    budget_usd,
    card_text,
    holdings_lines,
    limit_price,
    plan_buys,
    plan_rebalance,
    rebal_text,
    result_text,
    sell_price,
)

KST = ZoneInfo("Asia/Seoul")
NY = ZoneInfo("America/New_York")
STATE = REPO / "data" / "polaris" / "state.json"
OUTBOX = REPO / "reports" / "polaris" / "outbox.log"
HALTS = (REPO / "data" / "HALT", REPO / "data" / "HALT_polaris")
MAX_ORDER_USD = 1_500.0
"""한 번 주문 상한 — 회차 100 만 원의 가장 큰 몫(30%)보다 넉넉히."""
WAIT_S, POLL_S = 180.0, 15.0


def load() -> PolarisState:
    try:
        return PolarisState.from_dict(json.loads(STATE.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return PolarisState()


def save(st: PolarisState) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(STATE)


def client() -> NamuGlobal:
    return NamuGlobal(
        load_namu_credentials(REPO / ".env"),
        NamuTokenCache(REPO / "data" / "namu_token.json"),
        base_url=LIVE_URL,
        allowed=frozenset(SYMBOLS),
        max_qty=200,
        max_order_usd=MAX_ORDER_USD,
    )


def send(text: str, buttons: list[list[dict[str, str]]] | None = None) -> None:
    OutboxNotifier(OUTBOX).send(text)
    creds = load_telegram_credentials(REPO / ".env")
    if creds is None:
        return
    try:
        TelegramNotifier(creds).send_message(text, buttons=buttons)
    except NotifyError as exc:
        print(f"텔레그램 실패: {exc}")


def _now() -> datetime:
    return datetime.now(KST)


def quotes(nh: NamuGlobal) -> dict[str, NamuQuote]:
    return {s: nh.quote(s) for s in SYMBOLS}


def in_session(now: datetime, q: NamuQuote) -> bool:
    """뉴욕 평일 10:00 ~ 15:30 이고 시세 거래일이 뉴욕 오늘(휴장일 아님)."""
    ny = now.astimezone(NY)
    hm = ny.hour * 60 + ny.minute
    return ny.weekday() < 5 and 600 <= hm <= 930 and q.trade_date == ny.strftime("%Y%m%d")


def _halted(st: PolarisState) -> str:
    if st.halted:
        return st.halted
    return next((f"{p.name} 파일" for p in HALTS if p.exists()), "")


# ------------------------------------------------------------------ 회차 버튼


def offer(st: PolarisState, now: datetime) -> str:
    month = now.strftime("%Y-%m")
    if now.weekday() >= 5 or not st.due_month(month):
        return ""
    krw = float(user_settings.get(REPO, "polaris.monthly_krw"))
    if st.done_rounds == 0:
        krw -= st.prepaid_krw
    nh = client()
    try:
        q = quotes(nh)
    finally:
        nh.close()
    fx = q["SPYM"].fx
    asks = {s: x.ask or x.last for s, x in q.items()}
    plan = plan_buys(st.qty, asks, budget_usd(krw, fx))
    nonce = hashlib.sha256(f"pol:{month}:{time.time()}".encode()).hexdigest()[:10]
    st.rounds.append(Round(month, krw, offered_at=int(time.time()), nonce=nonce))
    save(st)
    text = card_text(st, month, krw, plan, asks, fx)
    send(
        text,
        [
            [
                {"text": "🛒 매수", "callback_data": f"pol:{nonce}:buy"},
                {"text": "이번 달 건너뛰기", "callback_data": f"pol:{nonce}:skip"},
            ]
        ],
    )
    return text


def _round_by_nonce(st: PolarisState, nonce: str) -> Round | None:
    return next((r for r in st.rounds if r.nonce == nonce), None)


def _rebal_by_nonce(st: PolarisState, nonce: str) -> Rebal | None:
    return next((x for x in st.rebals if x.nonce == nonce), None)


def on_buy(nonce: str) -> str:
    st = load()
    rb = _rebal_by_nonce(st, nonce)
    if rb is not None:
        if rb.status != "offered" or rb.year != _now().strftime("%Y") or _now().month != 12:
            return "⌛ 지난 버튼이에요"
        rb.status = "approved"
        save(st)
        return f"🧭 Polaris {rb.year}년 맞추기 — 오늘 밤 미국 정규장에 팔고 살게요"
    r = _round_by_nonce(st, nonce)
    if r is None or r.status != "offered":
        return "⌛ 지난 버튼이에요 (이미 누름 · 다른 달)"
    if r.month != _now().strftime("%Y-%m"):
        return "⌛ 지난 달 버튼이에요 — 이번 달 버튼을 기다려 주세요"
    r.status, r.approved_at = "approved", int(time.time())
    save(st)
    return (
        f"🧭 Polaris {int(r.month[5:])}월 {r.krw / 10_000:,.0f}만 원 — 오늘 밤 미국 정규장에 살게요"
    )


def on_skip(nonce: str) -> str:
    st = load()
    rb = _rebal_by_nonce(st, nonce)
    if rb is not None:
        if rb.status != "offered":
            return "⌛ 지난 버튼이에요"
        rb.status = "skipped"
        save(st)
        return f"🧭 Polaris {rb.year}년은 맞추지 않고 그대로 둬요"
    r = _round_by_nonce(st, nonce)
    if r is None or r.status != "offered":
        return "⌛ 지난 버튼이에요"
    r.status = "skipped"
    save(st)
    return f"🧭 Polaris {int(r.month[5:])}월은 건너뛰었어요 (회차는 그대로 남아요)"


# ------------------------------------------------------------------ 매수


def _wait(nh: NamuGlobal, ids: dict[str, str]) -> dict[str, Any]:
    """주문 ID → 마지막 상태. 다 닫히거나 ``WAIT_S`` 가 지나면 안 찬 것을 취소하고 다시 본다."""
    waited = 0.0
    sts: dict[str, Any] = {}
    while True:
        sts = {oid: nh.order_status(oid) for oid in ids}
        if all(s.status in ("FILLED", "CANCELED", "REJECTED") for s in sts.values()):
            return sts
        if waited >= WAIT_S:
            break
        nh.sleep(POLL_S)
        waited += POLL_S
    for oid, s in sts.items():
        if s.status in ("PENDING", "PARTIAL_FILLED", ""):
            nh.cancel(oid, ids[oid])
    nh.sleep(5)
    return {oid: nh.order_status(oid) for oid in ids}


def _send_orders(
    nh: NamuGlobal,
    st: PolarisState,
    side: str,
    plan: dict[str, int],
    price: dict[str, float],
    margin: str | None = None,
) -> tuple[dict[str, str], str]:
    """(주문 ID → 종목, 멈춤 글). 보내기 전에 장부에 적는다."""
    ids: dict[str, str] = {}
    for s, n in plan.items():
        limit = Decimal(str(price[s]))
        st.sent.append(
            {"id": "", "symbol": s, "side": side, "qty": n, "limit": float(limit), "sent": 0}
        )
        save(st)
        try:
            if side == "BUY":
                res = nh.order(s, side, n, limit, margin=margin)
            else:
                res = nh.order(s, side, n, limit)
        except OrderStateUnknownError as exc:
            st.halted = f"{s} 주문 결과 모름 — 나무 앱 확인 뒤 장부 정리 ({exc})"[:200]
            save(st)
            return ids, f"🛑 Polaris 멈춤: {st.halted}"
        if not res.accepted:
            st.sent[-1].update(sent=-1, note=f"{res.code} {res.message}"[:120])
            save(st)
            continue
        st.sent[-1].update(id=res.code, sent=1)
        ids[res.code] = s
        save(st)
    return ids, ""


def _filled(stt: Any) -> tuple[float, float]:
    filled = float(stt.filled_qty)
    usd = float(stt.filled_amount or 0) or filled * float(stt.avg_price or 0)
    return filled, usd


def do_rebal(st: PolarisState, rb: Rebal, now: datetime) -> str:
    """12 월 맞추기 한 번 — 팔고(익절) → 판 달러로 산다."""
    nh = client()
    try:
        q = quotes(nh)
        if not in_session(now, q["SPYM"]):
            return ""
        fx = q["SPYM"].fx
        bids = {s: x.bid for s, x in q.items()}
        asks = {s: x.ask for s, x in q.items()}
        sells, _ = plan_rebalance(st.qty, bids, asks)
        sells = {s: min(n, nh.sellable(s)) for s, n in sells.items()}
        sells = {s: n for s, n in sells.items() if n > 0}
        if not sells:
            rb.status = "done"
            save(st)
            return ""
        ids, halt = _send_orders(nh, st, "SELL", sells, {s: sell_price(bids[s]) for s in sells})
        if halt:
            return halt
        sts = _wait(nh, ids)
        year = now.strftime("%Y")
        lines = [f"🧭 Polaris {year}년 맞추기", "━━━━━━━━━━━━"]
        cash = 0.0
        for oid, s in ids.items():
            stt = sts[oid]
            if stt.status not in ("FILLED", "CANCELED", "REJECTED"):
                st.halted = f"{s} 매도 {oid} 상태 미확인({stt.status or '없음'}) — 나무 앱 확인"
            n, usd = _filled(stt)
            if n > 0:
                fee = usd * FEE
                gain = st.apply_sell(s, n, usd, fee, fx, oid, year)
                cash += usd - fee
                lines.append(
                    f"💰 {s} {n:g}주 팜 · 평균 ${usd / n:,.2f} · 이익 약 {gain * fx:+,.0f}원"
                )
        save(st)
        if st.halted:
            return "\n".join([*lines, f"🛑 Polaris 멈춤: {st.halted}"])
        budget = min(cash, nh.buyable_usd("SPYM", asks["SPYM"] or 100.0)) / (1 + FEE)
        sold = set(ids.values())
        buys = {s: n for s, n in plan_buys(st.qty, asks, budget).items() if s not in sold}
        bought = 0.0
        if buys:
            bids_ids, halt = _send_orders(
                nh, st, "BUY", buys, {s: limit_price(asks[s]) for s in buys}, USD_MARGIN
            )
            if halt:
                return "\n".join([*lines, halt])
            bsts = _wait(nh, bids_ids)
            for oid, s in bids_ids.items():
                n, usd = _filled(bsts[oid])
                if n > 0:
                    st.apply_fill(s, n, usd, usd * FEE, fx, oid)
                    bought += usd * (1 + FEE)
                    lines.append(f"🛒 {s} {n:g}주 삼 · 평균 ${usd / n:,.2f}")
        rb.tries += 1
        left, _ = plan_rebalance(st.qty, bids, asks)
        if not left or rb.tries >= MAX_TRIES:
            rb.status = "done"
        save(st)
        if cash - bought > 1:
            lines.append(f"💵 남은 달러 약 ${cash - bought:,.2f} — 계좌에 그대로")
        lines.append(f"🧾 Polaris {year}년 판 이익 약 {st.realized_krw.get(year, 0.0):,.0f}원")
        prices = {s: x.last for s, x in q.items()}
        lines += holdings_lines(st, prices, fx)
        return "\n".join(lines)
    finally:
        nh.close()


def offer_rebal(st: PolarisState, now: datetime) -> str:
    """4 회차를 다 채운 뒤 매년 12 월 첫 평일 — 맞추기 카드 (맞출 것이 없으면 짧게 알림)."""
    year = now.strftime("%Y")
    if (
        now.month != 12
        or now.weekday() >= 5
        or st.done_rounds < ROUNDS
        or st.rebal_for(year) is not None
        or sum(st.qty.values()) <= 0
    ):
        return ""
    nh = client()
    try:
        q = quotes(nh)
    finally:
        nh.close()
    bids = {s: x.bid or x.last for s, x in q.items()}
    asks = {s: x.ask or x.last for s, x in q.items()}
    sells, buys = plan_rebalance(st.qty, bids, asks)
    if not sells:
        st.rebals.append(Rebal(year, status="none"))
        save(st)
        text = f"🧭 Polaris {year}년 12월 — 비율이 목표와 1주 안쪽이라 올해는 맞출 것 없어요"
        send(text)
        return text
    nonce = hashlib.sha256(f"polr:{year}:{time.time()}".encode()).hexdigest()[:10]
    st.rebals.append(Rebal(year, nonce=nonce))
    save(st)
    text = rebal_text(st, year, sells, buys, bids, asks, q["SPYM"].fx)
    send(
        text,
        [
            [
                {"text": "✅ 맞추기", "callback_data": f"pol:{nonce}:buy"},
                {"text": "올해 건너뛰기", "callback_data": f"pol:{nonce}:skip"},
            ]
        ],
    )
    return text


def buy_round(st: PolarisState, r: Round, now: datetime) -> str:
    nh = client()
    try:
        q = quotes(nh)
        if not in_session(now, q["SPYM"]):
            return ""
        fx = q["SPYM"].fx
        asks = {s: x.ask for s, x in q.items()}
        left_krw = r.krw - r.spent_krw
        avail = nh.buyable_krw("SPYM", asks["SPYM"] or 100.0)
        if avail < left_krw:
            r.tries += 1
            r.note = f"원화 부족 {avail:,.0f} < {left_krw:,.0f}"
            if r.tries >= MAX_TRIES:
                r.status = "done"
            save(st)
            return (
                f"⚠️ Polaris {int(r.month[5:])}월 — 나무 원화로 살 수 있는 돈 {avail:,.0f}원이"
                f" 이번 회차 남은 {left_krw:,.0f}원보다 적어 안 샀어요"
                + (" (이번 회차 끝)" if r.status == "done" else " · 다음 확인 때 다시")
            )
        plan = plan_buys(st.qty, asks, budget_usd(left_krw, fx))
        if not plan:
            r.status = "done"
            save(st)
            return ""
        ids: dict[str, str] = {}
        for s, n in plan.items():
            limit = Decimal(str(limit_price(asks[s])))
            st.sent.append({"id": "", "symbol": s, "qty": n, "limit": float(limit), "sent": 0})
            save(st)  # 보내기 전에 적어 둔다 — 결과를 모르면 이 줄이 남는다
            try:
                res = nh.order(s, "BUY", n, limit)
            except OrderStateUnknownError as exc:
                st.halted = f"{s} 주문 결과 모름 — 나무 앱 확인 뒤 장부 정리 ({exc})"[:200]
                save(st)
                return f"🛑 Polaris 멈춤: {st.halted}"
            if not res.accepted:
                st.sent[-1].update(sent=-1, note=f"{res.code} {res.message}"[:120])
                save(st)
                continue
            st.sent[-1].update(id=res.code, sent=1)
            ids[res.code] = s
            save(st)
        sts = _wait(nh, ids)
        fills: dict[str, tuple[float, float]] = {}
        spent = 0.0
        for oid, s in ids.items():
            stt = sts[oid]
            if stt.status not in ("FILLED", "CANCELED", "REJECTED"):
                st.halted = f"{s} 주문 {oid} 상태 미확인({stt.status or '없음'}) — 나무 앱 확인"
            filled = float(stt.filled_qty)
            if filled > 0:
                usd = float(stt.filled_amount or 0) or filled * float(stt.avg_price or 0)
                fee = usd * FEE
                st.apply_fill(s, filled, usd, fee, fx, oid)
                fills[s] = (filled, usd)
                spent += usd + fee
        r.spent_usd += spent
        r.spent_krw += spent * fx
        r.tries += 1
        left = {s: n - int(fills.get(s, (0.0, 0.0))[0]) for s, n in plan.items()}
        left = {s: n for s, n in left.items() if n > 0}
        if not left or r.tries >= MAX_TRIES:
            r.status = "done"
        save(st)
        prices = {s: x.last for s, x in q.items()}
        text = result_text(r.month, fills, left, spent, fx, st, prices, final=r.status == "done")
        if st.halted:
            text += f"\n🛑 Polaris 멈춤: {st.halted}"
        return text
    finally:
        nh.close()


def daily() -> int:
    # Paper(가짜 돈)는 실거래 설정과 상관없이 먼저
    # (2026-10-20 사용자 "polaris 같은 경우 paper만 작동하도록 하면 돼")
    try:
        from scripts import polaris_paper

        polaris_paper.daily()
    except Exception as exc:  # paper 실패가 실거래 확인을 막지 않게
        print(f"Polaris Paper 실패: {type(exc).__name__} {exc}")
    if not user_settings.get(REPO, "polaris.on"):
        print("설정 polaris.on 꺼짐")
        return 0
    st = load()
    why = _halted(st)
    if why:
        print(f"멈춤: {why}")
        return 0
    now = _now()
    out: list[str] = []
    try:
        if now.hour >= 8:  # 아침 확인 — 회차 버튼 · 12 월 맞추기 카드
            for card in (offer(st, now), offer_rebal(st, now)):
                if card:
                    out.append(card)
        rb = next((x for x in st.rebals if x.status == "approved"), None)
        if rb is not None:
            if now.month != 12 or rb.year != now.strftime("%Y"):
                rb.status, rb.note = "done", "12 월이 지나 그만둠"
                save(st)
            else:
                msg = do_rebal(st, rb, now)
                if msg:
                    send(msg)
                    out.append(msg)
        r = next((x for x in st.rounds if x.status == "approved"), None)
        if r is not None:
            msg = buy_round(st, r, now)
            if msg:
                send(msg)
                out.append(msg)
    except AutoTradingError as exc:
        send(f"⚠️ Polaris 확인 오류: {type(exc).__name__} {exc}"[:400])
        raise
    print("\n\n".join(out) or "할 일 없음")
    return 0


def status() -> int:
    st = load()
    nh = client()
    try:
        q = quotes(nh)
    finally:
        nh.close()
    fx = q["SPYM"].fx
    prices = {s: x.last for s, x in q.items()}
    cost = sum(st.cost_krw.values())
    value = sum(st.qty[s] * prices[s] for s in SYMBOLS) * fx
    print(f"🧭 Polaris · 회차 {st.done_rounds}/{ROUNDS} · 멈춤 {_halted(st) or '없음'}")
    print(f"   매입 {cost:,.0f}원 → 평가 {value:,.0f}원 (환율 {fx:,.0f})")
    print("\n".join(holdings_lines(st, prices, fx)) or "   보유 없음")
    for r in st.rounds:
        print(f"   {r.month} {r.status} {r.krw:,.0f}원 · 쓴 ${r.spent_usd:,.2f} · {r.tries}번")
    return 0


def test_buy() -> int:
    """첫 회차 전 SPYM 1 주 — 원화 결제 확인. 산 돈은 첫 회차에서 뺀다."""
    st = load()
    if st.done_rounds or st.orders or st.sent:
        print("이미 장부에 매수가 있어 시험하지 않음")
        return 1
    nh = client()
    try:
        q = nh.quote("SPYM")
        now = _now()
        if not in_session(now, q):
            print("미국 정규장(뉴욕 10:00 ~ 15:30)에만 시험")
            return 1
        limit = Decimal(str(limit_price(q.ask)))
        before = nh.buyable_krw("SPYM", q.ask)
        st.sent.append({"id": "", "symbol": "SPYM", "qty": 1, "limit": float(limit), "sent": 0})
        save(st)
        res = nh.order("SPYM", "BUY", 1, limit)
        print(f"주문: {res}")
        if not res.accepted:
            st.sent[-1].update(sent=-1, note=f"{res.code} {res.message}"[:120])
            save(st)
            return 1
        st.sent[-1].update(id=res.code, sent=1)
        save(st)
        sts = _wait(nh, {res.code: "SPYM"})
        s = sts[res.code]
        print(f"상태: {s}")
        filled = float(s.filled_qty)
        usd = 0.0
        if filled > 0:
            usd = float(s.filled_amount or 0) or filled * float(s.avg_price or 0)
            st.apply_fill("SPYM", filled, usd, usd * FEE, q.fx, res.code)
            st.prepaid_krw += (usd * (1 + FEE)) * q.fx
            save(st)
        after = nh.buyable_krw("SPYM", q.ask)
        print(f"원화로 살 수 있는 돈 {before:,.0f} → {after:,.0f}원 (차이 {before - after:,.0f})")
        print(f"조회 환율 {q.fx:,.0f} · 체결 ${usd:,.2f}")
    finally:
        nh.close()
    return 0


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "daily":
        return daily()
    if cmd == "status":
        return status()
    if cmd == "test-buy":
        return test_buy()
    print(__doc__)
    return 2


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv))
