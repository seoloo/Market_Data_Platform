"""문서 179 — 계좌별 세후 금액 (순수 계산). 주문 없음.

* 매달 초 납입 → 목표 비중으로 다시 맞춤(모자라면 팜) → 그 달 수익 · 분배금.
* 매도 차익은 평균 단가로 셈한다.
* 세금
  - T(해외 직투 과세): 해마다 손익 합산 − 공제 → 세율. 12 월 말에 계좌에서 낸다.
  - G(국내 상장 ETF 일반 계좌): 팔 때마다 차익(이익만)에 세율.
  - I(ISA): 계좌 안 과세 없음. 끝날 때 (평가액 − 넣은 돈 − 공제) 에 세율.
* 세금을 내려고 파는 데서 생기는 차익은 무시한다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Any


@dataclass(frozen=True, slots=True)
class Account:
    kind: str  # "T" | "G" | "I"
    cost: float  # 매매 편도 비용
    gain_rate: float
    deduction: float  # T: 해마다 · I: 끝날 때 한 번 · G: 0
    dist_rate: float  # 분배금 세율(계좌 안에서 바로)


TAXABLE = Account("T", 0.0015, 0.22, 2_500_000.0, 0.15)
ISA = Account("I", 0.0002, 0.099, 2_000_000.0, 0.0)
GENERAL = Account("G", 0.0002, 0.154, 0.0, 0.154)


def _shrink(v: dict[str, float], basis: dict[str, float], amount: float) -> None:
    """계좌에서 amount 를 비중대로 빼낸다(평단은 같은 비율로 줄임)."""
    tot = sum(v.values())
    if tot <= 0 or amount <= 0:
        return
    f = max(0.0, 1 - amount / tot)
    for a in v:
        v[a] *= f
        basis[a] *= f


def _gains_tax(acct: Account, realized: float) -> float:
    return acct.gain_rate * max(0.0, realized - acct.deduction)


def simulate(
    acct: Account,
    months: Sequence[str],
    rets: Sequence[Mapping[str, float]],
    income: Sequence[Mapping[str, float]],
    weights: Mapping[str, float],
    contrib: Sequence[float],
) -> dict[str, float]:
    """세후 최종 금액 (순수).

    rets[k][a]: k 번째 달 총수익(분배 포함, 보수 뺀 값). income[k][a]: 그 중 분배금 몫(평가액 대비).
    contrib[k]: k 번째 달 초 납입.
    """
    assets = list(weights)
    v = dict.fromkeys(assets, 0.0)
    basis = dict.fromkeys(assets, 0.0)
    realized = 0.0
    paid_in = 0.0
    taxes = 0.0
    fees = 0.0
    last = len(months) - 1
    for k, m in enumerate(months):
        c = contrib[k]
        paid_in += c
        total = sum(v.values()) + c
        if total > 0:
            tgt = {a: weights[a] * total for a in assets}
            fee = sum(abs(tgt[a] - v[a]) for a in assets) * acct.cost
            fees += fee
            scale = (total - fee) / total
            sale_gain = 0.0
            for a in assets:
                t = tgt[a] * scale
                if t < v[a]:
                    g = (v[a] - t) * (1 - basis[a] / v[a])
                    basis[a] *= t / v[a]
                    if acct.kind == "T":
                        realized += g
                    elif acct.kind == "G" and g > 0:
                        sale_gain += g
                else:
                    basis[a] += t - v[a]
                v[a] = t
            if sale_gain > 0:
                tax = acct.gain_rate * sale_gain
                taxes += tax
                _shrink(v, basis, tax)
        for a in assets:
            inc = v[a] * income[k].get(a, 0.0)
            v[a] *= 1 + rets[k][a]
            tax = inc * acct.dist_rate
            v[a] -= tax
            taxes += tax
            basis[a] += inc - tax
        if acct.kind == "T" and m.endswith("-12") and k != last:
            tax = _gains_tax(acct, realized)
            taxes += tax
            _shrink(v, basis, tax)
            realized = 0.0
    gross = sum(v.values())
    fee = gross * acct.cost
    fees += fee
    final = gross - fee
    if acct.kind == "T":
        realized += final - sum(basis.values())
        tax = _gains_tax(acct, realized)
    elif acct.kind == "G":
        tax = acct.gain_rate * sum(max(0.0, v[a] * (1 - acct.cost) - basis[a]) for a in assets)
    else:
        tax = acct.gain_rate * max(0.0, final - paid_in - acct.deduction)
    taxes += tax
    return {
        "final": final - tax,
        "paid_in": paid_in,
        "taxes": taxes,
        "fees": fees,
    }


def tracking_gap(
    kr: Mapping[str, float], us: Mapping[str, float], fx: Mapping[str, float]
) -> dict[str, Any]:
    """월말 값(국내 ETF 원화 · 미국 ETF 달러 · 원/달러) → 국내 − 미국×환율 연율 차이 (순수).

    첫 달(상장 달)은 뺀다."""
    ms = sorted(m for m in kr if m in us and m in fx)[1:]
    a, b = [], []
    for p, q in pairwise(ms):
        a.append(kr[q] / kr[p] - 1)
        b.append(us[q] / us[p] * fx[q] / fx[p] - 1)
    n = len(a)
    ga = gb = 1.0
    for x, y in zip(a, b, strict=True):
        ga *= 1 + x
        gb *= 1 + y
    gap = (ga ** (12 / n) - gb ** (12 / n)) if n else 0.0
    d = [x - y for x, y in zip(a, b, strict=True)]
    md = sum(d) / n if n else 0.0
    te = (sum((x - md) ** 2 for x in d) / n) ** 0.5 * 12**0.5 if n else 0.0
    ma, mb = (sum(a) / n, sum(b) / n) if n else (0.0, 0.0)
    sa = (sum((x - ma) ** 2 for x in a) / n) ** 0.5 if n else 0.0
    sb = (sum((y - mb) ** 2 for y in b) / n) ** 0.5 if n else 0.0
    c = (
        sum((x - ma) * (y - mb) for x, y in zip(a, b, strict=True)) / (n * sa * sb)
        if n and sa and sb
        else 0.0
    )
    return {"months": n, "gap_annual": gap, "tracking_error": te, "corr": c, "start": ms[0]}
