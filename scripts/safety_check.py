"""매일 안전 점검 실행 — 조회만 하고, 이상하거나 바뀐 게 있으면 투자 봇으로 한 통.

    python -m scripts.safety_check show   # 보내지 않고 화면에만
    python -m scripts.safety_check run    # 오늘 아직 안 했으면 점검 · (있으면) 알림

현황 봇(2 분마다)이 매일 09:20 KST 뒤 한 번 부른다 —
Vega 09:05 매매가 끝난 뒤라 장부와 잔고가 맞아야 한다.
판단은 :mod:`auto_trading.ops.safety_check`. 어느 한 칸을 못 읽어도 나머지는 본다('확인 못 한 것').
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from auto_trading.config.secrets import load_telegram_credentials, read_env_file
from auto_trading.notify.channels import NotifyError, TelegramNotifier
from auto_trading.ops import safety_check as sc
from auto_trading.ops import user_settings
from auto_trading.ops.kill_switch import GROUPS, group_halted, halted_reason

REPO = Path(__file__).resolve().parent.parent
STATE = REPO / "data" / "safety" / "state.json"
HALT = REPO / "data" / "HALT"
KST = ZoneInfo("Asia/Seoul")
DUE = (9, 20)
FLAGS = (
    "TOSS_TRADING_ENABLED",
    "BINANCE_LIVE_TRADING_ENABLED",
    "BINANCE_FUTURES_LIVE_TRADING_ENABLED",
    "UPBIT_TRADING_ENABLED",
    "KIS_TRADING_ENABLED",
)
SETTINGS = ("live.altair", "stock.orders", "demo.altair")


def _load() -> dict[str, Any]:
    try:
        return dict(json.loads(STATE.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return {}


def _save(st: dict[str, Any]) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


def _switches() -> dict[str, str]:
    env = read_env_file(REPO / ".env")
    out = {f: ("켜짐" if env.get(f, "").strip().lower() == "true" else "꺼짐") for f in FLAGS}
    for k in SETTINGS:
        out[f"설정 {k}"] = "켜짐" if user_settings.get(REPO, k) else "꺼짐"
    return out


def _halts() -> dict[str, str]:
    out = {"전체": halted_reason(HALT) or "정상"}
    for g, name in GROUPS.items():
        out[name] = "정지" if group_halted(HALT, g) else "정상"
    return out


def check() -> tuple[list[sc.Finding], list[str], dict[str, Any]]:
    """(발견, 확인 실패, 오늘 기록할 값)."""
    prev = _load()
    findings: list[sc.Finding] = []
    failed: list[str] = []
    switches, halts = _switches(), _halts()
    findings += sc.state_changes(prev.get("switches") or {}, switches, "스위치")
    findings += sc.state_changes(prev.get("halts") or {}, halts, "정지")

    # 바이낸스 — 키 권한 · 선물 (Vega 는 2026-10-19 업비트로 — 바이낸스 Vega 장부는 은퇴)
    try:
        from auto_trading.exchange.binance_spot_live import BinanceSpotLive, load_live_spot

        creds, _on = load_live_spot(REPO / ".env")
        spot = BinanceSpotLive(creds, trading_enabled=False)
        try:
            findings += sc.key_issues(
                spot._request("/sapi/v1/account/apiRestrictions", {}, signed=True)
            )
        finally:
            spot.close()
    except Exception as exc:
        failed.append(f"바이낸스 키 ({type(exc).__name__})")
    # 업비트 — Vega 장부('있어야 할' 코인)보다 실제 코인이 적은지 (조회만, 주문 꺼짐)
    try:
        from auto_trading.exchange.upbit import UpbitClient

        env = read_env_file(REPO / ".env")
        upbit = UpbitClient(env["UPBIT_API_KEY"], env["UPBIT_AOU_SECRET"], trading_enabled=False)
        try:
            vega = json.loads(
                (REPO / "data" / "upbit_t2" / "state.json").read_text(encoding="utf-8")
            )
            if vega.get("expected"):
                findings += sc.vega_issues(vega["expected"], upbit.balances())
        finally:
            upbit.close()
    except Exception as exc:
        failed.append(f"업비트 Vega 장부 ({type(exc).__name__})")
    try:
        from auto_trading.exchange.binance_usdm_live import BinanceUsdmLive, load_live_futures

        fcreds, _fon = load_live_futures(REPO / ".env")
        fut = BinanceUsdmLive(fcreds, trading_enabled=False)
        try:
            findings += sc.futures_issues(
                fut.multi_assets_margin(),
                fut.dual_side_position(),
                {s: p.quantity for s, p in fut.account().positions.items()},
                bool(user_settings.get(REPO, "live.altair")),
            )
        finally:
            fut.close()
    except Exception as exc:
        failed.append(f"바이낸스 선물 ({type(exc).__name__})")

    # 토스 — Deneb · 💹 개별주 장부가 실제 보유보다 많지 않은지
    try:
        from auto_trading.config.secrets import load_toss_credentials
        from auto_trading.exchange.toss import TossReader, TossTokenCache

        reader = TossReader(
            load_toss_credentials(REPO / ".env"), TossTokenCache(REPO / "data" / "toss_token.json")
        )
        try:
            held = {s: p.qty for s, p in reader.holdings().items()}
        finally:
            reader.close()
        deneb = json.loads((REPO / "data" / "toss" / "DENEB.json").read_text(encoding="utf-8"))
        findings += sc.holding_issues(
            "Deneb", {s: Decimal(str(q)) for s, q in (deneb.get("qty") or {}).items()}, held
        )
        from scripts import size_guard

        stock: dict[str, Decimal] = {}
        for t in size_guard.load_tickets():
            if t.status in ("holding", "selling"):
                stock[t.symbol] = stock.get(t.symbol, Decimal(0)) + Decimal(str(t.filled - t.sold))
        findings += sc.holding_issues("💹 개별주", stock, held)
    except Exception as exc:
        failed.append(f"토스 보유 ({type(exc).__name__})")

    # 예약
    try:
        from auto_trading.ops import task_setup

        findings += sc.task_issues({n: s.state for n, s in task_setup.status().items()})
    except Exception as exc:
        failed.append(f"예약 ({type(exc).__name__})")
    return findings, failed, {"switches": switches, "halts": halts}


def _send(text: str) -> None:
    creds = load_telegram_credentials(REPO / ".env")
    if creds is None:
        return
    bot = TelegramNotifier(creds)
    try:
        bot.send(text)
    finally:
        bot.close()


def run(now: datetime | None = None) -> str | None:
    """오늘(09:20 KST 뒤) 아직 안 했으면 점검. 보낸 글(또는 None)."""
    now = now or datetime.now(KST)
    st = _load()
    today = now.date().isoformat()
    if st.get("day") == today or (now.hour, now.minute) < DUE:
        return None
    findings, failed, snap = check()
    text = sc.report_text(findings, failed)
    if text:
        try:
            _send(text)
        except NotifyError as exc:
            print(f"안전 점검 알림 실패: {exc}")
            return None  # 다음 실행(2 분 뒤)에서 다시
    _save({**snap, "day": today, "last": text or "이상 없음"})
    return text


def main(argv: list[str]) -> int:
    if argv == ["show"]:
        findings, failed, _snap = check()
        print(sc.report_text(findings, failed) or "🛡️ 매일 안전 점검 — 이상 없음")
    elif argv == ["run"]:
        print(run() or "할 차례 아님 · 또는 이상 없음")
    else:
        raise SystemExit("usage: python -m scripts.safety_check show|run")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
