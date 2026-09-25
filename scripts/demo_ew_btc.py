"""Elliott + 피보나치 2·4파 되돌림 — BTC 선물 **데모 전용** 상주 실행기 (docs/research/56). 가짜 돈.

* 신호: 실거래소 공개 BTCUSDT 무기한 1m(90 일) → 연구 스캐너(EW-3d′) →
  시나리오 묶음 상위 10 중 6 개 이상 같은 방향 "충격파 5파 종료" 합의 → :func:`setup_from_set`
  (0.5 되돌림 진입 · 손절 = 충격파 시작 · 목표 1.618).
* 주문: 데모 **BTCUSDC** 무기한
  (21:08 Phase 7 훈련이 같은 데모 계좌의 BTCUSDT 를 쓰므로 겹치지 않게).
  레버리지 설정 5x · 교차 증거금 · 크기 = 손절에 닿으면 USDC 잔고의 1% 손실(수수료 포함).
* 보호: 데모 거래소는 포지션 없이 closePosition 스톱을 받지 않는다(−4509, 2026-09-25 실측).
  그래서 **2 초마다 진입 주문을 확인하고 체결되면 즉시**
  손절(STOP_MARKET)·목표(TAKE_PROFIT_MARKET)를 건다.
* 알림: 텔레그램 — 예약·체결·취소·청산마다 짧게 (무슨 연구·무엇을·왜).

실행: ``python scripts/demo_ew_btc.py`` (로그온 때 스케줄러로 띄운다. 이미 떠 있으면 바로 끝난다).
``--once``: 스캔·점검을 한 번만 하고 끝낸다 (주문은 낸다). ``--scan-only``: 주문 없이 셋업만 출력.
기록: ``reports/demo_ew/state.json``(상태), ``events.jsonl``(사건), ``outbox.log``(알림 사본).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
import time
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
for p in (REPO_ROOT, REPO_ROOT / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import httpx  # noqa: E402
from scripts.audit_ew_edge_power import _aggregate, _chunks, _groups  # noqa: E402

from auto_trading.config.secrets import (  # noqa: E402
    BinanceCredentials,
    load_telegram_credentials,
    read_env_file,
)
from auto_trading.core.enums import Timeframe  # noqa: E402
from auto_trading.core.errors import AutoTradingError, OrderRejectedError  # noqa: E402
from auto_trading.core.types import Candle  # noqa: E402
from auto_trading.exchange.binance_usdm_testnet import BinanceUsdmTestnet  # noqa: E402
from auto_trading.exchange.futures import OrderStatus  # noqa: E402
from auto_trading.notify.channels import NotifyError, OutboxNotifier, TelegramNotifier  # noqa: E402
from auto_trading.research.elliott.demo_setup import (  # noqa: E402
    EwSetup,
    agreement,
    setup_from_set,
)
from auto_trading.research.elliott.minute_path import replay_minute_groups_ladder  # noqa: E402
from auto_trading.research.elliott.pivots import EW0_THRESHOLD_PCT  # noqa: E402
from auto_trading.research.elliott.scanner import (  # noqa: E402
    EW3E_LOOKBACK_BARS,
    causal_triggers,
    scenario_lists,
)
from auto_trading.research.elliott.scenario_contract import to_json_dict  # noqa: E402
from auto_trading.research.elliott.scenario_export import export_scenario_set  # noqa: E402

OUT = REPO_ROOT / "reports" / "demo_ew"
SYMBOL = "BTCUSDC"
SIGNAL_SYMBOL = "BTCUSDT"
ASSET = "USDC"
LEVERAGE = 5
RISK = Decimal("0.01")
COST_RATE = Decimal("0.0007")
"""진입 maker + 청산 taker + 여유 (명목 대비) — 크기 계산에만 쓴다."""
SETUP_TTL_MS = 10 * 86_400_000
MAX_HOLD_MS = 30 * 86_400_000
POLL_S = 2.0
SCAN_DELAY_MS = 90_000
"""4h 마감 뒤 이만큼 기다렸다가 스캔한다 (공개 자료 반영 여유)."""
LOOKBACK_DAYS = 150
"""EW-3e 시작 창 120 일 + 여유 (문서 58)."""
H4 = Timeframe.H4.ms
TAG = "[데모·선물 BTC 5x] 연구 56 Elliott+피보 2·4파 되돌림"


def _fmt(x: Decimal | float) -> str:
    return f"{Decimal(str(x)):,.1f}"


def _date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d %H:%M UTC")


# ---------------------------------------------------------------- 신호 (실거래소 공개 자료)


def fetch_minutes(client: httpx.Client, now_ms: int) -> list[Candle]:
    start = now_ms - LOOKBACK_DAYS * 86_400_000
    start -= start % H4
    end = now_ms - now_ms % H4  # 진행 중 4h 는 쓰지 않는다
    rows: list[list[Any]] = []
    cursor = start
    while cursor < end:
        batch = client.get(
            "https://fapi.binance.com/fapi/v1/klines",
            params={"symbol": SIGNAL_SYMBOL, "interval": "1m", "startTime": cursor, "limit": 1500},
        ).json()
        if not batch:
            break
        rows.extend(batch)
        cursor = int(batch[-1][0]) + 60_000
    return [
        Candle(
            open_ms=int(r[0]),
            close_ms=int(r[0]) + 59_999,
            open=Decimal(r[1]),
            high=Decimal(r[2]),
            low=Decimal(r[3]),
            close=Decimal(r[4]),
            volume=Decimal(r[5]),
        )
        for r in rows
        if int(r[0]) < end
    ]


ScanRow = tuple[int, EwSetup | None, int, dict[str, Any]]
"""(발동 시각, 셋업, 시나리오 수, 알림용 정보 — 꼭짓점 종류·가격·상위 10 합의 수)."""


def scan(minutes: list[Candle]) -> list[ScanRow]:
    """마지막 연속 구간의 모든 발동."""
    four = _aggregate(iter(minutes), H4)
    hours = _aggregate(iter(minutes), Timeframe.H1.ms)
    chunk = _chunks(four)[-1]
    (replay,) = replay_minute_groups_ladder(
        chunk, _groups(iter(minutes), chunk), chunk[-1].close_ms, (EW0_THRESHOLD_PCT,)
    )
    ch_hours = [h for h in hours if chunk[0].open_ms <= h.open_ms <= chunk[-1].close_ms]
    triggers = causal_triggers(replay, ch_hours, chunk, all_new_pivots=True)  # EW-3e
    out = []
    lists = scenario_lists(triggers, ch_hours, lookback_bars=EW3E_LOOKBACK_BARS)
    for trig, scen in zip(triggers, lists, strict=True):
        data_set, ranks = export_scenario_set(
            trig, scen, venue="binance_usdm", instrument=SIGNAL_SYMBOL, rule_profile="EW-3e"
        )
        data = to_json_dict(data_set)
        top, up, down = agreement(data, ranks)
        end = trig.segment[-1]
        info = {
            "pivot": "고점" if end.kind.value == "high" else "저점",
            "price": str(end.price),
            "top": top,
            "up": up,
            "down": down,
        }
        out.append((trig.close_ms, setup_from_set(data, ranks), len(data["scenarios"]), info))
    return out


# ---------------------------------------------------------------- 상태·알림


def no_setup_message(info: dict[str, Any]) -> str:
    """새 꼭짓점이 확정됐지만 들어가지 않은 이유 한 줄."""
    return (
        f"[데모·선물 Elliott] 4h {info['pivot']} 확정 @{_fmt(Decimal(info['price']))}"
        " — 진입 안 함\n"
        f"이유: 상위 {info['top']}개 시나리오 중 '충격파 5파 종료' 합의 "
        f"상승 {info['up']} · 하락 {info['down']} (같은 방향 6개 이상 필요)"
    )


class Store:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.path = root / "state.json"

    def load(self) -> dict[str, Any]:
        if self.path.exists():
            return dict(json.loads(self.path.read_text(encoding="utf-8")))
        return {"phase": "IDLE", "last_trigger_ms": 0}

    def save(self, state: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def event(self, kind: str, **data: Any) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        row = {"kind": kind, "at_ms": int(time.time() * 1000), **data}
        with (self.root / "events.jsonl").open("a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


class Notify:
    def __init__(self, root: Path, *, telegram: bool) -> None:
        creds = load_telegram_credentials(REPO_ROOT / ".env") if telegram else None
        self._telegram = TelegramNotifier(creds) if creds else None
        self._outbox = OutboxNotifier(root / "outbox.log")

    def __call__(self, text: str) -> None:
        print(text, flush=True)
        self._outbox.send(text)
        if self._telegram is not None:
            try:
                self._telegram.send(text)
            except NotifyError as exc:
                self._outbox.send(f"(텔레그램 실패: {exc})")


def _setup_row(s: EwSetup) -> dict[str, Any]:
    row = asdict(s)
    return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in row.items()}


# ---------------------------------------------------------------- 실행기


class EwDemoRunner:
    def __init__(
        self,
        api: BinanceUsdmTestnet,
        store: Store,
        notify: Callable[[str], None],
        *,
        clock: Callable[[], int],
    ) -> None:
        self.api = api
        self.store = store
        self.notify = notify
        self.clock = clock
        self.state = store.load()
        self.filters = api.filters(SYMBOL)

    # -------------------------------------------------- 공통

    def _save(self) -> None:
        self.store.save(self.state)

    def _position(self) -> Decimal:
        pos = self.api.account().positions.get(SYMBOL)
        return Decimal(0) if pos is None else pos.quantity

    def _tick(self, price: Decimal) -> Decimal:
        t = self.filters.tick_size
        return (price / t).quantize(Decimal(1), rounding=ROUND_DOWN) * t

    def _ids(self, as_of_ms: int) -> dict[str, str]:
        base = f"ew56-{as_of_ms // 1000}"
        return {"entry": f"{base}-E", "stop": f"{base}-S", "tp": f"{base}-T", "exit": f"{base}-X"}

    # -------------------------------------------------- 셋업 → 예약

    def place_setup(self, s: EwSetup) -> None:
        side = "BUY" if s.direction > 0 else "SELL"
        entry, stop, target = self._tick(s.entry), self._tick(s.stop), self._tick(s.target)
        mark = self.api.mark_price(SYMBOL)
        if (mark - entry) * s.direction <= 0:
            self.store.event("skip_passed", setup=_setup_row(s), mark=str(mark))
            self.notify(
                f"{TAG}\n셋업 건너뜀: 확정 시점에 이미 0.5 되돌림({_fmt(entry)})을 지났다 "
                f"(현재 {_fmt(mark)})"
            )
            return
        equity = self.api.asset_balance(ASSET)
        per_unit = abs(entry - stop) + entry * COST_RATE
        qty = (equity * RISK / per_unit / self.filters.step_size).quantize(
            Decimal(1), rounding=ROUND_DOWN
        ) * self.filters.step_size
        if qty * entry < self.filters.min_notional:
            self.store.event("skip_small", setup=_setup_row(s), qty=str(qty))
            self.notify(f"{TAG}\n셋업 건너뜀: 1% 위험 크기 {qty} 가 최소 주문 미만")
            return
        self.api.set_margin_type(SYMBOL, "CROSSED")
        self.api.set_leverage(SYMBOL, LEVERAGE)
        ids = self._ids(s.as_of_ms)
        try:
            result = self.api.place_limit_order(SYMBOL, side, qty, entry, ids["entry"])
        except OrderRejectedError as exc:
            self.store.event("entry_rejected", error=str(exc))
            self.notify(f"{TAG}\n예약 거절: {exc}")
            return
        self.state.update(
            phase="PENDING",
            setup=_setup_row(s),
            ids=ids,
            qty=str(qty),
            entry=str(entry),
            stop=str(stop),
            target=str(target),
            equity_at_setup=str(equity),
            placed_ms=self.clock(),
        )
        self._save()
        self.store.event("placed", setup=_setup_row(s), qty=str(qty), status=str(result.status))
        way = "롱" if s.direction > 0 else "숏"
        leg = "상승" if s.direction > 0 else "하락"
        self.notify(
            f"{TAG}\n{SYMBOL} {way} 지정가 예약 {qty} @{_fmt(entry)} (충격파 0.5 되돌림)\n"
            f"손절 {_fmt(stop)} (충격파 시작 — 넘으면 해석 무효) · 목표 {_fmt(target)} (1.618)\n"
            f"이유: 상위 {s.top_n}개 시나리오 중 {s.agree}개가 {leg} 충격파 5파 종료"
            f"({_fmt(s.a)}→{_fmt(s.b)}, {s.impulse_pct:.1%})로 읽음 → 2·4파 되돌림 진입. 위험 1%"
        )

    def cancel_pending(self, why: str) -> None:
        ids = self.state["ids"]
        with contextlib.suppress(OrderRejectedError):  # 이미 끝났다 — 아래 조회가 판단한다
            self.api.cancel_order(SYMBOL, ids["entry"])
        result = self.api.get_order(SYMBOL, ids["entry"])
        if result is not None and result.executed_qty > 0:
            self.on_entry_fill(result.executed_qty, result.avg_price)
            return
        self.store.event("cancelled", why=why)
        self.notify(f"[데모·선물] {SYMBOL} 예약 취소: {why}")
        self.state = {"phase": "IDLE", "last_trigger_ms": self.state.get("last_trigger_ms", 0)}
        self._save()

    # -------------------------------------------------- 체결 → 보호

    def protect(self) -> list[str]:
        """손절·목표를 건다 (이미 있으면 건너뛴다). 건 것들의 이름."""
        ids = self.state["ids"]
        close_side = "SELL" if Decimal(self.state["qty"]) and self._long() else "BUY"
        placed = []
        for name, place in (
            (
                "stop",
                lambda: self.api.place_stop_market(
                    SYMBOL, close_side, Decimal(self.state["stop"]), ids["stop"]
                ),
            ),
            (
                "tp",
                lambda: self.api.place_take_profit_market(
                    SYMBOL, close_side, Decimal(self.state["target"]), ids["tp"]
                ),
            ),
        ):
            existing = self.api.get_stop(SYMBOL, ids[name])
            if existing is not None and existing.status is OrderStatus.NEW:
                continue
            place()
            placed.append(name)
        return placed

    def _long(self) -> bool:
        return int(self.state["setup"]["direction"]) > 0

    def on_entry_fill(self, qty: Decimal, price: Decimal) -> None:
        t0 = self.clock()
        placed = self.protect()
        gap_ms = self.clock() - t0
        self.state.update(phase="OPEN", opened_ms=t0, entry_fill=str(price), filled_qty=str(qty))
        self._save()
        self.store.event(
            "entry_filled", qty=str(qty), price=str(price), protect_ms=gap_ms, placed=placed
        )
        way = "롱" if self._long() else "숏"
        self.notify(
            f"[데모·선물] {SYMBOL} {way} 진입 체결 {qty} @{_fmt(price)} · "
            f"손절 {_fmt(Decimal(self.state['stop']))}·"
            f"목표 {_fmt(Decimal(self.state['target']))} 걸림"
            f" (보호까지 {gap_ms / 1000:.1f}초)"
        )

    # -------------------------------------------------- 청산

    def on_flat(self) -> None:
        ids = self.state["ids"]
        stop = self.api.get_stop(SYMBOL, ids["stop"])
        tp = self.api.get_stop(SYMBOL, ids["tp"])
        how = (
            "손절"
            if stop is not None and stop.executed_qty > 0
            else "목표"
            if tp is not None and tp.executed_qty > 0
            else "청산"
        )
        for name in ("stop", "tp"):
            with contextlib.suppress(OrderRejectedError, AutoTradingError):
                self.api.cancel_stop(SYMBOL, ids[name])
        with_fill = stop if how == "손절" else tp
        price = with_fill.avg_price if with_fill is not None and with_fill.avg_price else None
        entry = Decimal(self.state.get("entry_fill", self.state["entry"]))
        qty = Decimal(self.state.get("filled_qty", self.state["qty"]))
        pnl = None if price is None else (price - entry) * qty * (1 if self._long() else -1)
        risk = Decimal(self.state["equity_at_setup"]) * RISK
        r_mult = None if pnl is None else pnl / risk
        self.store.event("closed", how=how, price=str(price), pnl=str(pnl), r=str(r_mult))
        tail = "" if pnl is None else f" · 손익 {pnl:+.1f} {ASSET} ({r_mult:+.2f}R, 수수료 제외)"
        self.notify(f"[데모·선물] {SYMBOL} 청산 — {how} @{_fmt(price) if price else '?'}{tail}")
        self.state = {"phase": "IDLE", "last_trigger_ms": self.state.get("last_trigger_ms", 0)}
        self._save()

    def time_exit(self) -> None:
        ids = self.state["ids"]
        pos = self._position()
        if pos:
            side = "SELL" if pos > 0 else "BUY"
            self.api.place_market_order(SYMBOL, side, abs(pos), ids["exit"], reduce_only=True)
        self.store.event("time_exit")
        self.notify(f"[데모·선물] {SYMBOL} 30일 보유 한도 — 시장가 청산")
        self.on_flat()

    # -------------------------------------------------- 한 번 점검

    def check(self) -> None:
        phase = self.state.get("phase", "IDLE")
        now = self.clock()
        if phase == "PENDING":
            ids = self.state["ids"]
            result = self.api.get_order(SYMBOL, ids["entry"])
            if result is None:
                self.cancel_pending("거래소에 예약 주문이 없음")
                return
            if result.executed_qty > 0:
                self.on_entry_fill(result.executed_qty, result.avg_price)
                return
            if result.status in (OrderStatus.EXPIRED, OrderStatus.CANCELED, OrderStatus.REJECTED):
                self.cancel_pending(
                    f"예약 주문 {result.status.value} (post-only 가 즉시 체결 가격이었을 수 있음)"
                )
                return
            b = Decimal(self.state["setup"]["b"])
            mark = self.api.mark_price(SYMBOL)
            if (mark - b) * (1 if self._long() else -1) > 0:
                self.cancel_pending(f"되돌림 전에 {_fmt(b)} 너머로 — 충격파가 더 이어짐(해석 변경)")
            elif now - int(self.state["placed_ms"]) > SETUP_TTL_MS:
                self.cancel_pending("10일 동안 0.5 되돌림에 닿지 않음")
        elif phase == "OPEN":
            pos = self._position()
            if pos == 0:
                self.on_flat()
                return
            filled = Decimal(self.state.get("filled_qty", "0"))
            if abs(pos) != filled:  # 부분 체결이 이어졌다
                self.state["filled_qty"] = str(abs(pos))
                self._save()
            self.protect()  # 없어진 보호 주문이 있으면 다시 건다
            if now - int(self.state["opened_ms"]) > MAX_HOLD_MS:
                self.time_exit()

    def on_scan(self, results: list[Any]) -> None:
        """``results``: :data:`ScanRow` 목록 (테스트는 앞의 세 칸만 줘도 된다)."""
        last = int(self.state.get("last_trigger_ms", 0))
        if not last:
            # 첫 실행: 지난 발동(최대 90 일 전)은 기록만 한다 — 켠 뒤 확정되는 꼭짓점부터 쓴다
            self.state["last_trigger_ms"] = results[-1][0] if results else self.clock()
            self._save()
            self.store.event("init", last_trigger_ms=self.state["last_trigger_ms"])
            return
        new = [r for r in results if r[0] > last]
        if not new:
            return
        self.state["last_trigger_ms"] = new[-1][0]
        self._save()
        for row in new:
            close_ms, setup, n = row[0], row[1], row[2]
            info = row[3] if len(row) > 3 else {}
            self.store.event(
                "trigger",
                close_ms=close_ms,
                scenarios=n,
                setup=None if setup is None else _setup_row(setup),
                **info,
            )
            if setup is None and info:
                self.notify(no_setup_message(info))
        setups = [row[1] for row in new if row[1] is not None]
        if not setups:
            return
        latest = setups[-1]
        phase = self.state.get("phase", "IDLE")
        if phase == "OPEN":
            self.store.event("setup_ignored_open", setup=_setup_row(latest))
            return
        if phase == "PENDING":
            self.cancel_pending("새 셋업으로 교체")
        if self.state.get("phase") == "IDLE":
            self.place_setup(latest)

    def heartbeat(self, now_ms: int) -> None:
        """스캔을 마친 시각 — 일일 점검이 실행기가 살아 있는지 본다."""
        self.state["last_scan_ms"] = now_ms
        self._save()

    def reconcile(self) -> None:
        """시작할 때: 거래소 상태와 기록을 맞춘다. 보호 없는 포지션이 있으면 바로 보호한다."""
        pos = self._position()
        phase = self.state.get("phase", "IDLE")
        if phase in ("IDLE",) and pos:
            self.store.event("orphan_position", qty=str(pos))
            side = "SELL" if pos > 0 else "BUY"
            self.api.place_market_order(
                SYMBOL, side, abs(pos), f"ew56-orphan-{self.clock() // 1000}", reduce_only=True
            )
            self.notify(f"[데모·선물] {SYMBOL} 기록에 없는 포지션 {pos} — 시장가로 정리")
        elif phase == "OPEN" and pos:
            placed = self.protect()
            if placed:
                self.notify(f"[데모·선물] 재시작: 빠진 보호 주문 {placed} 다시 걸음")
        self.check()


_LOCK_HANDLE: Any = None
"""프로세스가 사는 동안 잠금 파일을 열어 둔다 — 죽으면 OS 가 잠금을 푼다."""


def _lock(root: Path) -> bool:
    """단일 실행 잠금. 이미 다른 실행기가 잡고 있으면 False.

    ``os.kill(pid, 0)`` 로 살아 있는지 보는 방식은 쓰지 않는다
    — Windows 에서는 그 프로세스를 **종료**시킨다.
    """
    global _LOCK_HANDLE
    root.mkdir(parents=True, exist_ok=True)
    handle = (root / "runner.lock").open("a+")
    try:
        if sys.platform == "win32":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return False
    _LOCK_HANDLE = handle
    return True


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--scan-only", action="store_true")
    args = parser.parse_args(argv)
    client = httpx.Client(timeout=20)
    if args.scan_only:
        for close_ms, s, n, info in scan(fetch_minutes(client, int(time.time() * 1000)))[-10:]:
            print(_date(close_ms), n, info, None if s is None else _setup_row(s))
        return 0
    if not args.once and not _lock(OUT):
        print("실행기가 이미 돌고 있다 — 끝낸다")
        return 0
    env = read_env_file(REPO_ROOT / ".env")
    api = BinanceUsdmTestnet(
        BinanceCredentials(env["BINANCE_TESTNET_API_KEY"], env["BINANCE_TESTNET_API_SECRET"])
    )
    store = Store(OUT)
    runner = EwDemoRunner(
        api, store, Notify(OUT, telegram=True), clock=lambda: int(time.time() * 1000)
    )
    runner.reconcile()
    next_scan = 0
    while True:
        now = int(time.time() * 1000)
        try:
            if now >= next_scan:
                runner.on_scan(scan(fetch_minutes(client, now)))
                runner.heartbeat(now)
                boundary = now - now % H4 + H4
                next_scan = boundary + SCAN_DELAY_MS
            runner.check()
        except (AutoTradingError, httpx.HTTPError) as exc:
            store.event("error", error=str(exc))
            print(f"오류 (다음 점검에서 다시): {exc}", flush=True)
        if args.once:
            return 0
        time.sleep(POLL_S)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
