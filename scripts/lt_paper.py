"""LT Paper (문서 115) — 주도주 추세 보유를 가상 1,000 USD 로 매일 기록한다. 주문 없음.

사용 예::

    python -m scripts.lt_paper init     # 처음 한 번: 111 자료를 Paper 폴더로 복사, 가상 1,000 USD
    python -m scripts.lt_paper update   # 한국투자증권에서 최근 일봉을 받아 붙인다 (읽기 전용)
    python -m scripts.lt_paper run      # 처리 안 한 거래일을 엔진으로 처리, 보고서
    python -m scripts.lt_paper news     # 무료 RSS 뉴스 제목 모으기 (주도주·보유 종목 포함)
    python -m scripts.lt_paper ai       # 그날 뉴스로 ChatGPT 판단 기록 (키 없으면 건너뜀)
    python -m scripts.lt_paper daily    # update + run + news + ai + NQ Paper (예약 작업용)
    python -m scripts.lt_paper rebuild  # 시세를 고친 뒤 같은 시작일부터 다시 계산
    python -m scripts.lt_paper status

자료 ``data/lt_paper/bars/<SYM>.csv`` (111 연구 자료는 그대로), 상태 ``data/lt_paper/state.json``,
보고 ``reports/lt_paper/<날짜>.json`` · ``reports/lt_paper/latest.json``.
"""

from __future__ import annotations

import csv
import json
import shutil
import sys
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from auto_trading.ops.lt_paper import PaperState, step, trend_up
from auto_trading.ops.news_rss import FEEDS, YAHOO_TICKER, NewsItem, dedupe, parse_feed
from auto_trading.ops.us_daily_bars import Merge, merge, settled, to_row

