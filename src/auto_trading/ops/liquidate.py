"""'전부 정리' — 사용자가 누르면 고른 그룹의 보유를 지정가로 판다 (사용자 2026-10-11).

사용자 결정 (2026-10-11):

* 주문은 **공격적 지정가**만 — 시장가 금지 규칙 그대로(예외를 늘리지 않았다).
  업비트는 매수 호가 몇 칸 아래 지정가 IOC 를 여러 번, 토스는 매수 호가를 따라 당일(DAY) 지정가를
  내고 10 분마다 미체결분을 취소 · 다시 낸다. 어느 쪽도 최우선 매수 호가의 1% 아래로는 내지 않는다.
* 토스 장이 닫혀 있으면 **다음 정규장 시작 뒤** 판다(그때까지 '대기').
* 범위: 전부 · Vega 만 · Deneb 만 · 자동 적립만 · Altair 만
  (Altair 는 2026-10-16 사용자 "전부 정리에 altair넣어줘"). Altair 는 바이낸스 USDⓈ-M(USDC)
  포지션을 reduce-only 지정가 IOC 로 닫고, 그 종목의 비상 스톱을 지운다.
* 개별주(2026-10-16 사용자 "개별주도 자동 정리할 수 있도록"): 💹 개별주 장부(scripts/size_guard)가
  산 몫. '개별주만' · '전부' 에 들어간다.
* 한 종목(``sym:NVDA``, 사용자 "정리 nvda 이런식으로"): 그 종목의 토스 보유 중 Deneb · 적립 장부
  몫을 뺀 전부(개별주 몫 + 수동 보유). 그룹 정지는 걸지 않는다.

순서: 그룹 정지 파일(``data/HALT_<그룹>``)을 먼저 만들어 새 매수를 막고 → 토스의 그 종목 미체결
**매수** 주문을 취소하고 → 판다. 그룹 정지는 사람이 풀 때까지 남는다(풀면 각 전략이 자기 규칙대로
다시 산다).

이 모듈은 거래소 모듈을 import 하지 않는다 — 대시보드(ui)가 상태를 읽을 수 있게(import 계약).
실제 클라이언트는 ``scripts/liquidate.py`` 가 넣는다. 금액 · 수량은 Decimal.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from decimal import ROUND_DOWN, ROUND_UP, Decimal
from pathlib import Path
from typing import Any, Protocol

from auto_trading.core.errors import AutoTradingError, OrderStateUnknownError
from auto_trading.ops.kill_switch import GROUPS, group_path

__all__ = [
    "SCOPES",
    "VENUE_NAMES",
    "Item",
    "Job",
    "Store",
    "acc_symbol_of",
    "build_items",
    "choose_price",
    "floor_price",
    "groups_of",
    "job_text",
    "run_binance_item",
    "run_futures_item",
    "run_toss_item",
    "run_upbit_item",
    "scope_label",
    "set_group_halts",
    "symbol_of",
]

D = Decimal
ZERO = D(0)
SCOPES: dict[str, str] = {
    "all": "전부",
    "vega": "Vega 만",
    "deneb": "Deneb 만",
    "acc": "자동 적립만",
    "stock": "개별주만",
    "altair": "Altair 만",
    "sirius": "Sirius 만",
}
SYMBOL_SCOPE = "sym:"
"""한 종목 정리 범위 머리 (``sym:NVDA``)."""
FLOOR_PCT = D("0.01")
"""최우선 매수 호가에서 이만큼 아래까지만 판다 (한 번 주문에서)."""
UPBIT_MIN_KRW = D(5000)
VENUE_NAMES = {
    "upbit": "업비트",
    "binance": "바이낸스",
    "toss_us": "토스 미국",
    "toss_kr": "토스 국내",
    "binance_fut": "바이낸스 선물",
}
UPBIT_ROUNDS = 8
FUT_ROUNDS = 5
FUT_PAD = D("0.002")
"""선물 정리: 표시 가격에서 회차마다 0.2% 씩 더 불리하게 (5 회 = 1% 까지)."""
TOSS_REPLACE_MS = 10 * 60_000
TOSS_MAX_ORDERS = 12
REQUEST_TTL_MS = 5 * 60_000


def symbol_of(scope: str) -> str | None:
    """한 종목 범위면 그 종목, 아니면 None."""
    return scope[len(SYMBOL_SCOPE) :].upper() if scope.startswith(SYMBOL_SCOPE) else None


ACC_SCOPE = "acc:"
"""자동 적립 한 종목 정리 범위 머리 (``acc:BTQ``) — 2026-10-18 사용자 '각 종목을 선택해서 정리'."""


def acc_symbol_of(scope: str) -> str | None:
    """자동 적립 한 종목 범위면 그 종목(그 종목의 적립 몫만 판다), 아니면 None."""
    return scope[len(ACC_SCOPE) :].upper() if scope.startswith(ACC_SCOPE) else None


def scope_label(scope: str) -> str:
    sym = symbol_of(scope)
    acc = acc_symbol_of(scope)
    if acc:
        return f"자동 적립 {acc}"
    return f"{sym} 한 종목" if sym else SCOPES.get(scope, scope)


def groups_of(scope: str) -> tuple[str, ...]:
    """범위 → 정지할 그룹. 한 종목 정리는 그룹을 멈추지 않는다(빈 값)."""
    if symbol_of(scope) or acc_symbol_of(scope):
        return ()
    scope = _OLD_GROUPS.get(scope, scope)
    if scope == "all":
        return tuple(GROUPS)
    if scope not in GROUPS:
        raise ValueError(f"모르는 범위: {scope}")
    return (scope,)


# ------------------------------------------------------------------ 장부


_OLD_ITEM_KEYS = {"glide_part": "deneb_part", "glide_booked": "deneb_booked"}
"""2026-10-15 이름 변경 전에 저장된 정리 기록의 칸 이름."""
_OLD_GROUPS = {"glide": "deneb", "cruise": "vega"}
"""이름 변경 전 범위 · 그룹 키 (저장된 정리 기록)."""


@dataclass
class Order:
    order_id: str
    client_id: str
    qty: Decimal
    price: Decimal
    placed_ms: int
    filled: Decimal = ZERO
    amount: Decimal = ZERO
    """체결 금액 (그 통화, 수수료 전)."""
    fee: Decimal = ZERO
    """수수료 + 세금."""
    closed: bool = False
    status: str = ""


@dataclass
class Item:
    venue: str
    """upbit · binance · toss_us · toss_kr · binance_fut."""
    symbol: str
    groups: list[str]
    want: Decimal
    deneb_part: Decimal = ZERO
    """Deneb 장부 몫 (토스 미국 TQQQ · SCHD). 팔린 만큼 Deneb 장부에서 뺀다."""
    deneb_booked: Decimal = ZERO
    status: str = "waiting"
    """waiting · working · done · failed."""
    note: str = ""
    orders: list[Order] = field(default_factory=list)
    side: str = ""
    """binance_fut 만: 닫는 방향 (롱이면 SELL, 숏이면 BUY)."""

    @property
    def sold(self) -> Decimal:
        return sum((o.filled for o in self.orders), ZERO)

    @property
    def amount(self) -> Decimal:
        return sum((o.amount for o in self.orders), ZERO)

    @property
    def fee(self) -> Decimal:
        return sum((o.fee for o in self.orders), ZERO)

    @property
    def remaining(self) -> Decimal:
        return max(ZERO, self.want - self.sold)

    @property
    def currency(self) -> str:
        if self.venue == "binance":
            return "USDT"
        if self.venue == "binance_fut":
            return "USDC"
        return "USD" if self.venue == "toss_us" else "KRW"


@dataclass
class Job:
    job_id: str
    scope: str
    source: str
    created_ms: int
    items: list[Item]
    notes: list[str] = field(default_factory=list)
    """정리하지 않은 것 (먼지 · 소수점 · 범위 밖)."""
    log: list[str] = field(default_factory=list)
    done: bool = False
    reported: bool = False
    buys_canceled: list[str] = field(default_factory=list)
    last_note: str = ""
    """마지막으로 알린 진행 내용 — 같은 내용(예: 같은 오류)을 2 분마다 다시 보내지 않는다."""

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = _plain(asdict(self))
        return out

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> Job:
        items = []
        for raw in d.get("items") or []:
            x = dict(raw)
            orders = [
                Order(
                    **{
                        **o,
                        **{
                            k: D(str(o[k]))
                            for k in ("qty", "price", "filled", "amount", "fee")
                            if k in o
                        },
                    }
                )
                for o in x.pop("orders", [])
            ]
            for old, new in _OLD_ITEM_KEYS.items():  # 2026-10-15 이름 변경 전 기록
                if old in x:
                    x.setdefault(new, x.pop(old))
            x["groups"] = [_OLD_GROUPS.get(g, g) for g in x.get("groups") or []]
            for k in ("want", "deneb_part", "deneb_booked"):
                x[k] = D(str(x.get(k, "0")))
            items.append(Item(**x, orders=orders))
        return cls(
            job_id=str(d["job_id"]),
            scope=_OLD_GROUPS.get(str(d["scope"]), str(d["scope"])),
            source=str(d.get("source", "")),
            created_ms=int(d["created_ms"]),
            items=items,
            notes=list(d.get("notes") or []),
            log=list(d.get("log") or []),
            done=bool(d.get("done")),
            reported=bool(d.get("reported")),
            buys_canceled=list(d.get("buys_canceled") or []),
            last_note=str(d.get("last_note") or ""),
        )


def _plain(x: Any) -> Any:
    if isinstance(x, Decimal):
        return str(x)
    if isinstance(x, dict):
        return {k: _plain(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_plain(v) for v in x]
    return x


class Store:
    """``data/liquidate/jobs.json`` — 작업 · 텔레그램 요청(일회용 번호). 실행 잠금 하나."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.path = folder / "jobs.json"
        self.lock = folder / "run.lock"

    def _load(self) -> dict[str, Any]:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"jobs": [], "requests": {}}
        return d if isinstance(d, dict) else {"jobs": [], "requests": {}}

    def _save(self, d: Mapping[str, Any]) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def jobs(self) -> list[Job]:
        return [Job.from_dict(j) for j in self._load().get("jobs") or []]

    def active(self) -> list[Job]:
        return [j for j in self.jobs() if not j.done or not j.reported]

    def put(self, job: Job) -> None:
        d = self._load()
        rows = [j for j in d.get("jobs") or [] if j.get("job_id") != job.job_id]
        d["jobs"] = [*rows, job.to_dict()][-30:]
        self._save(d)

    # ---- 텔레그램 버튼 번호 (5 분, 한 번만)
    def new_request(self, now_ms: int, kind: str) -> str:
        d = self._load()
        reqs = {
            k: v
            for k, v in (d.get("requests") or {}).items()
            if now_ms - int(v.get("at", 0)) < REQUEST_TTL_MS
        }
        nonce = secrets.token_hex(4)
        reqs[nonce] = {"at": now_ms, "kind": kind}
        d["requests"] = reqs
        self._save(d)
        return nonce

    def use_request(self, nonce: str, now_ms: int, kind: str) -> bool:
        """맞는 번호면 지우고 True (두 번 누르거나 5 분이 지나면 False)."""
        d = self._load()
        reqs = dict(d.get("requests") or {})
        r = reqs.pop(nonce, None)
        d["requests"] = reqs
        self._save(d)
        return (
            r is not None
            and r.get("kind") == kind
            and now_ms - int(r.get("at", 0)) < REQUEST_TTL_MS
        )

    @contextmanager
    def running(self, stale_s: float = 600.0) -> Iterator[bool]:
        """실행 잠금. 다른 실행이 잡고 있으면 False 를 내준다 (기다리지 않는다)."""
        self.folder.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self.lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                if time.time() - self.lock.stat().st_mtime > stale_s:
                    self.lock.unlink(missing_ok=True)
                    fd = os.open(self.lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                else:
                    yield False
                    return
            except (FileNotFoundError, FileExistsError):
                yield False
                return
        try:
            yield True
        finally:
            os.close(fd)
            self.lock.unlink(missing_ok=True)


def set_group_halts(halt: Path, groups: Sequence[str], reason: str) -> list[str]:
    """그룹 정지 파일을 만든다 (이미 있으면 그대로). 만든 그룹 목록."""
    made = []
    for g in groups:
        p = group_path(halt, g)
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(reason + "\n", encoding="utf-8")
            made.append(g)
    return made


# ------------------------------------------------------------------ 계획 (순수)


def build_items(
    groups: Sequence[str],
    *,
    upbit_balances: Mapping[str, Decimal],
    upbit_prices: Mapping[str, Decimal],
    vega_coins: Sequence[str],
    toss_holdings: Mapping[str, Decimal],
    toss_sellable: Mapping[str, Decimal],
    deneb_qty: Mapping[str, Decimal],
    acc_qty: Mapping[str, Decimal],
    is_kr: Callable[[str], bool],
    binance_balances: Mapping[str, Decimal] | None = None,
    binance_prices: Mapping[str, Decimal] | None = None,
    binance_min: Mapping[str, Decimal] | None = None,
    altair_positions: Mapping[str, Decimal] | None = None,
    stock_qty: Mapping[str, Decimal] | None = None,
    only_symbol: str | None = None,
    only_acc: str | None = None,
) -> tuple[list[Item], list[str]]:
    """그룹 → 팔 것 · 못 파는 것 (먼지 · 소수점 · 묶인 수량 · 수동 보유).

    * Vega = 업비트 · 바이낸스 현물의 BTC · ETH · SOL 전량 (다른 코인은 건드리지 않는다).
      Vega 는 2026-10-15 바이낸스로 옮겼다 — 업비트에 남은 몫도 Vega 로 함께 판다.
    * Deneb = 토스 TQQQ · SCHD 중 Deneb 장부 수량.
    * 토스 적립 = 적립 장부가 산 몫(``acc_qty``) — 장부 밖 보유(사람이 직접 산 종목 · 적립 시작 전
      수량)는 **수동 보유로 남긴다**.
    * 토스 지정가는 정수 주만 — 소수점은 남기고 알린다(소수점 매도는 시장가만 된다).
    * Altair = 바이낸스 선물 BTCUSDC · ETHUSDC 포지션 전부(코인 → 부호 수량, 롱 · 숏 모두).
    """
    items: list[Item] = []
    notes: list[str] = []
    if "altair" in groups:
        for coin, q in sorted((altair_positions or {}).items()):
            if q != 0:
                items.append(
                    Item("binance_fut", coin, ["altair"], abs(q), side="SELL" if q > 0 else "BUY")
                )
    if "vega" in groups:
        for coin in vega_coins:
            qty = upbit_balances.get(coin, ZERO)
            if qty <= 0:
                continue
            px = upbit_prices.get(coin, ZERO)
            if qty * px < UPBIT_MIN_KRW:
                notes.append(f"{coin} {_n(qty)} — 5,000 원 미만이라 업비트 최소 주문 아래 (남김)")
                continue
            items.append(Item("upbit", coin, ["vega"], qty))
        for coin in vega_coins:
            qty = (binance_balances or {}).get(coin, ZERO)
            if qty <= 0:
                continue
            px = (binance_prices or {}).get(coin, ZERO)
            least = (binance_min or {}).get(coin, D(5))
            if qty * px < least:
                notes.append(f"{coin} {_n(qty)} — 바이낸스 최소 주문 {least} USDT 아래 (남김)")
                continue
            items.append(Item("binance", coin, ["vega"], qty))
    manual: list[str] = []
    stocks = stock_qty or {}
    toss_groups = {"deneb", "acc", "stock"} & set(groups)
    if only_symbol and only_symbol not in toss_holdings:
        notes.append(f"{only_symbol} — 토스에 보유 없음")
    if only_acc and only_acc not in toss_holdings:
        notes.append(f"{only_acc} — 토스에 보유 없음")
    for sym in sorted(toss_holdings):
        if only_symbol and sym != only_symbol:
            continue
        if only_acc and sym != only_acc:
            continue
        held = toss_holdings[sym]
        g_ledger = min(deneb_qty.get(sym, ZERO), held)
        a_ledger = min(acc_qty.get(sym, ZERO), held - g_ledger)
        s_ledger = min(stocks.get(sym, ZERO), held - g_ledger - a_ledger)
        rest = held - g_ledger - a_ledger - s_ledger
        parts: list[str] = []
        want = ZERO
        deneb_part = ZERO
        if only_acc:  # 자동 적립 한 종목 — 적립 몫만, 나머지는 남김
            want = a_ledger
            parts = ["acc"] if a_ledger > 0 else []
            kept = [
                f"{name} 몫 {_n(q)}"
                for name, q in (("Deneb", g_ledger), ("개별주", s_ledger), ("수동 보유", rest))
                if q > 0
            ]
            if kept:
                notes.append(f"{sym} 의 {' · '.join(kept)} 는 적립 몫이 아니라 남김")
        elif only_symbol:
            want = s_ledger + rest
            parts = (["stock"] if s_ledger > 0 else []) + (["manual"] if rest > 0 else [])
            kept = [
                f"{name} 몫 {_n(q)}"
                for name, q in (("Deneb", g_ledger), ("자동 적립", a_ledger))
                if q > 0
            ]
            if kept:
                notes.append(f"{sym} 의 {' · '.join(kept)} 는 그 전략 것이라 남김")
        else:
            if rest > 0 and toss_groups:
                manual.append(f"{sym} {_n(rest)}")
            if "deneb" in groups and g_ledger > 0:
                want += g_ledger
                deneb_part = g_ledger
                parts.append("deneb")
            if "acc" in groups and a_ledger > 0:
                want += a_ledger
                parts.append("acc")
            if "stock" in groups and s_ledger > 0:
                want += s_ledger
                parts.append("stock")
        if want <= 0:
            continue
        can = min(want, toss_sellable.get(sym, held))
        whole = can.to_integral_value(rounding=ROUND_DOWN)
        if can < want:
            notes.append(f"{sym} {_n(want - can)} 주 — 다른 매도 주문에 묶여 있음 (남김)")
        if whole < can:
            notes.append(f"{sym} {_n(can - whole)} 주 — 소수점이라 지정가로 못 팖 (남김)")
        if whole <= 0:
            continue
        venue = "toss_kr" if is_kr(sym) else "toss_us"
        items.append(Item(venue, sym, parts, whole, deneb_part=min(deneb_part, whole)))
    if manual:
        notes.append("수동 보유 (자동매매 장부 밖) — 건드리지 않음: " + ", ".join(manual))
    return items, notes


def floor_price(px: Decimal, venue: str) -> Decimal:
    """토스 미국 가격 자릿수 ($1 이상 0.01, 미만 0.0001) 로 내림.

    업비트 · 국내는 호가창 가격을 그대로 쓴다(이미 호가 단위)."""
    q = D("0.01") if px >= 1 else D("0.0001")
    return px.quantize(q, rounding=ROUND_DOWN) if venue == "toss_us" else px


def choose_price(bids: Sequence[tuple[Decimal, Decimal]], qty: Decimal) -> Decimal | None:
    """매수 호가를 위에서부터 쌓아 ``qty`` 가 다 차는 가격.

    최우선 호가의 1% 아래로는 내려가지 않는다.

    1% 안에서 다 못 차면 1% 안의 가장 낮은 호가. 호가가 없으면 None."""
    if not bids:
        return None
    floor = bids[0][0] * (1 - FLOOR_PCT)
    acc = ZERO
    price = bids[0][0]
    for px, vol in bids:
        if px < floor:
            break
        price = px
        acc += vol
        if acc >= qty:
            break
    return price


def upbit_level_price(bids: Sequence[Decimal], round_no: int) -> Decimal | None:
    """업비트 매수 호가 가격들(높은 순) 중 ``2 + 2 × 회차`` 칸 — 1% 아래로는 내려가지 않는다."""
    if not bids:
        return None
    floor = bids[0] * (1 - FLOOR_PCT)
    ok = [p for p in bids if p >= floor]
    return ok[min(2 + 2 * round_no, len(ok) - 1)]


# ------------------------------------------------------------------ 실행 (클라이언트는 밖에서)


class UpbitFill(Protocol):
    @property
    def state(self) -> str: ...
    @property
    def executed_volume(self) -> Decimal: ...
    @property
    def funds(self) -> Decimal: ...
    @property
    def paid_fee(self) -> Decimal: ...


class UpbitLike(Protocol):
    def orderbook(self, market: str) -> tuple[list[Decimal], list[Decimal]]: ...
    def place_limit_ioc(
        self, market: str, side: str, volume: Decimal, price: Decimal, identifier: str
    ) -> UpbitFill: ...
    def get_order(self, identifier: str) -> UpbitFill | None: ...


def run_upbit_item(
    item: Item,
    client: UpbitLike,
    job_id: str,
    now_ms: Callable[[], int],
    sleep: Callable[[float], None],
    *,
    rounds: int = UPBIT_ROUNDS,
) -> list[str]:
    """지정가 IOC 를 몇 번 — 회차마다 두 칸씩 더 깊게(1% 안).

    다 팔거나 5,000 원 아래가 남으면 끝.

    업비트 주문 응답은 IOC 가 처리되기 전 상태(wait · 체결 0)라, 체결은 주문 조회로 확인한다.
    예전 기록도 다시 조회해 고친다 — 2026-10-13 첫 주문에 다 팔렸는데 0 으로 적혀 같은 수량을
    다시 팔려다 '잔고 부족(insufficient_funds_ask)' 을 몇 분마다 되풀이했다(사용자 발견)."""
    log: list[str] = []
    market = f"KRW-{item.symbol}"
    empty = False
    for o in item.orders:
        if o.status == "IOC" and (seen := client.get_order(o.order_id)) is not None:
            o.filled, o.amount, o.fee = seen.executed_volume, seen.funds, seen.paid_fee
    start = len(item.orders)
    for r in range(rounds):
        _asks, bids = client.orderbook(market)
        price = upbit_level_price(bids, r)
        rem = item.remaining.quantize(D("0.00000001"), rounding=ROUND_DOWN)
        if price is None:
            log.append(f"{item.symbol} 매수 호가 없음 — 다음 확인 때 다시")
            break
        if rem * price < UPBIT_MIN_KRW:
            break
        ident = f"liq-{job_id}-{item.symbol}-{start + r}"
        try:
            got: UpbitFill | None = client.place_limit_ioc(market, "ask", rem, price, ident)
        except OrderStateUnknownError:
            got = None
        except AutoTradingError as exc:
            if "insufficient_funds_ask" not in str(exc):
                raise
            item.note = "업비트 잔고 없음 — 더 팔 것 없음"
            log.append(f"{item.symbol} 업비트에 남은 잔고 없음 — 끝으로 봄")
            empty = True
            break
        for _ in range(10):
            if got is not None and got.state in ("done", "cancel"):
                break
            sleep(0.5)
            got = client.get_order(ident) or got
        o = Order(ident, ident, rem, price, now_ms(), closed=True, status="IOC")
        if got is not None:
            o.filled, o.amount, o.fee = got.executed_volume, got.funds, got.paid_fee
        item.orders.append(o)
        if item.remaining * price < UPBIT_MIN_KRW:
            break
        sleep(1.5)
    rest = item.remaining
    bid = item.orders[-1].price if item.orders else ZERO
    done = empty or rest * bid < UPBIT_MIN_KRW or rest <= 0
    item.status = "done" if done else "working"
    log.append(
        f"{item.symbol} {_n(item.sold)} 팜 · {item.amount:,.0f} 원"
        + (f" · 남음 {_n(rest)}" if item.status != "done" else "")
    )
    return log


class BinanceLike(Protocol):
    def order_book(self, symbol: str, limit: int = ...) -> tuple[list[Any], list[Any]]: ...
    def place_limit_ioc(
        self, symbol: str, side: str, quantity: Decimal, price: Decimal, client_order_id: str
    ) -> Any: ...
    def get_order(self, symbol: str, client_order_id: str) -> Any: ...


BINANCE_FINAL = frozenset({"FILLED", "CANCELED", "EXPIRED", "REJECTED", "EXPIRED_IN_MATCH"})


def run_binance_item(
    item: Item,
    client: BinanceLike,
    job_id: str,
    now_ms: Callable[[], int],
    sleep: Callable[[float], None],
    *,
    step: Decimal,
    min_notional: Decimal,
    rounds: int = UPBIT_ROUNDS,
) -> list[str]:
    """바이낸스 현물(Vega) — 업비트와 같은 방식: 매수 호가 ``2 + 2 × 회차`` 칸(1% 안) 지정가 IOC.

    체결은 주문 조회로 확인하고, 예전 주문도 다시 조회해 고친다(업비트 10-13 교훈)."""
    log: list[str] = []
    symbol = f"{item.symbol}USDT"
    for o in item.orders:
        if o.status == "IOC" and (seen := client.get_order(symbol, o.client_id)) is not None:
            o.filled, o.amount = seen.executed_qty, seen.quote_qty
    start = len(item.orders)
    for r in range(rounds):
        bids, _asks = client.order_book(symbol, limit=20)
        price = upbit_level_price([b[0] for b in bids], r)
        rem = (item.remaining / step).to_integral_value(rounding=ROUND_DOWN) * step
        if price is None:
            log.append(f"{item.symbol} 바이낸스 매수 호가 없음 — 다음 확인 때 다시")
            break
        if rem * price < min_notional:
            break
        ident = f"liq-{job_id}-{item.symbol}-{start + r}"[:36]
        try:
            got = client.place_limit_ioc(symbol, "SELL", rem, price, ident)
        except OrderStateUnknownError:
            got = None
        for _ in range(10):
            if got is not None and got.status.value in BINANCE_FINAL:
                break
            sleep(0.5)
            got = client.get_order(symbol, ident) or got
        o = Order(ident, ident, rem, price, now_ms(), closed=True, status="IOC")
        if got is not None:
            o.filled, o.amount = got.executed_qty, got.quote_qty
            o.fee = got.quote_qty * D("0.001")
        item.orders.append(o)
        if item.remaining * price < min_notional:
            break
        sleep(1.5)
    rest = item.remaining
    bid = item.orders[-1].price if item.orders else ZERO
    item.status = "done" if rest <= 0 or rest * bid < min_notional else "working"
    log.append(
        f"{item.symbol} {_n(item.sold)} 팜 · {item.amount:,.2f} USDT"
        + (f" · 남음 {_n(rest)}" if item.status != "done" else "")
    )
    return log


class FuturesLike(Protocol):
    def mark_price(self, symbol: str) -> Decimal: ...
    def place_limit_ioc(
        self,
        symbol: str,
        side: str,
        quantity: Decimal,
        price: Decimal,
        client_order_id: str,
        *,
        reduce_only: bool,
    ) -> Any: ...
    def get_order(self, symbol: str, client_order_id: str) -> Any: ...


def run_futures_item(
    item: Item,
    client: FuturesLike,
    job_id: str,
    now_ms: Callable[[], int],
    sleep: Callable[[float], None],
    *,
    held: Decimal,
    step: Decimal,
    tick: Decimal,
    rounds: int = FUT_ROUNDS,
) -> list[str]:
    """바이낸스 선물(Altair) — reduce-only 지정가 IOC, 표시 가격 ± 0.2% × 회차(1% 까지).

    ``held`` = 지금 거래소의 그 포지션 절대 수량 — 남은 수량은 장부가 아니라 이것으로 정한다
    (비상 스톱이 먼저 닫았으면 0). reduce-only 라 포지션보다 많이 닫히지 않는다."""
    log: list[str] = []
    symbol = f"{item.symbol}USDC"
    if held <= 0:
        item.status = "done"
        item.note = item.note or "포지션 없음"
        log.append(f"{item.symbol} 선물 포지션 없음 — 끝")
        return log
    rem = held
    start = len(item.orders)
    for r in range(rounds):
        qty = (rem / step).to_integral_value(rounding=ROUND_DOWN) * step
        if qty <= 0:
            break
        mark = client.mark_price(symbol)
        pad = FUT_PAD * (r + 1)
        if item.side == "SELL":
            price = (mark * (1 - pad) / tick).to_integral_value(rounding=ROUND_DOWN) * tick
        else:
            price = (mark * (1 + pad) / tick).to_integral_value(rounding=ROUND_UP) * tick
        ident = f"liq-{job_id}-{item.symbol}-f{start + r}"[:36]
        try:
            got = client.place_limit_ioc(symbol, item.side, qty, price, ident, reduce_only=True)
        except OrderStateUnknownError:
            got = client.get_order(symbol, ident)
        o = Order(ident, ident, qty, price, now_ms(), closed=True, status="IOC")
        if got is not None:
            o.filled = got.executed_qty
            o.amount = got.executed_qty * (got.avg_price or price)
            o.fee = getattr(got, "fee", ZERO) or ZERO
        item.orders.append(o)
        rem -= o.filled
        if rem <= 0:
            break
        sleep(1.0)
    item.status = "done" if rem < step else "working"
    log.append(
        f"{item.symbol} 선물 {'롱' if item.side == 'SELL' else '숏'} {_n(item.sold)} 닫음"
        f" · {item.amount:,.2f} USDC" + (f" · 남음 {_n(rem)}" if item.status != "done" else "")
    )
    return log


class TossStatus(Protocol):
    @property
    def status(self) -> str: ...
    @property
    def filled_qty(self) -> Decimal: ...
    @property
    def filled_amount(self) -> Decimal | None: ...
    @property
    def commission(self) -> Decimal | None: ...


class TossResult(Protocol):
    @property
    def accepted(self) -> bool: ...
    @property
    def code(self) -> str: ...
    @property
    def message(self) -> str: ...


class TossLike(Protocol):
    def bids(self, symbol: str) -> list[tuple[Decimal, Decimal]]: ...
    def order(
        self,
        symbol: str,
        side: str,
        qty: int,
        limit: Decimal,
        *,
        client_order_id: str,
        time_in_force: str = "CLS",
    ) -> TossResult: ...
    def order_status(self, order_id: str) -> TossStatus: ...
    def cancel(self, order_id: str) -> TossResult: ...


_OPEN = {"PENDING", "PENDING_CANCEL", "PENDING_REPLACE", "PARTIAL_FILLED"}
"""토스 주문이 아직 살아 있는 상태. 이 밖의 값(FILLED · CANCELED · REJECTED · REPLACED, 장 마감에
만료된 당일 주문 등)은 끝난 주문으로 본다 — 모르는 끝 상태 하나로 정리가 멈추지 않게
(kr_accumulate 와 같은 기준). 빈 값은 모름이라 살아 있다고 본다."""


def run_toss_item(
    item: Item,
    trader: TossLike,
    job_id: str,
    now_ms: int,
    *,
    session_open: bool,
) -> list[str]:
    """한 번 확인: 주문 상태 갱신 → 10 분 넘게 덜 찼으면 취소 → 남은 수량을 지금 호가로 다시.

    장이 닫혀 있으면 새 주문을 내지 않는다(당일 주문은 정규장 끝에 거래소가 취소한다)."""
    log: list[str] = []
    for o in item.orders:
        if o.closed or not o.order_id:
            continue
        st = trader.order_status(o.order_id)
        o.status = st.status
        o.filled = st.filled_qty
        o.amount = st.filled_amount or ZERO
        o.fee = st.commission or ZERO
        if st.status and st.status not in _OPEN:
            o.closed = True
        elif session_open and now_ms - o.placed_ms >= TOSS_REPLACE_MS and o.filled < o.qty:
            res = trader.cancel(o.order_id)
            log.append(
                f"{item.symbol} 10 분 미체결 {o.qty - o.filled:f} 주 취소"
                + ("" if res.accepted else f" 실패({res.code})")
            )
    rem = item.remaining
    if rem <= 0:
        item.status = "done"
        return log
    if any(not o.closed for o in item.orders):
        item.status = "working"
        return log
    if not session_open:
        item.status = "waiting"
        return log
    if len(item.orders) >= TOSS_MAX_ORDERS:
        item.status = "failed"
        item.note = f"{TOSS_MAX_ORDERS} 번 내도 다 못 팖 — 앱에서 확인"
        log.append(f"{item.symbol} {item.note}")
        return log
    price = choose_price(trader.bids(item.symbol), rem)
    if price is None:
        item.status = "waiting"
        log.append(f"{item.symbol} 매수 호가 없음 — 다음 확인 때 다시")
        return log
    price = floor_price(price, item.venue)
    cid = f"liq-{job_id}-{item.symbol}-{len(item.orders)}"[:36]
    qty = int(rem)
    try:
        res = trader.order(
            item.symbol, "SELL", qty, price, client_order_id=cid, time_in_force="DAY"
        )
    except OrderStateUnknownError as exc:
        item.orders.append(Order("", cid, D(qty), price, now_ms, closed=True, status="UNKNOWN"))
        item.status = "failed"
        item.note = f"주문 결과 모름 — 앱에서 확인 ({exc})"
        log.append(f"{item.symbol} {item.note}")
        return log
    except AutoTradingError as exc:
        item.status = "failed"
        item.note = f"주문 막힘: {exc}"
        log.append(f"{item.symbol} {item.note}")
        return log
    if not res.accepted:
        item.orders.append(Order("", cid, D(qty), price, now_ms, closed=True, status=res.code))
        item.status = "working"
        log.append(f"{item.symbol} 매도 거절 {res.code} {res.message[:60]} — 다음 확인 때 다시")
        return log
    item.orders.append(Order(res.code, cid, D(qty), price, now_ms))
    item.status = "working"
    cur = "$" if item.venue == "toss_us" else ""
    log.append(f"{item.symbol} {qty} 주 매도 주문 · 지정가 {cur}{price:,}")
    return log


def deneb_to_book(item: Item) -> tuple[Decimal, Decimal]:
    """아직 Deneb 장부에 안 뺀 (수량, 달러 순수령액). 장부에 쓴 뒤 ``deneb_booked`` 를 올린다."""
    closed = [o for o in item.orders if o.closed and o.filled > 0]
    sold = sum((o.filled for o in closed), ZERO)
    if sold <= 0 or item.deneb_part <= item.deneb_booked:
        return ZERO, ZERO
    take = min(sold, item.deneb_part) - item.deneb_booked
    if take <= 0:
        return ZERO, ZERO
    net = sum((o.amount - o.fee for o in closed), ZERO)
    return take, net / sold * take


# ------------------------------------------------------------------ 보이기


def _n(x: Decimal) -> str:
    """보이기용 수량 — 뒤 0 없이, 지수 표기 없이."""
    s = format(x, "f")
    return s.rstrip("0").rstrip(".") if "." in s else s


def job_text(job: Job) -> str:
    """텔레그램 · 화면용 요약 (순수)."""
    head = f"🧯 전부 정리 · {scope_label(job.scope)} · {job.job_id}"
    lines = [head]
    names = {
        "upbit": "업비트",
        "binance": "바이낸스",
        "toss_us": "토스 미국",
        "toss_kr": "토스 국내",
        "binance_fut": "바이낸스 선물",
    }
    word = {"waiting": "⏳ 장 열리면", "working": "🔄 진행 중", "done": "✅ 끝", "failed": "⚠ 실패"}
    for it in job.items:
        amt = {
            "KRW": f"{it.amount:,.0f} 원",
            "USDT": f"{it.amount:,.2f} USDT",
            "USDC": f"{it.amount:,.2f} USDC",
        }.get(it.currency, f"${it.amount:,.2f}")
        lines.append(
            f"{word.get(it.status, it.status)} {names[it.venue]} {it.symbol} "
            f"{_n(it.sold)}/{_n(it.want)} · {amt}" + (f" · {it.note}" if it.note else "")
        )
    if not job.items:
        lines.append("팔 것이 없어요")
    if job.buys_canceled:
        lines.append("취소한 매수 주문: " + ", ".join(job.buys_canceled))
    lines += [f"· {n}" for n in job.notes]
    gs = ", ".join(GROUPS[g] for g in groups_of(job.scope))
    if gs:
        lines.append(f"⛔ {gs} 새 매수 정지 중 — 풀기: 텔레그램 '정지풀기' 또는 오르빗 설정")
    return "\n".join(lines)
