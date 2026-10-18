"""☄️ Altair **실거래** — 바이낸스 USDⓈ-M 선물(USDC), 데모와 같은 규칙 · 같은 주문 코드.

    python -m scripts.altair_live plan     # 지금 낼 주문 미리 보기 (주문 없음, 키만 있으면 됨)
    python -m scripts.altair_live tick     # 할 차례면 실행 (예약 AutoTrading_Altair 가 5 분마다)
    python -m scripts.altair_live status
    python -m scripts.altair_live unhalt   # 낙폭 30% 정지를 손으로 풀고 고점을 지금으로

사용자 (2026-10-16): "실행기 작업해줘" — 데모(scripts/altair_demo) 2 주 이상 본 뒤 켠다.

* 규칙 · 주문은 :mod:`scripts.altair_demo` 그대로(롱 = T/v2 × 4 · 비상 스톱 −20%, 숏 = 232 · 2 배,
  지정가 IOC, 비상 STOP_MARKET 만 시장가 예외). 다른 점만 여기 있다.
* **켜기 스위치 둘**: ``.env`` ``BINANCE_FUTURES_LIVE_TRADING_ENABLED=true`` **그리고** 설정
  ``live.altair`` (오르빗 설정 화면, 기본 꺼짐). 하나라도 꺼져 있으면 아무 주문도 내지 않는다
  (걸려 있는 비상 스톱은 거래소에 그대로).
* **다중 자산 모드 필수**(2026-10-16 사용자 결정, 인계 D-①): Vega 가 옮기는 USDT 가 USDC 계약의
  증거금으로 그대로 쓰인다. **장부 = 선물 지갑 전체 증거금 잔고(USDT + USDC, 미실현 포함)** —
  그 지갑에 Altair 돈만 둔다. 다중 자산 모드가 아니거나 헤지 모드면 시작하지 않는다.
* **입금 · 출금은 손익이 아니다**: 선물 지갑 이체(``TRANSFER``)를 단위(NAV)로 바꿔 낙폭을 잰다
  (Vega 와 같은 방식).
* **낙폭 30% 정지**: 단위 가치가 고점에서 30% 빠지면 새 노출(롱 늘리기 · 새 숏) 금지 —
  줄이기 · 정리 · 스톱은 그대로. **BTC 롱 목표가 0(추세 꺼짐)이 된 뒤 다시 0 보다 커지면(추세
  다시 켜짐) 스스로 풀고 고점을 그때 단위 가치로** 다시 잡는다(문서 266 TREND, 사용자 2026-10-18
  "추세가 꺼졌다 다시 켜지면 품으로 변경해줘"). 손으로는 ``unhalt``.
  전체 정지 · Altair 그룹 정지(전부 정리 · 텔레그램 정지) 동안도 새 노출 금지.
* 상태 ``data/altair_live/state.json``, 알림 기록 ``reports/altair_live/outbox.log``.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

from auto_trading.config.secrets import load_telegram_credentials
from auto_trading.core.errors import AutoTradingError
from auto_trading.exchange.binance_usdm_live import BinanceUsdmLive, load_live_futures
from auto_trading.notify.channels import NotifyError, OutboxNotifier, TelegramNotifier
from auto_trading.ops import user_settings
from auto_trading.ops.ag_short_paper import DAY, H4
from auto_trading.ops.kill_switch import halted_for
from scripts import altair_demo as core

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data" / "altair_live"
STATE = DATA / "state.json"
REPORTS = REPO / "reports" / "altair_live"
HALT = REPO / "data" / "HALT"
SETTING = "live.altair"
DD_HALT = 0.30
MIN_EQUITY = 50.0
"""USDC 장부가 이보다 작으면 돌지 않는다(입금 전)."""
TITLE = "☄️ Altair 실거래 · 바이낸스 선물"
MINUTE = 60_000


# ------------------------------------------------------------------ 바깥


def _client() -> BinanceUsdmLive:
    creds, enabled = load_live_futures(REPO / ".env")
    return BinanceUsdmLive(creds, trading_enabled=enabled)


def _enabled() -> tuple[bool, str]:
    """(켜짐, 꺼진 이유)."""
    if not user_settings.get(REPO, SETTING):
        return False, f"설정 {SETTING} 꺼짐"
    try:
        _creds, ok = load_live_futures(REPO / ".env")
    except AutoTradingError as exc:
        return False, str(exc)
    return (True, "") if ok else (False, "BINANCE_FUTURES_LIVE_TRADING_ENABLED 꺼짐")


def _notify(text: str) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    OutboxNotifier(REPORTS / "outbox.log").send(text)
    creds = load_telegram_credentials(REPO / ".env")
    if creds is None:
        return
    try:
        TelegramNotifier(creds).send(text)
    except NotifyError as exc:
        print(f"텔레그램 실패: {exc}")


def _load() -> dict[str, Any]:
    try:
        return dict(json.loads(STATE.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return {}


def _save(st: dict[str, Any]) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(STATE)


# ------------------------------------------------------------------ 계산 (순수)


def update_nav(
    st: dict[str, Any], eq: float, flows: list[tuple[int, float, str]]
) -> tuple[float, float, list[str]]:
    """새 이체를 단위로 바꾸고 (단위 가치, 고점 대비 낙폭, 알림 줄).

    처음엔 단위 = 장부(가치 1). 이체 F 가 새로 보이면 F 를 뺀 장부로 가치를 잰 뒤
    F ÷ 가치 만큼 단위를 더하고 뺀다 — 입금 · 출금이 낙폭 · 손익으로 잡히지 않게."""
    seen = set(st.get("flows_seen") or [])
    new = [(t, a, i) for t, a, i in flows if i not in seen]
    msgs: list[str] = []
    units = float(st.get("units") or 0.0)
    total = sum(a for _t, a, _i in new)
    if units <= 0:
        units = eq if eq > 0 else 0.0
    elif new:
        base = eq - total
        nav_before = base / units if base > 0 else float(st.get("nav_peak") or 1.0)
        units = max(0.0, units + total / nav_before)
    for _t, a, _i in new:
        msgs.append(f"💵 선물 지갑 {'입금' if a > 0 else '출금'} {abs(a):,.2f} 달러 — 손익 아님")
    st["units"] = units
    st["flows_seen"] = [*seen, *(i for _t, _a, i in new)][-500:]
    if flows:
        st["flow_ms"] = max(int(st.get("flow_ms", 0)), max(t for t, _a, _i in flows))
    nav = eq / units if units > 0 else 1.0
    peak = max(float(st.get("nav_peak") or nav), nav)
    st["nav_peak"] = peak
    dd = 1 - nav / peak if peak > 0 else 0.0
    if dd >= DD_HALT and not st.get("dd_halt"):
        st["dd_halt"] = f"계좌 낙폭 {dd:.0%} (30% 정지)"
        st["halt_off_seen"] = _f((st.get("long_w") or {}).get("BTC")) <= 0
        msgs.append(
            f"⛔ 계좌 낙폭 {dd:.0%} — 새 노출(롱 늘리기 · 새 숏) 멈춤."
            " 줄이기 · 정리 · 스톱은 그대로."
            " BTC 추세가 꺼졌다 다시 켜지면 스스로 풀림"
            " (손으로: python -m scripts.altair_live unhalt)"
        )
    return nav, dd, msgs


def _f(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def trend_release(st: dict[str, Any], btc_w: float, nav: float) -> list[str]:
    """낙폭 정지 중 하루 한 번(롱 맞출 때) — BTC 롱 목표가 0 이 된 적이 있고 지금 0 보다 크면 푼다.

    문서 266 TREND: 정지 9 곳 모두 걸렸고, 안 풀면 수익이 거의 사라졌다(2020-06 시작 186k → 1.7k).
    추세 꺼짐 → 다시 켜짐에 풀면 9 곳 중 8 곳에서 정지 없음과 같거나 나았다. 풀 때 고점 = 지금."""
    if not st.get("dd_halt"):
        return []
    if btc_w <= 0:
        st["halt_off_seen"] = True
        return []
    if not st.get("halt_off_seen"):
        return []
    reason = st.pop("dd_halt")
    st.pop("halt_off_seen", None)
    st["nav_peak"] = nav
    return [
        f"▶ 낙폭 정지 스스로 풀림 ({reason}) — BTC 추세가 꺼졌다 다시 켜짐, 고점을 지금 단위 가치로"
    ]


def stop_reason(st: dict[str, Any], halt: str | None) -> str | None:
    """새 노출을 막는 이유 (없으면 None)."""
    if st.get("dd_halt"):
        return str(st["dd_halt"])
    if halt:
        return f"전체 정지 중 ({halt})"
    return None


# ------------------------------------------------------------------ 할 일


def _setup(c: BinanceUsdmLive, st: dict[str, Any], now_ms: int) -> str | None:
    """처음 한 번: 헤지 · 다중 자산 모드 아님 확인, 배율 5, cross. 문제면 이유."""
    if c.dual_side_position():
        return "헤지 모드(dual side) — 앱에서 one-way 로 바꿔 주세요"
    if not c.multi_assets_margin():
        return (
            "단일 자산 모드 — Vega 가 옮기는 USDT 가 USDC 계약 증거금이 안 돼요. "
            "바이낸스 선물 설정에서 '다중 자산 모드(Multi-Assets Mode)' 로 바꿔 주세요"
        )
    for sym in core.TRADE.values():
        c.set_leverage(sym, core.LEVERAGE)
    st["setup"] = True
    st["start_ms"] = now_ms
    return None


def tick(now_ms: int | None = None) -> list[str]:
    """할 차례인 일만 (데모와 같은 시각): 하루 첫 실행(00:05 UTC 뒤) 롱, 4 시간봉 마감 뒤 숏."""
    now_ms = now_ms or int(time.time() * 1000)
    on, why = _enabled()
    if not on:
        return [f"꺼짐 — {why}"]
    st = _load()
    st["live"] = True
    st["multi"] = True  # 다중 자산 모드만 지원 (_setup 이 확인)
    day, bar = now_ms // DAY, now_ms // H4
    due_long = st.get("long_day") != day and now_ms % DAY >= 5 * MINUTE
    due_short = st.get("short_bar") != bar and now_ms % H4 >= 3 * MINUTE
    if not (due_long or due_short or st.get("maker")):
        return []
    lines: list[str] = []
    alert: list[str] = []
    skipped: list[str] = []
    c = _client()
    try:
        if not st.get("setup"):
            bad = _setup(c, st, now_ms)
            if bad:
                _notify(f"{TITLE}\n⚠ 시작 안 함 — {bad}")
                return [bad]
            alert.append("🟢 실거래 시작 — 배율 5 · cross · 다중 자산(선물 지갑 전체가 장부)")
        if not st.get("cross"):
            st["cross"] = core._ensure_cross(c)
        eq, _pos = core._equity(c, st)
        start = int(st.get("flow_ms") or st.get("start_ms") or now_ms) - DAY
        try:
            flows = [
                (t, float(a), f"{asset}-{i}")
                for asset in ("USDT", "USDC")  # 다중 자산 — 달러 스테이블 1:1 로 본다
                for t, a, i in c.transfers(asset, start)
            ]
        except AutoTradingError as exc:  # 다음 실행이 같은 구간부터 다시 읽는다
            flows = []
            lines.append(f"이체 기록 못 읽음 — {exc}")
        nav, dd, msgs = update_nav(st, eq, flows)
        alert += msgs
        if due_long and st.get("dd_halt"):  # 오늘 롱 목표로 정지 풀기 판단 (문서 266)
            w, _info = core.long_targets(now_ms)
            alert += trend_release(st, w.get("BTC", 0.0), nav)
        if eq < MIN_EQUITY:
            lines.append(f"USDC 장부 {eq:,.2f} < {MIN_EQUITY:,.0f} — 입금 전이라 쉼")
            return lines
        st["alloc"] = float(st.get("alloc") or eq)
        st.setdefault("peak", eq)  # 데모 기본값(가상 2,000)이 숏 규칙의 고점으로 쓰이지 않게
        halt = halted_for(HALT, "altair")
        st["no_new"] = stop_reason(st, halt)
        if st.get("maker"):  # 걸어 둔 메이커 주문 확인 (문서 269)
            lines += core.maker_check(c, st, now_ms, halted=halt, alert=alert)
        if due_long:
            lines += core.rebalance_long(c, st, now_ms, alert=alert, maker=True)
            st["long_day"] = day
        if due_short:
            lines += core.check_shorts(c, st, now_ms, alert=alert, skipped=skipped)
            st["short_bar"] = bar
        eq, _pos = core._equity(c, st)
        acct = c.account()
        rows = [
            (
                coin,
                float(acct.positions[sym].quantity),
                float(acct.positions[sym].entry_price),
                float(c.mark_price(sym)),
            )
            for coin, sym in core.TRADE.items()
            if sym in acct.positions
        ]
        st.setdefault("history", []).append([now_ms, round(eq, 2), round(nav, 4)])
        st["history"] = st["history"][-2000:]
    finally:
        c.close()
        _save(st)
    if any(x.strip() for x in alert):
        text = core.alert_text(
            now_ms,
            alert,
            skipped,
            st.get("long_w") or {},
            bool(st.get("shorts")),
            rows,
            eq,
            float(st["alloc"]),
            title=TITLE,
        )
        _notify(text + f"\n 단위 가치 {nav:.3f} · 고점 대비 -{dd:.1%} (30% 면 새 노출 정지)")
    return lines


def plan() -> dict[str, Any]:
    now_ms = int(time.time() * 1000)
    st = {**_load(), "live": True, "multi": True}
    c = _client()
    try:
        eq, pos = core._equity(c, dict(st))
        lines = core.rebalance_long(c, dict(st), now_ms, dry=True)
        lines += core.check_shorts(c, dict(st), now_ms, dry=True)
        mins = {k: str(c.filters(s).min_notional) for k, s in core.TRADE.items()}
        steps = {k: str(c.filters(s).step_size) for k, s in core.TRADE.items()}
        hedge, multi = c.dual_side_position(), c.multi_assets_margin()
    finally:
        c.close()
    return {
        "equity_usd": round(eq, 2),  # 다중 자산 — 선물 지갑 전체 증거금 잔고
        "positions": pos,
        "min_notional": mins,
        "step": steps,
        "hedge_mode": hedge,
        "multi_assets": multi,
        "enabled": _enabled(),
        "lines": lines,
    }


def status() -> dict[str, Any]:
    st = _load()
    hist = st.get("history") or []
    return {
        "enabled": _enabled(),
        "start": core._iso(st["start_ms"]) if st.get("start_ms") else None,
        "equity": hist[-1][1] if hist else None,
        "nav": hist[-1][2] if hist else None,
        "nav_peak": st.get("nav_peak"),
        "dd_halt": st.get("dd_halt"),
        "halt_off_seen": st.get("halt_off_seen"),
        "margin": "CROSSED" if st.get("cross") else "아직 설정 전",
        "long_w": st.get("long_w"),
        "shorts": st.get("shorts"),
        "maker_pending": st.get("maker"),
        "short_trades": len(st.get("short_trades") or []),
        "own_halted": st.get("own_halted"),
    }


def unhalt() -> str:
    st = _load()
    if not st.get("dd_halt"):
        return "낙폭 정지 아님"
    reason = st.pop("dd_halt")
    st.pop("halt_off_seen", None)
    hist = st.get("history") or []
    if hist and len(hist[-1]) > 2:
        st["nav_peak"] = hist[-1][2]
    _save(st)
    _notify(f"{TITLE}\n▶ 낙폭 정지 풀림 ({reason}) — 고점을 지금 단위 가치로")
    return f"풀림: {reason}"


def main(argv: list[str]) -> int:
    if argv == ["plan"]:
        out: Any = plan()
    elif argv == ["tick"]:
        out = tick() or ["할 차례 아님"]
    elif argv == ["status"]:
        out = status()
    elif argv == ["unhalt"]:
        out = unhalt()
    else:
        raise SystemExit("usage: python -m scripts.altair_live plan | tick | status | unhalt")
    sys.stdout.write(json.dumps(out, ensure_ascii=False, indent=1, default=str) + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
