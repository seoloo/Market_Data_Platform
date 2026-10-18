"""✨ Sirius — 미장 주도주 GBM 실거래 (문서 279, 토스 소수점) — 순수 계산.

주문은 ``scripts/sirius_live.py``.

규칙은 274 고르기 × 275/276 6 달 보유 그대로 (Paper 277 의 GBM10F 와 같음):

* 매달 마지막 거래일 종가까지의 자료로 고른 상위 10 종목을, 다음 거래일 정규장에서 **금액 주문**
  (소수점 시장가)으로 산다. 한 번에 쓰는 돈 = min(전략 현금 + 이번에 팔 돈, 평가액 ÷ 6),
  종목마다 같은 금액.
* 산 날로부터 **126 거래일째** 정규장에서 그 묶음을 **소수점 시장가로 전부 판다.** 손절 없음.
* 같은 날 팔 묶음의 종목이 새로 다시 뽑히면 팔지 않고 새 묶음으로 넘긴다(차액만 사고판다
  — 수수료 · 환전 아낌, 사용자 2026-10-18 설명).
* 장부는 이 전략 몫만 — 토스 계좌의 다른 전략 · 사용자 보유와 섞지 않는다. 체결은 주문 상세
  (체결 수량 · 금액 · 수수료)로만 장부에 넣는다.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

N_PICKS = 10
SLOTS = 6
HOLD = 126
MIN_ORDER_USD = 1.0
"""이보다 작은 금액 차이는 사고팔지 않는다."""
SELL_PROCEEDS_HAIRCUT = 0.99
"""살 돈을 잡을 때 오늘 팔 돈은 1% 깎아 셈(수수료 · 시세 변동)."""


def new_state(start: str) -> dict[str, Any]:
    return {
        "start": start,
        "deposits": [],
        "cash_usd": 0.0,
        "lots": [],
        "closed": [],
        "decisions": {},
        "orders": [],
        "history": [],
    }


def deposit(state: dict[str, Any], day: str, krw: float, usd: float, fx: float) -> None:
    state["deposits"].append([day, krw, usd, fx])
    state["cash_usd"] = float(state["cash_usd"]) + usd


def age(buy_day: str, bar_days: Sequence[str], today: str) -> int:
    """산 날 뒤 지나간 거래일 수(오늘 포함). 오늘 = 아직 일봉이 없는 정규장 날."""
    return sum(1 for d in bar_days if buy_day < d < today) + (1 if today > buy_day else 0)


def month_due(decisions: Mapping[str, Any], last_bar: str, today: str) -> str | None:
    """오늘이 새 달 첫 정규장이고 지난달 마지막 거래일(``last_bar``) 결정이 없으면 그 날짜."""
    if today[:7] != last_bar[:7] and last_bar not in decisions:
        return last_bar
    return None


def equity(state: Mapping[str, Any], prices: Mapping[str, float]) -> float:
    """현금 + 보유 평가(시세 없으면 원가)."""
    held = 0.0
    for lot in state["lots"]:
        px = prices.get(lot["sym"])
        held += float(lot["qty"]) * px if px else float(lot["cost_usd"])
    return float(state["cash_usd"]) + held


def client_id(today: str, sym: str, side: str) -> str:
    """토스 clientOrderId (36 자 · 영숫자 - _)."""
    return f"SIR-{today}-{sym.replace('.', '_')}-{side[0]}"[:36]


def plan(
    state: Mapping[str, Any],
    today: str,
    bar_days: Sequence[str],
    prices: Mapping[str, float],
    picks: Sequence[str] | None,
    tranche: str | None,
) -> list[dict[str, Any]]:
    """오늘 낼 주문(팔기 먼저). ``picks`` 가 있으면 ``tranche``(결정 월말) 묶음을 새로 산다.

    주문: {sym, side, usd 또는 qty, kind: sell · buy · roll, lots: [넘기거나 파는 묶음 id],
    tranche, ref_price}. roll 은 같은 종목의 판 묶음을 새 묶음으로 넘기며 차액만 사고판다
    (차액이 작으면 주문 없이 kind=roll, side=NONE)."""
    due: dict[str, list[dict[str, Any]]] = {}
    for lot in state["lots"]:
        if age(lot["buy_day"], bar_days, today) >= HOLD:
            due.setdefault(lot["sym"], []).append(lot)
    orders: list[dict[str, Any]] = []
    wanted = list(picks or [])[:N_PICKS]
    budget_each = 0.0
    if wanted and tranche:
        due_value = sum(
            float(lot["qty"]) * prices[s] for s, lots in due.items() for lot in lots if s in prices
        )
        budget = min(
            float(state["cash_usd"]) + SELL_PROCEEDS_HAIRCUT * due_value,
            equity(state, prices) / SLOTS,
        )
        budget_each = max(0.0, budget) / len(wanted)
    for sym, lots in sorted(due.items()):
        qty = sum(float(lot["qty"]) for lot in lots)
        ids = [lot["id"] for lot in lots]
        px = prices.get(sym)
        if sym in wanted and px:
            diff = budget_each - qty * px
            base = {"sym": sym, "kind": "roll", "lots": ids, "tranche": tranche, "ref_price": px}
            if diff >= MIN_ORDER_USD:
                orders.append({**base, "side": "BUY", "usd": round(diff, 2)})
            elif diff <= -MIN_ORDER_USD:
                orders.append({**base, "side": "SELL", "qty": round(min(qty, -diff / px), 6)})
            else:
                orders.append({**base, "side": "NONE"})
        else:
            orders.append({"sym": sym, "kind": "sell", "side": "SELL", "qty": round(qty, 6),
                           "lots": ids, "tranche": None, "ref_price": px})  # fmt: skip
    sells = [o for o in orders if o["side"] == "SELL"]
    rest = [o for o in orders if o["side"] != "SELL"]
    if budget_each >= MIN_ORDER_USD:
        for sym in wanted:
            if sym in due:
                continue
            rest.append({"sym": sym, "kind": "buy", "side": "BUY", "usd": round(budget_each, 2),
                         "lots": [], "tranche": tranche, "ref_price": prices.get(sym)})  # fmt: skip
    return sells + rest


def _close_lot(state: dict[str, Any], lot: Mapping[str, Any], value: float, day: str) -> None:
    state["closed"].append(
        {"sym": lot["sym"], "buy_day": lot["buy_day"], "sell_day": day,
         "cost_usd": lot["cost_usd"], "value_usd": value,
         "ret": value / float(lot["cost_usd"]) - 1 if float(lot["cost_usd"]) > 0 else 0.0}
    )  # fmt: skip


def apply(
    state: dict[str, Any],
    order: Mapping[str, Any],
    day: str,
    filled_qty: float,
    filled_usd: float,
    commission: float,
) -> None:
    """체결(또는 주문 없는 roll)을 장부에 넣는다. 체결 0 이면 산 · 판 것 없음(팔 묶음은 그대로 남아
    다음 실행에서 다시 판다)."""
    lots = [lot for lot in state["lots"] if lot["id"] in set(order.get("lots") or [])]
    sym, kind, side = order["sym"], order["kind"], order["side"]
    if side == "BUY":
        state["cash_usd"] = float(state["cash_usd"]) - filled_usd - commission
    elif side == "SELL":
        state["cash_usd"] = float(state["cash_usd"]) + filled_usd - commission
    if kind == "buy":
        if filled_qty > 0:
            state["lots"].append(
                {"id": f"{order['tranche']}:{sym}", "sym": sym, "qty": filled_qty,
                 "buy_day": day, "cost_usd": filled_usd + commission, "tranche": order["tranche"]}
            )  # fmt: skip
        return
    held = sum(float(lot["qty"]) for lot in lots)
    if kind == "sell":
        if filled_qty <= 0:
            return
        avg = (filled_usd - commission) / filled_qty
        left = max(0.0, held - filled_qty)
        for lot in lots:
            share = float(lot["qty"]) / held if held > 0 else 0.0
            _close_lot(state, lot, (filled_usd - commission) * share, day)
            state["lots"].remove(lot)
        if left > 1e-6:  # 덜 팔린 양은 같은 묶음으로 남겨 다음에 다시 판다
            first = lots[0]
            state["lots"].append({**first, "qty": left, "cost_usd": left * avg})
        return
    # roll — 판 묶음을 기준가로 닫고, 같은 종목 새 묶음을 연다
    if filled_qty <= 0 and side in ("BUY", "SELL"):
        return  # 차액 주문이 안 됐으면 넘기지 않는다 — 다음 실행에서 다시
    ref = float(order["ref_price"])
    for lot in lots:
        _close_lot(state, lot, float(lot["qty"]) * ref, day)
        state["lots"].remove(lot)
    if side == "BUY":
        qty, cost = held + filled_qty, held * ref + filled_usd + commission
    elif side == "SELL":
        qty = held - filled_qty
        cost = qty * ref
    else:
        qty, cost = held, held * ref
    if qty > 1e-6:
        state["lots"].append(
            {"id": f"{order['tranche']}:{sym}", "sym": sym, "qty": qty, "buy_day": day,
             "cost_usd": cost, "tranche": order["tranche"]}
        )  # fmt: skip


def holdings(state: Mapping[str, Any]) -> dict[str, tuple[float, float]]:
    """종목 → (수량, 원가 $)."""
    out: dict[str, tuple[float, float]] = {}
    for lot in state["lots"]:
        q, c = out.get(lot["sym"], (0.0, 0.0))
        out[lot["sym"]] = (q + float(lot["qty"]), c + float(lot["cost_usd"]))
    return out


# ------------------------------------------------------------------ 정리 (전부 정리 · Sirius 만)

LIQUIDATE = Path("data") / "sirius" / "LIQUIDATE.json"
"""정리 요청 (저장소 루트 기준).