REPO = Path(__file__).resolve().parent.parent
RESEARCH = REPO / "data" / "research" / "stocks"
SPY_SRC = REPO / "data" / "research" / "tradfi" / "SPY_adj.csv"
ROOT = REPO / "data" / "lt_paper"
BARS = ROOT / "bars"
STATE = ROOT / "state.json"
META = ROOT / "meta.json"
REPORTS = REPO / "reports" / "lt_paper"
NEWS = ROOT / "news"
UA = "AutoTradingResearch/1.0 (https://github.com/seoloo; personal research) httpx"
START_CASH = 1_000.0
FULL_SINCE = "2007-08-01"
OVERLAP_DAYS = 30
"""최근 30 일을 겹쳐 받아 끝난 날만 합친다 (auto_trading.ops.us_daily_bars)."""


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _write(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        w = csv.DictWriter(
            fh, fieldnames=["date", "open", "high", "low", "close", "volume"], lineterminator="\n"
        )
        w.writeheader()
        w.writerows(rows)


def init() -> dict[str, object]:
    if STATE.exists():
        raise SystemExit(f"이미 시작했다: {STATE}")
    BARS.mkdir(parents=True, exist_ok=True)
    status = json.loads((RESEARCH / "fetch_status.json").read_text(encoding="utf-8"))
    exchanges = {s: v["exchange"] for s, v in status.items() if v.get("ok")}
    for sym in exchanges:
        shutil.copyfile(RESEARCH / f"{sym}.csv", BARS / f"{sym}.csv")
    shutil.copyfile(SPY_SRC, BARS / "SPY.csv")
    sectors = json.loads((RESEARCH / "sectors.json").read_text(encoding="utf-8"))["sectors"]
    META.write_text(
        json.dumps(
            {
                "exchanges": {**exchanges, "SPY": "AMS"},
                "sectors": sectors,
                "started": datetime.now(UTC).isoformat(timespec="seconds"),
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    STATE.write_text(json.dumps(PaperState(cash=START_CASH).to_json(), indent=1), encoding="utf-8")
    return {"symbols": len(exchanges), "cash": START_CASH}


def update() -> dict[str, object]:
    """[쓰임] 시세 갱신 — 한국투자증권 실계좌 키로 받던 부분은
    실주문 코드를 걷어 낼 때 뺐다(README 6-3, 정할 것 2).
    저장된 일봉으로만 돈다. 다시 받으려면 키 없는 공개 원천을 붙인다."""
    return {
        "skipped": "시세 갱신 없음 — 실계좌 키로 받던 부분을 뺐다(README 6-3). 저장된 일봉으로만"
    }


def fetch_merge(
    broker: Any, symbol: str, excd: str, path: Path, last_ok: str, since_if_empty: str = ""
) -> Merge:
    """한 종목: 최근 ``OVERLAP_DAYS`` 를 겹쳐 받아 끝난 날만 합친다.

    수정주가가 바뀌었으면 전체를 다시 받는다.

    ``needs_full`` 이 참인 결과는 '전체를 다시 받아 썼다' 는 뜻이다.
    """
    rows = _read(path) if path.exists() else []
    full_since = since_if_empty or FULL_SINCE
    if rows:
        since = (date.fromisoformat(rows[-1]["date"]) - timedelta(days=OVERLAP_DAYS)).isoformat()
    else:
        since = full_since
    bars = broker.daily_history(symbol, since=since, exchange=excd, adjusted=True)
    m = merge(rows, settled(bars, last_ok), last_ok)
    if m.needs_full:
        full = settled(
            broker.daily_history(symbol, since=full_since, exchange=excd, adjusted=True), last_ok
        )
        rows = [to_row(d, b) for d, b in sorted(full.items())]
        m = Merge(rows, len(rows), 0, 0, True)
    if m.appended or m.repaired or m.dropped_unsettled or m.needs_full:
        _write(path, m.rows)
    return m


def _panel() -> tuple[list[str], dict[str, list[float | None]], list[float | None], dict[str, str]]:
    meta = json.loads(META.read_text(encoding="utf-8"))
    spy_rows = _read(BARS / "SPY.csv")
    days = [r["date"] for r in spy_rows]
    spy: list[float | None] = [float(r["close"]) for r in spy_rows]
    closes: dict[str, list[float | None]] = {}
    for sym in meta["exchanges"]:
        if sym == "SPY" or not (BARS / f"{sym}.csv").exists():
            continue
        m = {r["date"]: float(r["close"]) for r in _read(BARS / f"{sym}.csv")}
        closes[sym] = [m.get(d) for d in days]
    return days, closes, spy, meta["sectors"]


def run(*, rebuild_from: str | None = None) -> dict[str, object]:
    """처리 안 한 거래일을 처리한다. ``rebuild_from`` 이면 빈 계좌로 그날부터 다시 계산한다.

    다시 계산할 때 날짜별 보고서의 뉴스 · AI 기록(``news_shadow``)은 그대로 옮긴다.
    """
    days, closes, spy, sectors = _panel()
    if rebuild_from:
        state = PaperState(cash=START_CASH)
        todo = [i for i, d in enumerate(days) if d >= rebuild_from]
    else:
        state = PaperState.from_json(json.loads(STATE.read_text(encoding="utf-8")))
        if state.last_date is None:
            todo = [
                len(days) - 1
            ]  # 첫 실행: 가장 최근 거래일 하나 (어제 종가 주도주를 오늘 종가에 산다)
        else:
            todo = [i for i, d in enumerate(days) if d > state.last_date]
    REPORTS.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {}
    for t in todo:
        actions = step(state, days, closes, spy, sectors, t)
        report = _report(state, actions, days, closes, t)
        dated = REPORTS / f"{days[t]}.json"
        if rebuild_from and dated.exists():
            kept = json.loads(dated.read_text(encoding="utf-8")).get("news_shadow")
            if kept:
                report["news_shadow"] = kept
        dated.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    STATE.write_text(json.dumps(state.to_json(), indent=1), encoding="utf-8")
    if report:
        (REPORTS / "latest.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    return {
        "processed_days": len(todo),
        "last_date": state.last_date,
        "equity": state.equity[-1][1] if state.equity else None,
    }


def _report(
    state: PaperState,
    actions: dict[str, Any],
    days: list[str],
    closes: dict[str, list[float | None]],
    t: int,
) -> dict[str, Any]:
    def px(sym: str) -> float:
        xs = closes[sym]
        i = t
        while xs[i] is None:
            i -= 1
        v = xs[i]
        assert v is not None
        return v

    equity = actions["equity"]
    positions = []
    for sym, p in sorted(state.positions.items()):
        value = p.qty * px(sym)
        positions.append(
            {
                "symbol": sym,
                "entry": p.entry,
                "cost_usd": round(p.cost, 2),
                "value_usd": round(value, 2),
                "return": round(value / p.cost - 1, 4),
                "weight": round(value / equity, 4) if equity else None,
                "from_peak": round(px(sym) / p.peak - 1, 4),
            }
        )
    above = [
        s
        for s, xs in closes.items()
        if xs[t] is not None and t >= 50 and all(v is not None for v in xs[t - 49 : t + 1])
    ]
    breadth = sum(
        1
        for s in above
        if (c := closes[s][t]) is not None
        and c > sum(v for v in closes[s][t - 49 : t + 1] if v is not None) / 50
    ) / max(1, len(above))
    return {
        "date": days[t],
        "equity_usd": round(equity, 2),
        "start_usd": START_CASH,
        "return_since_start": round(equity / START_CASH - 1, 4),
        "cash_usd": round(state.cash, 2),
        "positions": positions,
        "today": {k: actions[k] for k in ("sold", "bought", "topped_up")},
        "pending_sell_next_session": actions["pending_sell"],
        "leaders_as_of": actions["leaders_as_of"],
        "leaders": [
            {"symbol": s, "score": sc, "trend_up": trend_up(closes[s], t - 1)}
            for s, sc in actions["leaders"]
        ],
        "leading_sectors": actions["leading_sectors"],
        "spy_above_200": actions["spy_above_200"],
        "breadth_above_50ma": round(breadth, 4),
        "news_shadow": None,
        "note": "문서 115 Paper — 가상 계좌, 주문 없음. news_shadow 는 2 단계(뉴스·AI)에서 채운다.",
    }


def news() -> dict[str, object]:
    """고정 출처 + 주도주·보유 종목 Yahoo 뉴스. 같은 기사는 하나만, 실패 출처는 건너뜀."""
    import httpx

    latest = REPORTS / "latest.json"
    report = json.loads(latest.read_text(encoding="utf-8")) if latest.exists() else {}
    symbols = sorted(
        {x["symbol"] for x in report.get("leaders", [])}
        | {x["symbol"] for x in report.get("positions", [])}
    )
    urls = dict(FEEDS)
    urls.update({f"yahoo_{s}": YAHOO_TICKER.format(symbol=s) for s in symbols})
    fetched_at = datetime.now(UTC).isoformat(timespec="seconds")
    items: list[NewsItem] = []
    failed: list[str] = []
    with httpx.Client(headers={"User-Agent": UA}, timeout=20, follow_redirects=True) as client:
        for name, url in urls.items():
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
    day = report.get("date") or fetched_at[:10]
    path = NEWS / f"{day}.jsonl"
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        for it in items:
            fh.write(json.dumps(it.to_json(), ensure_ascii=False) + "\n")
    summary = {
        "file": str(path.relative_to(REPO)),
        "items": len(items),
        "sources_ok": len(urls) - len(failed),
        "sources_failed": failed,
        "fetched_at": fetched_at,
        "ai_judgement": None,
    }
    if report:
        report["news_shadow"] = summary
        latest.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        dated = REPORTS / f"{report['date']}.json"
        if dated.exists():
            dated.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return summary


OPENAI_URL = "https://api.openai.com/v1/chat/completions"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_MODEL = "gpt-4o-mini"
GEMINI_MODELS = (
    "gemini-flash-latest",
    "gemini-flash-lite-latest",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
)
"""``GEMINI_MODEL`` 이 없으면 이 순서로 시도한다 (2026-10-03 모델 목록 조회로 확인한 이름)."""
AI = ROOT / "ai"


def _env(name: str, default: str = "") -> str:
    import os

    from auto_trading.config.secrets import read_env_file

    return os.environ.get(name) or read_env_file(REPO / ".env").get(name, default)


def _ask_gemini(key: str, messages: list[dict[str, str]]) -> dict[str, object]:
    """Gemini 무료 등급. 키는 머리말(x-goog-api-key)로만 보낸다 — 주소에 넣지 않는다."""
    import httpx

    body = {
        "systemInstruction": {"parts": [{"text": messages[0]["content"]}]},
        "contents": [{"role": "user", "parts": [{"text": messages[1]["content"]}]}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
    }
    models = [_env("GEMINI_MODEL")] if _env("GEMINI_MODEL") else list(GEMINI_MODELS)
    last: dict[str, object] = {"error": "모델 없음"}
    for model in models:
        try:
            resp = httpx.post(
                GEMINI_URL.format(model=model),
                headers={"x-goog-api-key": key},
                json=body,
                timeout=120,
            )
        except httpx.HTTPError as exc:
            return {"error": f"연결 실패: {type(exc).__name__}"}
        if resp.status_code in (404, 429, 503):
            # 없는 모델·한도·몰림 — 잠깐 쉬고 다음 무료 모델로 (몰림은 보통 잠깐이다)
            last = {"error": f"HTTP {resp.status_code}", "message": f"모델 {model} 거절"}
            time.sleep(5)
            continue
        if resp.status_code != 200:
            try:
                message = str(resp.json().get("error", {}).get("message", ""))[:300]
            except ValueError:
                message = ""
            return {"error": f"HTTP {resp.status_code}", "message": message}
        payload = resp.json()
        try:
            text = payload["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError):
            return {"error": "답이 비어 있다 (안전 필터 또는 형식)"}
        return {"text": text, "model": f"gemini:{model}", "usage": payload.get("usageMetadata")}
    return last


def _ask_openai(key: str, messages: list[dict[str, str]]) -> dict[str, object]:
    import httpx

    model = _env("CHATGPT_MODEL", DEFAULT_MODEL)
    body = {
        "model": model,
        "messages": messages,
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    try:
        resp = httpx.post(
            OPENAI_URL, headers={"Authorization": f"Bearer {key}"}, json=body, timeout=90
        )
    except httpx.HTTPError as exc:
        return {"error": f"연결 실패: {type(exc).__name__}"}
    if resp.status_code != 200:
        try:
            message = str(resp.json().get("error", {}).get("message", ""))[:300]
        except ValueError:
            message = ""
        return {"error": f"HTTP {resp.status_code}", "message": message}
    payload = resp.json()
    return {
        "text": payload["choices"][0]["message"]["content"],
        "model": f"openai:{payload.get('model', model)}",
        "usage": payload.get("usage"),
    }


def ai() -> dict[str, object]:
    """그날 모은 뉴스로 AI 판단 기록 (문서 115 2 단계). Gemini 우선, 없으면 ChatGPT. 매매 안 씀."""
    import hashlib

    from auto_trading.config.secrets import load_gemini_key, load_openai_key
    from auto_trading.ops.news_ai import build_messages, compare, parse_reply

    gemini = load_gemini_key(REPO / ".env")
    openai = load_openai_key(REPO / ".env")
    if gemini is None and openai is None:
        return {"skipped": "GEMINI_API_KEY · OPENAI_API_KEY · CHATGPT_KEY 가 .env 에 없다"}
    latest = REPORTS / "latest.json"
    report = json.loads(latest.read_text(encoding="utf-8"))
    news_file = (report.get("news_shadow") or {}).get("file")
    if not news_file:
        return {"skipped": "그날 뉴스 기록이 없다 — news 먼저"}
    items = [
        json.loads(line)
        for line in (REPO / news_file).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    meta = json.loads(META.read_text(encoding="utf-8"))
    universe = sorted(s for s in meta["exchanges"] if s != "SPY")
    messages = build_messages(report, items, universe)
    answer = (
        _ask_gemini(gemini.value, messages)
        if gemini is not None
        else _ask_openai(openai.value, messages)  # type: ignore[union-attr]
    )
    if "error" in answer:
        return answer
    text = str(answer["text"])
    model = str(answer["model"])
    payload = {"model": model, "usage": answer.get("usage")}
    try:
        judgement = parse_reply(text, universe)
    except ValueError as exc:
        return {"error": f"답 형식 오류: {exc}"}
    mech = [str(x["symbol"]) for x in report.get("leaders") or []]
    record = {
        "date": report.get("date"),
        "model": payload.get("model", model),
        "asked_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "news_items_given": min(len(items), 150),
        "news_latest_fetched_at": max((str(i.get("fetched_at", "")) for i in items), default=""),
        "prompt_sha256": hashlib.sha256(
            json.dumps(messages, ensure_ascii=False).encode()
        ).hexdigest(),
        "usage": payload.get("usage"),
        "judgement": judgement,
        "vs_mechanical": compare(mech, [x["symbol"] for x in judgement["leaders"]]),
    }
    AI.mkdir(parents=True, exist_ok=True)
    (AI / f"{report.get('date')}.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    summary = {
        "model": record["model"],
        "market_room": judgement["market_room"],
        "confidence": judgement["confidence"],
        "leading_sectors": judgement["leading_sectors"],
        "leaders": [x["symbol"] for x in judgement["leaders"]],
        "vs_mechanical": record["vs_mechanical"],
        "summary": judgement["summary"],
    }
    report.setdefault("news_shadow", {})["ai_judgement"] = summary
    latest.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    dated = REPORTS / f"{report['date']}.json"
    if dated.exists():
        dated.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"ok": True, "market_room": judgement["market_room"], "leaders": summary["leaders"]}


def rebuild() -> dict[str, object]:
    """시세를 고친 뒤: 같은 시작일부터 빈 계좌로 다시 계산 (2026-10-12 미완성 일봉 결함)."""
    old = PaperState.from_json(json.loads(STATE.read_text(encoding="utf-8")))
    if not old.equity:
        raise SystemExit("다시 계산할 기록이 없다")
    before = old.equity[-1]
    out = run(rebuild_from=old.equity[0][0])
    return {"from": old.equity[0][0], "before": list(before), **out}


def status() -> dict[str, object]:
    latest = REPORTS / "latest.json"
    return (
        json.loads(latest.read_text(encoding="utf-8"))
        if latest.exists()
        else {"note": "아직 실행 전"}
    )


def main(argv: list[str]) -> int:
    commands = {
        "init": init,
        "update": update,
        "run": run,
        "news": news,
        "ai": ai,
        "rebuild": rebuild,
        "status": status,
    }
    if argv == ["daily"]:
        result: dict[str, object] = {"update": update(), "run": run(), "news": news(), "ai": ai()}
    elif len(argv) == 1 and argv[0] in commands:
        result = commands[argv[0]]()
    else:
        raise SystemExit(
            "usage: python -m scripts.lt_paper "
            "init | update | run | news | ai | daily | rebuild | status"
        )
    sys.stdout.write(json.dumps(result, ensure_ascii=True, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
