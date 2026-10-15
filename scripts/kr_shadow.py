"""문서 143 — 국장 주도주·테마 그림자 기록 (앞으로의 자료). 주문 없음.

사용 예::

    python -m scripts.kr_shadow init     # 141 자료에서 지금 상장 보통주 일봉을 복사 (한 번)
    python -m scripts.kr_shadow daily    # update → flows → run → news → ai
    python -m scripts.kr_shadow status

매 거래일 저녁에 세 목록을 나란히 기록한다 — 몇 달 뒤 채점(143 §4):

1. **가격 주도주** — 141 LT-KR 의 점수(1·3·6 달 수익) · 섹터 3 · 종목 10.
2. **수급 주도주** — 142 FLOW 의 점수(외국인 + 기관 순매수 ÷ 거래대금, 20·60 일).
   같은 틀로 **외국인만** 본 목록 · Paper 도 함께(2026-10-15 사용자).
3. **AI 판단** — 그날 모은 한국 경제 뉴스 제목과 종목군(코드·이름)만 주고 Gemini 에게
   주도 테마·주도주·KOSPI 여지를 묻는다. 기계 목록과 가격 지표는 주지 않는다(베끼면 비교가 안 됨).

두 기계 규칙은 가상 Paper 로도 굴린다(141·142 의 `simulate_kr`, 2026-10-06 부터).
판단일은 2026-10-15 사용자 요청으로 월말 → 2 주마다(15 일 이전 마지막 거래일 · 월말).
문서 147: 세 목록·종목군·KOSPI 에 매달 같은 금액을 넣고 팔지 않는 **장기 적립 장부**도 굴린다.
자료는 141·142 연구 자료(해시 고정)를 건드리지 않게 ``data/kr_shadow/`` 에 따로 둔다.
한국투자증권은 조회만 쓴다.
"""

from __future__ import annotations

import json
import shutil
import sys
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

from scripts.fetch_kr_stocks import (
    DATA as RESEARCH,
)
from scripts.fetch_kr_stocks import (
    _flow_pages,
    _kis,
    _pages,
    index_pages,
)
from scripts.run_kr_flow import flow_picker, flow_scores, load_flows
from scripts.run_lt_kr import (
    UNKNOWN,
    biweekly_day,
    build_panel,
    equal_weight_pit,
    ksic_sector,
    leaders_pit,
    members,
    simulate_kr,
)
from scripts.run_lt_leaders import trend_up

from auto_trading.ops.news_rss import NewsItem, dedupe, parse_feed
from auto_trading.ops.theme_acc import accumulate, cohort_returns, irr

REPO = Path(__file__).resolve().parent.parent
ROOT = REPO / "data" / "kr_shadow"
PRICES = ROOT / "prices"
FLOWS = ROOT / "flows"
NEWS = ROOT / "news"
AI = ROOT / "ai"
REPORTS = REPO / "reports" / "kr_shadow"
PAPER_START = "2026-10-06"
ACC_MONTHLY = 1_000_000
"""문서 147 장기 적립: 장부마다 매달 넣는 가상 금액(원)."""
AI_LOOKBACK = 5
"""월말 AI 기록이 없으면 그 전 5 거래일 안의 가장 최근 기록을 쓴다."""
FLOW_DAYS = 100
"""수급은 최근 100 일(달력)을 받아 기존 기록과 합친다 — 60 거래일 창을 채운다."""
FLOW_POOL = 250
"""수급을 받을 종목: 거래대금 상위 250 (종목군 200 + 여유)."""
UA = "AutoTradingResearch/1.0 (https://github.com/seoloo; personal research) httpx"
_QUERIES = {
    "gnews_kr_market": "코스피 증시",
    "gnews_kr_leaders": "주도주",
    "gnews_kr_theme": "테마주 강세",
    "gnews_kr_flows": "외국인 순매수",
    "gnews_kr_sector": "업종 강세",
    "gnews_kr_outlook": "증시 전망",
}
KR_FEEDS: dict[str, str] = {
    name: f"https://news.google.com/rss/search?q={quote(q)}&hl=ko&gl=KR&ceid=KR:ko"
    for name, q in _QUERIES.items()
}
"""Google 뉴스 RSS 한국어 검색 (공개 주소, 가입·키 없음).
네이버 금융은 robots.txt 가 막아 쓰지 않는다."""


