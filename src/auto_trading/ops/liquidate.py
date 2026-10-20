"""🧯 '전부 정리' 계산 · 기록 — 사용자가 누르면 고른 그룹의 보유를 지정가로 판다 (2026-10-11).

언제 쓰나: 텔레그램 '정리' 버튼 · 오르빗 설정 화면 → ``scripts/liquidate.py``
           (봇이 2 분마다 이어 감).
읽고 쓰는 것: 작업 기록 ``data/liquidate/jobs.json`` · 실행 잠금 ``data/liquidate/run.lock`` ·
              그룹 정지 파일 ``data/HALT_<그룹>`` (만들기만). 주문은 밖에서 넣어 준 창구로만.

사용자 결정 (2026-10-11):

* 주문은 **공격적 지정가**만 (당시 시장가 금지 규칙 그대로 — 규칙은 10-18 에 지워졌지만
  그대로 둔다).
  업비트는 매수 호가 몇 칸 아래 지정가 IOC 를 여러 번, 토스는 매수 호가를 따라 당일(DAY) 지정가를
  내고 10 분마다 미체결분을 취소 · 다시 낸다. 어느 쪽도 최우선 매수 호가의 1% 아래로는 내지 않는다.
* 토스 장이 닫혀 있으면 **다음 정규장 시작 뒤** 판다(그때까지 '대기').
* 범위: 전부 · Vega 만 · Deneb 만 · 자동 적립만 · 개별주만 · Altair 만 · Sirius 만.
  Altair(은퇴 2026-10-19, 4 · 5 단계에서 뺄 예정)는 바이낸스 USDⓈ-M(USDC) 포지션을 reduce-only
  지정가 IOC 로 닫는다.
* 개별주(2026-10-16 사용자 "개별주도 자동 정리할 수 있도록"): 💹 개별주 장부(scripts/size_guard)가
  산 몫. '개별주만' · '전부' 에 들어간다.
* 한 종목(``sym:NVDA``, 사용자 "정리 nvda 이런식으로"): 그 종목의 토스 보유 중 Deneb · 적립 장부
  몫을 뺀 전부(개별주 몫 + 수동 보유). 그룹 정지는 걸지 않는다.

순서: 그룹 정지 파일(``data/HALT_<그룹>``)을 먼저 만들어 새 매수를 막고 → 토스의 그 종목 미체결
**매수** 주문을 취소하고 → 판다. 그룹 정지는 사람이 풀 때까지 남는다(풀면 각 전략이 자기 규칙대로
다시 산다).

[이유] 이 모듈은 거래소 모듈을 import 하지 않는다 — 대시보드(ui)가 상태를 읽을 수 있게(import 계약).
       실제 클라이언트는 ``scripts/liquidate.py`` 가 넣는다(창구 모양은 아래 Protocol).
       금액 · 수량은 Decimal.
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
"""정리 범위 키(저장 · 버튼 값) → 보이는 이름."""
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
"""파는 곳 키 → 보이는 이름."""
UPBIT_ROUNDS = 8
"""업비트 · 바이낸스 현물: 한 번 확인에 IOC 를 몇 번까지 낼지."""
FUT_ROUNDS = 5
FUT_PAD = D("0.002")
"""선물 정리: 표시 가격에서 회차마다 0.2% 씩 더 불리하게 (5 회 = 1% 까지)."""
TOSS_REPLACE_MS = 10 * 60_000
"""토스: 이만큼(10 분) 덜 찬 주문은 취소하고 지금 호가로 다시 낸다."""
TOSS_MAX_ORDERS = 12
"""토스: 이만큼 내도 다 못 팔면 '실패' 로 두고 사람에게 넘긴다."""
REQUEST_TTL_MS = 5 * 60_000
"""텔레그램 확인 버튼이 유효한 시간 (5 분)."""


def symbol_of(scope: str) -> str | None:
    """[쓰임] 한 종목 범위(``sym:NVDA``)면 그 종목, 아니면 None."""
    if not scope.startswith(SYMBOL_SCOPE):
        return None
    return scope[len(SYMBOL_SCOPE) :].upper()


ACC_SCOPE = "acc:"
"""자동 적립 한 종목 정리 범위 머리 (``acc:BTQ``) — 2026-10-18 사용자 '각 종목을 선택해서 정리'."""


def acc_symbol_of(scope: str) -> str | None:
    """[쓰임] 자동 적립 한 종목 범위면 그 종목(그 종목의 적립 몫만 판다), 아니면 None."""
    if not scope.startswith(ACC_SCOPE):
        return None
    return scope[len(ACC_SCOPE) :].upper()


def scope_label(scope: str) -> str:
    """[쓰임] 범위 → 보이는 이름 ('Deneb 만' · 'NVDA 한 종목' · '자동 적립 BTQ')."""
    symbol = symbol_of(scope)
    acc_symbol = acc_symbol_of(scope)
    if acc_symbol:
        return f"자동 적립 {acc_symbol}"
    return f"{symbol} 한 종목" if symbol else SCOPES.get(scope, scope)


def groups_of(scope: str) -> tuple[str, ...]:
    """[쓰임] 범위 → 정지할 그룹. 한 종목 정리는 그룹을 멈추지 않는다(빈 값)."""
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
    """정리 주문 하나 (작업 기록 한 줄 — 키 이름은 파일에 저장돼 바꾸지 않는다)."""

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
    """팔 것 하나 (곳 · 종목 · 수량)."""

    venue: str
    """upbit · binance · toss_us · toss_kr · binance_fut."""
    symbol: str
    groups: list[str]
    want: Decimal
    """팔 수량."""
    deneb_part: Decimal = ZERO
    """Deneb 장부 몫 (토스 미국 TQQQ · SCHD · IAUM). 팔린 만큼 Deneb 장부에서 뺀다."""
    deneb_booked: Decimal = ZERO
    """Deneb 장부에 이미 뺀 수량."""
    status: str = "waiting"
    """waiting · working · done · failed."""
    note: str = ""
    orders: list[Order] = field(default_factory=list)
    side: str = ""
    """binance_fut 만: 닫는 방향 (롱이면 SELL, 숏이면 BUY)."""

    @property
    def sold(self) -> Decimal:
        return sum((order.filled for order in self.orders), ZERO)

    @property
    def amount(self) -> Decimal:
        return sum((order.amount for order in self.orders), ZERO)

    @property
    def fee(self) -> Decimal:
        return sum((order.fee for order in self.orders), ZERO)

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


def _order_from_dict(raw: Mapping[str, Any]) -> Order:
    """[쓰임] 작업 기록의 주문 한 줄 → :class:`Order` (숫자 칸은 Decimal 로)."""
    data = dict(raw)
    for key in ("qty", "price", "filled", "amount", "fee"):
        if key in data:
            data[key] = D(str(data[key]))
    return Order(**data)


def _item_from_dict(raw: Mapping[str, Any]) -> Item:
    """[쓰임] 작업 기록의 팔 것 한 줄 → :class:`Item` (옛 이름 칸 · 그룹 키도 받는다)."""
    data = dict(raw)
    orders = [_order_from_dict(order) for order in data.pop("orders", [])]
    for old, new in _OLD_ITEM_KEYS.items():  # 2026-10-15 이름 변경 전 기록
        if old in data:
            data.setdefault(new, data.pop(old))
    data["groups"] = [_OLD_GROUPS.get(group, group) for group in data.get("groups") or []]
    for key in ("want", "deneb_part", "deneb_booked"):
        data[key] = D(str(data.get(key, "0")))
    return Item(**data, orders=orders)


@dataclass
class Job:
    """정리 작업 하나 (버튼 한 번)."""

    job_id: str
    """시작 시각 ``YYYYMMDDTHHMMSS`` (KST)."""
    scope: str
    source: str
    """텔레그램 · 오르빗 등 누른 곳."""
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
        result: dict[str, Any] = _plain(asdict(self))
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Job:
        return cls(
            job_id=str(data["job_id"]),
            scope=_OLD_GROUPS.get(str(data["scope"]), str(data["scope"])),
            source=str(data.get("source", "")),
            created_ms=int(data["created_ms"]),
            items=[_item_from_dict(item) for item in data.get("items") or []],
            notes=list(data.get("notes") or []),
            log=list(data.get("log") or []),
            done=bool(data.get("done")),
            reported=bool(data.get("reported")),
            buys_canceled=list(data.get("buys_canceled") or []),
            last_note=str(data.get("last_note") or ""),
        )


def _plain(value: Any) -> Any:
    """[쓰임] JSON 으로 쓸 수 있게 Decimal 을 글자로 (dict · list 안까지)."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


