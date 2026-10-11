"""미국 시장 하락 브리핑 (IDEAS 29, 미국 첫판) — 계산 · 검사 · 문장 만들기는 순수 함수.

docs/research/BRIEFING_PHASE0_AUDIT.md §5 계약:

* 수치(등락 · VIX · 기여도)는 **코드가 계산**하고 AI 에게는 그대로 준다. AI 는 전달한 근거(E 번호)만
  요약 · 분류한다. 근거 번호가 없거나, 자료에 없는 숫자를 쓴 주장은 출력에서 뺀다.
* 출력 칸: 확인된 사실 / 원인 후보 / 반대 근거 / 확인 불가 / 추가 확인. 모델 확신도는 쓰지 않고
  **증거 상태**(공식 원문 · 기사 · 요약만)를 코드가 붙인다.
* 지수 기여도 = (그날 ETF 보유 비중 또는 시총 비중 대리) × 같은 날 종목 수익. 기계적 근사이고 뉴스가
  그 기여를 일으켰다는 뜻이 아니다. 나머지 종목 몫을 함께 적는다.
* 매매 · 주문과 무관한 읽기 전용 도구다.
"""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from auto_trading.ops.laya_tags import brief_line

__all__ = [
    "OFFICIAL_KINDS",
    "Evidence",
    "Holding",
    "contributions",
    "describe_numbers",
    "jev_check_parse",
    "jev_check_requests",
    "parse_nasdaq100",
    "parse_spy_holdings",
    "pct_rank",
    "render_telegram",
    "top_by",
    "validate_answer",
]

OFFICIAL_KINDS = ("sec", "fed")
_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_NUM = re.compile(r"[-+]?\d[\d,]*\.?\d*")


@dataclass(frozen=True, slots=True)
class Holding:
    symbol: str
    name: str
    weight: float  # 0 ~ 1


@dataclass(frozen=True, slots=True)
class Evidence:
    id: str
    kind: str  # "sec" | "fed" | "news"
    source: str
    title: str
    published: str
    url: str
    summary: str = ""
    symbol: str = ""

    @property
    def state(self) -> str:
        return "공식 원문" if self.kind in OFFICIAL_KINDS else "기사 · 요약만"


# ---------------------------------------------------------------- 보유 · 구성


def _cells(sheet: ET.Element, shared: Sequence[str]) -> list[list[str]]:
    rows = []
    for row in sheet.iter(f"{{{_NS['m']}}}row"):
        vals = []
        for c in row.iter(f"{{{_NS['m']}}}c"):
            v = c.find("m:v", _NS)
            t = c.get("t")
            if t == "s" and v is not None and v.text is not None:
                vals.append(shared[int(v.text)])
            elif t == "inlineStr":
                node = c.find("m:is/m:t", _NS)
                vals.append(node.text or "" if node is not None else "")
            else:
                vals.append(v.text or "" if v is not None else "")
        rows.append(vals)
    return rows


def parse_spy_holdings(xlsx: bytes) -> list[Holding]:
    """SPDR 일일 보유 XLSX → 보통주 보유 (순수). 헤더 행의 'Ticker' · 'Weight' 칸을 찾는다."""
    with zipfile.ZipFile(io.BytesIO(xlsx)) as z:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.iter(f"{{{_NS['m']}}}si"):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{_NS['m']}}}t")))
        sheet = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
    rows = _cells(sheet, shared)
    head = next((i for i, r in enumerate(rows) if "Ticker" in r and "Weight" in r), None)
    if head is None:
        return []
    h = rows[head]
    ti, wi, ni = h.index("Ticker"), h.index("Weight"), h.index("Name") if "Name" in h else 0
    out = []
    for r in rows[head + 1 :]:
        if len(r) <= max(ti, wi) or not r[ti] or r[ti] in ("-", "CASH_USD"):
            continue
        try:
            w = float(r[wi]) / 100
        except ValueError:
            continue
        out.append(Holding(r[ti].strip(), r[ni].strip() if ni < len(r) else "", w))
    return out


def parse_nasdaq100(payload: Mapping[str, Any]) -> list[Holding]:
    """Nasdaq 공식 나스닥 100 목록(JSON) → 시총 비중 **대리** (순수). 실제 지수는 수정 시총 가중."""
    rows = payload["data"]["data"]["rows"]
    caps = []
    for r in rows:
        try:
            cap = float(str(r["marketCap"]).replace(",", ""))
        except (KeyError, ValueError):
            continue
        caps.append((r["symbol"], str(r.get("companyName", "")), cap))
    total = sum(c for _, _, c in caps)
    return [Holding(s, n, c / total) for s, n, c in caps] if total > 0 else []


