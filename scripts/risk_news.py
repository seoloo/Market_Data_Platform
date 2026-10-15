"""보유 종목 위험 뉴스 감시 (사용자 2026-10-11) — 1 시간마다, 위험 사건일 때만 투자 봇으로 알림.

사용 예::

    python -m scripts.risk_news run            # 예약: 매시 17 분
    python -m scripts.risk_news run --no-send  # 알림 없이 화면에만 (기억도 남기지 않음)

* 대상: Deneb(TQQQ · SCHD), 토스 적립 장부 종목, Vega(BTC · ETH · SOL) + 업비트 거래소,
  토스의 그 밖 미국 보유(수동 보유). 국내 종목은 영어 기사가 드물어 뺀다.
* 뉴스: 구글 뉴스 영어 검색(지난 1 일) + 미국 종목은 야후 RSS. 새 기사만 Laya 에 한 번에 보낸다.
* Laya 가 위험 후보(50% 이상)를 고르면 Gemini(무료)가 '이미 일어난 중대 사건' 인지
  확인한 것만 알린다.
* 판단 · 문구는 :mod:`auto_trading.ops.risk_news`. 매매 · 주문 없음.
  ``.env`` ``RISK_NEWS_OFF=true`` 면 끔.
* 기록: ``data/risk_news/seen.json`` (본 기사) · ``log.jsonl`` (모든 판단).
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote

import httpx

from auto_trading.config.secrets import (
    load_gemini_key,
    load_telegram_credentials,
    read_env_file,
)
from auto_trading.notify.channels import NotifyError, TelegramNotifier
from auto_trading.ops.deneb_live import SYMBOLS as DENEB_SYMBOLS
from auto_trading.ops.laya_client import choice_of, from_env
from auto_trading.ops.news_rss import parse_feed
from auto_trading.ops.risk_news import (
    ALERT_MIN,
    CANDIDATE_MIN,
    RISK_Q,
    Asset,
    Headline,
    SeenStore,
    alert_text,
    confirm_prompt,
    log_rows,
    parse_confirm,
    pick_alerts,
    state_of,
)
from auto_trading.ops.stock_news import is_junk, yahoo_rss_url

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data" / "risk_news"
_UA = {"User-Agent": "Mozilla/5.0"}
PER_ASSET = 15
FRESH_H = 26

NAMES = {
    "TQQQ": "ProShares UltraPro QQQ (TQQQ), a 3x leveraged Nasdaq-100 ETF",
    "SCHD": "Schwab U.S. Dividend Equity ETF (SCHD)",
    "QNDX": "Nasdaq-100 ETF (QNDX)",
    "QS": "QuantumScape (QS)",
    "BTQ": "BTQ Technologies (BTQ)",
    "WQTM": "WisdomTree Quantum Computing Fund (WQTM)",
}
CRYPTO = (
    Asset("BTC", "Bitcoin", "Bitcoin", group="Vega"),
    Asset("ETH", "Ethereum", "Ethereum", group="Vega"),
    Asset("SOL", "Solana", "Solana crypto", group="Vega"),
    Asset("Upbit", "Upbit, the Korean crypto exchange", "Upbit exchange", group="Vega 거래소"),
)


def _us(sym: str, group: str) -> Asset:
    name = NAMES.get(sym, f"the stock or ETF with ticker {sym}")
    return Asset(sym, name, f"{sym} stock", yahoo=sym, group=group)


def assets(root: Path = REPO) -> list[Asset]:
    out = [_us(s, "Deneb") for s in DENEB_SYMBOLS]
    acc: list[str] = []
    for f in sorted((root / "data" / "toss").glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(d, dict) and "baseline_qty" in d and d.get("symbol"):
            acc.append(str(d["symbol"]).upper())
    out += [_us(s, "토스 적립") for s in sorted(set(acc)) if s not in DENEB_SYMBOLS]
    out += list(CRYPTO)
    known = {a.key for a in out}
    try:  # 그 밖 미국 보유 (수동) — 조회만, 실패하면 건너뜀
        from auto_trading.config.secrets import load_toss_credentials
        from auto_trading.exchange.toss import TossReader, TossTokenCache, is_kr_symbol

        r = TossReader(
            load_toss_credentials(root / ".env"), TossTokenCache(root / "data" / "toss_token.json")
        )
        try:
            held = r.holdings()
        finally:
            r.close()
        out += [_us(s, "수동 보유") for s in sorted(held) if s not in known and not is_kr_symbol(s)]
    except Exception as exc:
        print(f"토스 보유 조회 건너뜀: {type(exc).__name__}")
    return out


def _fresh(published: str, now: datetime) -> bool:
    try:
        return now - parsedate_to_datetime(published) <= timedelta(hours=FRESH_H)
    except (TypeError, ValueError, IndexError):
        return False


def fetch(asset: Asset, now: datetime) -> list[Headline]:
    urls = [
        "https://news.google.com/rss/search?q="
        + quote(asset.query + " when:1d")
        + "&hl=en-US&gl=US&ceid=US:en"
    ]
    if asset.yahoo:
        urls.append(yahoo_rss_url(asset.yahoo))
    out: list[Headline] = []
    stamp = now.isoformat(timespec="seconds")
    for url in urls:
        try:
            r = httpx.get(url, headers=_UA, timeout=15, follow_redirects=True)
        except httpx.HTTPError:
            continue
        if r.status_code != 200:
            continue
        for it in parse_feed(r.text, "risk", stamp):
            if _fresh(it.published, now) and not is_junk(it):
                out.append(Headline(asset, it.title.strip(), it.link, it.published))
    return out[:PER_ASSET]


def _decide(
    new: list[Headline], choices: Sequence[tuple[str, float] | None], root: Path
) -> tuple[list[tuple[Headline, str, float]], dict[int, str]]:
    """Laya 후보(넓게) → Gemini 확인. Gemini 키가 없거나 실패하면 Laya 만으로 높은 문턱."""
    key = load_gemini_key(root / ".env")
    if key is None:
        return pick_alerts(new, choices, ALERT_MIN), {}
    cands = pick_alerts(new, choices, CANDIDATE_MIN)
    if not cands:
        return [], {}
    from scripts.market_brief import ask_gemini

    system, user = confirm_prompt(cands)
    answer, _attempts = ask_gemini(key.value, system, user)
    if not isinstance(answer, dict):
        return pick_alerts(new, choices, ALERT_MIN), {}
    ok = parse_confirm(answer, len(cands))
    kept = [c for k, c in enumerate(cands, 1) if k in ok]
    whys = {n: ok[k] for n, k in enumerate(sorted(ok), 1)}
    return kept, whys


def run(root: Path = REPO, *, send: bool = True) -> int:
    if read_env_file(root / ".env").get("RISK_NEWS_OFF", "").strip().lower() == "true":
        print("위험 뉴스 감시 꺼짐 (RISK_NEWS_OFF)")
        return 0
    laya = from_env(root)
    if laya is None:
        print("Laya 키 없음 — 할 일 없음")
        return 0
    now = datetime.now(UTC)
    store = SeenStore(DATA / "seen.json")
    items: list[Headline] = []
    for a in assets(root):
        items += fetch(a, now)
    new = store.new(items)
    if not new:
        print("새 기사 없음")
        return 0
    try:
        results = laya.batch([state_of(h) for h in new], RISK_Q, "english")
    finally:
        laya.close()
    choices = [
        (c.label, c.prob) if (c := choice_of(res, "risk")) is not None else None for res in results
    ]
    alerts, whys = _decide(new, choices, root)
    done = [h for h, c in zip(new, choices, strict=False) if c is not None]
    print(f"새 기사 {len(new)} · 판단 {len(done)} · 알림 {len(alerts)} · {laya.tokens} 토큰")
    if not send:
        for h, label, prob in alerts:
            print(f"  {h.asset.key} {label} {prob:.2f} {h.title[:90]}")
        return 0
    if alerts:
        creds = load_telegram_credentials(root / ".env")
        if creds is not None:
            bot = TelegramNotifier(creds)
            try:
                bot.send_html(alert_text(alerts, whys))
            except NotifyError as exc:
                print(f"텔레그램 실패: {exc}")
                return 1  # 못 보낸 기사는 다음 실행에 다시 본다
            finally:
                bot.close()
    store.mark(done)  # 판단이 끝난 기사만 기억 (Laya 실패 칸은 다음에 다시)
    store.save()
    DATA.mkdir(parents=True, exist_ok=True)
    with (DATA / "log.jsonl").open("a", encoding="utf-8") as fh:
        for row in log_rows(new, choices, now.isoformat(timespec="seconds")):
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return 0


def main(argv: list[str]) -> int:
    if argv[:1] == ["run"]:
        return run(send="--no-send" not in argv)
    raise SystemExit("usage: python -m scripts.risk_news run [--no-send]")


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
