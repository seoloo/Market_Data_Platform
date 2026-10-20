"""🌟 Deneb 그림자 기록 (문서 280) — 실거래 Deneb 규칙을 정지와 상관없이 매일 따라 한다.

주문 · 알림 없음.

사용 예::

    python -m scripts.deneb_shadow run      # 예약: 끝난 미국 거래일을 차례로 처리 (토스 조회만)
    python -m scripts.deneb_shadow status

* 시작 = 실거래 Deneb 시작일(2026-10-09) · 같은 첫 납입. 그 뒤 달마다 실거래 장부의 금액
  (텔레그램 답), 없으면 설정 ``deneb.monthly_krw``.
* 시세는 토스 수정주가 일봉(실거래와 같은 곳), 환율은 처리하는 때의 토스 환율,
  수수료는 토스 미국 수수료.
* 상태 ``data/deneb_shadow/state.json``, 보고 ``reports/deneb_shadow/latest.json``.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from auto_trading.config.secrets import load_toss_credentials
from auto_trading.exchange.toss import TossReader, TossTokenCache
from auto_trading.ops import deneb_shadow as core
from auto_trading.ops import user_settings
from auto_trading.ops.deneb_guard import Brake
from auto_trading.ops.deneb_live import DIVIDEND_ETF, LEVERAGED_ETF, DenebState
from auto_trading.ops.qs_paper import trend_on
from auto_trading.ops.us_daily_bars import last_settled_day

REPO = Path(__file__).resolve().parent.parent
ROOT = REPO / "data" / "deneb_shadow"
STATE = ROOT / "state.json"
REPORTS = REPO / "reports" / "deneb_shadow"
REAL = REPO / "data" / "toss" / "DENEB.json"
TOKEN = REPO / "data" / "toss_token.json"
SYMS = (LEVERAGED_ETF, DIVIDEND_ETF, "QQQ")


def _since(a: str, b: str) -> int:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def _trend(closes: list[float]) -> bool | None:
    return trend_on(closes, len(closes) - 1) if len(closes) >= 121 else None


def _real() -> dict[str, Any]:
    try:
        d: dict[str, Any] = json.loads(REAL.read_text(encoding="utf-8"))
        return d
    except (OSError, ValueError):
        return {}


def _load() -> dict[str, Any]:
    if STATE.exists():
        d: dict[str, Any] = json.loads(STATE.read_text(encoding="utf-8"))
        return d
    real = _real()
    start = str(real.get("start") or "2026-10-09")
    return {
        "deneb": DenebState(start=start).to_dict(),
        "brake": Brake(ref="T").to_dict(),
        "last_day": "",
        "history": [],
        "log": [],
    }


def _save(st: dict[str, Any]) -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(STATE)


def _amount(month: str) -> float:
    """실거래 장부가 정한 그 달 금액(텔레그램 답), 없으면 설정 기본."""
    amounts = _real().get("amounts") or {}
    if month in amounts:
        return float(amounts[month])
    return float(user_settings.get(REPO, "deneb.monthly_krw"))


def _first_fx(month: str, fx: float) -> float:
    """실거래 첫 납입이 있으면 그 환율 (같은 날 같은 돈으로 시작)."""
    for d in _real().get("deposits") or []:
        if str(d[0])[:7] == month and len(d) >= 4:
            return float(d[3])
    return fx


def process(
    st: dict[str, Any],
    closes: core.Closes,
    settled: str,
    *,
    fx: float,
    fee: float,
    events: list[tuple[str, str, float]],
) -> list[str]:
    deneb = DenebState.from_dict(st["deneb"])
    brake = Brake.from_dict(st["brake"])
    days = [d for d, _ in closes["QQQ"] if deneb.start <= d <= settled and d > st["last_day"]]
    out: list[str] = []
    for day in days:
        lines = core.step(
            deneb, brake, day, closes,
            monthly_krw=_amount(day[:7]), fx=_first_fx(day[:7], fx), fee=fee, events=events,
            trend_on=_trend, days_since=_since,
        )  # fmt: skip
        value = core.value_usd(deneb, brake, closes, day)
        st["history"].append([day, round(value, 2), round(fx, 2)])
        if lines:
            st["log"].append([day, lines])
            out += [f"{day} {x}" for x in lines]
        st["last_day"] = day
    st["deneb"], st["brake"] = deneb.to_dict(), brake.to_dict()
    return out


def report(st: dict[str, Any]) -> dict[str, Any]:
    deneb = DenebState.from_dict(st["deneb"])
    brake = Brake.from_dict(st["brake"])
    paid_krw = sum(float(d[1]) for d in deneb.deposits)
    paid_usd = sum(float(d[2]) for d in deneb.deposits)
    last = st["history"][-1] if st["history"] else None
    value = float(last[1]) if last else 0.0
    peak, mdd = 0.0, 0.0
    for _d, v, _fx in st["history"]:
        peak = max(peak, float(v))
        mdd = max(mdd, 1 - float(v) / peak) if peak > 0 else mdd
    return {
        "generated_utc": datetime.now(UTC).isoformat(),
        "start": deneb.start,
        "last_day": st["last_day"],
        "paid_krw": paid_krw,
        "paid_usd": round(paid_usd, 2),
        "value_usd": value,
        "return_on_paid": value / paid_usd - 1 if paid_usd > 0 else 0.0,
        "mdd_value": mdd,
        "qty": core.holdings(deneb),
        "cash_usd": round(deneb.cash_usd, 2),
        "brake": {
            "stage": brake.stage,
            "reserve_usd": round(brake.reserve_usd, 2),
            "lev_peak": brake.lev_peak,
            "sales": brake.sales,
        },
        "months": deneb.months,
        "recent": st["log"][-5:],
    }


def text(rep: dict[str, Any]) -> str:
    q = rep["qty"]
    b = rep["brake"]
    lines = [
        f"🌟 Deneb 그림자 기록 — {rep['start']} ~ {rep['last_day'] or '처리 전'} (주문 없음)",
        f"납입 {rep['paid_krw']:,.0f}원(${rep['paid_usd']:,.2f}) → 평가 ${rep['value_usd']:,.2f}"
        f" ({rep['return_on_paid']:+.2%}) · 평가 고점 대비 최대 −{rep['mdd_value']:.1%}",
        f"보유 TQQQ {q.get('TQQQ', 0):g} · SCHD {q.get('SCHD', 0):g}"
        f" · 현금 ${rep['cash_usd']:,.2f}",
        f"브레이크 단계 {b['stage']} · 보관금 ${b['reserve_usd']:,.2f}"
        f" · TQQQ 고점 ${b['lev_peak']:,.2f}",
    ]
    if rep["months"]:
        m = rep["months"][-1]
        lines.append(f"최근 비율 {m[0]} TQQQ {float(m[2]):.0%}")
    for day, ls in rep["recent"][-3:]:
        lines.append(f"· {day}: {' / '.join(ls)}")
    return "\n".join(lines)


def run() -> int:
    reader = TossReader(load_toss_credentials(REPO / ".env"), TossTokenCache(TOKEN))
    closes = {s: [(d, float(px)) for d, px in reader.daily_closes(s, 250)] for s in SYMS}
    fx = float(reader.usd_krw())
    fee = float(reader.us_commission() or 0.001)
    from scripts.toss_accumulate import _dividend_events

    st = _load()
    out = process(st, closes, last_settled_day(), fx=fx, fee=fee, events=_dividend_events())
    _save(st)
    rep = report(st)
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "latest.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), "utf-8")
    print("\n".join(out) if out else "새로 처리한 거래일 없음")
    print(text(rep))
    return 0


def main(argv: list[str]) -> int:
    reconf = getattr(sys.stdout, "reconfigure", None)
    if reconf is not None:
        reconf(encoding="utf-8", errors="replace")
    if len(argv) > 1 and argv[1] == "run":
        return run()
    if len(argv) > 1 and argv[1] == "status":
        path = REPORTS / "latest.json"
        print(text(json.loads(path.read_text(encoding="utf-8"))) if path.exists() else "보고 없음")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