# ------------------------------------------------------------------ 순수


def merge_csv(text: str, rows: Iterable[Sequence[str]]) -> str:
    """머리말 있는 CSV 에 새 행을 날짜(첫 칸) 기준으로 합친다 — 같은 날짜는 새 값."""
    lines = text.splitlines()
    head, old = lines[0], [line.split(",") for line in lines[1:] if line]
    merged = {r[0]: list(r) for r in old}
    for r in rows:
        merged[r[0]] = list(r)
    return head + "\n" + "".join(",".join(merged[d]) + "\n" for d in sorted(merged))


def _next_day(yyyymmdd: str) -> str:
    d = date(int(yyyymmdd[:4]), int(yyyymmdd[4:6]), int(yyyymmdd[6:]))
    return (d + timedelta(days=1)).strftime("%Y%m%d")


def _today() -> str:
    return datetime.now().strftime("%Y%m%d")  # noqa: DTZ005 — PC 시계(KST) 기준 날짜


# ------------------------------------------------------------------ 단계


def init() -> dict[str, object]:
    """141 자료에서 지금 상장된 보통주만 복사한다. 이미 있으면 건드리지 않는다."""
    if PRICES.exists() and any(PRICES.glob("*.csv")):
        return {"skipped": f"이미 있다: {PRICES}"}
    info = json.loads((RESEARCH / "info.json").read_text(encoding="utf-8"))
    PRICES.mkdir(parents=True, exist_ok=True)
    n = 0
    for code, v in info.items():
        src = RESEARCH / "prices" / f"{code}.csv"
        if v.get("kind") == "101" and v.get("kind_listed") and src.exists():
            shutil.copyfile(src, PRICES / src.name)
            n += 1
    shutil.copyfile(RESEARCH / "info.json", ROOT / "info.json")
    shutil.copyfile(RESEARCH / "kospi.csv", ROOT / "kospi.csv")
    return {"copied": n}


def update() -> dict[str, object]:
    """일봉·KOSPI 를 마지막 날 다음 날부터 오늘까지 이어 받는다."""
    today = _today()
    kis = _kis()
    done = failed = 0
    try:
        kospi = ROOT / "kospi.csv"
        text = kospi.read_text(encoding="utf-8")
        lo = _next_day(text.splitlines()[-1].split(",")[0])
        if lo <= today:
            got = index_pages(kis._get, lo, today)
            kospi.write_text(
                merge_csv(text, [(d, f"{c:g}") for d, c in got]), encoding="utf-8", newline="\n"
            )
        for path in sorted(PRICES.glob("*.csv")):
            text = path.read_text(encoding="utf-8")
            last = text.splitlines()[-1].split(",")[0]
            lo = _next_day(last)
            if lo > today:
                continue
            try:
                rows = _pages(kis._get, path.stem, lo, today)
            except Exception:  # 한 종목 실패가 전체를 멈추지 않게
                failed += 1
                continue
            if rows:
                new = [(d, f"{c:g}", f"{v:g}", f"{a:g}") for d, c, v, a in rows]
                path.write_text(merge_csv(text, new), encoding="utf-8", newline="\n")
            done += 1
    finally:
        kis.close()
    return {"updated": done, "failed": failed, "to": today}


