"""국내 실적 발표 일정 — DART 공시로 (2026-10-16 사용자 "8번 확인해줘" → "C -> B -> D").

전에는 국내 종목이 늘 '실적 일정 원천 미확보' 로 막혔다. DART 거래소공시(``pblntf_ty=I``)로 채운다.

* **확정:** '기업설명회(IR)개최(안내공시)' 본문의 ``일시 YYYY-MM-DD`` 와 ``개최목적 … 실적``
  (예: SK하이닉스 10-14 공시 → '2026-10-28 09:00 · 2026년 3분기 경영실적 발표'). 보통 1 ~ 2 주 전.
* **지난 발표:** '…영업(잠정)실적(공정공시)' 접수일. 분기 발표만 센다 — '연결재무제표기준' 이
  있으면 그것만(현대차는 월별 판매도 '영업(잠정)실적' 이라 거른다), 없으면 1 년에 6 건 이하일 때만.
* **예상:** 확정이 없으면 1 년 전 같은 분기 발표일 + 1 년
  (삼성전자처럼 잠정실적을 예고 없이 내는 회사).
* 이번 분기 발표가 45 일 안에 나왔으면 '이번 분기 끝' — 다음은 석 달쯤 뒤.

판단은 순수 함수(:func:`judge`), 자료는 :func:`lookup` 이 받아 하루 캐시한다(키는 주소에만).
"""

from __future__ import annotations

import io
import json
import re
import time
import zipfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx

from auto_trading.ops.entry_events import EntryEvents

__all__ = [
    "KrEarnings",
    "judge",
    "kr_company_events",
    "lookup",
    "parse_ir",
    "quarterly_releases",
]

API = "https://opendart.fss.or.kr/api"
DONE_DAYS = 45
"""이번 분기 발표가 이만큼 안에 나왔으면 '끝' 으로 본다."""
ESTIMATE_AHEAD = 60
"""1 년 전 + 1 년 예상일이 이 날짜 수 안에 있을 때만 '예상' 으로 쓴다."""
CACHE_S = 6 * 3600


@dataclass(frozen=True, slots=True)
class KrEarnings:
    day: date | None
    kind: str
    """confirmed (IR 공시) · estimated (지난해 기준) · done (이번 분기 끝) · unknown."""
    note: str
    last_release: date | None = None


