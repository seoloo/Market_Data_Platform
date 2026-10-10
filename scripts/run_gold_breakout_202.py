# ruff: noqa: E501 — 한국어 설명 · 표 문자열이 길다
"""문서 202 — 금 장중 돌파 (B 꼬리 돌파 · C 15분 종가 확인) 사전 등록 실행기. 주문 없음.

사용::

    python -m scripts.run_gold_breakout_202 power   # TRAIN 범위 폭만 재서 검정력 (수익 계산 없음)
    python -m scripts.run_gold_breakout_202 run     # 판정 1 회 → reports/gold_breakout_202/result.json

규칙은 문서 202 §2 에 고정돼 있다. 바꾸려면 새 문서로 다시 사전 등록한다.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import zipfile
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts.fetch_dukascopy import OUT_ROOT, decode

NY = ZoneInfo("America/New_York")
REPORT = Path(__file__).resolve().parent.parent / "reports" / "gold_breakout_202"

Bar = tuple[int, float, float, float, float, float]
"""(open_ms UTC, open, high, low, close, volume)."""


@dataclass(frozen=True, slots=True)
class Rules:
    """문서 202 §2 — 결과를 보기 전에 고정."""

    range_start: time = time(19, 0)  # 전날 뉴욕 19:00
    range_end: time = time(7, 0)  # 당일 뉴욕 07:00 (이 시각 직전 봉까지)
    entry_end: time = time(11, 0)  # 진입은 07:00 ~ 11:00
    exit_time: time = time(15, 0)  # 남은 포지션은 15:00 봉 시가에 정리
    min_width_bp: float = 30.0  # 범위 폭이 이보다 좁으면 그날 쉰다
    buffer_bp: float = 2.0  # 범위 바깥 2bp 를 넘어야 돌파
    cap_bp: float = 5.0  # 지정가 상한 (시장가 금지 — 이보다 나쁘면 안 산다)
    stop_w: float = 0.5  # 손절 = 진입가 ∓ 0.5 × 범위 폭
    target_w: float = 1.0  # 익절 = 진입가 ± 1.0 × 범위 폭
    tp_through_bp: float = 1.0  # 익절 지정가는 1bp 더 지나가야 체결로 본다
    confirm_min: int = 15  # C: 15분봉 종가로 확인
    min_coverage: float = 0.8  # 범위 시간 중 거래 있는 분이 80% 미만이면 그날 뺀다


@dataclass(frozen=True, slots=True)
class Costs:
    """편도. 문서 202 §3 — XAUUSDT 실측(2026-10-11): 지정가 0% · 즉시 체결 0.04% + 미끄러짐 0.01%."""

    entry: float = 0.0005
    stop: float = 0.0005
    time_exit: float = 0.0005
    target: float = 0.0
    funding_long: float = 0.00012  # 16:00 UTC 정산을 넘기는 롱 (상장 뒤 롱 비용 연 13.2% / 1095)


VERDICT = Costs()
OLD_BASELINE = Costs(0.00095, 0.00095, 0.00095, 0.00095, 0.00012)  # 기존 선물 판정 비용 (참고)
DOUBLE = Costs(0.001, 0.001, 0.001, 0.0, 0.00024)  # 비용 2배 (V4)


@dataclass(frozen=True, slots=True)
class Trade:
    day: str
    variant: str
    side: int  # +1 롱 · −1 숏
    entry_ms: int
    entry: float
    exit_ms: int
    exit: float
    reason: str  # target · stop · time
    width_bp: float
    crossed_funding: bool

    def gross(self) -> float:
        return self.side * (self.exit / self.entry - 1)

    def net(self, c: Costs) -> float:
        fee = c.entry + {"target": c.target, "stop": c.stop, "time": c.time_exit}[self.reason]
        fund = c.funding_long if (self.side > 0 and self.crossed_funding) else 0.0
        return self.gross() - fee - fund


# ------------------------------------------------------------------ 자료


HISTDATA = Path(__file__).resolve().parent.parent / "data" / "external" / "histdata"


def load_bars(
    symbol: str = "XAUUSD", start: date | None = None, end: date | None = None
) -> list[Bar]:
    """HistData 1분봉 (판정 자료, §1). 시각은 서머타임 없는 뉴욕 표준시 → UTC 는 +5 시간.

    틱이 있던 분만 들어 있다(거래량 칸은 늘 0 이라 1 로 둔다).
    """
    out: list[Bar] = []
    lo_ms = int(datetime.combine(start, time(0), UTC).timestamp() * 1000) if start else 0
    hi_ms = (
        int(datetime.combine(end + timedelta(days=1), time(0), UTC).timestamp() * 1000)
        if end
        else 1 << 62
    )
    for z in sorted((HISTDATA / symbol).glob("*.zip")):
        with zipfile.ZipFile(z) as zf:
            name = next(n for n in zf.namelist() if n.endswith(".csv"))
            for line in zf.open(name).read().decode("ascii").splitlines():
                t, o, h, low, c, _v = line.split(";")
                ms = (
                    int(
                        datetime.strptime(t, "%Y%m%d %H%M%S").replace(tzinfo=UTC).timestamp() * 1000
                    )
                    + _EST_TO_UTC
                )
                if lo_ms <= ms < hi_ms:
                    out.append((ms, float(o), float(h), float(low), float(c), 1.0))
    out.sort(key=lambda b: b[0])
    return out


_EST_TO_UTC = 5 * 3_600_000


def load_dukascopy(symbol: str = "XAUUSD") -> list[Bar]:
    """대조용 Dukascopy 일부 (거래량 0 = 장이 닫힌 채움 봉은 뺀다)."""
    out: list[Bar] = []
    for p in sorted((OUT_ROOT / symbol / "BID").glob("*/*.bi5")):
        d = date(int(p.parent.name), int(p.stem[:2]), int(p.stem[3:]))
        out.extend(b for b in decode(p.read_bytes(), d, 1000.0) if b[5] > 0)
    out.sort(key=lambda b: b[0])
    return out


def _ny_ms(d: date, t: time) -> int:
    return int(datetime.combine(d, t, NY).astimezone(UTC).timestamp() * 1000)


def sessions(bars: Sequence[Bar], rules: Rules) -> Iterable[tuple[date, list[Bar], list[Bar]]]:
    """뉴욕 평일마다 (날, 범위 봉, 07:00~15:00 봉+정리 봉). 봉은 시각순."""
    if not bars:
        return
    first = datetime.fromtimestamp(bars[0][0] / 1000, NY).date()
    last = datetime.fromtimestamp(bars[-1][0] / 1000, NY).date()
    import bisect

    keys = [b[0] for b in bars]
    d = first
    while d <= last:
        if d.weekday() < 5:
            r0 = _ny_ms(d - timedelta(days=1), rules.range_start)
            r1 = _ny_ms(d, rules.range_end)
            t1 = _ny_ms(d, rules.exit_time)
            i0, i1 = bisect.bisect_left(keys, r0), bisect.bisect_left(keys, r1)
            i2 = bisect.bisect_right(keys, t1)
            yield d, list(bars[i0:i1]), list(bars[i1:i2])
        d += timedelta(days=1)


# ------------------------------------------------------------------ 규칙 (순수)


def _crossed_funding(entry_ms: int, exit_ms: int) -> bool:
    """16:00 UTC 정산(XAUUSDT 8시간 정산 중 이 시간대에 걸리는 것)을 넘겼나."""
    day = datetime.fromtimestamp(entry_ms / 1000, UTC).date()
    for k in (0, 1):
        s = int(datetime.combine(day + timedelta(days=k), time(16, 0), UTC).timestamp() * 1000)
        if entry_ms < s <= exit_ms:
            return True
    return False


def _manage(
    day: date,
    variant: str,
    side: int,
    entry_ms: int,
    entry: float,
    width: float,
    rest: Sequence[Bar],
    rules: Rules,
    exit_ms: int,
    width_bp: float,
) -> Trade:
    """진입 다음 봉부터 손절 · 익절 · 시간 정리. 같은 봉에 둘 다 닿으면 손절 먼저."""
    stop = entry - side * rules.stop_w * width
    target = entry + side * rules.target_w * width
    through = target * (1 + side * rules.tp_through_bp / 1e4)
    for ms, o, h, lo, _c, _v in rest:
        if ms >= exit_ms:
            return Trade(
                day.isoformat(),
                variant,
                side,
                entry_ms,
                entry,
                ms,
                o,
                "time",
                width_bp,
                _crossed_funding(entry_ms, ms),
            )
        hit_stop = lo <= stop if side > 0 else h >= stop
        if hit_stop:
            px = min(stop, o) if side > 0 else max(stop, o)  # 갭이면 시가 (손절가 보장 없음)
            return Trade(
                day.isoformat(),
                variant,
                side,
                entry_ms,
                entry,
                ms,
                px,
                "stop",
                width_bp,
                _crossed_funding(entry_ms, ms),
            )
        hit_target = h >= through if side > 0 else lo <= through
        if hit_target:
            return Trade(
                day.isoformat(),
                variant,
                side,
                entry_ms,
                entry,
                ms,
                target,
                "target",
                width_bp,
                _crossed_funding(entry_ms, ms),
            )
    last = rest[-1] if rest else None
    if last is None:
        return Trade(
            day.isoformat(),
            variant,
            side,
            entry_ms,
            entry,
            entry_ms,
            entry,
            "time",
            width_bp,
            False,
        )
    return Trade(
        day.isoformat(),
        variant,
        side,
        entry_ms,
        entry,
        last[0],
        last[4],
        "time",
        width_bp,
        _crossed_funding(entry_ms, last[0]),
    )


def simulate_day(
    day: date, rng: Sequence[Bar], sess: Sequence[Bar], rules: Rules, variant: str
) -> Trade | None:
    """하루 최대 한 번 (먼저 일어난 쪽). variant = 'B' 꼬리 돌파 · 'C' 15분 종가 확인."""
    if not rng or not sess:
        return None
    span_min = (
        _ny_ms(day, rules.range_end) - _ny_ms(day - timedelta(days=1), rules.range_start)
    ) / 60_000
    if len(rng) < rules.min_coverage * span_min:
        return None
    hi, lo = max(b[2] for b in rng), min(b[3] for b in rng)
    width = hi - lo
    width_bp = (hi / lo - 1) * 1e4
    if width_bp < rules.min_width_bp:
        return None
    up = hi * (1 + rules.buffer_bp / 1e4)
    dn = lo * (1 - rules.buffer_bp / 1e4)
    entry_end = _ny_ms(day, rules.entry_end)
    exit_ms = _ny_ms(day, rules.exit_time)
    if variant == "B":
        for i, (ms, o, h, low, _c, _v) in enumerate(sess):
            if ms >= entry_end:
                return None
            for side, trig, touched in ((1, up, h >= up), (-1, dn, low <= dn)):
                if not touched:
                    continue
                px = max(trig, o) if side > 0 else min(trig, o)
                if abs(px / trig - 1) * 1e4 > rules.cap_bp:
                    return None  # 갭으로 상한 밖 — 그날은 쉰다 (지정가 미체결)
                if h >= up and low <= dn:
                    return None  # 한 봉에 양쪽 — 순서를 모르니 쉰다
                return _manage(
                    day, "B", side, ms, px, width, sess[i + 1 :], rules, exit_ms, width_bp
                )
        return None
    # C: 뉴욕 시각 15분 칸이 끝날 때 종가가 범위 밖이면 다음 봉 시가에 (상한 안에서) 진입
    bucket: list[Bar] = []
    for i, bar in enumerate(sess):
        ms = bar[0]
        if ms >= entry_end:
            return None
        bucket.append(bar)
        t = datetime.fromtimestamp(ms / 1000, NY)
        if (t.minute + 1) % rules.confirm_min:
            continue
        close = bucket[-1][4]
        bucket = []
        side = 1 if close > up else (-1 if close < dn else 0)
        if side == 0 or i + 1 >= len(sess):
            continue
        nxt = sess[i + 1]
        if nxt[0] >= entry_end:
            return None
        px = nxt[1]
        if (px / close - 1) * 1e4 * side > rules.cap_bp:
            return None
        return _manage(day, "C", side, nxt[0], px, width, sess[i + 2 :], rules, exit_ms, width_bp)
    return None


# ------------------------------------------------------------------ 통계


def summary(trades: Sequence[Trade], c: Costs) -> dict[str, float | int]:
    r = [t.net(c) * 1e4 for t in trades]
    if len(r) < 2:
        return {"n": len(r)}
    m, sd = statistics.fmean(r), statistics.stdev(r)
    se = sd / math.sqrt(len(r))
    return {
        "n": len(r),
        "mean_bp": round(m, 2),
        "sd_bp": round(sd, 2),
        "se_bp": round(se, 2),
        "lower_224": round(m - 2.24 * se, 2),
        "win": round(sum(1 for x in r if x > 0) / len(r) * 100, 1),
        "gross_bp": round(statistics.fmean(t.gross() * 1e4 for t in trades), 2),
        "target": sum(t.reason == "target" for t in trades),
        "stop": sum(t.reason == "stop" for t in trades),
        "time": sum(t.reason == "time" for t in trades),
        "long": sum(t.side > 0 for t in trades),
    }


SPLITS = {
    "TRAIN": (date(2015, 1, 1), date(2021, 12, 31)),
    "VALIDATION": (date(2022, 1, 1), date(2023, 12, 31)),
    "OOS": (date(2024, 1, 1), date(2026, 9, 30)),
}


def _in(t: Trade, a: date, b: date) -> bool:
    return a.isoformat() <= t.day <= b.isoformat()


def verdict(trades: Sequence[Trade]) -> dict[str, object]:
    by = {k: [t for t in trades if _in(t, a, b)] for k, (a, b) in SPLITS.items()}
    s = {k: summary(v, VERDICT) for k, v in by.items()}
    v1 = s["TRAIN"].get("lower_224", -1) > 0
    v2 = s["VALIDATION"].get("mean_bp", -1) > 0
    v3 = s["OOS"].get("lower_224", -1) > 0
    v4 = summary(by["OOS"], DOUBLE).get("mean_bp", -1) > 0
    return {
        "V1_train": v1,
        "V2_validation": v2,
        "V3_oos": v3,
        "V4_oos_double_cost": v4,
        "verdict": "PASS" if (v1 and v2 and v3 and v4) else "REJECT",
        "splits": s,
        "oos_double_cost": summary(by["OOS"], DOUBLE),
        "old_baseline_cost": {k: summary(v, OLD_BASELINE) for k, v in by.items()},
    }


# ------------------------------------------------------------------ 명령


def cmd_power() -> int:
    """TRAIN 범위 폭(시장 통계)만 재서 검정력. 신호 · 수익은 계산하지 않는다."""
    from auto_trading.backtest.power import detectable_effect_bps, required_samples

    rules = Rules()
    a, b = SPLITS["TRAIN"]
    bars = load_bars(start=a - timedelta(days=2), end=b)
    widths, days = [], 0
    for d, rng, _s in sessions(bars, rules):
        if not (a <= d <= b) or not rng:
            continue
        span = (
            _ny_ms(d, rules.range_end) - _ny_ms(d - timedelta(days=1), rules.range_start)
        ) / 60_000
        if len(rng) < rules.min_coverage * span:
            continue
        days += 1
        hi, lo = max(x[2] for x in rng), min(x[3] for x in rng)
        widths.append((hi / lo - 1) * 1e4)
    widths.sort()
    med = statistics.median(widths)
    usable = sum(1 for w in widths if w >= rules.min_width_bp)
    print(
        f"TRAIN 자료 있는 평일 {days} · 폭 ≥ {rules.min_width_bp}bp {usable} · 폭 중앙값 {med:.1f}bp (p10 {widths[len(widths) // 10]:.1f} · p90 {widths[9 * len(widths) // 10]:.1f})"
    )
    sd = 0.65 * med  # §4 구조 추정: 결과가 −0.5W · +1.0W · 그 사이(시간 정리)
    cost = 7.5
    for label, sharpe in (("외부 근거 (Gold Reaper 거래당 0.37)", 0.37), ("그 절반 0.185", 0.185)):
        e = sharpe * sd
        n = required_samples(sd, e, cost, z_alpha=2.24) if e > cost else float("inf")
        print(f"{label}: SD {sd:.1f}bp · e {e:.1f}bp · 비용 {cost}bp → 필요 거래 {n}")
    for n in (300, 400, 500, 600):
        print(
            f"OOS 거래 {n} 건이면 증명 가능한 최소 효과(비용 전) {detectable_effect_bps(sd, n, cost, z_alpha=2.24):.1f}bp"
        )
    return 0


def cmd_run() -> int:
    rules = Rules()
    bars = load_bars(start=date(2014, 12, 30), end=date(2026, 9, 30))
    out: dict[str, object] = {
        "rules": asdict(rules),
        "costs": asdict(VERDICT),
        "made": datetime.now(UTC).isoformat(),
    }
    all_trades: dict[str, list[Trade]] = {"B": [], "C": []}
    for d, rng, sess in sessions(bars, rules):
        if d > date(2026, 9, 30):
            break
        for v in ("B", "C"):
            t = simulate_day(d, rng, sess, rules, v)
            if t is not None:
                all_trades[v].append(t)
    for v, trades in all_trades.items():
        res = verdict(trades)
        years: dict[str, list[Trade]] = {}
        for t in trades:
            years.setdefault(t.day[:4], []).append(t)
        res["by_year"] = {y: summary(ts, VERDICT) for y, ts in sorted(years.items())}
        res["by_side_oos"] = {
            s: summary([t for t in trades if t.side == k and _in(t, *SPLITS["OOS"])], VERDICT)
            for s, k in (("long", 1), ("short", -1))
        }
        out[v] = res
    # 참고: 실제 상품 XAUUSDT 2025-12 ~ (같은 규칙, 판정 밖)
    out["xauusdt_reference"] = _xau_reference(rules)
    REPORT.mkdir(parents=True, exist_ok=True)
    (REPORT / "result.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (REPORT / "trades.json").write_text(
        json.dumps(
            {v: [asdict(t) for t in ts] for v, ts in all_trades.items()}, ensure_ascii=False
        ),
        encoding="utf-8",
    )
    for v in ("B", "C"):
        r = out[v]
        assert isinstance(r, dict)
        print(
            v,
            r["verdict"],
            {k: r[k] for k in ("V1_train", "V2_validation", "V3_oos", "V4_oos_double_cost")},
        )
        for k, s in r["splits"].items():
            print("  ", k, s)
    print("XAUUSDT 참고", out["xauusdt_reference"])
    return 0


def _xau_reference(rules: Rules) -> dict[str, object]:
    from scripts.gold_breakout_data_check import load

    raw = load("um", "XAUUSDT")
    bars = sorted((ms, o, h, lo, c, v) for ms, (o, h, lo, c, v) in raw.items() if v > 0)
    res: dict[str, object] = {}
    for v in ("B", "C"):
        trades = [
            t for d, rng, s in sessions(bars, rules) if (t := simulate_day(d, rng, s, rules, v))
        ]
        res[v] = summary(trades, VERDICT)
    return res


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=("power", "run"))
    a = p.parse_args(argv)
    return cmd_power() if a.cmd == "power" else cmd_run()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))


__all__ = ["Costs", "Rules", "Trade", "replace", "simulate_day", "summary", "verdict"]