def flows() -> dict[str, object]:
    """거래대금 상위 250 의 최근 100 일 수급을 받아 기존 기록과 합친다."""
    panel, _ok = build_panel(ROOT, to="9999-12-31")
    t = len(panel.days) - 1
    pool = members(panel, t, size=FLOW_POOL)
    # 오늘 날짜를 장중에 물으면 "TIME LIMIT 00:00 ~ 15:40" 으로 거절된다 → 일봉의 마지막 거래일까지
    hi = panel.days[t].replace("-", "")
    lo = (date.fromisoformat(panel.days[t]) - timedelta(days=FLOW_DAYS)).strftime("%Y%m%d")
    FLOWS.mkdir(parents=True, exist_ok=True)
    kis = _kis()
    done = failed = 0
    head = "date,frgn_ntby_tr_pbmn,orgn_ntby_tr_pbmn,prsn_ntby_tr_pbmn,acml_tr_pbmn\n"
    try:
        for code in pool:
            path = FLOWS / f"{code}.csv"
            try:
                rows = _flow_pages(kis._get, code, lo, hi)
            except Exception:  # 한 종목 실패가 전체를 멈추지 않게
                failed += 1
                continue
            text = path.read_text(encoding="utf-8") if path.exists() else head
            path.write_text(merge_csv(text, rows), encoding="utf-8", newline="\n")
            done += 1
    finally:
        kis.close()
    return {"flows": done, "failed": failed, "pool": len(pool), "to": hi}


def shown_sector(ksic: str) -> str:
    """화면 · 보고서에 적는 섹터.

    연구 표(:func:`ksic_sector`)는 중분류 27(의료 · 정밀 · 광학기기)을 통째로 헬스케어로 묶어
    반도체 검사 장비(디아이 · 케이씨텍, 소분류 272)까지 헬스케어로 보였다
    (사용자 지적 2026-10-15). 27 은 의료용 기기(271)만 헬스케어, 나머지(272 · 273)는 IT 로 적는다.
    Paper · 목록을 고르는 규칙은 검증한 그대로 :func:`ksic_sector` 를 쓴다.
    """
    if ksic[2:4] == "27" and ksic[4:6] != "01":
        return "IT"
    return ksic_sector(ksic)


def _sector_rank(scores: dict[str, float], sector: dict[str, str]) -> list[dict[str, object]]:
    import statistics

    by: dict[str, list[float]] = defaultdict(list)
    for s, v in scores.items():
        if sector[s] != UNKNOWN:
            by[sector[s]].append(v)
    ranked = sorted(by, key=lambda k: (statistics.median(by[k]), k), reverse=True)
    return [
        {"sector": k, "median": round(statistics.median(by[k]), 3), "n": len(by[k])} for k in ranked
    ]


def _paper(run: Any, names: dict[str, str]) -> dict[str, object]:
    eq = run.equity
    return {
        "value_krw": round(10_000_000 * eq[-1] / eq[0]) if eq else None,
        "positions": [
            {
                "code": s,
                "name": names.get(s, s),
                "entry": p["entry"],
                "return": round(float(p["value"]) / float(p["cost"]) - 1, 4),
                "weight": round(float(p["value"]) / eq[-1], 4) if eq else None,
            }
            for s, p in sorted(run.positions.items())
        ],
        "sell_next_close": dict(run.pending_sells),
        "buy_next_close": run.pending_buys,
        "trades": run.trades[-10:],
    }


def _ai_basket(days: Sequence[str], t: int, known: set[str]) -> list[str] | None:
    """월말 ``t`` 의 AI 주도주(그날 기록, 없으면 그 전 ``AI_LOOKBACK`` 거래일 안 최근 기록)."""
    for k in range(AI_LOOKBACK + 1):
        if t - k < 0:
            break
        path = AI / f"{days[t - k]}.json"
        if path.exists():
            rec = json.loads(path.read_text(encoding="utf-8"))
            leaders = (rec.get("judgement") or {}).get("leaders") or []
            return [str(x["symbol"]) for x in leaders if str(x.get("symbol")) in known]
    return None


