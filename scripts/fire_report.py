"""파이어 진행 보고 — 매달 1 일 09:15 투자 봇으로 (사용자 2026-10-10, 목표 1 억).

사용 예::

    python -m scripts.fire_report run    # 계산 → 텔레그램 (예약: 매달 1 일)
    python -m scripts.fire_report show   # 보내지 않고 화면에만 (기록도 안 남김)

* 평가는 대시보드 실거래 홈과 같은 장부 + 공개 시세. 주문 · 계좌 조회 없음.
* 지난 보고 값은 ``data/fire/state.json`` 에 남겨 다음 달 '지난 보고보다' 줄에 쓴다.

언제 실행: 매달 1 일 09:15 ``AutoTrading_Fire_Report``.
읽고 쓰는 것: 실거래 장부(입금 날짜 · 평가) · 공개 시세 · 설정 ``fire.*`` → 투자 봇 텔레그램 ·
              ``data/fire/state.json``.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from auto_trading.app import live_accounts
from auto_trading.config.secrets import load_telegram_credentials
from auto_trading.notify.channels import NotifyError, TelegramNotifier
from auto_trading.ops import user_settings
from auto_trading.ops.fire import Snapshot, monthly_rate, report_text
from auto_trading.ui.live_home import build_books, fetch_quotes, needed

# 현황을 그 순간 실제 계좌로 맞출 조회 창구 (ui 는 거래소를 직접 부르지 않는다, 2026-10-21)
live_accounts.install()

REPO = Path(__file__).resolve().parent.parent


def _read(path: Path) -> dict[str, Any]:
    """[쓰임] 장부 JSON 하나 (없거나 깨졌으면 빈 dict)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _fx_now() -> float:
    """원/달러 (USDT 입금을 원화로) — 실거래 홈과 같은 시세. 못 받으면 0 (그 입금은 0 원으로 셈)."""
    return fetch_quotes((), ()).fx or 0.0


def dated_deposits(root: Path) -> list[tuple[str, float]]:
    """(날짜, 원) — Deneb 입금 · Vega 입금 · 토스 적립 원가."""
    out: list[tuple[str, float]] = []
    for row in _read(root / "data" / "toss" / "DENEB.json").get("deposits") or []:
        if isinstance(row, list) and len(row) >= 2:
            out.append((str(row[0]), float(row[1])))
    binance = _read(root / "data" / "vega" / "state.json")
    # Vega 를 바이낸스로 옮긴 뒤(2026-10-15): 같은 돈을 두 번 세지 않게 업비트 입금은 뺀다
    if binance:
        fx = _fx_now()
        for row in binance.get("deposits") or []:
            if isinstance(row, list) and len(row) >= 2:
                out.append((str(row[0]), float(row[1]) * fx))
        # Altair 자동 이체분(2026-10-15) — Vega 입금에서 빠진 만큼 Altair 입금으로 센다
        for row in (binance.get("altair") or {}).get("transfers") or []:
            if isinstance(row, list) and len(row) >= 2:
                out.append((str(row[0]), float(row[1]) * fx))
    else:
        for row in _read(root / "data" / "upbit_t2" / "state.json").get("deposits") or []:
            if isinstance(row, list) and len(row) >= 2:
                out.append((str(row[0]), float(row[1])))
    for path in sorted((root / "data" / "toss").glob("*.json")):
        if path.stem in ("M3", "QS_SWITCH"):
            continue
        for buy in _read(path).get("buys") or []:
            if isinstance(buy, dict):
                if "cost_krw" in buy:  # 국내 적립 장부 (KR_<코드>.json)
                    out.append((str(buy.get("date")), float(buy["cost_krw"])))
                    continue
                cost = float(buy.get("cost_usd") or 0) * float(buy.get("fx_rate") or 0)
                out.append((str(buy.get("date")), cost))
    return out


def build(root: Path, today: date) -> tuple[str, Snapshot]:
    """[쓰임] 파이어 보고 글 + 지금 값 (평가 · 넣은 돈) — 실거래 홈과 같은 장부 + 공개 시세.
    [이유] 목표 · 연수익 세 가지는 설정 화면 값(``fire.*``)."""
    coins, tickers = needed(root)
    quotes = fetch_quotes(coins, tickers)
    books = build_books(root, quotes)
    now = Snapshot(sum(book.deposits_krw for book in books), sum(book.value_krw for book in books))
    last_raw = _read(root / "data" / "fire" / "state.json")
    last = (
        Snapshot(float(last_raw["deposits"]), float(last_raw["value"]))
        if "deposits" in last_raw and "value" in last_raw
        else None
    )
    prefs = user_settings.load(root)
    scenarios = (
        ("🟢", "좋을 때", float(prefs["fire.rate_good"]) / 100),
        ("⚪", "보통", float(prefs["fire.rate_mid"]) / 100),
        ("🔴", "나쁠 때", float(prefs["fire.rate_bad"]) / 100),
    )
    text = report_text(
        today,
        now,
        monthly_rate(dated_deposits(root), today),
        last,
        target=float(prefs["fire.target_krw"]),
        scenarios=scenarios,
    )
    if quotes.errors:
        text += "\n⚠ 시세 일부 실패 — 원가로 셈: " + " · ".join(quotes.errors)
    return text, now


def _today() -> date:
    """[쓰임] 오늘 날짜 (한국 시각) — 시험이 바꿔 끼운다."""
    return (datetime.now(UTC) + timedelta(hours=9)).date()


def run(root: Path = REPO) -> int:
    """[쓰임] 보고 한 번 — 계산 → 텔레그램 → 지난 보고 값 저장 (설정에서 끄면 안 함)."""
    if not user_settings.get(root, "fire.report_on"):
        print("파이어 보고 꺼짐 (설정)")
        return 0
    today = _today()
    text, now = build(root, today)
    creds = load_telegram_credentials(root / ".env")
    if creds is not None:
        bot = TelegramNotifier(creds)
        try:
            bot.send(text)
        except NotifyError as exc:
            print(f"텔레그램 실패: {exc}")
            return 1
        finally:
            bot.close()
    state = root / "data" / "fire" / "state.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(
        json.dumps(
            {
                "date": today.isoformat(),
                "deposits": now.deposits,
                "value": now.value,
                "at_ms": int(time.time() * 1000),
            }
        ),
        encoding="utf-8",
    )
    print(text)
    return 0


def main(argv: list[str]) -> int:
    """[쓰임] 명령 이름 → 함수 (예약은 ``run``)."""
    if argv == ["run"]:
        return run()
    if argv == ["show"]:
        sys.stdout.write(build(REPO, _today())[0] + "\n")
        return 0
    raise SystemExit("usage: python -m scripts.fire_report run|show")


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
