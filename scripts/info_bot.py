"""정보 봇 — 종목 이름을 보내면 최근 7 일 뉴스를 요약해 답한다 (사용자 2026-10-10).

사용 예::

    python -m scripts.info_bot run           # 받은 글에 답 (예약: 1 분마다)
    python -m scripts.info_bot ask 삼성전자   # 보내지 않고 화면에만 (HTML 그대로)

* ``TELEGRAM_INFO_BOT_TOKEN`` 의 봇만 읽는다. 없으면 아무것도 하지 않는다 —
  투자 봇의 글을 가져가면 Deneb 금액 · 버튼 답이 사라진다.
* 뉴스: 구글 뉴스 검색 RSS(키 없음) + 미국 종목은 야후 파이낸스 RSS.
  바로 가는 링크는 기사 앞부분을 조금 읽는다. 요약 · 호재/악재 · AI 의견: Gemini.
  가격 · 추세 상태 · 과거 통계: 야후 일봉 10 년(우리 계산).
* 2026-10-11: 줄마다 근거 기사 링크(HTML). AI 의견은 '검증 안 됨' 으로 보이고 기록 →
  20 거래일 뒤 채점('성적'). Laya 는 기사별 호재/악재를 판단해(영어 · 한국어)
  호재 표시와 근거 기사 옆에 확률과 함께 보인다('라야' 로 Gemini 와 비교).
  키 ``LAYA_API_KEY``, 끄기 ``LAYA_OFF=true``.
* ``pin-help``: 사용법을 대화방 공지로 고정 + '/' 메뉴.
* 주문 · 계좌 조회 없음. 정보용.
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import UTC, datetime, timedelta
from html import escape, unescape
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from auto_trading.config.secrets import (
    TelegramCredentials,
    load_gemini_key,
    read_env_file,
)
from auto_trading.notify.channels import NotifyError, TelegramNotifier
from auto_trading.notify.heartbeat import beat_and_alert
from auto_trading.notify.inbox import InboxQueue, TelegramInbox
from auto_trading.ops import user_settings
from auto_trading.ops.news_rss import NewsItem, parse_feed
from auto_trading.ops.stock_calls import (
    Call,
    append_call,
    laya_text,
    load_calls,
    record_line,
    save_calls,
    score,
    score_text,
)
from auto_trading.ops.stock_news import (
    Target,
    build_prompt,
    format_reply,
    gnews_url,
    kr_index,
    parse_answer,
    pick_items,
    price_block,
    resolve,
    trend_stats,
    yahoo_rss_url,
)

REPO = Path(__file__).resolve().parent.parent
PER_RUN = 3
"""한 번 실행에 답하는 글 수 (나머지는 다음 실행)."""
HELP = """📌 정보 봇 사용법 — 이렇게 보내 보세요

🗞 종목 이름이나 기호 (예: 삼성전자 · 005930 · 엔비디아 · NVDA · IREN)
  최근 7일 뉴스 요약 — 줄마다 🟢호재/🔴악재 + 근거 기사 링크
  호재/악재 옆 % = 근거 기사 판단 확률
  아래에 가격 · 52주 고점 대비 · 우리 추세 규칙(켜짐/꺼짐)
  · 과거 같은 상태 뒤 20거래일에 오른 비율
  🤖 AI 의견(강세·중립·약세)은 검증 안 된 참고 — 20거래일 뒤 채점해요

🧾 성적 (또는 /score) — AI 의견이 실제로 맞은 비율
🧪 비교 (또는 /compare) — 보조 AI 와 Gemini 판단 비교
❓ 도움 (또는 /help) — 이 안내

자동으로 오는 것: 주간 테마(토 09:00) · 시장 브리핑(화~토 07:30)
※ 정보용 · 매매 신호 아님"""
COMMANDS_MENU = [
    ("help", "사용법"),
    ("score", "성적 — AI 의견 채점표"),
    ("compare", "비교 — 보조 AI 와 Gemini 판단 비교"),
]
SCORE_WORDS = ("성적", "/성적", "성적표", "/score", "score")
LAYA_WORDS = ("비교", "/비교", "compare", "/compare", "라야", "/라야", "laya", "/laya")
"""보조 AI 비교 명령. 알림 · 도움말에는 '비교' 만 보인다(옛 말도 받는다).