def _accumulation(panel: Any, lt: Any, fl: Any) -> dict[str, object]:
    """문서 147 — 다섯 장부에 매달 ``ACC_MONTHLY`` 를 넣고 팔지 않는다."""
    days = panel.days
    start = next(i for i, d in enumerate(days) if d >= PAPER_START)
    month_ends = [t for t in range(start, len(days) - 1) if days[t + 1][:7] != days[t][:7]]
    known = set(panel.close)
    rows = (ROOT / "kospi.csv").read_text(encoding="utf-8").splitlines()[1:]
    kospi_map = {f"{r[:4]}-{r[4:6]}-{r[6:8]}": float(r.split(",")[1]) for r in rows}
    close: dict[str, Sequence[float | None]] = dict(panel.close)
    close["KOSPI"] = [kospi_map.get(d) for d in days]
    ai_missing: list[str] = []
    baskets: dict[str, dict[int, list[str]]] = {
        k: {} for k in ("ai", "price", "flow", "ew", "kospi")
    }
    for t in month_ends:
        d = days[t]
        ai = _ai_basket(days, t, known)
        if ai is None:
            ai_missing.append(d)
        baskets["ai"][t] = ai or []
        baskets["price"][t] = list(lt.leader_log.get(d, []))
        baskets["flow"][t] = list(fl.leader_log.get(d, []))
        baskets["ew"][t] = list(lt.universe_log.get(d, []))
        baskets["kospi"][t] = ["KOSPI"]
    books = {
        key: accumulate(days, close, b, monthly=ACC_MONTHLY, start=start)
        for key, b in baskets.items()
    }
    books["ai_trend"] = accumulate(
        days,
        close,
        baskets["ai"],
        monthly=ACC_MONTHLY,
        start=start,
        keep=lambda sym, t: trend_up(panel.close[sym], t),
    )
    last = len(days) - 1
    ledgers = {}
    for key, b in books.items():
        value = b.equity[-1] if b.equity else 0.0
        r = irr(b.flows, last, value) if b.flows and last - b.flows[0][0] >= 21 else None
        ledgers[key] = {
            "contributed": round(b.contributed),
            "value": round(value),
            "return": round(value / b.contributed - 1, 4) if b.contributed else None,
            "irr": round(r, 4) if r is not None else None,
            "holdings": sum(1 for q in b.qty.values() if q > 0),
            "cash": round(b.cash),
        }
    cohorts = []
    for t in month_ends:
        if t + 1 > last:
            continue
        row: dict[str, object] = {"month_end": days[t]}
        for key in ("ai", "price", "flow", "ew", "kospi"):
            r = cohort_returns(close, baskets[key][t], t, last)
            row[key] = round(r, 4) if r is not None else None
        row["ai_names"] = baskets["ai"][t]
        cohorts.append(row)
    return {
        "start": PAPER_START,
        "monthly_krw": ACC_MONTHLY,
        "ledgers": ledgers,
        "cohorts_to_date": cohorts,
        "ai_missing_month_ends": ai_missing,
    }


