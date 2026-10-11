# ruff: noqa: E501 — 한국어 설명 · 표 문자열이 길다
"""문서 212 §5 · §7 — ④ 실제 체결 · 위험 · funding 파일럿 (계좌 10,000 USDT). 주문 없음 · 독립 원장.

사용: ``python -m scripts.run_resid_rev_212 pilot`` → reports/resid_rev_212/pilot.json
"""

from __future__ import annotations

import json
import math
import random
import statistics
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

from scripts.run_resid_rev_212 import DAY, M5, OUT, REPORT, VISION, Signal, load_daily, signals
from scripts.run_resid_rev_212_threshold import HOLD, Intraday, _month, _rows

WAIT_1H = 12  # 5분봉 12 개 = 1 시간
ATTEMPTS = 4


@dataclass(frozen=True, slots=True)
class P:
    cost: float = 0.00095
    limit: float = 0.003
    participation: float = 0.01
    fill: str = "open"  # open · worst
    extra: float = 0.01
    risk: float = 0.0025
    risk_cap: float = 0.01
    gross_cap: float = 1.5
    max_bundles: int = 3
    stop_atr: float = 2.0
    hedge: bool = True
    halt: float = 0.30  # None 이면 정지 없음 (진단)
    use_halt: bool = True
    use_mark: bool = True
    daily_loss: float = 0.02
    equity0: float = 10_000.0


class LruIntraday(Intraday):
    """5분봉 캐시를 최근 60 개 종목 · 달로 제한 (BTC 는 그대로 둔다)."""

    def __init__(self) -> None:
        super().__init__()
        self.k5 = OrderedDict()

    def bar(self, s: str, t: int) -> tuple[float, float, float, float, float] | None:
        """(시가, 고가, 저가, 종가, 거래대금) — 거래 없으면 None."""
        key = (s, _month(t))
        d = self._full(key)
        b = d.get(t)
        return b if b and b[4] > 0 else None

    def _full(self, key: tuple[str, str]) -> dict[int, tuple[float, float, float, float, float]]:
        if key in self.k5:
            self.k5.move_to_end(key)  # type: ignore[attr-defined]
            return self.k5[key]  # type: ignore[return-value]
        z = VISION / "um" / key[0] / "5m" / f"{key[0]}-5m-{key[1]}.zip"
        d = (
            {
                int(r[0]): (float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[7]))
                for r in _rows(z)
            }
            if z.is_file()
            else {}
        )
        self.k5[key] = d  # type: ignore[assignment]
        # BTC 달은 따로 둔다 (헤지라 계속 쓴다). 예전 판은 BTC 달이 60 개를 넘으면 여기서 끝없이 돌았다(2026-10-11 고침).
        alts = [k for k in self.k5 if k[0] != "BTCUSDT"]
        for k in alts[: max(0, len(alts) - 60)]:
            del self.k5[k]
        return d

    def close_before(self, s: str, t: int) -> float | None:
        x = t - M5
        for _ in range(WAIT_1H * 24):
            b = self._full((s, _month(x))).get(x)
            if b:
                return b[3]
            x -= M5
        return None


@dataclass
class Leg:
    symbol: str
    side: int  # +1 롱 · −1 숏
    target: float = 0.0  # 목표 수량(양수)
    qty: float = 0.0  # 보유 수량(양수)
    avg: float = 0.0
    stop: float = 0.0
    first_fill: int = -1
    pending_exit: int = 0  # 정리 시도 남은 횟수
    exit_deadline: int = 0  # 이 시각 지나면 비상 정리
    fev: list[tuple[int, float]] = field(
        default_factory=list
    )  # 이 묶음 기간 funding 정산 (시각, rate)
    fptr: int = 0


@dataclass
class Bundle:
    sig: Signal
    start: int  # 진입일 00:00
    coin: Leg
    btc: Leg
    risk_amt: float
    cash: float = 0.0  # 이 묶음 현금 흐름 합 (손익)
    fees: float = 0.0
    funding: float = 0.0
    extra: float = 0.0
    state: str = "entering"  # entering · open · exiting · done
    reason: str = ""
    exit_due: int = 0
    hedge_deadline: int = 0
    coin_attempts: int = 0
    entry_equity: float = 0.0
    price_coin: float = 0.0
    price_btc: float = 0.0


