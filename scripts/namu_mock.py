"""나무증권(NH) 모의투자 주문 경로 시험 — 가상 돈, 실제 체결 없음.

    python -m scripts.namu_mock status            # 모의 계좌 예수금 · 보유
    python -m scripts.namu_mock run [종목코드]     # 시험 두 가지 (기본 005940 NH투자증권)

시험 (사용자 2026-10-15 "모의 투자 한번 진행해줘"):

1. **미체결 → 취소**: 매수 호가 10 번째 값에 1 주 지정가 매수(안 닿는 가격) → 주문 목록에서
   미체결 확인 → 취소 → 취소 수량 확인.
2. **체결 → 되팔기**: 매도 1 호가에 10 주 지정가 매수(바로 닿는 가격) → 체결 확인 → 잔고 확인 →
   매수 1 호가에 10 주 지정가 매도 → 체결 확인 → 잔고에서 빠졌는지 확인.

지정가만 쓴다(시장가 금지 규칙). 결과는 ``reports/namu_mock/<날짜>.json``.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from auto_trading.config.secrets import load_namu_credentials
from auto_trading.exchange.namu import NamuMock, NamuOrder, NamuTokenCache

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "reports" / "namu_mock"
KST = ZoneInfo("Asia/Seoul")
WAIT_S = 30.0
POLL_S = 3.0


def _client() -> NamuMock:
    return NamuMock(
        load_namu_credentials(REPO / ".env"), NamuTokenCache(REPO / "data" / "namu_token.json")
    )


def _find(nh: NamuMock, day: str, no: int) -> NamuOrder | None:
    return next((o for o in nh.orders(day) if o.order_no == no), None)


def _until(nh: NamuMock, day: str, no: int, done: Callable[[NamuOrder], bool]) -> NamuOrder | None:
    """주문 ``no`` 가 ``done`` 을 만족할 때까지 (최대 ``WAIT_S``) 다시 본다."""
    waited = 0.0
    while True:
        o = _find(nh, day, no)
        if o is not None and done(o):
            return o
        if waited >= WAIT_S:
            return o
        nh.sleep(POLL_S)
        waited += POLL_S


def _held(nh: NamuMock, code: str) -> float:
    """지금 들고 있는 수량 = 잔량수량(``rsdl_qty``). 통합잔고(``itg_bnc_qty``)는 결제(D+2)된
    것만이라 오늘 산 주식은 0 으로 나온다 (2026-10-15 모의 확인)."""
    _, rows = nh.balance()
    return sum(float(r.get("rsdl_qty") or 0) for r in rows if str(r.get("iem_cd")) == code)


def _row(o: NamuOrder | None) -> dict[str, Any] | None:
    if o is None:
        return None
    return {
        "order_no": o.order_no,
        "side": o.side,
        "qty": o.qty,
        "price": o.price,
        "filled": o.filled,
        "avg_price": o.avg_price,
        "open": o.open_qty,
        "canceled": o.canceled,
        "reject": o.reject,
    }


def run(code: str = "005940") -> dict[str, Any]:
    nh = _client()
    now = datetime.now(KST)
    day = now.strftime("%Y%m%d")
    report: dict[str, Any] = {"at": now.isoformat(timespec="seconds"), "code": code}
    try:
        head, _ = nh.balance()
        report["cash_before"] = head.get("dca")
        q = nh.quote(code)
        report["name"] = str(q.get("iem_nm") or code).lstrip("*#")
        ask, bid, far = int(q["askp1"]), int(q["bidp1"]), int(q["bidp10"])
        report["quote"] = {"ask": ask, "bid": bid, "bid10": far}

        # 1. 미체결 → 취소
        no = nh.limit("buy", code, 1, far)
        rest = _until(nh, day, no, lambda o: o.open_qty > 0 or o.filled > 0 or bool(o.reject))
        can = nh.cancel(no, code)
        after = _until(nh, day, no, lambda o: o.canceled > 0 or o.open_qty == 0)
        report["test1_rest_cancel"] = {
            "order": _row(rest),
            "cancel_no": can,
            "after": _row(after),
            "ok": bool(after and after.canceled == 1 and after.filled == 0),
        }

        # 2. 체결 → 되팔기
        before = _held(nh, code)
        buy = nh.limit("buy", code, 10, ask)
        bought = _until(nh, day, buy, lambda o: o.filled >= o.qty or bool(o.reject))
        mid = _held(nh, code)
        filled = bought.filled if bought else 0
        sold = None
        if filled > 0:
            q2 = nh.quote(code)
            sell = nh.limit("sell", code, filled, int(q2["bidp1"]))
            sold = _until(nh, day, sell, lambda o: o.filled >= o.qty or bool(o.reject))
        end = _held(nh, code)
        report["test2_fill_sell"] = {
            "buy": _row(bought),
            "held_before": before,
            "held_after_buy": mid,
            "sell": _row(sold),
            "held_after_sell": end,
            "ok": bool(
                bought
                and bought.filled == 10
                and mid == before + 10
                and sold
                and sold.filled == 10
                and end == before
            ),
        }
        head, _ = nh.balance()
        report["cash_after"] = head.get("dca")
    finally:
        nh.close()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{day}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return report


def status() -> dict[str, Any]:
    nh = _client()
    try:
        head, rows = nh.balance()
    finally:
        nh.close()
    return {
        "account_tail": nh.account()[-4:] if nh._account else None,
        "cash": head.get("dca"),
        "cash_d2": head.get("nxt2_dd_dca"),
        "eval": head.get("tot_eal_amt"),
        "holdings": [
            {"code": r.get("iem_cd"), "name": r.get("iem_nm"), "qty": r.get("rsdl_qty")}
            for r in rows
            if r.get("iem_cd")
        ],
    }


def main(argv: list[str]) -> int:
    if argv == ["status"]:
        out = status()
    elif argv and argv[0] == "run" and len(argv) <= 2:
        out = run(*argv[1:])
    else:
        raise SystemExit("usage: python -m scripts.namu_mock status | run [종목코드]")
    sys.stdout.write(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