def run() -> dict[str, object]:
    """오늘(마지막 거래일) 세 목록·섹터 순위·Paper 를 계산해 보고서를 쓴다."""
    info = json.loads((ROOT / "info.json").read_text(encoding="utf-8"))
    names = {c: str(v.get("name", c)) for c, v in info.items()}
    panel, index_ok = build_panel(ROOT, to="9999-12-31")
    shown = {c: shown_sector(str(info.get(c, {}).get("ksic", ""))) for c in panel.close}
    f = load_flows(panel, FLOWS)
    ff = load_flows(panel, FLOWS, who="foreign")  # 외국인만 (사용자 2026-10-15)
    t = len(panel.days) - 1
    day = panel.days[t]
    pool = members(panel, t)
    price = leaders_pit(panel, t, pool)
    flow = flow_picker(f)(panel, t, pool)
    foreign = flow_picker(ff)(panel, t, pool)
    import statistics

    from scripts.run_lt_leaders import LOOKS

    price_scores: dict[str, float] = defaultdict(float)
    for k in LOOKS:
        rets = {
            s: panel.close[s][t] / panel.close[s][t - k] - 1  # type: ignore[operator]
            for s in pool
        }
        ordered = sorted(pool, key=lambda s: (rets[s], s))
        for i, s in enumerate(ordered):
            price_scores[s] += i / (len(ordered) - 1) / len(LOOKS)

    def listed(rows: Sequence[tuple[str, float]]) -> list[dict[str, object]]:
        return [
            {
                "code": s,
                "name": names.get(s, s),
                "sector": shown[s],
                "score": round(v, 3),
                "trend": trend_up(panel.close[s], t),
            }
            for s, v in rows
        ]

    report: dict[str, Any] = {
        "date": day,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "universe": len(pool),
        "kospi_above_200d": index_ok[t],
        "price_leaders": listed(price),
        "flow_leaders": listed(flow),
        "foreign_leaders": listed(foreign),
        "price_sectors": _sector_rank(dict(price_scores), shown),
        "flow_sectors": _sector_rank(flow_scores(f, t, pool), shown),
        "foreign_sectors": _sector_rank(flow_scores(ff, t, pool), shown),
        "flow_coverage": sum(1 for s in pool if s in f.net),
        "universe_median_6m": round(
            statistics.median(panel.close[s][t] / panel.close[s][t - 126] - 1 for s in pool),  # type: ignore[operator]
            4,
        ),
    }
    if day >= PAPER_START:
        # 판단은 2 주마다(15 일 무렵 · 월말) — 사용자 2026-10-15. 141 · 142 연구는 월말 판단이었다.
        lt = simulate_kr(panel, index_ok, None, start_date=PAPER_START, decide=biweekly_day)
        fl = simulate_kr(
            panel, index_ok, flow_picker(f), start_date=PAPER_START, decide=biweekly_day
        )
        fr = simulate_kr(
            panel, index_ok, flow_picker(ff), start_date=PAPER_START, decide=biweekly_day
        )
        ew = equal_weight_pit(panel, lt.universe_log, start_date=PAPER_START)
        report["paper"] = {
            "start": PAPER_START,
            "price_rule": _paper(lt, names),
            "flow_rule": _paper(fl, names),
            "foreign_rule": _paper(fr, names),
            "equal_weight_krw": round(10_000_000 * ew[-1] / ew[0]),
        }
        report["accumulation"] = _accumulation(panel, lt, fl)
    REPORTS.mkdir(parents=True, exist_ok=True)
    same_day = REPORTS / f"{day}.json"
    if same_day.exists():  # 같은 날 다시 돌리면 이미 모은 뉴스 기록은 그대로 둔다
        before = json.loads(same_day.read_text(encoding="utf-8"))
        if "news" in before:
            report["news"] = before["news"]
    _save_report(report)
    return {
        "date": day,
        "price_leaders": [x["name"] for x in report["price_leaders"]],
        "flow_leaders": [x["name"] for x in report["flow_leaders"]],
        "foreign_leaders": [x["name"] for x in report["foreign_leaders"]],
    }


def _save_report(report: dict[str, Any]) -> None:
    for path in (REPORTS / f"{report['date']}.json", REPORTS / "latest.json"):
        path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")


def news() -> dict[str, object]:
    import httpx

    report = json.loads((REPORTS / "latest.json").read_text(encoding="utf-8"))
    fetched_at = datetime.now(UTC).isoformat(timespec="seconds")
    items: list[NewsItem] = []
    failed: list[str] = []
    with httpx.Client(headers={"User-Agent": UA}, timeout=20, follow_redirects=True) as client:
        for name, url in KR_FEEDS.items():
            try:
                resp = client.get(url)
                resp.raise_for_status()
            except Exception:
                failed.append(name)
                continue
            got = parse_feed(resp.text, name, fetched_at)
            if not got:
                failed.append(name)
            items.extend(got)
    items = dedupe(items)
    NEWS.mkdir(parents=True, exist_ok=True)
    path = NEWS / f"{report['date']}.jsonl"
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        for it in items:
            fh.write(json.dumps(it.to_json(), ensure_ascii=False) + "\n")
    report["news"] = {
        "file": path.relative_to(REPO).as_posix(),
        "items": len(items),
        "sources_failed": failed,
        "fetched_at": fetched_at,
    }
    _save_report(report)
    return report["news"]  # type: ignore[no-any-return]