class Store:
    """[쓰임] 작업 기록 ``data/liquidate/jobs.json`` — 작업 · 텔레그램 요청(일회용 번호) ·
    실행 잠금 하나.
    디자인 패턴: Repository (기록 읽기 · 쓰기를 한 곳에)."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.path = folder / "jobs.json"
        self.lock = folder / "run.lock"

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"jobs": [], "requests": {}}
        return data if isinstance(data, dict) else {"jobs": [], "requests": {}}

    def _save(self, data: Mapping[str, Any]) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        temporary.replace(self.path)

    def jobs(self) -> list[Job]:
        return [Job.from_dict(job) for job in self._load().get("jobs") or []]

    def active(self) -> list[Job]:
        """[쓰임] 아직 안 끝났거나 끝난 것을 알리지 않은 작업."""
        return [job for job in self.jobs() if not job.done or not job.reported]

    def put(self, job: Job) -> None:
        """[쓰임] 작업을 기록에 쓴다 (같은 번호는 바꿔 쓰고, 최근 30 개만 둔다)."""
        data = self._load()
        rows = [row for row in data.get("jobs") or [] if row.get("job_id") != job.job_id]
        data["jobs"] = [*rows, job.to_dict()][-30:]
        self._save(data)

    # ---- 텔레그램 버튼 번호 (5 분, 한 번만)
    def new_request(self, now_ms: int, kind: str) -> str:
        """[쓰임] 확인 버튼에 붙일 일회용 번호를 만든다 (5 분 지난 번호는 지운다)."""
        data = self._load()
        requests = {
            nonce: request
            for nonce, request in (data.get("requests") or {}).items()
            if now_ms - int(request.get("at", 0)) < REQUEST_TTL_MS
        }
        nonce = secrets.token_hex(4)
        requests[nonce] = {"at": now_ms, "kind": kind}
        data["requests"] = requests
        self._save(data)
        return nonce

    def use_request(self, nonce: str, now_ms: int, kind: str) -> bool:
        """[쓰임] 맞는 번호면 지우고 True (두 번 누르거나 5 분이 지나면 False).
        [이유] 같은 버튼을 두 번 눌러 정리가 두 번 시작되지 않게."""
        data = self._load()
        requests = dict(data.get("requests") or {})
        request = requests.pop(nonce, None)
        data["requests"] = requests
        self._save(data)
        return (
            request is not None
            and request.get("kind") == kind
            and now_ms - int(request.get("at", 0)) < REQUEST_TTL_MS
        )

    @contextmanager
    def running(self, stale_s: float = 600.0) -> Iterator[bool]:
        """[쓰임] 실행 잠금. 다른 실행이 잡고 있으면 False 를 내준다 (기다리지 않는다).
        [이유] 봇 · 화면이 동시에 정리를 이어 가 같은 주문을 두 번 내지 않게.
               10 분 넘은 잠금은 버린다."""
        self.folder.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(self.lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                if time.time() - self.lock.stat().st_mtime > stale_s:
                    self.lock.unlink(missing_ok=True)
                    descriptor = os.open(self.lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                else:
                    yield False
                    return
            except (FileNotFoundError, FileExistsError):
                yield False
                return
        try:
            yield True
        finally:
            os.close(descriptor)
            self.lock.unlink(missing_ok=True)


def set_group_halts(halt: Path, groups: Sequence[str], reason: str) -> list[str]:
    """[쓰임] 그룹 정지 파일을 만든다 (이미 있으면 그대로). 만든 그룹 목록."""
    made = []
    for group in groups:
        path = group_path(halt, group)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(reason + "\n", encoding="utf-8")
            made.append(group)
    return made


# ------------------------------------------------------------------ 계획 (계산만)


def _kept_text(parts: Sequence[tuple[str, Decimal]]) -> str:
    """[쓰임] '이름 몫 수량 · …' (0 인 몫은 뺀다)."""
    return " · ".join(f"{name} 몫 {_n(qty)}" for name, qty in parts if qty > 0)


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
    """[쓰임] 그룹 → (팔 것, 못 파는 것 메모 — 먼지 · 소수점 · 묶인 수량 · 수동 보유).
    [이유] 토스 보유는 장부별 몫으로 나눠, 고른 그룹의 몫만 판다 —
           사람이 직접 산 몫은 건드리지 않는다.

    * Vega = 업비트 · 바이낸스 현물의 BTC · ETH · SOL 전량 (다른 코인은 건드리지 않는다).
    * Deneb = 토스 TQQQ · SCHD · IAUM 중 Deneb 장부 수량.
    * 토스 적립 = 적립 장부가 산 몫(``acc_qty``) — 장부 밖 보유(사람이 직접 산 종목 · 적립 시작 전
      수량)는 **수동 보유로 남긴다**.
    * 토스 지정가는 정수 주만 — 소수점은 남기고 알린다(소수점 매도는 시장가만 된다).
    * Altair = 바이낸스 선물 BTCUSDC · ETHUSDC 포지션 전부(코인 → 부호 수량, 롱 · 숏 모두)."""
    items: list[Item] = []
    notes: list[str] = []
    # 1 단계: Altair (바이낸스 선물)
    if "altair" in groups:
        for coin, qty in sorted((altair_positions or {}).items()):
            if qty != 0:
                side = "SELL" if qty > 0 else "BUY"
                items.append(Item("binance_fut", coin, ["altair"], abs(qty), side=side))
    # 2 단계: Vega (업비트 · 바이낸스 현물 코인)
    if "vega" in groups:
        for coin in vega_coins:
            qty = upbit_balances.get(coin, ZERO)
            if qty <= 0:
                continue
            price = upbit_prices.get(coin, ZERO)
            if qty * price < UPBIT_MIN_KRW:
                notes.append(f"{coin} {_n(qty)} — 5,000 원 미만이라 업비트 최소 주문 아래 (남김)")
                continue
            items.append(Item("upbit", coin, ["vega"], qty))
        for coin in vega_coins:
            qty = (binance_balances or {}).get(coin, ZERO)
            if qty <= 0:
                continue
            price = (binance_prices or {}).get(coin, ZERO)
            least = (binance_min or {}).get(coin, D(5))
            if qty * price < least:
                notes.append(f"{coin} {_n(qty)} — 바이낸스 최소 주문 {least} USDT 아래 (남김)")
                continue
            items.append(Item("binance", coin, ["vega"], qty))
    # 3 단계: 토스 — 종목마다 Deneb · 적립 · 개별주 · 수동 몫으로 나누기
    manual: list[str] = []
    stocks = stock_qty or {}
    toss_groups = {"deneb", "acc", "stock"} & set(groups)
    if only_symbol and only_symbol not in toss_holdings:
        notes.append(f"{only_symbol} — 토스에 보유 없음")
    if only_acc and only_acc not in toss_holdings:
        notes.append(f"{only_acc} — 토스에 보유 없음")
    for symbol in sorted(toss_holdings):
        if only_symbol and symbol != only_symbol:
            continue
        if only_acc and symbol != only_acc:
            continue
        held = toss_holdings[symbol]
        deneb_share = min(deneb_qty.get(symbol, ZERO), held)
        acc_share = min(acc_qty.get(symbol, ZERO), held - deneb_share)
        stock_share = min(stocks.get(symbol, ZERO), held - deneb_share - acc_share)
        manual_share = held - deneb_share - acc_share - stock_share
        parts: list[str] = []
        want = ZERO
        deneb_part = ZERO
        if only_acc:  # 자동 적립 한 종목 — 적립 몫만, 나머지는 남김
            want = acc_share
            parts = ["acc"] if acc_share > 0 else []
            kept = _kept_text(
                (("Deneb", deneb_share), ("개별주", stock_share), ("수동 보유", manual_share))
            )
            if kept:
                notes.append(f"{symbol} 의 {kept} 는 적립 몫이 아니라 남김")
        elif only_symbol:
            want = stock_share + manual_share
            if stock_share > 0:
                parts.append("stock")
            if manual_share > 0:
                parts.append("manual")
            kept = _kept_text((("Deneb", deneb_share), ("자동 적립", acc_share)))
            if kept:
                notes.append(f"{symbol} 의 {kept} 는 그 전략 것이라 남김")
        else:
            if manual_share > 0 and toss_groups:
                manual.append(f"{symbol} {_n(manual_share)}")
            if "deneb" in groups and deneb_share > 0:
                want += deneb_share
                deneb_part = deneb_share
                parts.append("deneb")
            if "acc" in groups and acc_share > 0:
                want += acc_share
                parts.append("acc")
            if "stock" in groups and stock_share > 0:
                want += stock_share
                parts.append("stock")
        if want <= 0:
            continue
        # 4 단계: 팔 수 있는 수량 (다른 매도에 묶인 것 · 소수점은 남김)
        sellable = min(want, toss_sellable.get(symbol, held))
        whole = sellable.to_integral_value(rounding=ROUND_DOWN)
        if sellable < want:
            notes.append(f"{symbol} {_n(want - sellable)} 주 — 다른 매도 주문에 묶여 있음 (남김)")
        if whole < sellable:
            notes.append(f"{symbol} {_n(sellable - whole)} 주 — 소수점이라 지정가로 못 팖 (남김)")
        if whole <= 0:
            continue
        venue = "toss_kr" if is_kr(symbol) else "toss_us"
        items.append(Item(venue, symbol, parts, whole, deneb_part=min(deneb_part, whole)))
    if manual:
        notes.append("수동 보유 (자동매매 장부 밖) — 건드리지 않음: " + ", ".join(manual))
    return items, notes


def floor_price(price: Decimal, venue: str) -> Decimal:
    """[쓰임] 토스 미국 가격 자릿수 ($1 이상 0.01, 미만 0.0001) 로 내림.

    업비트 · 국내는 호가창 가격을 그대로 쓴다(이미 호가 단위)."""
    unit = D("0.01") if price >= 1 else D("0.0001")
    return price.quantize(unit, rounding=ROUND_DOWN) if venue == "toss_us" else price


def choose_price(bids: Sequence[tuple[Decimal, Decimal]], qty: Decimal) -> Decimal | None:
    """[쓰임] 매수 호가를 위에서부터 쌓아 ``qty`` 가 다 차는 가격. 호가가 없으면 None.
    [이유] 최우선 호가의 1% 아래로는 내려가지 않는다 —
           1% 안에서 다 못 차면 1% 안의 가장 낮은 호가."""
    if not bids:
        return None
    floor = bids[0][0] * (1 - FLOOR_PCT)
    stacked = ZERO
    price = bids[0][0]
    for level_price, volume in bids:
        if level_price < floor:
            break
        price = level_price
        stacked += volume
        if stacked >= qty:
            break
    return price


def upbit_level_price(bids: Sequence[Decimal], round_no: int) -> Decimal | None:
    """[쓰임] 업비트 매수 호가 가격들(높은 순) 중 ``2 + 2 × 회차`` 칸.

    1% 아래로는 내려가지 않는다."""
    if not bids:
        return None
    floor = bids[0] * (1 - FLOOR_PCT)
    allowed = [price for price in bids if price >= floor]
    return allowed[min(2 + 2 * round_no, len(allowed) - 1)]


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
    """업비트 창구 모양 (``exchange.upbit.UpbitClient`` 가 맞는다)."""

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
    """[쓰임] 업비트 한 종목 팔기 — 지정가 IOC 를 몇 번, 회차마다 두 칸씩 더 깊게(1% 안).
    다 팔거나 5,000 원 아래가 남으면 끝. → 진행 기록 줄.
    [이유] 업비트 주문 응답은 IOC 가 처리되기 전 상태(wait · 체결 0)라, 체결은 주문 조회로 확인한다.
           예전 기록도 다시 조회해 고친다 — 2026-10-13 첫 주문에 다 팔렸는데 0 으로 적혀 같은 수량을
           다시 팔려다 '잔고 부족(insufficient_funds_ask)' 을 몇 분마다 되풀이했다(사용자 발견)."""
    log: list[str] = []
    market = f"KRW-{item.symbol}"
    empty = False
    # 1 단계: 예전 IOC 주문 체결을 다시 조회해 고친다
    for order in item.orders:
        if order.status != "IOC":
            continue
        seen = client.get_order(order.order_id)
        if seen is not None:
            order.filled, order.amount, order.fee = seen.executed_volume, seen.funds, seen.paid_fee
    # 2 단계: 회차마다 IOC
    start = len(item.orders)
    for round_no in range(rounds):
        _asks, bids = client.orderbook(market)
        price = upbit_level_price(bids, round_no)
        remaining = item.remaining.quantize(D("0.00000001"), rounding=ROUND_DOWN)
        if price is None:
            log.append(f"{item.symbol} 매수 호가 없음 — 다음 확인 때 다시")
            break
        if remaining * price < UPBIT_MIN_KRW:
            break
        identifier = f"liq-{job_id}-{item.symbol}-{start + round_no}"
        try:
            result: UpbitFill | None = client.place_limit_ioc(
                market, "ask", remaining, price, identifier
            )
        except OrderStateUnknownError:
            result = None
        except AutoTradingError as exc:
            if "insufficient_funds_ask" not in str(exc):
                raise
            item.note = "업비트 잔고 없음 — 더 팔 것 없음"
            log.append(f"{item.symbol} 업비트에 남은 잔고 없음 — 끝으로 봄")
            empty = True
            break
        for _ in range(10):
            if result is not None and result.state in ("done", "cancel"):
                break
            sleep(0.5)
            result = client.get_order(identifier) or result
        order = Order(identifier, identifier, remaining, price, now_ms(), closed=True, status="IOC")
        if result is not None:
            order.filled, order.amount, order.fee = (
                result.executed_volume,
                result.funds,
                result.paid_fee,
            )
        item.orders.append(order)
        if item.remaining * price < UPBIT_MIN_KRW:
            break
        sleep(1.5)
    # 3 단계: 끝났나
    rest = item.remaining
    last_price = item.orders[-1].price if item.orders else ZERO
    done = empty or rest * last_price < UPBIT_MIN_KRW or rest <= 0
    item.status = "done" if done else "working"
    log.append(
        f"{item.symbol} {_n(item.sold)} 팜 · {item.amount:,.0f} 원"
        + (f" · 남음 {_n(rest)}" if item.status != "done" else "")
    )
    return log


class BinanceLike(Protocol):
    """바이낸스 현물 창구 모양 (옛 Vega)."""

    def order_book(self, symbol: str, limit: int = ...) -> tuple[list[Any], list[Any]]: ...
    def place_limit_ioc(
        self, symbol: str, side: str, quantity: Decimal, price: Decimal, client_order_id: str
    ) -> Any: ...
    def get_order(self, symbol: str, client_order_id: str) -> Any: ...


BINANCE_FINAL = frozenset({"FILLED", "CANCELED", "EXPIRED", "REJECTED", "EXPIRED_IN_MATCH"})
"""바이낸스 주문 상태 중 끝난 것."""


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
    """[쓰임] 바이낸스 현물(옛 Vega) 한 종목 팔기 — 업비트와 같은 방식:
    매수 호가 ``2 + 2 × 회차`` 칸(1% 안) 지정가 IOC.
    [이유] 체결은 주문 조회로 확인하고, 예전 주문도 다시 조회해 고친다(업비트 10-13 교훈)."""
    log: list[str] = []
    symbol = f"{item.symbol}USDT"
    for order in item.orders:
        if order.status != "IOC":
            continue
        seen = client.get_order(symbol, order.client_id)
        if seen is not None:
            order.filled, order.amount = seen.executed_qty, seen.quote_qty
    start = len(item.orders)
    for round_no in range(rounds):
        bids, _asks = client.order_book(symbol, limit=20)
        price = upbit_level_price([level[0] for level in bids], round_no)
        remaining = (item.remaining / step).to_integral_value(rounding=ROUND_DOWN) * step
        if price is None:
            log.append(f"{item.symbol} 바이낸스 매수 호가 없음 — 다음 확인 때 다시")
            break
        if remaining * price < min_notional:
            break
        identifier = f"liq-{job_id}-{item.symbol}-{start + round_no}"[:36]
        try:
            result = client.place_limit_ioc(symbol, "SELL", remaining, price, identifier)
        except OrderStateUnknownError:
            result = None
        for _ in range(10):
            if result is not None and result.status.value in BINANCE_FINAL:
                break
            sleep(0.5)
            result = client.get_order(symbol, identifier) or result
        order = Order(identifier, identifier, remaining, price, now_ms(), closed=True, status="IOC")
        if result is not None:
            order.filled, order.amount = result.executed_qty, result.quote_qty
            order.fee = result.quote_qty * D("0.001")
        item.orders.append(order)
        if item.remaining * price < min_notional:
            break
        sleep(1.5)
    rest = item.remaining
    last_price = item.orders[-1].price if item.orders else ZERO
    item.status = "done" if rest <= 0 or rest * last_price < min_notional else "working"
    log.append(
        f"{item.symbol} {_n(item.sold)} 팜 · {item.amount:,.2f} USDT"
        + (f" · 남음 {_n(rest)}" if item.status != "done" else "")
    )
    return log


class FuturesLike(Protocol):
    """바이낸스 선물 창구 모양 (옛 Altair)."""

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
    """[쓰임] 바이낸스 선물(옛 Altair) 포지션 닫기 — reduce-only 지정가 IOC,
    표시 가격 ± 0.2% × 회차(1% 까지).
    [이유] ``held`` = 지금 거래소의 그 포지션 절대 수량 — 남은 수량은 장부가 아니라 이것으로 정한다
           (비상 스톱이 먼저 닫았으면 0). reduce-only 라 포지션보다 많이 닫히지 않는다."""
    log: list[str] = []
    symbol = f"{item.symbol}USDC"
    if held <= 0:
        item.status = "done"
        item.note = item.note or "포지션 없음"
        log.append(f"{item.symbol} 선물 포지션 없음 — 끝")
        return log
    remaining = held
    start = len(item.orders)
    for round_no in range(rounds):
        qty = (remaining / step).to_integral_value(rounding=ROUND_DOWN) * step
        if qty <= 0:
            break
        mark = client.mark_price(symbol)
        pad = FUT_PAD * (round_no + 1)
        if item.side == "SELL":
            price = (mark * (1 - pad) / tick).to_integral_value(rounding=ROUND_DOWN) * tick
        else:
            price = (mark * (1 + pad) / tick).to_integral_value(rounding=ROUND_UP) * tick
        identifier = f"liq-{job_id}-{item.symbol}-f{start + round_no}"[:36]
        try:
            result = client.place_limit_ioc(
                symbol, item.side, qty, price, identifier, reduce_only=True
            )
        except OrderStateUnknownError:
            result = client.get_order(symbol, identifier)
        order = Order(identifier, identifier, qty, price, now_ms(), closed=True, status="IOC")
        if result is not None:
            order.filled = result.executed_qty
            order.amount = result.executed_qty * (result.avg_price or price)
            order.fee = getattr(result, "fee", ZERO) or ZERO
        item.orders.append(order)
        remaining -= order.filled
        if remaining <= 0:
            break
        sleep(1.0)
    item.status = "done" if remaining < step else "working"
    log.append(
        f"{item.symbol} 선물 {'롱' if item.side == 'SELL' else '숏'} {_n(item.sold)} 닫음"
        f" · {item.amount:,.2f} USDC"
        + (f" · 남음 {_n(remaining)}" if item.status != "done" else "")
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
    """토스 창구 모양 (``exchange.toss.TossTrader`` 가 맞는다)."""

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
    """[쓰임] 토스 한 종목 한 번 확인 — 주문 상태 갱신 → 10 분 넘게 덜 찼으면 취소 →
    남은 수량을 지금 호가로 다시. → 진행 기록 줄.
    [이유] 장이 닫혀 있으면 새 주문을 내지 않는다(당일 주문은 정규장 끝에 거래소가 취소한다).
           살아 있는 주문이 있으면 새로 내지 않는다 — 같은 수량을 두 번 팔지 않게."""
    log: list[str] = []
    # 1 단계: 낸 주문 상태 갱신 · 10 분 넘게 덜 찬 주문 취소
    for order in item.orders:
        if order.closed or not order.order_id:
            continue
        status = trader.order_status(order.order_id)
        order.status = status.status
        order.filled = status.filled_qty
        order.amount = status.filled_amount or ZERO
        order.fee = status.commission or ZERO
        if status.status and status.status not in _OPEN:
            order.closed = True
        elif (
            session_open
            and now_ms - order.placed_ms >= TOSS_REPLACE_MS
            and order.filled < order.qty
        ):
            result = trader.cancel(order.order_id)
            log.append(
                f"{item.symbol} 10 분 미체결 {order.qty - order.filled:f} 주 취소"
                + ("" if result.accepted else f" 실패({result.code})")
            )
    # 2 단계: 새로 낼 때인가
    remaining = item.remaining
    if remaining <= 0:
        item.status = "done"
        return log
    if any(not order.closed for order in item.orders):
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
    # 3 단계: 지금 호가로 당일 지정가 매도
    price = choose_price(trader.bids(item.symbol), remaining)
    if price is None:
        item.status = "waiting"
        log.append(f"{item.symbol} 매수 호가 없음 — 다음 확인 때 다시")
        return log
    price = floor_price(price, item.venue)
    client_id = f"liq-{job_id}-{item.symbol}-{len(item.orders)}"[:36]
    qty = int(remaining)
    try:
        result = trader.order(
            item.symbol, "SELL", qty, price, client_order_id=client_id, time_in_force="DAY"
        )
    except OrderStateUnknownError as exc:
        item.orders.append(
            Order("", client_id, D(qty), price, now_ms, closed=True, status="UNKNOWN")
        )
        item.status = "failed"
        item.note = f"주문 결과 모름 — 앱에서 확인 ({exc})"
        log.append(f"{item.symbol} {item.note}")
        return log
    except AutoTradingError as exc:
        item.status = "failed"
        item.note = f"주문 막힘: {exc}"
        log.append(f"{item.symbol} {item.note}")
        return log
    if not result.accepted:
        item.orders.append(
            Order("", client_id, D(qty), price, now_ms, closed=True, status=result.code)
        )
        item.status = "working"
        log.append(
            f"{item.symbol} 매도 거절 {result.code} {result.message[:60]} — 다음 확인 때 다시"
        )
        return log
    item.orders.append(Order(result.code, client_id, D(qty), price, now_ms))
    item.status = "working"
    currency_mark = "$" if item.venue == "toss_us" else ""
    log.append(f"{item.symbol} {qty} 주 매도 주문 · 지정가 {currency_mark}{price:,}")
    return log


def deneb_to_book(item: Item) -> tuple[Decimal, Decimal]:
    """[쓰임] 아직 Deneb 장부에 안 뺀 (수량, 달러 순수령액).
    장부에 쓴 뒤 부르는 쪽이 ``deneb_booked`` 를 올린다.
    [이유] 정리가 여러 번 이어져도 Deneb 장부에서 같은 수량을 두 번 빼지 않게."""
    closed_orders = [order for order in item.orders if order.closed and order.filled > 0]
    sold = sum((order.filled for order in closed_orders), ZERO)
    if sold <= 0 or item.deneb_part <= item.deneb_booked:
        return ZERO, ZERO
    take = min(sold, item.deneb_part) - item.deneb_booked
    if take <= 0:
        return ZERO, ZERO
    net = sum((order.amount - order.fee for order in closed_orders), ZERO)
    return take, net / sold * take


# ------------------------------------------------------------------ 보이기


def _n(value: Decimal) -> str:
    """[쓰임] 보이기용 수량 — 뒤 0 없이, 지수 표기 없이."""
    text = format(value, "f")
    if "." not in text:
        return text
    return text.rstrip("0").rstrip(".")


def job_text(job: Job) -> str:
    """[쓰임] 텔레그램 · 화면용 작업 요약 (계산만)."""
    head = f"🧯 전부 정리 · {scope_label(job.scope)} · {job.job_id}"
    lines = [head]
    status_words = {
        "waiting": "⏳ 장 열리면",
        "working": "🔄 진행 중",
        "done": "✅ 끝",
        "failed": "⚠ 실패",
    }
    for item in job.items:
        amount_text = {
            "KRW": f"{item.amount:,.0f} 원",
            "USDT": f"{item.amount:,.2f} USDT",
            "USDC": f"{item.amount:,.2f} USDC",
        }.get(item.currency, f"${item.amount:,.2f}")
        lines.append(
            f"{status_words.get(item.status, item.status)} {VENUE_NAMES[item.venue]} {item.symbol} "
            f"{_n(item.sold)}/{_n(item.want)} · {amount_text}"
            + (f" · {item.note}" if item.note else "")
        )
    if not job.items:
        lines.append("팔 것이 없어요")
    if job.buys_canceled:
        lines.append("취소한 매수 주문: " + ", ".join(job.buys_canceled))
    lines += [f"· {note}" for note in job.notes]
    group_names = ", ".join(GROUPS[group] for group in groups_of(job.scope))
    if group_names:
        lines.append(
            f"⛔ {group_names} 새 매수 정지 중 — 풀기: 텔레그램 '정지풀기' 또는 오르빗 설정"
        )
    return "\n".join(lines)