사용자 2026-10-15 'Laya 라는 단어 좀 빼'."""
HELP_WORDS = ("/start", "/help", "도움", "도움말", "help", "?")
_UA = {"User-Agent": "Mozilla/5.0"}
EXCERPTS = 4
"""본문 앞부분을 읽어 볼 기사 수 (바로 가는 링크만 — 구글 뉴스 링크는 열 수 없다)."""
LAYA_ITEMS = 8


def calls_path(root: Path = REPO) -> Path:
    return root / "data" / "info_bot" / "calls.jsonl"


def info_credentials(root: Path = REPO) -> TelegramCredentials | None:
    """정보 봇 토큰이 따로 있을 때만 (투자 봇으로 돌아가지 않는다)."""
    env = read_env_file(root / ".env")
    token, chat = env.get("TELEGRAM_INFO_BOT_TOKEN", ""), env.get("TELEGRAM_CHAT_ID", "")
    return TelegramCredentials(token, chat) if token and chat else None


def _kr(root: Path) -> dict[str, tuple[str, str, str]]:
    infos = []
    for rel in ("data/kr_shadow/info.json", "data/research/kq_stocks/info.json"):
        try:
            infos.append(json.loads((root / rel).read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return kr_index(infos)


def _is_us(target: Target) -> bool:
    return bool(target.yahoo) and not str(target.yahoo).endswith((".KS", ".KQ"))


def fetch_news(target: Target) -> list[NewsItem]:
    now = datetime.now(UTC)
    stamp = now.isoformat(timespec="seconds")
    urls = [(gnews_url(q, lang), f"gnews_{lang}") for q, lang in target.queries]
    if _is_us(target):
        urls.append((yahoo_rss_url(str(target.yahoo)), "yahoo_en"))
    items: list[NewsItem] = []
    for url, source in urls:
        try:
            r = httpx.get(url, headers=_UA, timeout=15, follow_redirects=True)
        except httpx.HTTPError:
            continue
        if r.status_code == 200:
            got = parse_feed(r.text, source, stamp)
            if source.startswith("yahoo"):  # 야후는 오래된 기사도 섞여 온다 — 7 일만
                got = [x for x in got if _fresh(x, now)]
            items += got
    return pick_items(items)


def _fresh(item: NewsItem, now: datetime) -> bool:
    from email.utils import parsedate_to_datetime

    try:
        return now - parsedate_to_datetime(item.published) <= timedelta(days=7)
    except (TypeError, ValueError, IndexError):
        return False


_META = re.compile(
    r'<meta[^>]+(?:property|name)=["\'](?:og:description|description)["\'][^>]+content=["\']([^"\']+)',
    re.IGNORECASE,
)
_PARA = re.compile(r"<p[^>]*>(.*?)</p>", re.IGNORECASE | re.DOTALL)


def excerpt(html_text: str) -> str:
    """기사 HTML → 설명 + 앞 문단 몇 개 (태그 제거, 700 자)."""
    parts = [unescape(m) for m in _META.findall(html_text)[:1]]
    for p in _PARA.findall(html_text):
        text = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", p))).strip()
        if len(text) >= 60:
            parts.append(text)
        if sum(len(x) for x in parts) > 700:
            break
    return " ".join(parts)[:700]


def fetch_excerpts(items: list[NewsItem]) -> dict[int, str]:
    out: dict[int, str] = {}
    for i, it in enumerate(items, 1):
        if len(out) >= EXCERPTS:
            break
        host = urlparse(it.link).netloc
        if not host or host.endswith("news.google.com"):
            continue
        try:
            r = httpx.get(it.link, headers=_UA, timeout=6, follow_redirects=True)
        except httpx.HTTPError:
            continue
        if r.status_code == 200 and "html" in r.headers.get("content-type", ""):
            text = excerpt(r.text)
            if text:
                out[i] = text
    return out


def fetch_series(yahoo: str, span: str = "10y") -> tuple[list[tuple[str, float]], str]:
    """야후 일봉 (날짜, 종가) 오래된 것 먼저 · 통화. 실패하면 빈 목록."""
    try:
        res = httpx.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo}?range={span}&interval=1d",
            headers=_UA,
            timeout=15,
        ).json()["chart"]["result"][0]
        stamps = res["timestamp"]
        closes = res["indicators"]["quote"][0]["close"]
        currency = str(res["meta"].get("currency") or "USD")
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
        return [], ""
    rows = [
        (datetime.fromtimestamp(int(t), UTC).strftime("%Y-%m-%d"), float(c))
        for t, c in zip(stamps, closes, strict=False)
        if c
    ]
    return rows, currency


def _summarize(
    target: Target, items: list[NewsItem], excerpts: dict[int, str], root: Path
) -> dict[str, Any] | None:
    key = load_gemini_key(root / ".env")
    if key is None or not items:
        return None
    from scripts.market_brief import ask_gemini

    system, user = build_prompt(target, items, excerpts)
    answer, _attempts = ask_gemini(key.value, system, user)
    return parse_answer(answer, len(items)) if isinstance(answer, dict) else None


# ------------------------------------------------------------------ Laya (그림자)

_LAYA_Q = {
    "impact": {
        "type": "choice",
        "instructions": "For a shareholder of this company, is this news headline good, bad or "
        "neutral for the stock price?",
        "criteria": {
            "good": "positive for the stock price (growth, deals, beats, upgrades)",
            "bad": "negative for the stock price (losses, misses, lawsuits, downgrades, dilution)",
            "neutral": "no clear price impact, or not about this company",
        },
    }
}
_LAYA_KO = {"good": "호재", "bad": "악재", "neutral": "중립"}


LAYA_URL = "https://api.laya-ai.com"
"""공식 Laya API (console.laya-ai.com 에서 키 발급, 2026-10-11 확인).