def parse_ir(text: str) -> tuple[date | None, str]:
    """IR 안내공시 본문(태그 뺀 글) → (개최일, 개최목적). 못 읽으면 (None, '')."""
    t = re.sub(r"\s+", " ", text)
    m = re.search(r"일시\s*(\d{4})[-.](\d{2})[-.](\d{2})", t)
    purpose = re.search(r"개최\s*목적\s*(.+?)(?:\s\d+\.\s|$)", t)
    when = date(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None
    return when, (purpose.group(1).strip() if purpose else "")


def quarterly_releases(rows: Iterable[Mapping[str, Any]]) -> list[date]:
    """공시 목록 → 분기 실적(잠정) 접수일 (오래된 것 먼저). 정정 공시는 뺀다."""
    picked: list[tuple[date, bool]] = []
    for r in rows:
        name = str(r.get("report_nm") or "").replace(" ", "")
        if "영업(잠정)실적" not in name or "정정" in name:
            continue
        d = str(r.get("rcept_dt") or "")
        if len(d) != 8:
            continue
        picked.append((date(int(d[:4]), int(d[4:6]), int(d[6:])), "연결재무제표기준" in name))
    if any(c for _, c in picked):
        days = [d for d, c in picked if c]
    else:
        days = [d for d, _ in picked]
        if days and len([d for d in days if d >= max(days) - timedelta(days=365)]) > 6:
            return []  # 월별 판매 등 — 분기 발표로 볼 수 없다
    return sorted(set(days))


def judge(
    today: date, ir: Sequence[tuple[date | None, str]], releases: Sequence[date]
) -> KrEarnings:
    """확정(IR) → 이번 분기 끝 → 예상(지난해) → 모름 순서 (순수)."""
    last = max(releases) if releases else None
    upcoming = sorted(d for d, p in ir if d is not None and d >= today and "실적" in p)
    if upcoming:
        return KrEarnings(upcoming[0], "confirmed", "IR 안내공시", last)
    if last is not None and (today - last).days <= DONE_DAYS:
        return KrEarnings(None, "done", f"이번 분기 실적 발표 끝 ({last:%m/%d})", last)
    guesses = sorted(
        d + timedelta(days=365)
        for d in releases
        if 0 <= (d + timedelta(days=365) - today).days <= ESTIMATE_AHEAD
    )
    if guesses:
        return KrEarnings(guesses[0], "estimated", "지난해 같은 분기 기준 예상", last)
    return KrEarnings(None, "unknown", "DART 에서 실적 일정을 못 찾음", last)


# ------------------------------------------------------------------ 자료 (DART)


def _strip(xml: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", xml))


def corp_code(root: Path, stock: str) -> str | None:
    """종목코드 → DART 고유번호 (DART 감사 때 받은 corpCode zip 에서, 새 요청 없음)."""
    zips = sorted(
        (root / "data" / "research" / "fundamental_pilot" / "dart").glob("corpcode-*.zip")
    )
    if not zips:
        return None
    with zipfile.ZipFile(io.BytesIO(zips[-1].read_bytes())) as zf:
        xml = zf.read(zf.namelist()[0]).decode("utf-8")
    m = re.search(
        r"<corp_code>(\d+)</corp_code>\s*<corp_name>[^<]*</corp_name>(?:\s*<corp_eng_name>[^<]*"
        rf"</corp_eng_name>)?\s*<stock_code>{re.escape(stock)}</stock_code>",
        xml,
    )
    return m.group(1) if m else None


def _list(
    get: Callable[..., Any], key: str, corp: str, since: date, until: date
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for page in range(1, 4):
        d = get(
            f"{API}/list.json",
            params={
                "crtfc_key": key,
                "corp_code": corp,
                "bgn_de": f"{since:%Y%m%d}",
                "end_de": f"{until:%Y%m%d}",
                "pblntf_ty": "I",
                "page_no": str(page),
                "page_count": "100",
            },
        ).json()
        if d.get("status") == "013":  # 조회된 데이터 없음
            break
        if d.get("status") != "000":
            raise ValueError(f"DART 목록 오류 {d.get('status')}")
        out += list(d.get("list") or [])
        if page >= int(d.get("total_page") or 1):
            break
    return out


def _ir_text(get: Callable[..., Any], key: str, rcept_no: str) -> str:
    r = get(f"{API}/document.xml", params={"crtfc_key": key, "rcept_no": rcept_no})
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        return _strip(zf.read(zf.namelist()[0]).decode("utf-8", "replace"))


def lookup(
    root: Path,
    stock: str,
    today: date,
    key: str,
    *,
    get: Callable[..., Any] | None = None,
    now_s: float | None = None,
) -> KrEarnings:
    """한 종목의 실적 일정 (6 시간 캐시 ``data/event_calendar/kr_earnings/<코드>.json``).

    DART 실패는 예외로 올린다 — 부르는 쪽이 '확인 실패 → 신규 매수 보류' 로 다룬다."""
    now_s = now_s if now_s is not None else time.time()
    cache = root / "data" / "event_calendar" / "kr_earnings" / f"{stock}.json"
    try:
        c = json.loads(cache.read_text(encoding="utf-8"))
        if c.get("today") == today.isoformat() and now_s - float(c["at"]) < CACHE_S:
            cached = [(date.fromisoformat(d) if d else None, p) for d, p in c["ir"]]
            return judge(today, cached, [date.fromisoformat(d) for d in c["releases"]])
    except (OSError, ValueError, KeyError, TypeError):
        pass
    corp = corp_code(root, stock)
    if corp is None:
        raise ValueError(f"DART 고유번호 없음: {stock}")
    if get is None:
        client = httpx.Client(timeout=20.0)
        get = client.get
    rows = _list(get, key, corp, today - timedelta(days=430), today)
    releases = quarterly_releases(rows)
    ir: list[tuple[date | None, str]] = []
    for r in rows:
        name = str(r.get("report_nm") or "")
        d = str(r.get("rcept_dt") or "")
        if (
            "기업설명회" in name
            and "정정" not in name
            and d >= f"{today - timedelta(days=45):%Y%m%d}"
        ):
            ir.append(parse_ir(_ir_text(get, key, str(r["rcept_no"]))))
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(
        json.dumps(
            {
                "today": today.isoformat(),
                "at": now_s,
                "ir": [[d.isoformat() if d else "", p] for d, p in ir],
                "releases": [d.isoformat() for d in releases],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return judge(today, ir, releases)


def kr_company_events(info: KrEarnings, session_days: Sequence[date]) -> EntryEvents:
    """미국 :func:`entry_events.company_events` 와 같은 꼴 — 진입일 + 다음 3 거래일 안이면 보류."""
    if not session_days:
        return EntryEvents(("실제 거래일 달력 확인 불가 — 신규 매수 보류",))
    start, end = session_days[0], session_days[-1]
    label = {"confirmed": "IR 공시 확정", "estimated": "지난해 기준 예상"}.get(info.kind, "")
    if info.day is not None and info.kind in ("confirmed", "estimated"):
        if info.day <= end:
            distance = sum(start < s <= info.day for s in session_days)
            return EntryEvents(
                (
                    f"실적 발표 {info.day} ({distance} 거래일 뒤 · {label})"
                    " — 발표 다음 정규장에 새 추천 필요",
                ),
                entry_day=start,
                earnings_date=info.day.isoformat(),
                earnings_in_days=distance,
            )
        weekdays = sum(
            (start + timedelta(days=k)).weekday() < 5 for k in range(1, (info.day - start).days + 1)
        )
        return EntryEvents(
            lines=(f"ℹ️ 국내 실적 일정: {label} (DART)",),
            entry_day=start,
            earnings_date=info.day.isoformat(),
            earnings_in_days=weekdays,
        )
    if info.kind == "done":
        return EntryEvents(
            lines=(f"✅ {info.note} — 다음 발표는 석 달쯤 뒤",),
            entry_day=start,
            earnings_date="",
            earnings_in_days=len(session_days),
        )
    return EntryEvents((f"국내 실적 일정 미확인 ({info.note}) — 신규 매수 보류",), entry_day=start)
