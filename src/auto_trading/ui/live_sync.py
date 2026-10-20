"""현황 동기화 — 장부를 그 순간의 실제 계좌로 맞춰 보여 준다.

화면 · 텔레그램 현황 · 매일 기록이 같이 쓴다.

사용자 2026-10-20: "항시 모든 현황 알림은 동기화 되어서 그 당시의 현황에 맞게 보내줘야해".

장부(``data/toss/*.json`` · ``data/upbit_t2/state.json``)는 매매 실행 때만 바뀐다. 그 사이
체결 · 입출금 · 정리가 있으면 현황이 실제와 달랐다(예: 10-19 현황이 10-14 업비트 원화를 보임,
토스 장 마감 지정가가 새벽에 체결돼도 다음 21:00 실행 전까지 '접수' 로 보임).

* **업비트(Vega)**: 원화 · 코인 잔고와 앱의 매수 평균가를 그대로 쓴다(업비트 원화 계좌 전체 = Vega).
* **토스(Deneb · 자동 적립)**: 장부에 '접수(SENT)' 로 남은 주문의 체결 상태를 조회해, 체결됐으면
  장부 사본에 반영해 보인다. 계좌 전체 잔고는 쓰지 않는다(사용자가 따로 든 같은 종목과 섞지 않게).
* **장부 파일은 고치지 않는다** — 사본만 바꾼다. 실제 반영은 다음 매매 실행이 한다(같은 규칙).
* 조회 실패 · 키 없음이면 장부 그대로 + 안내 한 줄. 조회는 60 초 동안 다시 쓰지 않는다.
"""

from __future__ import annotations

import copy
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CACHE_S = 60.0
MAX_ORDERS = 20
"""한 번에 조회할 토스 주문 수 상한 (오래 남은 접수 주문이 많을 때 조회가 길어지지 않게)."""
BRAKE_KINDS = frozenset({"brake", "rebuy"})
PENDING = frozenset({"PENDING", "PENDING_CANCEL", ""})


@dataclass(slots=True)
class Sync:
    upbit: dict[str, tuple[float, float]] | None = None
    """통화 → (잔고, 매수 평균가). None = 조회 안 함 · 실패."""
    toss: dict[str, dict[str, float | str]] = field(default_factory=dict)
    """토스 주문 번호 → {status, filled_qty, filled_amount, commission}."""
    toss_qty: dict[str, float] | None = None
    """토스 미국 보유 수량 (적립 장부 last_qty 와 같은 뜻 — 계좌 전체). None = 조회 안 함."""
    notes: list[str] = field(default_factory=list)
    at: float = 0.0


_CACHE: dict[str, Sync] = {}


# ------------------------------------------------------------------ 순수 계산