def lot(exinfo: dict[str, Any], s: str) -> tuple[float, float, float]:
    """(stepSize, minQty, 최소 명목) — 지금 거래소 규격(2026-10-11 받음). 폐지 종목은 (0, 0, 5).

    당시 규격은 공개 자료로 복원할 수 없어 지금 값을 쓴다(문서 212 §11 한계).
    """
    info = exinfo.get(s)
    if not info:
        return 0.0, 0.0, 5.0
    step, minq, mn = 0.0, 0.0, 5.0
    for f in info.get("filters", []):
        if f.get("filterType") == "LOT_SIZE":
            step = float(f.get("stepSize", 0) or 0)
            minq = float(f.get("minQty", 0) or 0)
        if f.get("filterType") == "MIN_NOTIONAL":
            mn = float(f.get("notional", 5) or 5)
    return step, minq, mn


def floor_step(q: float, step: float) -> float:
    return math.floor(q / step + 1e-9) * step if step > 0 else q


class Sim:
    def __init__(self, p: P, sigs: list[Signal], it: LruIntraday, exinfo: dict[str, Any]) -> None:
        self.p, self.it, self.ex = p, it, exinfo
        self.cash = p.equity0
        self.bundles: list[Bundle] = []
        self.done: list[Bundle] = []
        self.skips: dict[str, int] = defaultdict(int)
        self.daily: list[
            tuple[str, float, float, float]
        ] = []  # 날, equity, 총명목/equity, 잔여 시장 노출/equity
        self.peak = p.equity0
        self.halted: str | None = None
        self.fund_ptr: dict[str, int] = {}
        self.proxy = 0
        self.by_day: dict[str, list[Signal]] = defaultdict(list)
        for s in sigs:
            d = (date.fromisoformat(s.day) + timedelta(days=1)).isoformat()
            self.by_day[d].append(s)
        for v in self.by_day.values():
            v.sort(key=lambda x: (x.z, -x.qv_prev, x.symbol))

    # ---------------- 가격 · 평가

    def px(self, s: str, t: int) -> float | None:
        """t 이전에 끝난 마지막 5분봉 종가."""
        return self.it.close_before(s, t)

    def equity(self, t: int) -> float:
        eq = self.cash
        for b in self.bundles:
            for lg in (b.coin, b.btc):
                if lg.qty > 0:
                    pr = self.px(lg.symbol, t) or lg.avg  # t 이전 끝난 봉
                    eq += lg.side * lg.qty * pr
        return eq

    # ---------------- 체결

    def _fill(self, b: Bundle, lg: Leg, q: float, price: float, extra: float = 0.0) -> None:
        """q > 0: 보유 늘림, q < 0: 줄임 (lg.side 방향 기준)."""
        signed = lg.side * q  # 계좌 수량 변화
        notional = abs(q) * price
        fee = notional * self.p.cost
        ext = notional * extra
        self.cash -= signed * price + fee + ext
        b.cash -= signed * price + fee + ext
        b.fees += fee
        b.extra += ext
        if q > 0:
            lg.avg = (lg.avg * lg.qty + price * q) / (lg.qty + q)
        lg.qty = max(0.0, lg.qty + q)
        if lg.qty < 1e-12:
            lg.qty = 0.0

    def try_ioc(self, b: Bundle, lg: Leg, t: int, want: float, ref: float) -> float:
        """한 봉 IOC 시도. want > 0 늘림 · < 0 줄임. 체결 수량(부호 포함) 반환."""
        bar = self.it.bar(lg.symbol, t)
        if bar is None or want == 0:
            return 0.0
        o, h, lo, _c, qv = bar
        buy = (lg.side * want) > 0  # 계좌 기준 매수인가
        lim = ref * (1 + self.p.limit) if buy else ref * (1 - self.p.limit)
        if (buy and o > lim) or (not buy and o < lim):
            return 0.0
        price = o if self.p.fill == "open" else (min(h, lim) if buy else max(lo, lim))
        cap = self.p.participation * qv / price
        q = max(-cap, min(cap, want))
        if want < 0:
            q = max(want, -cap, -lg.qty)
        step, minq, mn = lot(self.ex, lg.symbol)
        q = math.copysign(floor_step(abs(q), step), q)
        if q == 0:
            return 0.0
        if want > 0 and (abs(q) < minq or abs(q) * price < mn):
            return 0.0  # 늘리는 주문은 minQty · 최소 명목을 지켜야 한다 (212 감사 A1 고침)
        if want < 0 and abs(q) < minq and abs(q) < lg.qty:
            return 0.0  # 줄이는 주문: 전량 청산이 아니면 minQty 이상
        self._fill(b, lg, q, price)
        return q

    def emergency(self, b: Bundle, lg: Leg, t: int, price: float | None = None) -> None:
        """비상 정리: 그 봉 시가(또는 보호가와 시가 중 나쁜 쪽)에 전량 + 추가 불리폭."""
        if lg.qty <= 0:
            return
        bar = self.it.bar(lg.symbol, t)
        o = bar[0] if bar else (self.px(lg.symbol, t) or lg.avg)
        pr = o if price is None else (min(price, o) if lg.side > 0 else max(price, o))
        self._fill(b, lg, -lg.qty, pr, self.p.extra)

    # ---------------- funding

    def funding_until(self, t: int) -> None:
        """t 이하 정산을 그 직전 보유 수량으로. BTC 는 묶음별 수량 × 같은 rate 라 합산 포지션 정산과 같다(묶음 수량 비율 배분)."""
        for b in self.bundles:
            for lg in (b.coin, b.btc):
                while lg.fptr < len(lg.fev) and lg.fev[lg.fptr][0] <= t:
                    ft, rate = lg.fev[lg.fptr]
                    lg.fptr += 1
                    if lg.qty <= 0 or lg.first_fill < 0 or ft <= lg.first_fill:
                        continue
                    mk = self.it.mark_at(lg.symbol, ft) if self.p.use_mark else None
                    if mk is None:
                        mk = self.px(lg.symbol, ft) or lg.avg
                        self.proxy += int(self.p.use_mark)
                    amt = lg.side * lg.qty * mk * rate  # 롱: + rate 면 냄 · 숏: 받음
                    self.cash -= amt
                    b.cash -= amt
                    b.funding += amt

    # ---------------- 진입

    def open_new(self, t: int, day: str, day_equity: float) -> None:
        for sg in self.by_day.get(day, []):
            if self.halted:
                self.skips["계좌 30% 정지"] += 1
                continue
            if any(b.sig.symbol == sg.symbol for b in self.bundles):
                self.skips["같은 코인 보유 중"] += 1
                continue
            if len(self.bundles) >= self.p.max_bundles:
                self.skips["동시 3 묶음 꽉 참"] += 1
                continue
            eq = self.equity(t)
            if eq < day_equity * (1 - self.p.daily_loss):
                self.skips["일 손실 2%"] += 1
                continue
            _evs, miss_c = self.it.funding_between(sg.symbol, t, t + HOLD + 2 * 3_600_000)
            _evs, miss_b = self.it.funding_between("BTCUSDT", t, t + HOLD + 2 * 3_600_000)
            if miss_c or miss_b:
                self.skips["자료 결측(funding)"] += 1
                continue
            pc, pb = self.px(sg.symbol, t), self.px("BTCUSDT", t)
            if not pc or not pb:
                self.skips["자료 결측(가격)"] += 1
                continue
            unit_risk = (
                self.p.stop_atr * sg.atr_coin / pc + sg.beta * self.p.stop_atr * sg.atr_btc / pb
            )
            n = self.p.risk * eq / unit_risk  # 헤지 없는 진단도 같은 롱 수량
            open_risk = sum(b.risk_amt for b in self.bundles)
            gross = sum(
                lg.qty * (self.px(lg.symbol, t) or lg.avg)
                for b in self.bundles
                for lg in (b.coin, b.btc)
            )
            if open_risk + self.p.risk * eq > self.p.risk_cap * eq + 1e-9:
                self.skips["합산 위험 1%"] += 1
                continue
            if gross + n * (1 + (sg.beta if self.p.hedge else 0)) > self.p.gross_cap * eq:
                self.skips["총명목 1.5 배"] += 1
                continue
            # 진입 전 주문 수량 검사 (212 감사 A1): 단위로 내린 코인 · 헤지 수량이 규격을 지키고
            # 헤지가 목표의 90% 이상인지 롱을 넣기 전에 본다. 예전 판은 이 검사 없이 들어가 헤지 실패로 정리됐다.
            step_c, minq_c, mn_c = lot(self.ex, sg.symbol)
            qc = floor_step(n / pc, step_c)
            if qc <= 0 or qc < minq_c or qc * pc < mn_c:
                self.skips["코인 수량 불가(사전 검사)"] += 1
                continue
            if self.p.hedge:
                step_b, minq_b, mn_b = lot(self.ex, "BTCUSDT")
                want_b = sg.beta * qc * pc
                qh = floor_step(want_b / pb, step_b)
                if qh < minq_b or qh * pb < mn_b or qh * pb < 0.9 * want_b:
                    self.skips["헤지 수량 불가(사전 검사)"] += 1
                    continue
            n = qc * pc
            span = (t - DAY, t + HOLD + 6 * 3_600_000)
            coin = Leg(
                sg.symbol, +1, target=n / pc, fev=self.it.funding_between(sg.symbol, *span)[0]
            )
            btc = Leg("BTCUSDT", -1, fev=self.it.funding_between("BTCUSDT", *span)[0])
            b = Bundle(
                sg, t, coin, btc, self.p.risk * eq, entry_equity=eq, price_coin=pc, price_btc=pb
            )
            b.exit_due = t + HOLD + 10 * DAY  # 첫 체결 뒤 다시 정함
            self.bundles.append(b)

    # ---------------- 한 봉

    def step(self, t: int) -> None:
        for b in list(self.bundles):
            c, h = b.coin, b.btc
            if b.state == "entering":
                self.enter_step(b, t)
                if b not in self.bundles:
                    continue
            if b.state in ("entering", "open"):
                # 보호 — 각 다리가 처음 체결된 봉의 다음 봉부터
                bc = (
                    self.it.bar(c.symbol, t)
                    if c.qty > 0 and c.stop > 0 and t > c.first_fill
                    else None
                )
                bh = (
                    self.it.bar("BTCUSDT", t)
                    if h.qty > 0 and h.stop > 0 and t > h.first_fill
                    else None
                )
                hit_c = bc is not None and bc[2] <= c.stop
                hit_h = bh is not None and bh[1] >= h.stop
                if hit_c or hit_h:
                    if hit_c:
                        self.emergency(b, c, t, c.stop)
                    if hit_h:
                        self.emergency(b, h, t, h.stop)
                    b.reason = "보호 청산"
                    self.start_exit(b, t + M5)
                elif b.state == "open" and t >= b.exit_due:
                    b.reason = "72 시간"
                    self.start_exit(b, t)
            if b.state == "exiting":
                self.exit_step(b, t)

    def enter_step(self, b: Bundle, t: int) -> None:
        c, h = b.coin, b.btc
        if b.coin_attempts < ATTEMPTS and c.qty < c.target:
            ref = self.px(c.symbol, t) or b.price_coin
            q = self.try_ioc(b, c, t, c.target - c.qty, ref)
            b.coin_attempts += 1
            if q > 0 and c.first_fill < 0:
                c.first_fill = t
                b.exit_due = t + HOLD
                b.hedge_deadline = t + WAIT_1H * M5
        coin_done = b.coin_attempts >= ATTEMPTS or c.qty >= c.target
        if coin_done and c.qty == 0:
            self.skips["미체결(코인)"] += 1
            self.bundles.remove(b)
            return
        if c.qty > 0:
            c.stop = (
                c.avg - self.p.stop_atr * b.sig.atr_coin
            )  # 채워지는 동안은 평균가 기준으로 갱신
        if not self.p.hedge:
            if coin_done:
                b.state = "open"
            return
        if c.first_fill >= 0 and t > c.first_fill:
            pb = self.px("BTCUSDT", t) or b.price_btc
            h.target = b.sig.beta * c.qty * c.avg / pb
            if h.qty < h.target * 0.999:
                q = self.try_ioc(b, h, t, h.target - h.qty, pb)
                if q > 0 and h.first_fill < 0:
                    h.first_fill = t
            if h.qty > 0:
                h.stop = h.avg + self.p.stop_atr * b.sig.atr_btc
            if coin_done and h.qty >= 0.9 * h.target:
                b.state = "open"
            elif t >= b.hedge_deadline:
                b.reason = "헤지 실패"
                self.start_exit(b, t)

    def start_exit(self, b: Bundle, t: int) -> None:
        b.state = "exiting"
        for lg in (b.coin, b.btc):
            lg.pending_exit = ATTEMPTS
            lg.exit_deadline = t + WAIT_1H * M5
        b.exit_due = t

    def exit_step(self, b: Bundle, t: int) -> None:
        for lg in (b.coin, b.btc):
            if lg.qty <= 0:
                continue
            if t >= lg.exit_deadline:
                self.emergency(b, lg, t)
                continue
            if lg.pending_exit > 0 and t >= b.exit_due:
                ref = self.px(lg.symbol, t - M5) or lg.avg
                self.try_ioc(b, lg, t, -lg.qty, ref)
                lg.pending_exit -= 1
        if b.coin.qty <= 0 and b.btc.qty <= 0:
            b.state = "done"
            self.bundles.remove(b)
            self.done.append(b)

    # ---------------- 전체

    def run(self, d0: date, d1: date) -> None:
        d = d0
        while d <= d1:
            t_day = int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp() * 1000)
            self.funding_until(t_day)
            eq = self.equity(t_day)
            gross = sum(
                lg.qty * (self.px(lg.symbol, t_day) or lg.avg)
                for b in self.bundles
                for lg in (b.coin, b.btc)
            )
            resid = sum(
                b.sig.beta * b.coin.qty * (self.px(b.coin.symbol, t_day) or b.coin.avg)
                - b.btc.qty * (self.px("BTCUSDT", t_day) or b.btc.avg)
                for b in self.bundles
            )
            self.daily.append(
                (
                    (d - timedelta(days=1)).isoformat(),
                    eq,
                    gross / eq if eq > 0 else 0.0,
                    resid / eq if eq > 0 else 0.0,
                )
            )
            self.peak = max(self.peak, eq)
            if self.p.use_halt and self.halted is None and eq <= self.peak * (1 - self.p.halt):
                self.halted = d.isoformat()
            self.open_new(t_day, d.isoformat(), eq)
            t = t_day
            end = t_day + DAY
            while t < end:
                if self.bundles:
                    self.funding_until(t)
                    self.step(t)
                    t += M5
                else:
                    break
            if d.day == 1 and d.month == 1:
                print(f"  … {d.isoformat()} 진행 (묶음 끝 {len(self.done)})", flush=True)
            d += timedelta(days=1)
        t_end = int(datetime(d1.year, d1.month, d1.day, tzinfo=UTC).timestamp() * 1000) + DAY
        for b in list(self.bundles):  # 자료 끝: 남은 묶음 정리
            b.reason = "자료 끝"
            for lg in (b.coin, b.btc):
                self.emergency(b, lg, t_end - M5)
            self.bundles.remove(b)
            self.done.append(b)
        self.daily.append((d1.isoformat(), self.equity(t_end), 0.0, 0.0))


