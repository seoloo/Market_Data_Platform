"""매일 안전 점검 실행 — 조회만 하고, 이상하거나 바뀐 게 있으면 투자 봇으로 한 통.

언제 실행: 텔레그램 봇(2 분마다)이 매일 09:20 KST 뒤 한 번 부른다 — Vega 09:05 매매가 끝난 뒤라
           장부와 잔고가 맞아야 한다.
읽고 쓰는 것: ``.env`` 주문 스위치 · 정지 파일 · 각 장부 · 거래소 조회(주문 꺼짐) →
              기록 ``data/safety/state.json`` (어제 값 · 오늘 한 날짜).

    python -m scripts.safety_check show   # 보내지 않고 화면에만
    python -m scripts.safety_check run    # 오늘 아직 안 했으면 점검 · (있으면) 알림

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
from auto_trading.ops.ledger_file import read_json_or_none, write_json_atomic

REPO = Path(__file__).resolve().parent.parent
STATE = REPO / "data" / "safety" / "state.json"
HALT = REPO / "data" / "HALT"
KST = ZoneInfo("Asia/Seoul")
DUE = (9, 20)
"""이 시각(KST) 뒤에 하루 한 번."""
FLAGS = (
    "TOSS_TRADING_ENABLED",
    "BINANCE_LIVE_TRADING_ENABLED",
    "BINANCE_FUTURES_LIVE_TRADING_ENABLED",
    "UPBIT_TRADING_ENABLED",
    "KIS_TRADING_ENABLED",
)
"""``.env`` 주문 스위치 — 어제와 달라지면 알린다."""
SETTINGS = ("stock.orders",)
"""설정 화면 주문 스위치 — 어제와 달라지면 알린다."""


def _load() -> dict[str, Any]:
    """[쓰임] 어제 점검 기록. 없거나 깨졌으면 빈 dict (처음처럼)."""
    try:
        saved = read_json_or_none(STATE)
    except (OSError, ValueError):
        return {}
    return dict(saved) if saved is not None else {}


def _save(record: dict[str, Any]) -> None:
    write_json_atomic(STATE, record)


def _switches() -> dict[str, str]:
    """[쓰임] 지금 주문 스위치 {이름: 켜짐 · 꺼짐} — ``.env`` 스위치와 설정 화면 스위치."""
    env = read_env_file(REPO / ".env")
    switches = {}
    for flag in FLAGS:
        switches[flag] = "켜짐" if env.get(flag, "").strip().lower() == "true" else "꺼짐"
    for key in SETTINGS:
        switches[f"설정 {key}"] = "켜짐" if user_settings.get(REPO, key) else "꺼짐"
    return switches


def _halts() -> dict[str, str]:
    """[쓰임] 지금 정지 상태 {전체: 사유 · 정상, 그룹 이름: 정지 · 정상}."""
    halts = {"전체": halted_reason(HALT) or "정상"}
    for group, name in GROUPS.items():
        halts[name] = "정지" if group_halted(HALT, group) else "정상"
    return halts


def _check_binance(findings: list[sc.Finding], failed: list[str]) -> None:
    """[쓰임] 바이낸스 키 권한 · 선물 포지션 (조회만, 주문 꺼짐).
    [이유] Vega 는 2026-10-19 업비트로 돌아가 바이낸스 Vega 장부는 은퇴 —
           키는 남아 있어 권한만 본다."""
    try:
        from auto_trading.exchange.binance_spot_live import BinanceSpotLive, load_live_spot

        spot_credentials, _spot_on = load_live_spot(REPO / ".env")
        spot = BinanceSpotLive(spot_credentials, trading_enabled=False)
        try:
            restrictions = spot._request("/sapi/v1/account/apiRestrictions", {}, signed=True)
            findings += sc.key_issues(restrictions)
        finally:
            spot.close()
    except Exception as exc:
        failed.append(f"바이낸스 키 ({type(exc).__name__})")


def _check_upbit_vega(findings: list[sc.Finding], failed: list[str]) -> None:
    """[쓰임] 업비트 — Vega 장부('있어야 할' 코인)보다 실제 코인이 적은지 (조회만, 주문 꺼짐)."""
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


def _check_binance_futures(findings: list[sc.Finding], failed: list[str]) -> None:
    """[쓰임] 바이낸스 선물에 포지션이 남아 있는지 (Altair 은퇴 뒤라 있으면 안 됨)."""
    try:
        from auto_trading.exchange.binance_usdm_live import BinanceUsdmLive, load_live_futures

        futures_credentials, _futures_on = load_live_futures(REPO / ".env")
        futures = BinanceUsdmLive(futures_credentials, trading_enabled=False)
        try:
            positions = {}
            for symbol, position in futures.account().positions.items():
                positions[symbol] = position.quantity
            findings += sc.futures_issues(positions)
        finally:
            futures.close()
    except Exception as exc:
        failed.append(f"바이낸스 선물 ({type(exc).__name__})")


def _stock_ledger_qty() -> dict[str, Decimal]:
    """[쓰임] 💹 개별주 장부의 종목별 보유 수량 (보유 중 · 파는 중 티켓만, 산 것 − 판 것)."""
    from scripts import size_guard

    qty_by_symbol: dict[str, Decimal] = {}
    for ticket in size_guard.load_tickets():
        if ticket.status in ("holding", "selling"):
            held = Decimal(str(ticket.filled - ticket.sold))
            qty_by_symbol[ticket.symbol] = qty_by_symbol.get(ticket.symbol, Decimal(0)) + held
    return qty_by_symbol


def _check_toss_holdings(findings: list[sc.Finding], failed: list[str]) -> None:
    """[쓰임] 토스 — Deneb · 💹 개별주 장부가 실제 보유보다 많지 않은지 (조회만)."""
    try:
        from auto_trading.config.secrets import load_toss_credentials
        from auto_trading.exchange.toss import TossReader, TossTokenCache

        reader = TossReader(
            load_toss_credentials(REPO / ".env"), TossTokenCache(REPO / "data" / "toss_token.json")
        )
        try:
            held = {symbol: position.qty for symbol, position in reader.holdings().items()}
        finally:
            reader.close()
        deneb = json.loads((REPO / "data" / "toss" / "DENEB.json").read_text(encoding="utf-8"))
        deneb_qty = {}
        for symbol, qty in (deneb.get("qty") or {}).items():
            deneb_qty[symbol] = Decimal(str(qty))
        findings += sc.holding_issues("Deneb", deneb_qty, held)
        findings += sc.holding_issues("💹 개별주", _stock_ledger_qty(), held)
    except Exception as exc:
        failed.append(f"토스 보유 ({type(exc).__name__})")


def _check_tasks(findings: list[sc.Finding], failed: list[str]) -> None:
    """[쓰임] Windows 예약 — 옛 · 새 예약 중복, 꼭 필요한 예약 꺼짐."""
    try:
        from auto_trading.ops import task_setup

        states = {name: task.state for name, task in task_setup.status().items()}
        findings += sc.task_issues(states)
    except Exception as exc:
        failed.append(f"예약 ({type(exc).__name__})")


def check() -> tuple[list[sc.Finding], list[str], dict[str, Any]]:
    """[쓰임] 점검 한 번 → (발견, 확인 못 한 것, 오늘 기록할 값).
    [이유] 한 곳을 못 읽어도(네트워크 · 키) 멈추지 않고 나머지를 본다 —
           못 읽은 곳은 '확인 못 한 것' 으로."""
    previous = _load()
    findings: list[sc.Finding] = []
    failed: list[str] = []
    switches = _switches()
    halts = _halts()
    findings += sc.state_changes(previous.get("switches") or {}, switches, "스위치")
    findings += sc.state_changes(previous.get("halts") or {}, halts, "정지")
    _check_binance(findings, failed)
    _check_upbit_vega(findings, failed)
    _check_binance_futures(findings, failed)
    _check_toss_holdings(findings, failed)
    _check_tasks(findings, failed)
    return findings, failed, {"switches": switches, "halts": halts}


def _send(text: str) -> None:
    """[쓰임] 투자 봇으로 한 통 (텔레그램 키가 없으면 조용히 건너뜀)."""
    credentials = load_telegram_credentials(REPO / ".env")
    if credentials is None:
        return
    bot = TelegramNotifier(credentials)
    try:
        bot.send(text)
    finally:
        bot.close()


def run(now: datetime | None = None) -> str | None:
    """[쓰임] 오늘(09:20 KST 뒤) 아직 안 했으면 점검하고, 보낸 글(또는 None)을 돌려준다.
    [이유] 보내기에 실패하면 '오늘 함' 을 기록하지 않는다 — 2 분 뒤 봇이 다시 부를 때 또 보낸다."""
    now = now or datetime.now(KST)
    record = _load()
    today = now.date().isoformat()
    if record.get("day") == today or (now.hour, now.minute) < DUE:
        return None
    findings, failed, snapshot = check()
    text = sc.report_text(findings, failed)
    if text:
        try:
            _send(text)
        except NotifyError as exc:
            print(f"안전 점검 알림 실패: {exc}")
            return None  # 다음 실행(2 분 뒤)에서 다시
    _save({**snapshot, "day": today, "last": text or "이상 없음"})
    return text


def main(argv: list[str]) -> int:
    if argv == ["show"]:
        findings, failed, _snapshot = check()
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
