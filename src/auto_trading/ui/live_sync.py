"""현황 동기화 — 장부를 그 순간의 실제 계좌로 맞춘 사본을 만든다 (화면 · 텔레그램 현황 · 매일 기록).

언제 쓰나: ``ui/live_home.build_books`` 가 카드를 만들 때 (텔레그램 '현황' · 대시보드 · 매일 기록).
읽고 쓰는 것: 장부 파일은 **읽지도 고치지도 않는다** — 받은 장부 사본만 바꾼다. 실제 계좌 조회는
              실행기가 넣어 준 창구(``app/live_accounts.py``)가 하고, 60 초 동안 다시 쓰지 않는다.

[이유] 사용자 2026-10-20: "항시 모든 현황 알림은 동기화 되어서 그 당시의 현황에 맞게 보내줘야해".
       장부(``data/toss/*.json`` · ``data/upbit_t2/state.json``)는 매매 실행 때만 바뀐다. 그 사이
       체결 · 입출금 · 정리가 있으면 현황이 실제와 달랐다(예: 10-19 현황이 10-14 업비트 원화를 보임,
       토스 장 마감 지정가가 새벽에 체결돼도 다음 21:00 실행 전까지 '접수' 로 보임).

* **업비트(Vega)**: 원화 · 코인 잔고와 앱의 매수 평균가를 그대로 쓴다(업비트 원화 계좌 전체 = Vega).
* **토스(Deneb · 자동 적립)**: 장부에 '접수(SENT)' 로 남은 주문의 체결 상태를 조회해, 체결됐으면
  장부 사본에 반영해 보인다. 계좌 전체 잔고는 쓰지 않는다(사용자가 따로 든 같은 종목과 섞지 않게).
* 실제 반영은 다음 매매 실행이 한다(같은 규칙). 조회 실패 · 키 없음이면 장부 그대로 + 안내 한 줄.

디자인 패턴: **Facade** (현황 한 번에 맞추기) · 조회 창구는 밖에서 넣는다(의존성 주입) —
import 계약 'UI 는 매매 로직(거래소)을 import 하지 않는다' 를 지키려고
(리팩토링 5 단계, 2026-10-21).
"""

from __future__ import annotations

import copy
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CACHE_SECONDS = 60.0
"""실제 계좌 조회 결과를 다시 쓰는 시간 (봇 · 화면이 연달아 불러도 조회는 한 번)."""
MAX_ORDERS = 20
"""한 번에 조회할 토스 주문 수 상한 (오래 남은 접수 주문이 많을 때 조회가 길어지지 않게)."""
BRAKE_KINDS = frozenset({"brake", "rebuy"})
"""보관금으로 돈이 오가는 Deneb 주문 종류."""
PENDING = frozenset({"PENDING", "PENDING_CANCEL", ""})
"""토스 주문 상태 중 '아직 접수 중'."""


@dataclass(slots=True)
class LiveAccountSnapshot:
    """그 순간의 실제 계좌 (조회 결과 한 번)."""

    upbit: dict[str, tuple[float, float]] | None = None
    """통화 → (잔고, 매수 평균가). None = 조회 안 함 · 실패."""
    toss: dict[str, dict[str, float | str]] = field(default_factory=dict)
    """토스 주문 번호 → {status, filled_qty, filled_amount, commission}."""
    toss_qty: dict[str, float] | None = None
    """토스 미국 보유 수량 (적립 장부 last_qty 와 같은 뜻 — 계좌 전체). None = 조회 안 함."""
    notes: list[str] = field(default_factory=list)
    """조회 실패 안내 줄."""
    at: float = 0.0
    """조회한 시각 (초)."""


AccountReader = Callable[[Path, Mapping[str, str], Sequence[str], bool], LiveAccountSnapshot]
"""조회 창구 모양: (저장소 위치, .env 값, 조회할 토스 주문 번호들, 토스 보유도 읽을까) → 결과."""

_CACHE: dict[str, LiveAccountSnapshot] = {}
_account_reader: AccountReader | None = None


def install_account_reader(reader: AccountReader | None) -> None:
    """[쓰임] 실제 계좌 조회 창구를 넣는다 (``app.live_accounts.install`` 이 부른다).
    [이유] 화면 코드가 거래소를 직접 부르지 않게, 조회는 실행기가 넣어 준 함수로만 한다."""
    global _account_reader
    _account_reader = reader


# ------------------------------------------------------------------ 계산만