def ai() -> dict[str, object]:
    """그날 뉴스로 AI 판단 기록. Gemini 우선(115 와 같은 호출). 매매에 쓰지 않는다."""
    import hashlib

    from scripts.lt_paper import _ask_gemini, _ask_openai

    from auto_trading.config.secrets import load_gemini_key, load_openai_key
    from auto_trading.ops.news_ai import compare, parse_reply
    from auto_trading.ops.news_ai_kr import build_messages

    gemini = load_gemini_key(REPO / ".env")
    openai = load_openai_key(REPO / ".env")
    if gemini is None and openai is None:
        return {"skipped": "GEMINI_API_KEY · OPENAI_API_KEY 가 .env 에 없다"}
    report = json.loads((REPORTS / "latest.json").read_text(encoding="utf-8"))
    news_file = (report.get("news") or {}).get("file")
    if not news_file:
        return {"skipped": "그날 뉴스 기록이 없다 — news 먼저"}
    items = [
        json.loads(line)
        for line in (REPO / news_file).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    info = json.loads((ROOT / "info.json").read_text(encoding="utf-8"))
    panel, _ok = build_panel(ROOT, to="9999-12-31")
    pool = members(panel, len(panel.days) - 1)
    universe = {c: str(info.get(c, {}).get("name", c)) for c in pool}
    messages = build_messages(report["date"], items, universe)
    answer = (
        _ask_gemini(gemini.value, messages)
        if gemini is not None
        else _ask_openai(openai.value, messages)  # type: ignore[union-attr]
    )
    if "error" in answer:
        return answer
    try:
        judgement = parse_reply(str(answer["text"]), sorted(universe))
    except ValueError as exc:
        return {"error": f"답 형식 오류: {exc}"}
    ai_codes = [x["symbol"] for x in judgement["leaders"]]
    record = {
        "date": report["date"],
        "model": answer.get("model"),
        "asked_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "news_items_given": min(len(items), 150),
        "prompt_sha256": hashlib.sha256(
            json.dumps(messages, ensure_ascii=False).encode()
        ).hexdigest(),
        "usage": answer.get("usage"),
        "judgement": judgement,
        "vs_price": compare([x["code"] for x in report["price_leaders"]], ai_codes),
        "vs_flow": compare([x["code"] for x in report["flow_leaders"]], ai_codes),
    }
    AI.mkdir(parents=True, exist_ok=True)
    (AI / f"{report['date']}.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    report["ai"] = {
        "model": record["model"],
        "market_room": judgement["market_room"],
        "confidence": judgement["confidence"],
        "themes": judgement["leading_sectors"],
        "leaders": [{"code": c, "name": universe.get(c, c)} for c in ai_codes],
        "summary": judgement["summary"],
        "overlap_price": record["vs_price"]["both"],
        "overlap_flow": record["vs_flow"]["both"],
    }
    _save_report(report)
    return {"ok": True, "themes": judgement["leading_sectors"], "leaders": ai_codes}


def daily() -> dict[str, object]:
    out: dict[str, object] = {"update": update(), "flows": flows(), "run": run()}
    for name, step in (("news", news), ("ai", ai)):
        try:
            out[name] = step()
        except Exception as exc:  # 뉴스·AI 실패가 기록 전체를 막지 않게
            out[name] = {"error": f"{type(exc).__name__}: {exc}"[:300]}
    return out


def status() -> dict[str, object]:
    latest = REPORTS / "latest.json"
    return (
        json.loads(latest.read_text(encoding="utf-8"))
        if latest.exists()
        else {"note": "아직 실행 전"}
    )


def main(argv: list[str]) -> int:
    stages = {
        "init": init,
        "update": update,
        "flows": flows,
        "run": run,
        "news": news,
        "ai": ai,
        "daily": daily,
        "status": status,
    }
    if len(argv) != 1 or argv[0] not in stages:
        raise SystemExit(f"usage: python -m scripts.kr_shadow {'|'.join(stages)}")
    sys.stdout.write(json.dumps(stages[argv[0]](), ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
