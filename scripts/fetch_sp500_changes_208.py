"""문서 208 — S&P 500 편입 · 편출 기록(위키백과) + 빠진 종목 가격(Tiingo 무료).

연구 자료 전용 → ``data/research/sp500_events_208/``.

- ``events.json``: 위키백과 'Historical components of the S&P 500' 표(고정 판 번호)에서 효력일 ·
  편입 · 편출 티커 · 사유 · 근거 링크의 발표일(``date=`` 또는 보도자료 주소 속 날짜).
- ``prices/<TICKER>.csv``: 로컬(``data/research/stocks`` · ``stocks_sp400``)에 없는 티커만 Tiingo
  수정 가격(adjOpen/adjClose). 무료 등급 한도(시간당 약 50 회)에 맞춰 천천히, 이어 받기 가능.

사용: ``python -m scripts.fetch_sp500_changes_208 events`` → ``... prices``
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "data" / "research" / "sp500_events_208"
LOCAL = (REPO / "data" / "research" / "stocks", REPO / "data" / "research" / "stocks_sp400")
WIKI = "https://en.wikipedia.org/w/api.php"
PAGE = "Historical_components_of_the_S&P_500"
TIINGO = "https://api.tiingo.com/tiingo/daily/{t}/prices?startDate={a}&endDate={b}"
PAUSE_S = 75
UA = {"User-Agent": "auto-trading-research/1.0 (personal research)"}


def _get(url: str, headers: dict[str, str] | None = None) -> bytes:
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=120) as r:
        return bytes(r.read())


def _ref_date(txt: str) -> date | None:
    m = re.search(r"\|\s*date\s*=\s*([A-Za-z]+ \d{1,2}, \d{4}|\d{4}-\d{2}-\d{2})", txt)
    if m:
        for f in ("%B %d, %Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(m.group(1), f).replace(tzinfo=UTC).date()
            except ValueError:
                pass
    m = re.search(r"/(20\d{2})(\d{2})(\d{2})[-/_]", txt) or re.search(
        r"(20\d{2})-(\d{2})-(\d{2})-", txt
    )
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


def parse(wikitext: str) -> list[dict[str, Any]]:
    """표 한 줄 = 효력일 · 편입(티커, 이름) · 편출(티커, 이름) · 사유 · 근거.

    발표일 = 근거 링크 날짜 중 가장 이른 것 (효력일 앞 0 ~ 40 일만)."""
    i = wikitext.find('id="changes"')
    body = wikitext[i:]
    body = body[: body.find("\n|}")]
    named: dict[str, date] = {}
    out = []
    for r in body.split("\n|-")[1:]:
        raw = [c.strip() for c in re.split(r"\n\|\|?|\|\|", r)][1:]
        if len(raw) < 6:
            continue
        try:
            eff = datetime.strptime(raw[0].strip("| "), "%B %d, %Y").replace(tzinfo=UTC).date()
        except ValueError:
            continue
        ann: date | None = None
        for ref in re.findall(r"<ref[^>]*>.*?</ref>|<ref[^>]*/>", r, re.S):
            nm = re.search(r'name\s*=\s*"?([^"/>]+)"?', ref)
            d = _ref_date(ref)
            if nm and d:
                named[nm.group(1).strip()] = d
            if d is None and nm:
                d = named.get(nm.group(1).strip())
            if d and (ann is None or d < ann):
                ann = d
        if ann is not None and not 0 <= (eff - ann).days <= 40:
            ann = None  # 근거 날짜가 발표일로 보기 어려움
        clean = [re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", c) for c in raw]
        out.append(
            {
                "effective": str(eff),
                "announced": str(ann) if ann else None,
                "added": clean[1] or None,
                "added_name": clean[2] or None,
                "removed": clean[3] or None,
                "removed_name": clean[4] or None,
                "reason": re.sub(r"<ref.*", "", clean[5], flags=re.S).strip(),
            }
        )
    return out


def events() -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    meta = json.loads(
        _get(
            f"{WIKI}?action=query&prop=revisions&titles={PAGE.replace('&', '%26')}"
            "&rvprop=ids|timestamp&format=json"
        )
    )
    page = next(iter(meta["query"]["pages"].values()))
    rev = page["revisions"][0]
    body = json.loads(_get(f"{WIKI}?action=parse&oldid={rev['revid']}&prop=wikitext&format=json"))
    text = body["parse"]["wikitext"]["*"]
    (OUT / "wikitext.txt").write_text(text, encoding="utf-8")
    ev = parse(text)
    (OUT / "events.json").write_text(json.dumps(ev, indent=1), encoding="utf-8")
    info = {
        "revid": rev["revid"],
        "rev_timestamp": rev["timestamp"],
        "rows": len(ev),
        "with_announcement": sum(1 for e in ev if e["announced"]),
        "wikitext_sha256": hashlib.sha256(text.encode()).hexdigest(),
    }
    (OUT / "events_manifest.json").write_text(json.dumps(info, indent=1), encoding="utf-8")
    return info


def _local(t: str) -> bool:
    return any(
        (d / f"{t}.csv").exists() or (d / f"{t.replace('.', '-')}.csv").exists() for d in LOCAL
    )


def prices() -> dict[str, Any]:
    key = next(
        line.split("=", 1)[1].strip().strip('"')
        for line in (REPO / ".env").read_text(encoding="utf-8").splitlines()
        if line.startswith("TIINGO_API_KEY=")
    )
    ev = json.loads((OUT / "events.json").read_text(encoding="utf-8"))
    need: dict[str, tuple[date, date]] = {}
    for e in ev:
        eff = date.fromisoformat(e["effective"])
        for t in (e["added"], e["removed"]):
            if t and re.fullmatch(r"[A-Z.\-]{1,6}", t) and not _local(t):
                lo, hi = need.get(t, (eff, eff))
                need[t] = (min(lo, eff), max(hi, eff))
    need.setdefault("SPY", (date(2000, 1, 1), date(2026, 9, 30)))
    (OUT / "prices").mkdir(parents=True, exist_ok=True)
    status: dict[str, str] = {}
    for t, (lo, hi) in sorted(need.items()):
        path = OUT / "prices" / f"{t}.csv"
        if path.exists() or (OUT / "prices" / f"{t}.empty").exists():
            status[t] = "cached"
            continue
        a, b = lo - timedelta(days=400), hi + timedelta(days=120)
        url = TIINGO.format(t=t.replace(".", "-"), a=a, b=b)
        for _attempt in range(3):
            try:
                rows = json.loads(_get(url, {"Authorization": f"Token {key}"}))
                break
            except urllib.error.HTTPError as err:
                if err.code == 429:
                    time.sleep(3600)
                    continue
                rows = []
                break
        if rows:
            with path.open("w", encoding="utf-8", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["date", "open", "high", "low", "close", "volume"])
                for x in rows:
                    w.writerow(
                        [
                            x["date"][:10],
                            x["adjOpen"],
                            x["adjHigh"],
                            x["adjLow"],
                            x["adjClose"],
                            x["adjVolume"],
                        ]
                    )
            status[t] = f"ok {len(rows)}"
        else:
            (OUT / "prices" / f"{t}.empty").write_text("", encoding="utf-8")
            status[t] = "empty"
        sys.stdout.write(f"{t} {status[t]}\n")
        sys.stdout.flush()
        time.sleep(PAUSE_S)
    (OUT / "prices_manifest.json").write_text(
        json.dumps(
            {
                t: hashlib.sha256((OUT / "prices" / f"{t}.csv").read_bytes()).hexdigest()
                for t in need
                if (OUT / "prices" / f"{t}.csv").exists()
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    return {"needed": len(need), "status": dict(sorted((k, v) for k, v in status.items()))}


def main(argv: list[str]) -> int:
    cmds = {"events": events, "prices": prices}
    if len(argv) != 1 or argv[0] not in cmds:
        raise SystemExit("usage: python -m scripts.fetch_sp500_changes_208 events|prices")
    sys.stdout.write(json.dumps(cmds[argv[0]](), indent=1)[:3000] + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