def to_float(value: Any) -> float:
    """[쓰임] 장부 · 조회 값을 float 로 (비었거나 숫자가 아니면 0)."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def sync_vega_ledger_to_upbit(
    state: Mapping[str, Any], upbit: Mapping[str, tuple[float, float]]
) -> dict[str, Any]:
    """[쓰임] 업비트 Vega 장부 사본: 잔고 = 업비트 실제,
    코인 원가 = 보유 × 앱 평균가 (``live_costs``)."""
    synced = copy.deepcopy(dict(state))
    expected = dict(synced.get("expected") or {})
    expected["KRW"] = str(upbit.get("KRW", (0.0, 0.0))[0])
    costs: dict[str, float] = {}
    for coin in ("BTC", "ETH", "SOL"):
        qty, average = upbit.get(coin, (0.0, 0.0))
        expected[coin] = str(qty)
        costs[coin] = qty * average
    synced["expected"] = expected
    synced["live_costs"] = costs
    synced["synced"] = True
    return synced


def _filled(status: Mapping[str, float | str] | None) -> bool | None:
    """[쓰임] True 체결 · False 안 됨(끝남) · None 아직 접수 중이거나 조회 없음."""
    if not status or str(status.get("status") or "") in PENDING:
        return None
    return to_float(status.get("filled_qty")) > 0


def sync_deneb_ledger_to_toss(
    state: Mapping[str, Any], toss: Mapping[str, Mapping[str, float | str]]
) -> dict[str, Any]:
    """[쓰임] Deneb 장부 사본: 접수 주문 중 끝난 것을 체결 · 미체결로 반영.
    [이유] ``deneb_live.apply_fill`` 과 같은 현금 · 수량 셈 — 브레이크 · 되사기 돈은 보관금으로."""
    synced = copy.deepcopy(dict(state))
    qty = dict(synced.get("qty") or {})
    brake = dict(synced.get("brake") or {})
    for order in synced.get("orders") or []:
        if order.get("status") != "SENT":
            continue
        status = toss.get(str(order.get("order_id") or ""))
        done = _filled(status)
        if done is None or status is None:
            continue
        if not done:
            order["status"] = "EXPIRED"
            continue
        filled_qty = to_float(status.get("filled_qty"))
        filled_usd = to_float(status.get("filled_amount"))
        fee = to_float(status.get("commission"))
        sign = 1 if order.get("side") == "BUY" else -1
        symbol = str(order.get("symbol"))
        qty[symbol] = to_float(qty.get(symbol)) + sign * filled_qty
        if order.get("kind") in BRAKE_KINDS:
            brake["reserve_usd"] = to_float(brake.get("reserve_usd")) - sign * filled_usd - fee
        else:
            synced["cash_usd"] = to_float(synced.get("cash_usd")) - sign * filled_usd - fee
        order.update(
            status="FILLED",
            filled_qty=filled_qty,
            filled_usd=filled_usd,
            commission=fee,
            booked=True,
        )
    synced["qty"] = qty
    if brake:
        synced["brake"] = brake
    return synced


def sync_accumulate_ledger_to_toss(
    state: Mapping[str, Any],
    toss: Mapping[str, Mapping[str, float | str]],
    fx: float | None,
    holdings: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """[쓰임] 토스 미국 적립 장부 사본: 접수(매수) 주문 중 체결된 것을 산 것으로 반영하고,
    ``holdings`` (실제 보유)가 있으면 보유 수량을 그것으로.
    [이유] 정리 · 직접 매도가 다음 적립 실행 전에도 보이게. 국내 적립은 장부 형식이 달라 그대로."""
    synced = copy.deepcopy(dict(state))
    if synced.get("currency") == "KRW":
        return synced  # 국내 적립은 장부 형식이 달라 그대로
    buys = list(synced.get("buys") or [])
    for order in synced.get("orders") or []:
        if order.get("status") != "SENT" or str(order.get("message") or "BUY") != "BUY":
            continue
        status = toss.get(str(order.get("code") or ""))
        done = _filled(status)
        if done is None or status is None:
            continue
        if not done:
            order["status"] = "EXPIRED"
            continue
        filled_qty = to_float(status.get("filled_qty"))
        filled_usd = to_float(status.get("filled_amount"))
        fee = to_float(status.get("commission"))
        buys.append(
            {
                "date": order.get("date"),
                "qty": filled_qty,
                "cost_usd": filled_usd,
                "fx_rate": fx or 0.0,
                "cost": filled_usd + fee,
            }
        )
        synced["last_qty"] = to_float(synced.get("last_qty")) + filled_qty
        order["status"] = "FILLED"
    synced["buys"] = buys
    if holdings is not None:
        synced["last_qty"] = float(holdings.get(str(synced.get("symbol") or "").upper(), 0.0))
    return synced


def pending_toss_order_ids(
    deneb: Mapping[str, Any] | None, plans: Sequence[Mapping[str, Any]]
) -> list[str]:
    """[쓰임] 조회할 토스 주문 번호 (접수 상태, 최근 것 먼저, :data:`MAX_ORDERS` 개까지)."""
    rows: list[tuple[str, str]] = []
    for order in (deneb or {}).get("orders") or []:
        if order.get("status") == "SENT" and order.get("order_id"):
            rows.append((str(order.get("date") or ""), str(order["order_id"])))
    for plan in plans:
        if plan.get("currency") == "KRW":
            continue
        for order in plan.get("orders") or []:
            if order.get("status") == "SENT" and order.get("code"):
                rows.append((str(order.get("date") or ""), str(order["code"])))
    rows.sort(reverse=True)
    return [order_id for _, order_id in rows[:MAX_ORDERS]]


# ------------------------------------------------------------------ 조회 (넣어 준 창구로)


def load(
    root: Path, deneb: Mapping[str, Any] | None, plans: Sequence[Mapping[str, Any]]
) -> LiveAccountSnapshot | None:
    """[쓰임] 실제 계좌 조회 (60 초 저장). 키 파일이 없거나 조회 창구가 없으면 None — 장부 그대로.
    [이유] 시험 · 다른 PC 에서는 ``.env`` 가 없어 조회하지 않는다."""
    from auto_trading.config.secrets import read_env_file

    env_path = root / ".env"
    if not env_path.exists() or _account_reader is None:
        return None
    key = str(root.resolve())
    cached = _CACHE.get(key)
    if cached is not None and time.time() - cached.at < CACHE_SECONDS:
        return cached
    started = time.time()
    env = read_env_file(env_path)
    order_ids = pending_toss_order_ids(deneb, plans)
    want_toss_qty = any(plan.get("currency") != "KRW" and plan.get("symbol") for plan in plans)
    snapshot = _account_reader(root, env, order_ids, want_toss_qty)
    snapshot.at = started
    _CACHE[key] = snapshot
    return snapshot


__all__ = [
    "LiveAccountSnapshot",
    "install_account_reader",
    "load",
    "pending_toss_order_ids",
    "sync_accumulate_ledger_to_toss",
    "sync_deneb_ledger_to_toss",
    "sync_vega_ledger_to_upbit",
]