# ------------------------------------------------------------------ 통계


def stats(sim: Sim) -> dict[str, Any]:
    d = sim.daily
    eq = [x[1] for x in d]
    r = [eq[i] / eq[i - 1] - 1 for i in range(1, len(eq))]
    days = [x[0] for x in d[1:]]
    years = len(r) / 365
    sd = statistics.stdev(r) if len(r) > 1 else 0.0
    peak, mdd = eq[0], 0.0
    for x in eq:
        peak = max(peak, x)
        mdd = max(mdd, 1 - x / peak)
    trades = [b for b in sim.done if b.coin.first_fill >= 0]
    pnl = [b.cash for b in trades]
    wins = [x for x in pnl if x > 0]
    losses = [x for x in pnl if x <= 0]
    streak = best = 0
    for x in pnl:
        streak = streak + 1 if x <= 0 else 0
        best = max(best, streak)
    by_year: dict[str, float] = defaultdict(float)
    by_sym: dict[str, float] = defaultdict(float)
    for b in trades:
        by_year[b.sig.day[:4]] += b.cash
        by_sym[b.sig.symbol] += b.cash
    net = eq[-1] - eq[0]
    return {
        "final": round(eq[-1], 2),
        "net": round(net, 2),
        "cagr_pct": round(((eq[-1] / eq[0]) ** (1 / years) - 1) * 100, 3) if years > 0 else 0.0,
        "sharpe": round(statistics.fmean(r) / sd * math.sqrt(365), 3) if sd > 0 else 0.0,
        "mdd_pct": round(mdd * 100, 2),
        "trades": len(trades),
        "win_pct": round(len(wins) / len(pnl) * 100, 1) if pnl else 0.0,
        "avg_win": round(statistics.fmean(wins), 2) if wins else 0.0,
        "avg_loss": round(statistics.fmean(losses), 2) if losses else 0.0,
        "max_losing_streak": best,
        "fees": round(sum(b.fees for b in trades), 2),
        "funding": round(sum(b.funding for b in trades), 2),
        "extra": round(sum(b.extra for b in trades), 2),
        "gross_before_costs": round(net + sum(b.fees + b.funding + b.extra for b in trades), 2),
        "exit_reasons": dict(
            sorted(
                {
                    k: sum(1 for b in trades if b.reason == k) for k in {b.reason for b in trades}
                }.items()
            )
        ),
        "skips": dict(sim.skips),
        "halted": sim.halted,
        "avg_gross_exposure_pct": round(statistics.fmean(x[2] for x in d) * 100, 2),
        "max_gross_exposure_pct": round(max(x[2] for x in d) * 100, 2),
        "avg_resid_market_exposure_pct": round(statistics.fmean(x[3] for x in d) * 100, 3),
        "by_year": {k: round(v, 2) for k, v in sorted(by_year.items())},
        "top_symbols": sorted(
            ((k, round(v, 2)) for k, v in by_sym.items()), key=lambda x: -abs(x[1])
        )[:8],
        "top3_trades_share_pct": round(sum(sorted(pnl, reverse=True)[:3]) / net * 100, 1)
        if net > 0
        else None,
        "mark_proxy": sim.proxy,
        "_r": list(zip(days, r, strict=True)),
    }