``LAYA_BASE_URL`` 로 바꿀 수 있다. 무료는 가입 때 20 회 · 그 뒤 입력 100 만 토큰당 $0.05."""


def _laya_call(base: str, key: str, picked: list[tuple[int, NewsItem]], model: str) -> list[Any]:
    try:
        r = httpx.post(
            f"{base.rstrip('/')}/v1/systemone/batch",
            json={"states": [it.title for _, it in picked], "questions": _LAYA_Q, "model": model},
            headers={"Authorization": f"Bearer {key}"},
            timeout=15,
        )
        return list(r.json()["results"]) if r.status_code == 200 else []
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return []


def laya_labels(
    items: list[NewsItem], root: Path
) -> tuple[dict[str, tuple[str, float]], float, dict[str, dict[str, float]]]:
    """기사별 호재/악재.

    돌려주는 값: 기사 번호 → (판단, 확률), 호출 ms, 기사 번호 → {호재 · 악재 · 중립: 확률}.
    영어 기사는 영어 모델, 한국어 등 나머지는 다국어 모델
    (사용자 2026-10-11: 한국 종목도 확률을 보고 싶다).
    언어마다 **한 번**씩만 부른다(``/v1/systemone/batch`` — 기사 여러 개를 한 요청에).
    한국어 시험(2026-10-11): 악재(파업)는 잘 잡지만 분명한 호재 제목을 악재 53 % 로 본
    경우가 있어 덜 정확하다.
    ``LAYA_API_KEY`` 가 없거나 실패하면 빈 값."""
    env = read_env_file(root / ".env")
    key = env.get("LAYA_API_KEY", "").strip()
    if not key or env.get("LAYA_OFF", "").strip().lower() == "true":
        return {}, 0.0, {}
    base = env.get("LAYA_BASE_URL", "").strip() or LAYA_URL
    numbered = list(enumerate(items, 1))
    groups = {
        "english": [x for x in numbered if x[1].source.endswith("_en")][:LAYA_ITEMS],
        "multilingual": [x for x in numbered if not x[1].source.endswith("_en")][:LAYA_ITEMS],
    }
    out: dict[str, tuple[str, float]] = {}
    probs: dict[str, dict[str, float]] = {}
    t0 = time.monotonic()
    for model, picked in groups.items():
        if not picked:
            continue
        for (i, _it), res in zip(picked, _laya_call(base, key, picked, model), strict=False):
            try:
                ans = res["answers"]["impact"]
                choice = str(ans["choice"])
                dist = {str(k): float(v) for k, v in (ans.get("probabilities") or {}).items()}
                prob = dist.get(choice, float(ans.get("answer_confidence", 0)))
            except (KeyError, TypeError, ValueError, AttributeError):
                continue
            if choice in _LAYA_KO:
                out[f"N{i}"] = (_LAYA_KO[choice], prob)
                probs[f"N{i}"] = {_LAYA_KO[k]: v for k, v in dist.items() if k in _LAYA_KO}
    ms = (time.monotonic() - t0) * 1000
    return out, (ms if out else 0.0), probs


# ------------------------------------------------------------------ 답


def scorecard(root: Path = REPO) -> str:
    """'성적' — 채점 안 된 기록을 지금 채점하고 표를 돌려준다."""
    path = calls_path(root)
    calls = load_calls(path)
    pending = {c.yahoo for c in calls if c.outcome is None}
    series = {s: fetch_series(s, "2y")[0] for s in pending}
    if score(calls, series):
        save_calls(path, calls)
    return score_text(calls)


def answer(text: str, root: Path = REPO) -> str:
    """텔레그램 HTML 답."""
    t = text.strip()
    if t.lower() in HELP_WORDS:
        return escape(HELP)
    if t.lower() in SCORE_WORDS:
        return escape(scorecard(root))
    if t.lower() in LAYA_WORDS:
        return escape(laya_text(load_calls(calls_path(root))))
    if not user_settings.get(root, "alerts.news"):
        return escape("종목 뉴스 답이 꺼져 있어요 — 오르빗 설정 › 알림에서 켤 수 있어요")
    if len(t) > 30:
        return escape("종목 이름이나 기호만 보내 주세요 (예: 삼성전자, NVDA)")
    target = resolve(t.removeprefix("뉴스").strip() or t, _kr(root))
    items = fetch_news(target)
    excerpts = fetch_excerpts(items)
    judged = _summarize(target, items, excerpts, root)
    rows, currency = fetch_series(str(target.yahoo)) if target.yahoo else ([], "")
    closes = [c for _, c in rows]
    stats = trend_stats(closes)
    path = calls_path(root)
    calls = load_calls(path)
    laya, laya_ms, laya_probs = laya_labels(items, root)
    if rows and target.yahoo:
        if score(calls, {str(target.yahoo): rows}):
            save_calls(path, calls)
        tones: dict[str, str] = {}
        for p in (judged or {}).get("points") or []:
            for ref in p["ids"]:
                tones.setdefault(ref, p.get("tone") or "")
        new = Call(
            date=rows[-1][0],
            query=t,
            yahoo=str(target.yahoo),
            close=rows[-1][1],
            trend_on=stats.on if stats else None,
            view=(judged or {}).get("view") or "",
            tones=tones,
            laya={k: v[0] for k, v in laya.items()},
            laya_ms=laya_ms,
        )
        if append_call(path, new):
            calls.append(new)
    return format_reply(
        target,
        items,
        judged,
        price_block(closes, currency),
        stats,
        record_line(calls),
        laya,
        laya_probs,
    )


def run(root: Path = REPO) -> int:
    creds = info_credentials(root)
    if creds is None:
        print("정보 봇 토큰 없음 — 할 일 없음")
        return 0
    try:
        msgs = InboxQueue(root / "data" / "telegram" / "info_inbox.json").take(
            TelegramInbox(creds), lambda m: True
        )
    except NotifyError as exc:
        print(f"텔레그램 받기 실패: {exc}")
        return 1
    asks = [m.text for m in msgs if m.text][-PER_RUN:]
    if not asks:
        print("정보 봇: 받은 글 없음")
        beat_and_alert(
            root,
            "info_bot",
            int(time.time() * 1000),
            alert=bool(user_settings.get(root, "alerts.bot_down")),
        )
        return 0
    bot = TelegramNotifier(creds)
    try:
        for text in asks:
            word = text.strip().lower()
            if (
                user_settings.get(root, "alerts.news")
                and word not in HELP_WORDS
                and word not in SCORE_WORDS + LAYA_WORDS
            ):
                bot.send(f"🔎 '{text.strip()[:30]}' 뉴스 찾는 중…")
            reply = answer(text, root)
            bot.send_html(reply)
            print(reply)
    except NotifyError as exc:
        print(f"텔레그램 보내기 실패: {exc}")
        return 1
    finally:
        bot.close()
    beat_and_alert(
        root,
        "info_bot",
        int(time.time() * 1000),
        alert=bool(user_settings.get(root, "alerts.bot_down")),
    )
    return 0


def pin_help(root: Path = REPO) -> str:
    """사용법을 보내고 정보 봇 대화방 공지로 고정한다 (예전 고정은 모두 푼다) + '/' 메뉴."""
    creds = info_credentials(root)
    if creds is None:
        return "정보 봇 토큰 없음"
    bot = TelegramNotifier(creds)
    try:
        mid = bot.send_message(HELP)
        bot.unpin_all()
        bot.pin(mid)
        bot.set_commands(COMMANDS_MENU)
    finally:
        bot.close()
    return f"정보 봇 공지 고정 (메시지 {mid})"


def main(argv: list[str]) -> int:
    if argv == ["run"]:
        return run()
    if argv == ["pin-help"]:
        print(pin_help())
        return 0
    if len(argv) >= 2 and argv[0] == "ask":
        sys.stdout.write(answer(" ".join(argv[1:])) + "\n")
        return 0
    raise SystemExit("usage: python -m scripts.info_bot run | ask <종목> | pin-help")


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
