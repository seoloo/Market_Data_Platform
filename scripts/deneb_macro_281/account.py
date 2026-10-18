"""Decimal accounting extension of research 184, independent of every live executor."""

from __future__ import annotations

import math
import statistics
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

D = Decimal
ZERO = D(0)
ONE = D(1)
FIXED_FX_KRW_PER_USD = D(1400)


def dec(value: Any) -> Decimal:
    return D(str(value))


def tax(gain: Decimal) -> Decimal:
    return max(ZERO, gain - D(2_500_000)) * D("0.22")


@dataclass
class Book:
    fee: Decimal
    qty: dict[str, Decimal] = field(default_factory=lambda: {"L": ZERO, "D": ZERO})
    basis: dict[str, Decimal] = field(default_factory=lambda: {"L": ZERO, "D": ZERO})
    gains: dict[str, Decimal] = field(default_factory=dict)
    fees: Decimal = ZERO
    turnover: Decimal = ZERO
    trades: list[dict[str, str]] = field(default_factory=list)

    def buy(self, symbol: str, amount: Decimal, px: Decimal, day: str, reason: str) -> None:
        if amount <= ZERO:
            return
        fee = amount * self.fee
        qty = (amount - fee) / px
        self.qty[symbol] += qty
        self.basis[symbol] += amount
        self.fees += fee
        self.turnover += qty * px
        self.trades.append(
            {
                "day": day,
                "symbol": symbol,
                "side": "BUY",
                "qty": str(qty),
                "price": str(px),
                "cash": str(-amount),
                "fee": str(fee),
                "reason": reason,
            }
        )

    def sell(self, symbol: str, qty: Decimal, px: Decimal, day: str, reason: str) -> Decimal:
        qty = min(max(qty, ZERO), self.qty[symbol])
        if qty <= ZERO:
            return ZERO
        gross = qty * px
        fee = gross * self.fee
        basis = self.basis[symbol] * qty / self.qty[symbol]
        self.qty[symbol] -= qty
        self.basis[symbol] -= basis
        self.gains[day[:4]] = self.gains.get(day[:4], ZERO) + gross - fee - basis
        self.fees += fee
        self.turnover += gross
        self.trades.append(
            {
                "day": day,
                "symbol": symbol,
                "side": "SELL",
                "qty": str(qty),
                "price": str(px),
                "cash": str(gross - fee),
                "fee": str(fee),
                "reason": reason,
            }
        )
        return gross - fee

    def scale_for_tax(self, fraction: Decimal) -> None:
        # 184's common proportional-tax-payment approximation, not a real sell order.
        for symbol in self.qty:
            self.qty[symbol] *= fraction
            self.basis[symbol] *= fraction


@dataclass
class Dial:
    multiplier: Decimal = ONE
    armed: bool = True
    off_seen: bool = False

    def update(self, signal: Mapping[str, Any] | None, trend: bool | None) -> str | None:
        before = self.multiplier
        reason = None
        if self.multiplier < 1 and trend is False:
            self.off_seen = True
        if self.multiplier < 1 and self.off_seen and trend is True:
            self.multiplier, self.armed, self.off_seen = D(1), False, False
            reason = "PRICE_RETURN"
        if signal is not None:
            p, b = signal["probabilities"]["LR"], signal["base"]
            if signal["macro_missing"] >= 3:
                self.multiplier, self.off_seen = D("0.5"), False
                reason = "MISSING_REDUCE"
            elif p <= b:
                self.multiplier, self.armed, self.off_seen = D(1), True, False
                reason = "MACRO_CLEAR"
            elif p >= 2 * b and self.armed and self.multiplier == 1:
                self.multiplier, self.off_seen = D("0.5"), trend is False
                reason = "MACRO_ENTER"
        if self.multiplier == before:
            return None
        return reason