있으면 Sirius 는 사지 않고, 정규장에서 제 몫을 소수점 시장가로 판다."""


def request_liquidation(root: Path, reason: str, at_ms: int) -> bool:
    """정리 요청을 남긴다. 이미 있으면 그대로(False)."""
    path = root / LIQUIDATE
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"reason": reason, "at_ms": at_ms}, ensure_ascii=False), "utf-8")
    return True


def liquidation_request(root: Path) -> dict[str, Any] | None:
    try:
        d = json.loads((root / LIQUIDATE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def clear_liquidation(root: Path) -> bool:
    path = root / LIQUIDATE
    if path.exists():
        path.unlink()
        return True
    return False


def liquidation_orders(
    state: Mapping[str, Any], prices: Mapping[str, float]
) -> list[dict[str, Any]]:
    """보유 묶음 전부를 종목별 소수점 매도 한 건씩으로."""
    by: dict[str, list[Mapping[str, Any]]] = {}
    for lot in state["lots"]:
        by.setdefault(lot["sym"], []).append(lot)
    out = []
    for sym, lots in sorted(by.items()):
        qty = round(sum(float(lot["qty"]) for lot in lots), 6)
        if qty <= 0:
            continue
        out.append({"sym": sym, "kind": "sell", "side": "SELL", "qty": qty,
                    "lots": [lot["id"] for lot in lots], "tranche": None,
                    "ref_price": prices.get(sym)})  # fmt: skip
    return out