def top_by(holdings: Sequence[Holding], n: int) -> list[Holding]:
    return sorted(holdings, key=lambda h: (-h.weight, h.symbol))[:n]


# ---------------------------------------------------------------- 수치


def contributions(
    holdings: Sequence[Holding], returns: Mapping[str, float], index_return: float
) -> dict[str, Any]:
    """종목별 기여(비중 × 수익, %p) · 합 · 나머지 (순수). 수익이 없는 종목은 '미확보'."""
    rows: list[dict[str, Any]] = []
    known = 0.0
    for h in holdings:
        r = returns.get(h.symbol)
        c = h.weight * r if r is not None else None
        if c is not None:
            known += c
        rows.append({"symbol": h.symbol, "weight": h.weight, "ret": r, "contrib": c})
    return {"rows": rows, "top_sum": known, "rest": index_return - known}


def pct_rank(history: Sequence[float], value: float) -> float:
    """value 가 history 안에서 몇 % 위치인가 (0 ~ 100, 순수)."""
    if not history:
        return float("nan")
    return 100 * sum(1 for x in history if x <= value) / len(history)


# ---------------------------------------------------------------- AI 답 검사


def _numbers(text: str) -> list[float]:
    out = []
    for m in _NUM.findall(text):
        try:
            out.append(float(m.replace(",", "")))
        except ValueError:
            continue
    return out


def validate_answer(
    answer: Mapping[str, Any], evidence: Sequence[Evidence], allowed_text: str
) -> dict[str, Any]:
    """근거 번호가 실제로 있고 숫자가 전달 자료(allowed_text)에 있는 AI 주장만 남긴다 (순수).

    ``allowed_text`` = 코드가 만든 수치 블록 + 근거 제목 · 요약을 이은 글."""
    ids = {e.id: e for e in evidence}
    allowed = _numbers(allowed_text)

    def ok_numbers(text: str) -> bool:
        for x in _numbers(text):
            if abs(x) < 10 and float(x).is_integer():
                continue  # 1 · 2 · 3 같은 순서 · 개수 표현은 허용
            if not any(abs(abs(x) - abs(a)) <= max(0.006, 0.0005 * abs(a)) for a in allowed):
                return False
        return True

    kept: dict[str, list[dict[str, Any]]] = {"facts": [], "causes": [], "counter": []}
    dropped: list[dict[str, Any]] = []
    for key in kept:
        for item in answer.get(key, []) or []:
            if not isinstance(item, Mapping):
                continue
            text = str(item.get("text", "")).strip()
            refs = [r for r in item.get("evidence", []) or [] if r in ids]
            reason = None
            if not text:
                reason = "빈 문장"
            elif not refs:
                reason = "근거 번호 없음 · 없는 번호"
            elif not ok_numbers(text):
                reason = "자료에 없는 숫자"
            if reason:
                dropped.append({"where": key, "text": text, "reason": reason})
                continue
            states = sorted({ids[r].state for r in refs})
            kept[key].append({"text": text, "evidence": refs, "state": " / ".join(states)})
    unknown = [str(x) for x in answer.get("unknown", []) or [] if str(x).strip()]
    to_check = [str(x) for x in answer.get("to_check", []) or [] if str(x).strip()]
    return {**kept, "unknown": unknown, "to_check": to_check, "dropped": dropped}


# ---------------------------------------------------------------- JEV 대조 (그림자 기록, 문서 173)

JEV_MODEL = "jev-1.13.0"
JEV_PROMPT_VERSION = "brief-jev-check/2"
"""/1 은 출처 · 자료표를 한 질문에 묶어 맞는 뉴스 문장까지 모두 틀렸다고 봤다(2026-10-09 첫 실행).
/2 는 두 질문으로 나눈다 — (A) 인용 출처에 있나, (B) 자료표 숫자와 어긋나나."""
_SOURCE_Q = {
    "question": (
        "Do the cited sources (English headlines and summaries) state what this Korean sentence "
        "says? Ignore wording and language differences; check the facts, directions (up/down, "
        "rise/fall, hike/cut) and numbers."
    ),
}
_SOURCE_C = {
    "true": "The sources state the same facts, with the same directions and numbers.",
    "false": (
        "The sources say something different, the opposite direction, a different number, "
        "or do not mention it."
    ),
}
_DATA_Q = {
    "question": (
        "Does this Korean sentence state a return for an index or stock that appears in the data "
        "block in the state, and get its direction or number wrong? Sentences about other things, "
        "or about items not in the data block, are not contradictions."
    ),
}
_DATA_C = {
    "true": (
        "It names an item from the data block and states the opposite direction or a different "
        "number."
    ),
    "false": (
        "It matches the data block, or it does not state a return for any item in the data block."
    ),
}