def simulate(
    data: Mapping[str, Any],
    predictions: Sequence[Mapping[str, Any]],
    *,
    start: str,
    end: str,
    mode: str,
    constant: float,
    fee: str = "0.0015",
    delay: int = 0,
    monthly_krw: int = 100_000,
) -> dict[str, Any]:
    all_days = data["days"]
    days = [d for d in all_days if start <= d <= end]
    locations = {d: i for i, d in enumerate(all_days)}
    known = {}
    for row in predictions:
        j = locations.get(row["execute"])
        if j is not None and j + delay < len(all_days):
            known[all_days[j + delay]] = row
    bk, dial = Book(D(fee)), Dial(D(1) if mode != "STATIC" else dec(constant))
    reserve, macro, units, contributed = ZERO, ZERO, ZERO, ZERO
    stage, off_seen, peak = 0, False, ZERO
    navs, ledger, flows, events = [], [], [], []
    taxes: dict[str, Decimal] = {}
    previous_value, after_nav, previous_qty = ZERO, D(1), dict(bk.qty)

    def value(px: Mapping[str, Decimal]) -> Decimal:
        return sum((bk.qty[s] * px[s] for s in ("L", "D")), ZERO) + reserve + macro

    for day in days:
        i = locations[day]
        prev = all_days[i - 1]
        px = {
            "L": dec(data["lev"][day]) * FIXED_FX_KRW_PER_USD,
            "D": dec(data["div"][day]) * FIXED_FX_KRW_PER_USD,
        }
        old_px = {
            "L": dec(data["lev"][prev]) * FIXED_FX_KRW_PER_USD,
            "D": dec(data["div"][prev]) * FIXED_FX_KRW_PER_USD,
        }
        gain = {s: previous_qty[s] * (px[s] - old_px[s]) for s in ("L", "D")}
        before_fees = bk.fees
        paid_today, added = ZERO, ZERO
        new_month = day[:7] != prev[:7]
        if new_month and day[5:7] == "06" and units > ZERO:
            year = str(int(day[:4]) - 1)
            if year not in taxes:
                paid_today = tax(bk.gains.get(year, ZERO))
                taxes[year] = paid_today
                total = value(px)
                if total > ZERO:
                    fraction = max(ZERO, 1 - paid_today / total)
                    bk.scale_for_tax(fraction)
                    reserve, macro, units = reserve * fraction, macro * fraction, units * fraction
        if units > ZERO and bk.qty["L"] > ZERO and stage < 2:
            dd = 1 - old_px["L"] / peak if peak > ZERO else ZERO
            quantity = ZERO
            if dd >= D("0.30"):
                quantity, stage = bk.qty["L"], 2
            elif dd >= D("0.15") and stage == 0:
                quantity, stage = bk.qty["L"] / 2, 1
            if quantity > ZERO:
                was_empty = reserve == ZERO
                reserve += bk.sell("L", quantity, px["L"], day, "BRAKE")
                if was_empty:
                    off_seen = False
        trend = data["trend"].get(prev)
        old_multiplier = dial.multiplier
        reason = dial.update(known.get(day), trend) if mode == "MACRO" else None
        if reason:
            events.append(
                {
                    "day": day,
                    "reason": reason,
                    "brake_stage": stage,
                    "from": str(old_multiplier),
                    "to": str(dial.multiplier),
                }
            )
        if dial.multiplier < old_multiplier:
            macro += bk.sell(
                "L",
                bk.qty["L"] * (1 - dial.multiplier / old_multiplier),
                px["L"],
                day,
                reason or "REDUCE",
            )
        elif dial.multiplier > old_multiplier and macro > ZERO:
            if stage == 0:
                bk.buy("L", macro, px["L"], day, reason or "RETURN")
            else:
                reserve += macro
            macro = ZERO
        if stage > 0 and reserve > ZERO:
            if trend is False:
                off_seen = True
            if trend is True and off_seen:
                bk.buy("L", reserve * dial.multiplier, px["L"], day, "BRAKE_RETURN")
                macro += reserve * (1 - dial.multiplier)
                reserve, stage, off_seen, peak = ZERO, 0, False, px["L"]
        if new_month:
            before = value(px)
            current_nav = before / units if units > ZERO else D(1)
            added = D(monthly_krw)
            units += added / current_nav
            contributed += added
            flows.append((day, float(added)))
            active = bk.qty["L"] * px["L"] + bk.qty["D"] * px["D"] + macro + added
            v = data["vol"].get(prev)
            share = D("0.6") * (min(D(1), D("0.20") / dec(v)) if v and v > 0 else D(1))
            share *= (D(1), D("0.5"), ZERO)[stage]
            targets = {"L": active * share * dial.multiplier, "D": active * (1 - share)}
            desired_cash = active * share * (1 - dial.multiplier)
            cash, macro = added + macro, ZERO
            for symbol in ("L", "D"):
                excess = bk.qty[symbol] * px[symbol] - targets[symbol]
                if excess > ZERO:
                    cash += bk.sell(symbol, excess / px[symbol], px[symbol], day, "MONTH")
            macro = min(cash, desired_cash)
            cash -= macro
            needs = {s: max(ZERO, targets[s] - bk.qty[s] * px[s]) for s in ("L", "D")}
            total = sum(needs.values(), ZERO)
            if total > ZERO:
                for symbol in ("L", "D"):
                    bk.buy(symbol, cash * needs[symbol] / total, px[symbol], day, "MONTH")
            elif cash > ZERO:
                bk.buy("D", cash, px["D"], day, "MONTH")
        total_value = value(px)
        fees_today = bk.fees - before_fees
        error = total_value - (
            previous_value + sum(gain.values(), ZERO) + added - fees_today - paid_today
        )
        if abs(error) > D("0.000001"):
            raise ValueError(f"Ledger reconciliation failed {day}: {error}")
        if previous_value > ZERO:
            after_nav *= (total_value - added) / previous_value
        elif added > ZERO:
            after_nav = total_value / added
        nav = total_value / units if units > ZERO else D(1)
        navs.append(float(nav))
        ledger.append(
            {
                "day": day,
                "value": str(total_value),
                "nav": str(nav),
                "after_tax_nav": str(after_nav),
                "deposit": str(added),
                "price_gain_L": str(gain["L"]),
                "price_gain_D": str(gain["D"]),
                "fees": str(fees_today),
                "tax": str(paid_today),
                "TQQQ_qty": str(bk.qty["L"]),
                "SCHD_qty": str(bk.qty["D"]),
                "brake_cash": str(reserve),
                "macro_cash": str(macro),
                "exposure": float(bk.qty["L"] * px["L"] / total_value) if total_value else 0,
                "dial": str(dial.multiplier),
                "brake_stage": stage,
                "reconciliation_error": str(error),
            }
        )
        previous_value, previous_qty = total_value, dict(bk.qty)
        peak = max(peak, px["L"])
    final_px = {
        "L": dec(data["lev"][days[-1]]) * FIXED_FX_KRW_PER_USD,
        "D": dec(data["div"][days[-1]]) * FIXED_FX_KRW_PER_USD,
    }
    final_gain = bk.gains.get(days[-1][:4], ZERO)
    final_tax = tax(final_gain)
    unrealized = sum(
        (bk.qty[s] * final_px[s] * (1 - bk.fee) - bk.basis[s] for s in ("L", "D")), ZERO
    )
    liquidation_tax = max(ZERO, tax(final_gain + unrealized) - final_tax)
    # Preserve legacy figures for parity, but debit actual end liquidation costs in the pilot.
    after_hold = value(final_px) - final_tax
    legacy_after_liquidation = after_hold - liquidation_tax
    closing_fee = sum((bk.qty[s] * final_px[s] * bk.fee for s in ("L", "D")), ZERO)
    combined_final_tax = tax(final_gain + unrealized)
    after_liquidation = value(final_px) - closing_fee - combined_final_tax
    from scripts.run_brake_184 import irr

    risk = risk_stats([float(dec(r["after_tax_nav"])) for r in ledger])
    legacy = risk_stats(navs)
    return {
        "mode": mode,
        "start": days[0],
        "end": days[-1],
        "monthly_krw": monthly_krw,
        "fixed_fx_krw_per_usd": str(FIXED_FX_KRW_PER_USD),
        "contributed_krw": str(contributed),
        "after_tax_hold_krw": str(after_hold),
        "after_tax_liquidation_krw": str(after_liquidation),
        "net_profit_krw": str(after_liquidation - contributed),
        "after_tax_irr": irr(flows, days[-1], float(after_liquidation)),
        "fees_krw": str(bk.fees + closing_fee),
        "fees_during_krw": str(bk.fees),
        "closing_fee_krw": str(closing_fee),
        "taxes_paid_krw": str(sum(taxes.values(), ZERO)),
        "final_realized_tax_krw": str(final_tax),
        "liquidation_tax_krw": str(liquidation_tax),
        "combined_final_tax_krw": str(combined_final_tax),
        "legacy_after_tax_liquidation_krw": str(legacy_after_liquidation),
        "average_exposure": statistics.fmean(r["exposure"] for r in ledger),
        "maximum_exposure": max(r["exposure"] for r in ledger),
        "annual_turnover": float(bk.turnover)
        / statistics.fmean(float(r["value"]) for r in ledger)
        / (len(days) / 252),
        "trades": len(bk.trades),
        "event_counts": dict(Counter(e["reason"] for e in events)),
        "legacy_nav": legacy,
        "tax_payment_nav": risk,
        "ledger": ledger,
        "orders": bk.trades,
        "events": events,
        "nav_days": days,
        "navs": navs,
    }


def risk_stats(navs: Sequence[float]) -> dict[str, float]:
    high, mdd = navs[0], 0.0
    returns = []
    for i, value in enumerate(navs):
        high = max(high, value)
        mdd = max(mdd, 1 - value / high)
        if i:
            returns.append(value / navs[i - 1] - 1)
    sd = statistics.pstdev(returns) if returns else 0
    return {
        "cagr": (navs[-1] / navs[0]) ** (252 / len(navs)) - 1,
        "sharpe": statistics.fmean(returns) / sd * math.sqrt(252) if sd else 0,
        "mdd": mdd,
    }