def block_p(
    r: list[tuple[str, float]], reps: int = 2000, block: int = 7, seed: int = 212
) -> dict[str, Any]:
    vals = [x for _, x in r]
    n = len(vals)
    rng = random.Random(seed)
    means = []
    for _ in range(reps):
        idx: list[int] = []
        while len(idx) < n:
            st = rng.randrange(n)
            idx.extend((st + k) % n for k in range(block))
        means.append(statistics.fmean(vals[i] for i in idx[:n]))
    means.sort()
    half = [x for d, x in r if d <= "2022-12-31"], [x for d, x in r if d > "2022-12-31"]
    return {
        "mean_daily_bp": round(statistics.fmean(vals) * 1e4, 4),
        "ci90_daily_bp": [
            round(means[int(0.05 * reps)] * 1e4, 4),
            round(means[int(0.95 * reps)] * 1e4, 4),
        ],
        "p_mean_gt0": round(sum(1 for m in means if m > 0) / reps, 3),
        "halves_mean_bp": [round(statistics.fmean(h) * 1e4, 4) for h in half],
    }


def cmd_pilot() -> int:
    members = json.loads((OUT / "members.json").read_text(encoding="utf-8"))
    exinfo = json.loads((OUT / "exchange_info.json").read_text(encoding="utf-8"))
    daily = load_daily()
    sigs = signals(daily, members)
    runs = {
        "main": P(),
        "no_halt_diag": P(use_halt=False),
        "no_hedge_diag": P(hedge=False),
        "cost_x2": P(cost=0.0019),
        "fill_worst": P(fill="worst"),
        "participation_0.1pct": P(participation=0.001),
        "funding_price_proxy": P(use_mark=False),
        "cost_realistic_ref": P(cost=0.00023),
    }
    out: dict[str, Any] = {"made": datetime.now(UTC).isoformat(), "signals": len(sigs)}
    res: dict[str, dict[str, Any]] = {}
    for name, p in runs.items():
        sim = Sim(p, sigs, LruIntraday(), exinfo)
        sim.run(date(2020, 3, 1), date(2026, 8, 31))
        st = stats(sim)
        st["bootstrap"] = block_p(st["_r"])
        res[name] = st
        print(
            name,
            {
                k: st[k]
                for k in (
                    "net",
                    "cagr_pct",
                    "sharpe",
                    "mdd_pct",
                    "trades",
                    "win_pct",
                    "fees",
                    "funding",
                    "extra",
                    "halted",
                )
            },
            st["bootstrap"],
            flush=True,
        )
    m = res["main"]["bootstrap"]
    sens = [
        res[k]["bootstrap"]["mean_daily_bp"] > 0
        for k in ("main", "cost_x2", "fill_worst", "participation_0.1pct", "funding_price_proxy")
    ]
    same = all(sens) or not any(sens)
    h0, h1 = m["halves_mean_bp"]
    if not same:
        word = "판단 불가"
    elif m["p_mean_gt0"] >= 0.9 and h0 > 0 and h1 > 0:
        word = "유리"
    elif m["mean_daily_bp"] < 0 and (m["p_mean_gt0"] <= 0.1 or (h0 < 0 and h1 < 0)):
        word = "불리"
    else:
        word = "판단 불가"
    out["pilot"] = word
    out["sens_mean_daily_bp"] = {k: res[k]["bootstrap"]["mean_daily_bp"] for k in runs}
    out["runs"] = {k: {kk: vv for kk, vv in v.items() if kk != "_r"} for k, v in res.items()}
    REPORT.mkdir(parents=True, exist_ok=True)
    (REPORT / "pilot.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
    )
    print("결론", word, out["sens_mean_daily_bp"])
    return 0


__all__ = ["P", "Sim", "cmd_pilot", "field", "replace"]