def jev_check_requests(
    ai: Mapping[str, Any], evidence: Sequence[Evidence], numbers: str
) -> tuple[dict[str, Any], dict[str, Any], dict[str, tuple[str, int]]]:
    """검사를 통과해 남은 AI 문장마다 (A) 출처 대조 · (B) 자료표 모순 Noul. 결과는 기록만.

    (출처 요청, 자료표 요청, {질문 키: (구역, 순번)}). 인용 출처가 없으면 (A) 를 묻지 않는다."""
    by_id = {e.id: e for e in evidence}
    source_q: dict[str, Any] = {}
    data_q: dict[str, Any] = {}
    keys: dict[str, tuple[str, int]] = {}
    for section in ("facts", "causes", "counter"):
        for i, item in enumerate(ai.get(section) or []):
            key = f"{section[:2]}{i:02d}"
            keys[key] = (section, i)
            text = str(item.get("text", ""))
            cited = [by_id[r] for r in item.get("evidence", []) if r in by_id]
            if cited:
                source_q[key] = {
                    "type": "noul",
                    "instructions": {
                        **_SOURCE_Q,
                        "sentence": text,
                        "cited_sources": [f"{e.title} — {e.summary}".strip(" —") for e in cited],
                    },
                    "criteria": _SOURCE_C,
                }
            data_q[key] = {
                "type": "noul",
                "instructions": {**_DATA_Q, "sentence": text},
                "criteria": _DATA_C,
            }
    source = {
        "state": "Fact check of a market report against its cited sources.",
        "model": JEV_MODEL,
        "questions": source_q,
    }
    data = {"state": {"data_block": numbers}, "model": JEV_MODEL, "questions": data_q}
    return source, data, keys


def _noul(body: Mapping[str, Any] | None, key: str) -> float | None:
    answer = ((body or {}).get("answers") or {}).get(key)
    value = answer.get("noul") if isinstance(answer, Mapping) else None
    return float(value) if isinstance(value, int | float) and 0 <= value <= 1 else None


