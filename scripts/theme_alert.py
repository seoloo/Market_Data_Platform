"""문서 148 — 주간 미래 주도 테마 알림 (국장 + 미국). 매매하지 않는 정보 도구.

사용 예::

    python -m scripts.theme_alert weekly                 # AI 판단 → 텔레그램 두 통 → 보고서
    python -m scripts.theme_alert weekly --no-telegram   # 보내지 않고 보고서·화면 출력만
    python -m scripts.theme_alert status

뉴스는 새로 받지 않는다 — 143(``data/kr_shadow/news``)·115(``data/lt_paper/news``)가 매일 모은
지난 7 일 제목을 쓴다. 근거는 뉴스 번호로만 받고 코드가 실제 제목으로 바꾼다. 종목별 사실(추세·
3 달 수익·주도주 목록)은 우리 자료로 코드가 붙인다. 보고서 ``reports/theme_alert/``.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from scripts.run_lt_kr import build_panel, members
from scripts.run_lt_leaders import trend_up

from auto_trading.ops import user_settings
from auto_trading.ops.theme_alert import (
    build_messages,
    diff_weeks,
    format_market,
    number_headlines,
    parse_weekly,
)

REPO = Path(__file__).resolve().parent.parent
KR = REPO / "data" / "kr_shadow"
US = REPO / "data" / "lt_paper"
REPORTS = REPO / "reports" / "theme_alert"
DAYS = 7
RET_DAYS = 63


def _today() -> datetime:
    return datetime.now()  # noqa: DTZ005 — PC 시계(KST)


def _week_news(folder: Path, today: datetime) -> list[dict[str, Any]]:
    since = (today - timedelta(days=DAYS)).strftime("%Y-%m-%d")
    items: list[dict[str, Any]] = []
    for path in sorted(folder.glob("20*.jsonl")):
        if path.stem >= since:
            items += [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
    return items


def _read(path: Path) -> dict[str, Any]:
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return body if isinstance(body, dict) else {}


def _kr_side() -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    """(종목군 코드:이름, 코드별 사실)."""
    info = json.loads((KR / "info.json").read_text(encoding="utf-8"))
    panel, _ok = build_panel(KR, to="9999-12-31")
    t = len(panel.days) - 1
    pool = members(panel, t)
    latest = _read(REPO / "reports" / "kr_shadow" / "latest.json")
    price = {x["code"] for x in latest.get("price_leaders") or []}
    flow = {x["code"] for x in latest.get("flow_leaders") or []}
    facts: dict[str, dict[str, Any]] = {}
    for s in pool:
        xs = panel.close[s]
        a, b = xs[t], xs[t - RET_DAYS] if t >= RET_DAYS else None
        tags = (["가격 주도주"] if s in price else []) + (["수급 주도주"] if s in flow else [])
        facts[s] = {
            "trend": trend_up(xs, t),
            "ret_3m": a / b - 1 if a is not None and b else None,
            "in_lists": tags,
        }
    return {s: str(info.get(s, {}).get("name", s)) for s in pool}, facts


def _us_side() -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    meta = json.loads((US / "meta.json").read_text(encoding="utf-8"))
    syms = sorted(s for s in meta.get("exchanges", {}) if s != "SPY")
    latest = _read(REPO / "reports" / "lt_paper" / "latest.json")
    leaders = {str(x.get("symbol")) for x in latest.get("leaders") or []}
    facts: dict[str, dict[str, Any]] = {}
    for s in syms:
        path = US / "bars" / f"{s}.csv"
        if not path.exists():
            continue
        rows = path.read_text(encoding="utf-8").splitlines()[1:]
        closes: list[float | None] = [float(r.split(",")[4]) for r in rows if r]
        t = len(closes) - 1
        a = closes[t] if t >= 0 else None
        b = closes[t - RET_DAYS] if t >= RET_DAYS else None
        facts[s] = {
            "trend": trend_up(closes, t) if t >= 0 else None,
            "ret_3m": a / b - 1 if a is not None and b else None,
            "in_lists": ["LT 주도주"] if s in leaders else [],
        }
    return {s: s for s in syms}, facts


def _ask(messages: list[dict[str, str]]) -> dict[str, Any]:
    from scripts.lt_paper import _ask_gemini, _ask_openai

    from auto_trading.config.secrets import load_gemini_key, load_openai_key

    gemini = load_gemini_key(REPO / ".env")
    openai = load_openai_key(REPO / ".env")
    if gemini is not None:
        return dict(_ask_gemini(gemini.value, messages))
    if openai is not None:
        return dict(_ask_openai(openai.value, messages))
    return {"error": "GEMINI_API_KEY · OPENAI_API_KEY 가 .env 에 없다"}


def _send(texts: list[str], telegram: bool) -> list[str]:
    from auto_trading.config.secrets import load_telegram_info_credentials
    from auto_trading.notify.channels import NotifyError, OutboxNotifier, TelegramNotifier

    REPORTS.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    for text in texts:
        OutboxNotifier(REPORTS / "outbox.log").send(text)
        if not telegram or not user_settings.get(REPO, "alerts.theme"):
            continue  # 설정에서 주간 테마를 끄면 기록만 남긴다
        creds = load_telegram_info_credentials(REPO / ".env")  # 정보 봇 (2026-10-10)
        if creds is None:
            errors.append("텔레그램 설정 없음")
            continue
        try:
            TelegramNotifier(creds).send(text)
        except NotifyError as exc:
            errors.append(str(exc))
    return errors


def weekly(telegram: bool = True) -> dict[str, Any]:
    today = _today()
    week = today.strftime("%Y-%m-%d")
    headlines = {
        "kr": number_headlines(_week_news(KR / "news", today), "K"),
        "us": number_headlines(_week_news(US / "news", today), "U"),
    }
    kr_uni, kr_facts = _kr_side()
    us_uni, us_facts = _us_side()
    universes = {"kr": kr_uni, "us": us_uni}
    messages = build_messages(week, headlines, universes)
    # AI 가 JSON 모드에서도 가끔 깨진 답을 준다(2026-10-10) — 한 번 더 묻고, 깨진 답은 남긴다
    for attempt in (1, 2):
        answer = _ask(messages)
        if "error" in answer:
            _send(
                [f"🔭 주간 테마 알림 실패: {answer.get('error')} {answer.get('message', '')}"],
                telegram,
            )
            return {"error": answer.get("error"), "message": answer.get("message")}
        try:
            judged = parse_weekly(str(answer["text"]), universes, headlines)
            break
        except ValueError as exc:
            REPORTS.mkdir(parents=True, exist_ok=True)
            (REPORTS / f"bad-answer-{week}-{attempt}.txt").write_text(
                str(answer["text"]), encoding="utf-8"
            )
            if attempt == 2:
                _send([f"🔭 주간 테마 알림 실패: 답 형식 오류 2 번 ({exc})"], telegram)
                return {"error": f"답 형식 오류: {exc}"}
    last = _read(REPORTS / "latest.json").get("judged")
    facts = {"kr": kr_facts, "us": us_facts}
    diffs = {m: diff_weeks(judged, last, m) if last else None for m in ("kr", "us")}
    texts = [format_market(m, judged, headlines[m], facts[m], diffs[m], week) for m in ("kr", "us")]
    errors = _send(texts, telegram)
    report = {
        "week": week,
        "model": answer.get("model"),
        "usage": answer.get("usage"),
        "prompt_sha256": hashlib.sha256(
            json.dumps(messages, ensure_ascii=False).encode()
        ).hexdigest(),
        "headlines": headlines,
        "judged": judged,
        "facts": {
            m: {p["symbol"]: facts[m].get(p["symbol"], {}) for p in judged[m]["picks"]}
            for m in ("kr", "us")
        },
        "diffs": diffs,
        "texts": texts,
        "send_errors": errors,
    }
    REPORTS.mkdir(parents=True, exist_ok=True)
    for path in (REPORTS / f"{week}.json", REPORTS / "latest.json"):
        path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"week": week, "dropped": judged["dropped"], "texts": texts, "send_errors": errors}


def main(argv: list[str]) -> int:
    telegram = "--no-telegram" not in argv
    args = [a for a in argv if a != "--no-telegram"]
    if args == ["weekly"]:
        out = weekly(telegram)
        sys.stdout.write(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
        return 1 if "error" in out else 0
    if args == ["status"]:
        sys.stdout.write(
            json.dumps(_read(REPORTS / "latest.json"), ensure_ascii=False)[:4000] + "\n"
        )
        return 0
    raise SystemExit("usage: python -m scripts.theme_alert weekly [--no-telegram] | status")


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
