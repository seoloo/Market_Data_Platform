"""엘리엇 보조 지표 알림 — **1 시간 단위** (BTC, 주문 없음). 2026-10-09 사용자.

사용자: "4시간 단위 말고 최소 1시간 단위로 변경해서 알림 보내는 걸로 변경해줘".

* 연구 엔진(EW-3e)은 '4h 봉 = 큰 파동 · 1h 봉 = 하위 파동 · 1m = 극값 순서' 로 고정돼 있다.
  엔진은 고치지 않고 **시간축을 4 배로 늘려** 넣는다(실제 1m 하나 → 엔진 1m 넷, 같은 값).
  그러면 실제 1h = 엔진 4h, 실제 15m = 엔진 1h 가 된다.
  - 꼭짓점 반전 크기는 2% → **1%** (파동 크기를 시간에 맞춰 줄임 · 결과 보기 전 정함).
  - 엔진의 120 일 창 = 실제 30 일, 실제 1m 은 40 일치를 받는다.
* 알림 조건 · 모양은 4h 판과 같다.
  - 상위 10 중 진입 패턴(충격파 5파 끝)이 하나라도 있을 때만 보낸다.
  - 내용: 큰 추세 해석 상승 · 하락 수, 패턴별, 1 등 해석 무효선.
* 실행: ``python -m scripts.ew_indicator_1h`` (상주, 매시 정각 1 분 30 초 뒤 확인).
  ``--once`` 는 한 번만 확인하고 끝낸다. 상태는 ``reports/ew_indicator_1h/``.
* 엘리엇 매매 규칙은 연구에서 기각됐다. 이 알림은 사람이 흐름을 볼 때 쓰는 참고 자료다.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
from scripts.audit_ew_edge_power import _aggregate, _chunks, _groups

from auto_trading.config.secrets import load_telegram_credentials
from auto_trading.core.enums import Timeframe
from auto_trading.core.errors import AutoTradingError
from auto_trading.core.types import Candle
from auto_trading.notify.channels import NotifyError, OutboxNotifier, TelegramNotifier
from auto_trading.notify.style import RULE, sub
from auto_trading.research.elliott.demo_setup import (
    EwSetup,
    agreement,
    scenario_view,
    setup_from_set,
)
from auto_trading.research.elliott.minute_path import replay_minute_groups_ladder
from auto_trading.research.elliott.pivots import EW0_THRESHOLD_PCT
from auto_trading.research.elliott.scanner import (
    EW3E_LOOKBACK_BARS,
    causal_triggers,
    scenario_lists,
)
from auto_trading.research.elliott.scenario_contract import to_json_dict
from auto_trading.research.elliott.scenario_export import export_scenario_set

REPO_ROOT = Path(__file__).resolve().parent.parent
SIGNAL_SYMBOL = "BTCUSDT"
H4 = Timeframe.H4.ms

OUT = REPO_ROOT / "reports" / "ew_indicator_1h"
TAG = "🌊 [엘리엇 보조지표 · BTC]"
STRETCH = 4
THRESHOLD = Decimal("0.01")
REAL_DAYS = 40
HOUR_MS = 3_600_000
DELAY_MS = 90_000


# ---------------------------------------------------------------- 스캔 · 알림 문구 · 상태
# 아래는 예전 ``scripts/demo_ew_btc.py`` (데모 선물 주문 실행기)에 있던 것을 글자 그대로 옮겼다.
# 실주문 · 모의 주문 코드를 걷어 낼 때(README 6-3) 그 파일은 지웠고, 이 알림이 쓰는 계산 · 문구 ·
# 상태만 남겼다(연구 숫자 그대로).


def _fmt(x: Decimal | float) -> str:
    return f"{Decimal(str(x)):,.1f}"


ScanRow = tuple[int, EwSetup | None, int, dict[str, Any]]
"""(발동 시각, 셋업, 시나리오 수, 알림용 정보 — 꼭짓점 종류·가격·상위 10 합의 수)."""


def scan(minutes: list[Candle], threshold: Decimal = EW0_THRESHOLD_PCT) -> list[ScanRow]:
    """마지막 연속 구간의 모든 발동. ``threshold`` 는 꼭짓점 반전 크기(기본 2%)."""
    four = _aggregate(iter(minutes), H4)
    hours = _aggregate(iter(minutes), Timeframe.H1.ms)
    chunk = _chunks(four)[-1]
    (replay,) = replay_minute_groups_ladder(
        chunk, _groups(iter(minutes), chunk), chunk[-1].close_ms, (threshold,)
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
            "view": scenario_view(data, ranks),
        }
        out.append((trig.close_ms, setup_from_set(data, ranks), len(data["scenarios"]), info))
    return out


def has_pattern(info: dict[str, Any]) -> bool:
    """알림 조건: 상위 10 중 데모가 들어가는 패턴(충격파 5파 끝)이 하나라도 있다.

    2026-10-09 사용자 "들어가는 패턴이 하나라도 있어야 알림을 보내야지" — 0 · 0 이면 보내지 않는다
    (사건 기록 ``events.jsonl`` 에는 그대로 남긴다)."""
    return bool(info) and int(info.get("up", 0)) + int(info.get("down", 0)) > 0


def view_lines(info: dict[str, Any]) -> list[str]:
    """보조 지표 줄: 상위 10 시나리오가 큰 추세를 어느 쪽으로 읽나 + 1 등 해석 · 무효 가격."""
    view = info.get("view") or {}
    if not view.get("top"):
        return ["읽히는 시나리오 없음 (규칙에 맞는 파동 해석이 없다)"]
    lines = [
        f"상위 {view['top']}개 시나리오의 큰 추세 해석",
        f"🔺 상승 {view['trend_up']} · 🔻 하락 {view['trend_down']}",
        sub(" · ".join(f"{k} {n}" for k, n in view["kinds"].items())),
    ]
    best = view.get("best")
    if best:
        line = f"1등 해석: {best['label']}"
        if best.get("invalid"):
            word = "아래로" if best.get("invalid_cmp") == "<=" else "위로"
            line += f" · {_fmt(Decimal(best['invalid']))} {word} 다시 가면 이 해석 무효"
        lines.append(sub(line))
    return lines


def no_setup_message(
    info: dict[str, Any], tag: str = "🌊 [데모·선물 Elliott]", *, demo: bool = True, tf: str = "4h"
) -> str:
    """새 꼭짓점이 확정됐지만 들어가지 않은 이유 + 보조 지표 요약.

    2026-10-09 사용자: 상위 10 시나리오를 '내가 판단할 보조 지표' 로 — 상승 몇 개 · 하락 몇 개.
    합의 0 · 0 이어도 알림이 가는 이유: **새 4h 꼭짓점이 확정될 때마다** 보낸다(진입 여부와 무관).
    """
    return "\n".join(
        [
            f"{tag} {tf} {info['pivot']} 확정 @{_fmt(Decimal(info['price']))}",
            RULE,
            *view_lines(info),
            *(
                [
                    "",
                    sub(
                        f"데모 진입 조건(충격파 5파 끝 같은 방향 6개 이상): "
                        f"상승 {info['up']} · 하락 {info['down']} → 진입 안 함"
                    ),
                ]
                if demo
                else [sub("참고용 — 엘리엇 매매 규칙은 연구에서 기각됐다")]
            ),
        ]
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


def fetch_real_minutes(client: httpx.Client | None, now_ms: int) -> list[list[Any]]:
    """실제 BTCUSDT 무기한 1m (끝난 1h 까지)."""
    start = now_ms - REAL_DAYS * 86_400_000
    start -= start % HOUR_MS
    end = now_ms - now_ms % HOUR_MS
    rows: list[list[Any]] = []
    if client is None:
        return rows
    cursor = start
    while cursor < end:
        batch = client.get(
            "https://fapi.binance.com/fapi/v1/klines",
            params={"symbol": SIGNAL_SYMBOL, "interval": "1m", "startTime": cursor, "limit": 1500},
        ).json()
        if not batch:
            break
        rows.extend(r for r in batch if int(r[0]) < end)
        cursor = int(batch[-1][0]) + 60_000
    return rows


def stretch(rows: list[list[Any]]) -> list[Candle]:
    """실제 1m 하나를 엔진 1m 넷으로 (시간 × 4, 값 그대로 · 거래량 ÷ 4) — 순수."""
    out: list[Candle] = []
    for r in rows:
        base = int(r[0]) * STRETCH
        vol = Decimal(str(r[5])) / STRETCH
        o, h, lo, c = (Decimal(str(x)) for x in r[1:5])
        for j in range(STRETCH):
            start = base + j * 60_000
            out.append(Candle(start, start + 59_999, o, h, lo, c, vol))
    return out


def real_ms(engine_ms: int) -> int:
    return (engine_ms + 1) // STRETCH - 1


def check_once(
    client: httpx.Client | None, store: Store, notify: Callable[[str], None], now_ms: int
) -> int:
    """새로 확정된 1h 꼭짓점을 알린다. 보낸 수."""
    rows = scan(stretch(fetch_real_minutes(client, now_ms)), THRESHOLD)
    state = store.load()
    last = int(state.get("last_trigger_ms", 0))
    sent = 0
    if not last:
        state["last_trigger_ms"] = real_ms(rows[-1][0]) if rows else now_ms
        store.event("init", last_trigger_ms=state["last_trigger_ms"])
    else:
        for row in rows:
            at = real_ms(row[0])
            if at <= last:
                continue
            store.event("trigger", close_ms=at, scenarios=row[2], **row[3])
            if has_pattern(row[3]):
                notify(no_setup_message(row[3], TAG, demo=False, tf="1h"))
                sent += 1
            state["last_trigger_ms"] = at
    store.save(state)
    return sent


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    if not args.once and not _lock(OUT):
        print("1h 보조 지표가 이미 돌고 있다 — 끝낸다")
        return 0
    client = httpx.Client(timeout=30)
    store = Store(OUT)
    notify = Notify(OUT, telegram=True)
    next_check = 0
    while True:
        now = int(time.time() * 1000)
        if now >= next_check:
            try:
                n = check_once(client, store, notify, now)
                print(f"확인 · 알림 {n}", flush=True)
            except (AutoTradingError, httpx.HTTPError, ValueError, KeyError) as exc:
                store.event("error", error=str(exc))
                print(f"오류 (다음 확인에서 다시): {exc}", flush=True)
            next_check = now - now % HOUR_MS + HOUR_MS + DELAY_MS
        if args.once:
            return 0
        time.sleep(20)


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
