"""토스 해외주식 양도소득 추정 (조회 전용 — 주문 · 상태 변경 없음).

사용 예::

    python -m scripts.us_tax            # 올해
    python -m scripts.us_tax 2025       # 다른 해

토스 체결 기록(주문 목록 CLOSED) · 보유 · 한국은행 ECOS 매매기준율(``ECOS_API_KEY``)로
``auto_trading.ops.us_capital_gains`` 를 돌려 올해 실현 손익 · 남은 250 만 원 공제 · 예상 세금,
지금 보유를 팔면 생길 손익을 보여 준다. M3 주문(``data/toss/M3.json`` 의 주문 번호)은 따로 묶는다.
보고 ``reports/us_tax/<KST 날짜>.json``.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from auto_trading.config.secrets import load_toss_credentials, read_env_file
from auto_trading.exchange.toss import TossFill, TossReader, TossTokenCache
from auto_trading.ops.us_capital_gains import (
    DEDUCTION_KRW,
    Opening,
    Trade,
    build,
    by_tag,
    tax_settle,
    unrealized,
    year_summary,
)

REPO = Path(__file__).resolve().parent.parent
TOKEN = REPO / "data" / "toss_token.json"
M3_STATE = REPO / "data" / "toss" / "M3.json"
REPORTS = REPO / "reports" / "us_tax"
ECOS = "https://ecos.bok.or.kr/api/StatisticSearch/{key}/json/kr/1/{n}/731Y001/D/{a}/{b}/0000001"


def _today_kst() -> str:
    return (datetime.now(UTC) + timedelta(hours=9)).date().isoformat()


def ecos_usd_krw(key: str, start: str, end: str) -> dict[str, float]:
    """원/미국달러 매매기준율 (일별). 키는 주소에만 쓰고 오류에 싣지 않는다."""
    a, b = start.replace("-", ""), end.replace("-", "")
    try:
        r = httpx.get(ECOS.format(key=key, n=5000, a=a, b=b), timeout=30.0)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"ECOS 요청 실패 ({type(exc).__name__})") from None
    rows = (r.json().get("StatisticSearch") or {}).get("row") or []
    out = {}
    for row in rows:
        t = str(row.get("TIME") or "")
        v = str(row.get("DATA_VALUE") or "").replace(",", "")
        if len(t) == 8 and v:
            out[f"{t[:4]}-{t[4:6]}-{t[6:]}"] = float(v)
    if not out:
        raise RuntimeError("ECOS 환율이 비어 있다 (키 · 기간 확인)")
    return out


def _m3_ids() -> set[str]:
    if not M3_STATE.is_file():
        return set()
    state = json.loads(M3_STATE.read_text(encoding="utf-8"))
    return {str(o.get("order_id")) for o in state.get("orders") or [] if o.get("order_id")}


def trades_from(fills: list[TossFill], m3_ids: set[str]) -> list[Trade]:
    out = []
    for f in fills:
        if f.currency != "USD":
            continue
        out.append(
            Trade(
                f.symbol,
                f.side,
                float(f.qty),
                float(f.amount),
                float(f.commission + f.tax),
                tax_settle(f.filled_at),
                f.filled_at,
                "M3" if f.order_id in m3_ids else "",
            )
        )
    return out


def _won(x: float) -> str:
    return f"{x:+,.0f}원" if x else "0원"


def compute(reader: TossReader, key: str, m3_ids: set[str], year: str) -> dict[str, Any]:
    """토스 체결 · 보유 · ECOS 환율 → 그해 양도소득 추정 (조회만). 텔레그램 알림에서도 쓴다."""
    fills = reader.fills()
    hold = {s: p for s, p in reader.holdings().items() if p.currency == "USD"}
    fx_now = float(reader.usd_krw())
    trades = trades_from(fills, m3_ids)
    first = min([t.settle for t in trades] + [_today_kst()])
    rates = ecos_usd_krw(
        key, (datetime.fromisoformat(first) - timedelta(days=10)).date().isoformat(), _today_kst()
    )
    # 기록보다 많이 가진 주식 → 평균 단가로 어림 (가장 이른 환율)
    net: dict[str, float] = defaultdict(float)
    for t in trades:
        net[t.symbol] += t.qty if t.side == "BUY" else -t.qty
    openings = [
        Opening(s, float(p.qty) - net[s], float(p.avg_price), first)
        for s, p in hold.items()
        if float(p.qty) - net[s] > 1e-9
    ]
    book = build(trades, rates, openings)
    per_sym: dict[str, float] = defaultdict(float)
    for r in book.realized:
        if r.settle[:4] == year:
            per_sym[r.symbol] += r.gain_krw
    unreal = unrealized(book, {sym: float(p.last) for sym, p in hold.items()}, fx_now)
    return {
        "year": year,
        "book": book,
        "summary": year_summary(book, year),
        "by_tag": by_tag(book, year),
        "by_symbol": dict(per_sym),
        "unrealized": unreal,
        "unrealized_total": sum(u["gain_krw"] for u in unreal.values()),
        "fx_now": fx_now,
        "openings": openings,
        "trades": trades,
        "holdings": hold,
    }


def main(argv: list[str]) -> int:
    year = argv[0] if argv else _today_kst()[:4]
    key = read_env_file(REPO / ".env").get("ECOS_API_KEY", "")
    if not key:
        print("ECOS_API_KEY 가 .env 에 없다")
        return 1
    reader = TossReader(load_toss_credentials(REPO / ".env"), TossTokenCache(TOKEN))
    try:
        res = compute(reader, key, _m3_ids(), year)
    finally:
        reader.close()
    book, s, tags, per_sym = res["book"], res["summary"], res["by_tag"], res["by_symbol"]
    unreal, fx_now, openings, trades = (
        res["unrealized"],
        res["fx_now"],
        res["openings"],
        res["trades"],
    )
    hold = res["holdings"]
    # 토스 앱 평균 단가와 견주기 (달러) — 이동평균이 맞는지 대조용
    toss_avg = {sym: float(p.avg_price) for sym, p in hold.items()}

    n_sells = sum(1 for r in book.realized if r.settle[:4] == year)
    lines = [
        f"🧾 {year}년 해외주식 양도소득 추정 (결제일 기준 · ECOS 매매기준율)",
        f"체결 {len(trades)}건 (달러) · 매도 결제 {n_sells}건",
        "",
        f"실현 손익 (이동평균) {_won(s['gain_krw'])} · 선입선출 참고 {_won(s['gain_fifo_krw'])}",
        f"  M3 {_won(tags.get('M3', 0.0))} · 그 밖 {_won(tags.get('개별', 0.0))}",
        f"남은 공제 {s['deduction_left_krw']:,.0f}원 / {DEDUCTION_KRW:,.0f}원",
        f"올해 세금 없이 더 실현할 수 있는 이익 {s['tax_free_room_krw']:,.0f}원",
        f"예상 세금 {s['tax_krw']:,.0f}원 (선입선출이면 {s['tax_fifo_krw']:,.0f}원)",
        "",
        "종목별 실현 (이동평균):",
        *[f"  {sym} {_won(g)}" for sym, g in sorted(per_sym.items(), key=lambda kv: -abs(kv[1]))],
        "",
        f"지금 보유를 팔면 (현재 환율 {fx_now:,.1f}원):",
    ]
    for sym, u in sorted(unreal.items()):
        avg_krw = book.cost_krw.get(sym, 0.0) / u["qty"] if u["qty"] else 0.0
        est = " (원가 어림)" if sym in book.estimated else ""
        lines.append(
            f"  {sym} {u['qty']:g}주 · 평가 {u['value_krw']:,.0f}원 · 손익 {_won(u['gain_krw'])}"
            f" · 원가/주 {avg_krw:,.0f}원 (토스 평단 ${toss_avg.get(sym, 0):,.2f}){est}"
        )
    total_unreal = sum(u["gain_krw"] for u in unreal.values())
    lines.append(f"  합계 {_won(total_unreal)}")
    if book.unmatched:
        lines += [
            "",
            "⚠️ 원가를 모르는 매도 (기록 전 입고 · 분할 등):",
            *[f"  {x}" for x in book.unmatched],
        ]
    if openings:
        lines += ["", "ℹ️ 기록보다 많이 가진 주식 — 토스 평단 × 가장 이른 환율로 어림:"]
        lines += [f"  {o.symbol} {o.qty:g}주 @ ${o.avg_usd:,.2f}" for o in openings]
    print("\n".join(lines))

    REPORTS.mkdir(parents=True, exist_ok=True)
    body: dict[str, Any] = {
        "year": year,
        "summary": s,
        "by_tag": tags,
        "by_symbol": per_sym,
        "unrealized": unreal,
        "fx_now": fx_now,
        "unmatched": book.unmatched,
        "openings": [{"symbol": o.symbol, "qty": o.qty, "avg_usd": o.avg_usd} for o in openings],
        "realized": [
            {
                "symbol": r.symbol,
                "settle": r.settle,
                "qty": r.qty,
                "proceeds_krw": r.proceeds_krw,
                "cost_krw": r.cost_krw,
                "tag": r.tag,
                "estimated": r.estimated,
            }
            for r in book.realized
        ],
    }
    path = REPORTS / f"{_today_kst()}.json"
    path.write_bytes(json.dumps(body, ensure_ascii=False, indent=2).encode("utf-8"))
    print(f"\n보고: {path.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