def jev_check_parse(
    ai: Mapping[str, Any],
    keys: Mapping[str, tuple[str, int]],
    source_body: Mapping[str, Any] | None,
    data_body: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """문장별 (A) ``source`` · (B) ``contradiction`` 값과 ``flag``.

    flag = 출처에 없다(A < 0.5) 또는 자료표와 어긋난다(B > 0.5). 둘 다 답이 없으면 None."""
    out = []
    for key, (section, i) in keys.items():
        src, bad = _noul(source_body, key), _noul(data_body, key)
        flags = (src is not None and src < 0.5, bad is not None and bad > 0.5)
        out.append(
            {
                "where": section,
                "index": i,
                "text": str(ai[section][i].get("text", "")),
                "source": src,
                "contradiction": bad,
                "flag": None if src is None and bad is None else any(flags),
            }
        )
    return out


# ---------------------------------------------------------------- 문장


def _pct(x: float | None) -> str:
    return "미확보" if x is None else f"{x * 100:+.2f}%"


def _arrow(x: float | None) -> str:
    if x is None:
        return "·"
    return "🔺" if x > 0 else "🔻" if x < 0 else "▫️"


_STATE_SHORT = {"공식 원문": "공식", "기사 · 요약만": "기사"}
RULE = "━━━━━━━━━━━━"


def _tag(item: Mapping[str, Any]) -> str:
    states = [_STATE_SHORT.get(x.strip(), x.strip()) for x in str(item["state"]).split(" / ")]
    return f"   └ {', '.join(item['evidence'])} · {'/'.join(x for x in states if x)}"


def render_telegram(brief: Mapping[str, Any]) -> str:
    """텔레그램 보고서 (순수, 휴대폰에서 읽기 쉽게 한 줄에 하나).

    순서: 시장 → 대형주 기여 → 관심 종목 → AI 칸(검사 통과분) → 마감 뒤 소식 → 자료 상태."""
    m = brief["market"]
    lines = ["📊 미국 시장 브리핑", f"{brief['session']} 미국 종가 기준", RULE]
    for k in ("SPY", "QQQ"):
        lines.append(f"{_arrow(m.get(k))} {k}  {_pct(m.get(k))}")
    vix = m.get("vix")
    if vix:
        lines.append(f"😰 VIX  {vix['close']:.2f} ({vix['change']:+.2f})")
        lines.append(f"   └ 1 년 중 {vix['pct_rank']:.0f}% 위치 · {vix['date']} 종가")
    else:
        lines.append("😰 VIX  미확보")
    groups = (
        ("🏢 S&P 500 상위 10", "SPY 보유 비중 기준", "spy"),
        ("💻 나스닥 100 상위 10", "시가총액 비중 기준(근사)", "qqq"),
    )
    for title, basis, key in groups:
        lines += ["", title]
        c = (brief.get("contrib") or {}).get(key)
        if not c:
            lines.append("   미확보")
            continue
        lines.append(f"   상위 10 합 {c['top_sum'] * 100:+.2f}%p · 나머지 {c['rest'] * 100:+.2f}%p")
        lines.append(f"   ({basis}, 지수 기여 = 비중 × 등락)")
        known = [r for r in c["rows"] if r["contrib"] is not None]
        for r in sorted(known, key=lambda r: abs(r["contrib"]), reverse=True)[:5]:
            share = f"(기여 {r['contrib'] * 100:+.2f}%p)"
            lines.append(f"{_arrow(r['ret'])} {r['symbol']}  {_pct(r['ret'])}  {share}")
        missing = [r["symbol"] for r in c["rows"] if r["contrib"] is None]
        if missing:
            lines.append(f"   미확보: {', '.join(missing)}")
    watch = brief.get("watch") or {}
    if watch:
        lines += ["", "⭐ 관심 종목"]
        lines.extend(f"{_arrow(r)} {s}  {_pct(r)}" for s, r in watch.items())
    ai = brief.get("ai")
    if ai:
        lines += ["", RULE, "🤖 AI 정리 (근거 번호 · 공식/기사)"]
        for title, key in (
            ("✅ 확인된 사실", "facts"),
            ("🔎 원인 후보", "causes"),
            ("↔️ 반대 근거", "counter"),
        ):
            items = ai.get(key) or []
            if items:
                lines += ["", title]
                for x in items[:5]:
                    lines += [f"• {x['text']}", _tag(x)]
        for title, key in (("❓ 확인 불가", "unknown"), ("📌 추가로 볼 것", "to_check")):
            items = ai.get(key) or []
            if items:
                lines += ["", title]
                lines.extend(f"• {x}" for x in items[:4])
        if ai.get("dropped"):
            lines += ["", f"(검사에서 뺀 AI 주장 {len(ai['dropped'])} 건 — 보고서 파일 참고)"]
    mood = brief_line(brief.get("laya") or {})
    if mood:
        lines += ["", mood]
    after = brief.get("after_close") or []
    if after:
        lines += ["", RULE, "🕗 마감 뒤 소식 (다음 날 참고 · 원인 분석엔 안 씀)"]
        lines.extend(f"• {x['title']}" for x in after[:3])
    status = brief.get("status", {})
    lines += [
        "",
        RULE,
        f"자료: 근거 {status.get('evidence', 0)} 건 (공식 {status.get('official', 0)})",
        f"AI: {status.get('model', '없음')}",
        "※ 정보용 · 매매 신호 아님 · 정확성 검산 전",
    ]
    return "\n".join(lines)


def describe_numbers(brief: Mapping[str, Any]) -> str:
    """AI 에게 줄 수치 블록 (순수) — 이 글에 있는 숫자만 AI 가 쓸 수 있다."""
    m = brief["market"]
    out = [f"session={brief['session']}"]
    for k in ("SPY", "QQQ"):
        v = m.get(k)
        out.append(f"{k}_return_pct={v * 100:.2f}" if v is not None else f"{k}_return_pct=NA")
    vix = m.get("vix")
    if vix:
        out.append(
            f"VIX_close={vix['close']:.2f} VIX_change={vix['change']:.2f} "
            f"VIX_1y_pct_rank={vix['pct_rank']:.0f} VIX_date={vix['date']}"
        )
    for key, c in (brief.get("contrib") or {}).items():
        if not c:
            continue
        for r in c["rows"]:
            if r["ret"] is None:
                continue
            out.append(
                f"{key}:{r['symbol']} weight_pct={r['weight'] * 100:.2f} "
                f"return_pct={r['ret'] * 100:.2f} contrib_pctpt={r['contrib'] * 100:.2f}"
            )
        out.append(
            f"{key}:top10_sum_pctpt={c['top_sum'] * 100:.2f} rest_pctpt={c['rest'] * 100:.2f}"
        )
    for s, r in (brief.get("watch") or {}).items():
        out.append(f"watch:{s} return_pct={r * 100:.2f}" if r is not None else f"watch:{s} NA")
    return "\n".join(out)
