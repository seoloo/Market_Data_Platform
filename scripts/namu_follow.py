"""나무 모의 계좌로 국장 주도주 Paper(② 수급) 따라가기 — 가상 700 만 원, 실제 돈 없음.

    python -m scripts.namu_follow show           # 오늘 낼 주문 미리 보기 (주문 없음)
    python -m scripts.namu_follow run            # 장 마감 전 창(15:00 ~ 15:20 KST)이면 주문
    python -m scripts.namu_follow run --now      # 창 밖이어도 지금 (수동 시험)

현황 봇(``telegram_bot run``, 2 분마다)이 거래일 15:00 ~ 15:20 에 하루 한 번 부른다
(새 예약 작업 없음).
판단은 :mod:`auto_trading.ops.namu_follow`. 장부 ``data/namu_follow/ledger.json``,
날마다 결과 ``reports/namu_follow/<날짜>.json``. 매일 Paper 요약(21:30)에 한 줄로 나온다.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from auto_trading.config.secrets import load_namu_credentials
from auto_trading.exchange.namu import NamuMock, NamuOrder, NamuTokenCache
from auto_trading.ops.namu_follow import Ledger, Order, plan

REPO = Path(__file__).resolve().parent.parent
KST = ZoneInfo("Asia/Seoul")
RULE = "flow_rule"
"""따라가는 Paper — ② 수급 (사용자의 국장 방향: 수급 주도주)."""
WINDOW = (time(15, 0), time(15, 20))
"""종가(15:30) 실행인 Paper 와 가깝게, 마감 동시호가(15:20 ~) 전 연속 매매 시간."""
STALE_DAYS = 5
WAIT_S = 20.0
POLL_S = 3.0


def _paths(root: Path) -> tuple[Path, Path, Path]:
    return (
        root / "reports" / "kr_shadow" / "latest.json",
        root / "data" / "namu_follow" / "ledger.json",
        root / "reports" / "namu_follow",
    )


def _load_ledger(path: Path) -> tuple[Ledger, dict[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    return Ledger.from_json(raw), raw


def _client(root: Path) -> NamuMock:
    return NamuMock(
        load_namu_credentials(root / ".env"), NamuTokenCache(root / "data" / "namu_token.json")
    )


def _held(nh: NamuMock) -> dict[str, int]:
    """지금 수량 = 잔량수량(``rsdl_qty``) — 통합잔고는 결제(D+2)분만이다."""
    _, rows = nh.balance()
    out: dict[str, int] = {}
    for r in rows:
        code = str(r.get("iem_cd") or "").strip()
        q = int(float(r.get("rsdl_qty") or 0))
        if code and q > 0:
            out[code] = q
    return out


def _quotes(nh: NamuMock, codes: set[str]) -> tuple[dict[str, int], dict[str, int]]:
    ask: dict[str, int] = {}
    bid: dict[str, int] = {}
    for c in sorted(codes):
        q = nh.quote(c)
        ask[c], bid[c] = int(q.get("askp1") or 0), int(q.get("bidp1") or 0)
    return ask, bid


def _execute(nh: NamuMock, day: str, orders: list[Order]) -> list[dict[str, Any]]:
    """주문을 한꺼번에 넣고 ``WAIT_S`` 까지 체결을 본 뒤, 남은 수량은 취소한다."""
    placed: list[tuple[Order, int | None, str]] = []
    for o in orders:
        try:
            placed.append((o, nh.limit(o.side, o.code, o.qty, o.price), ""))
        except Exception as exc:  # 한 종목 거절이 나머지를 막지 않게
            placed.append((o, None, f"{type(exc).__name__}: {exc}"))
    waited = 0.0
    seen: dict[int, NamuOrder] = {}
    while True:
        seen = {x.order_no: x for x in nh.orders(day)}
        open_ = [n for _, n, _ in placed if n and (n not in seen or seen[n].open_qty > 0)]
        if not open_ or waited >= WAIT_S:
            break
        nh.sleep(POLL_S)
        waited += POLL_S
    out = []
    for o, no, err in placed:
        x = seen.get(no) if no else None
        if x is not None and x.open_qty > 0:
            try:
                nh.cancel(no, o.code)  # type: ignore[arg-type]
            except Exception as exc:  # 취소 실패는 기록만 — 다음 날 맞춘다
                err = f"취소 실패 {type(exc).__name__}: {exc}"
        out.append(
            {
                "side": o.side,
                "code": o.code,
                "qty": o.qty,
                "limit": o.price,
                "why": o.why,
                "order_no": no,
                "filled": x.filled if x else 0,
                "avg_price": x.avg_price if x else 0.0,
                "error": err or (x.reject if x else ""),
            }
        )
    return out


def _paper(root: Path) -> dict[str, Any]:
    report = json.loads(_paths(root)[0].read_text(encoding="utf-8"))
    paper = dict((report.get("paper") or {}).get(RULE) or {})
    paper["date"] = report.get("date")
    paper["kospi_above_200d"] = report.get("kospi_above_200d")
    return paper


def show(root: Path = REPO) -> dict[str, Any]:
    paper = _paper(root)
    ledger, _ = _load_ledger(_paths(root)[1])
    nh = _client(root)
    try:
        held = _held(nh)
        codes = set(held) | {str(p["code"]) for p in paper.get("positions") or []}
        codes |= set(paper.get("buy_next_close") or [])
        ask, bid = _quotes(nh, codes)
    finally:
        nh.close()
    orders = plan(paper, held, ask, bid, ledger)
    return {
        "paper_date": paper.get("date"),
        "kospi_above_200d": paper.get("kospi_above_200d"),
        "paper_positions": [p["code"] for p in paper.get("positions") or []],
        "sell_next_close": paper.get("sell_next_close"),
        "buy_next_close": paper.get("buy_next_close"),
        "held": held,
        "cash": round(ledger.cash),
        "equity": round(ledger.equity(held, {c: float(v) for c, v in bid.items()})),
        "orders": [
            {"side": o.side, "code": o.code, "qty": o.qty, "price": o.price, "why": o.why}
            for o in orders
        ],
    }


def _open_today(root: Path, day: date) -> bool:
    if day.weekday() >= 5:
        return False
    try:
        from auto_trading.config.secrets import load_toss_credentials
        from auto_trading.exchange.toss import TossReader, TossTokenCache

        r = TossReader(
            load_toss_credentials(root / ".env"), TossTokenCache(root / "data" / "toss_token.json")
        )
        try:
            return r.kr_open_today(day.isoformat())
        finally:
            r.close()
    except Exception:  # 휴장 조회 실패 — 평일이면 시도 (휴장이면 주문이 거절될 뿐)
        return True


def run(root: Path = REPO, now: datetime | None = None, force: bool = False) -> dict[str, Any]:
    now = now or datetime.now(KST)
    day = now.date()
    _report, ledger_path, out_dir = _paths(root)
    ledger, raw = _load_ledger(ledger_path)
    if raw.get("last_run") == day.isoformat():
        return {"skip": "오늘 이미 함"}
    if not force:
        if not (WINDOW[0] <= now.time() < WINDOW[1]):
            return {"skip": "창 밖"}
        if not _open_today(root, day):
            return {"skip": "휴장"}
    paper = _paper(root)
    pday = date.fromisoformat(str(paper.get("date")))
    if (day - pday).days > STALE_DAYS or pday > day:
        return {"skip": f"Paper 기록이 오래됨({pday})"}
    ymd = day.strftime("%Y%m%d")
    nh = _client(root)
    try:
        held = _held(nh)
        codes = set(held) | {str(p["code"]) for p in paper.get("positions") or []}
        codes |= set(paper.get("buy_next_close") or [])
        ask, bid = _quotes(nh, codes)
        orders = plan(paper, held, ask, bid, ledger)
        done: list[dict[str, Any]] = []
        for side in ("sell", "buy"):  # 판 돈으로 사도록 매도 먼저
            batch = [o for o in orders if o.side == side]
            if batch:
                done += _execute(nh, ymd, batch)
        for d in done:
            if d["filled"]:
                ledger.fill(day.isoformat(), d["side"], d["code"], d["filled"], d["avg_price"])
        held = _held(nh)
        ask, bid = _quotes(nh, set(held))
    finally:
        nh.close()
    equity = ledger.equity(held, {c: float(v) for c, v in bid.items()})
    ledger.history = [h for h in ledger.history if h[0] != day.isoformat()]
    ledger.history.append([day.isoformat(), round(equity)])
    result = {
        "day": day.isoformat(),
        "paper_date": paper.get("date"),
        "kospi_above_200d": paper.get("kospi_above_200d"),
        "orders": done,
        "held": held,
        "cash": round(ledger.cash),
        "equity": round(equity),
    }
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(
        json.dumps({**ledger.to_json(), "last_run": day.isoformat(), "rule": RULE}, indent=1),
        encoding="utf-8",
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{day.isoformat()}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return result


def main(argv: list[str]) -> int:
    if argv == ["show"]:
        out = show()
    elif argv and argv[0] == "run" and set(argv[1:]) <= {"--now"}:
        out = run(force="--now" in argv)
    else:
        raise SystemExit("usage: python -m scripts.namu_follow show | run [--now]")
    sys.stdout.write(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