def _f(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def apply_vega(
    state: Mapping[str, Any], upbit: Mapping[str, tuple[float, float]]
) -> dict[str, Any]:
    """업비트 Vega 장부 사본: 잔고 = 업비트 실제, 코인 원가 = 보유 × 앱 평균가 (``live_costs``)."""
    out = copy.deepcopy(dict(state))
    exp = dict(out.get("expected") or {})
    exp["KRW"] = str(upbit.get("KRW", (0.0, 0.0))[0])
    costs: dict[str, float] = {}
    for coin in ("BTC", "ETH", "SOL"):
        qty, avg = upbit.get(coin, (0.0, 0.0))
        exp[coin] = str(qty)
        costs[coin] = qty * avg
    out["expected"] = exp
    out["live_costs"] = costs
    out["synced"] = True
    return out


def _filled(s: Mapping[str, float | str] | None) -> bool | None:
    """True 체결 · False 안 됨(끝남) · None 아직 접수 중이거나 조회 없음."""
    if not s or str(s.get("status") or "") in PENDING:
        return None
    return _f(s.get("filled_qty")) > 0


def apply_deneb(
    state: Mapping[str, Any], toss: Mapping[str, Mapping[str, float | str]]
) -> dict[str, Any]:
    """Deneb 장부 사본: 접수 주문 중 끝난 것을 체결 · 미체결로 반영.

    ``deneb_live.apply_fill`` 과 같은 현금 · 수량, 브레이크 · 되사기 돈은 보관금."""
    out = copy.deepcopy(dict(state))
    qty = dict(out.get("qty") or {})
    brake = dict(out.get("brake") or {})
    for o in out.get("orders") or []:
        if o.get("status") != "SENT":
            continue
        s = toss.get(str(o.get("order_id") or ""))
        done = _filled(s)
        if done is None or s is None:
            continue
        if not done:
            o["status"] = "EXPIRED"
            continue
        n, usd, fee = _f(s.get("filled_qty")), _f(s.get("filled_amount")), _f(s.get("commission"))
        sign = 1 if o.get("side") == "BUY" else -1
        sym = str(o.get("symbol"))
        qty[sym] = _f(qty.get(sym)) + sign * n
        if o.get("kind") in BRAKE_KINDS:
            brake["reserve_usd"] = _f(brake.get("reserve_usd")) - sign * usd - fee
        else:
            out["cash_usd"] = _f(out.get("cash_usd")) - sign * usd - fee
        o.update(status="FILLED", filled_qty=n, filled_usd=usd, commission=fee, booked=True)
    out["qty"] = qty
    if brake:
        out["brake"] = brake
    return out


def apply_plan(
    state: Mapping[str, Any],
    toss: Mapping[str, Mapping[str, float | str]],
    fx: float | None,
    holdings: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """토스 미국 적립 장부 사본: 접수(매수) 주문 중 체결된 것을 산 것으로 반영하고, ``holdings``
    (실제 보유)가 있으면 보유 수량을 그것으로 — 정리 · 직접 매도가 다음 적립 실행 전에도 보이게."""
    out = copy.deepcopy(dict(state))
    if out.get("currency") == "KRW":
        return out  # 국내 적립은 장부 형식이 달라 그대로
    buys = list(out.get("buys") or [])
    for o in out.get("orders") or []:
        if o.get("status") != "SENT" or str(o.get("message") or "BUY") != "BUY":
            continue
        s = toss.get(str(o.get("code") or ""))
        done = _filled(s)
        if done is None or s is None:
            continue
        if not done:
            o["status"] = "EXPIRED"
            continue
        n, usd, fee = _f(s.get("filled_qty")), _f(s.get("filled_amount")), _f(s.get("commission"))
        buys.append(
            {
                "date": o.get("date"),
                "qty": n,
                "cost_usd": usd,
                "fx_rate": fx or 0.0,
                "cost": usd + fee,
            }
        )
        out["last_qty"] = _f(out.get("last_qty")) + n
        o["status"] = "FILLED"
    out["buys"] = buys
    if holdings is not None:
        out["last_qty"] = float(holdings.get(str(out.get("symbol") or "").upper(), 0.0))
    return out


def sent_ids(deneb: Mapping[str, Any] | None, plans: Sequence[Mapping[str, Any]]) -> list[str]:
    """조회할 토스 주문 번호 (접수 상태, 최근 것 먼저, :data:`MAX_ORDERS` 개까지)."""
    rows: list[tuple[str, str]] = []
    for o in (deneb or {}).get("orders") or []:
        if o.get("status") == "SENT" and o.get("order_id"):
            rows.append((str(o.get("date") or ""), str(o["order_id"])))
    for p in plans:
        if p.get("currency") == "KRW":
            continue
        for o in p.get("orders") or []:
            if o.get("status") == "SENT" and o.get("code"):
                rows.append((str(o.get("date") or ""), str(o["code"])))
    rows.sort(reverse=True)
    return [oid for _, oid in rows[:MAX_ORDERS]]


# ------------------------------------------------------------------ 조회 (I/O)


def load(
    root: Path, deneb: Mapping[str, Any] | None, plans: Sequence[Mapping[str, Any]]
) -> Sync | None:
    """실제 계좌 조회 (60 초 저장). 키가 하나도 없으면 None — 장부 그대로(시험 · 다른 PC)."""
    from auto_trading.config.secrets import read_env_file

    env_path = root / ".env"
    if not env_path.exists():
        return None
    key = str(root.resolve())
    hit = _CACHE.get(key)
    if hit is not None and time.time() - hit.at < CACHE_S:
        return hit
    env = read_env_file(env_path)
    out = Sync(at=time.time())
    if env.get("UPBIT_API_KEY") and env.get("UPBIT_AOU_SECRET"):
        try:
            from auto_trading.exchange.upbit import UpbitClient

            c = UpbitClient(env["UPBIT_API_KEY"], env["UPBIT_AOU_SECRET"])
            try:
                out.upbit = {k: (float(q), float(a)) for k, (q, a) in c.accounts().items()}
            finally:
                c.close()
        except Exception as exc:  # 조회 실패로 현황을 막지 않는다
            out.notes.append(f"업비트 조회 실패 — 장부 기준 ({type(exc).__name__})")
    ids = sent_ids(deneb, plans)
    want_qty = any(p.get("currency") != "KRW" and p.get("symbol") for p in plans)
    if ids or want_qty:
        try:
            from auto_trading.config.secrets import load_toss_credentials
            from auto_trading.exchange.toss import TossReader, TossTokenCache

            r = TossReader(
                load_toss_credentials(env_path), TossTokenCache(root / "data" / "toss_token.json")
            )
            try:
                if want_qty:
                    out.toss_qty = {k: float(v.qty) for k, v in r.holdings().items()}
                for oid in ids:
                    st = r.order_status(oid)
                    out.toss[oid] = {
                        "status": st.status,
                        "filled_qty": float(st.filled_qty),
                        "filled_amount": float(st.filled_amount or 0)
                        or float(st.filled_qty) * float(st.avg_price or 0),
                        "commission": float(st.commission or 0),
                    }
            finally:
                r.close()
        except Exception as exc:  # 조회 실패로 현황을 막지 않는다
            out.notes.append(f"토스 주문 조회 실패 — 장부 기준 ({type(exc).__name__})")
    _CACHE[key] = out
    return out


__all__ = ["Sync", "apply_deneb", "apply_plan", "apply_vega", "load", "sent_ids"]
