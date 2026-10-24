"""🧭 Polaris Paper — 실거래 Polaris 규칙을 가짜 돈으로 따라가는 기록 (주문 없음).

언제 실행: 예약 ``AutoTrading_Polaris`` (09:00 · 00:20 · 01:20 KST) 의
           ``scripts.polaris daily`` 가 먼저 부른다. 아침(08 시 뒤) 실행에서만,
           새 미국 종가가 생긴 날 한 번 계산한다.
읽고 쓰는 것: 토스 일봉 종가 · 환율(조회만) → 장부 ``data/polaris_paper/state.json``.

    python -m scripts.polaris_paper daily    # 새 종가가 있으면 회차 · 12 월 맞추기 · 하루 평가
    python -m scripts.polaris_paper status   # 장부 · 마지막 평가 (조회 없음)

[이유] 사용자 2026-10-20 "polaris 같은 경우 paper만 작동하도록 하면 돼" — 실거래(나무)는 쓰지 않고
       (설정 ``polaris.on`` 꺼짐) 같은 규칙의 성적만 쌓는다.

규칙은 실거래와 같은 :mod:`auto_trading.ops.polaris` 함수를 쓴다. 다른 점:
* 버튼이 없다 — 회차(11 월부터 4 번, 설정 ``polaris.monthly_krw``)는 그달 첫 종가에 바로 산다.
* 체결가 = 그날 종가로 낸 지정가(살 때 종가 + 0.2% 올림, 팔 때 − 0.2% 내림) — 실거래 지정가 규칙.
* 수수료 0.09% (``FEE``), 환율은 토스 조회 환율. 회차마다 그 회차 돈만 쓰고(환율 여유 1%),
  남은 원화는 현금으로 둔다 (실거래와 같음).
* 12 월 맞추기는 4 회차 뒤 매년 12 월 첫 종가에 바로 한다.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parent.parent
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from auto_trading.config.secrets import (  # noqa: E402
    load_telegram_credentials,
)
from auto_trading.notify.channels import (  # noqa: E402
    NotifyError,
    OutboxNotifier,
    TelegramNotifier,
)
from auto_trading.ops import user_settings  # noqa: E402
from auto_trading.ops.polaris import (  # noqa: E402
    FEE,
    ROUNDS,
    START_MONTH,
    SYMBOLS,
    PolarisState,
    Rebal,
    Round,
    budget_usd,
    limit_price,
    plan_buys,
    plan_rebalance,
    sell_price,
)

KST = ZoneInfo("Asia/Seoul")
STATE = REPO / "data" / "polaris_paper" / "state.json"
OUTBOX = REPO / "reports" / "polaris" / "paper_outbox.log"
MORNING_HOUR = 8
"""이 시각(KST) 뒤 실행에서만 — 밤 실행(00:20 · 01:20)은 미국 장중이라 오늘 봉이 덜 끝났다."""


# ------------------------------------------------------------------ 장부


def load() -> dict[str, Any]:
    """[쓰임] Paper 장부 {state: Polaris 장부, cash_krw, deposits_krw, last_close_day, history}.
    [이유] 없거나 깨졌으면 처음부터 — 가짜 돈 기록이라 멈출 까닭이 없다."""
    try:
        saved = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = None
    if not isinstance(saved, dict):
        saved = {}
    return {
        "state": PolarisState.from_dict(saved.get("state") or {}),
        "cash_krw": float(saved.get("cash_krw") or 0.0),
        "deposits_krw": float(saved.get("deposits_krw") or 0.0),
        "last_close_day": str(saved.get("last_close_day") or ""),
        "history": list(saved.get("history") or []),
    }


def save(paper: dict[str, Any]) -> None:
    body = {**paper, "state": paper["state"].to_dict()}
    STATE.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE.with_suffix(".tmp")
    temporary.write_text(json.dumps(body, ensure_ascii=False, indent=1), encoding="utf-8")
    temporary.replace(STATE)


# ------------------------------------------------------------------ 계산 (조회 · 파일 없음)


def _buy(
    paper: dict[str, Any], buys: dict[str, int], closes: dict[str, float], fx: float, tag: str
) -> list[str]:
    """[쓰임] 살 것 {종목: 주} 을 종가 지정가로 가짜 체결 — 장부 · 현금(원화)을 고친다."""
    st: PolarisState = paper["state"]
    lines = []
    for symbol, qty in sorted(buys.items()):
        price = limit_price(closes[symbol])
        usd = qty * price
        fee = usd * FEE
        st.apply_fill(symbol, float(qty), usd, fee, fx, f"paper-{tag}-{symbol}")
        paper["cash_krw"] -= (usd + fee) * fx
        lines.append(f"{symbol} {qty}주 × ${price:,.2f}")
    return lines


def _value_krw(paper: dict[str, Any], closes: dict[str, float], fx: float) -> float:
    st: PolarisState = paper["state"]
    held = sum(st.qty.get(symbol, 0.0) * closes[symbol] for symbol in SYMBOLS)
    return float(held * fx + paper["cash_krw"])


def step(
    paper: dict[str, Any], close_day: str, closes: dict[str, float], fx: float, month_krw: float
) -> list[str]:
    """[쓰임] 새 종가 하루치로 장부를 한 걸음 — 회차 · 12 월 맞추기 · 하루 평가. 알릴 글 목록.
    [이유] 같은 종가로 두 번 계산하지 않게 ``last_close_day`` 를 본다 (예약이 하루 세 번 돈다)."""
    if close_day <= paper["last_close_day"]:
        return []
    st: PolarisState = paper["state"]
    month = close_day[:7]
    messages: list[str] = []
    # 1 단계: 이달 회차 (11 월부터 4 번, 그달 첫 종가에)
    if month >= START_MONTH and st.done_rounds < ROUNDS and st.round_for(month) is None:
        paper["cash_krw"] += month_krw
        paper["deposits_krw"] += month_krw
        buys = plan_buys(st.qty, closes, budget_usd(month_krw, fx))  # 실거래처럼 그 회차 돈만
        lines = _buy(paper, buys, closes, fx, close_day)
        st.rounds.append(Round(month=month, krw=month_krw, status="done", note="paper"))
        number = st.done_rounds
        messages.append(
            f"🧭 Polaris Paper (가짜 돈) — {number}/{ROUNDS} 회차 {month_krw:,.0f}원"
            f" ({close_day} 종가)\n" + ("\n".join(f"• {line}" for line in lines) or "• 살 것 없음")
        )
    # 2 단계: 4 회차 뒤 매년 12 월 맞추기 (첫 종가에)
    year = close_day[:4]
    if st.done_rounds >= ROUNDS and close_day[5:7] == "12" and st.rebal_for(year) is None:
        sells, buys = plan_rebalance(st.qty, closes, closes)
        if not sells:
            st.rebals.append(Rebal(year=year, status="none", note="paper — 맞출 것 없음"))
        else:
            sold = []
            for symbol, qty in sorted(sells.items()):
                price = sell_price(closes[symbol])
                usd = qty * price
                fee = usd * FEE
                st.apply_sell(
                    symbol, float(qty), usd, fee, fx, f"paper-{close_day}-S{symbol}", year
                )
                paper["cash_krw"] += (usd - fee) * fx
                sold.append(f"{symbol} {qty}주 팔기 × ${price:,.2f}")
            bought = _buy(paper, buys, closes, fx, f"{close_day}-R")
            st.rebals.append(Rebal(year=year, status="done", note="paper"))
            messages.append(
                f"🧭 Polaris Paper (가짜 돈) — {year} 12 월 맞추기 ({close_day} 종가)\n"
                + "\n".join(f"• {line}" for line in sold + bought)
            )
    # 3 단계: 하루 평가 (첫 회차 뒤부터)
    if paper["deposits_krw"] > 0:
        value = _value_krw(paper, closes, fx)
        paper["history"].append(
            {"date": close_day, "value_krw": round(value), "deposits_krw": paper["deposits_krw"],
             "fx": fx}
        )  # fmt: skip
        if messages:
            gain = value - paper["deposits_krw"]
            messages[-1] += (
                f"\n평가 {value:,.0f}원 · 넣은 돈 {paper['deposits_krw']:,.0f}원 · "
                f"{gain:+,.0f}원 ({gain / paper['deposits_krw']:+.1%})"
            )
    paper["last_close_day"] = close_day
    return messages


# ------------------------------------------------------------------ 실행


def _market_data(today: str) -> tuple[str, dict[str, float], float] | None:
    """[쓰임] 종가 · 환율 — 토스증권 실계좌 키로 조회하던 부분은
    실주문 코드를 걷어 낼 때 뺐다(README 6-3, 정할 것 2).
    새 종가를 받지 않으므로 늘 None (Paper 는 저장된 상태 그대로)."""
    print("Polaris Paper: 종가 조회 없음 — 실계좌 키로 받던 부분을 뺐다(README 6-3)")
    return None


def _send(text: str) -> None:
    OutboxNotifier(OUTBOX).send(text)
    credentials = load_telegram_credentials(REPO / ".env")
    if credentials is None:
        return
    try:
        TelegramNotifier(credentials).send(text)
    except NotifyError as exc:
        print(f"텔레그램 실패: {exc}")


def daily(now: datetime | None = None) -> int:
    """[쓰임] 예약이 부른다 — 아침이고 새 종가가 있으면 :func:`step` 하고 저장 · 알림."""
    now = now or datetime.now(KST)
    if now.hour < MORNING_HOUR:
        print("Polaris Paper: 아침 실행에서만")
        return 0
    data = _market_data(now.strftime("%Y-%m-%d"))
    if data is None:
        print("Polaris Paper: 종가가 아직 맞지 않음 — 다음 실행에서")
        return 0
    close_day, closes, fx = data
    paper = load()
    month_krw = float(user_settings.get(REPO, "polaris.monthly_krw"))
    messages = step(paper, close_day, closes, fx, month_krw)
    save(paper)
    for text in messages:
        _send(text)
    print("\n\n".join(messages) or f"Polaris Paper: {close_day} 기록 (할 일 없음)")
    return 0


def status() -> int:
    paper = load()
    st: PolarisState = paper["state"]
    print(
        f"🧭 Polaris Paper — 회차 {st.done_rounds}/{ROUNDS}"
        f" · 마지막 종가 {paper['last_close_day'] or '-'}"
    )
    for symbol in SYMBOLS:
        print(
            f"  {symbol} {st.qty.get(symbol, 0.0):g}주 · 원가 ${st.cost_usd.get(symbol, 0.0):,.2f}"
        )
    print(f"  현금 {paper['cash_krw']:,.0f}원 · 넣은 돈 {paper['deposits_krw']:,.0f}원")
    if paper["history"]:
        last = paper["history"][-1]
        print(f"  평가 {last['value_krw']:,.0f}원 ({last['date']})")
    return 0


def main(argv: list[str]) -> int:
    if argv == ["daily"]:
        return daily()
    if argv == ["status"]:
        return status()
    raise SystemExit("usage: python -m scripts.polaris_paper daily|status")


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
